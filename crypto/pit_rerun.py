#!/usr/bin/env python3
"""Strategies 20, 21 and 22 again, on a universe without survivorship.

    python3 crypto/pit_rerun.py

Same rules, costs, lookbacks and primaries as funding_carry.py, carry_switch.py
and funding_crowding.py. Three things change, all fixed in the registry before
this ran:

  UNIVERSE  each week, the top 20 listed Hyperliquid perps by trailing 30-day
            notional volume, delisted coins included (ingest_hl_universe.py)
  BASIS     minus the daily change in Hyperliquid's premium (perp vs the
            oracle spot index), since delisted coins have no OKX spot to mark
  PRICES    Hyperliquid daily perp closes, for the same reason

Entering or leaving the universe is a trade, and it is charged like one.
"""

from __future__ import annotations

import sys

import numpy as np
import pandas as pd

from funding_carry import (COST_PER_LEG, DATA, HERE, INTEREST_PER_HOUR, PERP_MARGIN,
                           sharpe)
from funding_crowding import MIN_COINS, sr, weights
from deflated_sharpe import deflated_sharpe_ratio, expected_max_sharpe
import registry

HALF_TRIP = 2 * COST_PER_LEG


def daily() -> pd.DataFrame:
    f = pd.read_parquet(DATA / "hl_funding_pit.parquet").sort_values(["coin", "ts"])
    gap = f.groupby("coin")["ts"].diff().dt.total_seconds().div(3600).round()
    f["hours"] = gap.fillna(gap.groupby(f["coin"]).bfill()).fillna(1).clip(upper=8)
    f["interest"] = INTEREST_PER_HOUR * f["hours"]
    f["day"] = f["ts"].dt.floor("D")
    d = f.groupby(["coin", "day"]).agg(rate=("rate", "sum"), interest=("interest", "sum"),
                                       premium=("premium", "last")).reset_index()
    c = pd.read_parquet(DATA / "hl_candles.parquet")[["coin", "day", "close", "high", "delisted"]]
    d = d.merge(c, on=["coin", "day"], how="inner").sort_values(["coin", "day"])
    g = d.groupby("coin")
    d["ret"] = g["close"].pct_change()
    d["basis_pnl"] = -g["premium"].diff()           # long spot, short perp
    d["market"] = d["rate"] - d["interest"]

    u = pd.read_parquet(DATA / "hl_universe.parquet")
    d["week_start"] = (d["day"] - pd.to_timedelta(d["day"].dt.dayofweek, unit="D")).dt.normalize()
    d = d.merge(u[["week_start", "coin"]].assign(member=1.0), on=["week_start", "coin"],
                how="left")
    d["member"] = d["member"].fillna(0.0)

    tb = pd.read_parquet(DATA / "tbill.parquet").set_index("date")["yld"]
    d["tbill"] = tb.reindex(d["day"].dt.tz_localize(None).dt.normalize(), method="ffill").values
    return d.reset_index(drop=True)


def carry_book(d: pd.DataFrame, lookback: int | None) -> pd.DataFrame:
    """lookback None is always on (strategy 20); 30 is strategy 21's primary."""
    d = d.copy()
    g = d.groupby("coin")["rate"]
    if lookback:
        trail = g.transform(lambda s: s.shift(1).rolling(lookback).sum()) * 365 / lookback
        want = (trail / (1 + PERP_MARGIN) > d["tbill"]).astype(float).where(trail.notna(), 0.0)
        week = d["day"].dt.tz_localize(None).dt.to_period("W")
        first = week != week.groupby(d["coin"]).shift(1)
        want = want.where(first).groupby(d["coin"]).ffill().fillna(0.0)
    else:
        want = pd.Series(1.0, index=d.index)
    d["pos"] = want * d["member"]
    prev = d.groupby("coin")["pos"].shift(1).fillna(0.0)
    last = d.groupby("coin")["day"].transform("max") == d["day"]
    switches = (d["pos"] - prev).abs() + np.where(last, d["pos"], 0.0)
    carry = (d["rate"] + d["basis_pnl"].fillna(0.0)) / (1 + PERP_MARGIN)
    d["excess"] = d["pos"] * (carry - d["tbill"] / 365) - switches * HALF_TRIP / (1 + PERP_MARGIN)
    # Equal weight across the universe; a coin switched off sits in T-bills (0 excess).
    return d[d["member"] > 0]


