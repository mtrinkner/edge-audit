#!/usr/bin/env python3
"""Turn-of-month, alone and stacked with mean reversion. (trial 33)

    python3 python/calendar_edges.py

THE CLAIM BEING TESTED

SetupAlpha, "60+ Ways Systematic Traders Find Market Edges in 2026": "A
mean-reversion strategy that also incorporates turn-of-month timing can
significantly outperform the same strategy traded any day of the month. Stack
your edges." The article gives no figures, so there is no number to replicate,
only a direction.

DEFINITIONS FIXED IN ADVANCE, NOT TUNED

  TOM window      last 1 trading day of a month plus the first 3 of the next.
                  This is the Lakonishok-Smidt definition, taken from the
                  literature rather than chosen to fit.
  mean reversion  buy the bottom decile of trailing 5-day return,
                  cross-sectionally, hold 5 sessions, equal weight.
  costs           one full spread per round trip, from EDGE measurements on this
                  project's own bars, not from the volume heuristic that got the
                  sign of the stress effect backwards.

TWO FALSIFIABLE PREDICTIONS

  1. TOM-window returns beat non-TOM returns, net of costs.
  2. Stacking beats mean reversion alone by MORE than the standalone TOM effect
     would imply. That is what "significantly outperform" has to mean; if the
     stack only inherits the calendar effect, nothing is being stacked.

A NOTE ON WHY THIS IS CHEAP TO TEST AND THE INTRADAY WORK WAS NOT. Monthly
rebalancing pays the spread 12 times a year instead of 252. The cost-first
screen puts the annual drag at 0.9-2.1% rather than 18.7-43.9%. Calendar effects
are among the few ideas whose holding period makes them affordable at all.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "python"))

import db

HOLD = 5
TOM_BEFORE = 1       # last N trading days of the month
TOM_AFTER = 3        # first N trading days of the next


def measured_cost() -> float:
    """Median round-trip from the EDGE estimates, pooled across the universe."""
    p = ROOT / "data" / "edge_spreads.parquet"
    if not p.exists():
        return 0.0010
    return float(pd.read_parquet(p)["edge_full"].median())


def main() -> int:
    with db.connect() as con:
        d = pd.read_sql(
            "SELECT b.symbol, b.dt, b.adj_close FROM bars b "
            "JOIN symbols s ON s.symbol=b.symbol WHERE s.kind='stock' "
            "ORDER BY b.symbol, b.dt", con)
    d = d.sort_values(["symbol", "dt"]).reset_index(drop=True)
    print(f"  {len(d):,} bars, {d['symbol'].nunique()} symbols")

    cost = measured_cost()
    print(f"  measured round-trip cost: {cost*1e4:.2f}bp (EDGE median)\n")

    # Trading-day index within each month, forward and backward.
    days = pd.DataFrame({"dt": sorted(d["dt"].unique())})
    days["ym"] = days["dt"].str[:7]
    days["fwd"] = days.groupby("ym").cumcount()                 # 0 = first of month
    days["bwd"] = days.groupby("ym").cumcount(ascending=False)  # 0 = last of month
    days["is_tom"] = (days["bwd"] < TOM_BEFORE) | (days["fwd"] < TOM_AFTER)
    d = d.merge(days[["dt", "is_tom", "fwd", "bwd"]], on="dt", how="left")

    g = d.groupby("symbol")["adj_close"]
    d["ret5_past"] = g.pct_change(5)
    d["fwd5"] = g.shift(-HOLD) / d["adj_close"] - 1.0
    d = d.dropna(subset=["ret5_past", "fwd5"])

    # ---- prediction 1: is the TOM window itself better? ----
    print("=" * 78)
    print("PREDICTION 1  does the turn-of-month window beat the rest of the month")
    print("=" * 78)
    rows = []
    for lab, sel in (("turn of month", d["is_tom"]), ("rest of month", ~d["is_tom"])):
        x = d[sel]
        per = x.groupby("dt")["fwd5"].mean()
        s = per.to_numpy()[::HOLD]
        t = s.mean() / (s.std(ddof=1) / np.sqrt(len(s))) if len(s) > 2 else np.nan
        print(f"  {lab:<16}{len(x):>12,} obs  {len(s):>5} indep  "
              f"mean 5d {x['fwd5'].mean():>+8.4%}  t={t:>6.2f}")
        rows.append({"group": lab, "mean": x["fwd5"].mean(), "t": t})
    diff = rows[0]["mean"] - rows[1]["mean"]
    print(f"\n  TOM minus rest: {diff:>+.4%} per 5 sessions, "
          f"before any cost")

    # ---- prediction 2: does stacking add anything? ----
    d["dec"] = d.groupby("dt")["ret5_past"].transform(
        lambda x: pd.qcut(x.rank(method="first"), 10, labels=False,
                          duplicates="drop"))
    mr = d[d["dec"] == 0]        # bottom decile of trailing 5-day return
    print(f"\n{'=' * 78}")
    print("PREDICTION 2  does stacking TOM onto mean reversion add anything")
    print("=" * 78)
    print(f"{'book':<34}{'trades':>11}{'gross':>10}{'net':>10}{'t':>8}")
    print("-" * 78)
    out = []
    for lab, x in (("mean reversion, any day", mr),
                   ("mean reversion, TOM only", mr[mr["is_tom"]]),
                   ("mean reversion, non-TOM only", mr[~mr["is_tom"]])):
        per = x.groupby("dt")["fwd5"].mean()
        s = per.to_numpy()[::HOLD]
        t = s.mean() / (s.std(ddof=1) / np.sqrt(len(s))) if len(s) > 2 else np.nan
        net = x["fwd5"].mean() - cost
        print(f"{lab:<34}{len(x):>11,}{x['fwd5'].mean():>+10.4%}{net:>+10.4%}{t:>8.2f}")
        out.append({"book": lab, "gross": x["fwd5"].mean(), "net": net, "t": t})

    r = pd.DataFrame(out)
    (ROOT / "reports").mkdir(exist_ok=True)
    r.to_csv(ROOT / "reports" / "calendar_edges.csv", index=False)
    stack_gain = out[1]["gross"] - out[0]["gross"]
    print(f"\n  stacking gain (TOM-only minus any-day): {stack_gain:>+.4%}")
    print(f"  standalone TOM effect:                  {diff:>+.4%}")
    print(f"  -> stacking adds {'MORE' if stack_gain > diff else 'NO MORE'} than the "
          f"calendar effect alone")
    print(f"\n  net of the {cost*1e4:.1f}bp measured round trip, the best book is "
          f"{r['net'].max():+.4%} per 5 sessions")
    print(f"  annualised at {252/HOLD:.0f} rebalances: "
          f"{r['net'].max() * 252/HOLD:+.2%}")
    print(f"\nwrote reports/calendar_edges.csv")
    return 0


if __name__ == "__main__":
    sys.exit(main())
