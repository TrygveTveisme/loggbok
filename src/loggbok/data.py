"""Market data. Everything else talks to a DataSource, so the provider can be swapped.

YFinanceSource uses Yahoo Finance (free, unofficial). Results are cached on disk for a day.
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path
from typing import Protocol

import pandas as pd

from .config import DATA_DIR

FINANCIAL_INDUSTRY_PREFIXES = ("Banks", "Insurance")


@dataclass
class Fundamentals:
    """Key figures for one stock. Money values are in `currency` (the trading currency)
    unless noted. Ratios are fractions (0.12 = 12 %)."""

    ticker: str
    name: str = ""
    currency: str = ""
    financial_currency: str = ""
    sector: str = ""
    industry: str = ""
    market_cap: float | None = None
    avg_daily_volume: float | None = None
    price: float | None = None
    roe: float | None = None
    free_cash_flow: float | None = None  # trading currency
    revenue_growth: float | None = None
    net_debt: float | None = None  # financial currency
    ebitda: float | None = None  # financial currency
    pe: float | None = None
    pb: float | None = None
    dividend_rate: float | None = None  # per share, trading currency
    op_margins: list[float] = field(default_factory=list)  # yearly, newest first
    pe_history: list[float] = field(default_factory=list)  # P/E at past fiscal year ends
    annual_dividends: dict[int, float] = field(default_factory=dict)  # full years only

    @property
    def is_financial(self) -> bool:
        return self.industry.startswith(FINANCIAL_INDUSTRY_PREFIXES)

    @property
    def daily_turnover(self) -> float | None:
        if self.avg_daily_volume is None or self.price is None:
            return None
        return self.avg_daily_volume * self.price

    @property
    def net_debt_ebitda(self) -> float | None:
        if self.net_debt is None or not self.ebitda or self.ebitda <= 0:
            return None
        return self.net_debt / self.ebitda

    @property
    def fcf_yield(self) -> float | None:
        if self.free_cash_flow is None or not self.market_cap:
            return None
        return self.free_cash_flow / self.market_cap

    @property
    def dividend_yield(self) -> float | None:
        if self.dividend_rate is None or not self.price:
            return None
        return self.dividend_rate / self.price

    def to_dict(self) -> dict:
        d = asdict(self)
        d["annual_dividends"] = {str(k): v for k, v in self.annual_dividends.items()}
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Fundamentals":
        d = dict(d)
        d["annual_dividends"] = {int(k): v for k, v in d.get("annual_dividends", {}).items()}
        return cls(**d)


class DataSource(Protocol):
    def prices(self, tickers: list[str], period: str = "5y") -> pd.DataFrame:
        """Daily adjusted close, one column per ticker, naive date index."""

    def fundamentals(self, ticker: str) -> Fundamentals: ...

    def fx_to_nok(self, currency: str) -> float:
        """NOK per one unit of `currency`."""


def _num(x) -> float | None:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(v) or math.isinf(v) else v


class YFinanceSource:
    def __init__(self, cache_dir: Path | None = None, use_cache: bool = True):
        self.cache_dir = (cache_dir or DATA_DIR / "cache") / date.today().isoformat()
        self.use_cache = use_cache
        self._fx: dict[tuple[str, str], float] = {}

    # ---- cache helpers
    def _cache_path(self, name: str) -> Path:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        return self.cache_dir / name

    # ---- prices
    def prices(self, tickers: list[str], period: str = "5y") -> pd.DataFrame:
        import yfinance as yf

        digest = hashlib.sha1(",".join(sorted(tickers)).encode()).hexdigest()[:12]
        key = self._cache_path(f"prices_{period}_{digest}.pkl")
        if self.use_cache and key.exists():
            return pd.read_pickle(key)
        raw = yf.download(tickers, period=period, auto_adjust=True, progress=False, threads=True)
        close = raw["Close"] if isinstance(raw.columns, pd.MultiIndex) else raw[["Close"]]
        if isinstance(close, pd.Series):
            close = close.to_frame(tickers[0])
        if list(close.columns) == ["Close"]:
            close.columns = tickers[:1]
        close.index = pd.to_datetime(close.index).tz_localize(None).normalize()
        if self.use_cache:
            close.to_pickle(key)
        return close

    # ---- currency
    def fx(self, base: str, quote: str) -> float:
        if not base or not quote or base == quote:
            return 1.0
        if (base, quote) not in self._fx:
            import yfinance as yf

            hist = yf.Ticker(f"{base}{quote}=X").history(period="5d")
            self._fx[(base, quote)] = float(hist["Close"].dropna().iloc[-1])
        return self._fx[(base, quote)]

    def fx_to_nok(self, currency: str) -> float:
        return self.fx(currency, "NOK")

    # ---- fundamentals
    def fundamentals(self, ticker: str) -> Fundamentals:
        key = self._cache_path(f"fund_{ticker}.json")
        if self.use_cache and key.exists():
            return Fundamentals.from_dict(json.loads(key.read_text(encoding="utf-8")))
        f = self._fetch_fundamentals(ticker)
        if self.use_cache:
            key.write_text(json.dumps(f.to_dict()), encoding="utf-8")
        return f

    def _fetch_fundamentals(self, ticker: str) -> Fundamentals:
        import yfinance as yf

        tk = yf.Ticker(ticker)
        info = tk.info or {}
        cur = info.get("currency") or ""
        fin_cur = info.get("financialCurrency") or cur
        # Some Oslo companies report in USD or EUR while trading in NOK.
        fin_to_trade = self.fx(fin_cur, cur) if fin_cur and cur and fin_cur != cur else 1.0

        fcf = _num(info.get("freeCashflow"))
        debt, cash = _num(info.get("totalDebt")), _num(info.get("totalCash"))
        net_debt = None if debt is None else debt - (cash or 0.0)

        op_margins: list[float] = []
        pe_history: list[float] = []
        try:
            inc = tk.income_stmt  # columns = fiscal year ends, newest first
            if inc is not None and not inc.empty:
                for col in inc.columns:
                    rev = _num(inc.at["Total Revenue", col]) if "Total Revenue" in inc.index else None
                    op = _num(inc.at["Operating Income", col]) if "Operating Income" in inc.index else None
                    if rev and op is not None:
                        op_margins.append(op / rev)
                pe_history = self._pe_history(tk, inc, fin_to_trade)
        except Exception:  # yfinance raises many kinds of errors for missing statements
            pass

        annual_dividends: dict[int, float] = {}
        try:
            div = tk.dividends
            if div is not None and len(div):
                by_year = div.groupby(div.index.year).sum()
                this_year = date.today().year
                annual_dividends = {int(y): float(v) for y, v in by_year.items() if y < this_year}
        except Exception:
            pass

        return Fundamentals(
            ticker=ticker,
            name=info.get("shortName") or info.get("longName") or ticker,
            currency=cur,
            financial_currency=fin_cur,
            sector=info.get("sector") or "",
            industry=info.get("industry") or "",
            market_cap=_num(info.get("marketCap")),
            avg_daily_volume=_num(info.get("averageDailyVolume10Day") or info.get("averageVolume")),
            price=_num(info.get("currentPrice") or info.get("regularMarketPrice")),
            roe=_num(info.get("returnOnEquity")),
            free_cash_flow=None if fcf is None else fcf * fin_to_trade,
            revenue_growth=_num(info.get("revenueGrowth")),
            net_debt=net_debt,
            ebitda=_num(info.get("ebitda")),
            pe=_num(info.get("trailingPE")),
            pb=_num(info.get("priceToBook")),
            dividend_rate=_num(info.get("dividendRate") or info.get("trailingAnnualDividendRate")),
            op_margins=op_margins,
            pe_history=pe_history,
            annual_dividends=annual_dividends,
        )

    @staticmethod
    def _pe_history(tk, inc: pd.DataFrame, fin_to_trade: float) -> list[float]:
        if "Diluted EPS" not in inc.index:
            return []
        hist = tk.history(period="6y", auto_adjust=False)["Close"]
        hist.index = pd.to_datetime(hist.index).tz_localize(None)
        out = []
        for col in inc.columns:
            eps = _num(inc.at["Diluted EPS", col])
            if not eps or eps <= 0:
                continue
            before = hist[hist.index <= pd.Timestamp(col)]
            if before.empty:
                continue
            out.append(float(before.iloc[-1]) / (eps * fin_to_trade))
        return out
