#!/usr/bin/env python3
"""Trial 60 report format: dollars a year over the years each row covers.

    python3 python/rsplit_report.py            # rewrites both trial 60 reports

rsplit_trial60.score() divided every row's dollar total by the whole
period's length (7 years for the confirmation, 9 for the design), so a
one-year row or the 2024-2025 row understated its own dollars a year. This
helper recomputes usd_per_yr from each row's own aggregates,

    usd_per_yr = events x mean / years_covered,

with years_covered 1 for a year_YYYY row, 2 for ex_2024_2025, and the
period's length otherwise. It reads and writes only the aggregate CSVs in
reports/: no payoff, price or frozen function is touched, and the pass
checks don't use usd_per_yr. docs/REVERSE_SPLIT_ROUNDUP.md records the
correction.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
PERIOD_YEARS = {"design": 9, "confirm": 7}          # 2010-2018, 2019-2025


def years_covered(row: str, period: str) -> int:
    if row.startswith("year_"):
        return 1
    if row == "ex_2024_2025":
        return 2
    return PERIOD_YEARS[period]


def fix(r: pd.DataFrame) -> pd.DataFrame:
    """r read as text: every other cell is written back exactly as it was."""
    r = r.copy()
    period = r["period"].iloc[0]
    yc = pd.Series([years_covered(x, period) for x in r["row"]], index=r.index)
    n, m = pd.to_numeric(r["events"]), pd.to_numeric(r["mean"])
    usd = (n * m).where(n > 0, 0.0) / yc
    r["usd_per_yr"] = usd.map(repr)
    r.insert(r.columns.get_loc("usd_per_yr"), "years_covered", yc.astype(str))
    return r


def main() -> int:
    for period in PERIOD_YEARS:
        p = ROOT / "reports" / f"rsplit_trial60_{period}.csv"
        if p.exists():
            fix(pd.read_csv(p, dtype=str, keep_default_na=False)).to_csv(p, index=False)
            print(f"rewrote {p.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
