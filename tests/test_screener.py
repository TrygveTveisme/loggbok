import pandas as pd

from loggbok.config import load_rules
from loggbok.data import Fundamentals
from loggbok.fake import FakeSource
from loggbok.screener import evaluate, rank

RULES = load_rules()


def rising(n=300, start=100.0, step=0.002):
    idx = pd.bdate_range(end="2026-11-16", periods=n)
    return pd.Series([start * (1 + step) ** i for i in range(n)], index=idx)


def good(**kw):
    base = dict(ticker="GOOD.OL", name="Good", currency="NOK", financial_currency="NOK", sector="Industrials",
                industry="Machinery", market_cap=1e10, avg_daily_volume=1e6, price=150.0, roe=0.2,
                free_cash_flow=6e8, revenue_growth=0.1, net_debt=1e9, ebitda=1e9, pe=15.0, pb=3.0,
                dividend_rate=3.0, op_margins=[0.2, 0.18, 0.15], pe_history=[18.0, 20.0, 22.0])
    base.update(kw)
    return Fundamentals(**base)


def test_quality_stock_in_uptrend_is_eligible():
    ev = evaluate(good(), rising(), RULES)
    assert ev.eligible, ev.failed + ev.missing


def test_each_quality_rule_can_fail():
    for kw, rule in [({"roe": 0.05}, "ROE"), ({"free_cash_flow": -1.0}, "free cash flow"),
                     ({"revenue_growth": 0.01}, "revenue growth"), ({"net_debt": 5e9}, "net debt / EBITDA"),
                     ({"pe": 30.0}, "P/E"), ({"pe": 21.0}, "P/E below own median"),
                     ({"free_cash_flow": 1e8}, "FCF yield"), ({"op_margins": [0.1, -0.02, 0.1]}, "positive op. margin 3y")]:
        ev = evaluate(good(**kw), rising(), RULES)
        assert not ev.eligible and rule in ev.failed, (kw, ev.failed)


def test_net_cash_passes_debt_rule():
    ev = evaluate(good(net_debt=-2e9, ebitda=None), rising(), RULES)
    assert "net debt / EBITDA" not in ev.failed + ev.missing


def test_missing_data_fails_by_default():
    ev = evaluate(good(roe=None), rising(), RULES)
    assert not ev.eligible and "ROE" in ev.missing


def test_downtrend_is_not_eligible():
    ev = evaluate(good(), rising(step=-0.002), RULES)
    assert not ev.group_passed["trend"] and not ev.eligible


def test_small_or_illiquid_stock_is_excluded():
    assert not evaluate(good(market_cap=1e9), rising(), RULES).eligible
    assert not evaluate(good(avg_daily_volume=1e3), rising(), RULES).eligible


def test_banks_use_their_own_rules():
    bank = good(ticker="BANK.OL", sector="Financial Services", industry="Banks - Regional", pe=10.0, pb=1.2,
                roe=0.14, free_cash_flow=None, ebitda=None, net_debt=None,
                annual_dividends={2023: 10.0, 2024: 11.0, 2025: 12.0})
    ev = evaluate(bank, rising(), RULES)
    assert ev.is_financial and ev.eligible, ev.failed + ev.missing
    ev2 = evaluate(good(**{**bank.__dict__, "pb": 2.0}), rising(), RULES)
    assert "P/B" in ev2.failed
    cut = evaluate(good(**{**bank.__dict__, "annual_dividends": {2023: 12.0, 2024: 6.0, 2025: 7.0}}), rising(), RULES)
    assert "dividend stable 3y" in cut.failed


def test_rank_orders_by_momentum():
    fast = evaluate(good(ticker="FAST.OL"), rising(step=0.004), RULES)
    slow = evaluate(good(ticker="SLOW.OL"), rising(step=0.001), RULES)
    bad = evaluate(good(ticker="BAD.OL", roe=0.01), rising(step=0.01), RULES)
    out = rank([slow, bad, fast])
    assert [e.ticker for e in out] == ["FAST.OL", "SLOW.OL", "BAD.OL"]
    assert fast.rank == 1 and slow.rank == 2 and bad.rank is None


def test_fake_source_runs_through_pipeline():
    from loggbok.pipeline import run_screen
    src = FakeSource()
    res = run_screen(src, src.tickers, RULES)
    assert len(res.evaluations) == len(src.tickers) and not res.errors


def test_rule_summary_counts_fails_and_missing():
    from loggbok.screener import rule_summary
    evs = [evaluate(good(roe=0.01), rising(), RULES), evaluate(good(roe=None), rising(), RULES), evaluate(good(), rising(), RULES)]
    roe = next(c for c in rule_summary(evs) if c["rule"] == "ROE")
    assert (roe["failed"], roe["missing"], roe["checked"]) == (1, 1, 3)
    assert rule_summary(evs)[0]["rule"] == "ROE"
