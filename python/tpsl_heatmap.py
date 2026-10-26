#!/usr/bin/env python3
"""
Take-profit / stop-loss heatmaps for the 12 day-trading rules (trial 38).

    python3 python/tpsl_heatmap.py

WHERE THIS COMES FROM

CodeTrading's "How to Spot and Avoid Overfitting" argues: leave the indicators at
their defaults, tune only the take-profit and stop-loss, draw every combination
as a heatmap, and call an indicator robust if the map is green almost everywhere.

The plateau idea is sound. One green cell in a sea of red is overfitting. But a
green map is not an edge, for a reason the video never tests: every cell shares
the same entries, so if the entries happen to land on days the stock rose anyway,
every cell is green. So each cell is scored TWICE:

  raw     the video's number. Mean net return of the trades.
  excess  the same exits applied after entering at the SAME BAR on EVERY session
          of that symbol, subtracted. What the signal added over the clock.

THE GRID. Stops and targets in units of the symbol's trailing 20-session mean
hourly range, measured on sessions BEFORE the current one. 5 x 5 = 25 cells,
12 rules, 300 trials, declared before any return was computed.

EXITS. Entry at the close of the signal bar, as in intraday_study.py. On each
later bar: low at or below the stop exits at the stop; high at or above the
target exits at the target; both in one bar is scored as the stop, because hourly
bars cannot say which came first and assuming the good one is how backtests lie.
Otherwise out at the session close. Never held overnight.

COSTS. Two times the EDGE half-spread measured for that symbol in the PREVIOUS
month, so the cost is known before the trade. Symbols without a prior month of
EDGE are dropped rather than given an invented spread.

CORRECTION (trial 42). The "net/day" and session-weighted columns split each
session's capital equally across ALL of that session's signals, which needs the
end-of-day count at entry time. vwap_reversion_long fires more on days the market
keeps falling, so that weighting quietly down-weights the losing days. It is not
tradeable. The per-trade columns are the causal results.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "python"))

import config

BARS = 7
STOPS = [0.5, 1.0, 1.5, 2.0, 3.0]
TARGETS = [0.5, 1.0, 1.5, 2.0, 3.0]
CELLS = [(s, t) for s in STOPS for t in TARGETS]
RANGE_LOOKBACK = 20


def load():
    d = pd.read_parquet(ROOT / "data" / "intraday_1h.parquet")
    d = d[d["bars_in_session"] == BARS].sort_values(["symbol", "ts"])
    e = pd.read_parquet(ROOT / "data" / "edge_spreads.parquet")
    e = e.sort_values(["symbol", "month"])
    # Previous month's spread: known before any trade this month.
    e["cost"] = 2 * e.groupby("symbol")["edge_half_bps"].shift(1) / 10_000.0
    e["month"] = e["month"].astype(str)
    cost = {(s, m): c for s, m, c in zip(e["symbol"], e["month"], e["cost"])
            if np.isfinite(c)}
    t = pd.read_parquet(ROOT / "data" / "intraday_trades.parquet",
                        columns=["rule", "symbol", "session", "bar"])
    return d, cost, t


def outcomes(O, H, L, C, R, cost):
    """Net return for every (session, entry bar, cell). Shape (S, BARS, cells)."""
    S = len(C)
    out = np.full((S, BARS, len(CELLS)), np.nan)
    gross = np.full_like(out, np.nan)
    for i in range(BARS):
        entry = C[:, i]
        for k, (sm, tm) in enumerate(CELLS):
            stop = entry * (1 - sm * R)
            tgt = entry * (1 + tm * R)
            exitp = C[:, BARS - 1].copy()
            done = np.zeros(S, bool)
            for j in range(i + 1, BARS):
                hs = (~done) & (L[:, j] <= stop)
                ht = (~done) & (~hs) & (H[:, j] >= tgt)
                exitp[hs] = stop[hs]
                exitp[ht] = tgt[ht]
                done |= hs | ht
            g = exitp / entry - 1.0
            gross[:, i, k] = g
            out[:, i, k] = g - cost
    return out, gross


def run():
    """Accumulate per-(rule, session) sums so 4M trades x 25 cells never sit in
    memory at once. Returns sums (rules, sessions, 3, cells) and counts."""
    d, cost, trades = load()
    rules = sorted(trades["rule"].unique())
    sessions = sorted(trades["session"].unique())
    ri = {r: n for n, r in enumerate(rules)}
    sx = {s: n for n, s in enumerate(sessions)}
    SUM = np.zeros((len(rules), len(sessions), 3, len(CELLS)))
    CNT = np.zeros((len(rules), len(sessions)))
    fired = {sym: g for sym, g in trades.groupby("symbol")}
    used = 0
    for sym, g in d.groupby("symbol", sort=False):
        if sym not in fired:
            continue
        sess = g["session"].to_numpy()[::BARS]
        O, H, L, C = (g[c].to_numpy(float).reshape(-1, BARS)
                      for c in ("open", "high", "low", "close"))
        rng = pd.Series(((H - L) / C).mean(axis=1))
        R = rng.shift(1).rolling(RANGE_LOOKBACK, min_periods=5).mean().to_numpy()
        cst = np.array([cost.get((sym, s[:7]), np.nan) for s in sess])
        ok = np.isfinite(R) & np.isfinite(cst)
        if ok.sum() < 30:
            continue
        O, H, L, C, R, cst, sess = O[ok], H[ok], L[ok], C[ok], R[ok], cst[ok], sess[ok]
        net, gross = outcomes(O, H, L, C, R, cst)
        # Benchmark: every session of this symbol, same bar, same cell.
        b = np.nanmean(net, axis=0)
        pos = {s: n for n, s in enumerate(sess)}
        f = fired[sym]
        f = f[f["session"].isin(pos)]
        if f.empty:
            continue
        used += 1
        si = f["session"].map(pos).to_numpy()
        bi = f["bar"].to_numpy()
        vals = np.stack([gross[si, bi, :], net[si, bi, :],
                         net[si, bi, :] - b[bi, :]], axis=1)   # (n, 3, cells)
        rr = f["rule"].map(ri).to_numpy()
        ss = f["session"].map(sx).to_numpy()
        np.add.at(SUM, (rr, ss), vals)
        np.add.at(CNT, (rr, ss), 1)
    print(f"  {used} symbols with prior-month EDGE and a trailing range")
    print(f"  {int(CNT.sum()):,} trades x {len(CELLS)} cells")
    return rules, SUM, CNT


def score(rules, SUM, CNT) -> pd.DataFrame:
    from scipy.stats import t as tdist
    out = []
    for a, rule in enumerate(rules):
        has = CNT[a] > 0
        # One observation per session: a market-wide day is one bet.
        m = SUM[a][has] / CNT[a][has][:, None, None]          # (n, 3, cells)
        n = int(has.sum())
        for k, (sm, tm) in enumerate(CELLS):
            x = m[:, 2, k]
            sd = x.std(ddof=1)
            t = x.mean() / (sd / np.sqrt(n)) if n > 30 and sd > 0 else np.nan
            out.append({"rule": rule, "stop": sm, "target": tm, "sessions": n,
                        "trades": int(CNT[a].sum()),
                        "gross_bp": m[:, 0, k].mean() * 1e4,
                        "net_bp": m[:, 1, k].mean() * 1e4,
                        "excess_bp": x.mean() * 1e4, "t": t})
    r = pd.DataFrame(out)
    r["p"] = 2 * tdist.sf(np.abs(r["t"]), r["sessions"] - 1)
    k = r.dropna(subset=["p"]).sort_values("p").copy()
    mm = len(k)
    k["bh"] = k["p"].to_numpy() <= np.arange(1, mm + 1) / mm * 0.05
    r = r.merge(k[["rule", "stop", "target", "bh"]], how="left",
                on=["rule", "stop", "target"])
    r["bh"] = r["bh"].fillna(False).astype(bool)
    return r


def plot(r: pd.DataFrame, col: str, title: str, path: Path):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    rules = sorted(r["rule"].unique())
    v = np.nanmax(np.abs(r[col]))
    fig, axes = plt.subplots(3, 4, figsize=(16, 11))
    for ax, rule in zip(axes.flat, rules):
        g = r[r["rule"] == rule].pivot(index="stop", columns="target", values=col)
        ax.imshow(g.to_numpy(), cmap="RdYlGn", vmin=-v, vmax=v, origin="lower")
        for (a, b), val in np.ndenumerate(g.to_numpy()):
            ax.text(b, a, f"{val:+.0f}", ha="center", va="center", fontsize=7)
        ax.set_xticks(range(len(TARGETS)), TARGETS)
        ax.set_yticks(range(len(STOPS)), STOPS)
        ax.set_xlabel("target (x range)")
        ax.set_ylabel("stop (x range)")
        ax.set_title(rule, fontsize=9)
    fig.suptitle(title)
    fig.tight_layout()
    fig.savefig(path, dpi=110)
    plt.close(fig)


def main() -> int:
    print(f"{len(CELLS)} cells x 12 rules, declared as trial 38\n")
    r = score(*run())
    print()
    r.to_csv(ROOT / "reports" / "tpsl_heatmap.csv", index=False)
    fig = ROOT / "reports" / "figures"
    fig.mkdir(exist_ok=True)
    plot(r, "net_bp", "The video's heatmap: raw net return per trade (bp)",
         fig / "tpsl_raw.png")
    plot(r, "excess_bp", "The honest heatmap: excess over same bar, same exits, "
         "every session (bp)", fig / "tpsl_excess.png")

    s = r.groupby("rule").agg(
        gross_green=("gross_bp", lambda x: (x > 0).mean()),
        raw_green=("net_bp", lambda x: (x > 0).mean()),
        excess_green=("excess_bp", lambda x: (x > 0).mean()),
        best_excess=("excess_bp", "max"),
        mean_excess=("excess_bp", "mean"),
        bh=("bh", "sum")).sort_values("mean_excess", ascending=False)
    print(f"{'rule':<26}{'gross>0':>9}{'raw>0':>8}{'excess>0':>10}"
          f"{'mean exc':>10}{'best exc':>10}{'BH':>4}")
    for rule, x in s.iterrows():
        print(f"{rule:<26}{x.gross_green:>9.0%}{x.raw_green:>8.0%}"
              f"{x.excess_green:>10.0%}{x.mean_excess:>9.1f}b{x.best_excess:>9.1f}b"
              f"{int(x.bh):>4}")
    print(f"\ncells green, video's way (raw net):  {(r['net_bp'] > 0).mean():.0%}")
    print(f"cells green, gross before costs:      {(r['gross_bp'] > 0).mean():.0%}")
    print(f"cells green, excess over the clock:   {(r['excess_bp'] > 0).mean():.0%}")
    print(f"cells surviving BH of 300:            {int(r['bh'].sum())}")
    passing = s[(s["excess_green"] >= 0.8) & (s["bh"] > 0)]
    print(f"rules meeting the declared pass bar:  {len(passing)}")
    print("\nwrote reports/tpsl_heatmap.csv, figures/tpsl_raw.png, figures/tpsl_excess.png")
    return 0


if __name__ == "__main__":
    sys.exit(main())
