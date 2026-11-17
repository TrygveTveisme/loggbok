"""Apply the rules in config/rules.yaml to one stock and rank the stocks that pass."""
from __future__ import annotations

import statistics
from dataclasses import asdict, dataclass, field

import pandas as pd

from .config import market_of
from .data import Fundamentals
from .indicators import momentum, sma

GROUPS = ("universe", "quality", "price", "financials", "trend")


@dataclass
class RuleResult:
    group: str
    name: str
    passed: bool | None  # None = data missing
    value: float | None = None
    threshold: float | str | None = None


@dataclass
class Evaluation:
    ticker: str
    name: str
    market: str
    currency: str
    sector: str
    industry: str
    is_financial: bool
    rule_version: str
    last_close: float | None
    momentum: float | None
    results: list[RuleResult] = field(default_factory=list)
    group_passed: dict[str, bool] = field(default_factory=dict)
    rank: int | None = None
    key_figures: dict[str, float | None] = field(default_factory=dict)

    @property
    def passes_quality(self) -> bool:
        """Quality and price rules (or the bank and insurer rules)."""
        if self.is_financial:
            return self.group_passed.get("financials", False)
        return self.group_passed.get("quality", False) and self.group_passed.get("price", False)

    @property
    def eligible(self) -> bool:
        return (
            self.group_passed.get("universe", False)
            and self.passes_quality
            and self.group_passed.get("trend", False)
        )

    @property
    def failed(self) -> list[str]:
        return [r.name for r in self.results if r.passed is False]

    @property
    def missing(self) -> list[str]:
        return [r.name for r in self.results if r.passed is None]

    def snapshot(self) -> dict:
        """Everything known about the stock at decision time, for the trade journal."""
        d = asdict(self)
        d["eligible"] = self.eligible
        d["passes_quality"] = self.passes_quality
        return d


def _check(group, name, value, threshold, op) -> RuleResult:
    if value is None:
        return RuleResult(group, name, None, None, threshold)
    return RuleResult(group, name, bool(op(value, threshold)), float(value), threshold)


def evaluate(f: Fundamentals, close: pd.Series, rules: dict) -> Evaluation:
    close = close.dropna() if close is not None else pd.Series(dtype=float)
    last = float(close.iloc[-1]) if len(close) else f.price
    t = rules["trend"]
    gt = lambda a, b: a > b  # noqa: E731
    lt = lambda a, b: a < b  # noqa: E731
    ge = lambda a, b: a >= b  # noqa: E731

    ev = Evaluation(
        ticker=f.ticker,
        name=f.name,
        market=market_of(f.ticker),
        currency=f.currency,
        sector=f.sector or "Unknown",
        industry=f.industry,
        is_financial=f.is_financial,
        rule_version=str(rules["version"]),
        last_close=last,
        momentum=momentum(close, t["momentum_lookback_days"], t["momentum_skip_days"]),
    )
    R = ev.results

    # Universe: size and liquidity
    u = rules["universe"]
    R.append(_check("universe", "market cap", f.market_cap, u["min_market_cap"].get(f.currency), ge)
             if f.currency in u["min_market_cap"] else RuleResult("universe", "market cap", None, f.market_cap, f"no limit for {f.currency}"))
    R.append(_check("universe", "daily turnover", f.daily_turnover, u["min_daily_turnover"].get(f.currency), ge)
             if f.currency in u["min_daily_turnover"] else RuleResult("universe", "daily turnover", None, f.daily_turnover, f"no limit for {f.currency}"))

    if f.is_financial:
        b = rules["financials"]
        R.append(_check("financials", "ROE", f.roe, b["min_roe"], gt))
        R.append(_check("financials", "P/E", f.pe, b["max_pe"], lt))
        R.append(_check("financials", "P/B", f.pb, b["max_pb"], lt))
        R.append(_dividend_stable(f, b["dividend_stable_years"]))
    else:
        q = rules["quality"]
        R.append(_check("quality", "ROE", f.roe, q["min_roe"], gt))
        R.append(_check("quality", "free cash flow", f.free_cash_flow, q["min_free_cash_flow"], gt))
        R.append(_check("quality", "revenue growth", f.revenue_growth, q["min_revenue_growth"], gt))
        R.append(_check("quality", "net debt / EBITDA", _nd_ebitda(f), q["max_net_debt_ebitda"], lt))
        n = q["positive_op_margin_years"]
        margins = f.op_margins[:n]
        R.append(RuleResult("quality", f"positive op. margin {n}y",
                            None if len(margins) < n else all(m > 0 for m in margins),
                            min(margins) if margins else None, 0))
        p = rules["price"]
        R.append(_check("price", "P/E", f.pe if (f.pe or 0) > 0 else None, p["max_pe"], lt))
        if p.get("pe_below_own_median"):
            hist = f.pe_history
            if len(hist) >= p["min_pe_history_points"] and f.pe and f.pe > 0:
                med = statistics.median(hist)
                R.append(RuleResult("price", "P/E below own median", f.pe < med, f.pe, round(med, 2)))
            else:
                R.append(RuleResult("price", "P/E below own median", None, f.pe, "too little history"))
        R.append(_check("price", "FCF yield", f.fcf_yield, p["min_fcf_yield"], gt))

    short, long_ = sma(close, t["short_ma"]), sma(close, t["long_ma"])
    R.append(RuleResult("trend", f"price above MA{t['short_ma']}",
                        None if short is None or last is None else last > short, last, short))
    R.append(RuleResult("trend", f"MA{t['short_ma']} above MA{t['long_ma']}",
                        None if short is None or long_ is None else short > long_, short, long_))

    strict = rules.get("missing_data", "fail") == "fail"
    for g in GROUPS:
        rs = [r for r in R if r.group == g]
        if not rs:
            continue
        ev.group_passed[g] = all(r.passed is True for r in rs) if strict else all(r.passed is not False for r in rs)

    ev.key_figures = {
        "market_cap": f.market_cap, "roe": f.roe, "fcf_yield": f.fcf_yield,
        "revenue_growth": f.revenue_growth, "net_debt_ebitda": _nd_ebitda(f), "pe": f.pe,
        "pb": f.pb, "dividend_yield": f.dividend_yield,
        "op_margin": f.op_margins[0] if f.op_margins else None,
        "ma_short": short, "ma_long": long_,
    }
    return ev


