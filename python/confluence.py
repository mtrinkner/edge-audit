#!/usr/bin/env python3
"""Confluence: trade only when several day-trading rules agree (trial 40).

    python3 python/confluence.py

WHY THIS AND NOT A PORTFOLIO. Every rule loses after costs, so running them side
by side just adds losses. The cost is fixed per trade, so the only thing that can
beat it is a bigger move per trade. Requiring k rules to fire on the same stock
in the same session makes fewer trades; if the rules carry partly independent
information, they should also be better ones.

ENTRY at the close of the bar where the k-th rule fires that session, known at
that moment. Hold to the session close, the best exit found in trials 38 and 39.
Cost is twice the previous month's EDGE half-spread.

  A  count over all 12 rules, k = 2, 3, 4. No selection.
  B  count over the buy-weakness family, k = 2, 3. Grouped by mechanism, but
     chosen after seeing trials 38-39, so a pass in B alone counts for little.

k = 1 is printed as the reference and is not a new trial.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import t as tdist

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "python"))

from tpsl_heatmap import BARS, load

FAMILY_B = {"vwap_reversion_long", "volume_climax_long", "midday_reversal",
            "opening_range_break_down", "gap_down_fill"}
VARIANTS = [("A all 12", None, 1, False), ("A all 12", None, 2, True),
            ("A all 12", None, 3, True), ("A all 12", None, 4, True),
            ("B buy-weakness", FAMILY_B, 1, False),
            ("B buy-weakness", FAMILY_B, 2, True), ("B buy-weakness", FAMILY_B, 3, True)]


def main() -> int:
    d, cost, trades = load()
    last = d.groupby(["symbol", "session"])["close"].transform("last")
    d["gross"] = last / d["close"] - 1.0
    d["cost"] = [cost.get((s, x[:7]), np.nan) for s, x in zip(d["symbol"], d["session"])]
    d = d.dropna(subset=["cost"])
    d["net"] = d["gross"] - d["cost"]
    # Control: entering at the same bar of the same symbol on every session.
    bench = d.groupby(["symbol", "bar"])["net"].mean().rename("bench")
    px = d.set_index(["symbol", "session", "bar"])[["gross", "net"]]

    rows = []
    for name, fam, k, counted in VARIANTS:
        t = trades if fam is None else trades[trades["rule"].isin(fam)]
        # The bar at which the k-th distinct rule has fired this session.
        t = t.sort_values(["symbol", "session", "bar"])
        kth = t[t.groupby(["symbol", "session"]).cumcount() == k - 1][
            ["symbol", "session", "bar"]]
        x = kth.join(px, on=["symbol", "session", "bar"]).dropna()
        x = x.join(bench, on=["symbol", "bar"])
        x["excess"] = x["net"] - x["bench"]
        ps = x.groupby("session")[["gross", "net", "excess"]].mean()
        n = len(ps)
        tt = ps["net"].mean() / (ps["net"].std(ddof=1) / np.sqrt(n))
        rows.append({"family": name, "k": k, "counted": counted, "trades": len(x),
                     "sessions": n, "trades_per_day": len(x) / n,
                     "gross_tw_bp": x["gross"].mean() * 1e4,
                     "net_tw_bp": x["net"].mean() * 1e4,
                     "gross_sw_bp": ps["gross"].mean() * 1e4,
                     "net_sw_bp": ps["net"].mean() * 1e4,
                     "excess_sw_bp": ps["excess"].mean() * 1e4,
                     "t_net": tt, "p_net": tdist.sf(tt, n - 1),
                     "hit": (x["net"] > 0).mean()})
    r = pd.DataFrame(rows)
    c = r[r["counted"]].sort_values("p_net")
    m = len(c)
    r["bh"] = False
    r.loc[c.index, "bh"] = c["p_net"].to_numpy() <= np.arange(1, m + 1) / m * 0.05
    r["passes"] = r["bh"] & (r["net_tw_bp"] > 0) & (r["net_sw_bp"] > 0)
    r.to_csv(ROOT / "reports" / "confluence.csv", index=False)

    print(f"{'family':<16}{'k':>3}{'trades':>10}{'/day':>7}{'gross/tr':>10}{'net/tr':>9}"
          f"{'gross/day':>11}{'net/day':>9}{'excess':>8}{'t':>7}{'hit':>6}")
    for _, x in r.iterrows():
        tag = "" if x.counted else "  (reference)"
        print(f"{x.family:<16}{x.k:>3}{x.trades:>10,}{x.trades_per_day:>7.0f}"
              f"{x.gross_tw_bp:>9.1f}b{x.net_tw_bp:>8.1f}b{x.gross_sw_bp:>10.1f}b"
              f"{x.net_sw_bp:>8.1f}b{x.excess_sw_bp:>7.1f}b{x.t_net:>7.2f}{x.hit:>6.0%}{tag}")
    print(f"\nvariants meeting the declared pass bar: {int(r['passes'].sum())} of {m}")
    print("wrote reports/confluence.csv")
    return 0


if __name__ == "__main__":
    sys.exit(main())
