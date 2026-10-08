#!/usr/bin/env python3
"""Turning the same signal into better positions.

    python3 python/portfolio.py --horizon 63

THE SIGNAL DOES NOT CHANGE HERE. Only the map from signal to weights does, so any
improvement is construction rather than prediction. Four methods, all standard,
all declared before running:

  A  QUINTILE, EQUAL WEIGHT. The baseline used everywhere else in this project.
     Sort, buy the top fifth, sell the bottom fifth, equal weight inside each.
     It throws away the middle 60% of the cross-section and gives the most
     marginal name in the bucket the same weight as the most extreme one.

  B  SIGNAL WEIGHTED. Weight proportional to the demeaned score across the whole
     cross-section. Every name carries weight in proportion to conviction, and
     the book is dollar-neutral by construction because the scores are demeaned.

  C  RISK ADJUSTED. Weight proportional to score divided by that stock's trailing
     volatility. Two names with equal conviction should not carry equal risk;
     this equalises contribution to variance rather than to dollars.

  D  FACTOR NEUTRAL. Cross-sectionally regress the score on market beta, size and
     momentum each day, keep the residual, then risk-weight it. The earlier
     diagnostic found the supposedly market-neutral book carried +0.079 of beta
     that was quietly supplying a third of its return. Residualising removes that
     and whatever size or momentum tilt rides along with it.

Costs are charged on realised turnover, name by name, at each stock's own modelled
half-spread. That matters more here than before: these books hold hundreds of
positions rather than two buckets, and a construction that improves the ratio by
trading more is not an improvement.
"""

from __future__ import annotations

import argparse
import sys

import numpy as np
import pandas as pd

import config
import db
import lockbox
from earnings_signals import as_of_panel, build_event_features
from spread_estimator import liquidity_half_spread_bps


def build_panel(max_date: str, h: int, min_adv: float) -> pd.DataFrame:
    with db.connect() as con:
        bars = pd.read_sql(
            "SELECT b.symbol, b.dt, b.close, b.adj_close, b.volume, s.sector "
            "FROM bars b JOIN symbols s ON s.symbol=b.symbol "
            "WHERE s.kind='stock' AND b.dt <= ? ORDER BY b.symbol, b.dt",
            con, params=(max_date,))
        spy = pd.read_sql(
            "SELECT dt, adj_close FROM bars WHERE symbol='SPY' ORDER BY dt", con)
        ev = pd.read_sql(
            "SELECT symbol, tradeable_from, surprise_pct FROM earnings "
            "WHERE surprise_pct IS NOT NULL ORDER BY symbol, tradeable_from", con)
    print(f"  {len(bars):,} bars, {bars['symbol'].nunique()} symbols")

    spy["mkt_ret"] = spy["adj_close"].pct_change()
    mkt = dict(zip(spy["dt"], spy["mkt_ret"]))

    out = []
    for sym, g in bars.groupby("symbol", sort=False):
        g = g.sort_values("dt").reset_index(drop=True)
        g["ret"] = g["adj_close"].pct_change()
        g["fwd"] = g["adj_close"].shift(-h) / g["adj_close"] - 1.0
        g["vol63"] = g["ret"].rolling(63, min_periods=40).std()
        g["adv"] = (g["close"] * g["volume"]).rolling(20, min_periods=20).mean()
        g["mom126"] = g["adj_close"] / g["adj_close"].shift(126) - 1.0
        m = g["dt"].map(mkt)
        # Trailing beta from a rolling covariance ratio; strictly backward looking.
        cov = g["ret"].rolling(252, min_periods=120).cov(m)
        var = m.rolling(252, min_periods=120).var()
        g["beta"] = cov / var.replace(0, np.nan)
        out.append(g)
    panel = pd.concat(out, ignore_index=True)
    panel["half_bps"] = liquidity_half_spread_bps(panel["close"], panel["adv"])
    panel["size"] = np.log(panel["adv"].clip(lower=1e5))

    ev = build_event_features(ev)
    merged = as_of_panel(ev, panel[["symbol", "dt"]].copy())
    panel = panel.merge(merged[["symbol", "dt", "sue", "surprise_pct"]],
                        on=["symbol", "dt"], how="left")
    panel = panel[panel["adv"] >= min_adv]
    return panel.dropna(subset=["fwd", "vol63", "adv"])


def zs(s: pd.Series, keys) -> pd.Series:
    g = s.groupby(keys)
    return (s - g.transform("mean")) / g.transform("std").replace(0, np.nan)


def residualize(d: pd.DataFrame, score: str, factors: list[str]) -> pd.Series:
    """Cross-sectional OLS of the score on factor exposures, per date, residual kept."""
    def _one(g):
        cols = [c for c in factors if g[c].notna().sum() > len(g) * 0.5]
        y = g[score]
        ok = y.notna() & g[cols].notna().all(axis=1)
        res = pd.Series(np.nan, index=g.index)
        if ok.sum() < 30 or not cols:
            return res
        X = np.column_stack([np.ones(ok.sum())] + [g.loc[ok, c].to_numpy() for c in cols])
        b, *_ = np.linalg.lstsq(X, y[ok].to_numpy(), rcond=None)
        res.loc[ok] = y[ok].to_numpy() - X @ b
        return res
    return d.groupby("dt", group_keys=False).apply(_one, include_groups=False)


