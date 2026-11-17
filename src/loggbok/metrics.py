"""Learning-loop metrics from the trade journal (see "Stock Tracker Learning Loop")."""
from __future__ import annotations

import pandas as pd


def trade_stats(closed: pd.DataFrame) -> dict:
    if closed is None or closed.empty:
        return {"trades": 0}
    wins = closed[closed["result_nok"] > 0]
    losses = closed[closed["result_nok"] <= 0]
    gross_win, gross_loss = wins["result_nok"].sum(), -losses["result_nok"].sum()
    return {
        "trades": len(closed),
        "win_rate": len(wins) / len(closed),
        "avg_win_pct": wins["result_pct"].mean() if len(wins) else None,
        "avg_loss_pct": losses["result_pct"].mean() if len(losses) else None,
        "expectancy_pct": closed["result_pct"].mean(),
        "profit_factor": gross_win / gross_loss if gross_loss > 0 else None,
        "total_result_nok": closed["result_nok"].sum(),
        "avg_holding_days": closed["holding_days"].mean(),
    }


def breakdown(closed: pd.DataFrame, by: str) -> pd.DataFrame:
    if closed is None or closed.empty or by not in closed:
        return pd.DataFrame()
    g = closed.groupby(by)
    return pd.DataFrame({
        "trades": g.size(),
        "win_rate": g.apply(lambda d: (d["result_nok"] > 0).mean(), include_groups=False),
        "avg_result_pct": g["result_pct"].mean(),
        "result_nok": g["result_nok"].sum(),
    }).sort_values("trades", ascending=False)


def max_drawdown(values: pd.Series) -> float | None:
    values = values.dropna()
    if values.empty:
        return None
    return float((values / values.cummax() - 1).min())
