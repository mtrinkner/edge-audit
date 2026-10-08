#!/usr/bin/env python3
"""The before/after test: price-only composite vs price-plus-earnings, walk-forward.

    python3 python/combined_book.py --horizon 63

CONSTRUCTION RULES, CHOSEN TO AVOID FITTING THE ANSWER

Signal orientation comes from the published literature, never from this sample.
Low-volatility posted a Sharpe of -0.90 here because 2014-2026 was a long
high-beta bull market; flipping its sign on that basis would be fitting the
composite to the very period being measured. It is simply excluded instead,
along with every other signal whose direction is not settled in the literature.

Weights are equal within each sleeve and equal across sleeves. No optimizer. A
mean-variance fit over nineteen correlated books with a few dozen independent
observations would produce a beautiful in-sample curve and nothing else.

Evaluation is walk-forward: the composite is formed with no reference to the test
window at all, since nothing is estimated from data. The folds exist so the
comparison is reported out-of-sample and per regime rather than as one pooled
number that could hide a single lucky stretch.

Costs are charged two-sided on every rebalance, same as everywhere else.
"""

from __future__ import annotations

import argparse
import sys

import numpy as np
import pandas as pd

import config
import db
import lockbox
from cross_sectional import daily_ic
from earnings_book import EARNINGS_SCORES, attach_earnings, effective_n
from neutral_book import book_returns, get_panel, zscore

# One representative per distinct documented effect, orientation fixed a priori.
PRICE_SLEEVE = {
    "mom_126d": +1,        # intermediate momentum, Jegadeesh & Titman 1993
    "mom_252d_skip": +1,   # 12-month momentum
    "reversal_5d": +1,     # short-horizon reversal, Lehmann 1990
    "near_52w_high": +1,   # 52-week high effect, George & Hwang 2004
}
EARNINGS_SLEEVE = {
    "sue": +1,             # standardized surprise, Bernard & Thomas 1989
    "surprise_pct": +1,    # raw surprise
}


def make_sleeve(panel: pd.DataFrame, weights: dict, name: str) -> pd.Series:
    parts, used = [], []
    for k, w in weights.items():
        if k not in panel.columns:
            continue
        z = zscore(panel[k], [panel["dt"], panel["sector"]])
        parts.append(w * z)
        used.append(k)
    if not parts:
        return pd.Series(np.nan, index=panel.index)
    print(f"  {name} sleeve: {len(used)} signals {used}")
    return pd.concat(parts, axis=1).mean(axis=1, skipna=True)