def crowding_book(d: pd.DataFrame, lookback: int) -> tuple[pd.DataFrame, pd.Series]:
    d = d.copy()
    d["signal"] = d.groupby("coin")["rate"].transform(lambda s: s.shift(1).rolling(lookback).sum())
    d["week"] = d["day"].dt.tz_localize(None).dt.to_period("W")
    w = d.groupby(["coin", "week"]).agg(
        signal=("signal", "first"), member=("member", "first"),
        ret=("ret", lambda r: (1 + r.fillna(0)).prod() - 1),
        funding=("rate", "sum"), days=("ret", "size")).reset_index()
    # Membership is decided before the week; a coin that delists mid-week is
    # held to its last close, not dropped, or the losers would vanish again.
    w = w[(w["member"] > 0) & w["signal"].notna()].sort_values(["coin", "week"])
    w = w.reset_index(drop=True)
    w["w"] = weights(w, "xs")
    prev = w.groupby("coin")["w"].shift(1).fillna(0.0)
    w["price"] = w["w"] * w["ret"]
    w["fund"] = -w["w"] * w["funding"]
    w["cost"] = (w["w"] - prev).abs() * COST_PER_LEG
    book = w.groupby("week")[["price", "fund", "cost"]].sum()
    book["net"] = book["price"] + book["fund"] - book["cost"]
    ic = w.groupby("week").apply(
        lambda x: x["signal"].rank().corr(x["ret"].rank()) if len(x) >= MIN_COINS else np.nan,
        include_groups=False).dropna()
    return book, ic


def monthly(x: pd.Series) -> pd.Series:
    return x.groupby(x.index.strftime("%Y-%m")).sum()


def main() -> int:
    d = daily()
    n = registry.trials()["n_trials"]
    mem = d[d["member"] > 0]
    print(f"{d.coin.nunique()} coins with data, {mem.coin.nunique()} ever in the universe, "
          f"{mem[mem.delisted].coin.nunique()} since delisted; "
          f"{mem.day.min().date()} to {mem.day.max().date()}")
    print(f"basis from premium: daily |change| median {d.basis_pnl.abs().median():.3%}, "
          f"99th pct {d.basis_pnl.abs().quantile(0.99):.2%}\n")

    # Funding decomposition on the point-in-time universe.
    yrs = mem.groupby(mem.day.dt.year)
    print("funding on the universe, annualized (equal weight)")
    print(pd.DataFrame({
        "funding": yrs["rate"].mean() * 365, "interest": yrs["interest"].mean() * 365,
        "market": yrs["market"].mean() * 365,
    }).to_string(float_format=lambda v: f"{v:8.1%}"))

    print("\nstrategy 20 / 21: excess over T-bills per dollar of capital, after costs")
    print(f"  {'':<26}{'excess':>8}{'mo. SR':>8}{'bar':>7}{'p':>7}{'worst mo':>10}"
          f"   2023    2024    2025    2026")
    for name, lb, old in [("20 always on", None, "was 4.0%, 0.87"),
                          ("21 switched 30d/weekly", 30, "was 5.9%, 1.71")]:
        b = carry_book(d, lb).groupby("day")["excess"].mean()
        m = monthly(b)
        s = sharpe(m, 12)
        yr = b.groupby(b.index.year).mean() * 365
        print(f"  {name:<26}{b.mean() * 365:8.1%}{s:8.2f}"
              f"{expected_max_sharpe(n, len(m)):7.2f}{deflated_sharpe_ratio(s, n, len(m)):7.3f}"
              f"{m.min():10.1%}  " + "  ".join(f"{yr.get(y, np.nan):6.1%}" for y in range(2023, 2027))
              + f"   ({old})")

    book, ic = crowding_book(d, 7)
    print("\nstrategy 22, cross-sectional 7d (primary), annualized per $1 long / $1 short")
    print(f"  price {book.price.mean() * 52:.1%}  funding {book.fund.mean() * 52:.1%}  "
          f"cost {-book.cost.mean() * 52:.1%}  net {book.net.mean() * 52:.1%}  "
          f"net SR {sr(book.net):.2f}")
    print(f"  IC {ic.mean():+.3f}  t {ic.mean() / ic.std() * np.sqrt(len(ic)):.2f}  "
          f"over {len(ic)} weeks   (was price -88.0%, IC +0.06 t 2.15)")
    print(f"  worst week {book.net.min():.1%}; share of price loss from worst 10 weeks "
          f"{book.price.nsmallest(10).sum() / book.price.sum():.0%}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
