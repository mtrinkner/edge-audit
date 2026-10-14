#!/usr/bin/env python3
"""Point-in-time S&P 500 membership, reconstructed from Wikipedia revisions.

    python3 python/pit_universe.py --years 2014 2026
    python3 python/pit_universe.py --probe 2015-06-30

WHY

Every result in this project used the index membership of 2026-10. Companies that
went bankrupt, were acquired or were dropped are absent, which inflates anything
that buys losers and made one mean-reversion book print +27% a year that is not
reported anywhere as a finding.

WHAT THIS DOES AND DOES NOT FIX

Wikipedia's "List of S&P 500 companies" page has been edited continuously since
2007, and the API serves any past revision. Fetching the revision current on a
given date gives the membership list as it was understood THEN, including names
that have since vanished. That is a genuine point-in-time reconstruction.

  FIXES   holding a stock before it entered the index
  FIXES   missing names that later left the index, to the extent Wikipedia
          recorded them at the time
  DOES NOT FIX   price history for delisted names. Knowing that a company was a
          member in 2016 does not produce its bars. The vendor here only serves
          currently listed symbols, so a reconstructed universe will still have
          holes, and those holes are exactly the failures.

So this is a partial correction and must be labelled as one. A universe that
knows who the losers were, but cannot price them, is more honest than one that
pretends they never existed, and still not unbiased.

ACCURACY CAVEAT. Wikipedia is crowd-edited. A membership change may be recorded
late, early, or not at all, and the page format has changed repeatedly (the
ticker is {{NyseSymbol|X}} in 2014 and {{NYSE|X}} later). Coverage is reported
per year so the reconstruction can be judged rather than trusted.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "pit_sp500.json"
API = "https://en.wikipedia.org/w/api.php"
UA = {"User-Agent": "edge-audit-research/1.0 (backtest universe reconstruction)"}
PAGE = "List_of_S%26P_500_companies"

# The ticker cell has worn several costumes over the years.
PATTERNS = [
    r"\{\{NYSE\|([A-Z][A-Z\.\-]{0,6})\}\}",
    r"\{\{NyseSymbol\|([A-Z][A-Z\.\-]{0,6})\}\}",
    r"\{\{Nasdaq\|([A-Z][A-Z\.\-]{0,6})\}\}",
    r"\{\{NasdaqSymbol\|([A-Z][A-Z\.\-]{0,6})\}\}",
    r"\{\{BATS\|([A-Z][A-Z\.\-]{0,6})\}\}",
    r"\{\{NYSEAmerican\|([A-Z][A-Z\.\-]{0,6})\}\}",
]


def _get(url: str, tries: int = 6) -> dict:
    """Wikipedia rate-limits an unthrottled loop. A first run got five snapshots
    and then 21 consecutive HTTP errors, so this backs off rather than recording
    a hole as a result."""
    for a in range(tries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.loads(r.read())
        except Exception:
            if a == tries - 1:
                raise
            time.sleep(5.0 * 2 ** a)
    raise RuntimeError("unreachable")


def revision_asof(ts: str) -> tuple[int, str]:
    """The newest revision at or before ts (YYYY-MM-DD)."""
    u = (f"{API}?action=query&prop=revisions&titles={PAGE}&rvlimit=1"
         f"&rvstart={ts}T23:59:59Z&rvdir=older&rvprop=ids|timestamp&format=json")
    pg = list(_get(u)["query"]["pages"].values())[0]
    rev = pg["revisions"][0]
    return int(rev["revid"]), rev["timestamp"][:10]


def members(revid: int) -> set[str]:
    t = _get(f"{API}?action=parse&oldid={revid}&prop=wikitext&format=json")
    t = t["parse"]["wikitext"]["*"]
    # Only the first table: later sections list changes, not membership.
    cut = t.find("Selected changes")
    if cut > 0:
        t = t[:cut]
    out: set[str] = set()
    for p in PATTERNS:
        out |= set(re.findall(p, t))
    if len(out) < 100:
        # Fallback for revisions that write the ticker as a bare cell.
        out |= set(re.findall(r"^\|\s*([A-Z]{1,5}(?:\.[A-Z])?)\s*$", t, re.M))
    return {s.strip() for s in out if s.strip()}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--years", nargs=2, type=int, default=[2014, 2026])
    ap.add_argument("--probe", default=None, help="single date, print and exit")
    args = ap.parse_args()

    if args.probe:
        rid, when = revision_asof(args.probe)
        m = members(rid)
        print(f"{args.probe}: revision {rid} dated {when}, {len(m)} tickers")
        print("  sample:", sorted(m)[:12])
        return 0

    snaps = {}
    lo, hi = args.years
    dates = [f"{y}-{mm:02d}-01" for y in range(lo, hi + 1) for mm in (1, 7)]
    print(f"reconstructing {len(dates)} semi-annual snapshots\n")
    for dt in dates:
        try:
            rid, when = revision_asof(dt)
            m = members(rid)
        except Exception as e:
            print(f"  {dt}  FAILED {type(e).__name__}")
            continue
        snaps[dt] = sorted(m)
        flag = "  <- suspicious" if not (400 <= len(m) <= 520) else ""
        print(f"  {dt}  rev {when}  {len(m):>4} names{flag}")
        time.sleep(2.0)

    OUT.write_text(json.dumps(snaps, indent=1))
    allnames = sorted({s for v in snaps.values() for s in v})
    # Compare against the LATEST SNAPSHOT THAT SUCCEEDED. A first version used
    # dates[-1] blindly; that snapshot had failed, so `current` was empty and
    # every one of the 551 names was reported as deleted. A summary computed
    # from a missing input is worse than no summary.
    ok = [d for d in dates if d in snaps]
    if not ok:
        print("\nno snapshots succeeded; nothing to summarise")
        return 1
    latest = ok[-1]
    current = set(snaps[latest])
    print(f"\n{len(ok)}/{len(dates)} snapshots succeeded; "
          f"comparing against {latest} ({len(current)} names)")
    gone = [s for s in allnames if s not in current]
    print(f"{len(allnames)} distinct tickers ever seen across the window")
    print(f"{len(gone)} appear in some snapshot but NOT the latest, i.e. the names")
    print(f"  a current-constituent universe silently deletes")
    print(f"  examples: {gone[:14]}")
    print(f"\nwrote {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
