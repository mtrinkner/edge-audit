#!/usr/bin/env python3
"""Does volume_climax_long work as a MAKER instead of a taker? (trial 30)

    python3 python/intraday_passive.py

THE ARGUMENT

The signal says a high-volume down bar reverts. That is a liquidity-provision
signal: you are being paid to absorb a burst of forced selling. Every simulation
so far bought it by CROSSING the spread, twice, which costs 2x the half-spread
(12.5bps round trip) against a gross edge of 4.94bps. A liquidity provider does
not cross the spread. They post and collect it.

THE RULES, declared before running and not swept

  entry      post a limit bid at the signal bar's BID, close x (1 - half_spread)
  fill       the first later bar in the session whose LOW touches the bid
  exit       aggressively at the session close, paying ONE half-spread
  cost       1x half-spread instead of 2x

THE PENALTY THIS MUST SURVIVE, predicted in advance

A bid only fills when price keeps falling. So fills are ADVERSELY SELECTED: the
subset that fills is the subset where the signal was initially wrong. A passive
simulation that counts only the fills and ignores what it missed is a taker
simulation with the costs deleted, which is cheating. So this reports the fill
rate, the filled returns, and what the UNFILLED signals went on to do.

LIMITATION, stated rather than buried

Hourly bars cannot show queue position or depth at a price. Touching a price is
treated as a fill, which in reality it is not: you can be behind in the queue and
never trade. That makes every passive number here an UPPER BOUND, and the honest
reading of a marginal result is that it is worse than printed.
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


def boot_p(x, reps=2000, seed=config.RANDOM_SEED):
    x = x[np.isfinite(x)]
    if len(x) < 30 or x.std(ddof=1) == 0:
        return float("nan")
    rng = np.random.default_rng(seed)
    cen = x - x.mean()
    b = np.array([cen[rng.integers(0, len(x), len(x))].mean() for _ in range(reps)])
    return float((np.abs(b) >= abs(x.mean())).mean())


def sess_stat(df, col):
    s = df.groupby("session")[col].mean().to_numpy()
    s = s[np.isfinite(s)]
    if len(s) < 3 or s.std(ddof=1) == 0:
        return float(df[col].mean()), np.nan, np.nan, len(s)
    return (float(df[col].mean()),
            float(s.mean() / (s.std(ddof=1) / np.sqrt(len(s)))),
            boot_p(s), len(s))


def main() -> int:
    # Through data_access, which stops at the lockbox seal (added 2026-10-26).
    d = da.parquet("intraday_passive", "intraday_1h")
    d = d[d["bars_in_session"] == SESSION_BARS].copy()
    d = d.sort_values(["symbol", "ts"]).reset_index(drop=True)
    g = d.groupby(["symbol", "session"])
    d["fin"] = g["close"].transform("last")
    # CAUSAL volume baseline: expanding mean of the bars BEFORE this one in
    # the same session. Using the session mean (as this did originally) lets a
    # decision at 11:30 depend on volume at 15:30, and that one line was the
    # largest error in the project. tests/test_no_lookahead_intraday.py now
    # catches it by swapping the future and checking the decision is unchanged.
    d["vm"] = (g["volume"].transform(
        lambda x: x.shift(1).expanding().mean()))
    d["dollar"] = g["volume"].transform("sum") * d["fin"]
    d["half"] = liquidity_half_spread_bps(d["fin"], d["dollar"]) / 1e4
    d["vr"] = d["volume"] / d["vm"].replace(0, np.nan)
    d["down"] = d["close"] < d["open"]
    d["sym_i"] = d.groupby(["symbol", "session"]).ngroup()
    d = d.dropna(subset=["vr", "half", "fin"])

    print(f"  {len(d):,} bars, {d['symbol'].nunique()} symbols, "
          f"{d['session'].nunique()} sessions")

    # Walk each session once, collecting both executions for every signal bar.
    rows = []
    low = d["low"].to_numpy()
    close = d["close"].to_numpy()
    half = d["half"].to_numpy()
    bar = d["bar"].to_numpy()
    fin = d["fin"].to_numpy()
    vr = d["vr"].to_numpy()
    down = d["down"].to_numpy()
    sym = d["symbol"].to_numpy()
    ses = d["session"].to_numpy()
    grp = d["sym_i"].to_numpy()

    starts = np.flatnonzero(np.r_[True, grp[1:] != grp[:-1]])
    ends = np.r_[starts[1:], len(d)]
    for s0, s1 in zip(starts, ends):
        n = s1 - s0
        if n != SESSION_BARS:
            continue
        for k in range(1, n - 1):
            i = s0 + k
            if not (vr[i] > VOL_MULT and down[i]):
                continue
            h = half[i]
            taker = fin[i] / close[i] - 1.0 - 2 * h          # cross both ways
            bid = close[i] * (1.0 - h)
            fwd_low = low[i + 1: s1]
            hit = np.flatnonzero(fwd_low <= bid)
            filled = len(hit) > 0
            # Passive entry pays no spread; the exit still crosses once.
            passive = (fin[i] / bid - 1.0 - h) if filled else np.nan
            rows.append((sym[i], ses[i], int(bar[i]), h, taker, passive, filled))

    T = pd.DataFrame(rows, columns=["symbol", "session", "bar", "half",
                                    "taker", "passive", "filled"])
    T.to_parquet(ROOT / "data" / "intraday_passive.parquet", index=False)
    print(f"  {len(T):,} signal bars\n")

    fr = T["filled"].mean()
    print("=" * 92)
    print("PASSIVE vs AGGRESSIVE EXECUTION  —  volume_climax_long")
    print("=" * 92)
    print(f"  fill rate on a bid posted at the bar's bid: {fr:.1%}  "
          f"({int(T['filled'].sum()):,} of {len(T):,})")
    print()
    print(f"{'execution':<44}{'bars':>8}{'mean':>11}{'t':>8}{'p':>8}")
    print("-" * 92)
    out = []
    for lab, df, col in (
        ("TAKER, all signals (the original test)", T, "taker"),
        ("TAKER, only the bars that would have filled", T[T.filled], "taker"),
        ("TAKER, only the bars that would NOT fill", T[~T.filled], "taker"),
        ("PASSIVE, filled bars (1x half-spread)", T[T.filled], "passive"),
    ):
        if len(df) < 50:
            continue
        m, t, p, ns = sess_stat(df, col)
        print(f"{lab:<44}{len(df):>8,}{m:>+11.4%}{t:>8.2f}{p:>8.3f}")
        out.append({"execution": lab, "bars": len(df), "mean": m, "t": t, "p": p})

    # The adverse-selection penalty, stated as a number.
    tf = T.loc[T.filled, "taker"].mean()
    tn = T.loc[~T.filled, "taker"].mean()
    print(f"\n  ADVERSE SELECTION: bars that fill return {tf:+.4%} as a taker,")
    print(f"  bars that do NOT fill return {tn:+.4%}. Gap {tf - tn:+.4%}.")
    print(f"  A bid only fills when price keeps dropping, so filling selects")
    print(f"  the subset where the signal was initially wrong.")

    pf = T.loc[T.filled, "passive"].mean()
    print(f"\n  WHAT PASSIVE ENTRY IS WORTH: {pf:+.4%} passive vs {tf:+.4%} taker")
    print(f"  on the SAME filled bars, a saving of {pf - tf:+.4%}.")
    print(f"  Median half-spread {T['half'].median() * 1e4:.2f} bps, so the saving")
    print(f"  should be about one of those.")

    pd.DataFrame(out).to_csv(ROOT / "reports" / "intraday_passive.csv", index=False)
    print(f"\nwrote reports/intraday_passive.csv")
    return 0


if __name__ == "__main__":
    sys.exit(main())
