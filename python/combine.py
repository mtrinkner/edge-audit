#!/usr/bin/env python3
"""Does combining weak signals rescue them? (trial 37)

    python3 python/combine.py

THE GAP THIS CLOSES

This project tested signals one at a time, or in naive equal-weight sleeves.
Real funds do neither: they combine many weak, loosely-correlated signals, and
the Fundamental Law says K uncorrelated signals of equal IC combine to roughly
IC x sqrt(K). That is how 2bp of information becomes a tradeable book, and it is
the one place our method differed from a professional one in a way that favoured
the professionals.

THE TEST, declared before running

The combined IC must exceed the BEST INDIVIDUAL IC by about sqrt(K_effective),
where K_effective is the participation ratio of the signal correlation matrix:

    K_eff = (sum of eigenvalues)^2 / sum of (eigenvalues squared)

Raw signal count is the wrong denominator. Thirteen price signals computed from
one price series are not thirteen independent opinions, and this project already
measured that elsewhere: thirteen technical signals were 2.9 effective.

WEIGHTS. Equal, plus expanding-window inverse-volatility. The inverse-vol variant
uses only each signal's PAST VOLATILITY, never its past mean, so a signal cannot
earn weight by having been profitable. No optimised weights: fitting weights on
the sample you evaluate on is the purest form of the overfitting this project
exists to prevent.

EVERY INPUT CAUSAL. Earnings on tradeable_from, fundamentals on SEC filed date,
volume baselines trailing and excluding the current bar.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "python"))

import db

HOLD = 21
MIN_NAMES = 50


def zs(s: pd.Series, keys) -> pd.Series:
    g = s.groupby(keys)
    return (s - g.transform("mean")) / g.transform("std").replace(0, np.nan)


def build() -> tuple[pd.DataFrame, list[str]]:
    with db.connect() as con:
        d = pd.read_sql(
            "SELECT b.symbol, b.dt, b.adj_close, b.close, b.volume, s.sector "
            "FROM bars b JOIN symbols s ON s.symbol=b.symbol "
            "WHERE s.kind='stock' ORDER BY b.symbol, b.dt", con)
        spy = pd.read_sql("SELECT dt, adj_close AS spy FROM bars WHERE symbol='SPY'", con)
        ev = pd.read_sql("SELECT symbol, tradeable_from AS dt, surprise_pct "
                         "FROM earnings WHERE surprise_pct IS NOT NULL", con)
        si = pd.read_sql("SELECT symbol, tradeable_from, short_shares, prev_short, "
                         "avg_daily_vol FROM short_interest", con)
    d = d.merge(spy, on="dt", how="left").sort_values(["symbol", "dt"]).reset_index(drop=True)
    g = d.groupby("symbol")
    d["fwd"] = g["adj_close"].shift(-HOLD) / d["adj_close"] - 1.0
    d["abn"] = d["fwd"] - (d["spy"].shift(-HOLD) / d["spy"] - 1.0)
    d["dollar"] = d["close"] * d["volume"]
    d["adv"] = g["dollar"].transform(lambda s: s.shift(1).rolling(20, min_periods=15).mean())

    # --- the weak signals, all causal, all previously tested and found small ---
    px = g["adj_close"]
    d["mom126"] = px.pct_change(126)
    d["mom252"] = px.pct_change(252)
    d["rev5"] = -px.pct_change(5)
    d["rev21"] = -px.pct_change(21)
    r = g["adj_close"].pct_change()
    d["lowvol"] = -r.groupby(d["symbol"]).transform(
        lambda s: s.shift(1).rolling(63, min_periods=40).std())
    d["near52w"] = d["adj_close"] / g["adj_close"].transform(
        lambda s: s.shift(1).rolling(252, min_periods=200).max()) - 1.0
    d["volshock"] = d["volume"] / g["volume"].transform(
        lambda s: s.shift(1).rolling(20, min_periods=15).mean())
    d["illiq"] = -np.log(d["adv"].clip(lower=1e5))

    # earnings surprise, as-of the first tradeable session, carried 60 sessions
    e = ev.sort_values(["symbol", "dt"])
    d = d.merge(e, on=["symbol", "dt"], how="left")
    # `g` was bound before this merge and does not know the new column, so the
    # groupby has to be rebuilt. A stale groupby silently refers to an older
    # frame; here it raised, which is the good case.
    d["surprise"] = d.groupby("symbol")["surprise_pct"].ffill(limit=60)

    # short interest, as-of its publication date
    si = si.dropna(subset=["tradeable_from"]).sort_values("tradeable_from")
    si["_t"] = pd.to_datetime(si["tradeable_from"])
    si["si_ratio"] = si["short_shares"] / si["avg_daily_vol"].replace(0, np.nan)
    si["si_chg"] = (si["short_shares"] - si["prev_short"]) / si["prev_short"].replace(0, np.nan)
    q = d[["symbol", "dt"]].copy()
    q["_d"] = pd.to_datetime(q["dt"])
    m = pd.merge_asof(q.sort_values("_d"), si[["symbol", "_t", "si_ratio", "si_chg"]],
                      left_on="_d", right_on="_t", by="symbol", direction="backward")
    d["si_ratio"] = -m["si_ratio"].to_numpy()      # high short interest = bearish
    d["si_chg"] = -m["si_chg"].to_numpy()

    sigs = ["mom126", "mom252", "rev5", "rev21", "lowvol", "near52w",
            "volshock", "illiq", "surprise", "si_ratio", "si_chg"]
    for s in sigs:
        d[f"z_{s}"] = zs(d[s], [d["dt"], d["sector"]])
    return d, [f"z_{s}" for s in sigs]


def ic_of(d: pd.DataFrame, col: str) -> tuple[float, float, int]:
    x = d.dropna(subset=[col, "abn"])
    x = x[x.groupby("dt")[col].transform("size") >= MIN_NAMES]
    if len(x) < 5000:
        return np.nan, np.nan, 0
    ic = x.groupby("dt").apply(lambda g: g[col].rank().corr(g["abn"].rank()),
                               include_groups=False)
    ic = ic[np.isfinite(ic)]
    n = ic.to_numpy()[::HOLD]
    t = (n.mean() / (n.std(ddof=1) / np.sqrt(len(n)))
         if len(n) > 2 and n.std(ddof=1) > 0 else np.nan)
    return float(ic.mean()), float(t), len(n)


def main() -> int:
    print("building signals...")
    d, cols = build()
    print(f"  {len(d):,} rows, {len(cols)} signals\n")

    print("=" * 84)
    print("STEP 1  each signal on its own")
    print("=" * 84)
    print(f"{'signal':<16}{'IC':>10}{'t':>8}{'indep':>8}")
    res = {}
    for c in cols:
        ic, t, n = ic_of(d, c)
        res[c] = ic
        print(f"{c[2:]:<16}{ic:>10.4f}{t:>8.2f}{n:>8}")
    best = max((v for v in res.values() if np.isfinite(v)), key=abs)
    bestname = [k for k, v in res.items() if v == best][0]
    print(f"\n  best individual: {bestname[2:]} at IC {best:+.4f}")

    print(f"\n{'=' * 84}")
    print("STEP 2  how independent are they really?")
    print("=" * 84)
    sub = d[cols].dropna()
    C = np.corrcoef(sub.to_numpy(), rowvar=False)
    lam = np.linalg.eigvalsh(C)
    lam = lam[lam > 1e-10]
    keff = float(lam.sum() ** 2 / (lam ** 2).sum())
    print(f"  {len(cols)} signals, {len(sub):,} complete rows")
    print(f"  mean |pairwise correlation|: "
          f"{np.abs(C[np.triu_indices_from(C, 1)]).mean():.3f}")
    print(f"  K_effective (participation ratio): {keff:.2f} of {len(cols)}")
    print(f"  so the diversification factor available is sqrt({keff:.2f}) = {np.sqrt(keff):.2f}x")

    print(f"\n{'=' * 84}")
    print("STEP 3  the combined signal")
    print("=" * 84)
    d["combo_eq"] = d[cols].mean(axis=1, skipna=True)
    # expanding inverse-vol: uses only each signal's PAST volatility, never its mean
    piv = d.groupby("dt")[cols].mean()
    iv = 1.0 / piv.expanding(min_periods=252).std().shift(1)
    w = iv.div(iv.sum(axis=1), axis=0)
    wmap = w.reindex(d["dt"].to_numpy()).to_numpy()
    with np.errstate(invalid="ignore"):
        d["combo_iv"] = np.nansum(d[cols].to_numpy() * wmap, axis=1)
    d.loc[~np.isfinite(d["combo_iv"]), "combo_iv"] = np.nan

    print(f"{'book':<28}{'IC':>10}{'t':>8}{'indep':>8}{'vs best single':>16}")
    for lab, c in (("best single signal", bestname),
                   ("combined, equal weight", "combo_eq"),
                   ("combined, inverse-vol", "combo_iv")):
        ic, t, n = ic_of(d, c)
        ratio = ic / best if np.isfinite(ic) and best else np.nan
        print(f"{lab:<28}{ic:>10.4f}{t:>8.2f}{n:>8}{ratio:>15.2f}x")

    ic_eq, t_eq, _ = ic_of(d, "combo_eq")
    print(f"\n  PREDICTED gain from diversification: {np.sqrt(keff):.2f}x")
    print(f"  ACTUAL gain (equal weight / best single): {ic_eq/best:.2f}x")
    verdict = ("combination DOES help, roughly as the law predicts"
               if ic_eq / best > 0.8 * np.sqrt(keff)
               else "combination does NOT deliver the predicted gain")
    print(f"  -> {verdict}")
    (ROOT / "reports").mkdir(exist_ok=True)
    pd.DataFrame([{"signal": k[2:], "ic": v} for k, v in res.items()]
                 ).to_csv(ROOT / "reports" / "combine.csv", index=False)
    print(f"\nwrote reports/combine.csv")
    return 0


if __name__ == "__main__":
    sys.exit(main())
