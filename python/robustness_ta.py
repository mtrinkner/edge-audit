#!/usr/bin/env python3
"""Does the 41-signal headline survive the data-quality sweep? (2026-10-14)

    python3 python/robustness_ta.py

Trial 50 was distorted by one vendor bankruptcy re-stitch (CHRD, +25,700% in a
day). A sweep of the daily database (reports/data_quality_flags.csv) found 66
severe rows in 42 stocks: impossible one-day jumps and opens more than 50% from
the close. One such row can dominate an equal-weighted average, in EITHER
direction. In ta_exits a trade that hits its target on a fake jump exits at the
target while the hold benchmark books the whole jump, which makes a signal look
worse than it is.

This re-runs trial 27 exactly (ta_exits engine, same data window) three ways:
  as published    every trade
  clean           trades in the 42 flagged stocks removed
  trimmed         per-trade excess clipped at its 0.5% / 99.5% percentiles
and reports whether any signal's verdict changes. It is a robustness check of
a recorded result, not a new strategy, so it is not a registry trial.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "python"))

import lockbox
import signals as sg
from evaluation import clustered_mean
from ta_exits import load_bars, simulate_symbol


def main() -> int:
    st = lockbox.state()
    max_date = (pd.Timestamp(st["seal_from"]) - pd.Timedelta(days=1)).date().isoformat()
    d = load_bars(max_date)
    names = list(sg.SIGNALS)
    parts = []
    for _, g in d.groupby("symbol", sort=False):
        parts += simulate_symbol(g, names)
    t = pd.concat(parts, ignore_index=True)[["signal", "symbol", "dt", "excess"]]
    t.to_parquet(ROOT / "data" / "ta_exits_trades_robust.parquet", index=False)
    return report(t)


def report(t: pd.DataFrame) -> int:
    fl = pd.read_csv(ROOT / "reports" / "data_quality_flags.csv")
    # the sweep's severe criteria: impossible one-day moves, or an open more
    # than 50% away from the close
    severe = fl[(fl["r1"] > 3) | (fl["r1"] < -0.9) | ((fl["open"] / fl["close"] - 1).abs() > 0.5)]
    bad = set(severe["symbol"])
    lo, hi = t["excess"].quantile([0.005, 0.995])
    rows = []
    for sig, x in t.groupby("signal"):
        a = clustered_mean(x["excess"], x["dt"])
        c = x[~x["symbol"].isin(bad)]
        b = clustered_mean(c["excess"], c["dt"])
        w = clustered_mean(x["excess"].clip(lo, hi), x["dt"])
        top5 = x["excess"].abs().nlargest(5).sum() / max(x["excess"].abs().sum(), 1e-12)
        rows.append({"signal": sig, "trades": len(x), "excess": a["mean"], "t": a["t"],
                     "clean": b["mean"], "t_clean": b["t"],
                     "trimmed": w["mean"], "t_trim": w["t"], "top5_share": top5})
    r = pd.DataFrame(rows).sort_values("excess", ascending=False)
    r.to_csv(ROOT / "reports" / "robustness_ta.csv", index=False)
    print(f"{len(t):,} trades, {t['signal'].nunique()} signals; {len(bad)} flagged stocks removed in 'clean'\n")
    print(f"{'signal':<28}{'trades':>9}{'as pub':>9}{'clean':>9}{'trimmed':>9}{'t trim':>8}{'top5':>7}")
    for _, x in r.iterrows():
        print(f"{x.signal:<28}{x.trades:>9,}{x.excess:>+9.2%}{x.clean:>+9.2%}{x.trimmed:>+9.2%}"
              f"{x.t_trim:>8.2f}{x.top5_share:>7.1%}")
    for col in ("excess", "clean", "trimmed"):
        print(f"\nsignals beating hold, {col:<8}: {(r[col] > 0).sum()} of {len(r)}; "
              f"mean {r[col].mean():+.3%}", end="")
    sig_pos = ((r["trimmed"] > 0) & (r["t_trim"] >= 2)).sum()
    print(f"\nsignals significantly positive after trimming (t >= 2): {sig_pos}")
    flips = r[np.sign(r["excess"]) != np.sign(r["trimmed"])]
    print(f"signals whose sign flips between as-published and trimmed: {len(flips)} "
          f"{list(flips['signal'])}")
    return 0


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "report":
        sys.exit(report(pd.read_parquet(ROOT / "data" / "ta_exits_trades_robust.parquet")))
    sys.exit(main())
