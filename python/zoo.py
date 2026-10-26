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

FIXED 2026-10-26 (audit). Trial 8's original output is
reports/signal_zoo_pre_2026-10-26.csv.
  * Forward returns start at the next session's open, not the signal close.
  * The non-overlapping series steps through FIRING dates ([::h]); it is now
    annualised by how often those samples actually occur, not as if the signal
    traded every h sessions (sparse signals were inflated).
  * The deflated Sharpe and the luck bar use each row's own sampling frequency
    (they were in weekly units for every horizon).
  * Every signal here is long-only, so it is judged on excess over the average
    stock. A raw Sharpe against a zero-return bar credited market beta: before
    the fix, above_sma_200 at 63 days had a deflated Sharpe of 0.94 with zero
    excess over holding.
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
        # Enter at the NEXT session's open, exit at the close h sessions after
        # the signal (fixed 2026-10-26; this used to enter at the signal close).
        adj_open = g["open"] * g["adj_close"] / g["close"].replace(0, np.nan)
        for h in horizons:
            fwd = g["adj_close"].shift(-h) / adj_open.shift(-1) - 1.0
            row[f"ret_{h}"] = fwd - ROUND_TRIP
        for name in sg.SIGNALS:
            row[name] = sg.compute(g, name)
        out.append(pd.DataFrame(row))
    return pd.concat(out, ignore_index=True)


def _spaced_sharpe(x: pd.Series, h: int, day_index: dict) -> tuple:
    """Sharpe of a firing-date series sampled every h-th firing, annualised by
    how often those samples actually occur (fixed 2026-10-26: sparse signals were
    annualised as if they traded every h sessions). Returns (sharpe, n, periods/yr)."""
    nonov = x.iloc[::h]
    v = nonov.to_numpy()
    if len(v) <= 2 or v.std(ddof=1) <= 0:
        return None, len(v), None
    span = day_index[nonov.index[-1]] - day_index[nonov.index[0]] + h
    ppy = len(v) / (span / 252)
    return float(v.mean() / v.std(ddof=1) * np.sqrt(ppy)), len(v), float(ppy)


def evaluate(panel: pd.DataFrame, name: str, h: int,
             bench: pd.Series, day_index: dict, reps: int = 2000) -> dict:
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
    sharpe, n_nonov, ppy = _spaced_sharpe(per_date, h, day_index)

    # Every signal here is long-only, so it is judged on its return OVER the
    # average stock (fixed 2026-10-26; raw Sharpe against a zero-return bar
    # credited market beta as skill).
    common = per_date.index.intersection(bench.index)
    dx = (per_date.loc[common] - bench.loc[common]).dropna()
    ex_sharpe, n_ex, ppy_ex = _spaced_sharpe(dx, h, day_index)
    d = dx.to_numpy()
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
            "dates": int(len(per_date)), "n_nonoverlap": int(n_nonov),
            "periods_per_year": ppy, "sharpe": sharpe,
            "excess_sharpe": ex_sharpe, "n_excess": int(n_ex),
            "excess_periods_per_year": ppy_ex, "excess": excess, "p": p,
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
        tested = int(res["excess_sharpe"].notna().sum())
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

    day_index = {d: i for i, d in enumerate(sorted(panel["dt"].unique()))}
    rows = []
    total = len(sg.SIGNALS) * len(args.horizons)
    for i, name in enumerate(sg.SIGNALS, 1):
        for h in args.horizons:
            rows.append(evaluate(panel, name, h, benches[h], day_index))
        print(f"\r  evaluated {i*len(args.horizons)}/{total}", end="", flush=True)
    print()

    res = pd.DataFrame(rows)
    tested = int(res["excess_sharpe"].notna().sum())
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

    # Ranked and judged on EXCESS over the average stock (see evaluate()).
    ranked = res.dropna(subset=["excess_sharpe"]).sort_values("excess_sharpe", ascending=False)
    print(f"\n{'=' * 86}")
    print(f"SIGNAL ZOO — {total} trials ({len(sg.SIGNALS)} signals x "
          f"{len(args.horizons)} horizons), {tested} evaluable")
    print("=" * 86)
    print(f"{'signal':<26}{'h':>4}{'trades':>8}{'dates':>7}{'raw sh':>8}"
          f"{'exc sh':>8}{'excess':>9}{'p':>7}")
    for _, r in ranked.head(15).iterrows():
        ex = f"{r.excess*100:+.3f}%" if pd.notna(r.excess) else "    -"
        pv = f"{r.p:.3f}" if pd.notna(r.p) else "   -"
        rs = f"{r.sharpe:.2f}" if pd.notna(r.sharpe) else "-"
        print(f"{r.signal:<26}{int(r.horizon):>4}{int(r.trades):>8}"
              f"{int(r.dates):>7}{rs:>8}{r.excess_sharpe:>8.2f}{ex:>9}{pv:>7}")

    print(f"\n{'-' * 86}")
    print(f"signal-horizon pairs run      {total}")
    print(f"total trials in the registry  {n_total}")
    print(f"bar at 348 weekly obs         {bar:.3f}  (median best from pure noise)")
    # Each signal gets its OWN sample size and its OWN sampling frequency. An
    # earlier version passed 348 for every row (the project's effective sample,
    # not the signal's), and until 2026-10-26 every row was deflated in weekly
    # units although 21- and 63-day Sharpes are annualised at their own spacing.
    ranked = ranked.copy()
    ranked["dsr"] = [
        deflated_sharpe_ratio(r.excess_sharpe, n_total, max(3, int(r.n_excess)),
                              periods_per_year=r.excess_periods_per_year)
        for r in ranked.itertuples()
    ]
    ranked["bar_own_n"] = [
        expected_max_sharpe(n_total, max(3, int(r.n_excess)),
                            periods_per_year=r.excess_periods_per_year)
        for r in ranked.itertuples()
    ]
    best = ranked.iloc[0] if len(ranked) else None
    if best is not None:
        dsr = float(best.dsr)
        print(f"best excess Sharpe            {best.excess_sharpe:.3f}  ({best.signal}, "
              f"{int(best.horizon)}d)")
        print(f"deflated p(real)              {dsr:.3f}  (want > 0.95)")
        # Clearing means beating the luck bar for its OWN sample size and
        # reaching a deflated probability of 0.95. Sharpe alone is not enough:
        # a signal with six observations can post a big ratio and mean nothing.
        clears = ranked[(ranked["excess_sharpe"] > ranked["bar_own_n"])
                        & (ranked["dsr"] > 0.95)]
        print(f"\nsignals clearing the bar      {len(clears)} of {tested}")
        if len(clears):
            print("  (excess Sharpe must beat its own-sample bar AND reach 0.95 deflated)")
            for _, c in clears.iterrows():
                print(f"    {c.signal} {int(c.horizon)}d  excess sharpe {c.excess_sharpe:.2f} "
                      f"vs bar {c.bar_own_n:.2f}, dsr {c.dsr:.3f}")
        sig = ranked[(ranked["p"] < 0.05) & ranked["p"].notna()]
        print(f"significant before correction {len(sig)} of {tested}")
        print(f"expected false positives at p<0.05 from noise alone: "
              f"{tested * 0.05:.1f}")
        if len(clears) == 0:
            print("\nNothing clears: no signal beats the average stock by more than")
            print("testing this many ideas yields from noise.")
    print(f"\nwrote reports/signal_zoo.csv")
    return 0


if __name__ == "__main__":
    sys.exit(main())
