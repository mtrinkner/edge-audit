#!/usr/bin/env python3
"""Does post-earnings drift survive down-cap, once costs are charged honestly?

    python3 python/cap_spectrum.py --horizon 63

THE SETUP. Reg SHO gives the cleanest causal evidence that anomalies are larger
where arbitrage is costly: lifting short-sale constraints cut long-short anomaly
returns by 94bp for small stocks against 48bp for large, and the effect ran
through the short leg. PEAD in particular is documented as stronger in smaller,
less-followed firms with thinner analyst coverage.

THE CATCH, WHICH IS THE WHOLE POINT OF THIS SCRIPT. Those same stocks cost
several times more to trade. Li, Sullivan and Garcia-Feijoo show the
low-volatility premium largely survives only until realistic costs are applied,
precisely because the abnormal returns sit in illiquid names. Running a down-cap
test on a flat large-cap cost assumption would produce a spurious edge made
entirely of an accounting choice.

So every trade here is charged its own stock's modelled half-spread, which runs
2.1bp for the S&P 500, 4.4bp for the 400 and 7.6bp for the 600. Gross and net are
both reported so it is visible how much of any down-cap gain is real and how much
is eaten.
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

BUCKETS = {"S&P 500": "large", "midcap400": "mid", "smallcap600": "small"}


def build(max_date: str, h: int) -> pd.DataFrame:
    with db.connect() as con:
        bars = pd.read_sql(
            "SELECT b.symbol, b.dt, b.close, b.adj_close, b.volume, s.sector, "
            "s.inclusion_reason FROM bars b JOIN symbols s ON s.symbol=b.symbol "
            "WHERE s.kind='stock' AND b.dt <= ? ORDER BY b.symbol, b.dt",
            con, params=(max_date,))
        ev = pd.read_sql(
            "SELECT symbol, tradeable_from, surprise_pct FROM earnings "
            "WHERE surprise_pct IS NOT NULL ORDER BY symbol, tradeable_from", con)
    print(f"  {len(bars):,} bars, {bars['symbol'].nunique()} symbols")

    parts = []
    for sym, g in bars.groupby("symbol", sort=False):
        g = g.sort_values("dt").reset_index(drop=True)
        g["fwd"] = g["adj_close"].shift(-h) / g["adj_close"] - 1.0
        g["adv"] = (g["close"] * g["volume"]).rolling(20, min_periods=20).mean()
        parts.append(g)
    panel = pd.concat(parts, ignore_index=True)

    panel["half_bps"] = liquidity_half_spread_bps(panel["close"], panel["adv"])
    panel["bucket"] = "other"
    for key, label in BUCKETS.items():
        panel.loc[panel["inclusion_reason"].str.contains(key, na=False), "bucket"] = label

    ev = build_event_features(ev)
    merged = as_of_panel(ev, panel[["symbol", "dt"]].copy())
    panel = panel.merge(merged[["symbol", "dt", "sue", "surprise_pct"]],
                        on=["symbol", "dt"], how="left")
    return panel.dropna(subset=["fwd", "adv"])


def zs(s, keys):
    g = s.groupby(keys)
    return (s - g.transform("mean")) / g.transform("std").replace(0, np.nan)


def book(panel: pd.DataFrame, col: str, h: int, q: int, net: bool) -> pd.Series:
    """Sector-neutral quintile book. When net, each leg pays its own stocks'
    half-spread on entry and exit rather than one flat universe-wide number."""
    d = panel[["dt", "sector", "symbol", col, "fwd", "half_bps"]].dropna()
    d = d[d.groupby(["dt", "sector"])[col].transform("size") >= 10].copy()
    if d.empty:
        return pd.Series(dtype=float)
    d["b"] = d.groupby(["dt", "sector"])[col].transform(
        lambda x: pd.qcut(x.rank(method="first"), min(q, max(2, len(x)//5)),
                          labels=False, duplicates="drop"))
    mx = d.groupby(["dt", "sector"])["b"].transform("max")
    # COST ACCOUNTING. A long/short book PAYS on both legs, so the two cost
    # terms add. An earlier version subtracted the cost from each leg and then
    # differenced them, which cancelled the costs and reported a drag of 0.00%
    # on a book trading small caps at 7.7bp. The book return is
    #     (fwd_long - fwd_short) - cost_long - cost_short
    # and the costs are accumulated separately from the returns to keep that
    # explicit rather than implied by sign conventions.
    d["cost"] = 2 * d["half_bps"] / 10_000       # two sides: entry and exit
    long_leg = d[d.b == mx].groupby(["dt", "sector"]).agg(
        r=("fwd", "mean"), c=("cost", "mean"))
    short_leg = d[d.b == 0].groupby(["dt", "sector"]).agg(
        r=("fwd", "mean"), c=("cost", "mean"))
    gross = long_leg["r"] - short_leg["r"]
    total_cost = long_leg["c"] + short_leg["c"]
    per = (gross if not net else gross - total_cost).reset_index(name="r")
    return per.groupby("dt")["r"].mean()


def stat(r: pd.Series, h: int) -> dict:
    if len(r) < 3 * h:
        return {}
    s = r.to_numpy()[::h]
    if len(s) < 3 or s.std(ddof=1) == 0:
        return {}
    return {"n": int(len(s)), "ret_ann": float(r.mean() * 252 / h),
            "sharpe": float(s.mean() / s.std(ddof=1) * np.sqrt(252 / h)),
            "t": float(s.mean() / (s.std(ddof=1) / np.sqrt(len(s))))}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--horizon", type=int, default=63)
    ap.add_argument("--quantiles", type=int, default=5)
    ap.add_argument("--min-adv", type=float, default=1e6)
    args = ap.parse_args()
    h, q = args.horizon, args.quantiles

    st = lockbox.state()
    max_date = (pd.Timestamp(st["seal_from"]) - pd.Timedelta(days=1)).date().isoformat()
    lockbox.guard(max_date, "cap_spectrum")
    panel = build(max_date, h)
    panel = panel[panel["adv"] >= args.min_adv]

    rows = []
    for bucket in ("large", "mid", "small", "all"):
        p = panel if bucket == "all" else panel[panel["bucket"] == bucket]
        if p.empty:
            continue
        p = p.copy()
        p["s"] = pd.concat(
            [zs(p["sue"], [p["dt"], p["sector"]]),
             zs(p["surprise_pct"], [p["dt"], p["sector"]])], axis=1).mean(axis=1, skipna=True)
        g = stat(book(p, "s", h, q, net=False), h)
        n = stat(book(p, "s", h, q, net=True), h)
        if not g or not n:
            continue
        rows.append({"bucket": bucket, "names": p["symbol"].nunique(),
                     "med_half_bps": float(p["half_bps"].median()),
                     "gross_ret": g["ret_ann"], "gross_sharpe": g["sharpe"],
                     "net_ret": n["ret_ann"], "net_sharpe": n["sharpe"],
                     "net_t": n["t"], "n_obs": n["n"]})

    res = pd.DataFrame(rows)
    res.to_csv(config.ROOT / "reports" / "cap_spectrum.csv", index=False)
    print(f"\n{'=' * 92}")
    print("POST-EARNINGS DRIFT BY MARKET-CAP BUCKET (sector-neutral quintiles)")
    print("=" * 92)
    print(f"{'bucket':<9}{'names':>7}{'half bps':>10}{'GROSS ret':>11}{'GROSS Sh':>10}"
          f"{'NET ret':>10}{'NET Sh':>9}{'t':>7}{'cost drag':>11}")
    for _, r in res.iterrows():
        print(f"{r.bucket:<9}{int(r.names):>7}{r.med_half_bps:>10.1f}"
              f"{r.gross_ret:>+10.2%}{r.gross_sharpe:>10.2f}"
              f"{r.net_ret:>+9.2%}{r.net_sharpe:>9.2f}{r.net_t:>7.2f}"
              f"{r.gross_ret - r.net_ret:>+10.2%}")
    print("\nThe question is whether GROSS rises down-cap (the anomaly is bigger")
    print("where arbitrage is costly) and whether NET survives the cost increase.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
