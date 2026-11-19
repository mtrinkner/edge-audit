#!/usr/bin/env python3
"""Reverse-split round-ups (trial 60) on the shared layer: a regression port.

    python3 python/studies/rsplit_pit.py confirm      # compare with trial 60's frozen trades
    python3 python/studies/rsplit_pit.py design

The parsing, the timeline, known_at and the entry rule are the frozen
reverse_split functions, imported unchanged. What is new is how data is read
(pit.py) and how orders fill (sim.py). The port must reproduce trial 60's
trades file exactly; any difference is a bug in the new layer until shown
otherwise.

Inputs: the frozen parse output (data/rsplit/parsed_v2_<period>.csv) and
event table. For the confirmation period, each must carry a manifest naming
the trial60-frozen commit and the md5 of python/ AT THE TAG. Today's python/
no longer matches the tag, so trial 60 itself can only be rerun from a
checkout of the tag; this port reads the tag's outputs instead.

Declared look-ahead (not hidden): the event universe is CRSP's list of
reverse splits that happened, with their actual ex dates. Trial 60 chose
which filings to read, the entry window and the exit timing from it, so a
cancelled split never enters the sample. The dataset is registered with
lookahead set, the harness reports it and skips it, and every other dataset
is tested.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "python"))

import pit as P
import report as R
import reverse_split as rs
import sim as S

CACHE = rs.CACHE
WINDOW_BEFORE, WINDOW_AFTER = 100, 15        # reverse_split.simulate's quote window (trading days)
EXIT_LAG = rs.EXIT_LAG
UNIVERSE_LOOKAHEAD = ("trial 60's universe: CRSP reverse splits that happened, with their actual ex dates; "
                      "filing scope, entry window and exit timing are keyed to it (cancelled splits absent)")


# ------------------------------------------------------------------ frozen inputs, verified

def _git(*a) -> str:
    return subprocess.run(["git", *a], cwd=ROOT, capture_output=True, text=True, check=True).stdout


def tag_python_md5() -> str:
    """reverse_split.python_tree_md5's algorithm, over python/*.py as they are at the tag."""
    h = hashlib.md5()
    names = sorted(n.split("/")[-1] for n in _git("ls-tree", "--name-only", f"{rs.FROZEN_TAG}:python").split()
                   if n.endswith(".py"))
    for n in names:
        h.update(n.encode() + b"\0" + subprocess.run(["git", "show", f"{rs.FROZEN_TAG}:python/{n}"], cwd=ROOT,
                                                      capture_output=True, check=True).stdout + b"\0")
    return h.hexdigest()


def frozen_input(path: Path, period: str) -> pd.DataFrame:
    """A trial 60 output, read only if (for the confirmation) its manifest
    shows the tag's code wrote it."""
    if period == "confirm":
        m = json.loads(Path(str(path) + rs.MANIFEST).read_text())
        if m.get("commit") != rs.frozen_commit() or m.get("python_md5") != tag_python_md5():
            raise SystemExit(f"{path.name}: not produced by {rs.FROZEN_TAG}")
    return pd.read_csv(path, dtype={"cik": str, "items": str})


def load(period: str) -> P.PIT:
    cal = P.Calendar.default()
    rs.CAL = cal.days                                    # the frozen functions' calendar is the same one
    lo, hi = rs.SPLIT[period]
    hi = min(hi, rs.da.guard("reverse_split"))
    e = frozen_input(CACHE / f"event_table_v2_{period}.csv", period)
    e = e[(e["exdt"] >= lo) & (e["exdt"] <= hi)].copy()
    f = frozen_input(CACHE / f"parsed_v2_{period}.csv", period)
    f["accepted"] = f["accepted"].astype(str)
    f["known_at"] = P.known_at_next_open(f["accepted"], cal)
    uni = e.assign(known_at=pd.Timestamp("1900-01-01"))
    acts = e[["permno", "exdt", "n_crsp"]].rename(columns={"n_crsp": "factor"})
    acts["known_at"] = pd.to_datetime(acts["exdt"] + " " + P.OPEN)
    return P.PIT([
        P.crsp_daily(CACHE / "crsp_daily.parquet"),
        P.Dataset("rsplit_filings", f, keys=("permno", "exdt"), event_time="accepted",
                  value_cols=("frac_class", "ratio", "ratio_is_range", "effective", "cancel"),
                  doc="trial 60's parsed filings (frozen parse), usable the trading day after acceptance"),
        P.Dataset("rsplit_universe", uni, keys=(), lookahead=UNIVERSE_LOOKAHEAD),
        P.Dataset("crsp_splits", acts, keys=("permno",), event_time="exdt", value_cols=("factor",),
                  doc="CRSP split factors, applied at the ex date's open"),
        P.tbill(cal),
    ], cal)


