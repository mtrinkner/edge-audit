#!/usr/bin/env python3
"""Classic day-trading setups, defined once and never tuned.

Every rule here enters and exits INSIDE ONE SESSION. Nothing is held overnight,
which is the thing that separates this from the 41 daily signals that ended up
holding for a median of eleven days.

Seven bars a session, 09:30 through 15:30, so `bar` runs 0 to 6.

  bar 0  09:30  the opening hour, where the overnight gap gets resolved
  bar 1  10:30
  bar 2  11:30
  bar 3  12:30  midday
  bar 4  13:30
  bar 5  14:30
  bar 6  15:30  the closing hour

EVERY PARAMETER IS FIXED IN ADVANCE. No thresholds are swept, no bar indices are
optimised. Each rule is the textbook version of a setup that day traders actually
describe, and the exit is always the session close. Searching for the best entry
bar or the best gap threshold across 1,500 symbols would find something, and that
something would be fitted to this sample. The daily study already showed where
that road ends: 41 signals, 4 with positive excess, and the one that cleared
significance was a reliable loser.

THE BENCHMARK IS THE SAME SESSION. Each rule is scored against simply holding the
stock from the same entry bar to the same close. That isolates the TIMING
decision from the market move, which is the only thing a signal can claim to add.
A rule that makes money because the market went up that hour has added nothing.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

# ---------------------------------------------------------------- the rules
# Each returns a boolean mask over the session's 7 bars: True on the bar whose
# CLOSE is the entry. The exit is always the session's final close.


def opening_range_break_up(s: pd.DataFrame) -> np.ndarray:
    """Bar 1 closes above bar 0's high. The textbook opening-range breakout."""
    m = np.zeros(len(s), bool)
    if len(s) < 3:
        return m
    m[1] = s["close"].iloc[1] > s["high"].iloc[0]
    return m


def opening_range_break_down(s: pd.DataFrame) -> np.ndarray:
    """Bar 1 closes below bar 0's low. Taken LONG, to test whether the
    breakdown is actually a reversal setup rather than a short."""
    m = np.zeros(len(s), bool)
    if len(s) < 3:
        return m
    m[1] = s["close"].iloc[1] < s["low"].iloc[0]
    return m


def first_hour_high_break(s: pd.DataFrame) -> np.ndarray:
    """Any bar from 2 onward trading above the first hour's high."""
    m = np.zeros(len(s), bool)
    if len(s) < 4:
        return m
    hi = s["high"].iloc[0]
    for i in range(2, len(s)):
        if s["close"].iloc[i] > hi:
            m[i] = True
            break
    return m


def vwap_reversion_long(s: pd.DataFrame) -> np.ndarray:
    """Price falls below session VWAP by more than half a bar's typical range.

    The range scale is an EXPANDING mean over bars 0..i, not the whole session.
    A first version used the full-session mean range, which meant the entry
    threshold at 10:30 depended on how wide the bars were at 15:30. The
    future-swap audit caught it on 78 of 400 session pairs.
    """
    m = np.zeros(len(s), bool)
    if len(s) < 4:
        return m
    tp = (s["high"] + s["low"] + s["close"]) / 3
    vol = s["volume"].replace(0, np.nan)
    vwap = (tp * vol).cumsum() / vol.cumsum()
    rng = (s["high"] - s["low"]).expanding().mean()
    for i in range(1, len(s) - 1):
        r = rng.iloc[i]
        if not np.isfinite(r) or r <= 0:
            continue
        if s["close"].iloc[i] < vwap.iloc[i] - 0.5 * r:
            m[i] = True
            break
    return m


def vwap_reclaim(s: pd.DataFrame) -> np.ndarray:
    """Price crosses back ABOVE session VWAP after being below it."""
    m = np.zeros(len(s), bool)
    if len(s) < 4:
        return m
    tp = (s["high"] + s["low"] + s["close"]) / 3
    vol = s["volume"].replace(0, np.nan)
    vwap = (tp * vol).cumsum() / vol.cumsum()
    below = s["close"] < vwap
    for i in range(1, len(s) - 1):
        if below.iloc[i - 1] and not below.iloc[i]:
            m[i] = True
            break
    return m


