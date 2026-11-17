"""The trade journal: a local SQLite database with every transaction, trade and decision.

See "Stock Tracker Learning Loop" in the Second Brain for why each table exists.
All money in the journal is stored in NOK as well as the trading currency.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path

import pandas as pd

from .config import DATA_DIR

ACCOUNTS = ("paper", "ask", "normal")

SCHEMA = """
CREATE TABLE IF NOT EXISTS rule_versions (
    version TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    rules_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS decision_snapshots (
    id INTEGER PRIMARY KEY,
    ts TEXT NOT NULL,
    ticker TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('buy', 'sell', 'skip')),
    rule_version TEXT,
    data_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY,
    ticker TEXT NOT NULL,
    account TEXT NOT NULL,
    currency TEXT NOT NULL,
    sector TEXT,
    opened_at TEXT NOT NULL,
    closed_at TEXT,
    rule_version TEXT,
    entry_snapshot_id INTEGER REFERENCES decision_snapshots(id),
    exit_snapshot_id INTEGER REFERENCES decision_snapshots(id),
    exit_reason TEXT,
    peak_close REAL,
    peak_close_at TEXT
);
CREATE TABLE IF NOT EXISTS transactions (
    id INTEGER PRIMARY KEY,
    ts TEXT NOT NULL,
    account TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('buy', 'sell', 'dividend', 'fee', 'deposit', 'withdrawal')),
    ticker TEXT,
    quantity REAL,
    price REAL,
    currency TEXT NOT NULL DEFAULT 'NOK',
    fx_rate REAL NOT NULL DEFAULT 1.0,
    fees_nok REAL NOT NULL DEFAULT 0,
    amount_nok REAL NOT NULL,
    trade_id INTEGER REFERENCES trades(id),
    source TEXT NOT NULL DEFAULT 'app',
    note TEXT
);
CREATE TABLE IF NOT EXISTS skipped_signals (
    id INTEGER PRIMARY KEY,
    ts TEXT NOT NULL,
    ticker TEXT NOT NULL,
    rule_version TEXT,
    rank INTEGER,
    reason TEXT NOT NULL,
    snapshot_id INTEGER REFERENCES decision_snapshots(id)
);
CREATE TABLE IF NOT EXISTS daily_snapshots (
    date TEXT NOT NULL,
    account TEXT NOT NULL,
    total_value_nok REAL NOT NULL,
    cash_nok REAL NOT NULL,
    positions_json TEXT NOT NULL,
    PRIMARY KEY (date, account)
);
CREATE TABLE IF NOT EXISTS market_context (
    date TEXT PRIMARY KEY,
    data_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS watchlist (
    ticker TEXT PRIMARY KEY,
    added_at TEXT NOT NULL,
    note TEXT
);
CREATE TABLE IF NOT EXISTS notes (
    id INTEGER PRIMARY KEY,
    ts TEXT NOT NULL,
    trade_id INTEGER REFERENCES trades(id),
    text TEXT NOT NULL
);
"""


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


class Journal:
    def __init__(self, path: Path | str | None = None):
        self.path = Path(path) if path else DATA_DIR / "loggbok.db"
        if str(self.path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(SCHEMA)

    @contextmanager
    def tx(self):
        try:
            yield self.conn
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise

    def close(self):
        self.conn.close()

    # ---- rules and snapshots
    def register_rules(self, rules: dict) -> str:
        version = str(rules["version"])
        with self.tx() as c:
            c.execute("INSERT OR IGNORE INTO rule_versions VALUES (?, ?, ?)",
                      (version, _now(), json.dumps(rules, sort_keys=True)))
        return version

    def add_snapshot(self, ticker: str, kind: str, data: dict, rule_version: str | None, ts: str | None = None) -> int:
        with self.tx() as c:
            cur = c.execute(
                "INSERT INTO decision_snapshots (ts, ticker, kind, rule_version, data_json) VALUES (?, ?, ?, ?, ?)",
                (ts or _now(), ticker, kind, rule_version, json.dumps(data, default=str)))
            return cur.lastrowid

    # ---- cash
    def deposit(self, account: str, amount_nok: float, ts: str | None = None, note: str | None = None):
        self._check_account(account)
        with self.tx() as c:
            c.execute("INSERT INTO transactions (ts, account, kind, amount_nok, note) VALUES (?, ?, 'deposit', ?, ?)",
                      (ts or _now(), account, float(amount_nok), note))

    def withdraw(self, account: str, amount_nok: float, ts: str | None = None, note: str | None = None):
        self._check_account(account)
        with self.tx() as c:
            c.execute("INSERT INTO transactions (ts, account, kind, amount_nok, note) VALUES (?, ?, 'withdrawal', ?, ?)",
                      (ts or _now(), account, -float(amount_nok), note))

    def cash(self, accounts: tuple[str, ...] | list[str]) -> float:
        q = f"SELECT COALESCE(SUM(amount_nok), 0) FROM transactions WHERE account IN ({','.join('?' * len(accounts))})"
        return float(self.conn.execute(q, list(accounts)).fetchone()[0])

    # ---- trading
    def record_buy(self, *, account: str, ticker: str, quantity: float, price: float, currency: str,
                   fx_rate: float = 1.0, fees_nok: float = 0.0, sector: str | None = None,
                   snapshot: dict | None = None, rule_version: str | None = None,
                   ts: str | None = None, source: str = "app", note: str | None = None) -> int:
        """Log a buy. Opens a trade, or adds to the open trade in the same stock and account."""
        self._check_account(account)
        ts = ts or _now()
        snap_id = self.add_snapshot(ticker, "buy", snapshot or {}, rule_version, ts) if snapshot is not None else None
        with self.tx() as c:
            row = c.execute("SELECT id FROM trades WHERE ticker=? AND account=? AND closed_at IS NULL",
                            (ticker, account)).fetchone()
            if row:
                trade_id = row["id"]
            else:
                trade_id = c.execute(
                    "INSERT INTO trades (ticker, account, currency, sector, opened_at, rule_version, entry_snapshot_id, peak_close, peak_close_at)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (ticker, account, currency, sector, ts, rule_version, snap_id, price, ts[:10])).lastrowid
            amount = -(quantity * price * fx_rate) - fees_nok
            c.execute(
                "INSERT INTO transactions (ts, account, kind, ticker, quantity, price, currency, fx_rate, fees_nok, amount_nok, trade_id, source, note)"
                " VALUES (?, ?, 'buy', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (ts, account, ticker, quantity, price, currency, fx_rate, fees_nok, amount, trade_id, source, note))
        return trade_id

    def record_sell(self, *, account: str, ticker: str, quantity: float, price: float, currency: str,
                    fx_rate: float = 1.0, fees_nok: float = 0.0, exit_reason: str = "manual",
                    snapshot: dict | None = None, rule_version: str | None = None,
                    ts: str | None = None, source: str = "app", note: str | None = None) -> int:
        """Log a sell. Closes the trade when the whole position is sold."""
        self._check_account(account)
        ts = ts or _now()
        row = self.conn.execute("SELECT id FROM trades WHERE ticker=? AND account=? AND closed_at IS NULL",
                                (ticker, account)).fetchone()
        if not row:
            raise ValueError(f"No open position in {ticker} on the {account} account")
        trade_id = row["id"]
        held = self._quantity(trade_id)
        if quantity > held + 1e-9:
            raise ValueError(f"Cannot sell {quantity} {ticker}: only {held} held")
        snap_id = self.add_snapshot(ticker, "sell", snapshot or {}, rule_version, ts) if snapshot is not None else None
        with self.tx() as c:
            amount = quantity * price * fx_rate - fees_nok
            c.execute(
                "INSERT INTO transactions (ts, account, kind, ticker, quantity, price, currency, fx_rate, fees_nok, amount_nok, trade_id, source, note)"
                " VALUES (?, ?, 'sell', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (ts, account, ticker, -quantity, price, currency, fx_rate, fees_nok, amount, trade_id, source, note))
            if held - quantity <= 1e-9:
                c.execute("UPDATE trades SET closed_at=?, exit_reason=?, exit_snapshot_id=? WHERE id=?",
                          (ts, exit_reason, snap_id, trade_id))
        return trade_id

    def record_dividend(self, *, account: str, ticker: str, amount_nok: float, ts: str | None = None):
        row = self.conn.execute("SELECT id FROM trades WHERE ticker=? AND account=? ORDER BY opened_at DESC LIMIT 1",
                                (ticker, account)).fetchone()
        with self.tx() as c:
            c.execute("INSERT INTO transactions (ts, account, kind, ticker, amount_nok, trade_id) VALUES (?, ?, 'dividend', ?, ?, ?)",
                      (ts or _now(), account, ticker, float(amount_nok), row["id"] if row else None))

    def _quantity(self, trade_id: int) -> float:
        return float(self.conn.execute(
            "SELECT COALESCE(SUM(quantity), 0) FROM transactions WHERE trade_id=? AND kind IN ('buy','sell')",
            (trade_id,)).fetchone()[0])

    # ---- positions and trades
    def positions(self, accounts: tuple[str, ...] | list[str]) -> pd.DataFrame:
        q = f"""
            SELECT t.id AS trade_id, t.ticker, t.account, t.currency, t.sector, t.opened_at, t.peak_close,
                   SUM(x.quantity) AS quantity,
                   -SUM(CASE WHEN x.kind='buy' THEN x.amount_nok ELSE 0 END) AS cost_nok,
                   SUM(CASE WHEN x.kind='buy' THEN x.quantity ELSE 0 END) AS bought
            FROM trades t JOIN transactions x ON x.trade_id = t.id AND x.kind IN ('buy','sell')
            WHERE t.closed_at IS NULL AND t.account IN ({','.join('?' * len(accounts))})
            GROUP BY t.id
        """
        df = pd.read_sql_query(q, self.conn, params=list(accounts))
        if df.empty:
            return df
        df["avg_cost_nok"] = df["cost_nok"] / df["bought"]
        df["cost_nok"] = df["avg_cost_nok"] * df["quantity"]  # cost of what is still held
        return df.drop(columns=["bought"])

    def update_peaks(self, closes: pd.DataFrame):
        """Keep the highest close since buying on each open trade (used by the trailing stop)."""
        rows = self.conn.execute("SELECT id, ticker, opened_at, peak_close FROM trades WHERE closed_at IS NULL").fetchall()
        with self.tx() as c:
            for r in rows:
                if r["ticker"] not in closes:
                    continue
                s = closes[r["ticker"]].dropna()
                s = s[s.index >= pd.Timestamp(r["opened_at"][:10])]
                if s.empty:
                    continue
                peak, at = float(s.max()), s.idxmax().date().isoformat()
                if r["peak_close"] is None or peak > r["peak_close"]:
                    c.execute("UPDATE trades SET peak_close=?, peak_close_at=? WHERE id=?", (peak, at, r["id"]))

    def closed_trades(self) -> pd.DataFrame:
        q = """
            SELECT t.id AS trade_id, t.ticker, t.account, t.sector, t.opened_at, t.closed_at, t.rule_version,
                   t.exit_reason, t.peak_close,
                   -SUM(CASE WHEN x.kind='buy' THEN x.amount_nok ELSE 0 END) AS cost_nok,
                   SUM(CASE WHEN x.kind IN ('sell','dividend') THEN x.amount_nok ELSE 0 END) AS proceeds_nok
            FROM trades t JOIN transactions x ON x.trade_id = t.id
            WHERE t.closed_at IS NOT NULL
            GROUP BY t.id ORDER BY t.closed_at
        """
        df = pd.read_sql_query(q, self.conn)
        if df.empty:
            return df
        df["result_nok"] = df["proceeds_nok"] - df["cost_nok"]
        df["result_pct"] = df["result_nok"] / df["cost_nok"]
        df["holding_days"] = (pd.to_datetime(df["closed_at"]) - pd.to_datetime(df["opened_at"])).dt.days
        return df

    def transactions(self, accounts: tuple[str, ...] | list[str] | None = None) -> pd.DataFrame:
        if accounts:
            q = f"SELECT * FROM transactions WHERE account IN ({','.join('?' * len(accounts))}) ORDER BY ts"
            return pd.read_sql_query(q, self.conn, params=list(accounts))
        return pd.read_sql_query("SELECT * FROM transactions ORDER BY ts", self.conn)

    # ---- skipped signals, notes, watchlist
    def record_skip(self, *, ticker: str, reason: str, rank: int | None = None, snapshot: dict | None = None,
                    rule_version: str | None = None, ts: str | None = None) -> int:
        ts = ts or _now()
        snap_id = self.add_snapshot(ticker, "skip", snapshot, rule_version, ts) if snapshot is not None else None
        with self.tx() as c:
            return c.execute("INSERT INTO skipped_signals (ts, ticker, rule_version, rank, reason, snapshot_id) VALUES (?, ?, ?, ?, ?, ?)",
                             (ts, ticker, rule_version, rank, reason, snap_id)).lastrowid

    def add_note(self, text: str, trade_id: int | None = None):
        with self.tx() as c:
            c.execute("INSERT INTO notes (ts, trade_id, text) VALUES (?, ?, ?)", (_now(), trade_id, text))

    def watchlist(self) -> list[str]:
        return [r["ticker"] for r in self.conn.execute("SELECT ticker FROM watchlist ORDER BY added_at")]

    def watch(self, ticker: str, note: str | None = None):
        with self.tx() as c:
            c.execute("INSERT OR IGNORE INTO watchlist VALUES (?, ?, ?)", (ticker, _now(), note))

    def unwatch(self, ticker: str):
        with self.tx() as c:
            c.execute("DELETE FROM watchlist WHERE ticker=?", (ticker,))

    # ---- daily snapshots
    def save_daily_snapshot(self, account: str, positions: list[dict], cash_nok: float, day: str | None = None):
        total = cash_nok + sum(p.get("value_nok") or 0 for p in positions)
        with self.tx() as c:
            c.execute("INSERT OR REPLACE INTO daily_snapshots VALUES (?, ?, ?, ?, ?)",
                      (day or date.today().isoformat(), account, total, cash_nok, json.dumps(positions, default=str)))

    def save_market_context(self, data: dict, day: str | None = None):
        with self.tx() as c:
            c.execute("INSERT OR REPLACE INTO market_context VALUES (?, ?)",
                      (day or date.today().isoformat(), json.dumps(data, default=str)))

    def equity_curve(self, accounts: tuple[str, ...] | list[str]) -> pd.DataFrame:
        q = f"""SELECT date, SUM(total_value_nok) AS value_nok, SUM(cash_nok) AS cash_nok
                FROM daily_snapshots WHERE account IN ({','.join('?' * len(accounts))})
                GROUP BY date ORDER BY date"""
        return pd.read_sql_query(q, self.conn, params=list(accounts))

    @staticmethod
    def _check_account(account: str):
        if account not in ACCOUNTS:
            raise ValueError(f"Unknown account {account!r}; use one of {ACCOUNTS}")
