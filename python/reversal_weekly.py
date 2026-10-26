#!/usr/bin/env python3
"""Short-term reversal: buy last week's biggest losers (trial 49).

    python3 python/reversal_weekly.py

Every 5 sessions, take the 500 most liquid stocks (ranked on the previous
month), find the 50 that fell the most over the last 5 sessions, buy them at the
next OPEN, and sell 5 sessions later at the open. Entering at the open rather
than the signal's own close avoids bid-ask bounce, the classic way a reversal
backtest flatters itself. The benchmark is the same 500 stocks held over the
same open-to-open week.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "python"))

import db
import lockbox

UNIV = 500
DECILE = 50
COST = 5e-4


def load(max_date: str):
    with db.connect() as con:
        d = pd.read_sql("SELECT b.symbol, b.dt, b.open, b.close, b.adj_close, b.volume "
                        "FROM bars b JOIN symbols s USING(symbol) WHERE s.kind='stock' "
                        "AND b.dt <= ?", con, params=(max_date,))
    d["aopen"] = d["open"] * d["adj_close"] / d["close"]
    d["dv"] = d["close"] * d["volume"]
    px = d.pivot(index="dt", columns="symbol", values="adj_close").sort_index()
    op = d.pivot(index="dt", columns="symbol", values="aopen").sort_index()
    dv = d.pivot(index="dt", columns="symbol", values="dv").sort_index()
    return px, op, dv


def main() -> int:
    end = lockbox.state()["seal_from"]
    max_date = (pd.Timestamp(end) - pd.Timedelta(days=1)).date().isoformat()
    lockbox.guard(max_date, "reversal_weekly")
    px, op, dv = load(max_date)
    dates = px.index
    month = pd.Index(dates).str[:7]
    med = dv.groupby(month).median()
    prev = med.shift(1)                                  # ranked on the previous month

    rows = []
    for i in range(25, len(dates) - 6, 5):
        dt = dates[i]
        liq = prev.loc[month[i]].dropna()
        uni = liq.nlargest(UNIV).index
        r5 = (px.iloc[i][uni] / px.iloc[i - 5][uni] - 1).dropna()
        fwd = (op.iloc[i + 6][uni] / op.iloc[i + 1][uni] - 1)
        ok = r5.index.intersection(fwd.dropna().index)
        if len(ok) < UNIV * 0.9:
            continue
        r5, fwd = r5[ok], fwd[ok]
        losers, winners = r5.nsmallest(DECILE).index, r5.nlargest(DECILE).index
        rows.append({"dt": dt, "losers": fwd[losers].mean(), "winners": fwd[winners].mean(),
                     "universe": fwd.mean()})
    w = pd.DataFrame(rows).set_index("dt")
    w["losers_net"] = w["losers"] - COST
    w["excess"] = w["losers_net"] - w["universe"]
    n = len(w)
    t = w["excess"].mean() / (w["excess"].std() / np.sqrt(n))
    sh = lambda s: s.mean() / s.std() * np.sqrt(52)
    print(f"trial 49: {n} non-overlapping weeks, {w.index.min()} to {w.index.max()}\n")
    print("PRE-DECLARED TEST")
    print(f"  losers net of 5bp   {w['losers_net'].mean() * 1e4:+.1f}bp/week, Sharpe {sh(w['losers_net']):.2f}")
    print(f"  universe (hold)     {w['universe'].mean() * 1e4:+.1f}bp/week, Sharpe {sh(w['universe']):.2f}")
    print(f"  excess              {w['excess'].mean() * 1e4:+.1f}bp/week, t={t:.2f}   "
          f"{'PASS' if t >= 2 else 'FAIL'}")
    print(f"  Sharpe above hold:  {'PASS' if sh(w['losers_net']) > sh(w['universe']) else 'FAIL'}")
    ok = t >= 2 and sh(w["losers_net"]) > sh(w["universe"])
    print(f"\n  VERDICT: {'PASS' if ok else 'FAIL'}\n")

    gross_ex = w["losers"] - w["universe"]
    se = gross_ex.std() / np.sqrt(n)
    print("descriptive only:")
    print(f"  break-even round trip: {gross_ex.mean() * 1e4:+.1f}bp "
          f"(lower 95% {(gross_ex.mean() - 1.96 * se) * 1e4:+.1f}bp)")
    print(f"  winners minus universe: {(w['winners'] - w['universe']).mean() * 1e4:+.1f}bp/week")
    ls = w["losers"] - w["winners"] - 2 * COST
    print(f"  long losers / short winners, net: {ls.mean() * 1e4:+.1f}bp/week, Sharpe {sh(ls):.2f}, "
          f"t={ls.mean() / (ls.std() / np.sqrt(n)):.2f}")
    print(f"  hit rate (excess > 0): {(w['excess'] > 0).mean():.0%}")
    for y, g in w.groupby(w.index.str[:4]):
        print(f"  {y}: excess {g['excess'].mean() * 1e4:+.1f}bp/week ({len(g)} weeks)")
    try:
        v = pd.read_parquet(ROOT / "data" / "vix.parquet").set_index("session")["vix"]
        j = w.join(v.rename("vix"), how="inner")
        hi = j["vix"] > j["vix"].median()
        print(f"  VIX coverage {len(j)} weeks: high-VIX excess {j.loc[hi, 'excess'].mean() * 1e4:+.1f}bp, "
              f"low-VIX {j.loc[~hi, 'excess'].mean() * 1e4:+.1f}bp")
    except Exception as e:
        print(f"  VIX split unavailable: {type(e).__name__}")
    w.to_csv(ROOT / "reports" / "reversal_weekly.csv")
    print("\nwrote reports/reversal_weekly.csv")
    return 0


if __name__ == "__main__":
    sys.exit(main())
