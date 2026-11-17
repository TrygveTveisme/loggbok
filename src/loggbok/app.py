"""Loggbok dashboard. Start with:  loggbok app   (or: streamlit run src/loggbok/app.py)"""
from __future__ import annotations

import os
from datetime import date

import pandas as pd
import streamlit as st

from loggbok.config import load_rules, load_universe
from loggbok.journal import Journal
from loggbok.metrics import breakdown, max_drawdown, trade_stats
from loggbok.pipeline import daily_update, position_values, run_screen
from loggbok.tradelist import build_trade_list

st.set_page_config(page_title="Loggbok", layout="wide")

FAKE = os.environ.get("LOGGBOK_FAKE_DATA") == "1"


@st.cache_resource
def get_source():
    if FAKE:
        from loggbok.fake import FakeSource
        return FakeSource()
    from loggbok.data import YFinanceSource
    return YFinanceSource()


@st.cache_resource
def get_journal() -> Journal:
    return Journal(os.environ.get("LOGGBOK_DB") or None)


@st.cache_data(ttl=12 * 3600, show_spinner=False)
def screen(tickers: tuple[str, ...], rules_version: str):
    bar = st.progress(0.0, text="Fetching data")
    res = run_screen(get_source(), list(tickers), load_rules(),
                     progress=lambda i, n, t: bar.progress((i + 1) / n, text=f"Fetching {t} ({i + 1}/{n})"))
    bar.empty()
    return res


def pct(x):
    return None if x is None or pd.isna(x) else round(100 * x, 1)


rules = load_rules()
journal = get_journal()
version = journal.register_rules(rules)

with st.sidebar:
    st.title("Loggbok")
    mode = st.radio("Mode", ["paper", "real"], help="Paper uses a practice account. Real uses your ASK (Oslo) and normal account (US).")
    accounts = ["paper"] if mode == "paper" else ["ask", "normal"]
    st.caption(f"Rules {version}" + (" · fake data" if FAKE else ""))
    if st.button("Refresh market data"):
        screen.clear()
    universe = load_universe() if not FAKE else {"all": get_source().tickers}
    tickers = tuple(t for ts in universe.values() for t in ts) + tuple(journal.watchlist())

res = screen(tickers, version)
evals = res.by_ticker()

# Keep peaks and the daily snapshot current once per day per mode
key = f"updated-{mode}-{date.today()}"
if key not in st.session_state:
    daily_update(journal, get_source(), accounts, res.closes, res.fx_to_nok)
    st.session_state[key] = True

tab_screen, tab_trades, tab_portfolio, tab_journal = st.tabs(["Screener", "Trade list", "Portfolio", "Journal"])

# ---------------- Screener
with tab_screen:
    eligible = [e for e in res.evaluations if e.eligible]
    st.subheader(f"{len(eligible)} of {len(res.evaluations)} stocks pass all rules")
    only = st.toggle("Show only stocks that pass", value=True)
    rows = []
    for e in res.evaluations:
        if only and not e.eligible:
            continue
        k = e.key_figures
        rows.append({
            "rank": e.rank, "ticker": e.ticker, "name": e.name, "market": e.market, "sector": e.sector,
            "bank/insurer": e.is_financial, "momentum %": pct(e.momentum), "P/E": k["pe"], "ROE %": pct(k["roe"]),
            "FCF yield %": pct(k["fcf_yield"]), "growth %": pct(k["revenue_growth"]),
            "net debt/EBITDA": k["net_debt_ebitda"], "div. yield %": pct(k["dividend_yield"]),
            "failed": ", ".join(e.failed), "missing data": ", ".join(e.missing),
        })
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    if res.errors:
        with st.expander(f"{len(res.errors)} tickers could not be fetched"):
            st.write(res.errors)
    with st.expander("Watchlist"):
        st.write(", ".join(journal.watchlist()) or "Empty.")
        c1, c2 = st.columns(2)
        add = c1.text_input("Add ticker (e.g. EQNR.OL or AAPL)")
        if c1.button("Add") and add:
            journal.watch(add.strip().upper())
            st.rerun()
        rem = c2.selectbox("Remove", [""] + journal.watchlist())
        if c2.button("Remove") and rem:
            journal.unwatch(rem)
            st.rerun()

