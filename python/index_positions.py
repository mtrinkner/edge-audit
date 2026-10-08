#!/usr/bin/env python3
"""Detect new S&P 500 additions and emit the index_v1 book.

    python3 python/index_positions.py              # show what is live
    python3 python/index_positions.py --record     # append to the ledger

HOW EVENTS ARE DETECTED. There is no feed of index changes, so the current
constituent list is fetched and diffed against the last stored snapshot. Anything
present now and absent before is an addition. The snapshot is written after every
run, so the next diff is against this one.

That has a consequence worth stating: the FIRST run cannot detect anything,
because there is no prior snapshot to compare against. It establishes the
baseline. Real detection starts from the second run, which is why this is wired
into a monthly job rather than expected to produce a book today.

WHAT THE STRATEGY DOES. Short each new addition at the close of the first session
on or after its effective date, hold ten sessions, hedge with an equal dollar
amount of SPY. The backtest found 1.20% per event with t = 2.18 over 148 events,
against a luck bar of 1.63 and a deflated probability of 0.041. It fails that
bar. It is frozen because it is the only result in this project whose mechanism
made a falsifiable prediction about the SHAPE of the effect, and testing that
prediction is what exposed the selection confound in the headline version.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import sys
import urllib.request
from datetime import date

import pandas as pd

import config
import db
import ledger

MODELS = config.ROOT / "models"
SNAPSHOT = config.DATA / "sp500_snapshot.json"
WIKI = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"


def verify(version: str) -> dict:
    path = MODELS / f"{version}.json"
    if not path.exists():
        raise SystemExit(f"no frozen strategy at {path}")
    m = json.loads(path.read_text())
    spec = {k: m[k] for k in ("description", "signal", "universe", "construction",
                              "costs", "backtest", "live_sizing", "implemented_by")}
    h = hashlib.sha256()
    h.update(json.dumps(spec, sort_keys=True).encode())
    for p in sorted(m["implemented_by"]):
        h.update((config.ROOT / p).read_bytes())
    if h.hexdigest() != m["code_sha256"]:
        raise SystemExit(
            f"HASH MISMATCH for {version}. The spec or its implementing code has "
            "changed since freezing; this version's record is void. Freeze a new "
            "version rather than continuing it.")
    return m


def current_constituents() -> set[str]:
    req = urllib.request.Request(WIKI, headers={"User-Agent": "Mozilla/5.0 (research)"})
    html = urllib.request.urlopen(req, timeout=90).read().decode("utf-8", "ignore")
    t = pd.read_html(io.StringIO(html))[0]
    s = t["Symbol"].astype(str).str.replace(".", "-", regex=False).str.strip()
    return set(s[s.str.fullmatch(r"[A-Z\-]{1,6}")])


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--version", default="index_v1")
    ap.add_argument("--record", action="store_true")
    args = ap.parse_args()

    m = verify(args.version)
    print(f"{args.version} verified against its hash")
    gross = m["live_sizing"]["gross_exposure_usd"]
    hold = m["construction"]["holding_days"]

    now = current_constituents()
    print(f"  S&P 500 today: {len(now)} names")

    if not SNAPSHOT.exists():
        SNAPSHOT.write_text(json.dumps(
            {"date": date.today().isoformat(), "members": sorted(now)}, indent=2))
        print(f"\n  no prior snapshot. baseline written to {SNAPSHOT.name}.")
        print("  additions can only be detected from the NEXT run onward; there is")
        print("  nothing to diff against on a first run. this is the intended")
        print("  behaviour, not a failure.")
        return 0

    prev = json.loads(SNAPSHOT.read_text())
    before = set(prev["members"])
    added = sorted(now - before)
    removed = sorted(before - now)
    print(f"  last snapshot {prev['date']}: {len(before)} names")
    print(f"  additions since: {len(added)}  {added if added else ''}")
    print(f"  removals since:  {len(removed)}  {removed if removed else ''}")

    SNAPSHOT.write_text(json.dumps(
        {"date": date.today().isoformat(), "members": sorted(now)}, indent=2))

    if not added:
        print("\n  no new additions. no book today.")
        return 0

    with db.connect() as con:
        q = ",".join(f"'{s}'" for s in added)
        px = pd.read_sql(
            f"SELECT symbol, dt, close FROM bars WHERE symbol IN ({q}) "
            "ORDER BY symbol, dt", con)
    last = px.groupby("symbol").tail(1).set_index("symbol")["close"].to_dict()
    per = gross / (2 * len(added))   # half to the short leg, half to the SPY hedge

    print(f"\n  BOOK: short each addition, hedge with SPY, hold {hold} sessions")
    print(f"  {'symbol':<8}{'side':<7}{'price':>10}{'shares':>8}{'usd':>10}")
    rows = []
    for s in added:
        p = last.get(s)
        if p is None:
            print(f"  {s:<8}{'SKIP':<7}{'no bars loaded':>28}")
            continue
        sh = int(round(per / p))
        print(f"  {s:<8}{'SHORT':<7}{p:>10.2f}{-sh:>8}{-per:>10,.0f}")
        rows.append({"kind": "index_v1_position", "model_version": args.version,
                     "asof": date.today().isoformat(), "symbol": s,
                     "side": "short", "shares": sh, "usd": -per,
                     "price_at_signal": float(p), "holding_days": hold,
                     "stage": "live"})
    if rows:
        print(f"  {'SPY':<8}{'LONG':<7}{'':>10}{'':>8}{per * len(rows):>10,.0f}   (hedge)")
        rows.append({"kind": "index_v1_position", "model_version": args.version,
                     "asof": date.today().isoformat(), "symbol": "SPY",
                     "side": "long", "usd": per * (len(rows)),
                     "holding_days": hold, "stage": "live"})

    if args.record and rows:
        v = ledger.verify()
        if not v.ok:
            print(f"\n{v}\nRefusing to append to a broken ledger.", file=sys.stderr)
            return 1
        ledger.append(rows)
        print(f"\n  recorded {len(rows)} positions, timestamped before the outcome exists")
    elif rows:
        print("\n  (not recorded. pass --record to write this to the ledger)")

    print("\nThis is a position list. No orders placed, no broker connected.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