def _nd_ebitda(f: Fundamentals) -> float | None:
    # Net cash (negative net debt) passes even when EBITDA is missing or negative.
    if f.net_debt is not None and f.net_debt <= 0:
        return f.net_debt / f.ebitda if f.ebitda and f.ebitda > 0 else 0.0
    return f.net_debt_ebitda


def _dividend_stable(f: Fundamentals, years: int) -> RuleResult:
    name = f"dividend stable {years}y"
    if not f.annual_dividends:
        return RuleResult("financials", name, None, None, "no dividend data")
    last_years = sorted(f.annual_dividends)[-years:]
    if len(last_years) < years:
        return RuleResult("financials", name, None, None, "too little history")
    vals = [f.annual_dividends[y] for y in last_years]
    ok = all(v > 0 for v in vals) and all(b >= a * 0.95 for a, b in zip(vals, vals[1:]))
    return RuleResult("financials", name, ok, vals[-1], "not falling")


def rank(evals: list[Evaluation]) -> list[Evaluation]:
    """Rank eligible stocks by momentum, best first. Returns all, eligible ones first."""
    eligible = sorted([e for e in evals if e.eligible],
                      key=lambda e: e.momentum if e.momentum is not None else float("-inf"), reverse=True)
    for i, e in enumerate(eligible, 1):
        e.rank = i
    rest = [e for e in evals if not e.eligible]
    for e in rest:
        e.rank = None
    return eligible + rest


def rule_summary(evals: list[Evaluation]) -> list[dict]:
    """How many stocks each rule knocks out, split into a real fail and missing data. Most first."""
    counts: dict[tuple[str, str], dict] = {}
    for e in evals:
        for r in e.results:
            c = counts.setdefault((r.group, r.name), {"group": r.group, "rule": r.name, "checked": 0, "failed": 0, "missing": 0})
            c["checked"] += 1
            if r.passed is False:
                c["failed"] += 1
            elif r.passed is None:
                c["missing"] += 1
    return sorted(counts.values(), key=lambda c: c["failed"] + c["missing"], reverse=True)
