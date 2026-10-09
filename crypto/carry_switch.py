#!/usr/bin/env python3
"""Strategy 21: can trailing funding tell you when the carry is worth holding?

    python3 crypto/carry_switch.py

Strategy 20 found the always-on carry beats T-bills by 4.0% a year per dollar of
capital, and that all of it arrived in 2024. Funding is persistent, because the
levered-long demand behind it moves slowly. So a rule that holds each coin's
carry only while its trailing funding clears the T-bill yield might keep 2024
and sit out the years that paid nothing.

Per coin, at each rebalance: hold short perp / long spot if

    trailing annualized funding / (1 + perp margin)  >  3-month T-bill yield

and otherwise hold T-bills. The decision on rebalance day r uses funding
through r - 1 only. Every switch on or off pays half of a 4-leg round trip.

Four variants, declared before running: lookback 7 or 30 days, rebalance weekly
or monthly. The primary, named in advance, is 30 days / weekly.
"""

from __future__ import annotations

import sys

import numpy as np
import pandas as pd

from funding_carry import (COST_PER_LEG, PERP_MARGIN, DATA, HERE, load_basis,
                           load_funding, sharpe)
from deflated_sharpe import deflated_sharpe_ratio, expected_max_sharpe
import registry

HALF_TRIP = 2 * COST_PER_LEG          # two legs, one direction
VARIANTS = [(30, "W", True), (30, "M", False), (7, "W", False), (7, "M", False)]


def panel() -> pd.DataFrame:
    f = load_funding()
    b = load_basis()
    fd = f.groupby(["coin", "day"])["rate"].sum().reset_index()
    p = fd.merge(b[["coin", "day", "basis_pnl"]], on=["coin", "day"], how="inner")
    p = p.dropna(subset=["basis_pnl"]).sort_values(["coin", "day"]).reset_index(drop=True)
    tb = pd.read_parquet(DATA / "tbill.parquet").set_index("date")["yld"]
    p["tbill"] = tb.reindex(p["day"].dt.tz_localize(None).dt.normalize(),
                            method="ffill").values
    return p


def run(p: pd.DataFrame, lookback: int | None, freq: str) -> pd.DataFrame:
    """lookback None means always on: the strategy 20 benchmark."""
    p = p.copy()
    g = p.groupby("coin")["rate"]
    # Trailing funding through yesterday, annualized. Shifting by one day is the
    # whole lookahead guard: today's funding is not known at today's open.
    if lookback:
        trail = g.transform(lambda s: s.shift(1).rolling(lookback).sum()) * 365 / lookback
        want = (trail / (1 + PERP_MARGIN) > p["tbill"]).astype(float)
        want[trail.isna()] = 0.0
    else:
        want = pd.Series(1.0, index=p.index)

    # The position only changes on rebalance days, and holds in between.
    period = p["day"].dt.tz_localize(None).dt.to_period(freq)
    first_of_period = period != period.groupby(p["coin"]).shift(1)
    pos = want.where(first_of_period).groupby(p["coin"]).ffill().fillna(0.0)
    if not lookback:
        pos[:] = 1.0

    prev = pos.groupby(p["coin"]).shift(1).fillna(0.0)
    last = p.groupby("coin")["day"].transform("max") == p["day"]
    switches = (pos - prev).abs() + np.where(last, pos, 0.0)   # unwind at the end

    rf = p["tbill"] / 365
    carry = (p["rate"] + p["basis_pnl"]) / (1 + PERP_MARGIN)
    cost = switches * HALF_TRIP / (1 + PERP_MARGIN)
    p["pos"] = pos
    p["switches"] = switches
    p["excess"] = pos * (carry - rf) - cost
    return p


def summarize(p: pd.DataFrame, name: str, n_trials: int) -> dict:
    book = p.groupby("day")["excess"].mean()
    m = book.groupby(book.index.strftime("%Y-%m")).sum()
    coin_years = p.groupby("coin")["day"].agg(lambda d: (d.max() - d.min()).days / 365.25).sum()
    yr = book.groupby(book.index.year).mean() * 365
    sr = sharpe(m, 12)
    return {
        "variant": name,
        "excess": book.mean() * 365,
        "vol": book.std() * np.sqrt(365),
        "monthly_sr": sr,
        "months_pos": (m > 0).mean(),
        "worst_month": m.min(),
        "time_on": p["pos"].mean(),
        "switches_per_coin_year": p["switches"].sum() / coin_years,
        "luck_bar": expected_max_sharpe(n_trials, len(m)),
        "deflated_p": deflated_sharpe_ratio(sr, n_trials, len(m)),
        **{str(y): v for y, v in yr.items()},
    }


def main() -> int:
    p = panel()
    n = registry.trials()["n_trials"]
    print(f"{p.coin.nunique()} coins, {p.day.nunique():,} days, "
          f"{p.day.min().date()} to {p.day.max().date()}, trials to date {n}\n")

    rows = [summarize(run(p, None, "W"), "always on (strategy 20)", n)]
    for lb, freq, primary in VARIANTS:
        name = f"{lb}d / {'weekly' if freq == 'W' else 'monthly'}" + ("  PRIMARY" if primary else "")
        rows.append(summarize(run(p, lb, freq), name, n))

    r = pd.DataFrame(rows).set_index("variant")
    pct = ["excess", "vol", "months_pos", "worst_month", "time_on",
           "2023", "2024", "2025", "2026"]
    fmt = {c: (lambda v: f"{v:7.1%}") for c in pct}
    fmt.update({c: (lambda v: f"{v:6.2f}") for c in
                ["monthly_sr", "switches_per_coin_year", "luck_bar"]})
    fmt["deflated_p"] = lambda v: f"{v:6.3f}"
    print("excess over T-bills, per dollar of capital, after costs")
    print(r[["excess", "vol", "monthly_sr", "luck_bar", "deflated_p", "months_pos",
             "worst_month", "time_on", "switches_per_coin_year"]].to_string(formatters=fmt))
    print("\nexcess by year")
    print(r[["2023", "2024", "2025", "2026"]].to_string(formatters=fmt))

    out = HERE / "reports"
    out.mkdir(exist_ok=True)
    r.to_csv(out / "carry_switch.csv")
    return 0


if __name__ == "__main__":
    sys.path.insert(0, str(HERE))
    sys.exit(main())
