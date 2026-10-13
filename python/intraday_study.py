#!/usr/bin/env python3
"""Do classic day-trading setups beat the clock? The declared test.

    python3 python/intraday_study.py

Registered as trial 28 with 12 variants BEFORE any return was computed.

THE BENCHMARK IS THE WHOLE EXPERIMENT

A day-trading rule says "enter here, exit at the close". The naive way to score
it is against holding from the same entry to the same close, which is the same
thing, so the excess is zero by construction. The next-naive way is against a
full-session hold, which confounds the signal with a time-of-day effect: buying
at 10:30 and selling at 16:00 has an average return of its own, and a rule that
only ever fires at 10:30 would inherit it and look skilful.

So every rule is scored against entering at the SAME BAR of the session and
exiting at the SAME close, but on EVERY session rather than only the ones where
the signal fired. That is the unconditional time-of-day return, and the
difference is the only thing the signal can claim to have added. It is the same
control that killed the contract-award result, where random dates beat the real
ones.

COSTS. Charged at each stock's own modelled half-spread, on both sides, which
matters far more here than in the daily study. A day trade pays the spread every
single session. The daily signals held for a median of eleven days and paid it
once per eleven. This is the arithmetic that makes intraday trading hard and it
is applied to the strategy and to the benchmark alike, so the comparison is not
rigged either way.

SESSIONS MUST BE COMPLETE. Only sessions with all 7 hourly bars are used. Half
days and gap-damaged sessions are dropped, and the count of what was dropped is
printed rather than quietly absorbed.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

import config
import intraday_signals as isg
from spread_estimator import liquidity_half_spread_bps

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data" / "intraday_1h.parquet"
SESSION_BARS = 7


def load() -> pd.DataFrame:
    d = pd.read_parquet(DATA)
    n0, s0 = len(d), d["session"].nunique()
    d = d[d["bars_in_session"] == SESSION_BARS].copy()
    print(f"  {n0:,} bars -> {len(d):,} after keeping only complete "
          f"{SESSION_BARS}-bar sessions")
    print(f"  {d['symbol'].nunique()} symbols, {d['session'].nunique()} sessions "
          f"(dropped {s0 - d['session'].nunique()} incomplete)")
    return d.sort_values(["symbol", "ts"]).reset_index(drop=True)


def run(d: pd.DataFrame) -> pd.DataFrame:
    # Half-spread per symbol-session from that session's dollar volume.
    sess = d.groupby(["symbol", "session"]).agg(
        dv=("volume", "sum"), px=("close", "last")).reset_index()
    sess["adv"] = sess["dv"] * sess["px"]
    sess["half"] = liquidity_half_spread_bps(sess["px"], sess["adv"]) / 10_000.0
    half = dict(zip(zip(sess["symbol"], sess["session"]), sess["half"]))

    rows, base = [], []
    for sym, g in d.groupby("symbol", sort=False):
        sessions = list(g.groupby("session", sort=True))
        prev_close = np.nan
        for sname, s in sessions:
            s = s.reset_index(drop=True)
            closes = s["close"].to_numpy()
            final = closes[-1]
            c = half.get((sym, sname), np.nan)
            if not np.isfinite(c):
                prev_close = final
                continue
            # The realized return from each bar to this session's close. This is
            # NOT the benchmark. A first version used this session's own value as
            # the benchmark, which made net and benchmark identical and produced
            # an excess of exactly 0.000% for all twelve rules. The benchmark has
            # to be the mean across ALL sessions at the same bar, computed below.
            to_close = final / closes - 1.0 - 2 * c
            for name, fn in isg.RULES.items():
                m = (fn(s, prev_close) if name in isg.NEEDS_PREV_CLOSE else fn(s))
                idx = np.flatnonzero(m)
                if len(idx) == 0:
                    continue
                i = int(idx[0])
                rows.append((name, sym, sname, i, float(to_close[i])))
            # Every bar of every session feeds the unconditional benchmark,
            # whether or not any rule fired on it.
            for i in range(len(s)):
                base.append((sym, i, float(to_close[i])))
            prev_close = final
    T = pd.DataFrame(rows, columns=["rule", "symbol", "session", "bar", "net"])
    B = (pd.DataFrame(base, columns=["symbol", "bar", "r"])
           .groupby(["symbol", "bar"], as_index=False)["r"].mean()
           .rename(columns={"r": "uncond"}))
    # The control: what entering at THIS bar, in THIS symbol, and holding to the
    # close earns on an average session. Whatever the rule beats that by is the
    # only thing the signal can claim to have added.
    return T.merge(B, on=["symbol", "bar"], how="left")


def summarize(t: pd.DataFrame, reps: int = 2000) -> dict:
    t = t.copy()
    t["excess"] = t["net"] - t["uncond"]
    # One observation per SESSION: thousands of symbols firing on the same day
    # are one market, not thousands of independent bets.
    per_sess = t.groupby("session")["excess"].mean().sort_index()
    d = per_sess.dropna().to_numpy()
    n = len(d)
    res = {
        "rule": t["rule"].iloc[0], "trades": len(t),
        "sessions": n, "median_bar": int(t["bar"].median()),
        "net": float(t["net"].mean()), "uncond": float(t["uncond"].mean()),
        "excess": float(t["excess"].mean()),
        "hit_rate": float((t["net"] > 0).mean()),
        "t": None, "p": None,
    }
    if n >= 30 and d.std(ddof=1) > 0:
        res["t"] = float(d.mean() / (d.std(ddof=1) / np.sqrt(n)))
        rng = np.random.default_rng(config.RANDOM_SEED)
        cen = d - d.mean()
        boot = np.array([cen[rng.integers(0, n, n)].mean() for _ in range(reps)])
        res["p"] = float((np.abs(boot) >= abs(d.mean())).mean())
    return res


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.parse_args()
    if not DATA.exists():
        print(f"missing {DATA}; run python/ingest_intraday.py first")
        return 1

    print(f"{len(isg.RULES)} declared day-trading rules, same-session exit\n")
    d = load()
    print("\n  simulating...")
    T = run(d)
    if T.empty:
        print("no trades")
        return 1
    T.to_parquet(ROOT / "data" / "intraday_trades.parquet", index=False)
    print(f"  {len(T):,} intraday trades "
          f"(saved to data/intraday_trades.parquet)\n")

    rows = [summarize(g) for _, g in T.groupby("rule", sort=False)]
    res = pd.DataFrame(rows).sort_values("excess", ascending=False)

    k = res.dropna(subset=["p"]).sort_values("p").copy()
    m = len(k)
    k["bh_crit"] = (np.arange(1, m + 1) / m) * 0.05
    k["passes_bh"] = k["p"] <= k["bh_crit"]
    res = res.merge(k[["rule", "passes_bh"]], on="rule", how="left")
    res["tested"] = res["p"].notna()
    res["passes_bh"] = res["passes_bh"].fillna(False).astype(bool)
    (ROOT / "reports").mkdir(exist_ok=True)
    res.to_csv(ROOT / "reports" / "intraday.csv", index=False)

    f = lambda v, s="{:.2f}": s.format(v) if pd.notna(v) else "     -"
    print("=" * 100)
    print("DAY-TRADING RULES  —  excess over entering at the SAME BAR on EVERY "
          "session (net of costs)")
    print("=" * 100)
    print(f"{'rule':<26}{'trades':>9}{'sess':>6}{'bar':>5}{'hit%':>6}"
          f"{'net':>9}{'all sessions':>14}{'excess':>9}{'t':>7}{'p':>7}{'BH':>5}")
    for _, r in res.iterrows():
        print(f"{r['rule']:<26}{int(r['trades']):>9}{int(r['sessions']):>6}"
              f"{int(r['median_bar']):>5}{f(r['hit_rate'],'{:.0%}'):>6}"
              f"{f(r['net'],'{:+.3%}'):>9}{f(r['uncond'],'{:+.3%}'):>14}"
              f"{f(r['excess'],'{:+.3%}'):>9}{f(r['t']):>7}{f(r['p'],'{:.3f}'):>7}"
              f"{('YES' if r['passes_bh'] else ('no' if r['tested'] else '  -')):>5}")

    print(f"\nsurvived Benjamini-Hochberg at FDR 5%: "
          f"{int(res['passes_bh'].sum())} of {m}")
    print(f"rules with positive excess: {int((res['excess'] > 0).sum())} "
          f"of {len(res)}")
    print(f"mean excess across all rules: {res['excess'].mean():+.4%}")
    print(f"\nwrote reports/intraday.csv")
    return 0


if __name__ == "__main__":
    sys.exit(main())
