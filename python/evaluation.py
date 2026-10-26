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
