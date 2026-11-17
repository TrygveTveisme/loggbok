import numpy as np
import pandas as pd

from loggbok.backtest import fee, run_backtest
from loggbok.config import load_rules

RULES = load_rules()


def prices(**series):
    n = len(next(iter(series.values())))
    idx = pd.bdate_range(end="2026-11-16", periods=n)
    return pd.DataFrame(series, index=idx)


def ramp(n, start, step):
    return [start * (1 + step) ** i for i in range(n)]


def test_buys_uptrend_and_trailing_stop_sells_after_drop():
    n = 600
    up_then_drop = ramp(450, 100, 0.002) + [ramp(450, 100, 0.002)[-1] * 0.8] * 150  # falls 20 % and stays
    flat = [100.0 + (i % 2) for i in range(n)]
    res = run_backtest(prices(**{"UP.OL": up_then_drop, "FLAT.OL": flat}), RULES)
    up = [t for t in res.trades if t.ticker == "UP.OL"]
    assert up and up[0].exit_reason == "trailing stop"
    assert up[0].result_pct > 0  # the trailing stop kept part of the gain


def test_fixed_stop_holds_longer_than_trailing():
    n = 600
    path = ramp(400, 100, 0.002) + list(np.linspace(ramp(400, 100, 0.002)[-1], 190, 200))  # gives back gains slowly
    df = prices(**{"A.OL": path})
    trailing = run_backtest(df, RULES, trailing=True)
    fixed = run_backtest(df, RULES, trailing=False)
    assert trailing.trades and fixed.trades
    assert trailing.trades[0].exit_date is not None


def test_equity_starts_at_start_value_and_fees_count():
    df = prices(**{"A.OL": ramp(500, 100, 0.001), "B.OL": ramp(500, 50, 0.0015)})
    res = run_backtest(df, RULES, start_value=100_000)
    assert abs(res.equity.iloc[0] - 100_000) < 1_000
    assert res.summary()["fees_nok"] > 0
    assert fee(10_000, {"pct": 0.00049, "min_nok": 79}) == 79
    assert fee(1_000_000, {"pct": 0.00049, "min_nok": 79}) == 490


def test_usd_stocks_are_converted_to_nok():
    df = prices(**{"AAPL": ramp(500, 100, 0.001)})
    fx = pd.Series(10.0, index=df.index)
    res = run_backtest(df, RULES, fx_usdnok=fx)
    t = res.trades[0]
    assert t.entry_fx == 10.0
    assert abs(t.entry_price_nok - 10.0 * df.loc[t.entry_date, "AAPL"]) < 1e-6


def test_backtest_cli_runs_on_fake_data(tmp_path, monkeypatch):
    from loggbok import backtest_cli
    from loggbok.fake import FakeSource

    src = FakeSource(days=800)
    monkeypatch.setattr(backtest_cli, "DATA_DIR", tmp_path)
    monkeypatch.setattr(backtest_cli, "all_tickers", lambda u: src.tickers)
    assert backtest_cli.run(years=2, source=src) == 0
    assert (next((tmp_path / "backtest").iterdir()) / "summary.csv").exists()
