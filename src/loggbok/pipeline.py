"""Glue: fetch data for the universe, evaluate every stock, rank, and keep the journal up to date."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Callable

import pandas as pd

from .data import DataSource, Fundamentals
from .journal import Journal
from .screener import Evaluation, evaluate, rank

BENCHMARKS = {"OSEBX": "OSEBX.OL", "S&P 500": "^GSPC"}


@dataclass
class ScreenResult:
    evaluations: list[Evaluation]
    closes: pd.DataFrame
    fx_to_nok: dict[str, float]
    errors: dict[str, str]

    def by_ticker(self) -> dict[str, Evaluation]:
        return {e.ticker: e for e in self.evaluations}


def run_screen(source: DataSource, tickers: list[str], rules: dict,
               progress: Callable[[int, int, str], None] | None = None, workers: int = 3) -> ScreenResult:
    """Fetch and evaluate every ticker. Fundamentals are fetched `workers` at a time (more makes Yahoo throttle);
    progress is always reported from the calling thread."""
    tickers = list(dict.fromkeys(tickers))
    closes = source.prices(tickers, period="2y")
    evals, errors, fx = [], {}, {"NOK": 1.0}
    funds: dict[str, Fundamentals] = {}
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = {pool.submit(source.fundamentals, tk): tk for tk in tickers}
        for i, fut in enumerate(as_completed(futures)):
            tk = futures[fut]
            if progress:
                progress(i, len(tickers), tk)
            try:
                funds[tk] = fut.result()
            except Exception as e:  # one bad ticker should not stop the screen
                errors[tk] = f"{type(e).__name__}: {e}"
    for tk in tickers:
        f = funds.get(tk)
        if f is None:
            continue
        try:
            if f.currency and f.currency not in fx:
                fx[f.currency] = source.fx_to_nok(f.currency)
            series = closes[tk] if tk in closes else pd.Series(dtype=float)
            evals.append(evaluate(f, series, rules))
        except Exception as e:
            errors[tk] = f"{type(e).__name__}: {e}"
    return ScreenResult(rank(evals), closes, fx, errors)


def position_values(positions: pd.DataFrame, closes: pd.DataFrame, fx_to_nok: dict[str, float]) -> list[dict]:
    out = []
    for p in positions.to_dict("records") if not positions.empty else []:
        s = closes[p["ticker"]].dropna() if p["ticker"] in closes else pd.Series(dtype=float)
        price = float(s.iloc[-1]) if len(s) else None
        value = price * p["quantity"] * fx_to_nok.get(p["currency"], 1.0) if price is not None else None
        out.append({"ticker": p["ticker"], "account": p["account"], "quantity": p["quantity"], "price": price,
                    "value_nok": value, "cost_nok": p["cost_nok"], "sector": p["sector"]})
    return out


def daily_update(journal: Journal, source: DataSource, accounts: list[str], extra_closes: pd.DataFrame | None = None,
                 fx_to_nok: dict[str, float] | None = None) -> None:
    """Update peaks for the trailing stop, and save today's portfolio and market snapshot."""
    positions = journal.positions(accounts)
    tickers = positions["ticker"].tolist() if not positions.empty else []
    closes = extra_closes if extra_closes is not None else pd.DataFrame()
    missing = [t for t in tickers if t not in closes]
    if missing:
        closes = pd.concat([closes, source.prices(missing, period="1y")], axis=1)
    fx = dict(fx_to_nok or {"NOK": 1.0})
    for cur in (positions["currency"].unique() if not positions.empty else []):
        if cur not in fx:
            fx[cur] = source.fx_to_nok(cur)
    journal.update_peaks(closes)
    for acc in accounts:
        pos = positions[positions["account"] == acc] if not positions.empty else positions
        journal.save_daily_snapshot(acc, position_values(pos, closes, fx), journal.cash([acc]))
    try:
        bench = source.prices(list(BENCHMARKS.values()), period="1y")
        ctx = {}
        for name, tk in BENCHMARKS.items():
            if tk in bench and bench[tk].notna().any():
                s = bench[tk].dropna()
                ctx[name] = {"close": float(s.iloc[-1]), "above_ma200": bool(len(s) >= 200 and s.iloc[-1] > s.iloc[-200:].mean())}
        journal.save_market_context(ctx)
    except Exception:
        pass
