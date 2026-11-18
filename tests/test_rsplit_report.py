"""Trial 60 report format (python/rsplit_report.py): dollars a year over the
years each row covers, other cells untouched."""

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "python"))

import rsplit_report as rr


def test_each_row_is_divided_by_its_own_years():
    assert rr.years_covered("year_2024", "confirm") == 1
    assert rr.years_covered("ex_2024_2025", "confirm") == 2
    assert rr.years_covered("primary", "confirm") == 7
    assert rr.years_covered("primary", "design") == 9


def test_usd_per_yr_is_events_times_mean_over_years_and_nothing_else_moves():
    r = pd.DataFrame({"row": ["primary", "ex_2024_2025", "year_2024", "year_2021"], "period": ["confirm"] * 4,
                      "events": ["121", "88", "45", "0"], "mean": ["3.5", "2.5", "2.0", ""],
                      "t": ["2.370115167911862e-40", "1", "2", ""], "usd_per_yr": ["1", "1", "1", "1"]})
    o = rr.fix(r)
    assert [round(float(x), 6) for x in o["usd_per_yr"]] == [121 * 3.5 / 7, 88 * 2.5 / 2, 90.0, 0.0]
    assert list(o["years_covered"]) == ["7", "2", "1", "1"]
    assert list(o["t"]) == list(r["t"])                       # text kept exactly
    assert list(o.columns) == ["row", "period", "events", "mean", "t", "years_covered", "usd_per_yr"]
