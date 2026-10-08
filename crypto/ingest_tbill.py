#!/usr/bin/env python3
"""3-month T-bill yield from FRED (DTB3), the hurdle the carry has to clear.

    python3 crypto/ingest_tbill.py

A delta-neutral carry ties up capital in the spot leg and margin in the perp leg.
That capital could have sat in T-bills for 4-5% with no venue risk at all, so the
number that matters is the carry in excess of that, not the carry above zero.
"""

from __future__ import annotations

import io
import sys
import urllib.request
from pathlib import Path

import pandas as pd

URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=DTB3"
OUT = Path(__file__).resolve().parent / "data" / "tbill.parquet"


def main() -> int:
    with urllib.request.urlopen(URL, timeout=60) as f:
        d = pd.read_csv(io.StringIO(f.read().decode()))
    d.columns = ["date", "yld"]
    d["date"] = pd.to_datetime(d["date"])
    d["yld"] = pd.to_numeric(d["yld"], errors="coerce") / 100
    d = d.dropna()
    d = d[d.date >= "2023-01-01"]
    d.to_parquet(OUT, index=False)
    print(f"tbill: {len(d):,} days, {d.date.min().date()} to {d.date.max().date()}, "
          f"latest {d.yld.iloc[-1]:.2%}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