def evaluate(panel: pd.DataFrame, col: str, h: int, q: int) -> dict:
    ic = daily_ic(panel, col, h)
    r = book_returns(panel, col, h, q, True)
    if len(r) < 60:
        return {}
    a = r.to_numpy()
    s = a[::h]
    # Drawdown must be computed on NON-OVERLAPPING periods. Compounding a series
    # of overlapping 63-day returns as though they were sequential multiplies the
    # same stretch of market roughly 63 times and produced a nonsensical 100%
    # drawdown in the first version of this report.
    eq = (1 + pd.Series(s)).cumprod()
    peak = eq.cummax()
    return {
        "ic": float(ic.mean()) if len(ic) else np.nan,
        "ic_t": float(ic.to_numpy()[::h].mean()
                      / (ic.to_numpy()[::h].std(ddof=1) / np.sqrt(len(ic.to_numpy()[::h]))))
        if len(ic) > 3 * h else np.nan,
        "ret_ann": float(r.mean() * 252 / h),
        "vol_ann": float(s.std(ddof=1) * np.sqrt(252 / h)),
        "sharpe": float(s.mean() / s.std(ddof=1) * np.sqrt(252 / h)) if s.std(ddof=1) else None,
        "t": float(s.mean() / (s.std(ddof=1) / np.sqrt(len(s)))) if s.std(ddof=1) else None,
        "max_dd": float(((peak - eq) / peak).max()),
        "n_dates": int(len(r)), "n_indep": int(len(s)),
        "_series": r,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--horizon", type=int, default=63)
    ap.add_argument("--quantiles", type=int, default=5)
    ap.add_argument("--min-adv", type=float, default=5e6)
    args = ap.parse_args()
    h, q = args.horizon, args.quantiles

    st = lockbox.state()
    max_date = (pd.Timestamp(st["seal_from"]) - pd.Timedelta(days=1)).date().isoformat()
    lockbox.guard(max_date, "combined_book")
    print(f"data through {max_date}, horizon {h}d\n")

    panel = get_panel(max_date, [h], False)
    panel = panel[panel["adv"] >= args.min_adv]
    panel = attach_earnings(panel)

    print()
    panel["sleeve_price"] = make_sleeve(panel, PRICE_SLEEVE, "price")
    panel["sleeve_earn"] = make_sleeve(panel, EARNINGS_SLEEVE, "earnings")
    # Equal weight across sleeves. Each sleeve is already an average, so this
    # gives the two data sources equal say regardless of how many signals each
    # contributes.
    panel["combined"] = panel[["sleeve_price", "sleeve_earn"]].mean(axis=1, skipna=True)

    variants = {"price only": "sleeve_price",
                "earnings only": "sleeve_earn",
                "price + earnings": "combined"}
    out = {}
    for label, col in variants.items():
        out[label] = evaluate(panel, col, h, q)

    f = lambda v, s="{:.2f}": s.format(v) if (v is not None and pd.notna(v)) else "    -"
    print(f"\n{'=' * 78}\nFULL SAMPLE — sector-neutral quintile books, net of costs\n{'=' * 78}")
    print(f"{'book':<20}{'IC':>9}{'ret/yr':>10}{'vol':>8}{'Sharpe':>9}{'t':>7}{'maxDD':>9}{'n':>6}")
    for k, v in out.items():
        if not v:
            continue
        print(f"{k:<20}{f(v['ic'],'{:.4f}'):>9}{f(v['ret_ann'],'{:+.2%}'):>10}"
              f"{f(v['vol_ann'],'{:.1%}'):>8}{f(v['sharpe']):>9}{f(v['t']):>7}"
              f"{f(v['max_dd'],'{:.1%}'):>9}{v['n_indep']:>6}")

    # ------------------------------------------------- walk-forward by year
    print(f"\n{'=' * 78}\nWALK-FORWARD BY TEST YEAR (out-of-sample Sharpe)\n{'=' * 78}")
    years = sorted({d[:4] for d in panel["dt"].unique()})[2:]
    print(f"{'year':<8}" + "".join(f"{k:>20}" for k in variants))
    wf = {k: [] for k in variants}
    for y in years:
        sub = panel[panel["dt"].str.startswith(y)]
        if len(sub) < 2000:
            continue
        row = f"{y:<8}"
        for label, col in variants.items():
            r = book_returns(sub, col, h, q, True)
            s = r.to_numpy()[::h] if len(r) else np.array([])
            sh = (float(s.mean() / s.std(ddof=1) * np.sqrt(252 / h))
                  if len(s) > 2 and s.std(ddof=1) > 0 else None)
            wf[label].append(sh)
            row += f"{f(sh):>20}"
        print(row)
    print(f"{'mean':<8}" + "".join(
        f"{f(np.nanmean([x for x in wf[k] if x is not None])):>20}" for k in variants))
    print(f"{'% pos':<8}" + "".join(
        f"{f(np.mean([x > 0 for x in wf[k] if x is not None]),'{:.0%}'):>20}"
        for k in variants))

    # -------------------------------------------------------- the verdict
    base = out.get("price only", {}).get("sharpe")
    comb = out.get("price + earnings", {}).get("sharpe")
    print(f"\n{'=' * 78}\nBEFORE / AFTER\n{'=' * 78}")
    if base is not None and comb is not None:
        print(f"  price only        Sharpe {base:+.3f}")
        print(f"  price + earnings  Sharpe {comb:+.3f}")
        print(f"  change            {comb - base:+.3f}  ({comb/base:.2f}x)" if base
              else "")
    print(f"  target            Sharpe +1.500")
    print(f"  SPY benchmark     Sharpe +0.910")
    if comb is not None:
        print(f"\n  gap to target: {1.5 - comb:+.3f} Sharpe")
    print(f"\nTrial count is owned by the registry, not by this script. Add these")
    print(f"variants before treating any number above as a finding.")

    pd.DataFrame([{**{k: v for k, v in val.items() if k != "_series"}, "book": lab}
                  for lab, val in out.items() if val]).to_csv(
        config.ROOT / "reports" / "combined_book.csv", index=False)
    print(f"\nwrote reports/combined_book.csv")
    return 0


if __name__ == "__main__":
    sys.exit(main())
