#!/usr/bin/env python3
"""Reverse-split round-up: forward signal ledger (trial 60, Phase 5, part 1).
See docs/REVERSE_SPLIT_ROUNDUP.md, "Phase 5: forward plan".

    python3 python/rsplit_ledger.py run              # discover, parse, append changes
    python3 python/rsplit_ledger.py run --lookback 120   # first run: seed four months
    python3 python/rsplit_ledger.py show             # the latest row of every signal
    python3 python/rsplit_ledger.py summary          # counts only (safe for reports/)

What it does, each run:
1. finds companies whose recent EDGAR filings mention a reverse split
   (EDGAR full-text search, the forms rule v2 reads);
2. keeps those listed on NYSE, Nasdaq or NYSE American (EDGAR's own
   exchange field; CRSP isn't available forward);
3. reads each company's v2 filings from the last 120 days with the FROZEN
   rule (reverse_split.parse_filing_v2, timeline, known_at, entry_day:
   imported, never changed);
4. writes one ledger row per announced split when it is new or when what is
   known about it changed (ratio, effective date, class, planned entry day,
   cancellation). Rows are only ever appended.

No prices are read. The ledger (EDGAR-derived rows) stays in
data/rsplit/forward/, which is git-ignored; `summary` prints counts only.

Caveat, by construction: the entry day is the last trading day whose trade
settles before the effective date known that day. A run that is not made
on that day records the plan, not the entry; run it every trading day so
that a short announcement-to-split window isn't missed.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.parse
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "python"))

import reverse_split as rs

OUT = rs.CACHE / "forward"
LEDGER = OUT / "ledger.csv"
EFTS = "https://efts.sec.gov/LATEST/search-index"
QUERIES = ('"reverse stock split"', '"reverse split"', '"share consolidation"')
SEARCH_FORMS = "8-K,8-K/A,DEF 14A,DEF 14C,PRE 14A,PRE 14C,424B1,424B2,424B3,424B4,424B5,S-1,S-1/A,S-3,S-3/A"
EXCHANGES = {"NYSE", "Nasdaq", "NYSE American", "NYSE MKT"}     # CRSP exchcd 1-3; not Arca, OTC or Cboe
COLUMNS = ["run_date", "cik", "company", "tickers", "exchange", "announcement_acc", "announcement_usable",
           "ratio", "effective_known", "cls", "class_acc", "planned_entry_day", "cancelled", "status", "rule"]
TRACKED = ["ratio", "effective_known", "cls", "planned_entry_day", "cancelled", "status"]

# NYSE full-day closures after the Fama-French calendar ends (published by NYSE)
NYSE_HOLIDAYS = {
    "2026-01-01", "2026-01-19", "2026-02-16", "2026-04-03", "2026-05-25", "2026-06-19", "2026-07-03",
    "2026-09-07", "2026-11-26", "2026-12-25",
    "2027-01-01", "2027-01-18", "2027-02-15", "2027-03-26", "2027-05-31", "2027-06-18", "2027-07-05",
    "2027-09-06", "2027-11-25", "2027-12-24",
}
CAL_END = "2027-12-31"


def calendar() -> np.ndarray:
    """The frozen calendar's trading days, then NYSE trading days to CAL_END."""
    past = rs.calendar()
    start = pd.Timestamp(str(past[-1])) + pd.Timedelta(days=1)
    fut = [d.strftime("%Y-%m-%d") for d in pd.bdate_range(start, CAL_END)]
    return np.array([str(d) for d in past] + [d for d in fut if d not in NYSE_HOLIDAYS])


def use_forward_calendar() -> None:
    rs.CAL = calendar()


# ------------------------------------------------------------------ discovery

def search(q: str, start: str, end: str) -> list[dict]:
    """EDGAR full-text search hits for one phrase, every page."""
    import edgar_tenders as et
    hits, frm = [], 0
    while True:
        url = EFTS + "?" + urllib.parse.urlencode({"q": q, "dateRange": "custom", "startdt": start, "enddt": end,
                                                   "forms": SEARCH_FORMS, "from": frm})
        j = json.loads(et.fetch(url))["hits"]
        page = j["hits"]
        hits += [h["_source"] for h in page]
        frm += len(page)
        if not page or frm >= min(j["total"]["value"], 9900):
            return hits


