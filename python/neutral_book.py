#!/usr/bin/env python3
"""Sector-neutral portfolio construction, and a measure of what breadth you
actually have rather than what you nominally have.

    python3 python/neutral_book.py
    python3 python/neutral_book.py --horizons 63 126 --rebuild

THE PROBLEM THIS ADDRESSES. The fundamental law says IR = IC x sqrt(breadth).
With IC around 0.03 and 480 names rebalanced twelve times a year, the nominal
bet count says the information ratio should be roughly 2.3. The unconstrained
book realized 0.35.

That gap is not a mystery, it is correlation. Rank 480 stocks by momentum and the
top decile is not 48 independent opinions, it is mostly "overweight whichever
sector has been running". One sector bet repeated 48 times is one bet. Breadth
counts INDEPENDENT decisions, and nothing about holding more names makes them
independent.

So: demean every score within its sector on each date. A stock is then scored
against its peers rather than against the whole market, the book holds the same
sector weights long and short, and the sector bet disappears. What is left is
stock selection, which is the thing the signal was supposed to be measuring.

EFFECTIVE BREADTH. Running the law backwards, B_eff = (IR / IC)^2, gives the
number of independent bets the book behaved as though it had. Comparing that to
the nominal count shows how much of the theoretical breadth the correlation
structure is eating. It is the most useful diagnostic here, because it says
whether to go hunting for a better signal or to go fix the portfolio.
"""

from __future__ import annotations

import argparse
import sys

import numpy as np
import pandas as pd

import config
import db
import lockbox
from cross_sectional import SCORES, build_panel, daily_ic

CACHE = config.ROOT / "data" / "xs_panel.parquet"
ROUND_TRIP = 2 * (config.COSTS.slippage_bps + config.COSTS.spread_bps) / 10_000


def get_panel(max_date: str, horizons: list[int], rebuild: bool) -> pd.DataFrame:
    if CACHE.exists() and not rebuild:
        p = pd.read_parquet(CACHE)
        if all(f"fwd_{h}" in p.columns for h in horizons):
            print(f"  using cached panel ({len(p):,} rows)")
            return p
    print("  building panel (a few minutes)")
    p = build_panel(max_date, horizons)
    p.to_parquet(CACHE, index=False)
    return p


def zscore(s: pd.Series, keys: list[pd.Series]) -> pd.Series:
    g = s.groupby(keys)
    return (s - g.transform("mean")) / g.transform("std").replace(0, np.nan)


