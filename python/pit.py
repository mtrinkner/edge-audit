"""Point-in-time data layer. See docs/INFRASTRUCTURE.md.

Every row of every dataset carries `known_at`, the first moment a trader
could use it. Studies read data only through `PIT.asof(name, t, ...)` or a
`View` bounded at t, which return rows with known_at <= t. Nothing else in a
study reads data.

Times are naive exchange-local timestamps. A trading day has two moments that
matter: OPEN (09:30) and CLOSE (16:00).

known_at rules (one function per dataset kind, below):
- CRSP daily: the close of its date.
- EDGAR filings and XBRL facts: the open of the trading day after the
  acceptance (or filing) date. A filing accepted on a non-trading day is
  usable from the next trading day's open. This is reverse_split.timeline's
  "usable" rule.
- IBES: an announcement time before the 16:00 close on a trading day is
  known at that time; at or after 16:00, or with no time (00:00), it is
  known at the next trading day's open.
- Fama-French factors: the library publishes the daily file once a month,
  after the month ends. known_at = the close of the last trading day of the
  month after the factor's month (conservative).
- T-bills: the one-month bill return for month m is fixed by the bill's
  yield when bought at the start of month m. known_at = the open of the
  first trading day of month m. This is the rate a simulation may accrue
  on cash: the rate known at t, never the path that followed.

Revisions: a dataset with `version_key` keeps every version of a value, each
with its own known_at. `asof` returns, per version key, the latest version
known at t.
"""

from __future__ import annotations

import builtins
import contextlib
import io
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
OPEN, CLOSE = "09:30", "16:00"


# ------------------------------------------------------------------ calendar

class Calendar:
    """Trading days (ISO strings)."""

    def __init__(self, days):
        self.days = np.array(sorted(str(d) for d in days))

    @classmethod
    def default(cls) -> "Calendar":
        ff = pd.read_parquet(ROOT / "data" / "pead" / "ff_daily.parquet", columns=["date"])
        return cls(ff["date"].astype(str))

    def next_on_or_after(self, d: str) -> str:
        i = np.searchsorted(self.days, d)
        return str(self.days[min(i, len(self.days) - 1)])

    def shift(self, d: str, n: int) -> str:
        i = np.searchsorted(self.days, d)
        return str(self.days[min(max(i + n, 0), len(self.days) - 1)])

    def last_on_or_before(self, d: str) -> str:
        i = np.searchsorted(self.days, d, side="right") - 1
        return str(self.days[max(i, 0)])

    def is_day(self, d: str) -> bool:
        i = np.searchsorted(self.days, d)
        return i < len(self.days) and self.days[i] == d

    def usable_after(self, d: str) -> str:
        """The trading day after d if d is one, else the next trading day."""
        return self.shift(d, 1) if self.is_day(d) else self.next_on_or_after(d)

    def range(self, lo: str, hi: str) -> list[str]:
        return [str(x) for x in self.days[(self.days >= lo) & (self.days <= hi)]]


def at(d: str, hhmm: str) -> pd.Timestamp:
    return pd.Timestamp(f"{d} {hhmm}")


# ------------------------------------------------------------------ known_at rules

def known_at_close(dates: pd.Series) -> pd.Series:
    """CRSP daily: the close of its date."""
    return pd.to_datetime(dates.astype(str) + " " + CLOSE)


def known_at_next_open(dates: pd.Series, cal: Calendar) -> pd.Series:
    """EDGAR filings and XBRL facts: the open of the trading day after the
    acceptance or filing date."""
    return pd.to_datetime(dates.astype(str).map(cal.usable_after) + " " + OPEN)


def known_at_ibes(anndats: pd.Series, anntims: pd.Series, cal: Calendar) -> pd.Series:
    """Before the close on a trading day: at that time. At or after 16:00, no
    time (00:00 or missing), or a non-trading day: the next trading day's open."""
    d = anndats.astype(str).str[:10]
    tm = anntims.fillna("00:00:00").astype(str).str[:5]
    intraday = (tm > "00:00") & (tm < CLOSE) & d.map(cal.is_day)
    nxt = d.map(cal.usable_after)
    return pd.to_datetime(np.where(intraday, d + " " + tm, nxt + " " + OPEN))