def candidates(start: str, end: str) -> list[str]:
    ciks = set()
    for q in QUERIES:
        for s in search(q, start, end):
            ciks.update(str(int(c)) for c in s.get("ciks", []))
    return sorted(ciks, key=int)


def company(cik: str, run_date: str) -> tuple[dict, pd.DataFrame]:
    """Fresh submissions for one company (cached for this run date only):
    its name, tickers and exchanges, and its recent filings."""
    import edgar_tenders as et
    p = OUT / "submissions" / run_date / f"CIK{int(cik):010d}.json"
    if not p.exists():
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(et.fetch(f"https://data.sec.gov/submissions/CIK{int(cik):010d}.json"))
    j = json.loads(p.read_text())
    rec = j["filings"]["recent"]
    f = pd.DataFrame({k: rec.get(k, [None] * len(rec["form"])) for k in
                      ("form", "filingDate", "acceptanceDateTime", "accessionNumber", "items", "primaryDocument")})
    f["accepted"] = f["acceptanceDateTime"].astype(str).str[:10]
    info = {"company": j.get("name", ""), "tickers": ",".join(t for t in j.get("tickers", []) if t),
            "exchanges": [e for e in j.get("exchanges", []) if e]}
    return info, f


# ------------------------------------------------------------------ the frozen rule, applied today

def parse_company(cik: str, f: pd.DataFrame, run_date: str) -> pd.DataFrame:
    """Every v2 filing of the last BEFORE days, parsed by the frozen rule."""
    lo = (pd.Timestamp(run_date) - pd.Timedelta(days=rs.BEFORE)).strftime("%Y-%m-%d")
    f = f[f["form"].fillna("").str.match(rs.V2_FORMS) & (f["filingDate"] >= lo) & (f["filingDate"] <= run_date)]
    rows = []
    for r in f.rename(columns={"accessionNumber": "acc", "filingDate": "filed"}).to_dict("records"):
        r.update(cik=cik, permno=int(cik), exdt=run_date)
        rows.append(rs._parse_one_v2(r))
    return pd.DataFrame(rows)


def planned_entry(tl: list[dict], today: str, effective: str) -> str | None:
    """The entry day the frozen rule would choose, from today on, if nothing
    more were filed: entry_day over the trading days before the effective date."""
    days = [str(d) for d in rs.CAL if today <= str(d) < effective]
    d, _ = rs.entry_day(tl, days)
    return d


def signal(parsed: pd.DataFrame, today: str) -> dict | None:
    """What the frozen rule knows today about one company's split, or None
    if no split is announced (a single ratio and a future effective date)."""
    if parsed.empty:
        return None
    tl = rs.timeline(parsed, "v2")
    k = rs.known_at(tl, today)
    if not k["announced"]:
        return None
    ann = next(f for f in tl if f["ratio"] and f["effective"])
    cls_f = next((f for f in reversed(tl) if f["usable"] <= today and f["cls"] != "UNKNOWN"), None)
    eff = k["effective"]
    plan = planned_entry(tl, today, eff) if eff and eff > today and not k["cancelled"] else None
    if k["cancelled"]:
        status = "cancelled"
    elif not eff or eff <= today:
        status = "past"                                     # the split's effective date has come
    elif k["cls"] not in rs.ROUNDUP:
        status = "not_roundup"                              # announced; class cash, down or unknown today
    elif plan is None:
        status = "no_entry"                                 # no day left that settles before the split
    elif plan == today:
        status = "entry_today"
    else:
        status = "upcoming"
    return {"announcement_acc": ann["acc"], "announcement_usable": ann["usable"], "ratio": k["ratio"],
            "effective_known": eff, "cls": k["cls"], "class_acc": cls_f["acc"] if cls_f else None,
            "planned_entry_day": plan, "cancelled": bool(k["cancelled"]), "status": status, "rule": "v2"}


# ------------------------------------------------------------------ the ledger (append-only)

