"""The sizing rule must not depend on signals that have not happened yet.

Trials 38-42 split each day's capital across all of that day's signals. This
test is the one that would have caught it.
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "python"))

from evaluation import check_causal_weights, clustered_mean


def _trades(seed=1, days=30):
    rng = np.random.default_rng(seed)
    rows = []
    for d in range(days):
        for _ in range(rng.integers(1, 8)):
            rows.append((f"2025-01-{d + 1:02d}", int(rng.integers(0, 7))))
    t = pd.DataFrame(rows, columns=["session", "bar"])
    t["time"] = t["session"] + " " + t["bar"].astype(str)
    return t


def test_daily_budget_is_caught():
    t = _trades()
    daily_budget = lambda x: 1.0 / x.groupby("session")["bar"].transform("size")
    assert check_causal_weights(daily_budget, t, "time"), \
        "splitting the day's capital across the day's signals must fail"


def test_fixed_notional_passes():
    t = _trades()
    fixed = lambda x: pd.Series(1.0, index=x.index)
    assert check_causal_weights(fixed, t, "time") == []


def test_signals_so_far_passes():
    # Sizing by how many signals have fired UP TO NOW is causal.
    t = _trades()
    so_far = lambda x: 1.0 / (x.sort_values("time").groupby("session").cumcount() + 1)
    assert check_causal_weights(so_far, t, "time") == []


def test_clustered_se_is_wider_than_naive_when_days_move_together():
    rng = np.random.default_rng(0)
    day = np.repeat(np.arange(200), 50)
    shock = rng.normal(0, 1, 200)[day]          # one common shock per day
    r = pd.Series(shock + rng.normal(0, 1, len(day)))
    c = clustered_mean(r, pd.Series(day))
    naive = r.std(ddof=1) / np.sqrt(len(r))
    assert c["se"] > 3 * naive


def test_concentration_alarm_fires_on_one_bad_row():
    # Trial 50 in miniature: ordinary weekly returns plus one vendor re-stitch.
    rng = np.random.default_rng(3)
    day = np.repeat(np.arange(300), 100)
    r = pd.Series(rng.normal(0.0, 0.03, len(day)))
    clean = clustered_mean(r, pd.Series(day))
    assert not clean["alarm"]
    r.iloc[12345] = 225.0                       # +22,500%, like CHRD 2020-11
    import warnings
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        bad = clustered_mean(r, pd.Series(day))
    assert bad["alarm"] and any("CONCENTRATION ALARM" in str(x.message) for x in w)
    assert abs(bad["trimmed_mean"]) < 0.01      # the trimmed mean ignores it
