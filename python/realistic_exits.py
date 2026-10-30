#!/usr/bin/env python3
"""Realistic exits for the 12 day-trading rules (trial 39).

    python3 python/realistic_exits.py

WHY. Trial 38 tried fixed-distance stops and targets, and the best cells were
the widest ones, which in practice meant selling at the close 97% of the time.
The fair objection: that is not how a trader exits. A trader sells into
resistance, takes a mean-reversion trade off at VWAP, moves the stop to
breakeven once the trade is working, or trails it. Those four are tested here.

EVERY EXIT HAS A PROTECTIVE STOP of 1R below entry, where R is the symbol's mean
hourly range over the 20 sessions BEFORE this one.

  resistance  target = nearest level above entry among the session high through
              the signal bar and the prior session's high
  vwap        target = session VWAP through the signal bar, if above entry
  breakeven   no target; once a bar's high reaches entry + 1R, the stop moves to
              entry, effective from the NEXT bar
  trailing    no target; stop = highest high since entry minus 1R, re-set after
              each bar closes

Every level is known at the moment it is used. Within one bar the stop is
checked before the target, because an hourly bar cannot say which came first.
A bar that opens through a level fills at the open, not at the level. Anything
still open at the last bar is sold at the close: this is day trading, nothing is
held overnight.

THE PASS BAR, corrected after trial 38. That trial scored the signal against a
benchmark, which measures information, and two rules "passed" while losing money
in every cell. Here the trade itself must make money: mean NET > 0 trade-weighted
and session-weighted, with the session-level t-test surviving BH across all 48.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "python"))

from tpsl_heatmap import BARS, RANGE_LOOKBACK, load

EXITS = ["resistance", "vwap", "breakeven", "trailing"]
KINDS = ["stop", "target", "close"]


def simulate(O, H, L, C, V, R, prev_high):
    """Gross return and exit kind for every (session, entry bar, exit style)."""
    S = len(C)
    tp = (H + L + C) / 3
    vwap = np.cumsum(tp * V, axis=1) / np.maximum(np.cumsum(V, axis=1), 1e-12)
    run_high = np.maximum.accumulate(H, axis=1)
    gross = np.full((S, BARS, len(EXITS)), np.nan)
    kind = np.full((S, BARS, len(EXITS)), 2, dtype=np.int8)
    # A signal on bar i fills at bar i+1's open (fixed 2026-10-26; entry was bar
    # i's own close). Levels computed "through the signal bar" still use bar i,
    # which was known when the order was placed. A last-bar signal stays NaN.
    for i in range(BARS - 1):
        e = O[:, i + 1]
        for x, name in enumerate(EXITS):
            stop = e * (1 - R)
            if name == "resistance":
                lv = np.where(run_high[:, i] > e, run_high[:, i], np.inf)
                lv = np.minimum(lv, np.where(prev_high > e, prev_high, np.inf))
                tgt = lv
            elif name == "vwap":
                tgt = np.where(vwap[:, i] > e, vwap[:, i], np.inf)
            else:
                tgt = np.full(S, np.inf)
            px = C[:, BARS - 1].copy()
            k = np.full(S, 2, dtype=np.int8)
            done = np.zeros(S, bool)
            hi_since = e.copy()
            for j in range(i + 1, BARS):
                live = ~done
                hs = live & (L[:, j] <= stop)
                px[hs] = np.minimum(stop[hs], O[hs, j])
                k[hs] = 0
                ht = live & ~hs & (H[:, j] >= tgt)
                px[ht] = np.maximum(tgt[ht], O[ht, j])
                k[ht] = 1
                done |= hs | ht
                # Stop updates use this bar's information, so they apply from
                # the next bar on.
                hi_since = np.maximum(hi_since, H[:, j])
                if name == "breakeven":
                    stop = np.where(hi_since >= e * (1 + R), np.maximum(stop, e), stop)
                elif name == "trailing":
                    stop = np.maximum(stop, hi_since - e * R)
            gross[:, i, x] = px / e - 1.0
            kind[:, i, x] = k
    return gross, kind


def run():
    d, cost, trades = load()
    rules = sorted(trades["rule"].unique())
    sessions = sorted(trades["session"].unique())
    ri = {r: n for n, r in enumerate(rules)}
    sx = {s: n for n, s in enumerate(sessions)}
    X = len(EXITS)
    SUM = np.zeros((len(rules), len(sessions), 3, X))   # gross, net, excess
    CNT = np.zeros((len(rules), len(sessions)))
    KIND = np.zeros((len(rules), X, 3))
    fired = {sym: g for sym, g in trades.groupby("symbol")}
    for sym, g in d.groupby("symbol", sort=False):
        if sym not in fired:
            continue
        sess = g["session"].to_numpy()[::BARS]
        O, H, L, C, V = (g[c].to_numpy(float).reshape(-1, BARS)
                         for c in ("open", "high", "low", "close", "volume"))
        R = (pd.Series(((H - L) / C).mean(axis=1)).shift(1)
               .rolling(RANGE_LOOKBACK, min_periods=5).mean().to_numpy())
        prev_high = np.r_[np.nan, H.max(axis=1)[:-1]]
        cst = np.array([cost.get((sym, s[:7]), np.nan) for s in sess])
        ok = np.isfinite(R) & np.isfinite(cst) & np.isfinite(prev_high)
        if ok.sum() < 30:
            continue
        O, H, L, C, V, R, cst, sess, prev_high = (a[ok] for a in
            (O, H, L, C, V, R, cst, sess, prev_high))
        gross, kind = simulate(O, H, L, C, V, R, prev_high)
        net = gross - cst[:, None, None]
        b = np.nanmean(net, axis=0)
        pos = {s: n for n, s in enumerate(sess)}
        f = fired[sym]
        f = f[f["session"].isin(pos)]
        if f.empty:
            continue
        si = f["session"].map(pos).to_numpy()
        bi = f["bar"].to_numpy()
        vals = np.stack([gross[si, bi], net[si, bi], net[si, bi] - b[bi]], axis=1)
        rr = f["rule"].map(ri).to_numpy()
        ss = f["session"].map(sx).to_numpy()
        np.add.at(SUM, (rr, ss), vals)
        np.add.at(CNT, (rr, ss), 1)
        kk = kind[si, bi]                                 # (n, X)
        for x in range(X):
            np.add.at(KIND, (rr, x, kk[:, x]), 1)
    return rules, SUM, CNT, KIND


def score(rules, SUM, CNT, KIND) -> pd.DataFrame:
    from scipy.stats import t as tdist
    out = []
    for a, rule in enumerate(rules):
        has = CNT[a] > 0
        n = int(has.sum())
        per = SUM[a][has] / CNT[a][has][:, None, None]
        tw = SUM[a].sum(axis=0) / CNT[a].sum()
        for x, name in enumerate(EXITS):
            net = per[:, 1, x]
            t = net.mean() / (net.std(ddof=1) / np.sqrt(n))
            ks = KIND[a, x] / KIND[a, x].sum()
            out.append({
                "rule": rule, "exit": name, "trades": int(CNT[a].sum()), "sessions": n,
                "gross_tw_bp": tw[0, x] * 1e4, "net_tw_bp": tw[1, x] * 1e4,
                "gross_sw_bp": per[:, 0, x].mean() * 1e4,
                "net_sw_bp": net.mean() * 1e4,
                "excess_sw_bp": per[:, 2, x].mean() * 1e4,
                "t_net": t, "p_net": tdist.sf(t, n - 1),       # one-sided: net > 0
                "pct_stop": ks[0], "pct_target": ks[1], "pct_close": ks[2]})
    r = pd.DataFrame(out).sort_values("p_net").reset_index(drop=True)
    m = len(r)
    r["bh"] = r["p_net"].to_numpy() <= np.arange(1, m + 1) / m * 0.05
    r["passes"] = r["bh"] & (r["net_tw_bp"] > 0) & (r["net_sw_bp"] > 0)
    return r


def main() -> int:
    print(f"12 rules x {len(EXITS)} realistic exits, declared as trial 39\n")
    r = score(*run())
    r.to_csv(ROOT / "reports" / "realistic_exits.csv", index=False)
    r = r.sort_values("net_sw_bp", ascending=False)
    print(f"{'rule':<26}{'exit':<12}{'gross':>8}{'net/trade':>10}{'net/day':>9}"
          f"{'excess':>8}{'t':>7}{'stop':>6}{'tgt':>6}{'close':>6}")
    for _, x in r.iterrows():
        print(f"{x['rule']:<26}{x['exit']:<12}{x.gross_tw_bp:>7.1f}b{x.net_tw_bp:>9.1f}b"
              f"{x.net_sw_bp:>8.1f}b{x.excess_sw_bp:>7.1f}b{x.t_net:>7.2f}"
              f"{x.pct_stop:>6.0%}{x.pct_target:>6.0%}{x.pct_close:>6.0%}")
    print("\nbest exit per rule (net per trade):")
    for rule, g in r.groupby("rule"):
        b = g.sort_values("net_tw_bp").iloc[-1]
        print(f"  {rule:<26}{b['exit']:<12}{b.net_tw_bp:+.1f}bp")
    print(f"\ncombinations with positive net per trade: {(r.net_tw_bp > 0).sum()} of {len(r)}")
    print(f"combinations meeting the declared pass bar: {int(r.passes.sum())}")
    print("\nwrote reports/realistic_exits.csv")
    return 0


if __name__ == "__main__":
    sys.exit(main())
