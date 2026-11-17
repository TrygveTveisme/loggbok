"""Test the trend, buying and selling rules on past prices.

What this can and cannot tell you:
- Yahoo only gives today's key figures, so the quality and price rules cannot be applied
  as they were in the past. The backtest either uses every stock (testing the trend and
  selling rules alone) or only today's quality list. The second is flattering, because it
  picks stocks that turned out to be good ("look-ahead bias"); use it to compare rule
  variants with each other, not to predict returns.
- Prices are adjusted closes, so dividends are counted. Orders fill at the close on the
  check day. Fees follow `fees` in rules.yaml. US stocks are converted to NOK.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import pandas as pd

from .metrics import max_drawdown


@dataclass
class BacktestTrade:
    ticker: str
    entry_date: pd.Timestamp
    entry_price_nok: float
    quantity: int
    peak: float  # highest close in trading currency since entry
    entry_fx: float
    exit_date: pd.Timestamp | None = None
    exit_price_nok: float | None = None
    exit_reason: str | None = None
    fees_nok: float = 0.0

    @property
    def result_pct(self) -> float | None:
        if self.exit_price_nok is None:
            return None
        cost = self.entry_price_nok * self.quantity
        return (self.exit_price_nok * self.quantity - cost - self.fees_nok) / cost


@dataclass
class BacktestResult:
    equity: pd.Series
    trades: list[BacktestTrade]
    benchmark: pd.Series | None = None
    settings: dict = field(default_factory=dict)

    def summary(self) -> dict:
        eq = self.equity.dropna()
        years = max((eq.index[-1] - eq.index[0]).days / 365.25, 1e-9)
        closed = [t for t in self.trades if t.exit_date is not None]
        wins = [t for t in closed if t.result_pct > 0]
        losses = [t for t in closed if t.result_pct <= 0]
        out = {
            "start": eq.index[0].date().isoformat(), "end": eq.index[-1].date().isoformat(),
            "total_return": eq.iloc[-1] / eq.iloc[0] - 1,
            "cagr": (eq.iloc[-1] / eq.iloc[0]) ** (1 / years) - 1,
            "max_drawdown": max_drawdown(eq),
            "trades": len(closed),
            "win_rate": len(wins) / len(closed) if closed else None,
            "avg_win": sum(t.result_pct for t in wins) / len(wins) if wins else None,
            "avg_loss": sum(t.result_pct for t in losses) / len(losses) if losses else None,
            "avg_holding_days": sum((t.exit_date - t.entry_date).days for t in closed) / len(closed) if closed else None,
            "fees_nok": sum(t.fees_nok for t in self.trades),
        }
        reasons: dict[str, int] = {}
        for t in closed:
            reasons[t.exit_reason] = reasons.get(t.exit_reason, 0) + 1
        out["exit_reasons"] = reasons
        if self.benchmark is not None and len(self.benchmark.dropna()) > 1:
            b = self.benchmark.dropna()
            out["benchmark_return"] = b.iloc[-1] / b.iloc[0] - 1
            out["benchmark_cagr"] = (b.iloc[-1] / b.iloc[0]) ** (1 / years) - 1
            out["benchmark_max_drawdown"] = max_drawdown(b)
        return out


def fee(value_nok: float, fees: dict) -> float:
    return max(fees.get("min_nok", 0.0), value_nok * fees.get("pct", 0.0))


def run_backtest(closes: pd.DataFrame, rules: dict, *, fx_usdnok: pd.Series | None = None,
                 sectors: dict[str, str] | None = None, start_value: float = 100_000.0,
                 trailing: bool = True, stop: float | None = None, start: str | None = None) -> BacktestResult:
    """Simulate the rules on daily closes (one column per ticker, trading currency)."""
    t_rules, b, s = rules["trend"], rules["buying"], rules["selling"]
    fees = rules.get("fees", {"pct": 0.0005, "min_nok": 39.0})
    stop = s["trailing_stop"] if stop is None else stop
    sectors = sectors or {}
    closes = closes.sort_index().ffill(limit=5)
    if fx_usdnok is not None:
        fx_usdnok = fx_usdnok.reindex(closes.index).ffill().bfill()

    def fx(tk: str, day) -> float:
        if tk.endswith(".OL") or fx_usdnok is None:
            return 1.0
        return float(fx_usdnok.loc[day])

    ma_s = closes.rolling(t_rules["short_ma"]).mean()
    ma_l = closes.rolling(t_rules["long_ma"]).mean()
    lb, sk = t_rules["momentum_lookback_days"], t_rules["momentum_skip_days"]
    mom = closes.shift(sk) / closes.shift(lb) - 1

    first = t_rules["long_ma"] + 1
    days = closes.index[first:]
    if start:
        days = days[days >= pd.Timestamp(start)]
    if len(days) == 0:
        raise ValueError("Not enough price history for the long moving average")
    checks = set(days[:: max(1, round(b["check_every_days"] * 5 / 7))])

    cash = start_value
    open_: dict[str, BacktestTrade] = {}
    trades: list[BacktestTrade] = []
    equity = {}
    for day in days:
        px = closes.loc[day]
        for tk, tr in open_.items():
            if not math.isnan(px.get(tk, float("nan"))):
                tr.peak = max(tr.peak, float(px[tk]))
        if day in checks:
            # Sells
            for tk in list(open_):
                tr, p = open_[tk], px.get(tk)
                if p is None or math.isnan(p):
                    continue
                ref = tr.peak if trailing else tr.entry_price_nok / tr.entry_fx
                reason = None
                if p <= ref * (1 - stop):
                    reason = "trailing stop" if trailing else "stop-loss"
                elif s.get("sell_below_long_ma") and not math.isnan(ma_l.at[day, tk]) and p < ma_l.at[day, tk]:
                    reason = "trend broken"
                if reason:
                    value = p * fx(tk, day) * tr.quantity
                    f = fee(value, fees)
                    cash += value - f
                    tr.exit_date, tr.exit_price_nok, tr.exit_reason = day, p * fx(tk, day), reason
                    tr.fees_nok += f
                    del open_[tk]
            # Buys
            value_total = cash + sum(float(px[tk]) * fx(tk, day) * tr.quantity for tk, tr in open_.items()
                                     if not math.isnan(px.get(tk, float("nan"))))
            ok = (px > ma_s.loc[day]) & (ma_s.loc[day] > ma_l.loc[day]) & mom.loc[day].notna()
            cands = mom.loc[day][ok].sort_values(ascending=False)
            sector_value: dict[str, float] = {}
            for tk, tr in open_.items():
                sec = sectors.get(tk, "Unknown")
                sector_value[sec] = sector_value.get(sec, 0.0) + float(px[tk]) * fx(tk, day) * tr.quantity
            target = b["position_size"] * value_total
            bought = 0
            for tk in cands.index:
                if bought >= b["max_new_per_check"] or len(open_) >= b["max_positions"]:
                    break
                if tk in open_:
                    continue
                sec = sectors.get(tk, "Unknown")
                if sec != "Unknown" and (sector_value.get(sec, 0.0) + target) / value_total > b["max_sector_weight"]:
                    continue
                amount = min(target, cash)
                if amount < b["min_order_share"] * target:
                    break
                price_nok = float(px[tk]) * fx(tk, day)
                qty = math.floor((amount - fee(amount, fees)) / price_nok)
                if qty < 1:
                    continue
                f = fee(qty * price_nok, fees)
                cash -= qty * price_nok + f
                tr = BacktestTrade(tk, day, price_nok, qty, float(px[tk]), fx(tk, day), fees_nok=f)
                open_[tk] = tr
                trades.append(tr)
                sector_value[sec] = sector_value.get(sec, 0.0) + qty * price_nok
                bought += 1
        equity[day] = cash + sum(float(px[tk]) * fx(tk, day) * tr.quantity for tk, tr in open_.items()
                                 if not math.isnan(px.get(tk, float("nan"))))

    eq = pd.Series(equity)
    # Benchmark: the same stocks bought equally on day one and held
    held = closes.loc[days].dropna(axis=1, how="any")
    bench = None
    if not held.empty:
        conv = held.copy()
        if fx_usdnok is not None:
            for tk in conv.columns:
                if not tk.endswith(".OL"):
                    conv[tk] = conv[tk] * fx_usdnok.loc[days]
        bench = (conv / conv.iloc[0]).mean(axis=1) * start_value
    return BacktestResult(eq, trades, bench, {"trailing": trailing, "stop": stop, "rule_version": rules.get("version")})