# ------------------------------------------------------------------ the study

class RoundUp:
    """decide(view, book): the frozen entry rule, day by day, and the exits."""

    def __init__(self, cal: P.Calendar, exit_mode: str = "close"):
        if exit_mode not in ("close", "open_mhs", "lag5"):
            raise ValueError(exit_mode)
        self.cal, self.exit_mode = cal, exit_mode
        self.ev, self.log = None, {}

    def _init(self, view: P.View) -> None:
        u = view.asof("rsplit_universe")
        self.ev = {}
        for r in u.itertuples():
            key = (int(r.permno), r.exdt)
            self.ev[key] = {"permno": int(r.permno), "exdt": r.exdt, "n_crsp": float(r.n_crsp),
                            "lo": self.cal.shift(r.exdt, -WINDOW_BEFORE), "hi": self.cal.shift(r.exdt, WINDOW_AFTER),
                            "state": "wait", "tl_n": -1, "tl": [], "rows_after": 0}

    def _tl(self, view: P.View, e: dict) -> list[dict]:
        f = view.asof("rsplit_filings", permno=e["permno"], exdt=e["exdt"])
        if len(f) != e["tl_n"]:
            e["tl"], e["tl_n"] = rs.timeline(f, "v2"), len(f)
        return e["tl"]

    def decide(self, view: P.View, book: S.Book) -> list[S.Order]:
        if self.ev is None:
            self._init(view)
        day = str(view.t.date())
        orders = []
        for key, e in self.ev.items():
            if e["state"] == "done" or day < e["lo"] or day > e["hi"]:
                continue
            lid = f"{key[0]}:{key[1]}"
            row = view.asof("crsp_daily", latest=True, permno=e["permno"])
            has_row = len(row) and row["dlycaldt"].iloc[-1] == day
            if e["state"] == "wait":
                if day >= e["exdt"] or not has_row:
                    continue
                tl = self._tl(view, e)
                d, k = rs.entry_day(tl, [day])
                if d is None:
                    continue
                if k["cls"] not in rs.ROUNDUP:
                    e["state"] = "done"
                    continue
                e.update(state="ordered", entry_day=d, k=k)
                orders.append(S.Order(e["permno"], "buy", qty=1.0, lot=lid, fill="close", tag="entry"))
                if self.exit_mode == "open_mhs":
                    orders.append(S.Order(e["permno"], "sell", lot=lid, fill="open_mhs", good_from=e["exdt"],
                                          first_row_only=True, tag="split"))
                continue
            if e["state"] == "ordered":
                if lid in book.lots:
                    e["state"] = "held"
                else:
                    e["state"] = "done"
                    self.log[key] = {"entry_day": e["entry_day"], "skipped": "no_valid_quote"}
                    continue
            if e["state"] == "held":
                if lid not in book.lots:
                    e["state"] = "done"
                    continue
                if day < e["exdt"]:
                    if e.get("changed"):
                        continue
                    k = e["k"]
                    for f in self._tl(view, e):
                        if f["usable"] == day and day > e["entry_day"] and (
                                f["cancel"] or (f["ratio"] and f["ratio"] != k["ratio"])
                                or (f["effective"] and f["effective"] != k["effective"])
                                or f["cls"] in ("CASH", "DOWN")):
                            e["changed"] = True
                            orders.append(S.Order(e["permno"], "sell", lot=lid, fill="close",
                                                  good_until=self.cal.shift(e["exdt"], -1),
                                                  cancel_on_action=True, tag="changed"))
                            break
                    continue
                # the split has happened (the action was applied at today's open)
                if has_row:
                    e["rows_after"] += 1
                if e.get("exit_placed"):
                    continue
                if self.exit_mode == "close" or (self.exit_mode == "open_mhs"):
                    if self.exit_mode == "close":
                        orders.append(S.Order(e["permno"], "sell", lot=lid, fill="close", good_until=e["hi"],
                                              tag="split"))
                    e["exit_placed"] = True
                elif self.exit_mode == "lag5":
                    if (has_row and e["rows_after"] == EXIT_LAG + 1) or day == e["hi"]:
                        orders.append(S.Order(e["permno"], "sell", lot=lid, fill="close", good_until=e["hi"],
                                              tag="split"))
                        e["exit_placed"] = True
        return orders


# ------------------------------------------------------------------ run and compare

def run(period: str, exit_mode: str = "close", pit: P.PIT | None = None) -> tuple[RoundUp, dict]:
    pit = pit or load(period)
    st = RoundUp(pit.cal, exit_mode)
    u = pit.asof("rsplit_universe", pd.Timestamp("2100-01-01"))
    start = pit.cal.shift(u["exdt"].min(), -WINDOW_BEFORE)
    end = pit.cal.shift(u["exdt"].max(), WINDOW_AFTER)
    res = S.Sim(pit, prices="crsp_daily", actions="crsp_splits", fractional="roundup").run(st, start, end)
    return st, res