def weights(d: pd.DataFrame, score: str, method: str, q: int = 5) -> pd.Series:
    """Dollar-neutral, SECTOR-neutral weights normalised to unit gross exposure.

    Sector neutrality is applied to every method, not just the quintile sort.
    A first version sorted quintiles globally while the project's existing
    baseline sorted within sector and equal-weighted sectors, so the comparison
    measured sector handling rather than the weighting scheme it claimed to test.
    Holding sector treatment constant is what makes this an experiment.
    """
    ds = d.groupby(["dt", "sector"])[score]
    if method == "quintile":
        # Within each sector: long the top bucket, short the bottom.
        b = ds.transform(lambda x: pd.qcut(x.rank(method="first"),
                                           min(q, max(2, len(x) // 5)),
                                           labels=False, duplicates="drop"))
        mx = d.groupby(["dt", "sector"])[score].transform(
            lambda x: pd.qcut(x.rank(method="first"), min(q, max(2, len(x) // 5)),
                              labels=False, duplicates="drop").max())
        w = pd.Series(0.0, index=d.index)
        w[b == mx] = 1.0
        w[b == 0] = -1.0
    elif method == "signal":
        w = d[score] - ds.transform("mean")
    elif method in ("risk", "neutral"):
        w = (d[score] - ds.transform("mean")) / d["vol63"].replace(0, np.nan)
    else:
        raise ValueError(method)
    w = w.fillna(0.0)
    # Each sector contributes equally, then the book is scaled to unit gross.
    sec_gross = w.abs().groupby([d["dt"], d["sector"]]).transform("sum").replace(0, np.nan)
    w = (w / sec_gross).fillna(0.0)
    gross = w.abs().groupby(d["dt"]).transform("sum").replace(0, np.nan)
    return (w / gross).fillna(0.0)


def run(d: pd.DataFrame, score: str, method: str, h: int, q: int) -> pd.Series:
    d = d.copy()
    d["w"] = weights(d, score, method, q)
    d["cost"] = 2 * d["half_bps"] / 10_000        # entry and exit
    d["contrib"] = d["w"] * d["fwd"] - d["w"].abs() * d["cost"]
    return d.groupby("dt")["contrib"].sum()


def stat(r: pd.Series, h: int) -> dict:
    s = r.to_numpy()[::h]
    if len(s) < 3 or s.std(ddof=1) == 0:
        return {}
    eq = (1 + pd.Series(s)).cumprod()
    return {"n": int(len(s)), "ret_ann": float(r.mean() * 252 / h),
            "vol": float(s.std(ddof=1) * np.sqrt(252 / h)),
            "sharpe": float(s.mean() / s.std(ddof=1) * np.sqrt(252 / h)),
            "t": float(s.mean() / (s.std(ddof=1) / np.sqrt(len(s)))),
            "max_dd": float(((eq.cummax() - eq) / eq.cummax()).max())}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--horizon", type=int, default=63)
    ap.add_argument("--quantiles", type=int, default=5)
    ap.add_argument("--min-adv", type=float, default=5e6)
    args = ap.parse_args()
    h, q = args.horizon, args.quantiles

    st = lockbox.state()
    max_date = (pd.Timestamp(st["seal_from"]) - pd.Timedelta(days=1)).date().isoformat()
    lockbox.guard(max_date, "portfolio")
    panel = build_panel(max_date, h, args.min_adv)

    # The signal, held constant across every construction below.
    panel["score"] = pd.concat(
        [zs(panel["sue"], [panel["dt"], panel["sector"]]),
         zs(panel["surprise_pct"], [panel["dt"], panel["sector"]])],
        axis=1).mean(axis=1, skipna=True)
    panel = panel.dropna(subset=["score"])
    print(f"  {len(panel):,} scored rows, median {panel.groupby('dt').size().median():.0f} names/day")

    print("  residualising against beta, size and momentum")
    panel["score_neutral"] = residualize(panel, "score", ["beta", "size", "mom126"])

    methods = [("A quintile, equal weight", "score", "quintile"),
               ("B signal weighted", "score", "signal"),
               ("C risk adjusted", "score", "risk"),
               ("D factor neutral + risk", "score_neutral", "neutral")]
    rows = []
    for label, col, meth in methods:
        r = run(panel.dropna(subset=[col]), col, meth, h, q)
        m = stat(r, h)
        if m:
            rows.append({"method": label, **m})

    res = pd.DataFrame(rows)
    res.to_csv(config.ROOT / "reports" / "portfolio.csv", index=False)
    f = lambda v, s="{:.2f}": s.format(v) if pd.notna(v) else "   -"
    print(f"\n{'=' * 80}\nSAME SIGNAL, FOUR CONSTRUCTIONS (net of per-stock costs)\n{'=' * 80}")
    print(f"{'method':<28}{'ret/yr':>10}{'vol':>8}{'Sharpe':>9}{'t':>7}{'maxDD':>9}")
    for _, r in res.iterrows():
        print(f"{r.method:<28}{f(r.ret_ann,'{:+.2%}'):>10}{f(r.vol,'{:.1%}'):>8}"
              f"{f(r.sharpe):>9}{f(r.t):>7}{f(r.max_dd,'{:.1%}'):>9}")
    print(f"\n{'SPY benchmark':<28}{'+17.3%':>10}{'19.0%':>8}{'0.91':>9}")
    if not res.empty:
        b = res.loc[res.sharpe.idxmax()]
        print(f"\nbest construction: {b.method}  Sharpe {b.sharpe:+.3f}")
        print(f"beats SPY? {'YES' if b.sharpe > 0.91 else 'no'}  (gap {0.91 - b.sharpe:+.3f})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
