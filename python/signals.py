"""A zoo of classical technical signals, every one computed point-in-time.

Each function takes a per-symbol OHLCV frame sorted by date and returns a boolean
Series: True on bars where the signal fires. Everything uses only data up to and
including the current bar. No function may reference a future row, and
`tests/test_no_lookahead.py` plus the truncation check in zoo.py enforce it.

These are the signals retail technical analysis actually talks about: moving
average crossovers, RSI, MACD, Bollinger bands, Donchian breakouts, support and
resistance, candlestick patterns, gaps and volume events. They are here to be
tested fairly, not to be endorsed. Most have been public for forty years, which
is itself a reason to expect nothing: an edge printed in a book in 1978 and
available in every charting package has had a long time to be arbitraged away.

The point is not to find the one that works. It is to measure how good the best
of N looks when you test N of them, and compare that to how good the best of N
coin flips looks.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


# ----------------------------------------------------------------- primitives
def sma(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n, min_periods=n).mean()


def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False, min_periods=n).mean()


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    d = close.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    rs = up / dn.replace(0, np.nan)
    return 100 - 100 / (1 + rs)


def true_range(df: pd.DataFrame) -> pd.Series:
    pc = df["close"].shift(1)
    return pd.concat([df["high"] - df["low"],
                      (df["high"] - pc).abs(),
                      (df["low"] - pc).abs()], axis=1).max(axis=1)


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    return true_range(df).rolling(n, min_periods=n).mean()


def body(df: pd.DataFrame) -> pd.Series:
    return (df["close"] - df["open"]).abs()


def upper_wick(df: pd.DataFrame) -> pd.Series:
    return df["high"] - df[["open", "close"]].max(axis=1)


def lower_wick(df: pd.DataFrame) -> pd.Series:
    return df[["open", "close"]].min(axis=1) - df["low"]


def rng(df: pd.DataFrame) -> pd.Series:
    return (df["high"] - df["low"]).replace(0, np.nan)


# ------------------------------------------------------- moving average family
def golden_cross(df, fast=50, slow=200):
    f, s = sma(df["adj_close"], fast), sma(df["adj_close"], slow)
    return (f > s) & (f.shift(1) <= s.shift(1))


def death_cross(df, fast=50, slow=200):
    f, s = sma(df["adj_close"], fast), sma(df["adj_close"], slow)
    return (f < s) & (f.shift(1) >= s.shift(1))


def above_sma(df, n=200):
    return df["adj_close"] > sma(df["adj_close"], n)


def cross_above_sma(df, n=50):
    m = sma(df["adj_close"], n)
    return (df["adj_close"] > m) & (df["adj_close"].shift(1) <= m.shift(1))


def ma_ribbon_aligned(df):
    c = df["adj_close"]
    return (sma(c, 20) > sma(c, 50)) & (sma(c, 50) > sma(c, 200))


def ema_cross(df, fast=12, slow=26):
    f, s = ema(df["adj_close"], fast), ema(df["adj_close"], slow)
    return (f > s) & (f.shift(1) <= s.shift(1))


# ------------------------------------------------------------- oscillators
def rsi_oversold(df, n=14, lvl=30):
    r = rsi(df["adj_close"], n)
    return (r < lvl) & (r.shift(1) >= lvl)


def rsi_overbought(df, n=14, lvl=70):
    r = rsi(df["adj_close"], n)
    return (r > lvl) & (r.shift(1) <= lvl)


def rsi_cross_50(df, n=14):
    r = rsi(df["adj_close"], n)
    return (r > 50) & (r.shift(1) <= 50)


def macd_cross_up(df):
    m = ema(df["adj_close"], 12) - ema(df["adj_close"], 26)
    sig = m.ewm(span=9, adjust=False, min_periods=9).mean()
    return (m > sig) & (m.shift(1) <= sig.shift(1))


def macd_hist_positive(df):
    m = ema(df["adj_close"], 12) - ema(df["adj_close"], 26)
    sig = m.ewm(span=9, adjust=False, min_periods=9).mean()
    h = m - sig
    return (h > 0) & (h.shift(1) <= 0)


def stochastic_oversold(df, n=14, lvl=20):
    lo = df["low"].rolling(n, min_periods=n).min()
    hi = df["high"].rolling(n, min_periods=n).max()
    k = 100 * (df["close"] - lo) / (hi - lo).replace(0, np.nan)
    return (k < lvl) & (k.shift(1) >= lvl)


# --------------------------------------------------------------- bands
def bollinger_lower_touch(df, n=20, k=2.0):
    m, s = sma(df["adj_close"], n), df["adj_close"].rolling(n, min_periods=n).std()
    return df["adj_close"] < (m - k * s)


def bollinger_upper_break(df, n=20, k=2.0):
    m, s = sma(df["adj_close"], n), df["adj_close"].rolling(n, min_periods=n).std()
    up = m + k * s
    return (df["adj_close"] > up) & (df["adj_close"].shift(1) <= up.shift(1))


def bollinger_squeeze(df, n=20, look=120):
    m, s = sma(df["adj_close"], n), df["adj_close"].rolling(n, min_periods=n).std()
    w = (2 * k_width(s)) / m.replace(0, np.nan)
    return w <= w.rolling(look, min_periods=look).quantile(0.10)


def k_width(s: pd.Series) -> pd.Series:
    return 2.0 * s


# --------------------------------------------------- breakouts, S/R levels
def donchian_break(df, n=20):
    prior = df["high"].rolling(n, min_periods=n).max().shift(1)
    return df["close"] > prior


def donchian_break_55(df):
    return donchian_break(df, 55)


def new_52w_high(df):
    prior = df["high"].rolling(252, min_periods=252).max().shift(1)
    return df["close"] > prior


def resistance_break_volume(df, n=20, mult=1.5):
    prior = df["high"].rolling(n, min_periods=n).max().shift(1)
    v = df["volume"] / df["volume"].rolling(20, min_periods=20).mean()
    return (df["close"] > prior) & (v > mult)


def support_bounce(df, n=20):
    """Near the 20-day low but still above the 200-day average: classic 'buy the
    dip in an uptrend'."""
    lo = df["low"].rolling(n, min_periods=n).min()
    near = df["close"] <= lo * 1.02
    return near & (df["adj_close"] > sma(df["adj_close"], 200))


