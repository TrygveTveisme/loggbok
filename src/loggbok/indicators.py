"""Price indicators used by the trend and selling rules."""
from __future__ import annotations

import pandas as pd


def sma(close: pd.Series, n: int) -> float | None:
    close = close.dropna()
    if len(close) < n:
        return None
    return float(close.iloc[-n:].mean())


def momentum(close: pd.Series, lookback: int, skip: int) -> float | None:
    """Price change from `lookback` to `skip` trading days ago (e.g. 6 months leaving out the last month)."""
    close = close.dropna()
    if len(close) <= lookback:
        return None
    start = close.iloc[-lookback - 1]
    end = close.iloc[-skip - 1] if skip else close.iloc[-1]
    if start <= 0:
        return None
    return float(end / start - 1)


def highest_close_since(close: pd.Series, since: pd.Timestamp | str) -> float | None:
    close = close.dropna()
    start = pd.Timestamp(since)
    if start.tzinfo is not None:
        start = start.tz_localize(None)
    window = close[close.index >= start.normalize()]
    if window.empty:
        return None
    return float(window.max())
