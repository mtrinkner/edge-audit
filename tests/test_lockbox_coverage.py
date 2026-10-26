"""Every study that reads market data must call lockbox.guard.

FOUND 2026-10-26. lockbox.guard is correct, but it only protects scripts that
call it, and most never did. overnight_auction.py scored 2026-10-07, a sealed
date, into reports/overnight_auction.csv (trial 44), and data/intraday_trades.parquet
holds 18,879 trades from 2026-10-07 to 10-09 that fed the trial 38 and 39 reports.

This is a static check: any python/ script that reads bars, features or the
intraday/ETF parquet files must mention lockbox. Scripts that legitimately read
everything (ingest, export, the forward test that exists to read new data) are
exempt. Studies that ran before this test are listed as KNOWN_UNGUARDED so that
the record is explicit; a NEW unguarded study fails.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

READS_DATA = re.compile(r"FROM bars|FROM features|feature_panel|intraday_1h|intraday_trades"
                        r"|load_bars|etf_1h|etf2_1h|sector_etfs_daily|spy_irx|msf")

NOT_A_STUDY = {
    "ingest_bars.py", "ingest_intraday.py", "crsp_pull.py", "quarantine_bad_rows.py",
    "export_excel.py", "forward_run.py", "index_positions.py", "audit_root_causes.py",
}

# Studies that predate this check and still read data unguarded. On 2026-10-26
# the other 17 were routed through python/data_access.py, which calls
# lockbox.guard; index_deletions.py waits on the recycled-ticker review (trial 45).
KNOWN_UNGUARDED = {"index_deletions.py"}

# A study is guarded if it calls lockbox.guard itself or loads through
# data_access. A comment that merely mentions the lockbox does not count.
GUARDED = re.compile(r"lockbox\.guard\(|^import data_access|^from data_access", re.M)


def test_studies_guard_the_lockbox():
    unguarded = set()
    for f in (ROOT / "python").glob("*.py"):
        src = f.read_text()
        if READS_DATA.search(src) and not GUARDED.search(src) and f.name not in NOT_A_STUDY:
            unguarded.add(f.name)
    new = unguarded - KNOWN_UNGUARDED
    assert not new, f"new studies read market data without lockbox.guard: {sorted(new)}"
    fixed = KNOWN_UNGUARDED - unguarded
    assert not fixed, f"guarded now, remove from KNOWN_UNGUARDED: {sorted(fixed)}"