def pullback_to_sma50(df):
    m = sma(df["adj_close"], 50)
    touched = (df["low"] <= m) & (df["close"] > m)
    return touched & (sma(df["adj_close"], 50) > sma(df["adj_close"], 200))


# ------------------------------------------------------------ candlesticks
def bullish_engulfing(df):
    prev_down = df["close"].shift(1) < df["open"].shift(1)
    up = df["close"] > df["open"]
    return (prev_down & up
            & (df["close"] >= df["open"].shift(1))
            & (df["open"] <= df["close"].shift(1)))


def bearish_engulfing(df):
    prev_up = df["close"].shift(1) > df["open"].shift(1)
    dn = df["close"] < df["open"]
    return (prev_up & dn
            & (df["open"] >= df["close"].shift(1))
            & (df["close"] <= df["open"].shift(1)))


def hammer(df):
    return (lower_wick(df) > 2 * body(df)) & (upper_wick(df) < body(df)) & (rng(df) > 0)


def shooting_star(df):
    return (upper_wick(df) > 2 * body(df)) & (lower_wick(df) < body(df)) & (rng(df) > 0)


def doji(df):
    return body(df) <= 0.1 * rng(df)


def morning_star(df):
    d1 = df["close"].shift(2) < df["open"].shift(2)
    d2 = body(df).shift(1) <= 0.3 * rng(df).shift(1)
    d3 = (df["close"] > df["open"]) & (df["close"] > df["close"].shift(2))
    return d1 & d2 & d3


def three_white_soldiers(df):
    up = df["close"] > df["open"]
    return up & up.shift(1) & up.shift(2) & (df["close"] > df["close"].shift(1)) \
        & (df["close"].shift(1) > df["close"].shift(2))


def inside_bar(df):
    return (df["high"] < df["high"].shift(1)) & (df["low"] > df["low"].shift(1))


def outside_bar_up(df):
    return (df["high"] > df["high"].shift(1)) & (df["low"] < df["low"].shift(1)) \
        & (df["close"] > df["open"])