def book_returns(panel: pd.DataFrame, score: str, h: int, q: int,
                 sector_neutral: bool, min_names: int = 50) -> pd.Series:
    """Long top quantile, short bottom, net of two-sided costs.

    Sector neutral version picks the top and bottom within EACH sector and then
    weights sectors equally, so the book is long and short the same sector
    exposure and cannot express a sector view.
    """
    col = f"fwd_{h}"
    d = panel[["dt", "sector", score, col]].dropna()
    if d.empty:
        return pd.Series(dtype=float)
    if not sector_neutral:
        d = d[d.groupby("dt")[score].transform("size") >= min_names].copy()
        if d.empty:
            return pd.Series(dtype=float)
        d["b"] = d.groupby("dt")[score].transform(
            lambda x: pd.qcut(x.rank(method="first"), q, labels=False, duplicates="drop"))
        top = d[d.b == q - 1].groupby("dt")[col].mean()
        bot = d[d.b == 0].groupby("dt")[col].mean()
        return (top - bot) - 2 * ROUND_TRIP

    d = d[d.groupby(["dt", "sector"])[score].transform("size") >= 10].copy()
    if d.empty:
        return pd.Series(dtype=float)
    d["b"] = d.groupby(["dt", "sector"])[score].transform(
        lambda x: pd.qcut(x.rank(method="first"), min(q, max(2, len(x) // 5)),
                          labels=False, duplicates="drop"))
    mx = d.groupby(["dt", "sector"])["b"].transform("max")
    long_leg = d[d.b == mx].groupby(["dt", "sector"])[col].mean()
    short_leg = d[d.b == 0].groupby(["dt", "sector"])[col].mean()
    per_sector = (long_leg - short_leg).reset_index(name="r")
    # Equal weight across sectors: no sector can dominate the book.
    return per_sector.groupby("dt")["r"].mean() - 2 * ROUND_TRIP


def stats(ic: pd.Series, ret: pd.Series, h: int, nominal: int) -> dict:
    per_year = 252 / h
    out: dict = {}
    if len(ic) > 2:
        out["ic"] = float(ic.mean())
        out["ic_t"] = _t(ic.to_numpy()[::h])
    if len(ret) > 2:
        s = ret.to_numpy()[::h]
        out["ret_ann"] = float(ret.mean() * per_year)
        out["sharpe"] = (float(s.mean() / s.std(ddof=1) * np.sqrt(per_year))
                         if len(s) > 2 and s.std(ddof=1) > 0 else None)
        out["t"] = _t(s)
        # Run the fundamental law backwards for the breadth actually realized.
        if out.get("ic") and out.get("sharpe") and abs(out["ic"]) > 1e-6:
            out["breadth_eff"] = float((out["sharpe"] / out["ic"]) ** 2)
            out["breadth_pct"] = float(out["breadth_eff"] / nominal) if nominal else None
    return out


def _t(a: np.ndarray) -> float | None:
    if len(a) < 3 or a.std(ddof=1) == 0:
        return None
    return float(a.mean() / (a.std(ddof=1) / np.sqrt(len(a))))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--horizons", nargs="*", type=int, default=[21, 63, 126])
    ap.add_argument("--quantiles", type=int, default=5)
    ap.add_argument("--min-adv", type=float, default=5e6)
    ap.add_argument("--rebuild", action="store_true")
    args = ap.parse_args()

    st = lockbox.state()
    max_date = (pd.Timestamp(st["seal_from"]) - pd.Timedelta(days=1)).date().isoformat()
    lockbox.guard(max_date, "neutral_book")
    print(f"data through {max_date}")

    panel = get_panel(max_date, args.horizons, args.rebuild)
    panel = panel[panel["adv"] >= args.min_adv]
    n_names = panel.groupby("dt").size().median()
    print(f"  {panel['symbol'].nunique()} symbols, median {n_names:.0f} names/day, "
          f"{panel['sector'].nunique()} sectors")

    raw = {s: zscore(panel[s], [panel["dt"]]) for s in SCORES}
    neu = {s: zscore(panel[s], [panel["dt"], panel["sector"]]) for s in SCORES}

    rows = []
    for name in SCORES:
        for h in args.horizons:
            nominal = n_names * (252 / h)
            p1 = panel.assign(**{name: raw[name]})
            p2 = panel.assign(**{name: neu[name]})
            r1 = stats(daily_ic(p1, name, h),
                       book_returns(p1, name, h, args.quantiles, False), h, nominal)
            r2 = stats(daily_ic(p2, name, h),
                       book_returns(p2, name, h, args.quantiles, True), h, nominal)
            rows.append({"score": name, "horizon": h,
                         **{f"raw_{k}": v for k, v in r1.items()},
                         **{f"neu_{k}": v for k, v in r2.items()}})
        print(f"\r  {name:<16}", end="", flush=True)
    print()

    res = pd.DataFrame(rows)
    res.to_csv(config.ROOT / "reports" / "neutral_book.csv", index=False)

    f = lambda v, s="{:.2f}": s.format(v) if pd.notna(v) else "    -"
    print(f"\n{'=' * 92}")
    print("SECTOR NEUTRAL vs UNCONSTRAINED — same signals, same costs, same dates")
    print("=" * 92)
    print(f"{'score':<16}{'h':>4}{'IC raw':>9}{'IC neu':>9}"
          f"{'Sharpe raw':>12}{'Sharpe neu':>12}{'t neu':>8}{'B_eff neu':>11}")
    show = res.sort_values("neu_sharpe", ascending=False)
    for _, r in show.iterrows():
        print(f"{r.score:<16}{int(r.horizon):>4}{f(r.get('raw_ic'),'{:.3f}'):>9}"
              f"{f(r.get('neu_ic'),'{:.3f}'):>9}{f(r.get('raw_sharpe')):>12}"
              f"{f(r.get('neu_sharpe')):>12}{f(r.get('neu_t')):>8}"
              f"{f(r.get('neu_breadth_eff'),'{:.0f}'):>11}")

    imp = res.dropna(subset=["raw_sharpe", "neu_sharpe"])
    better = int((imp.neu_sharpe > imp.raw_sharpe).sum())
    print(f"\nsector neutral beat unconstrained in {better} of {len(imp)} cases")
    print(f"median Sharpe  unconstrained {imp.raw_sharpe.median():.2f}   "
          f"neutral {imp.neu_sharpe.median():.2f}")
    best = show.iloc[0]
    print(f"\nbest neutral book: {best.score} at {int(best.horizon)}d, "
          f"Sharpe {f(best.get('neu_sharpe'))}, t {f(best.get('neu_t'))}")
    print(f"SPY over the same window: Sharpe 0.91")
    if pd.notna(best.get("neu_breadth_eff")):
        print(f"\neffective breadth of the best book: "
              f"{best.neu_breadth_eff:.0f} independent bets a year")
        print(f"nominal: {n_names * (252/best.horizon):.0f}. The ratio is how much of")
        print(f"your theoretical breadth the correlation between positions eats.")
    print(f"\nwrote reports/neutral_book.csv")
    return 0


if __name__ == "__main__":
    sys.exit(main())
