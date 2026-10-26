#!/usr/bin/env python3
"""Pull the CRSP monthly stock file and the risk-free rate from WRDS (trial 57).

    python3 python/crsp_pull.py

Needs a WRDS account with a saved ~/.pgpass (wrds.Connection().create_pgpass_file()
run once by the account holder). Only the username is read from that file here;
the password never passes through this code.

Sample, matching "common shares of US-based firms listed on the NYSE, AMEX, or
NASDAQ" (Murray, Xia & Xiao, SampleForFits.R) in CRSP's current (CIZ) format:
sharetype NS, securitytype EQTY, securitysubtype COM, usincflg Y, issuertype
CORP, primaryexch N/A/Q. mthret in msf_v2 already includes delisting returns.

CRSP is licensed: the output stays in data/crsp/, which is git-ignored.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "crsp"


def username() -> str:
    line = (Path.home() / ".pgpass").read_text().splitlines()[0]
    return line.split(":")[3]


def main() -> int:
    import wrds
    OUT.mkdir(parents=True, exist_ok=True)
    db = wrds.Connection(wrds_username=username())
    # One decade per query: a single 98-year query crashed the database driver
    # (segmentation fault, exit code 139) on this Python.
    parts = []
    for y0 in range(1920, 2030, 10):
        q = f"""
            SELECT permno, mthcaldt::text AS mthcaldt, mthret, mthprc, shrout, mthcap,
                   primaryexch
            FROM crsp.msf_v2
            WHERE mthcaldt >= '{y0}-01-01' AND mthcaldt < '{y0 + 10}-01-01'
              AND sharetype = 'NS' AND securitytype = 'EQTY' AND securitysubtype = 'COM'
              AND usincflg = 'Y' AND issuertype = 'CORP' AND primaryexch IN ('N', 'A', 'Q')
        """
        f = OUT / f"msf_{y0}s.csv"
        if not f.exists():
            # Saved per decade, as plain CSV, straight away: combining the decades
            # in memory and writing one parquet crashed the process after an
            # hour-scale download, losing all of it.
            db.raw_sql(q).to_csv(f, index=False)
        print(f"  {y0}s saved", flush=True)
        parts.append(f)
    msf = pd.concat([pd.read_csv(f) for f in parts], ignore_index=True)
    msf["mthcaldt"] = pd.to_datetime(msf["mthcaldt"])
    msf.to_parquet(OUT / "msf.parquet", index=False)
    print(f"msf: {len(msf):,} rows, {msf['permno'].nunique():,} stocks, "
          f"{msf['mthcaldt'].min():%Y-%m} to {msf['mthcaldt'].max():%Y-%m}")
    try:
        rf = db.raw_sql("SELECT date::text AS date, rf FROM ff.factors_monthly")
        rf["date"] = pd.to_datetime(rf["date"])
        rf.to_parquet(OUT / "rf.parquet", index=False)
        print(f"rf: {len(rf)} months to {rf['date'].max():%Y-%m}")
    except Exception as e:
        print(f"rf unavailable from ff.factors_monthly: {str(e).splitlines()[0][:100]}")
    db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