# ------------------------------------------------------------ gaps, volume
def gap_up(df, pct=0.02):
    return df["open"] / df["close"].shift(1) - 1 > pct


def gap_down(df, pct=0.02):
    return df["open"] / df["close"].shift(1) - 1 < -pct


def volume_spike_up(df, mult=2.0):
    v = df["volume"] / df["volume"].rolling(20, min_periods=20).mean()
    return (v > mult) & (df["close"] > df["open"])


def volume_dryup(df, mult=0.5):
    v = df["volume"] / df["volume"].rolling(20, min_periods=20).mean()
    return v < mult


# ------------------------------------------------------- mean reversion
def three_down_days(df):
    d = df["adj_close"] < df["adj_close"].shift(1)
    return d & d.shift(1) & d.shift(2)


def big_down_day(df, pct=-0.03):
    return df["adj_close"].pct_change() < pct


def extreme_below_sma20(df, k=2.0):
    m = sma(df["adj_close"], 20)
    s = df["adj_close"].rolling(20, min_periods=20).std()
    return df["adj_close"] < m - k * s


def oversold_in_uptrend(df):
    return (rsi(df["adj_close"], 14) < 35) & (df["adj_close"] > sma(df["adj_close"], 200))


# ------------------------------------------------------------- the registry
# name -> (function, kwargs). Parameter variants count as separate trials,
# because trying 50/200 and then 20/100 is looking twice.
SIGNALS: dict[str, tuple] = {
    "golden_cross_50_200": (golden_cross, {}),
    "golden_cross_20_100": (golden_cross, {"fast": 20, "slow": 100}),
    "death_cross_50_200": (death_cross, {}),
    "above_sma_200": (above_sma, {}),
    "above_sma_50": (above_sma, {"n": 50}),
    "cross_above_sma_50": (cross_above_sma, {}),
    "cross_above_sma_20": (cross_above_sma, {"n": 20}),
    "ma_ribbon_aligned": (ma_ribbon_aligned, {}),
    "ema_cross_12_26": (ema_cross, {}),
    "rsi_oversold_30": (rsi_oversold, {}),
    "rsi_oversold_20": (rsi_oversold, {"lvl": 20}),
    "rsi_overbought_70": (rsi_overbought, {}),
    "rsi_cross_50": (rsi_cross_50, {}),
    "macd_cross_up": (macd_cross_up, {}),
    "macd_hist_positive": (macd_hist_positive, {}),
    "stochastic_oversold": (stochastic_oversold, {}),
    "bollinger_lower_touch": (bollinger_lower_touch, {}),
    "bollinger_upper_break": (bollinger_upper_break, {}),
    "donchian_break_20": (donchian_break, {}),
    "donchian_break_55": (donchian_break_55, {}),
    "new_52w_high": (new_52w_high, {}),
    "resistance_break_volume": (resistance_break_volume, {}),
    "support_bounce": (support_bounce, {}),
    "pullback_to_sma50": (pullback_to_sma50, {}),
    "bullish_engulfing": (bullish_engulfing, {}),
    "bearish_engulfing": (bearish_engulfing, {}),
    "hammer": (hammer, {}),
    "shooting_star": (shooting_star, {}),
    "doji": (doji, {}),
    "morning_star": (morning_star, {}),
    "three_white_soldiers": (three_white_soldiers, {}),
    "inside_bar": (inside_bar, {}),
    "outside_bar_up": (outside_bar_up, {}),
    "gap_up_2pct": (gap_up, {}),
    "gap_down_2pct": (gap_down, {}),
    "volume_spike_up": (volume_spike_up, {}),
    "volume_dryup": (volume_dryup, {}),
    "three_down_days": (three_down_days, {}),
    "big_down_day": (big_down_day, {}),
    "extreme_below_sma20": (extreme_below_sma20, {}),
    "oversold_in_uptrend": (oversold_in_uptrend, {}),
}


def compute(df: pd.DataFrame, name: str) -> pd.Series:
    fn, kw = SIGNALS[name]
    return fn(df, **kw).fillna(False).astype(bool)