def trades(period: str, pit: P.PIT | None = None) -> pd.DataFrame:
    """Trial 60's trades table, rebuilt from three simulator runs (exit at the
    close; at the open minus half the spread; five trading days later)."""
    pit = pit or load(period)
    st, main = run(period, "close", pit)
    _, op = run(period, "open_mhs", pit)
    _, l5 = run(period, "lag5", pit)
    px = lambda r: r["trades"].set_index("id")["exit_px"]
    op_px, l5_px = px(op), px(l5)
    lots = main["trades"].set_index("id")
    rows = []
    for key, e in st.ev.items():
        lid = f"{key[0]}:{key[1]}"
        base = {"permno": key[0], "exdt": key[1]}
        if key in st.log:
            rows.append({**base, **st.log[key]})
            continue
        if lid not in lots.index:
            continue
        l, k = lots.loc[lid], e["k"]
        out = {**base, "entry_day": e["entry_day"], "cls": k["cls"], "ratio_entry": k["ratio"] or e["n_crsp"],
               "eff_known": k["effective"], "ask": l["entry_px"]}
        if l["exit_tag"] == "changed":
            rows.append({**out, "exit": "changed", "exit_day": l["exit_day"], "rounded": l["exit_px"],
                         "cash": l["exit_px"], "settle_miss": False})
        elif l["exit_tag"] == "split":
            rows.append({**out, "exit": "split", "exit_day": l["exit_day"], "rounded": l["exit_px"],
                         "cash": l["exit_px"] / e["n_crsp"], "settle_miss": not bool(l["of_record"]),
                         "rounded_open": op_px.get(lid, np.nan), "rounded_5d": l5_px.get(lid, np.nan),
                         "n_crsp": e["n_crsp"]})
        else:
            rows.append({**out, "skipped": "no_exit_quote"})
    return pd.DataFrame(rows)


COLS = ["permno", "exdt", "entry_day", "cls", "ratio_entry", "eff_known", "ask", "exit", "exit_day", "rounded",
        "cash", "settle_miss", "rounded_open", "rounded_5d", "n_crsp", "skipped"]


def compare(port: pd.DataFrame, frozen: pd.DataFrame, tol: float = 1e-9) -> list[str]:
    """Every difference between the port's trades and trial 60's, one line each."""
    key = ["permno", "exdt"]
    a = port.reindex(columns=COLS).set_index(key).sort_index()
    b = frozen.reindex(columns=COLS).set_index(key).sort_index()
    diffs = [f"only in port: {k}" for k in a.index.difference(b.index)]
    diffs += [f"only in trial 60: {k}" for k in b.index.difference(a.index)]
    for k in a.index.intersection(b.index):
        for c in a.columns:
            x, y = a.at[k, c], b.at[k, c]
            if pd.isna(x) and pd.isna(y):
                continue
            if isinstance(y, (bool, np.bool_)) or c in ("settle_miss",):
                same = (not pd.isna(x)) and (not pd.isna(y)) and str(x).lower() == str(y).lower()
            else:
                try:
                    same = abs(float(x) - float(y)) <= tol * max(1.0, abs(float(y)))
                except (TypeError, ValueError):
                    same = str(x) == str(y)
            if not same:
                diffs.append(f"{k} {c}: port {x!r}, trial 60 {y!r}")
    return diffs


def summary(t: pd.DataFrame, period: str) -> pd.DataFrame:
    """The primary row through report.py, at the primary p."""
    t = t[t["skipped"].isna()] if "skipped" in t else t
    pnl = pd.Series([rs.value(r, rs.P_PRIMARY.get(r["cls"], 0.0)) for r in t.to_dict("records")], index=t.index)
    return R.event_table(pnl, t["exdt"], rs.SPLIT[period], capital=t["ask"])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("period", choices=list(rs.SPLIT))
    a = ap.parse_args()
    port = trades(a.period)
    frozen = pd.read_csv(CACHE / f"trades_v2_{a.period}.csv")   # comparison only; the port's inputs are verified
    d = compare(port, frozen)
    s = summary(port, a.period).set_index("row").loc["all"]
    print(f"port: {len(port)} rows; trial 60: {len(frozen)} rows; differences: {len(d)}")
    for x in d[:50]:
        print("  " + x)
    print(f"primary: {int(s['events'])} events, mean ${s['mean']:.4f}, clustered t {s['t']:.2f}")
    return 0 if not d else 1


if __name__ == "__main__":
    sys.exit(main())
