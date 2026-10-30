#!/usr/bin/env python3
"""One way in to market data, with the lockbox enforced (added 2026-10-26).

lockbox.guard is correct, but it only protects scripts that call it, and the
2026-10-26 audit found 18 studies that never did. overnight_auction.py scored a
sealed date into trial 44, and the intraday family read 2026-10-07 to 10-09
into trials 38 and 39. Every loader here takes a max_date (default: the day
before the seal), calls lockbox.guard on it, and returns only rows on or
before it, so a study that loads through this module cannot read sealed data.

    import data_access as da
    bars = da.bars("my_study")                         # daily stock bars
    spy = da.sql("my_study", "SELECT dt, adj_close FROM bars WHERE symbol='SPY'")
    h1 = da.parquet("my_study", "intraday_1h")          # hourly, by session date

Dates are compared as ISO strings or with pyarrow, never by building pandas
datetimes, which segfault on this machine's pandas 3 / Python 3.14 build.
"""

from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

sys.path.insert(0, str(Path(__file__).resolve().parent))

import config
import db
import lockbox

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"

# The column holding each parquet file's date, and how it is stored.
#   "session"  ISO date string        "ts"  tz-aware timestamp (exchange time)
#   "month"    "YYYY-MM" string        "dt"  ISO date string
DATE_COLUMNS = {
    "intraday_1h": "session", "intraday_trades": "session", "intraday_passive": "session",
    "vix": "session", "etf_1h_raw": "ts", "etf2_1h_raw": "ts",
    "edge_spreads": "month", "deletion_prices": "dt",
}


def default_max_date() -> str:
    """The last date a study may read: the day before the seal, or config.END_DATE."""
    st = lockbox.state()
    if st["sealed"]:
        return (date.fromisoformat(st["seal_from"]) - timedelta(days=1)).isoformat()
    return config.END_DATE.isoformat()


def guard(context: str, max_date: str | None = None) -> str:
    """Resolve max_date, raise if it reaches the seal, and return it."""
    md = max_date or default_max_date()
    lockbox.guard(md, context)
    return md


def sql(context: str, query: str, params: tuple = (), max_date: str | None = None,
        date_col: str = "dt", order_by: str | None = None) -> pd.DataFrame:
    """Run a query and keep only rows whose date_col is on or before max_date.

    The query is wrapped, so it can be any SELECT that returns date_col as an
    ISO date string (every date in the warehouse is stored that way). Give the
    ordering as order_by (output column names): an ORDER BY inside the wrapped
    query is not guaranteed to survive."""
    md = guard(context, max_date)
    wrapped = f"SELECT * FROM ({query}) WHERE {date_col} <= ?"
    if order_by:
        wrapped += f" ORDER BY {order_by}"
    with db.connect() as con:
        return pd.read_sql(wrapped, con, params=(*params, md))


def bars(context: str, columns: tuple = ("symbol", "dt", "open", "high", "low", "close",
                                         "adj_close", "volume"),
         kind: str | None = "stock", max_date: str | None = None) -> pd.DataFrame:
    """Daily bars through max_date, ordered by symbol and date."""
    cols = ", ".join(f"b.{c}" for c in columns)
    join = " JOIN symbols s ON s.symbol=b.symbol" if kind else ""
    where = " WHERE s.kind=?" if kind else ""
    q = f"SELECT {cols} FROM bars b{join}{where}"
    return sql(context, q, (kind,) if kind else (), max_date, order_by="symbol, dt")


def _keep(table: pa.Table, col: str, md: str) -> pa.Array:
    a = table.column(col)
    if pa.types.is_timestamp(a.type):
        # Local calendar date of each stamp (the column's own time zone).
        a = pc.strftime(a, format="%Y-%m-%d")
        return pc.less_equal(a, md)
    if col == "month":
        # A month is usable only once it is complete, so a partly sealed month
        # (computed from sealed days) is dropped with the rest.
        return pc.less(a, (date.fromisoformat(md) + timedelta(days=1)).isoformat()[:7])
    return pc.less_equal(pc.utf8_slice_codeunits(a.cast(pa.string()), 0, 10), md)


def parquet(context: str, name: str, max_date: str | None = None,
            columns: list[str] | None = None, date_col: str | None = None) -> pd.DataFrame:
    """Read data/<name>.parquet, keeping rows dated on or before max_date."""
    md = guard(context, max_date)
    col = date_col or DATE_COLUMNS[name]
    want = None if columns is None else list(dict.fromkeys([*columns, col]))
    t = pq.read_table(DATA / f"{name}.parquet", columns=want)
    t = t.filter(_keep(t, col, md))
    if columns is not None and col not in columns:
        t = t.drop([col])
    return t.to_pandas()
