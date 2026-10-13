#!/usr/bin/env python3
"""Reject strategy ideas on cost BEFORE backtesting them.

    python3 python/cost_first_screen.py
    python3 python/cost_first_screen.py --annual-target 0.15

WHY THIS EXISTS

This project ran 427 registered trials. Almost every candidate died the same
way: the signal was real and smaller than the cost of trading it. The last one
was the clearest case. A high-volume-down-bar reversal paid 10.77bp gross and
cost 16.50bp to collect, and I only learned that after building five modules.

The order was backwards. The cost of trading a given holding period in a given
liquidity bucket is knowable in ADVANCE and does not depend on the signal at
all. So it should be the first filter, not the last.

SetupAlpha's "60+ Ways Systematic Traders Find Market Edges" makes exactly this
point in one line, and it is the best line in the article: "If a paper only works
with zero slippage and zero commissions it's not a real edge." This file
operationalises it.

WHAT IT COMPUTES

For each holding period and liquidity bucket: the gross per-trade return a
strategy MUST produce just to break even, and the annualised return it would
generate if it cleared that hurdle by a given margin. Spreads are MEASURED with
EDGE (Ardia, Guidotti & Kroencke, JFE 2024) from this project's own hourly bars,
not modelled from volume. The volume-based model this project used for most of
its life got the sign of the stress effect backwards: it had costs falling in
high-VIX periods because volume rises, when measured spreads widen.

HOW TO READ IT. Find your intended holding period and the liquidity you can
actually trade. The number is the hurdle. Then ask whether any effect in the
literature is that large at that horizon. Most published anomalies are 2-20bp
per rebalance, so a daily strategy in anything but the most liquid names is
dead before it is written.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "python"))

SPREADS = ROOT / "data" / "edge_spreads.parquet"
BARS = ROOT / "data" / "intraday_1h.parquet"
SESSIONS_PER_YEAR = 252


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--annual-target", type=float, default=0.10,
                    help="annual net return you want, for the second table")
    args = ap.parse_args()

    if not SPREADS.exists():
        print(f"missing {SPREADS}; run python/edge_spreads.py first")
        return 1
    s = pd.read_parquet(SPREADS)
    b = pd.read_parquet(BARS)
    b = b[b["bars_in_session"] == 7]
    b["month"] = b["session"].str[:7]
    g = b.groupby(["symbol", "session"])
    dollar = (g["volume"].sum() * g["close"].last()).reset_index(name="dollar")
    dollar["month"] = dollar["session"].str[:7]
    md = dollar.groupby(["symbol", "month"], as_index=False)["dollar"].median()
    j = s.merge(md, on=["symbol", "month"], how="inner")
    j["liq"] = pd.qcut(j["dollar"].rank(method="first"), 5, labels=False,
                       duplicates="drop")
    LAB = {0: "micro", 1: "small", 2: "mid", 3: "large", 4: "mega"}

    # Round trip = one full spread (half in, half out). Measured, not modelled.
    buckets = []
    for q, gg in j.groupby("liq"):
        buckets.append({"bucket": LAB[int(q)],
                        "median_dollar": gg["dollar"].median(),
                        "round_trip": gg["edge_full"].median()})
    bk = pd.DataFrame(buckets)

    HOLDS = [(1, "1 session (day trade)"), (2, "2 sessions"),
             (5, "1 week"), (21, "1 month"), (63, "1 quarter"),
             (252, "1 year")]

    print("=" * 94)
    print("COST-FIRST SCREEN  —  gross per-trade return needed just to BREAK EVEN")
    print("=" * 94)
    print(f"{'holding period':<24}" + "".join(f"{LAB[i]:>13}" for i in range(5)))
    print(f"{'median $ volume':<24}"
          + "".join(f"{bk.iloc[i]['median_dollar']/1e6:>12.0f}M" for i in range(5)))
    print(f"{'measured round trip':<24}"
          + "".join(f"{bk.iloc[i]['round_trip']*1e4:>12.1f}b" for i in range(5)))
    print("-" * 94)
    for h, lab in HOLDS:
        row = f"{lab:<24}"
        for i in range(5):
            row += f"{bk.iloc[i]['round_trip']*1e4:>12.1f}b"
        print(row)
        break   # the hurdle per trade does not depend on holding period
    print("  (the per-trade hurdle is the same at any horizon: you pay the")
    print("   spread once per round trip, whenever you choose to close)")

    print(f"\n{'=' * 94}")
    print(f"WHAT THAT COSTS YOU PER YEAR, at {SESSIONS_PER_YEAR/1:.0f} sessions")
    print("=" * 94)
    print(f"{'holding period':<24}{'trades/yr':>11}"
          + "".join(f"{LAB[i]:>13}" for i in range(5)))
    print("-" * 94)
    rows = []
    for h, lab in HOLDS:
        n = SESSIONS_PER_YEAR / h
        row = f"{lab:<24}{n:>11.0f}"
        for i in range(5):
            drag = bk.iloc[i]["round_trip"] * n
            row += f"{drag:>12.1%} "
            rows.append({"hold": lab, "bucket": LAB[i], "trades_yr": n,
                         "annual_drag": drag})
        print(row)
    pd.DataFrame(rows).to_csv(ROOT / "reports" / "cost_first_screen.csv", index=False)

    print(f"\n{'=' * 94}")
    print(f"THE VERDICT: can a {args.annual_target:.0%}/yr NET strategy exist here?")
    print("=" * 94)
    print(f"{'holding period':<24}{'trades/yr':>11}"
          + "".join(f"{LAB[i]:>13}" for i in range(5)))
    print("-" * 94)
    for h, lab in HOLDS:
        n = SESSIONS_PER_YEAR / h
        row = f"{lab:<24}{n:>11.0f}"
        for i in range(5):
            need = (bk.iloc[i]["round_trip"] * n + args.annual_target) / n
            row += f"{need*1e4:>12.1f}b"
        print(row)
    print("\n  Each cell is the gross per-trade return required. Compare it to the")
    print("  effect sizes that actually exist: most published anomalies run")
    print("  2-20bp per rebalance. Anything asking for 50bp+ per trade is not a")
    print("  strategy, it is a wish.")
    print(f"\nwrote reports/cost_first_screen.csv")
    return 0


if __name__ == "__main__":
    sys.exit(main())
