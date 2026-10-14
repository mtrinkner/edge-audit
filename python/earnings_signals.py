"""Earnings-based cross-sectional signals, built to be point-in-time safe.

The construction rule that governs everything here: on decision date t, a symbol
may only use announcements whose `tradeable_from` is less than or equal to t.
Nothing about a future release, not its date and not its content, may touch a
score computed at t.

Six signals, each a distinct mechanism rather than six views of one:

  sue              Standardized surprise. The classic PEAD driver: the surprise
                   scaled by the dispersion of that name's recent surprises, so
                   a 5% beat means more for a company that usually lands within
                   1% than for one that routinely swings 20%.
  surprise_pct     The raw reported surprise, unscaled.
  drift_window     1 while a name sits inside the documented drift window after
                   a release, 0 otherwise. PEAD is an event-time effect, and
                   this isolates it.
  beat_streak      Consecutive beats over the last four quarters. Tests whether
                   the persistence of surprises carries information beyond the
                   latest one.
  surprise_accel   Latest standardized surprise minus the previous one. Change
                   in the surprise, not its level.
  days_since       Trading days since the last release. Mostly a control: it
                   separates genuine drift from the simple fact that recently
                   reporting names behave differently.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

DRIFT_WINDOW_DAYS = 60       # Bernard & Thomas find drift persisting ~60 sessions
MIN_HISTORY = 4              # quarters needed before a standardized surprise means anything


def build_event_features(ev: pd.DataFrame) -> pd.DataFrame:
    """Per-announcement features, computed only from that name's PRIOR releases."""
    out = []
    for sym, g in ev.sort_values(["symbol", "tradeable_from"]).groupby("symbol", sort=False):
        g = g.reset_index(drop=True)
        s = g["surprise_pct"]
        # Expanding stats shifted by one so the current surprise never informs
        # its own normalization. Using the full-sample std here would leak.
        mu = s.shift(1).expanding(MIN_HISTORY).mean()
        sd = s.shift(1).expanding(MIN_HISTORY).std()
        g["sue"] = (s - mu) / sd.replace(0, np.nan)
        beat = (s > 0).astype(int)
        streak = beat.copy()
        for i in range(1, len(beat)):
            streak.iloc[i] = beat.iloc[i] * (streak.iloc[i - 1] + 1)
        g["beat_streak"] = streak.clip(upper=8)
        g["surprise_accel"] = g["sue"] - g["sue"].shift(1)
        out.append(g)
    return pd.concat(out, ignore_index=True) if out else ev


def as_of_panel(events: pd.DataFrame, sessions: pd.DataFrame) -> pd.DataFrame:
    """For each (symbol, session), attach the latest announcement already public.

    merge_asof with direction='backward' is exactly this: for every session it
    takes the most recent event at or before it, and never a later one. The
    allow_exact_matches flag stays True because tradeable_from has already been
    resolved to the first session the release could be acted on.
    """
    # merge_asof needs a numeric or datetime key, so both sides are converted
    # and the original ISO strings are restored afterwards for the downstream
    # joins, which key on the string form.
    ev = events.copy()
    ss = sessions.copy()
    ev["_tf"] = pd.to_datetime(ev["tradeable_from"])
    ss["_dt"] = pd.to_datetime(ss["dt"])
    ev = ev.sort_values("_tf")
    ss = ss.sort_values("_dt")
    merged = pd.merge_asof(
        ss, ev,
        left_on="_dt", right_on="_tf", by="symbol",
        direction="backward", allow_exact_matches=True,
    )
    merged["days_since_cal"] = (merged["_dt"] - merged["_tf"]).dt.days
    return merged.drop(columns=["_dt", "_tf"])