def gap_up_fade(s: pd.DataFrame, prev_close: float) -> np.ndarray:
    """Opens more than 1% above yesterday's close. Bought, to test the
    'gaps get faded' claim as a long rather than assuming the direction."""
    m = np.zeros(len(s), bool)
    if len(s) < 3 or not np.isfinite(prev_close) or prev_close <= 0:
        return m
    if s["open"].iloc[0] / prev_close - 1 > 0.01:
        m[0] = True
    return m


def gap_down_fill(s: pd.DataFrame, prev_close: float) -> np.ndarray:
    """Opens more than 1% below yesterday's close: the classic gap-fill long."""
    m = np.zeros(len(s), bool)
    if len(s) < 3 or not np.isfinite(prev_close) or prev_close <= 0:
        return m
    if s["open"].iloc[0] / prev_close - 1 < -0.01:
        m[0] = True
    return m


def midday_reversal(s: pd.DataFrame) -> np.ndarray:
    """Down through the morning, bought at midday for the afternoon."""
    m = np.zeros(len(s), bool)
    if len(s) < 5:
        return m
    if s["close"].iloc[2] < s["open"].iloc[0]:
        m[2] = True
    return m


def afternoon_momentum(s: pd.DataFrame) -> np.ndarray:
    """Up through the morning, bought at midday on the assumption it continues."""
    m = np.zeros(len(s), bool)
    if len(s) < 5:
        return m
    if s["close"].iloc[2] > s["open"].iloc[0]:
        m[2] = True
    return m


def power_hour_long(s: pd.DataFrame) -> np.ndarray:
    """Bought at the start of the last hour when the day is already up."""
    m = np.zeros(len(s), bool)
    if len(s) < 6:
        return m
    i = len(s) - 2
    if s["close"].iloc[i] > s["open"].iloc[0]:
        m[i] = True
    return m


def inside_session_squeeze(s: pd.DataFrame) -> np.ndarray:
    """Midday range narrower than the opening bar's: the 'coiling' setup."""
    m = np.zeros(len(s), bool)
    if len(s) < 5:
        return m
    open_rng = s["high"].iloc[0] - s["low"].iloc[0]
    mid_rng = s["high"].iloc[2] - s["low"].iloc[2]
    if open_rng > 0 and mid_rng < 0.5 * open_rng:
        m[2] = True
    return m


def volume_climax_long(s: pd.DataFrame) -> np.ndarray:
    """A bar with more than twice the volume SO FAR and a down close, bought
    as capitulation.

    The baseline is an expanding mean over bars 0..i-1. The original compared a
    bar's volume to the mean of the WHOLE session, which includes bars after the
    entry, so it selected bars followed by QUIET. Exhausted selling is precisely
    when reversal happens, so the filter was partly reading the future, and the
    measured edge collapsed from +12.6bp to -0.7bp once the baseline was made
    causal. That was the single largest error in this project.
    """
    m = np.zeros(len(s), bool)
    if len(s) < 4:
        return m
    v = s["volume"].to_numpy(dtype=float)
    for i in range(1, len(s) - 1):
        prior = v[:i]
        av = prior.mean() if len(prior) else np.nan
        if not np.isfinite(av) or av <= 0:
            continue
        if v[i] > 2 * av and s["close"].iloc[i] < s["open"].iloc[i]:
            m[i] = True
            break
    return m


NEEDS_PREV_CLOSE = {"gap_up_fade", "gap_down_fill"}

RULES = {
    "opening_range_break_up": opening_range_break_up,
    "opening_range_break_down": opening_range_break_down,
    "first_hour_high_break": first_hour_high_break,
    "vwap_reversion_long": vwap_reversion_long,
    "vwap_reclaim": vwap_reclaim,
    "gap_up_fade": gap_up_fade,
    "gap_down_fill": gap_down_fill,
    "midday_reversal": midday_reversal,
    "afternoon_momentum": afternoon_momentum,
    "power_hour_long": power_hour_long,
    "inside_session_squeeze": inside_session_squeeze,
    "volume_climax_long": volume_climax_long,
}