def known_at_ff(dates: pd.Series, cal: Calendar) -> pd.Series:
    """Fama-French factors: the close of the last trading day of the following month."""
    m = pd.to_datetime(dates.astype(str)).dt.to_period("M") + 1
    last = m.dt.to_timestamp(how="end").dt.strftime("%Y-%m-%d")
    return pd.to_datetime(last.map(cal.last_on_or_before) + " " + CLOSE)


def known_at_tbill(dates: pd.Series, cal: Calendar) -> pd.Series:
    """T-bills: the open of the first trading day of the month."""
    first = pd.to_datetime(dates.astype(str)).dt.to_period("M").dt.to_timestamp().dt.strftime("%Y-%m-%d")
    return pd.to_datetime(first.map(cal.next_on_or_after) + " " + OPEN)


# ------------------------------------------------------------------ datasets

@dataclass
class Dataset:
    """One dataset: rows with a known_at column.

    keys:        columns asof() can filter on quickly (an index is built on them).
    event_time:  the column saying when the row's event happened (a price's
                 date, a filing's acceptance). The harness scrambles rows whose
                 event_time is after t, which catches a known_at that is set too
                 early.
    value_cols:  the columns the scramble test permutes.
    version_key: if set, several rows per key are versions of one value.
    lookahead:   a reason, if the dataset is knowingly not point-in-time (a
                 study's conditioning set). The harness reports it and skips it.
    """
    name: str
    frame: pd.DataFrame
    keys: tuple[str, ...] = ()
    event_time: str | None = None
    value_cols: tuple[str, ...] = ()
    version_key: tuple[str, ...] = ()
    lookahead: str | None = None
    doc: str = ""
    _index: dict = field(default_factory=dict, repr=False)

    def __post_init__(self):
        f = self.frame
        if "known_at" not in f:
            raise ValueError(f"{self.name}: every row needs known_at")
        if not np.issubdtype(f["known_at"].dtype, np.datetime64):
            raise ValueError(f"{self.name}: known_at must be a timestamp")
        self.frame = f.sort_values("known_at", kind="stable").reset_index(drop=True)
        self._ka = self.frame["known_at"].values
        if self.keys:
            g = self.frame.groupby(list(self.keys), sort=False).indices
            self._index = {k if isinstance(k, tuple) else (k,): np.asarray(v) for k, v in g.items()}

    def rows(self, t: pd.Timestamp, filters: dict, latest: bool = False) -> pd.DataFrame:
        f = self.frame
        key_f = {k: v for k, v in filters.items() if k in self.keys}
        other = {k: v for k, v in filters.items() if k not in self.keys}
        if self.keys and len(key_f) == len(self.keys) and all(np.isscalar(v) for v in key_f.values()):
            idx = self._index.get(tuple(key_f[k] for k in self.keys))
            if idx is None:
                return f.iloc[0:0]
            n = np.searchsorted(self._ka[idx], np.datetime64(t), side="right")
            idx = idx[:n]
            if latest and len(idx) and not self.version_key:
                idx = idx[-1:]
            sub = f.iloc[idx]
        else:
            n = np.searchsorted(self._ka, np.datetime64(t), side="right")
            sub = f.iloc[:n]
            for k, v in key_f.items():
                sub = sub[sub[k].isin(v) if not np.isscalar(v) else sub[k] == v]
        for k, v in other.items():
            sub = sub[sub[k].isin(v) if not np.isscalar(v) else sub[k] == v]
        if self.version_key:
            sub = sub.groupby(list(self.version_key), sort=False).tail(1)
        if latest and not self.version_key and len(sub) > 1 and not self.keys:
            sub = sub.iloc[-1:]
        return sub


