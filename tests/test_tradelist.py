import pandas as pd

from loggbok.config import load_rules
from loggbok.journal import Journal
from loggbok.screener import evaluate, rank
from loggbok.tradelist import account_for, build_trade_list
from tests.test_screener import good, rising

RULES = load_rules()


def setup(tickers_steps, sectors=None):
    closes, evals = {}, []
    for i, (tk, step) in enumerate(tickers_steps.items()):
        s = rising(step=step)
        closes[tk] = s
        sector = (sectors or {}).get(tk, f"Sector{i}")
        evals.append(evaluate(good(ticker=tk, sector=sector, currency="USD" if not tk.endswith(".OL") else "NOK"), s, RULES))
    rank(evals)
    return pd.DataFrame(closes), {e.ticker: e for e in evals}


def test_buys_top_two_with_position_size():
    closes, evals = setup({"A.OL": 0.004, "B.OL": 0.003, "C.OL": 0.002})
    tl = build_trade_list(evaluations=evals, closes=closes, positions=pd.DataFrame(), cash_nok=100_000,
                          fx_to_nok={"NOK": 1.0}, rules=RULES)
    buys = [o for o in tl.orders if o.action == "buy"]
    assert [o.ticker for o in buys] == ["A.OL", "B.OL"]
    for o in buys:
        assert 0.05 * 100_000 < o.value_nok <= 0.07 * 100_000
        assert o.snapshot["eligible"] is True


def test_sector_cap_skips():
    closes, evals = setup({"A.OL": 0.004, "B.OL": 0.003, "C.OL": 0.002},
                          sectors={"A.OL": "Energy", "B.OL": "Energy", "C.OL": "Tech"})
    j = Journal(":memory:")
    for tk in ("Y.OL", "Z.OL"):  # 2 x 12 % in Energy, under the trim limit
        j.record_buy(account="paper", ticker=tk, quantity=120, price=100, currency="NOK", sector="Energy",
                     ts="2026-11-01T10:00:00")
        closes[tk] = 100.0
    tl = build_trade_list(evaluations=evals, closes=closes, positions=j.positions(["paper"]), cash_nok=76_000,
                          fx_to_nok={"NOK": 1.0}, rules=RULES)
    buys = [o.ticker for o in tl.orders if o.action == "buy"]
    assert buys == ["C.OL"]  # 24 % + 7 % would break the 30 % Energy cap
    assert {s.ticker for s in tl.skipped if "sector cap" in s.reason} == {"A.OL", "B.OL"}


def test_trailing_stop_sells():
    idx = pd.bdate_range(end="2026-11-16", periods=300)
    s = pd.Series([100.0] * 250 + [140.0] * 40 + [115.0] * 10, index=idx)  # 18 % below the 140 peak
    j = Journal(":memory:")
    j.record_buy(account="paper", ticker="X.OL", quantity=10, price=100, currency="NOK", sector="Energy",
                 ts=str(idx[200].date()))
    ev = evaluate(good(ticker="X.OL"), s, RULES)
    tl = build_trade_list(evaluations={"X.OL": ev}, closes=pd.DataFrame({"X.OL": s}),
                          positions=j.positions(["paper"]), cash_nok=0, fx_to_nok={"NOK": 1.0}, rules=RULES)
    assert tl.orders[0].action == "sell" and tl.orders[0].reason.startswith("trailing stop")


def test_quality_lost_sells_and_overweight_trims():
    closes, evals = setup({"Q.OL": 0.003, "T.OL": 0.003})
    evals["Q.OL"] = evaluate(good(ticker="Q.OL", roe=0.01), closes["Q.OL"], RULES)
    j = Journal(":memory:")
    for tk in ("Q.OL", "T.OL"):
        j.record_buy(account="paper", ticker=tk, quantity=100, price=float(closes[tk].iloc[-1]), currency="NOK",
                     sector="X", ts=str(closes.index[-2].date()))
    tl = build_trade_list(evaluations=evals, closes=closes, positions=j.positions(["paper"]), cash_nok=0,
                          fx_to_nok={"NOK": 1.0}, rules=RULES)
    actions = {o.ticker: o.action for o in tl.orders}
    assert actions["Q.OL"] == "sell" and actions["T.OL"] == "trim"


def test_accounts():
    assert account_for("EQNR.OL", "real") == "ask"
    assert account_for("AAPL", "real") == "normal"
    assert account_for("AAPL", "paper") == "paper"
