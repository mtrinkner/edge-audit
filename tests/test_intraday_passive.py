"""Trial 30 (python/intraday_passive.py): a full 7-bar session must reach the
signal loop, and the spread must be priced from the prior session only.

Before 2026-10-30 the NaN volume baseline on bar 0 was dropped, every
session came out with 6 bars, and the 7-bar check skipped them all: zero
signal bars, ever (docs/AUDIT_2026-10-26.md, addendum)."""

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "python"))

import intraday_passive as ip


def _session(day: str, vols, opens, closes, lows=None):
    n = ip.SESSION_BARS
    return pd.DataFrame({"symbol": "XYZ", "session": day, "bar": range(n),
                         "ts": pd.date_range(f"{day} 09:30", periods=n, freq="h"),
                         "open": opens, "close": closes, "low": lows if lows is not None else np.minimum(opens, closes),
                         "volume": vols, "bars_in_session": n})


def _two_sessions(last_session_volume=1000.0):
    flat = [10.0] * 7
    s1 = _session("2024-01-02", [1000.0] * 7, flat, flat)
    # bar 3 is a high-volume down bar: volume 5x the bars before it, close < open
    s2 = _session("2024-01-03", [1000, 1000, 1000, 5000, 1000, 1000, last_session_volume],
                  [10.0, 10.0, 10.0, 10.0, 9.9, 9.9, 9.95], [10.0, 10.0, 10.0, 9.9, 9.9, 9.95, 10.0])
    return pd.concat([s1, s2], ignore_index=True)


def test_a_full_seven_bar_session_produces_its_signal():
    T = ip.signals(_two_sessions())
    assert len(T) == 1 and T.iloc[0]["bar"] == 3 and T.iloc[0]["session"] == "2024-01-03"


def test_the_first_session_has_no_prior_spread_and_no_signal():
    T = ip.signals(_two_sessions())
    assert (T["session"] != "2024-01-02").all()


def test_spread_ignores_the_signal_sessions_own_later_volume():
    a = ip.signals(_two_sessions(1000.0))
    b = ip.signals(_two_sessions(1_000_000.0))         # the last bar's volume, known only at 16:00
    assert a.iloc[0]["half"] == b.iloc[0]["half"]