class PIT:
    """A set of datasets read only as of a time."""

    def __init__(self, datasets: list[Dataset], calendar: Calendar | None = None):
        self.ds = {d.name: d for d in datasets}
        self.cal = calendar

    def asof(self, name: str, t: pd.Timestamp, latest: bool = False, **filters) -> pd.DataFrame:
        """Rows of `name` with known_at <= t (and equal to each filter)."""
        return self.ds[name].rows(pd.Timestamp(t), filters, latest)

    def view(self, t: pd.Timestamp) -> "View":
        return View(self, pd.Timestamp(t))

    # ---- harness transforms
    def truncated(self, t: pd.Timestamp) -> "PIT":
        """Every row with known_at > t removed."""
        t = pd.Timestamp(t)
        return PIT([_replace(d, d.frame[d.frame["known_at"] <= t]) for d in self.ds.values()], self.cal)

    def scrambled(self, t: pd.Timestamp, seed: int = 0) -> "PIT":
        """Values of every row whose event happened after t permuted within
        its dataset. A study that sees only data known at t can't tell."""
        t = pd.Timestamp(t)
        rng = np.random.default_rng(seed)
        out = []
        for d in self.ds.values():
            f = d.frame.copy()
            if d.lookahead is None and d.event_time and d.value_cols:
                et = pd.to_datetime(f[d.event_time].astype(str).str[:10])
                late = np.flatnonzero((et > t.normalize()).values)
                if len(late) > 1:
                    perm = rng.permutation(late)
                    for c in d.value_cols:
                        f.loc[f.index[late], c] = f[c].values[perm]
            out.append(_replace(d, f))
        return PIT(out, self.cal)


def _replace(d: Dataset, frame: pd.DataFrame) -> Dataset:
    return Dataset(d.name, frame, d.keys, d.event_time, d.value_cols, d.version_key, d.lookahead, d.doc)


class View:
    """The data a decision at time t may see: asof(name, ...) at t, nothing else."""

    def __init__(self, pit: PIT, t: pd.Timestamp):
        self._pit, self.t = pit, t
        self.cal = pit.cal

    def asof(self, name: str, latest: bool = False, **filters) -> pd.DataFrame:
        return self._pit.asof(name, self.t, latest=latest, **filters)


# ------------------------------------------------------------------ sealing a decision

@contextlib.contextmanager
def sealed():
    """No file or network reads while a decision runs: data comes from the view."""
    def refuse(*a, **k):
        raise PermissionError("decide() may read data only through pit.asof (file access refused)")
    saved = (builtins.open, io.open, pd.read_csv, pd.read_parquet, pd.read_json)
    builtins.open = io.open = refuse
    pd.read_csv = pd.read_parquet = pd.read_json = refuse
    try:
        yield
    finally:
        builtins.open, io.open, pd.read_csv, pd.read_parquet, pd.read_json = saved


# ------------------------------------------------------------------ datasets on disk

def crsp_daily(path: Path, cal: Calendar | None = None) -> Dataset:
    p = pd.read_parquet(path)
    p["dlycaldt"] = p["dlycaldt"].astype(str)
    p["known_at"] = known_at_close(p["dlycaldt"])
    return Dataset("crsp_daily", p, keys=("permno",), event_time="dlycaldt",
                   value_cols=("dlyprc", "dlyopen", "dlyclose", "dlybid", "dlyask", "dlyvol"),
                   doc="CRSP daily stock file; known at the close of its date")


def tbill(cal: Calendar) -> Dataset:
    ff = pd.read_parquet(ROOT / "data" / "pead" / "ff_daily.parquet")
    ff["date"] = ff["date"].astype(str)
    ff["known_at"] = known_at_tbill(ff["date"], cal)
    return Dataset("tbill", ff[["date", "rf", "known_at"]], keys=("date",), event_time=None,
                   doc="daily one-month T-bill return (FF rf), known at the open of its month")


def ff_factors(path: Path, cal: Calendar) -> Dataset:
    f = pd.read_parquet(path)
    f["date"] = f["date"].astype(str)
    f["known_at"] = known_at_ff(f["date"], cal)
    cols = tuple(c for c in f.columns if c not in ("date", "known_at"))
    return Dataset("ff_factors", f, keys=(), event_time="date", value_cols=cols,
                   doc="Fama-French daily factors, known a month after their month")
