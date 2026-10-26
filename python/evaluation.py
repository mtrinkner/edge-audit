#!/usr/bin/env python3
"""One causal way to score a strategy. Every new study should go through this.

WHY THIS EXISTS. The 2026-10-14 audit found that trials 38-42 reported a
"per-day" return that split each session's capital across ALL of that session's
signals. That needs the end-of-day signal count at entry time, and the count
predicted the outcome, so a strategy with no edge at all showed a Sharpe of 1.52.
It also found t-statistics computed on that same per-session mean, which tested
the wrong quantity. Both mistakes are easy to make again, so the scoring lives
here, once.

THE RULES THIS MODULE ENFORCES

  1. The estimand is the mean return PER UNIT OF CAPITAL DEPLOYED, with a size
     fixed before the trade. That is the trade-weighted mean. Anything that
     re-weights trades by a quantity only known later is not a strategy.
  2. Inference is clustered by session: one market day is one shock, not
     thousands of independent ones. The trade-weighted mean is a ratio estimator
     and its standard error is computed as one.
  3. A Sharpe ratio comes from a P&L series built with causal sizing (fixed
     notional per trade, fixed capital), annualized by the frequency of that
     series and nothing else.
  4. Every result is reported with its minimum detectable effect, so "no edge"
     can be told apart from "this test could not have seen one".
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.stats import norm


def clustered_mean(ret: pd.Series, cluster: pd.Series) -> dict:
    """Trade-weighted mean, with a session-clustered standard error.

    Ratio estimator: mu = sum(r) / n. Residual per cluster = sum(r_c) - mu * n_c.
    """
    df = pd.DataFrame({"r": ret.to_numpy(float), "c": cluster.to_numpy()}).dropna()
    s = df.groupby("c")["r"].agg(["sum", "size"])
    n = s["size"].sum()
    mu = s["sum"].sum() / n
    resid = s["sum"] - mu * s["size"]
    g = len(s)
    se = np.sqrt((resid ** 2).sum() * g / max(g - 1, 1)) / n
    t = mu / se if se > 0 else np.nan
    out = {"mean": mu, "se": se, "t": t, "p_two": 2 * norm.sf(abs(t)),
           "p_one": norm.sf(t), "trades": int(n), "clusters": int(g)}
    out.update(concentration(df["r"], s["sum"], n, mu, se))
    return out


# How much of a result may come from its 5 largest clusters before it is flagged.
# Trial 50's headline came 93% from 5 weeks, one of them a vendor bankruptcy
# re-stitch worth +22,525%. Nobody noticed until the user asked. This makes the
# check automatic.
CONCENTRATION_ALARM = 0.25


def concentration(r: pd.Series, cluster_sums: pd.Series, n: int, mu: float,
                  se: float) -> dict:
    """Is the mean carried by a handful of observations? Always computed."""
    lo, hi = r.quantile([0.005, 0.995])
    trimmed = float(r.clip(lo, hi).mean())
    tot = cluster_sums.abs().sum()
    top5 = float(cluster_sums.abs().nlargest(5).sum() / tot) if tot > 0 else np.nan
    # Fire when trimming the extreme 1% moves the mean by more than its own
    # standard error, or when 5 clusters carry over a quarter of the total. A
    # relative test would fire on every near-zero null and be ignored.
    alarm = (top5 > CONCENTRATION_ALARM) or (abs(trimmed - mu) > se)
    if alarm:
        import warnings
        warnings.warn(f"CONCENTRATION ALARM: mean {mu:+.5f} vs trimmed {trimmed:+.5f}; "
                      f"top-5 clusters carry {top5:.0%} of the absolute total. Check for bad "
                      "data or a few extreme observations before believing this.",
                      stacklevel=3)
    return {"trimmed_mean": trimmed, "top5_share": top5, "alarm": bool(alarm)}


def mde(se: float, alpha: float = 0.05, power: float = 0.8) -> float:
    """Smallest true mean this test detects with the given power (two-sided)."""
    return (norm.ppf(1 - alpha / 2) + norm.ppf(power)) * se


def causal_daily_pnl(ret: pd.Series, session: pd.Series, all_sessions,
                     notional_per_trade: float, capital: float = 1.0) -> pd.Series:
    """Daily return of a book that puts a FIXED notional on every trade.

    The notional is a constant chosen before the sample, so it cannot depend on
    how many signals a day turns out to have. Days with no trades return zero.
    """
    pnl = (ret * notional_per_trade).groupby(session).sum() / capital
    return pnl.reindex(all_sessions, fill_value=0.0)


def sharpe(series: pd.Series, periods_per_year: float) -> float:
    s = series.dropna()
    sd = s.std(ddof=1)
    return float(s.mean() / sd * np.sqrt(periods_per_year)) if sd > 0 else np.nan


def check_causal_weights(weight_fn, trades: pd.DataFrame, time_col: str,
                         n_checks: int = 25, seed: int = 0) -> list[str]:
    """Future-perturbation test for a sizing rule.

    weight_fn(trades) -> Series of weights aligned to trades. For random cut
    points, every trade AFTER the cut is dropped; the weights of trades at or
    before the cut must not change. Returns a list of failures (empty = causal).

    The daily-budget rule fails this immediately: dropping the afternoon's
    signals changes the morning trade's share of the budget.
    """
    rng = np.random.default_rng(seed)
    t = trades.sort_values(time_col).reset_index(drop=True)
    full = weight_fn(t)
    fails = []
    times = t[time_col].unique()
    for cut in rng.choice(times, size=min(n_checks, len(times)), replace=False):
        keep = t[time_col] <= cut
        part = weight_fn(t[keep].copy())
        if not np.allclose(full[keep].to_numpy(float), part.to_numpy(float),
                           equal_nan=True):
            fails.append(str(cut))
    return fails


def breakeven_report(gross: pd.Series, cluster: pd.Series, price: pd.Series) -> dict:
    """Report what a strategy can AFFORD to pay, instead of assuming what it pays.

    The audit found the project's spread measurements were noise (hourly EDGE),
    so a verdict that hinges on a cost number is a verdict on the cost model.
    This reports the gross edge per round trip with its uncertainty, and the one
    hard fact available without quote data: no trade can cost less than one
    cent, so tick / price is a lower bound on the round-trip spread.
    """
    c = clustered_mean(gross, cluster)
    floor = float((0.01 / price).median())
    return {"gross_bp": c["mean"] * 1e4, "t_gross": c["t"],
            "breakeven_rt_bp": c["mean"] * 1e4,
            "breakeven_lower95_bp": (c["mean"] - 1.96 * c["se"]) * 1e4,
            "one_cent_floor_rt_bp": floor * 1e4,
            "mde_bp": mde(c["se"]) * 1e4,
            "verdict": ("no detectable gross edge; cost is irrelevant"
                        if c["t"] < 1.96 else
                        "gross edge below the one-cent floor; untradeable at any spread"
                        if c["mean"] - 1.96 * c["se"] < floor else
                        "gross edge clears the hard floor; now measure the real spread")}


# ----------------------------------------------------------- overlapping holds
# ADDED 2026-10-26. clustered_mean clusters by ENTRY session, which is right when
# every trade closes within its session. It is wrong for multi-day holds: a
# trade entered Monday and one entered Tuesday on the same stock share most of
# their holding period, sit in different clusters, and are treated as
# independent. On trial 27 (holds up to 60 sessions) entry-date clustering gave
# t-statistics a median 2.3x too large: 28 of 41 signals looked significant,
# 8 survive calendar-quarter clustering. Use this for any hold longer than a day.

def block_clustered_mean(ret: pd.Series, entry_date: pd.Series, hold_days: int,
                         block_multiple: float = 2.0) -> dict:
    """Trade-weighted mean, clustered by calendar blocks at least as long as the hold.

    Blocks are block_multiple x hold_days trading days (converted to calendar
    days at 7/5), so trades that overlap mostly land in the same cluster and the
    remaining dependence is confined to block boundaries. With fewer than about
    30 blocks the standard error is itself unreliable; the count is returned so
    that can be checked.
    """
    # numpy dates, not pandas: pandas datetime construction segfaults on this
    # machine's pandas 3 / Python 3.14 build (found 2026-10-26).
    days = np.asarray(np.asarray(entry_date, dtype=str), dtype="datetime64[D]").astype(np.int64)
    span = max(1, int(np.ceil(hold_days * block_multiple * 7 / 5)))
    block = (days - days.min()) // span
    out = clustered_mean(pd.Series(np.asarray(ret, float)), pd.Series(block))
    out["block_calendar_days"] = span
    if out["clusters"] < 30:
        import warnings
        warnings.warn(f"only {out['clusters']} blocks; the clustered SE is unreliable "
                      "below about 30", stacklevel=2)
    return out


def newey_west_t(series: pd.Series, lags: int | None = None) -> dict:
    """Mean of a time series with a Newey-West (Bartlett) standard error.

    For a portfolio return series, one row per period. lags=None uses the usual
    floor(4 (T/100)^(2/9)). When periods overlap (h-day returns sampled daily),
    pass lags >= h - 1.
    """
    x = pd.Series(series).dropna().to_numpy(float)
    n = len(x)
    if lags is None:
        lags = int(np.floor(4 * (n / 100) ** (2 / 9)))
    e = x - x.mean()
    lrv = e @ e / n
    for k in range(1, min(lags, n - 1) + 1):
        lrv += 2 * (1 - k / (lags + 1)) * (e[k:] @ e[:-k]) / n
    se = np.sqrt(max(lrv, 0) / n)
    return {"mean": float(x.mean()), "se": float(se),
            "t": float(x.mean() / se) if se > 0 else np.nan, "n": n, "lags": lags}


def factor_alpha(ret: pd.Series, factors: pd.DataFrame, lags: int | None = None,
                 periods_per_year: float = 252) -> dict:
    """Regress a strategy's EXCESS return on factor returns; report alpha with HAC SEs.

    ret and factors must be aligned on the same index and already net of the
    risk-free rate where relevant (Fama-French factors are long-short, so only
    the strategy and Mkt-RF need it). A high raw Sharpe that disappears here was
    paid for bearing market, size, value, momentum or other priced risk.
    """
    df = pd.concat([pd.Series(ret, name="_y"), factors], axis=1, join="inner").dropna()
    y = df["_y"].to_numpy(float)
    X = np.column_stack([np.ones(len(df)), df.drop(columns="_y").to_numpy(float)])
    n, k = X.shape
    if lags is None:
        lags = int(np.floor(4 * (n / 100) ** (2 / 9)))
    XtX_inv = np.linalg.inv(X.T @ X)
    b = XtX_inv @ X.T @ y
    u = y - X @ b
    Xu = X * u[:, None]
    S = Xu.T @ Xu
    for j in range(1, min(lags, n - 1) + 1):
        G = Xu[j:].T @ Xu[:-j]
        S += (1 - j / (lags + 1)) * (G + G.T)
    V = XtX_inv @ S @ XtX_inv * n / (n - k)
    se = np.sqrt(np.diag(V))
    names = ["alpha"] + list(df.columns.drop("_y"))
    resid = u + b[0]                      # alpha plus idiosyncratic return
    return {"coef": dict(zip(names, b)), "t": dict(zip(names, b / se)),
            "alpha_annual": float(b[0] * periods_per_year),
            "alpha_t": float(b[0] / se[0]),
            "information_ratio": float(resid.mean() / resid.std(ddof=1)
                                       * np.sqrt(periods_per_year)),
            "r2": float(1 - u @ u / ((y - y.mean()) @ (y - y.mean()))),
            "n": n, "lags": lags}


def rank_ic(signal: pd.Series, fwd_ret: pd.Series, date: pd.Series,
            min_names: int = 30) -> pd.Series:
    """Spearman rank IC per date. Test its mean with newey_west_t, with lags at
    least the forward-return horizon minus one when dates are daily."""
    df = pd.DataFrame({"s": np.asarray(signal, float), "r": np.asarray(fwd_ret, float),
                       "d": np.asarray(date)}).dropna()
    df = df[df.groupby("d")["s"].transform("size") >= min_names]
    rs = df.groupby("d")[["s", "r"]].rank()
    rs["d"] = df["d"]
    return rs.groupby("d").apply(lambda g: g["s"].corr(g["r"])).sort_index()
