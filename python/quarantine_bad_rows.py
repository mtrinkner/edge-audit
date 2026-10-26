#!/usr/bin/env python3
"""Move known-bad price rows out of `bars` into `bars_quarantine` (2026-10-17).

    python3 python/quarantine_bad_rows.py            # apply (idempotent)
    python3 python/quarantine_bad_rows.py --dry-run  # show what would move

WHY. A vendor bankruptcy re-stitch (CHRD: old Oasis Petroleum shares at $0.12
joined to new Chord Energy shares at $31) distorted trial 50 and trial 52, each
time through a single fake return of +22,500% to +30,991%. The 2026-10-14 sweep
(reports/data_quality_flags.csv) flagged 66 rows. Reviewing each one by hand:

  - 6 are real errors in 5 stocks, listed in RULES below. For re-stitches and
    pre-listing segments, removing the jump day alone is not enough: every price
    before it belongs to a different instrument, so any return spanning the gap
    is still fake. The whole segment is quarantined.
  - The other 60 rows ("open more than 50% from close") are REAL crash days:
    GME 2021-02-25, the March 2020 crash, Globe Life on 2024-04-11, LEU's July
    2014 surge on 100x normal volume. The open lies inside the high-low range in
    every case. They stay: deleting real crashes would flatter every strategy.

Rows are MOVED, not destroyed, with the reason attached. A backup of the full
database was taken first (data/warehouse_backup_2026-10-17.db). Re-run this
after any re-ingest, since the vendor will serve the same bad rows again.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import db

RULES = [
    ("CHRD", "dt < '2020-11-20'",
     "bankruptcy re-stitch: pre-2020-11-20 rows are old Oasis Petroleum shares ($0.12) "
     "joined to new Chord Energy shares ($31); fake +25,700% day"),
    ("ASTH", "dt = '2015-04-28'",
     "single bad print: $0.45 on zero volume between $4.50 and $5.00"),
    ("IVT", "dt < '2021-09-24'",
     "pre-listing non-traded REIT quotes not adjusted for the 1-for-10 reverse split "
     "($1.96 x 10 = listing price $19.75); fake +908% day"),
    ("INDV", "dt IN ('2022-11-23', '2022-11-25')",
     "two bad quotes of $3.13 between $19.64 and $21.03; fake +572% day"),
    ("CLSK", "dt <= '2018-09-19'",
     "thin OTC trading before uplisting with erratic prints ($150 high on 25,850 "
     "shares); fake +387% day"),
]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    with db.connect() as con:
        cols = [r[1] for r in con.execute("PRAGMA table_info(bars)")]
        con.execute(f"CREATE TABLE IF NOT EXISTS bars_quarantine AS SELECT *, "
                    f"'' AS reason, '' AS quarantined_at FROM bars WHERE 0")
        now = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
        total = 0
        for sym, cond, reason in RULES:
            where = f"symbol = ? AND {cond}"
            n = con.execute(f"SELECT COUNT(*) FROM bars WHERE {where}", (sym,)).fetchone()[0]
            total += n
            print(f"  {sym:<6}{n:>6} rows  {cond}")
            if args.dry_run or n == 0:
                continue
            collist = ", ".join(cols)
            con.execute(f"INSERT INTO bars_quarantine ({collist}, reason, quarantined_at) "
                        f"SELECT {collist}, ?, ? FROM bars WHERE {where}", (reason, now, sym))
            con.execute(f"DELETE FROM bars WHERE {where}", (sym,))
        if not args.dry_run:
            con.commit()
        q = con.execute("SELECT COUNT(*) FROM bars_quarantine").fetchone()[0]
    print(f"\n{'would move' if args.dry_run else 'moved'} {total} rows; "
          f"bars_quarantine now holds {q}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
