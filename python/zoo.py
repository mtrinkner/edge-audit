#!/usr/bin/env python3
"""Run every classical technical signal through the same honest evaluation.

    python3 python/zoo.py
    python3 python/zoo.py --horizons 5 21 63

Each signal is a rule: on days it fires, buy those names equal-weighted, hold for
the horizon, pay the modeled round-trip cost. One return per DATE, never one per
row, because signals fire in bursts and counting each row separately is what
inflated this project's first Sharpe from 0.36 to 1.59.

Every signal-horizon pair is one trial and all of them go in the registry. Testing
41 signals at 3 horizons is 123 looks, and the bar a winner must clear rises
accordingly. That is the honest cost of a wide search, and the whole point of
running it this way rather than reporting whichever one came out on top.

The lockbox is enforced here: this reads nothing on or after the sealed date.
"""

from __future__ import annotations

import argparse
import sys

import numpy as np
import pandas as pd

import config
import db
import lockbox
import signals as sg
from deflated_sharpe import deflated_sharpe_ratio, expected_max_sharpe

ROUND_TRIP = 2 * (config.COSTS.slippage_bps + config.COSTS.spread_bps) / 10_000


def load_bars(max_date: str) -> pd.DataFrame:
    with db.connect() as con:
        return pd.read_sql(
            "SELECT b.symbol, b.dt, b.open, b.high, b.low, b.close, b.adj_close, "
            "b.volume FROM bars b JOIN symbols s ON s.symbol=b.symbol "
            "WHERE s.kind='stock' AND b.dt <= ? ORDER BY b.symbol, b.dt",
            con, params=(max_date,))


def build(bars: pd.DataFrame, horizons: list[int]) -> pd.DataFrame:
    """Signal firings plus forward net returns, per symbol and date."""
    out = []
    for sym, g in bars.groupby("symbol", sort=False):
        g = g.sort_values("dt").reset_index(drop=True)
        row = {"symbol": g["symbol"], "dt": g["dt"]}
        for h in horizons:
            fwd = g["adj_close"].shift(-h) / g["adj_close"] - 1.0
            row[f"ret_{h}"] = fwd - ROUND_TRIP
        for name in sg.SIGNALS:
            row[name] = sg.compute(g, name)
        out.append(pd.DataFrame(row))
    return pd.concat(out, ignore_index=True)


def evaluate(panel: pd.DataFrame, name: str, h: int,
             bench: pd.Series, reps: int = 2000) -> dict:
    fired = panel[panel[name]]
    col = f"ret_{h}"
    fired = fired.dropna(subset=[col])
    if len(fired) < 50:
        return {"signal": name, "horizon": h, "trades": len(fired), "dates": 0,
                "sharpe": None, "excess": None, "p": None}

    per_date = fired.groupby("dt")[col].mean().sort_index()
    if len(per_date) < 30:
        return {"signal": name, "horizon": h, "trades": len(fired),
                "dates": len(per_date), "sharpe": None, "excess": None, "p": None}

    # Sharpe on non-overlapping dates only. Consecutive dates share h-1 days of
    # outcome, so using all of them would count the same week many times.
    nonov = per_date.to_numpy()[::h]
    sharpe = (float(nonov.mean() / nonov.std(ddof=1) * np.sqrt(252 / h))
              if len(nonov) > 2 and nonov.std(ddof=1) > 0 else None)

    common = per_date.index.intersection(bench.index)
    d = (per_date.loc[common] - bench.loc[common]).dropna().to_numpy()
    p = excess = None
    if len(d) >= 30:
        excess = float(d.mean())
        rng = np.random.default_rng(config.RANDOM_SEED)
        centered = d - d.mean()
        n = len(d)

        def idx():
            o, pr = [], 1 / max(2, h * 2)
            while len(o) < n:
                s = int(rng.integers(n))
                o.extend(((s + np.arange(int(rng.geometric(pr)))) % n).tolist())
            return np.array(o[:n])

        boot = np.array([centered[idx()].mean() for _ in range(reps)])
        p = float((np.abs(boot) >= abs(d.mean())).mean())

    return {"signal": name, "horizon": h, "trades": int(len(fired)),
            "dates": int(len(per_date)), "n_nonoverlap": int(len(nonov)),
            "sharpe": sharpe, "excess": excess, "p": p,
            "hit_rate": float((fired[col] > 0).mean())}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--horizons", nargs="*", type=int, default=[5, 21, 63])
    ap.add_argument("--summarize", action="store_true",
                    help="re-report from reports/signal_zoo.csv without recomputing")
    ap.add_argument("--max-date", default=None,
                    help="defaults to the day before the lockbox seal")
    args = ap.parse_args()

    st = lockbox.state()
    if args.max_date:
        max_date = args.max_date
    elif st["sealed"]:
        d = pd.Timestamp(st["seal_from"]) - pd.Timedelta(days=1)
        max_date = d.date().isoformat()
    else:
        max_date = config.END_DATE.isoformat()

    try:
        lockbox.guard(max_date, "zoo")
    except lockbox.LockboxViolation as e:
        print(e, file=sys.stderr)
        return 1
    print(f"reading data through {max_date} (lockbox sealed from "
          f"{st.get('seal_from') or 'n/a'})")

    if args.summarize:
        res = pd.read_csv(config.ROOT / "reports" / "signal_zoo.csv")
        tested = int(res["sharpe"].notna().sum())
        total = len(res)
        return report(res, tested, total, args)

    bars = load_bars(max_date)
    print(f"  {len(bars):,} bars, {bars['symbol'].nunique()} symbols")
    print(f"  computing {len(sg.SIGNALS)} signals at horizons {args.horizons}")
    panel = build(bars, args.horizons)

    # Benchmark: the average stock, same horizon, same costs. Beating zero is
    # unremarkable when the market drifts up; beating the average stock is the
    # question that matters.
    benches = {h: panel.groupby("dt")[f"ret_{h}"].mean() for h in args.horizons}

    rows = []
    total = len(sg.SIGNALS) * len(args.horizons)
    for i, name in enumerate(sg.SIGNALS, 1):
        for h in args.horizons:
            rows.append(evaluate(panel, name, h, benches[h]))
        print(f"\r  evaluated {i*len(args.horizons)}/{total}", end="", flush=True)
    print()

    res = pd.DataFrame(rows)
    tested = int(res["sharpe"].notna().sum())
    res.to_csv(config.ROOT / "reports" / "signal_zoo.csv", index=False)
    return report(res, tested, total, args)


