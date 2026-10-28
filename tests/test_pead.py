"""The PEAD event and portfolio rules, on synthetic data with known answers.

WRDS was unavailable when python/pead.py was written, so these pin down the
parts that are easy to get silently wrong: day 0 for after-close
announcements, deciles cut only on the previous quarter, and the entry-day,
holding-period and cost arithmetic.
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "python"))

import pead

CAL = np.array(["2020-01-02", "2020-01-03", "2020-01-06", "2020-01-07", "2020-01-08",
                "2020-01-09", "2020-01-10", "2020-01-13"])


def test_day_zero_moves_after_close_and_untimed_announcements():
    ann = pd.Series(["2020-01-03", "2020-01-03", "2020-01-03", "2020-01-04", "2020-01-03"])   # the 4th is a Saturday
    tim = pd.Series(["08:30:00", "16:05:00", None, "08:00:00", "00:00:00"])
    i = pead.day_zero(ann, tim, CAL)
    assert list(CAL[i]) == ["2020-01-03", "2020-01-06", "2020-01-06", "2020-01-06", "2020-01-06"]


def test_deciles_use_only_the_previous_quarter():
    rng = np.random.default_rng(0)
    q1 = pd.DataFrame({"day0_date": "2020-02-15", "sue": rng.normal(0, 1, 500)})
    # Q2 events are all huge; ranked against Q1's cutpoints every one is decile 10.
    q2 = pd.DataFrame({"day0_date": "2020-05-15", "sue": np.full(100, 50.0)})
    ev = pd.concat([q1, q2], ignore_index=True)
    d = pead.assign_deciles(ev)
    assert d[:500].isna().all()                  # Q4 2019 is missing, so Q1 is unranked
    assert (d[500:] == 10).all()


def test_deciles_are_balanced_against_a_stable_distribution():
    rng = np.random.default_rng(1)
    ev = pd.DataFrame({"day0_date": ["2020-02-01"] * 20000 + ["2020-05-01"] * 20000,
                       "sue": rng.normal(0, 1, 40000)})
    d = pead.assign_deciles(ev)[20000:]
    share = d.value_counts(normalize=True).sort_index()
    assert list(share.index) == list(range(1, 11))
    assert share.between(0.08, 0.12).all()


def test_leg_returns_entry_day_hold_and_costs():
    px = pd.DataFrame({"permno": 1, "d": CAL,
                       "open": [10, 10, 10, 20, 10, 10, 10, 10.0],
                       "close": [10, 10, 10, 22, 10, 10, 10, 10.0],
                       "ret": [0, 0, 0, 0.5, 0.01, 0.02, 0.03, 0.0],
                       "half_spread": 0.001})
    pos = pd.DataFrame({"permno": [1], "entry": [3], "exit": [5]})
    out = pead.leg_returns(pos, px, CAL).set_index("i")
    # Entry day earns close/open - 1 (not dlyret), then dlyret through the exit day.
    assert np.allclose(out["gross"].to_numpy(), [0.10, 0.01, 0.02])
    assert np.allclose(out["net"].to_numpy(), [0.10 - 0.001, 0.01, 0.02 - 0.001])
    assert list(out.index) == [3, 4, 5]


def test_leg_returns_equal_weights_open_positions_and_skips_missing_entry():
    px = pd.DataFrame({"permno": [1, 1, 2, 2], "d": [CAL[2], CAL[3], CAL[2], CAL[3]],
                       "open": [10, 10, 10, 10.0], "close": [11, 10, 9, 10.0],
                       "ret": [0, 0.04, 0, 0.0], "half_spread": 0.0})
    pos = pd.DataFrame({"permno": [1, 2, 2], "entry": [2, 2, 1], "exit": [3, 3, 3]})
    out = pead.leg_returns(pos, px, CAL).set_index("i")
    # Stock 2's position from day 1 has no row that day, so it is never entered.
    assert out.loc[2, "n"] == 2 and np.isclose(out.loc[2, "gross"], (0.10 - 0.10) / 2)
    assert np.isclose(out.loc[3, "gross"], (0.04 + 0.0) / 2)
