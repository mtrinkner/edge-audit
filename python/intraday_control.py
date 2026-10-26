#!/usr/bin/env python3
"""Matched control for volume_climax_long: is it the signal or the volume state?

    python3 python/intraday_control.py

THE PROBLEM THIS SOLVES

volume_climax_long fires on a bar with more than 2x the session's average volume
AND a down close. It beat its benchmark by +0.065% with t=7.05, which survived
Benjamini-Hochberg. But the benchmark was the mean return at the same hour across
ALL sessions, and a 2x-volume down bar is not an average bar. It is a
high-volatility moment. Comparing a high-volatility subsample against an
all-sessions average is not like-for-like, and the apparent edge could be a
volatility premium rather than anything the signal knows.

This is the same confound shape that killed the federal contract result, where
random dates outperformed the real ones once a placebo was run.

THREE COMPARISONS, declared before running

  TREATMENT   volume > 2x session average AND close < open      (the rule)
  CONTROL A   volume > 2x session average AND close >= open     (same volume
              state, opposite direction). This is the sharp test: if treatment
              and control A agree, the down-close condition contributes nothing
              and the result is the high-volume state alone.
  CONTROL B   every bar in the same volume-ratio decile as the treated bars,
              in the same bar-of-session slot, regardless of direction. This
              controls for time of day and volume state together.

Costs and the hold-to-close rule are identical across all three, so nothing is
rigged by construction. Significance uses session-level means, because thousands
of symbols firing on one day are one market, not thousands of independent bets.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

import data_access as da
import config
from spread_estimator import liquidity_half_spread_bps

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data" / "intraday_1h.parquet"
SESSION_BARS = 7
VOL_MULT = 2.0


def session_stats(d: pd.DataFrame) -> pd.DataFrame:
    g = d.groupby(["symbol", "session"])
    d["sess_final"] = g["close"].transform("last")
    # CAUSAL: expanding mean of the bars BEFORE this one. The session mean,
    # used originally, let a decision at 11:30 depend on volume at 15:30.
    d["sess_volmean"] = g["volume"].transform(
        lambda x: x.shift(1).expanding().mean())
    d["sess_dollar"] = g["volume"].transform("sum") * d["sess_final"]
    return d


def boot_p(x: np.ndarray, reps: int = 2000, seed: int = config.RANDOM_SEED) -> float:
    n = len(x)
    if n < 30 or x.std(ddof=1) == 0:
        return float("nan")
    rng = np.random.default_rng(seed)
    cen = x - x.mean()
    boot = np.array([cen[rng.integers(0, n, n)].mean() for _ in range(reps)])
    return float((np.abs(boot) >= abs(x.mean())).mean())


def report(name: str, t: pd.DataFrame) -> dict:
    per = t.groupby("session")["ret"].mean().to_numpy()
    per = per[np.isfinite(per)]
    tstat = (per.mean() / (per.std(ddof=1) / np.sqrt(len(per)))
             if len(per) > 2 and per.std(ddof=1) > 0 else np.nan)
    return {"group": name, "bars": len(t), "sessions": len(per),
            "mean_ret": float(t["ret"].mean()),
            "session_mean": float(per.mean()), "t": float(tstat),
            "p": boot_p(per)}


def main() -> int:
    # Through data_access, which stops at the lockbox seal (added 2026-10-26).
    d = da.parquet("intraday_control", "intraday_1h")
    d = d[d["bars_in_session"] == SESSION_BARS].copy()
    d = d.sort_values(["symbol", "ts"]).reset_index(drop=True)
    d = session_stats(d)

    half = liquidity_half_spread_bps(d["sess_final"], d["sess_dollar"]) / 10_000.0
    d["ret"] = d["sess_final"] / d["close"] - 1.0 - 2 * half
    d["vol_ratio"] = d["volume"] / d["sess_volmean"].replace(0, np.nan)
    d["is_down"] = d["close"] < d["open"]

    # The rule only ever fires on bars 1..5: bar 0 has no prior bar to compare
    # against and the last bar cannot be held to the close.
    d = d[(d["bar"] >= 1) & (d["bar"] <= SESSION_BARS - 2)]
    d = d.dropna(subset=["ret", "vol_ratio"])
    print(f"{len(d):,} eligible bars, {d['symbol'].nunique()} symbols, "
          f"{d['session'].nunique()} sessions\n")

    # The volume decile has to exist BEFORE the groups are sliced, or the
    # treated frame is a view without it.
    d["vdec"] = pd.qcut(d["vol_ratio"].rank(method="first"), 10,
                        labels=False, duplicates="drop")
    hv = d["vol_ratio"] > VOL_MULT
    treat = d[hv & d["is_down"]]
    ctrlA = d[hv & ~d["is_down"]]

    # Control B: same bar slot and same volume-ratio decile as the treated bars.
    # Done as a merge; a Python membership test over 5.2M rows is not viable.
    keys = treat[["bar", "vdec"]].drop_duplicates()
    ctrlB = d.merge(keys, on=["bar", "vdec"], how="inner")

    rows = [report("TREATMENT  >2x vol AND down close", treat),
            report("CONTROL A  >2x vol, NOT down close", ctrlA),
            report("CONTROL B  same bar + volume decile", ctrlB),
            report("ALL eligible bars (the old benchmark)", d)]
    r = pd.DataFrame(rows)
    r.to_csv(ROOT / "reports" / "intraday_control.csv", index=False)

    print("=" * 94)
    print("MATCHED CONTROL  —  volume_climax_long")
    print("=" * 94)
    print(f"{'group':<40}{'bars':>11}{'sess':>6}{'mean ret':>11}"
          f"{'sess mean':>12}{'t':>8}{'p':>8}")
    for _, x in r.iterrows():
        print(f"{x['group']:<40}{int(x['bars']):>11,}{int(x['sessions']):>6}"
              f"{x['mean_ret']:>+11.4%}{x['session_mean']:>+12.4%}"
              f"{x['t']:>8.2f}{x['p']:>8.3f}")

    t_m = treat["ret"].mean()
    print(f"\n{'=' * 94}\nWHAT THE SIGNAL ADDS OVER EACH CONTROL\n{'=' * 94}")
    for nm, c in (("vs CONTROL A (same volume, opposite direction)", ctrlA),
                  ("vs CONTROL B (same bar + volume decile)", ctrlB),
                  ("vs ALL bars (the original, unmatched benchmark)", d)):
        diff = t_m - c["ret"].mean()
        # Session-level difference in means, which is what carries a t-stat.
        a = treat.groupby("session")["ret"].mean()
        b = c.groupby("session")["ret"].mean()
        j = pd.concat([a.rename("t"), b.rename("c")], axis=1).dropna()
        dd = (j["t"] - j["c"]).to_numpy()
        ts = (dd.mean() / (dd.std(ddof=1) / np.sqrt(len(dd)))
              if len(dd) > 2 and dd.std(ddof=1) > 0 else np.nan)
        print(f"  {nm:<50}{diff:>+9.4%}  t={ts:>6.2f}  p={boot_p(dd):.3f}")

    print(f"\nwrote reports/intraday_control.csv")
    return 0


if __name__ == "__main__":
    sys.exit(main())