def report(res: pd.DataFrame, tested: int, total: int, args) -> int:

    # The registry is the authoritative trial count and ALREADY includes this
    # zoo once its run is recorded. Adding `total` again here double counted it
    # (175 became 298 on a rerun), which inflated the bar off a number that was
    # already correct. The registry is the single source of truth for N.
    import registry
    n_total = registry.trials()["n_trials"]
    bar = expected_max_sharpe(n_total, 348)

    ranked = res.dropna(subset=["sharpe"]).sort_values("sharpe", ascending=False)
    print(f"\n{'=' * 78}")
    print(f"SIGNAL ZOO — {total} trials ({len(sg.SIGNALS)} signals x "
          f"{len(args.horizons)} horizons), {tested} evaluable")
    print("=" * 78)
    print(f"{'signal':<26}{'h':>4}{'trades':>8}{'dates':>7}{'sharpe':>8}"
          f"{'excess':>9}{'p':>7}")
    for _, r in ranked.head(15).iterrows():
        ex = f"{r.excess*100:+.3f}%" if pd.notna(r.excess) else "    -"
        pv = f"{r.p:.3f}" if pd.notna(r.p) else "   -"
        print(f"{r.signal:<26}{int(r.horizon):>4}{int(r.trades):>8}"
              f"{int(r.dates):>7}{r.sharpe:>8.2f}{ex:>9}{pv:>7}")

    print(f"\n{'-' * 78}")
    print(f"signal-horizon pairs run      {total}")
    print(f"total trials in the registry  {n_total}")
    print(f"bar the best must clear       {bar:.3f}  (median best from pure noise)")
    # Each signal gets its OWN sample size. An earlier version passed 348 for
    # every row, which is the project's own effective sample, not the signal's.
    # That reported the top result (6 independent observations) as 0.992
    # probability of being real. A Sharpe from 6 points is not evidence of
    # anything, and a correction fed the wrong n will happily say otherwise.
    ranked = ranked.copy()
    ranked["dsr"] = [
        deflated_sharpe_ratio(r.sharpe, n_total, max(3, int(r.n_nonoverlap)))
        for r in ranked.itertuples()
    ]
    ranked["bar_own_n"] = [
        expected_max_sharpe(n_total, max(3, int(r.n_nonoverlap)))
        for r in ranked.itertuples()
    ]
    best = ranked.iloc[0] if len(ranked) else None
    if best is not None:
        dsr = float(best.dsr)
        print(f"best observed                 {best.sharpe:.3f}  ({best.signal}, "
              f"{int(best.horizon)}d)")
        print(f"deflated p(real)              {dsr:.3f}  (want > 0.95)")
        # Clearing means beating the luck bar for its OWN sample size and
        # reaching a deflated probability of 0.95. Sharpe alone is not enough:
        # a signal with six observations can post a big ratio and mean nothing.
        clears = ranked[(ranked["sharpe"] > ranked["bar_own_n"])
                        & (ranked["dsr"] > 0.95)]
        print(f"\nsignals clearing the bar      {len(clears)} of {tested}")
        if len(clears):
            print("  (must beat its own-sample bar AND reach 0.95 deflated)")
            for _, c in clears.iterrows():
                print(f"    {c.signal} {int(c.horizon)}d  sharpe {c.sharpe:.2f} "
                      f"vs bar {c.bar_own_n:.2f}, dsr {c.dsr:.3f}")
        sig = ranked[(ranked["p"] < 0.05) & ranked["p"].notna()]
        print(f"significant before correction {len(sig)} of {tested}")
        print(f"expected false positives at p<0.05 from noise alone: "
              f"{tested * 0.05:.1f}")
        if len(clears) == 0:
            print("\nNothing clears. Forty-one of the most widely published")
            print("technical signals, three horizons each, and not one of them")
            print("produces a result better than what testing this many ideas")
            print("yields from noise.")
    print(f"\nwrote reports/signal_zoo.csv")
    return 0


if __name__ == "__main__":
    sys.exit(main())
