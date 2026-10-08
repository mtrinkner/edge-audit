#!/usr/bin/env python3
"""Does a second data source raise effective breadth? The before/after test.

    python3 python/earnings_book.py --horizons 21 63

THE QUESTION THIS ANSWERS. Thirteen price signals collapsed to 2.9 effective
independent strategies, and that collapse, not signal quality, is what capped the
information ratio at 0.35. The fundamental law says IR = IC x sqrt(breadth), so
the lever is independence, not cleverness.

Earnings surprise comes from a different generating process: analyst forecast
error rather than price history. If the resulting books are weakly correlated
with the price books, effective breadth rises and the same mediocre ICs convert
into a materially better ratio. If they are strongly correlated, the second data
source bought nothing and that is the finding.

Everything is measured the same way as before so the comparison is like for like:
sector-neutral quintile books, two-sided costs charged on every rebalance, Sharpe
computed on non-overlapping periods, and no correction for trial count applied
here because the registry owns that.
"""

from __future__ import annotations

import argparse
import sys

import numpy as np
import pandas as pd

import config
import db
import lockbox
from cross_sectional import SCORES as PRICE_SCORES, daily_ic
from earnings_signals import DRIFT_WINDOW_DAYS, as_of_panel, build_event_features
from neutral_book import book_returns, get_panel, zscore

EARNINGS_SCORES = ["sue", "surprise_pct", "drift_window", "beat_streak",
                   "surprise_accel", "days_since"]


def attach_earnings(panel: pd.DataFrame) -> pd.DataFrame:
    with db.connect() as con:
        ev = pd.read_sql(
            "SELECT symbol, tradeable_from, surprise_pct, eps_estimate, eps_actual "
            "FROM earnings WHERE surprise_pct IS NOT NULL ORDER BY symbol, tradeable_from",
            con)
        sess = pd.read_sql("SELECT dt, day_index FROM trading_days ORDER BY dt", con)
    print(f"  {len(ev):,} earnings events, {ev['symbol'].nunique()} symbols")

    ev = build_event_features(ev)
    base = panel[["symbol", "dt"]].copy()
    merged = as_of_panel(ev, base)

    idx = dict(zip(sess["dt"], sess["day_index"]))
    merged["days_since"] = (merged["dt"].map(idx)
                            - merged["tradeable_from"].map(idx))
    merged["drift_window"] = (
        (merged["days_since"] >= 0) & (merged["days_since"] <= DRIFT_WINDOW_DAYS)
    ).astype(float)
    # Outside the drift window the event is stale; blanking it keeps a release
    # from three years ago out of today's cross-section.
    stale = merged["days_since"] > 250
    for c in ("sue", "surprise_pct", "beat_streak", "surprise_accel"):
        merged.loc[stale, c] = np.nan
    merged["days_since"] = -merged["days_since"]   # recent = high = ranked first

    out = panel.merge(
        merged[["symbol", "dt"] + EARNINGS_SCORES], on=["symbol", "dt"], how="left")
    cov = out[EARNINGS_SCORES].notna().mean()
    print("  coverage of each earnings signal across the panel:")
    for k, v in cov.items():
        print(f"    {k:<16}{v:>7.1%}")
    return out


def effective_n(R: pd.DataFrame) -> tuple[float, float]:
    C = R.corr().dropna(how="all").dropna(axis=1, how="all")
    if C.empty or len(C) < 2:
        return float("nan"), float("nan")
    ev = np.linalg.eigvalsh(C.values)
    n = len(C)
    avg = (C.values.sum() - n) / (n * (n - 1))
    return float((ev.sum() ** 2) / (ev ** 2).sum()), float(avg)


def sharpe(x: np.ndarray, h: int) -> float | None:
    s = x[::h]
    if len(s) < 3 or s.std(ddof=1) == 0:
        return None
    return float(s.mean() / s.std(ddof=1) * np.sqrt(252 / h))