def read_ledger() -> pd.DataFrame:
    if not LEDGER.exists():
        return pd.DataFrame(columns=COLUMNS)
    return pd.read_csv(LEDGER, dtype=str, keep_default_na=False)


def latest(led: pd.DataFrame) -> pd.DataFrame:
    return led.groupby(["cik", "announcement_acc"], sort=False).tail(1) if len(led) else led


def _norm(v) -> str:
    if v is None or (isinstance(v, float) and v != v):
        return ""
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return str(v)


def append(rows: list[dict], path: Path = LEDGER) -> int:
    """Append each row that is new, or whose tracked fields differ from the
    latest row for the same (cik, announcement). Earlier rows are never
    rewritten. Returns the number appended."""
    led = pd.read_csv(path, dtype=str, keep_default_na=False) if path.exists() else pd.DataFrame(columns=COLUMNS)
    last = {(r["cik"], r["announcement_acc"]): r for r in latest(led).to_dict("records")}
    new = []
    for r in rows:
        r = {c: _norm(r.get(c)) for c in COLUMNS}
        prev = last.get((r["cik"], r["announcement_acc"]))
        if prev is None or any(prev[c] != r[c] for c in TRACKED):
            new.append(r)
            last[(r["cik"], r["announcement_acc"])] = r
    if new:
        path.parent.mkdir(parents=True, exist_ok=True)
        pd.DataFrame(new, columns=COLUMNS).to_csv(path, mode="a", header=not path.exists(), index=False)
    return len(new)


def run(lookback: int, today: str | None = None) -> int:
    use_forward_calendar()
    today = today or date.today().isoformat()
    start = (pd.Timestamp(today) - pd.Timedelta(days=lookback)).strftime("%Y-%m-%d")
    # companies already being followed stay in scope until their split passes
    followed = set(latest(read_ledger()).query("status in ['upcoming', 'not_roundup', 'entry_today']")["cik"])
    ciks = sorted(set(candidates(start, today)) | followed, key=int)
    print(f"{today}: {len(ciks)} companies to read ({len(followed)} already followed)", flush=True)
    rows, skipped = [], 0
    for i, cik in enumerate(ciks, 1):
        try:
            info, f = company(cik, today)
            if not set(info["exchanges"]) & EXCHANGES:
                skipped += 1
                continue
            s = signal(parse_company(cik, f, today), today)
        except Exception as e:                            # recorded, never silently dropped
            print(f"  CIK {cik}: {type(e).__name__}: {e}", flush=True)
            continue
        if s:
            rows.append({"run_date": today, "cik": cik, "company": info["company"], "tickers": info["tickers"],
                         "exchange": ",".join(info["exchanges"]), **s})
        if i % 50 == 0:
            print(f"  {i}/{len(ciks)}", flush=True)
    n = append(rows)
    (OUT / "runs.jsonl").open("a").write(json.dumps(
        {"run_date": today, "at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "lookback": lookback,
         "companies": len(ciks), "off_exchange": skipped, "announced": len(rows), "appended": n,
         "python_md5": rs.python_tree_md5()}) + "\n")
    print(f"{len(rows)} announced splits; {n} ledger rows appended; {skipped} companies off NYSE/Nasdaq/NYSE American")
    return 0


def summary() -> pd.DataFrame:
    """Counts only: the latest status of every signal, by month announced."""
    lat = latest(read_ledger())
    if lat.empty:
        return pd.DataFrame()
    lat = lat.assign(month=lat["announcement_usable"].str[:7])
    return lat.pivot_table(index="month", columns="status", values="cik", aggfunc="count", fill_value=0)


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--lookback", type=int, default=14, help="calendar days of full-text search")
    r.add_argument("--today", default=None, help=argparse.SUPPRESS)
    sub.add_parser("show")
    sub.add_parser("summary")
    a = ap.parse_args()
    if a.cmd == "run":
        return run(a.lookback, a.today)
    pd.set_option("display.width", 220)
    if a.cmd == "show":
        lat = latest(read_ledger())
        print(lat[["cik", "tickers", "ratio", "effective_known", "cls", "planned_entry_day", "status"]].to_string())
    else:
        print(summary().to_string())
    return 0


if __name__ == "__main__":
    sys.exit(main())
