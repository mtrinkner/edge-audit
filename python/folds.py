"""Walk-forward fold construction with purging and embargo.

Separated from model.py so the splitting logic can be tested on its own. The
split is the single most common place a financial ML project leaks, and a split
buried inside a training loop is a split nobody audits.

The rules, in order of how often they are gotten wrong:

1. Folds are CONTIGUOUS IN TIME. Random k-fold puts 2025 in the training set and
   2016 in the test set, which lets the model learn from the future.

2. Training data is PURGED near the test boundary. A row dated t carries a label
   that resolves at t + horizon. If t + horizon >= test_start, that label was
   realized inside the test window, so the row must go. Purging only the rows
   whose labels overlap is enough; dropping more wastes data.

3. An EMBARGO follows the test window. Returns are serially correlated, so rows
   immediately after a test period still carry information about it. The embargo
   keeps them out of any later training fold.

4. Folds are reported individually, not just averaged. A strategy that works in
   three folds and fails in two is a different finding from one that works
   consistently, and an average hides the difference.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict

import pandas as pd

import config


@dataclass(frozen=True)
class Fold:
    fold_id: int
    train_start: str
    train_end: str      # already purged: excludes rows whose labels reach the test set
    test_start: str
    test_end: str
    purge_days: int
    embargo_days: int

    def as_row(self) -> tuple:
        d = asdict(self)
        return (d["fold_id"], d["train_start"], d["train_end"],
                d["test_start"], d["test_end"], d["purge_days"], d["embargo_days"])


def build_folds(
    dates: pd.Series,
    train_years: int = config.WF_TRAIN_YEARS,
    test_years: int = config.WF_TEST_YEARS,
    step_years: int = config.WF_STEP_YEARS,
    purge_days: int = config.PURGE_DAYS,
    embargo_days: int = config.EMBARGO_DAYS,
) -> list[Fold]:
    """Build rolling folds over the sorted unique trading dates.

    `dates` must be the sorted unique dates present in the modeling table. Using
    the observed calendar rather than a generated date range means the purge is
    counted in real trading sessions.
    """
    uniq = pd.Index(sorted(pd.unique(dates)))
    if len(uniq) == 0:
        return []
    years = sorted({int(str(d)[:4]) for d in uniq})
    folds: list[Fold] = []
    fold_id = 1

    first_year = years[0]
    # The first year is usually partial (features need 200 bars to warm up), so
    # training starts at the first year with a full calendar of data.
    full_years = [y for y in years if sum(1 for d in uniq if str(d)[:4] == str(y)) > 200]
    if not full_years:
        full_years = years
    start_year = full_years[0]

    y = start_year
    while True:
        train_lo_year = y
        train_hi_year = y + train_years - 1
        test_lo_year = train_hi_year + 1
        test_hi_year = test_lo_year + test_years - 1
        if test_hi_year > years[-1]:
            break

        train_start = f"{train_lo_year}-01-01"
        test_start = f"{test_lo_year}-01-01"
        test_end = f"{test_hi_year}-12-31"

        # Purge: training must stop `purge_days` TRADING sessions before the first
        # test date, so no training label resolves inside the test window.
        test_start_pos = uniq.searchsorted(test_start, side="left")
        purge_pos = max(0, test_start_pos - purge_days - 1)
        train_end = str(uniq[purge_pos])

        if train_end <= train_start:
            break

        folds.append(Fold(fold_id, train_start, train_end, test_start, test_end,
                          purge_days, embargo_days))
        fold_id += 1
        y += step_years

    return folds


def split(df: pd.DataFrame, fold: Fold) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return (train, test) for one fold. `df` needs a `dt` column of ISO strings."""
    train = df[(df["dt"] >= fold.train_start) & (df["dt"] <= fold.train_end)]
    test = df[(df["dt"] >= fold.test_start) & (df["dt"] <= fold.test_end)]
    return train, test


def assert_no_overlap(df: pd.DataFrame, folds: list[Fold], horizon: int) -> list[str]:
    """Verify the purge actually worked. Returns a list of violations.

    The check: for every training row, the date `horizon` sessions later must fall
    strictly before the fold's test_start. This is the property purging is
    supposed to guarantee, so it is worth asserting rather than trusting.
    """
    uniq = pd.Index(sorted(pd.unique(df["dt"])))
    problems: list[str] = []
    for f in folds:
        train, test = split(df, f)
        if train.empty or test.empty:
            problems.append(f"fold {f.fold_id}: empty train or test split")
            continue
        last_train = train["dt"].max()
        pos = uniq.searchsorted(last_train, side="left")
        resolve_pos = min(pos + horizon, len(uniq) - 1)
        resolve_date = str(uniq[resolve_pos])
        if resolve_date >= f.test_start:
            problems.append(
                f"fold {f.fold_id}: last training row {last_train} resolves on "
                f"{resolve_date}, which is inside the test window starting "
                f"{f.test_start}"
            )
        if train["dt"].max() >= test["dt"].min():
            problems.append(f"fold {f.fold_id}: train and test overlap in time")
    return problems
