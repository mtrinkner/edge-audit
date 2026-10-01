#!/usr/bin/env python3
"""Phase 5b — robustness: how much does the result depend on arbitrary choices?

    python3 python/sensitivity.py

WHY THIS FILE EXISTS. Rebuilding the project after re-downloading the same date
range changed net P/L from $1,596 to $3,191. The data had barely moved: adjusted
closes differed by at most 1.06e-6 in relative terms, vendor rounding from a
recomputed adjustment factor, with raw closes and volumes bit-identical.

A one-part-in-a-million input change should not double an outcome. When it does,
the outcome is not measuring a signal, it is measuring which names happened to
land on the correct side of a threshold. With at most three concurrent positions
chosen by a probability cutoff, a microscopic shift in predicted probability
reorders the queue, a different trade gets taken, and the path diverges.

So this sweep varies the choices that were picked by judgment rather than derived
from anything, and reports the spread. A strategy whose result is stable across
these is worth discussing. One whose result swings with them is a story about the
particular parameters, not about the market.

The sweep also supplies the honest count of strategy variants tested, which is the
input the multiple-comparison correction in R/validate.R needs.
"""

from __future__ import annotations

import itertools
import sys

import numpy as np
import pandas as pd

import backtest as bt
import config

THRESHOLDS = [0.50, 0.52, 0.55, 0.58, 0.60]
TOP_N = [1, 3, 5]
STOP_ATR = [1.0, 1.5, 2.0]
HORIZON = [config.LABEL_HORIZON_DAYS]


def main() -> int:
    combos = list(itertools.product(THRESHOLDS, TOP_N, STOP_ATR, HORIZON))
    print(f"running {len(combos)} parameter combinations on the gbm model\n")
    rows = []
    for i, (th, tn, sa, hz) in enumerate(combos, 1):
        try:
            out = bt.run("gbm", th, tn, hz, sa)
        except SystemExit as e:
            print(e, file=sys.stderr)
            return 1
        tdf, cdf = out["trades"], out["curve"]
        if tdf.empty:
            rows.append({"threshold": th, "top_n": tn, "stop_atr": sa,
                         "trades": 0, "return_pct": 0.0, "sharpe": None,
                         "max_dd_pct": 0.0, "profit_factor": None,
                         "gross": 0.0, "costs": 0.0, "net": 0.0})
            continue
        m = bt.metrics(tdf, cdf)
        rows.append({
            "threshold": th, "top_n": tn, "stop_atr": sa,
            "trades": m["trades"], "return_pct": m["return_pct"],
            "sharpe": m["sharpe_daily_annual"], "max_dd_pct": m["max_drawdown_pct"],
            "profit_factor": m["profit_factor"],
            "gross": float(tdf.gross_pnl.sum()), "costs": float(tdf.costs.sum()),
            "net": float(tdf.net_pnl.sum()),
        })
        print(f"  [{i:>2}/{len(combos)}] thr {th:.2f} top {tn} stop {sa:.1f}ATR  "
              f"{m['trades']:>5} trades  return {m['return_pct']:>7.2f}%  "
              f"sharpe {m['sharpe_daily_annual'] or 0:>5.2f}")

    df = pd.DataFrame(rows)
    df.to_csv(config.ROOT / "reports" / "sensitivity.csv", index=False)

    ret = df["return_pct"]
    sh = df["sharpe"].dropna()
    print("\n" + "=" * 74)
    print("ROBUSTNESS ACROSS ARBITRARY PARAMETER CHOICES")
    print("=" * 74)
    print(f"{'variants tested':<28}{len(df):>12}")
    print(f"{'best total return':<28}{ret.max():>11.2f}%")
    print(f"{'worst total return':<28}{ret.min():>11.2f}%")
    print(f"{'median total return':<28}{ret.median():>11.2f}%")
    print(f"{'spread (best - worst)':<28}{ret.max() - ret.min():>11.2f} pts")
    print(f"{'variants losing money':<28}{int((ret < 0).sum()):>8} of {len(df)}")
    print(f"{'variants beating SPY (204%)':<28}{int((ret > 204.4).sum()):>8} of {len(df)}")
    if len(sh):
        print(f"{'sharpe range':<28}{sh.min():>11.2f} to {sh.max():.2f}")
    print(f"{'median costs / gross':<28}"
          f"{(df['costs'] / df['gross'].replace(0, np.nan)).median():>11.1%}")

    best = df.loc[ret.idxmax()]
    print(f"\nThe best variant (threshold {best.threshold}, top {int(best.top_n)}, "
          f"stop {best.stop_atr} ATR) returned {best.return_pct:.1f}%.")
    print(f"That is the number a less careful write-up would report as 'the result'.")
    print(f"It is the maximum of {len(df)} variants, and the multiple-comparison")
    print(f"correction in R/validate.R exists precisely to discount it.")

    print("\nSENSITIVITY BY PARAMETER (median total return)")
    for col in ("threshold", "top_n", "stop_atr"):
        g = df.groupby(col)["return_pct"].median()
        print(f"  {col:<12} " + "  ".join(f"{k}={v:>7.1f}%" for k, v in g.items()))

    print("\nwrote reports/sensitivity.csv")
    return 0


if __name__ == "__main__":
    sys.exit(main())
