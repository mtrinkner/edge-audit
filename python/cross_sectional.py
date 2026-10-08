#!/usr/bin/env python3
"""Cross-sectional research the way a quant firm actually does it.

    python3 python/cross_sectional.py
    python3 python/cross_sectional.py --horizons 5 21 --quantiles 10

WHAT CHANGES FROM THE REST OF THIS PROJECT, AND WHY

1. THE QUESTION. Not "will AAPL go up" but "will AAPL beat the average stock
   today". Directional forecasting on large caps is mostly forecasting the market,
   which is why this project's first model leaned on market-wide features. Ranking
   names against each other on the same day removes the market move entirely.

2. THE PORTFOLIO. Long the top decile, short the bottom, equal weight. That is
   market neutral by construction, so the index drift that made every earlier
   result look better than it was simply cancels.

3. THE METRIC. Information Coefficient, not Sharpe. IC is the rank correlation,
   each day, between what the signal predicted and what actually happened. Real
   quant shops run on IC between 0.02 and 0.10, meaning they are right about 51
   to 55 percent of the time. A signal with IC 0.03 is a genuine finding and
   would be invisible to anyone screening for high Sharpe.

4. BREADTH IS THE POINT. The fundamental law says IR is roughly IC times the
   square root of the number of independent bets. You do not get paid for a
   better signal nearly as much as for the same signal applied across more names,
   so the universe matters more than the cleverness.

Spearman rank correlation is used rather than Pearson because the decision is a
ranking, outliers should not dominate it, and a single meme stock should not
decide the day's IC.
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

ROUND_TRIP = 2 * (config.COSTS.slippage_bps + config.COSTS.spread_bps) / 10_000

# Continuous scores, not on/off rules. A cross-sectional ranking needs something
# to sort by; a boolean only splits the universe in two.
def score_momentum(g, n): return g["adj_close"] / g["adj_close"].shift(n) - 1.0
def score_reversal(g, n): return -(g["adj_close"] / g["adj_close"].shift(n) - 1.0)
def score_dist_sma(g, n): return g["adj_close"] / sg.sma(g["adj_close"], n) - 1.0
def score_rsi(g, n): return sg.rsi(g["adj_close"], n)
def score_vol(g, n): return -g["adj_close"].pct_change().rolling(n, min_periods=n).std()
def score_relvol(g, n): return g["volume"] / g["volume"].rolling(n, min_periods=n).mean()
def score_range(g, n): return -(sg.atr(g, n) / g["close"])
def score_52w(g, n): return g["adj_close"] / g["high"].rolling(n, min_periods=n).max() - 1.0

SCORES = {
    "mom_21d":        (score_momentum, 21),
    "mom_63d":        (score_momentum, 63),
    "mom_126d":       (score_momentum, 126),
    "mom_252d_skip":  (score_momentum, 252),
    "reversal_5d":    (score_reversal, 5),
    "reversal_21d":   (score_reversal, 21),
    "dist_sma_50":    (score_dist_sma, 50),
    "dist_sma_200":   (score_dist_sma, 200),
    "rsi_14":         (score_rsi, 14),
    "low_vol_63d":    (score_vol, 63),
    "rel_volume_20":  (score_relvol, 20),
    "low_atr_14":     (score_range, 14),
    "near_52w_high":  (score_52w, 252),
}


def build_panel(max_date: str, horizons: list[int]) -> pd.DataFrame:
    with db.connect() as con:
        bars = pd.read_sql(
            "SELECT b.symbol, b.dt, b.open, b.high, b.low, b.close, b.adj_close, "
            "b.volume, s.sector FROM bars b JOIN symbols s ON s.symbol=b.symbol "
            "WHERE s.kind='stock' AND b.dt <= ? ORDER BY b.symbol, b.dt",
            con, params=(max_date,))
    out = []
    for sym, g in bars.groupby("symbol", sort=False):
        g = g.sort_values("dt").reset_index(drop=True)
        row = {"symbol": g["symbol"], "dt": g["dt"], "sector": g["sector"],
               "adv": (g["close"] * g["volume"]).rolling(20, min_periods=20).mean()}
        for h in horizons:
            row[f"fwd_{h}"] = g["adj_close"].shift(-h) / g["adj_close"] - 1.0
        for name, (fn, n) in SCORES.items():
            row[name] = fn(g, n)
        out.append(pd.DataFrame(row))
    return pd.concat(out, ignore_index=True)


def zscore_by_date(s: pd.Series, by: pd.Series) -> pd.Series:
    """Standardize within each date. This is what makes scores combinable."""
    grp = s.groupby(by)
    return (s - grp.transform("mean")) / grp.transform("std").replace(0, np.nan)


def daily_ic(panel: pd.DataFrame, score: str, h: int, min_names: int = 50) -> pd.Series:
    """Spearman rank IC per date between the score and the forward return."""
    col = f"fwd_{h}"
    d = panel[[score, col, "dt"]].dropna()
    counts = d.groupby("dt")[score].transform("size")
    d = d[counts >= min_names]
    if d.empty:
        return pd.Series(dtype=float)
    return d.groupby("dt").apply(
        lambda x: x[score].rank().corr(x[col].rank()), include_groups=False)


def quantile_spread(panel: pd.DataFrame, score: str, h: int, q: int = 10,
                    min_names: int = 50) -> pd.Series:
    """Return of (top quantile minus bottom quantile), equal weight, net of costs.

    Market neutral by construction: whatever the market did that day affects both
    legs and cancels. Costs are charged on both legs since both are traded.
    """
    col = f"fwd_{h}"
    d = panel[["dt", score, col]].dropna()
    counts = d.groupby("dt")[score].transform("size")
    d = d[counts >= min_names].copy()
    if d.empty:
        return pd.Series(dtype=float)
    d["bucket"] = d.groupby("dt")[score].transform(
        lambda x: pd.qcut(x.rank(method="first"), q, labels=False, duplicates="drop"))
    top = d[d["bucket"] == q - 1].groupby("dt")[col].mean()
    bot = d[d["bucket"] == 0].groupby("dt")[col].mean()
    return (top - bot) - 2 * ROUND_TRIP


def summarize(ic: pd.Series, spread: pd.Series, h: int) -> dict:
    per_year = 252 / h
    out = {"n_dates": int(len(ic))}
    if len(ic) > 2:
        out["ic_mean"] = float(ic.mean())
        out["ic_std"] = float(ic.std(ddof=1))
        # IC information ratio, the standard quant research statistic.
        out["ic_ir"] = float(ic.mean() / ic.std(ddof=1) * np.sqrt(per_year)) \
            if ic.std(ddof=1) > 0 else None
        out["ic_positive_pct"] = float((ic > 0).mean())
        # t-stat on non-overlapping dates only; consecutive dates share h-1 days.
        nono = ic.to_numpy()[::h]
        out["n_nonoverlap"] = int(len(nono))
        out["ic_t"] = (float(nono.mean() / (nono.std(ddof=1) / np.sqrt(len(nono))))
                       if len(nono) > 2 and nono.std(ddof=1) > 0 else None)
    if len(spread) > 2:
        s = spread.to_numpy()[::h]
        out["spread_mean"] = float(spread.mean())
        out["spread_ann"] = float(spread.mean() * per_year)
        out["spread_sharpe"] = (float(s.mean() / s.std(ddof=1) * np.sqrt(per_year))
                                if len(s) > 2 and s.std(ddof=1) > 0 else None)
        out["spread_t"] = (float(s.mean() / (s.std(ddof=1) / np.sqrt(len(s))))
                           if len(s) > 2 and s.std(ddof=1) > 0 else None)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--horizons", nargs="*", type=int, default=[21, 63, 126])
    ap.add_argument("--quantiles", type=int, default=10)
    ap.add_argument("--min-adv", type=float, default=5e6,
                    help="minimum 20-day average dollar volume, to keep it tradeable")
    args = ap.parse_args()

    st = lockbox.state()
    max_date = (pd.Timestamp(st["seal_from"]) - pd.Timedelta(days=1)).date().isoformat() \
        if st["sealed"] else config.END_DATE.isoformat()
    lockbox.guard(max_date, "cross_sectional")
    print(f"data through {max_date} (lockbox sealed from {st.get('seal_from')})")

    panel = build_panel(max_date, args.horizons)
    panel = panel[panel["adv"] >= args.min_adv]
    names_per_day = panel.groupby("dt").size()
    print(f"  {len(panel):,} rows, {panel['symbol'].nunique()} symbols")
    print(f"  median names per day: {names_per_day.median():.0f} "
          f"(breadth; was 55 before)")

    # Standardize each score within each date so they can be averaged together.
    for name in SCORES:
        panel[name] = zscore_by_date(panel[name], panel["dt"])
    # The composite: the thing firms actually trade. Many weak signals averaged,
    # not the single best one, because averaging weakly correlated signals raises
    # the information ratio even when no individual piece is impressive.
    # COMPOSITE, BUILT PROPERLY.
    #
    # The first version averaged every score, including mom_21d and reversal_21d,
    # which are the same measurement with opposite signs. They cancelled and the
    # composite came out with IC of -0.000. Averaging a signal with its own
    # negation is not diversification, it is subtraction.
    #
    # So the composite uses one representative per distinct IDEA, with each
    # orientation chosen in advance from the literature rather than from this
    # sample's results. Picking the sign that worked here would be fitting the
    # composite to the answer.
    COMPOSITE_PARTS = {
        "mom_252d_skip": +1,   # long-horizon momentum (Jegadeesh-Titman)
        "mom_126d":      +1,
        "reversal_5d":   +1,   # short-horizon reversal (Lehmann, Lo-MacKinlay)
        "low_vol_63d":   +1,   # low-volatility anomaly
        "near_52w_high": +1,   # 52-week high effect (George-Hwang)
    }
    panel["composite"] = sum(
        w * panel[k] for k, w in COMPOSITE_PARTS.items()
    ) / len(COMPOSITE_PARTS)

    rows = []
    for name in list(SCORES) + ["composite"]:
        for h in args.horizons:
            ic = daily_ic(panel, name, h)
            sp = quantile_spread(panel, name, h, args.quantiles)
            r = summarize(ic, sp, h)
            r.update({"score": name, "horizon": h})
            rows.append(r)
    res = pd.DataFrame(rows)
    res.to_csv(config.ROOT / "reports" / "cross_sectional.csv", index=False)

    print(f"\n{'=' * 86}")
    print("CROSS-SECTIONAL RESULTS — long top decile, short bottom, market neutral")
    print("=" * 86)
    print(f"{'score':<17}{'h':>3}{'IC':>8}{'IC IR':>7}{'IC t':>7}{'IC>0':>7}"
          f"{'spread/yr':>11}{'sharpe':>8}{'t':>7}")
    for _, r in res.sort_values("ic_mean", ascending=False).iterrows():
        f = lambda v, s="{:.3f}": s.format(v) if pd.notna(v) else "   -"
        print(f"{r.score:<17}{int(r.horizon):>3}{f(r.ic_mean):>8}{f(r.ic_ir,'{:.2f}'):>7}"
              f"{f(r.ic_t,'{:.2f}'):>7}{f(r.ic_positive_pct,'{:.0%}'):>7}"
              f"{f(r.spread_ann,'{:+.2%}'):>11}{f(r.spread_sharpe,'{:.2f}'):>8}"
              f"{f(r.spread_t,'{:.2f}'):>7}")

    print("\nReading this table: IC between 0.02 and 0.10 is the range real quant")
    print("firms operate in. |t| above 2 is the usual bar for believing a number.")
    print("Nothing here has been corrected for the number of strategies tried;")
    print("add these to the registry before treating any of them as a finding.")
    print(f"\nwrote reports/cross_sectional.csv")
    return 0


if __name__ == "__main__":
    sys.exit(main())
