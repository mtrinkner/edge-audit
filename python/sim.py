"""Event-driven simulator over the point-in-time layer. See docs/INFRASTRUCTURE.md.

A study provides decide(view, book) -> list[Order]. The view is the PIT data
known at the close of the day (pit.View); the book is the study's open lots
and pending orders. decide() runs sealed: no file reads (pit.sealed).

Each trading day D:
1. Open: corporate actions with ex date D are applied (shares / factor;
   the fractional policy decides what happens to a fraction). Pending
   orders flagged cancel_on_action for that security are cancelled.
   Orders filling at the open (fill="open_mhs") are tried.
2. Close: pending orders filling at the close are tried; then decide() runs
   and its fill="close" orders are tried at today's close; fill="next_close"
   and fill="open_mhs" orders wait for later days.
3. Expired orders are recorded as unfilled; NAV is marked.

Fills:
- buys at the ask, sells at the bid, at the close of the fill day; or the
  open minus half the closing spread (fill="open_mhs");
- a quote is valid only if bid > 0, ask >= bid and bid >= stub x ask (stub
  0.9 by default); with no valid quote an order waits until good_until;
- trades settle T+3 before 2017-09-05, T+2 until 2024-05-28, T+1 after; a lot
  whose settlement falls on or after an ex date is not of record for it;
- fees by broker schedule; position-count and position-size caps.
Cash earns the T-bill rate known at t (pit dataset "tbill"); NAV is marked at
the bid (the last valid bid if today's isn't).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

import pit as P

STUB = 0.90


def settlement_days(trade_date: str) -> int:
    return 3 if trade_date < "2017-09-05" else 2 if trade_date < "2024-05-28" else 1


@dataclass(frozen=True)
class Broker:
    name: str
    per_trade: float = 0.0           # commission per order
    corporate_action: float = 0.0    # per voluntary corporate action (tenders, redemptions)


BROKERS = {"zero": Broker("zero"), "flat5": Broker("flat5", per_trade=5.0),
           "etrade_voluntary": Broker("etrade_voluntary", corporate_action=38.0)}


@dataclass
class Order:
    key: int                          # security (permno)
    side: str                         # "buy" or "sell"
    qty: float | None = None          # buy: shares; sell: None = the whole lot
    lot: str | None = None            # buy: the new lot's id; sell: the lot to sell
    fill: str = "close"               # "close", "next_close", or "open_mhs"
    good_from: str | None = None
    good_until: str | None = None     # inclusive; None = only the first eligible day
    first_row_only: bool = False      # try only the first day the security trades
    cancel_on_action: bool = False    # cancelled if a corporate action hits the security first
    tag: str = ""
    placed: str | None = None


@dataclass
class Lot:
    id: str
    key: int
    qty: float
    entry_day: str
    entry_px: float
    settle_day: str
    fee_in: float
    tag: str
    action_exdt: str | None = None
    factor: float | None = None
    of_record: bool | None = None
    qty_before_rounding: float | None = None
    exit_day: str | None = None
    exit_px: float = np.nan
    fee_out: float = 0.0
    exit_tag: str = ""


@dataclass
class Book:
    lots: dict = field(default_factory=dict)          # open lots by id
    closed: dict = field(default_factory=dict)
    pending: list = field(default_factory=list)
    unfilled: list = field(default_factory=list)      # (order, reason, day)


def quote(row: pd.Series | None, stub: float = STUB) -> tuple[float, float]:
    """(bid, ask), NaN unless valid."""
    if row is None:
        return np.nan, np.nan
    b, a = float(row["dlybid"]) if pd.notna(row["dlybid"]) else np.nan, \
        float(row["dlyask"]) if pd.notna(row["dlyask"]) else np.nan
    ok = b > 0 and a >= b and b >= stub * a
    return (b, a) if ok else (np.nan, np.nan)


class Sim:
    def __init__(self, pit: P.PIT, prices: str = "crsp_daily", actions: str | None = None,
                 broker: Broker = BROKERS["zero"], fractional: str = "roundup", capital: float = 25_000.0,
                 max_positions: int | None = None, max_position_usd: float | None = None, stub: float = STUB):
        if fractional not in ("roundup", "cash", "down"):
            raise ValueError("fractional is roundup, cash or down")
        self.pit, self.cal = pit, pit.cal
        self.prices, self.actions = prices, actions
        self.broker, self.fractional, self.capital = broker, fractional, capital
        self.max_positions, self.max_position_usd, self.stub = max_positions, max_position_usd, stub

    # ---- market data at a moment (the simulator itself is not sealed: it fills)
    def _row(self, key: int, day: str) -> pd.Series | None:
        r = self.pit.asof(self.prices, P.at(day, P.CLOSE), latest=True, permno=key)
        if r.empty or r["dlycaldt"].iloc[-1] != day:
            return None
        return r.iloc[-1]

    def _fee(self) -> float:
        return self.broker.per_trade

    def _try(self, o: Order, day: str, book: Book, phase: str) -> bool:
        """Try one order on `day` in `phase` ("open" or "close"). True if done (filled or dropped)."""
        if o.good_from and day < o.good_from:
            return False
        if (o.fill == "open_mhs") != (phase == "open"):
            return False
        row = self._row(o.key, day)
        last_chance = o.good_until is None or day >= o.good_until
        if row is None:
            if o.first_row_only:
                return False
            if last_chance:
                book.unfilled.append((o, "no_row", day))
                return True
            return False
        bid, ask = quote(row, self.stub)
        if o.fill == "open_mhs":
            px = float(row["dlyopen"]) - (float(row["dlyask"]) - float(row["dlybid"])) / 2 if bid == bid else np.nan
        else:
            px = ask if o.side == "buy" else bid
        if not px == px:
            if o.first_row_only or last_chance:
                book.unfilled.append((o, "no_valid_quote", day))
                return True
            return False
        if o.side == "buy":
            if self.max_positions is not None and len(book.lots) >= self.max_positions:
                book.unfilled.append((o, "max_positions", day))
                return True
            if self.max_position_usd is not None and o.qty * px > self.max_position_usd:
                book.unfilled.append((o, "max_position_usd", day))
                return True
            lid = o.lot or f"{o.key}:{day}:{len(book.lots) + len(book.closed)}"
            fee = self._fee()
            book.lots[lid] = Lot(lid, o.key, float(o.qty), day, px,
                                 self.cal.shift(day, settlement_days(day)), fee, o.tag)
            self.cash -= o.qty * px + fee
        else:
            lot = book.lots.pop(o.lot, None)
            if lot is None:
                book.unfilled.append((o, "no_lot", day))
                return True
            lot.exit_day, lot.exit_px, lot.fee_out, lot.exit_tag = day, px, self._fee(), o.tag
            self.cash += lot.qty * px - lot.fee_out
            book.closed[lot.id] = lot
        return True

    @staticmethod
    def _drop_done(book: Book) -> None:
        """Sells for a lot already closed are moot (the lot was sold another way)."""
        book.pending = [o for o in book.pending if not (o.side == "sell" and o.lot in book.closed)]

    def _actions(self, day: str, book: Book) -> None:
        if not self.actions:
            return
        a = self.pit.asof(self.actions, P.at(day, P.OPEN), exdt=day)
        for r in a.itertuples():
            hit = [l for l in book.lots.values() if l.key == r.permno]
            for l in hit:
                new = l.qty / r.factor
                l.action_exdt, l.factor, l.qty_before_rounding = day, float(r.factor), new
                l.of_record = l.settle_day < day
                frac = new - math.floor(new + 1e-9)
                if frac > 1e-9 and l.of_record:
                    new = math.ceil(new) if self.fractional == "roundup" else \
                        math.floor(new) if self.fractional == "down" else new
                l.qty = new
            if hit:
                for o in [o for o in book.pending if o.cancel_on_action and o.key == r.permno]:
                    book.unfilled.append((o, "cancelled_by_action", day))
                    book.pending.remove(o)

    def _rate(self, day: str) -> float:
        if "tbill" not in self.pit.ds:
            return 0.0
        r = self.pit.asof("tbill", P.at(day, P.OPEN), date=day)
        return float(r["rf"].iloc[-1]) if len(r) else 0.0

    def run(self, study, start: str, end: str) -> dict:
        book = Book()
        self.cash = self.capital
        nav, last_bid = [], {}
        days = self.cal.range(start, end)
        for i, day in enumerate(days):
            self.cash *= 1 + self._rate(day)
            self._actions(day, book)
            book.pending = [o for o in book.pending if not self._try(o, day, book, "open")]
            self._drop_done(book)
            book.pending = [o for o in book.pending if not self._try(o, day, book, "close")]
            self._drop_done(book)
            with P.sealed():
                orders = study.decide(self.pit.view(P.at(day, P.CLOSE)), book) or []
            nxt = days[i + 1] if i + 1 < len(days) else self.cal.shift(day, 1)
            for o in orders:
                o.placed = day
                if o.good_from is None:
                    o.good_from = day if o.fill == "close" else nxt
                if o.fill == "close" and o.good_from == day and self._try(o, day, book, "close"):
                    self._drop_done(book)
                    continue
                book.pending.append(o)
            for o in [o for o in book.pending if o.good_until is not None and o.good_until <= day
                      and not o.first_row_only]:
                book.unfilled.append((o, "expired", day))
                book.pending.remove(o)
            mv = 0.0
            for l in book.lots.values():
                b, _ = quote(self._row(l.key, day), self.stub)
                if b == b:
                    last_bid[l.key] = b
                mv += l.qty * last_bid.get(l.key, l.entry_px)
            nav.append({"day": day, "cash": self.cash, "positions": len(book.lots), "market_value": mv,
                        "nav": self.cash + mv})
        lots = list(book.closed.values()) + list(book.lots.values())
        trades = pd.DataFrame([vars(l) for l in lots]) if lots else pd.DataFrame(columns=list(Lot.__annotations__))
        unfilled = pd.DataFrame([{"key": o.key, "side": o.side, "lot": o.lot, "tag": o.tag, "fill": o.fill,
                                  "placed": o.placed, "reason": why, "day": d} for o, why, d in book.unfilled])
        return {"trades": trades, "nav": pd.DataFrame(nav), "unfilled": unfilled, "book": book}
