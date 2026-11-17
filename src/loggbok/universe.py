"""Refresh the ticker files used by config/universe.yaml (`loggbok universe`)."""
from __future__ import annotations

import csv
import io
import urllib.request
from pathlib import Path

from .config import CONFIG_DIR

SP500_CSV = "https://raw.githubusercontent.com/datasets/s-and-p-500-companies/main/data/constituents.csv"


def _write(path: Path, header: str, tickers: list[str]) -> int:
    path.write_text(f"# {header}\n" + "\n".join(sorted(set(tickers))) + "\n", encoding="utf-8")
    return len(set(tickers))


def refresh_sp500(path: Path | None = None) -> int:
    with urllib.request.urlopen(SP500_CSV, timeout=30) as r:
        rows = list(csv.DictReader(io.StringIO(r.read().decode("utf-8"))))
    tickers = [row["Symbol"].strip().replace(".", "-") for row in rows if row.get("Symbol")]
    if len(tickers) < 400:
        raise RuntimeError(f"Only {len(tickers)} S&P 500 tickers found; keeping the old file")
    return _write(path or CONFIG_DIR / "sp500.txt", "S&P 500 members (Yahoo tickers). Refresh with: loggbok universe", tickers)


def refresh_oslo(path: Path | None = None, min_market_cap: float = 1e9) -> int:
    """All Oslo Børs stocks over `min_market_cap` kr, from Yahoo's stock screener."""
    import yfinance as yf
    from yfinance import EquityQuery

    query = EquityQuery("and", [EquityQuery("eq", ["exchange", "OSL"]),
                                EquityQuery("gt", ["intradaymarketcap", min_market_cap])])
    tickers, offset = [], 0
    while True:
        page = yf.screen(query, offset=offset, size=250, sortField="intradaymarketcap", sortAsc=False)
        quotes = page.get("quotes", [])
        tickers += [q["symbol"] for q in quotes if q.get("symbol", "").endswith(".OL")]
        offset += len(quotes)
        if not quotes or offset >= page.get("total", 0):
            break
    if len(tickers) < 30:
        raise RuntimeError(f"Only {len(tickers)} Oslo tickers found; keeping the old file")
    return _write(path or CONFIG_DIR / "oslo.txt", "Oslo Børs stocks over 1 bn kr (Yahoo screener). Refresh with: loggbok universe", tickers)