def tstat(x: np.ndarray, h: int) -> float | None:
    s = x[::h]
    if len(s) < 3 or s.std(ddof=1) == 0:
        return None
    return float(s.mean() / (s.std(ddof=1) / np.sqrt(len(s))))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--horizons", nargs="*", type=int, default=[21, 63])
    ap.add_argument("--quantiles", type=int, default=5)
    ap.add_argument("--min-adv", type=float, default=5e6)
    args = ap.parse_args()

    st = lockbox.state()
    max_date = (pd.Timestamp(st["seal_from"]) - pd.Timedelta(days=1)).date().isoformat()
    lockbox.guard(max_date, "earnings_book")
    print(f"data through {max_date}")

    panel = get_panel(max_date, args.horizons, False)
    panel = panel[panel["adv"] >= args.min_adv]
    panel = attach_earnings(panel)

    results, books = [], {}
    for h in args.horizons:
        for group, names in (("price", list(PRICE_SCORES)),
                             ("earnings", EARNINGS_SCORES)):
            for name in names:
                p = panel.assign(**{name: zscore(panel[name],
                                                 [panel["dt"], panel["sector"]])})
                ic = daily_ic(p, name, h)
                r = book_returns(p, name, h, args.quantiles, True)
                if len(r) < 60:
                    continue
                books[(group, name, h)] = r
                arr = r.to_numpy()
                results.append({
                    "group": group, "signal": name, "horizon": h,
                    "ic": float(ic.mean()) if len(ic) else np.nan,
                    "sharpe": sharpe(arr, h), "t": tstat(arr, h),
                    "ret_ann": float(r.mean() * 252 / h), "n_dates": len(r),
                })
        print(f"  horizon {h}d done")

    res = pd.DataFrame(results)
    res.to_csv(config.ROOT / "reports" / "earnings_book.csv", index=False)

    f = lambda v, s="{:.2f}": s.format(v) if pd.notna(v) else "    -"
    for h in args.horizons:
        sub = res[res.horizon == h].sort_values("sharpe", ascending=False)
        if sub.empty:
            continue
        print(f"\n{'=' * 74}\nHORIZON {h}d — sector-neutral quintile books\n{'=' * 74}")
        print(f"{'group':<10}{'signal':<17}{'IC':>8}{'ret/yr':>10}{'Sharpe':>9}{'t':>8}")
        for _, r in sub.iterrows():
            print(f"{r.group:<10}{r.signal:<17}{f(r.ic,'{:.4f}'):>8}"
                  f"{f(r.ret_ann,'{:+.2%}'):>10}{f(r.sharpe):>9}{f(r.t):>8}")

    # ---------------------------------------------- the actual question
    print(f"\n{'=' * 74}\nEFFECTIVE BREADTH — before and after the second data source\n{'=' * 74}")
    for h in args.horizons:
        pr = pd.DataFrame({k[1]: v for k, v in books.items()
                           if k[0] == "price" and k[2] == h}).dropna()
        ea = pd.DataFrame({k[1]: v for k, v in books.items()
                           if k[0] == "earnings" and k[2] == h}).dropna()
        if pr.empty or ea.empty:
            continue
        both = pr.join(ea, how="inner", lsuffix="_p", rsuffix="_e").dropna()
        n_pr, avg_pr = effective_n(pr)
        n_bo, avg_bo = effective_n(both)
        cross = pr.join(ea, how="inner", lsuffix="_p", rsuffix="_e").corr()
        pcols = [c for c in cross.columns if c in pr.columns or c.endswith("_p")]
        ecols = [c for c in cross.columns if c in ea.columns or c.endswith("_e")]
        xc = cross.loc[pcols, ecols].values
        print(f"\nhorizon {h}d")
        print(f"  price signals only      : {pr.shape[1]} nominal -> "
              f"{n_pr:.1f} effective  (avg corr {avg_pr:+.2f})")
        print(f"  price + earnings        : {both.shape[1]} nominal -> "
              f"{n_bo:.1f} effective  (avg corr {avg_bo:+.2f})")
        print(f"  cross-correlation price vs earnings: {np.nanmean(xc):+.3f} "
              f"(mean), {np.nanmax(np.abs(xc)):.3f} (max abs)")
        gain = n_bo - n_pr
        print(f"  effective strategies gained: {gain:+.1f}")
        if n_pr > 0:
            print(f"  implied Sharpe multiplier from breadth alone: "
                  f"{np.sqrt(n_bo / n_pr):.2f}x")

    print(f"\nwrote reports/earnings_book.csv")
    return 0


if __name__ == "__main__":
    sys.exit(main())
