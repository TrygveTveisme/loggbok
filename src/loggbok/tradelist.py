"""Build the fortnightly trade list: what to sell, trim and buy according to the rules."""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import pandas as pd

from .config import market_of
from .indicators import sma
from .screener import Evaluation


@dataclass
class Order:
    action: str  # buy, sell, trim
    ticker: str
    account: str
    quantity: float
    price: float
    currency: str
    fx_rate: float
    reason: str
    rank: int | None = None
    sector: str | None = None
    snapshot: dict = field(default_factory=dict)

    @property
    def value_nok(self) -> float:
        return self.quantity * self.price * self.fx_rate


@dataclass
class Skip:
    ticker: str
    reason: str
    rank: int | None = None
    snapshot: dict = field(default_factory=dict)


@dataclass
class TradeList:
    orders: list[Order]
    skipped: list[Skip]
    portfolio_value_nok: float
    cash_nok: float
    cash_after_nok: float
    sector_weights_after: dict[str, float]


def account_for(ticker: str, mode: str) -> str:
    """Paper mode uses one paper account. Real mode: Oslo stocks in the ASK, US stocks in a normal account."""
    if mode == "paper":
        return "paper"
    return "ask" if market_of(ticker) == "oslo" else "normal"


def build_trade_list(*, evaluations: dict[str, Evaluation], closes: pd.DataFrame, positions: pd.DataFrame,
                     cash_nok: float, fx_to_nok: dict[str, float], rules: dict, mode: str = "paper") -> TradeList:
    b, s, t = rules["buying"], rules["selling"], rules["trend"]
    orders: list[Order] = []
    skipped: list[Skip] = []

    def last(tk: str) -> float | None:
        if tk in closes and closes[tk].notna().any():
            return float(closes[tk].dropna().iloc[-1])
        ev = evaluations.get(tk)
        return ev.last_close if ev else None

    # Current holdings, valued in NOK
    holdings = []
    for p in (positions.to_dict("records") if positions is not None and not positions.empty else []):
        price = last(p["ticker"])
        fx = fx_to_nok.get(p["currency"], 1.0)
        value = (price or 0.0) * p["quantity"] * fx
        holdings.append({**p, "price": price, "fx": fx, "value_nok": value})
    value_total = cash_nok + sum(h["value_nok"] for h in holdings)
    cash = cash_nok
    sector_value: dict[str, float] = {}
    for h in holdings:
        sector_value[h["sector"] or "Unknown"] = sector_value.get(h["sector"] or "Unknown", 0.0) + h["value_nok"]

    # 1. Sells and trims
    sold = set()
    for h in holdings:
        tk, price = h["ticker"], h["price"]
        if price is None:
            continue
        ev = evaluations.get(tk)
        snap = ev.snapshot() if ev else {}
        series = closes[tk].dropna() if tk in closes else pd.Series(dtype=float)
        since = series[series.index >= pd.Timestamp(str(h["opened_at"])[:10])]
        peak = max([x for x in (h.get("peak_close"), float(since.max()) if len(since) else None) if x is not None],
                   default=price)
        long_ma = sma(series, t["long_ma"])
        reason = None
        if price <= peak * (1 - s["trailing_stop"]):
            reason = f"trailing stop: {price:.2f} is {1 - price / peak:.0%} below peak {peak:.2f}"
        elif s.get("sell_below_long_ma") and long_ma is not None and price < long_ma:
            reason = f"trend broken: below MA{t['long_ma']} ({long_ma:.2f})"
        elif s.get("sell_on_quality_lost") and ev is not None and not ev.passes_quality:
            reason = "quality lost: " + ", ".join(r for r in ev.failed if r) if ev.failed else "quality lost"
        common = dict(ticker=tk, account=h["account"], price=price, currency=h["currency"], fx_rate=h["fx"],
                      sector=h["sector"], snapshot={**snap, "peak_close": peak})
        if reason:
            orders.append(Order(action="sell", quantity=h["quantity"], reason=reason, **common))
            cash += h["value_nok"]
            sector_value[h["sector"] or "Unknown"] -= h["value_nok"]
            sold.add(tk)
            continue
        weight = h["value_nok"] / value_total if value_total else 0
        if weight > s["trim_above"]:
            target = s["trim_to"] * value_total
            qty = math.floor((h["value_nok"] - target) / (price * h["fx"]))
            if qty > 0:
                orders.append(Order(action="trim", quantity=qty, reason=f"weight {weight:.0%} above {s['trim_above']:.0%}", **common))
                cash += qty * price * h["fx"]
                sector_value[h["sector"] or "Unknown"] -= qty * price * h["fx"]

    # 2. Buys
    held = {h["ticker"] for h in holdings} - sold
    slots = b["max_positions"] - len(held)
    ranked = sorted([e for e in evaluations.values() if e.eligible and e.rank is not None], key=lambda e: e.rank)
    buys = 0
    target_value = b["position_size"] * value_total
    for ev in ranked:
        if buys >= b["max_new_per_check"] or slots <= 0:
            break
        if ev.ticker in held:
            continue
        sector = ev.sector or "Unknown"
        snap = ev.snapshot()
        if (sector_value.get(sector, 0.0) + target_value) / value_total > b["max_sector_weight"] if value_total else True:
            skipped.append(Skip(ev.ticker, f"sector cap: {sector} would exceed {b['max_sector_weight']:.0%}", ev.rank, snap))
            continue
        amount = min(target_value, cash)
        if amount < b["min_order_share"] * target_value:
            skipped.append(Skip(ev.ticker, "not enough cash", ev.rank, snap))
            continue
        price = last(ev.ticker)
        fx = fx_to_nok.get(ev.currency, 1.0)
        if not price:
            skipped.append(Skip(ev.ticker, "no price", ev.rank, snap))
            continue
        qty = math.floor(amount / (price * fx))
        if qty < 1:
            skipped.append(Skip(ev.ticker, "price too high for position size", ev.rank, snap))
            continue
        o = Order(action="buy", ticker=ev.ticker, account=account_for(ev.ticker, mode), quantity=qty, price=price,
                  currency=ev.currency, fx_rate=fx, reason=f"rank {ev.rank}, momentum {ev.momentum:+.0%}" + (f" + bonus: {', '.join(ev.bonuses)}" if ev.bonuses else "") if ev.momentum is not None else f"rank {ev.rank}",
                  rank=ev.rank, sector=sector, snapshot=snap)
        orders.append(o)
        cash -= o.value_nok
        sector_value[sector] = sector_value.get(sector, 0.0) + o.value_nok
        buys += 1
        slots -= 1

    weights = {k: v / value_total for k, v in sector_value.items() if value_total and v > 0}
    return TradeList(orders, skipped, value_total, cash_nok, cash, weights)
