import pandas as pd
import pytest

from loggbok.journal import Journal


@pytest.fixture
def j():
    jr = Journal(":memory:")
    yield jr
    jr.close()


def test_buy_sell_round_trip(j):
    j.deposit("paper", 100_000)
    tid = j.record_buy(account="paper", ticker="AAPL", quantity=10, price=200, currency="USD", fx_rate=10,
                       fees_nok=10, sector="Technology", snapshot={"rank": 1}, rule_version="v1", ts="2026-01-02T10:00:00")
    assert j.cash(["paper"]) == pytest.approx(100_000 - 20_010)
    pos = j.positions(["paper"])
    assert pos.loc[0, "quantity"] == 10 and pos.loc[0, "avg_cost_nok"] == pytest.approx(2001)
    j.record_sell(account="paper", ticker="AAPL", quantity=10, price=220, currency="USD", fx_rate=10,
                  fees_nok=10, exit_reason="trailing stop", snapshot={}, rule_version="v1", ts="2026-02-01T10:00:00")
    assert j.positions(["paper"]).empty
    closed = j.closed_trades()
    assert closed.loc[0, "trade_id"] == tid
    assert closed.loc[0, "result_nok"] == pytest.approx(22_000 - 10 - 20_010)
    assert closed.loc[0, "exit_reason"] == "trailing stop" and closed.loc[0, "holding_days"] == 30


def test_partial_sell_keeps_trade_open(j):
    j.record_buy(account="ask", ticker="EQNR.OL", quantity=100, price=300, currency="NOK")
    j.record_sell(account="ask", ticker="EQNR.OL", quantity=40, price=310, currency="NOK", exit_reason="trim")
    pos = j.positions(["ask"])
    assert pos.loc[0, "quantity"] == 60 and j.closed_trades().empty


def test_cannot_oversell_or_sell_unknown(j):
    j.record_buy(account="ask", ticker="EQNR.OL", quantity=1, price=300, currency="NOK")
    with pytest.raises(ValueError):
        j.record_sell(account="ask", ticker="EQNR.OL", quantity=2, price=300, currency="NOK")
    with pytest.raises(ValueError):
        j.record_sell(account="ask", ticker="DNB.OL", quantity=1, price=300, currency="NOK")
    with pytest.raises(ValueError):
        j.deposit("work", 10)


def test_peaks_and_snapshots(j):
    j.record_buy(account="paper", ticker="X", quantity=1, price=100, currency="NOK", ts="2026-03-02T09:00:00")
    idx = pd.to_datetime(["2026-02-27", "2026-03-02", "2026-03-03", "2026-03-04"])
    closes = pd.DataFrame({"X": [500, 100, 130, 120]}, index=idx)
    j.update_peaks(closes)
    assert j.positions(["paper"]).loc[0, "peak_close"] == 130  # the close before buying is ignored
    j.save_daily_snapshot("paper", [{"ticker": "X", "value_nok": 120}], cash_nok=880, day="2026-03-04")
    assert j.equity_curve(["paper"]).loc[0, "value_nok"] == 1000


def test_skips_watchlist_and_rule_versions(j):
    assert j.register_rules({"version": "v1", "x": 1}) == "v1"
    j.record_skip(ticker="DNB.OL", reason="sector cap", rank=3, snapshot={"a": 1}, rule_version="v1")
    assert j.conn.execute("SELECT COUNT(*) FROM skipped_signals").fetchone()[0] == 1
    j.watch("NOD.OL")
    assert j.watchlist() == ["NOD.OL"]
    j.unwatch("NOD.OL")
    assert j.watchlist() == []
