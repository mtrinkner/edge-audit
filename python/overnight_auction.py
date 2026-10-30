#!/usr/bin/env python3
"""The overnight premium, traded through the auctions on mega-caps (trial 44).

    python3 python/overnight_auction.py

Trial 17 found that most of the stock market's return happens overnight, but
crossing the spread twice a day cost as much as the premium paid. The
2026-10-14 audit found the cost models overstated costs for expensive liquid
names. And the realistic way to trade close-to-open is through the auctions:
a market-on-close buy and a market-on-open sell fill at the official prints,
so no spread is crossed.

SPEC, fixed in the registry first:
  universe  50 highest median daily dollar volume, ranked on the PREVIOUS month
  trade     buy at the close, sell at the next open, equal weight, every day
  prices    the open is put on the adjusted basis with that day's adj_close/close
  cost      1bp round trip
  pass      net daily t >= 2.0, net Sharpe > 0.626, AND net Sharpe above simply
            holding the same names close to close

FIXED 2026-10-26 (audit): this script never called lockbox.guard and loaded
bars without a date filter, so the original reports/overnight_auction.csv
(kept as overnight_auction_pre_2026-10-26.csv) scored 2026-10-07, a sealed
date. It now loads through data_access, which stops at the seal.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "python"))

import data_access as da
from deflated_sharpe import expected_max_sharpe
from evaluation import clustered_mean, sharpe

TOP_N = 50
COST = 1e-4
BAR = 0.626


def load() -> pd.DataFrame:
    # Through data_access, so nothing on or after the lockbox seal is read
    # (fixed 2026-10-26; the original run scored 2026-10-07).
    d = da.bars("overnight_auction",
                columns=("symbol", "dt", "open", "close", "adj_close", "volume"))
    spy = da.sql("overnight_auction",
                 "SELECT dt, open, close, adj_close FROM bars WHERE symbol='SPY'",
                 order_by="dt")
    return d, spy


def returns(d: pd.DataFrame) -> pd.DataFrame:
    d = d.copy()
    f = d["adj_close"] / d["close"]
    d["aopen"] = d["open"] * f
    g = d.groupby("symbol")
    prev = g["adj_close"].shift(1)
    d["night"] = d["aopen"] / prev - 1.0          # yesterday's close -> today's open
    d["day"] = d["adj_close"] / d["aopen"] - 1.0  # today's open -> today's close
    d["hold"] = d["adj_close"] / prev - 1.0
    # The night that ends on dt belongs to the position bought at the previous
    # close. Membership must be known at that close, so use the previous date.
    d["entry_dt"] = g["dt"].shift(1)
    return d


def members(d: pd.DataFrame) -> set:
    d = d.assign(dv=d["close"] * d["volume"], month=d["dt"].str[:7])
    m = d.groupby(["symbol", "month"])["dv"].median().reset_index()
    months = sorted(m["month"].unique())
    nxt = dict(zip(months[:-1], months[1:]))
    m["rank"] = m.groupby("month")["dv"].rank(ascending=False)
    top = m[m["rank"] <= TOP_N]
    return {(s, nxt[mo]) for s, mo in zip(top["symbol"], top["month"]) if mo in nxt}


def main() -> int:
    d, spy = load()
    d = returns(d)
    mem = members(d)
    d = d.dropna(subset=["night", "entry_dt"])
    d = d[[(s, e[:7]) in mem for s, e in zip(d["symbol"], d["entry_dt"])]]
    extreme = (d["night"].abs() > 0.5).sum()
    print(f"trial 44: {d['symbol'].nunique()} distinct mega-caps, {len(d):,} stock-nights, "
          f"{d['dt'].nunique():,} nights ({d['dt'].min()} to {d['dt'].max()})")
    print(f"  nights with |return| > 50% (data check): {extreme}\n")

    d["net"] = d["night"] - COST
    c = clustered_mean(d["net"], d["dt"])
    nights = d.groupby("dt")["net"].mean()
    hold = d.groupby("dt")["hold"].mean()
    day = d.groupby("dt")["day"].mean()
    sr, sr_hold = sharpe(nights, 252), sharpe(hold, 252)
    n = len(nights)

    print("PRE-DECLARED TEST")
    print(f"  net mean per night  {c['mean'] * 1e4:+.2f}bp  t={c['t']:.2f}   "
          f"{'PASS' if c['t'] >= 2 else 'FAIL'}")
    print(f"  net Sharpe          {sr:.3f}   vs luck bar {BAR}   "
          f"{'PASS' if sr > BAR else 'FAIL'}   (raw-N bar {expected_max_sharpe(839, n, 252):.3f})")
    print(f"  holding Sharpe      {sr_hold:.3f}   {'PASS' if sr > sr_hold else 'FAIL'} "
          f"(overnight must beat simply holding)")
    ok = c["t"] >= 2 and sr > BAR and sr > sr_hold
    print(f"\n  VERDICT: {'PASS' if ok else 'FAIL'}")

    ann = lambda s: (1 + s).prod() ** (252 / len(s)) - 1
    print("\ndescriptive only:")
    print(f"  annualized: overnight net {ann(nights):+.1%} (vol {nights.std() * 252 ** .5:.1%}), "
          f"intraday {ann(day):+.1%} (vol {day.std() * 252 ** .5:.1%}), "
          f"hold {ann(hold):+.1%} (vol {hold.std() * 252 ** .5:.1%})")
    for cst in (0.5, 2.0, 4.0):
        s = d.groupby("dt")["night"].mean() - cst * 1e-4
        print(f"  at {cst:.1f}bp round trip: Sharpe {sharpe(s, 252):.3f}, "
              f"{ann(s):+.1%}/yr")
    ls = (d.groupby("dt")["night"].mean() - d.groupby("dt")["day"].mean() - 2 * COST)
    print(f"  long overnight / short intraday, net 2bp: Sharpe {sharpe(ls, 252):.3f}, "
          f"{ann(ls):+.1%}/yr")
    yr = nights.groupby(nights.index.str[:4])
    for y, s in yr:
        print(f"  {y}: {s.mean() * 1e4:+.2f}bp/night, Sharpe {sharpe(s, 252):+.2f}")

    s = spy.copy()
    s["aopen"] = s["open"] * s["adj_close"] / s["close"]
    s["night"] = s["aopen"] / s["adj_close"].shift(1) - 1
    s["hold"] = s["adj_close"] / s["adj_close"].shift(1) - 1
    s = s.dropna().set_index("dt")
    print(f"  SPY overnight net 1bp: Sharpe {sharpe(s['night'] - COST, 252):.3f}, "
          f"{ann(s['night'] - COST):+.1%}/yr; SPY hold Sharpe {sharpe(s['hold'], 252):.3f}")
    j = pd.concat([nights, s["hold"]], axis=1, join="inner").dropna()
    beta = np.cov(j.iloc[:, 0], j.iloc[:, 1])[0, 1] / j.iloc[:, 1].var()
    print(f"  beta of the overnight book to SPY daily return: {beta:+.2f}")
    pd.DataFrame({"overnight_net": nights, "hold": hold, "intraday": day}).to_csv(
        ROOT / "reports" / "overnight_auction.csv")
    print("\nwrote reports/overnight_auction.csv")
    return 0


if __name__ == "__main__":
    sys.exit(main())
