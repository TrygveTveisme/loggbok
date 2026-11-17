"""A made-up data source for tests and for trying the dashboard without internet (LOGGBOK_FAKE_DATA=1)."""
from __future__ import annotations

import numpy as np
import pandas as pd

from .data import Fundamentals

_SECTORS = ["Technology", "Industrials", "Energy", "Healthcare", "Consumer Defensive"]


class FakeSource:
    def __init__(self, tickers: list[str] | None = None, days: int = 400, seed: int = 7):
        self.tickers = tickers or ["AAA.OL", "BBB.OL", "BANK.OL", "CCC", "DDD", "EEE", "FFF", "GGG"]
        self.days = days
        self.rng = np.random.default_rng(seed)
        self._prices = self._make_prices()

    def _make_prices(self) -> pd.DataFrame:
        idx = pd.bdate_range(end=pd.Timestamp.today().normalize(), periods=self.days)
        cols = {}
        for i, tk in enumerate(self.tickers):
            drift = 0.0012 - 0.0004 * (i % 4)  # some up, some flat, some down
            steps = self.rng.normal(drift, 0.015, self.days)
            cols[tk] = 100 * np.exp(np.cumsum(steps))
        return pd.DataFrame(cols, index=idx)

    def prices(self, tickers: list[str], period: str = "5y") -> pd.DataFrame:
        return self._prices[[t for t in tickers if t in self._prices]].copy()

    def fundamentals(self, ticker: str) -> Fundamentals:
        i = self.tickers.index(ticker) if ticker in self.tickers else 0
        oslo = ticker.endswith(".OL")
        cur = "NOK" if oslo else "USD"
        price = float(self._prices[ticker].iloc[-1]) if ticker in self._prices else 100.0
        bank = "BANK" in ticker
        return Fundamentals(
            ticker=ticker, name=f"{ticker.split('.')[0]} Company", currency=cur, financial_currency=cur,
            sector="Financial Services" if bank else _SECTORS[i % len(_SECTORS)],
            industry="Banks - Regional" if bank else "Something",
            market_cap=5e10, avg_daily_volume=2e6, price=price,
            roe=0.18 if i % 3 else 0.08, free_cash_flow=2.5e9, revenue_growth=0.09, net_debt=1e9, ebitda=3e9,
            pe=11.0 if bank else 16.0, pb=1.2, dividend_rate=price * 0.03,
            op_margins=[0.2, 0.19, 0.18, 0.17], pe_history=[18.0, 20.0, 17.0, 19.0],
            annual_dividends={2022: 5.0, 2023: 5.5, 2024: 6.0, 2025: 6.2},
        )

    def fx_to_nok(self, currency: str) -> float:
        return {"NOK": 1.0, "USD": 10.5, "EUR": 11.7}.get(currency, 1.0)