# ---------------- Trade list
with tab_trades:
    positions = journal.positions(accounts)
    cash = journal.cash(accounts)
    tl = build_trade_list(evaluations=evals, closes=res.closes, positions=positions, cash_nok=cash,
                          fx_to_nok=res.fx_to_nok, rules=rules, mode=mode)
    st.subheader("This check's trade list")
    st.caption(f"Portfolio value {tl.portfolio_value_nok:,.0f} kr · cash {tl.cash_nok:,.0f} kr · "
               f"after the list {tl.cash_after_nok:,.0f} kr. Check every {rules['buying']['check_every_days']} days.")
    if cash <= 0 and positions.empty:
        st.info("No money in this account yet. Add a deposit under Portfolio first.")
    if not tl.orders:
        st.write("Nothing to do this time.")
    for i, o in enumerate(tl.orders):
        with st.container(border=True):
            st.markdown(f"**{o.action.upper()} {o.quantity:g} {o.ticker}** ({o.account}) at about "
                        f"{o.price:,.2f} {o.currency} ≈ {o.value_nok:,.0f} kr · {o.reason}")
            with st.form(f"order-{i}"):
                c1, c2, c3, c4 = st.columns(4)
                qty = c1.number_input("Quantity", value=float(o.quantity), min_value=0.0, step=1.0)
                price = c2.number_input(f"Price ({o.currency})", value=float(round(o.price, 2)), min_value=0.0)
                fx = c3.number_input("NOK per unit", value=float(round(o.fx_rate, 4)), min_value=0.0, disabled=o.currency == "NOK")
                fees = c4.number_input("Fees (kr)", value=0.0, min_value=0.0)
                note = st.text_input("Why (one line, optional)")
                done, skip = st.columns(2)
                if done.form_submit_button("Done, log it"):
                    common = dict(account=o.account, ticker=o.ticker, quantity=qty, price=price, currency=o.currency,
                                  fx_rate=fx if o.currency != "NOK" else 1.0, fees_nok=fees, snapshot=o.snapshot,
                                  rule_version=version, note=note or None)
                    if o.action == "buy":
                        journal.record_buy(sector=o.sector, **common)
                    else:
                        journal.record_sell(exit_reason="trim" if o.action == "trim" else o.reason.split(":")[0], **common)
                    st.success("Logged.")
                    st.rerun()
                if skip.form_submit_button("Not doing it"):
                    journal.record_skip(ticker=o.ticker, reason=f"{o.action} not done: {note or 'no reason given'}",
                                        rank=o.rank, snapshot=o.snapshot, rule_version=version)
                    st.info("Logged as skipped.")
    if tl.skipped:
        with st.expander(f"{len(tl.skipped)} candidates the rules skipped"):
            st.dataframe(pd.DataFrame([{"rank": s.rank, "ticker": s.ticker, "reason": s.reason} for s in tl.skipped]),
                         hide_index=True)
            if st.button("Save these to the journal"):
                for s in tl.skipped:
                    journal.record_skip(ticker=s.ticker, reason=s.reason, rank=s.rank, snapshot=s.snapshot, rule_version=version)
                st.success("Saved.")

# ---------------- Portfolio
with tab_portfolio:
    positions = journal.positions(accounts)
    cash = journal.cash(accounts)
    vals = position_values(positions, res.closes, res.fx_to_nok)
    total = cash + sum(v["value_nok"] or 0 for v in vals)
    c1, c2, c3 = st.columns(3)
    c1.metric("Total value", f"{total:,.0f} kr")
    c2.metric("Cash", f"{cash:,.0f} kr")
    c3.metric("Positions", len(vals))
    if vals:
        df = pd.DataFrame(vals)
        df["result %"] = (df["value_nok"] / df["cost_nok"] - 1) * 100
        df["weight %"] = df["value_nok"] / total * 100
        peaks = dict(zip(positions["ticker"], positions["peak_close"]))
        df["stop at"] = [None if peaks.get(t) is None else round(peaks[t] * (1 - rules["selling"]["trailing_stop"]), 2) for t in df["ticker"]]
        st.dataframe(df.round(2), hide_index=True, width="stretch")
    curve = journal.equity_curve(accounts)
    if len(curve) > 1:
        st.line_chart(curve.set_index("date")["value_nok"])
        dd = max_drawdown(curve["value_nok"])
        st.caption(f"Largest drop from a peak: {dd:.1%}" if dd is not None else "")
    with st.expander("Deposit or withdraw"):
        acc = st.selectbox("Account", accounts)
        amount = st.number_input("Amount (kr)", min_value=0.0, step=1000.0)
        d1, d2 = st.columns(2)
        if d1.button("Deposit") and amount:
            journal.deposit(acc, amount)
            st.rerun()
        if d2.button("Withdraw") and amount:
            journal.withdraw(acc, amount)
            st.rerun()

# ---------------- Journal
with tab_journal:
    closed = journal.closed_trades()
    if mode == "paper":
        closed = closed[closed["account"] == "paper"] if not closed.empty else closed
    else:
        closed = closed[closed["account"] != "paper"] if not closed.empty else closed
    stats = trade_stats(closed)
    st.subheader(f"{stats['trades']} closed trades")
    if stats["trades"]:
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Win rate", f"{stats['win_rate']:.0%}")
        c2.metric("Avg win / loss", f"{pct(stats['avg_win_pct']) or 0}% / {pct(stats['avg_loss_pct']) or 0}%")
        c3.metric("Expected per trade", f"{pct(stats['expectancy_pct'])}%")
        c4.metric("Result", f"{stats['total_result_nok']:,.0f} kr")
        if stats["trades"] < 30:
            st.caption("Fewer than 30 closed trades: too early to judge the rules (see the learning loop).")
        for by in ("exit_reason", "sector", "rule_version"):
            st.markdown(f"**By {by.replace('_', ' ')}**")
            st.dataframe(breakdown(closed, by), width="stretch")
        st.dataframe(closed, hide_index=True, width="stretch")
    with st.expander("Add a note"):
        text = st.text_area("Note")
        if st.button("Save note") and text:
            journal.add_note(text)
            st.success("Saved.")
    with st.expander("All transactions"):
        st.dataframe(journal.transactions(accounts), hide_index=True, width="stretch")
