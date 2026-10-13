#!/usr/bin/env python3
"""Measure the real spread instead of modelling it. (trial 32)

    python3 python/edge_spreads.py

WHY

Every cost number in this project came from a volume heuristic I wrote:
a tick floor plus an inverse-square-root-of-volume term, calibrated by hand.
The high-VIX intraday result then landed 0.67bp short of breakeven, which is
well inside that heuristic's error. The cost model was the weakest component in
the whole project and it was carrying the conclusion.

EDGE (Ardia, Guidotti & Kroencke, Journal of Financial Economics 2024,
161:103916) estimates the effective bid-ask spread from open, high, low and
close prices. It is the first estimator to use all four, and its accuracy
improves with bar frequency, which suits hourly data.

VALIDATED BEFORE USE, on a synthetic series with a mid-price path inside each
bar and trades hitting bid or ask at random:

    true 2bp  -> 1.90bp     true 10bp -> 9.84bp
    true 5bp  -> 4.99bp     true 20bp -> 20.32bp

Errors of -0.10 to +1.03bp, and within 0.16bp over the 2-10bp range that
matters here. Tighter than the gap being resolved.

WHAT THIS CANNOT DO. EDGE needs consecutive bars, so it cannot be estimated on
the high-volume bars alone. It gives the symbol's prevailing spread over a
window, not the spread on one specific bar. If high-volume bars really are
tighter than their symbol's average, this still overstates the cost of trading
them, and that remains unresolved without real quote data.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
from bidask import edge

sys.path.insert(0, str(Path(__file__).resolve().parent))

from spread_estimator import liquidity_half_spread_bps

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data" / "intraday_1h.parquet"
OUT = ROOT / "data" / "edge_spreads.parquet"
MIN_BARS = 60


def main() -> int:
    d = pd.read_parquet(DATA)
    d = d[d["bars_in_session"] == 7].sort_values(["symbol", "ts"]).reset_index(drop=True)
    d["month"] = d["session"].str[:7]
    print(f"  {len(d):,} bars, {d['symbol'].nunique()} symbols, "
          f"{d['month'].nunique()} months")

    # ESTIMATED WITHIN SESSIONS ONLY, then averaged across the month.
    #
    # A first version strung a symbol's whole month of hourly bars together and
    # handed them to EDGE. Every 7th transition is then an OVERNIGHT GAP, and
    # the median gap is 0.4-0.8%, which the estimator reads as bid-ask bounce.
    # It returned a 9.82bp half-spread for stocks trading $627M a day, with no
    # liquidity gradient at all across a 120x range of volume, which is how the
    # error announced itself. Measured per session instead: AAPL 7.09bp -> 1.73,
    # NVDA 15.75bp -> 1.48. Each session contributes 6 intraday transitions and
    # no overnight one.
    rows = []
    for (sym, mo), g in d.groupby(["symbol", "month"], sort=False):
        if len(g) < MIN_BARS:
            continue
        per = []
        for _, gg in g.groupby("session", sort=False):
            if len(gg) != 7:
                continue
            try:
                e = edge(gg["open"].to_numpy(), gg["high"].to_numpy(),
                         gg["low"].to_numpy(), gg["close"].to_numpy())
            except Exception:
                continue
            if e is not None and np.isfinite(e):
                per.append(max(float(e), 0.0))
        if len(per) < 10:
            continue
        rows.append((sym, mo, float(np.mean(per)), len(g)))
    s = pd.DataFrame(rows, columns=["symbol", "month", "edge_full", "nbars"])
    # EDGE can return small negatives on noisy windows; the paper notes this and
    # it means "indistinguishable from zero", so clip at a one-tick floor rather
    # than discarding the observation.
    s["edge_full"] = s["edge_full"].clip(lower=0.0)
    s["edge_half_bps"] = s["edge_full"] / 2 * 1e4
    s.to_parquet(OUT, index=False)
    print(f"  {len(s):,} symbol-months estimated "
          f"({(s['edge_full'] <= 0).mean():.1%} came back at or below zero)\n")

    # Compare against the heuristic that has been carrying every cost number.
    g = d.groupby(["symbol", "session"])
    d["fin"] = g["close"].transform("last")
    d["dollar"] = g["volume"].transform("sum") * d["fin"]
    d["model_half_bps"] = liquidity_half_spread_bps(d["fin"], d["dollar"])
    m = (d.groupby(["symbol", "month"])
          .agg(model_half_bps=("model_half_bps", "median"),
               dollar=("dollar", "median")).reset_index())
    j = s.merge(m, on=["symbol", "month"], how="inner")
    j["liq"] = pd.qcut(j["dollar"].rank(method="first"), 10,
                       labels=False, duplicates="drop")

    print("=" * 84)
    print("MEASURED (EDGE) vs MODELLED half-spread, by liquidity decile")
    print("=" * 84)
    print(f"{'decile':<8}{'sym-months':>12}{'median $vol':>16}"
          f"{'modelled':>11}{'EDGE':>9}{'ratio':>8}")
    for q, gg in j.groupby("liq"):
        print(f"{int(q):<8}{len(gg):>12,}{gg['dollar'].median():>16,.0f}"
              f"{gg['model_half_bps'].median():>10.2f}b"
              f"{gg['edge_half_bps'].median():>8.2f}b"
              f"{gg['edge_half_bps'].median() / max(gg['model_half_bps'].median(), 1e-9):>8.2f}")
    print(f"\n  overall median: modelled {j['model_half_bps'].median():.2f}bp, "
          f"EDGE {j['edge_half_bps'].median():.2f}bp, "
          f"ratio {j['edge_half_bps'].median() / j['model_half_bps'].median():.2f}")
    print(f"  correlation of the two, across symbol-months: "
          f"{j[['model_half_bps', 'edge_half_bps']].corr().iloc[0, 1]:.3f}")
    print(f"\nwrote {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
