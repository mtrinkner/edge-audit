#!/usr/bin/env python3
"""Three-hypothesis pilot, run through the corrected pipeline. (trials 34-36)

    python3 python/pilot.py

Three economic mechanisms, not three parameter settings:

  H1  LIMITED ATTENTION      earnings diffuse slower where nobody is looking
  H2  INFORMATION AGE        stale fundamentals carry an uncertainty premium
  H3  LIQUIDITY PROVISION    forced selling, at a horizon costs permit

All registered with falsification criteria BEFORE this ran.

LEVELS, per the research framework
  1  predictive information   rank IC, quantile monotonicity, non-overlapping t
  2  risk-adjusted alpha      residualised on beta, size, 126d momentum, sector
  3  economic feasibility     scored against the four measured cost scenarios

EVERY INPUT IS CAUSAL. Volume baselines are trailing and exclude the current
bar, because the intraday version of H3 was destroyed by a baseline that
included future bars. Earnings use tradeable_from, never the announcement
timestamp. Fundamentals use the SEC filed date.

UNIVERSE A, which is MEASURED to be missing 37% of the names that passed through
the index in this window, asymmetrically: the recoverable missing names were
dropped for weak performance, the unrecoverable ones were acquired at a premium.
So these are Level 1-2 predictive tests. No feasibility claim is made from them,
and H3 in particular buys recent losers, which is the construction most exposed
to that bias.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "python"))

import config
import data_access as da

HOLD = 21
MIN_NAMES = 50


def load() -> pd.DataFrame:
    # Through data_access, which stops at the lockbox seal (added 2026-10-26).
    d = da.sql("pilot", "SELECT b.symbol, b.dt, b.adj_close, b.close, b.volume, s.sector "
               "FROM bars b JOIN symbols s ON s.symbol=b.symbol WHERE s.kind='stock'",
               order_by="symbol, dt")
    spy = da.sql("pilot", "SELECT dt, adj_close AS spy FROM bars WHERE symbol='SPY'",
                 order_by="dt")
    ev = da.sql("pilot", "SELECT symbol, tradeable_from AS dt, surprise_pct FROM earnings "
                "WHERE surprise_pct IS NOT NULL")
    fu = da.sql("pilot", "SELECT DISTINCT symbol, filed FROM fundamentals",
                date_col="filed", order_by="symbol, filed")
    d = d.merge(spy, on="dt", how="left").sort_values(["symbol", "dt"]).reset_index(drop=True)
    g = d.groupby("symbol")
    d["ret"] = g["adj_close"].pct_change()
    d["mkt"] = d["spy"].pct_change()
    d["fwd"] = g["adj_close"].shift(-HOLD) / d["adj_close"] - 1.0
    d["mfwd"] = d["spy"].shift(-HOLD) / d["spy"] - 1.0
    d["abn"] = d["fwd"] - d["mfwd"]                 # market-adjusted forward return
    d["dollar"] = d["close"] * d["volume"]
    # Strictly trailing, current bar EXCLUDED.
    d["vol60"] = g["volume"].transform(lambda s: s.shift(1).rolling(60, min_periods=40).mean())
    d["vol20"] = g["volume"].transform(lambda s: s.shift(1).rolling(20, min_periods=15).mean())
    d["adv"] = g["dollar"].transform(lambda s: s.shift(1).rolling(20, min_periods=15).mean())
    d["ret5"] = g["adj_close"].pct_change(5)
    d["mom126"] = g["adj_close"].pct_change(126)
    d["vol63"] = g["ret"].transform(lambda s: s.shift(1).rolling(63, min_periods=40).std())
    cov = g["ret"].transform(lambda s: s.shift(1).rolling(252, min_periods=120).cov(d["mkt"]))
    var = d["mkt"].rolling(252, min_periods=120).var()
    d["beta"] = cov / var.replace(0, np.nan)
    d["size"] = np.log(d["adv"].clip(lower=1e5))
    return d, ev, fu


def resid(d: pd.DataFrame, col: str) -> pd.Series:
    """Daily cross-sectional OLS on beta, size, 126d momentum + sector dummies."""
    F = ["beta", "size", "mom126"]
    sec = pd.get_dummies(d["sector"], drop_first=True, dtype=float)
    X = np.column_stack([np.ones(len(d)), d[F].to_numpy(float), sec.to_numpy(float)])
    y = d[col].to_numpy(float)
    out = np.full(len(d), np.nan)
    for _, idx in d.groupby("dt", sort=False).indices.items():
        ok = idx[np.isfinite(X[idx]).all(1) & np.isfinite(y[idx])]
        if len(ok) < 40:
            continue
        A = X[ok]
        keep = A.std(0) > 0
        keep[0] = True
        A = A[:, keep]
        b, *_ = np.linalg.lstsq(A, y[ok], rcond=None)
        out[ok] = y[ok] - A @ b
    return pd.Series(out, index=d.index)


def level12(d: pd.DataFrame, sig: str, label: str) -> dict:
    """Rank IC and quantile monotonicity, raw and factor-residualised."""
    x = d.dropna(subset=[sig, "abn"]).copy()
    x = x[x.groupby("dt")[sig].transform("size") >= MIN_NAMES]
    if len(x) < 5000:
        return {"hypothesis": label, "n": len(x), "note": "too few observations"}
    out = {"hypothesis": label, "n": len(x), "dates": x["dt"].nunique()}
    for tag, col in (("raw", sig), ("resid", "_r")):
        if tag == "resid":
            x["_r"] = resid(x, sig)
            if x["_r"].notna().sum() < 5000:
                continue
        ic = x.dropna(subset=[col]).groupby("dt").apply(
            lambda g: g[col].rank().corr(g["abn"].rank()), include_groups=False)
        ic = ic[np.isfinite(ic)]
        nono = ic.to_numpy()[::HOLD]
        t = (nono.mean() / (nono.std(ddof=1) / np.sqrt(len(nono)))
             if len(nono) > 2 and nono.std(ddof=1) > 0 else np.nan)
        q = x.dropna(subset=[col]).copy()
        q["q"] = q.groupby("dt")[col].transform(
            lambda s: pd.qcut(s.rank(method="first"), 5, labels=False, duplicates="drop"))
        qm = q.groupby("q")["abn"].mean()
        mono = bool(qm.is_monotonic_increasing or qm.is_monotonic_decreasing)
        out[f"ic_{tag}"] = float(ic.mean())
        out[f"t_{tag}"] = float(t)
        out[f"indep_{tag}"] = int(len(nono))
        out[f"spread_{tag}"] = float(qm.iloc[-1] - qm.iloc[0])
        out[f"mono_{tag}"] = mono
        out[f"q_{tag}"] = [round(v * 100, 3) for v in qm.tolist()]
    return out


def main() -> int:
    print("loading...")
    d, ev, fu = load()
    print(f"  {len(d):,} bars, {d['symbol'].nunique()} symbols\n")
    rows = []

    # ---------- H1 limited attention ----------
    e = ev.merge(d[["symbol", "dt", "volume", "vol60", "abn", "beta", "size",
                    "mom126", "sector"]], on=["symbol", "dt"], how="inner")
    e["attention"] = e["volume"] / e["vol60"].replace(0, np.nan)
    e = e.dropna(subset=["attention", "surprise_pct", "abn"])
    # The hypothesis is the INTERACTION: low attention, same surprise, more drift.
    e["surp_q"] = e.groupby("dt")["surprise_pct"].transform(
        lambda s: pd.qcut(s.rank(method="first"), 3, labels=False, duplicates="drop"))
    e["att_q"] = e.groupby("dt")["attention"].transform(
        lambda s: pd.qcut(s.rank(method="first"), 3, labels=False, duplicates="drop"))
    print("H1  LIMITED ATTENTION — drift by attention, holding surprise fixed")
    print(f"    {len(e):,} announcements")
    print(f"    {'surprise':<12}{'low att':>11}{'mid att':>11}{'high att':>11}{'low-high':>11}")
    h1rows = []
    for sq in sorted(e["surp_q"].dropna().unique()):
        g = e[e["surp_q"] == sq]
        m = [g[g["att_q"] == a]["abn"].mean() for a in (0, 1, 2)]
        print(f"    {['low','mid','high'][int(sq)]:<12}"
              + "".join(f"{v:>+10.3%}" for v in m) + f"{m[0]-m[2]:>+10.3%}")
        h1rows.append({"surprise_tercile": int(sq), "low_att": m[0],
                       "mid_att": m[1], "high_att": m[2], "low_minus_high": m[0]-m[2]})
    # the declared signal: -attention, so that "low attention" is a high score
    e["h1"] = -e["attention"]
    sub = d.merge(e[["symbol", "dt", "h1"]], on=["symbol", "dt"], how="left")
    rows.append(level12(sub, "h1", "H1 limited attention"))

    # ---------- H2 information age ----------
    fu2 = fu.copy()
    fu2["filed"] = fu2["filed"].astype(str)
    fu2 = fu2.sort_values(["symbol", "filed"])
    k = d[["symbol", "dt"]].copy()
    k["_d"] = pd.to_datetime(k["dt"])
    f2 = fu2.rename(columns={"filed": "_d"})
    f2["_d"] = pd.to_datetime(f2["_d"])
    f2["last_filed"] = f2["_d"]
    m = pd.merge_asof(k.sort_values("_d"), f2[["symbol", "_d", "last_filed"]].sort_values("_d"),
                      on="_d", by="symbol", direction="backward", allow_exact_matches=True)
    m["age_days"] = (m["_d"] - m["last_filed"]).dt.days
    d2 = d.merge(m[["symbol", "dt", "age_days"]], on=["symbol", "dt"], how="left")
    d2["month"] = d2["dt"].str[5:7]
    # Control for calendar month, the confound named in the registration.
    d2["h2"] = d2.groupby(["dt"])["age_days"].transform(lambda s: s)
    print(f"\nH2  INFORMATION AGE — sessions since the last SEC filing")
    print(f"    {d2['age_days'].notna().sum():,} obs, median age "
          f"{d2['age_days'].median():.0f} days")
    rows.append(level12(d2, "h2", "H2 information age"))

    # ---------- H3 liquidity provision at 21 days ----------
    d3 = d.copy()
    d3["volr"] = d3["volume"] / d3["vol20"].replace(0, np.nan)
    d3["dec"] = d3.groupby("dt")["ret5"].transform(
        lambda s: pd.qcut(s.rank(method="first"), 10, labels=False, duplicates="drop"))
    los = d3[d3["dec"] == 0].dropna(subset=["volr", "abn"])
    hi = los[los["volr"] > 2]
    lo = los[los["volr"] <= 2]
    print(f"\nH3  LIQUIDITY PROVISION — biggest 5-day losers, split by volume")
    print(f"    forced-selling signature (vol >2x): {len(hi):,} obs, "
          f"mean 21d abnormal {hi['abn'].mean():+.3%}")
    print(f"    no signature       (vol <=2x): {len(lo):,} obs, "
          f"mean 21d abnormal {lo['abn'].mean():+.3%}")
    print(f"    SEPARATION (the test): {hi['abn'].mean()-lo['abn'].mean():+.3%}")
    d3["h3"] = np.where((d3["dec"] == 0) & (d3["volr"] > 2), 1.0,
                        np.where(d3["dec"] == 0, 0.0, np.nan))
    rows.append(level12(d3, "h3", "H3 liquidity provision"))

    r = pd.DataFrame(rows)
    (ROOT / "reports").mkdir(exist_ok=True)
    r.to_csv(ROOT / "reports" / "pilot.csv", index=False)
    print(f"\n{'=' * 92}")
    print("LEVEL 1-2 SUMMARY  (IC vs market-adjusted 21d forward return)")
    print("=" * 92)
    print(f"{'hypothesis':<26}{'n':>10}{'IC raw':>9}{'t':>7}{'IC resid':>10}{'t':>7}"
          f"{'indep':>7}{'monotone':>10}")
    for _, x in r.iterrows():
        if "ic_raw" not in x or pd.isna(x.get("ic_raw")):
            print(f"{x['hypothesis']:<26}{x['n']:>10,}   {x.get('note','insufficient')}")
            continue
        print(f"{x['hypothesis']:<26}{x['n']:>10,}{x['ic_raw']:>9.4f}{x['t_raw']:>7.2f}"
              f"{x.get('ic_resid', float('nan')):>10.4f}{x.get('t_resid', float('nan')):>7.2f}"
              f"{x.get('indep_resid', 0):>7}{str(x.get('mono_resid')):>10}")
    print(f"\nwrote reports/pilot.csv")
    return 0


if __name__ == "__main__":
    sys.exit(main())
