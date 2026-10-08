"""Per-stock effective spreads estimated from daily OHLC (Corwin & Schultz 2012).

WHY THIS EXISTS. Every cost figure in this project so far has been a flat 7 basis
points a side. That is defensible for mega-cap US equities and badly wrong
anywhere else. The literature is blunt about it: percentage spreads are driven by
share PRICE more than market cap, large caps often sit under 15bp, and small caps
can run into the hundreds. Li, Sullivan and Garcia-Feijoo show the low-volatility
premium largely survives only until realistic costs are applied, because the
abnormal returns sit in exactly the illiquid names where costs bite hardest.

So testing a small-cap universe on a flat large-cap cost assumption would
manufacture an edge out of an accounting choice. This estimates a spread for each
stock, each month, from data we already have.

THE ESTIMATOR. Corwin and Schultz exploit a simple asymmetry: the high-low range
over two consecutive days reflects variance that scales with time, while the
bid-ask spread is a one-off component that does not. Comparing a single-day range
with a two-day range separates them.

    beta  = sum of squared single-day log ranges over two days
    gamma = squared log range of the two-day high and low
    alpha = (sqrt(2 beta) - sqrt(beta)) / (3 - 2 sqrt 2) - sqrt(gamma / (3 - 2 sqrt 2))
    S     = 2 (e^alpha - 1) / (1 + e^alpha)

Known limitations, kept visible rather than buried: the estimator produces
negative values when the two-day range is small relative to the daily ranges,
which is noise rather than a negative spread, and the standard treatment is to
floor those at zero before averaging. It also assumes continuous trading, so it
overstates spreads for names with frequent non-trading days. It is a daily-data
approximation to something that properly needs trade and quote data.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

K = 3 - 2 * np.sqrt(2)
FLOOR_BPS = 1.0       # nothing trades tighter than this in practice
CAP_BPS = 1000.0      # beyond this the estimate is noise, not a tradeable cost


def corwin_schultz(df: pd.DataFrame) -> pd.Series:
    """Daily spread estimate (as a fraction of price) for one symbol."""
    h, l = df["high"].astype(float), df["low"].astype(float)
    # Overnight moves can push the "true" range outside the posted high/low; the
    # paper's adjustment uses the previous close to extend the range.
    pc = df["close"].shift(1)
    h_adj = np.maximum(h, pc)
    l_adj = np.minimum(l, pc)

    hl = np.log(h_adj / l_adj) ** 2
    beta = hl + hl.shift(1)

    h2 = np.maximum(h_adj, h_adj.shift(1))
    l2 = np.minimum(l_adj, l_adj.shift(1))
    gamma = np.log(h2 / l2) ** 2

    alpha = (np.sqrt(2 * beta) - np.sqrt(beta)) / K - np.sqrt(gamma / K)
    s = 2 * (np.exp(alpha) - 1) / (1 + np.exp(alpha))
    # Negative estimates are sampling noise, not negative spreads.
    return s.clip(lower=0)


def monthly_spread_bps(bars: pd.DataFrame) -> pd.DataFrame:
    """Median spread in basis points per (symbol, month).

    The median is used rather than the mean because the daily estimator is
    noisy and occasionally explodes on gap days; a single bad day should not set
    a month's trading cost.
    """
    out = []
    for sym, g in bars.groupby("symbol", sort=False):
        g = g.sort_values("dt")
        s = corwin_schultz(g) * 10_000
        d = pd.DataFrame({"symbol": sym, "dt": g["dt"].values, "spread_bps": s.values})
        d["month"] = d["dt"].str.slice(0, 7)
        out.append(d.groupby(["symbol", "month"], as_index=False)["spread_bps"].median())
    res = pd.concat(out, ignore_index=True)
    return res.assign(spread_bps=res["spread_bps"].clip(FLOOR_BPS, CAP_BPS))


# ---------------------------------------------------------------- cost model
# CALIBRATION, AND WHY IT IS NEEDED.
#
# Corwin-Schultz returns roughly 23bp for AAPL, whose real quoted spread is
# nearer 1bp. The estimator conflates volatility with spread and is known to be
# biased upward, badly so for liquid names. Using its raw level as a trading cost
# would make every strategy look unprofitable for the wrong reason.
#
# What it does measure well is RELATIVE illiquidity across a wide range: a
# micro-cap really is far more expensive than a mega-cap, and the estimator
# ranks that correctly even when the absolute level is off.
#
# So the level is anchored to the assumption used everywhere else in this
# project (2bp of half-spread for large caps) and the cross-section is scaled by
# each stock's estimate relative to the large-cap median. Large caps therefore
# keep exactly the cost they had before, which keeps every earlier result
# comparable, and smaller names are charged in proportion to how much less
# liquid they actually are.
#
# This is an approximation. The right answer needs trade and quote data. It is
# stated here rather than hidden because the entire small-cap question turns on
# it: on a flat large-cap cost assumption, down-cap results would be fiction.

LARGE_CAP_HALF_SPREAD_BPS = 2.0
MIN_HALF_SPREAD_BPS = 1.0
MAX_HALF_SPREAD_BPS = 150.0


def calibrated_half_spread(monthly: pd.DataFrame, large_caps: set[str]) -> pd.DataFrame:
    """Scale the raw estimates so large caps match the project's standing
    assumption, and everything else is priced relative to them."""
    ref = monthly[monthly["symbol"].isin(large_caps)]
    if ref.empty:
        raise ValueError("no large-cap reference names present")
    # Per month, so the anchor moves with market-wide liquidity conditions
    # instead of pinning 2020 and 2024 to the same absolute level.
    anchor = ref.groupby("month")["spread_bps"].median().rename("anchor")
    out = monthly.merge(anchor, on="month", how="left")
    out["half_spread_bps"] = (
        LARGE_CAP_HALF_SPREAD_BPS * out["spread_bps"] / out["anchor"]
    ).clip(MIN_HALF_SPREAD_BPS, MAX_HALF_SPREAD_BPS)
    return out[["symbol", "month", "spread_bps", "half_spread_bps"]]


# ------------------------------------------------- liquidity-based cost model
# WHY THIS REPLACES THE CALIBRATED CORWIN-SCHULTZ VERSION ABOVE.
#
# Scaling CS estimates relative to large caps produced a half-spread of 2.0bp for
# the S&P 500 and only 3.1bp for the S&P 600. A 1.55x ratio across that range is
# not credible: small caps with a $35 median price and $15M of daily volume do
# not trade 1.5x wider than mega-caps, they trade several times wider. The
# estimator is dominated by volatility, and small caps are more volatile, so the
# volatility signal swamps the liquidity signal it was supposed to isolate.
#
# When a measurement cannot resolve the thing you need, the answer is not to use
# it anyway with a scaling factor. This model instead uses two quantities that
# ARE measured precisely in our data, with the functional form taken from
# standard market microstructure:
#
#   1. A TICK FLOOR. The minimum tick is one cent, so a stock cannot trade
#      tighter than half a cent relative to its price. On a $35 stock that floor
#      is 1.4bp; on a $400 stock it is 0.125bp. Price level alone creates a large
#      cross-sectional spread in percentage terms, which is exactly what the
#      literature emphasises.
#
#   2. AN INVERSE-SQRT-LIQUIDITY TERM. Spread narrows roughly with the square
#      root of volume. The coefficient is pinned so that a stock with the S&P 500
#      median dollar volume lands on the 2bp assumption used throughout this
#      project, keeping every earlier result comparable.
#
# Corwin-Schultz is retained above as a cross-check on ORDERING, which it does
# get right, not as a level. This is still an approximation to something that
# properly requires trade and quote data, and any down-cap conclusion should be
# read with that in mind.

TICK = 0.01
REF_ADV_USD = 212e6        # S&P 500 median daily dollar volume in this sample
REF_HALF_BPS = 2.0         # the assumption used everywhere else


def liquidity_half_spread_bps(price: pd.Series, adv_usd: pd.Series) -> pd.Series:
    coef = REF_HALF_BPS * np.sqrt(REF_ADV_USD)
    liq = coef / np.sqrt(adv_usd.clip(lower=1e5))
    tick_floor = (0.5 * TICK / price.clip(lower=1.0)) * 10_000
    return np.maximum(liq, tick_floor).clip(MIN_HALF_SPREAD_BPS, MAX_HALF_SPREAD_BPS)
