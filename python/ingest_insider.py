#!/usr/bin/env python3
"""SEC bulk Form 3/4/5 data: what company insiders did with their own money.

    python3 python/ingest_insider.py --from 2014q1 --to 2026q3

The SEC publishes parsed insider filings as quarterly datasets, which is far
better than scraping a quarter of a million individual Form 4 documents. Each
quarter is a zip of TSVs; SUBMISSION carries the filing date and ticker,
NONDERIV_TRANS carries the trades.

WHY FILING DATE AND NOT TRANSACTION DATE. A Form 4 is due within two business
days of the trade. The market cannot react to an insider purchase until the
filing appears, so the filing date is when the information exists. Using the
transaction date would give the strategy up to two days of hindsight on every
event, which is exactly the sort of small, invisible leak that manufactures an
anomaly.

WHAT GETS KEPT. Only open-market purchases and sales (codes P and S). Awards,
option exercises, gifts and tax withholding are mechanical compensation events,
not decisions to take a view, and the literature treats them separately for good
reason: including them would swamp the signal with vesting schedules.
"""

from __future__ import annotations

import argparse
import io
import sys
import time
import urllib.error
import urllib.request
import zipfile

import pandas as pd

import config
import db

UA = "Mason Trinkner edge-audit research mtrinkner@wisc.edu"
BASE = "https://www.sec.gov/files/structureddata/data/insider-transactions-data-sets"
KEEP_CODES = {"P", "S"}


def quarters(a: str, b: str) -> list[str]:
    ya, qa = int(a[:4]), int(a[5])
    yb, qb = int(b[:4]), int(b[5])
    out = []
    while (ya, qa) <= (yb, qb):
        out.append(f"{ya}q{qa}")
        qa += 1
        if qa > 4:
            qa, ya = 1, ya + 1
    return out


def fetch_quarter(q: str, retries: int = 3) -> pd.DataFrame | None:
    url = f"{BASE}/{q}_form345.zip"
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            raw = urllib.request.urlopen(req, timeout=180).read()
            break
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            time.sleep(2 * (attempt + 1))
        except Exception:
            time.sleep(2 * (attempt + 1))
    else:
        return None

    z = zipfile.ZipFile(io.BytesIO(raw))
    rd = lambda n: pd.read_csv(z.open(n), sep="\t", dtype=str,
                               on_bad_lines="skip", low_memory=False)
    sub = rd("SUBMISSION.tsv")
    trn = rd("NONDERIV_TRANS.tsv")
    own = rd("REPORTINGOWNER.tsv")

    sub = sub[["ACCESSION_NUMBER", "FILING_DATE", "ISSUERTRADINGSYMBOL", "DOCUMENT_TYPE"]]
    sub = sub[sub["DOCUMENT_TYPE"] == "4"]
    trn = trn[["ACCESSION_NUMBER", "TRANS_DATE", "TRANS_CODE", "TRANS_SHARES",
               "TRANS_PRICEPERSHARE", "TRANS_ACQUIRED_DISP_CD"]]
    trn = trn[trn["TRANS_CODE"].isin(KEEP_CODES)]
    own = (own[["ACCESSION_NUMBER", "RPTOWNERNAME", "RPTOWNER_RELATIONSHIP"]]
           .drop_duplicates("ACCESSION_NUMBER"))

    df = trn.merge(sub, on="ACCESSION_NUMBER").merge(own, on="ACCESSION_NUMBER", how="left")
    if df.empty:
        return df
    for c in ("FILING_DATE", "TRANS_DATE"):
        df[c] = pd.to_datetime(df[c], format="%d-%b-%Y", errors="coerce")
    df = df.dropna(subset=["FILING_DATE", "ISSUERTRADINGSYMBOL"])
    df["shares"] = pd.to_numeric(df["TRANS_SHARES"], errors="coerce")
    df["price"] = pd.to_numeric(df["TRANS_PRICEPERSHARE"], errors="coerce")
    return pd.DataFrame({
        "accession": df["ACCESSION_NUMBER"],
        "symbol": df["ISSUERTRADINGSYMBOL"].str.upper().str.replace(".", "-", regex=False),
        "filing_date": df["FILING_DATE"].dt.date.astype(str),
        "trans_date": df["TRANS_DATE"].dt.date.astype(str),
        "trans_code": df["TRANS_CODE"],
        "shares": df["shares"], "price": df["price"],
        "acquired_disposed": df["TRANS_ACQUIRED_DISP_CD"],
        "owner_name": df["RPTOWNERNAME"],
        "relationship": df["RPTOWNER_RELATIONSHIP"],
    }).dropna(subset=["shares"])


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--from", dest="start", default="2014q1")
    ap.add_argument("--to", dest="end", default="2026q3")
    args = ap.parse_args()

    with db.connect() as con:
        universe = {r["symbol"] for r in con.execute(
            "SELECT symbol FROM symbols WHERE kind='stock'")}
    qs = quarters(args.start, args.end)
    print(f"fetching {len(qs)} quarters of SEC insider data ({qs[0]} to {qs[-1]})")

    with db.transaction() as con:
        cur = con.execute(
            """INSERT INTO ingest_runs (source, interval, requested_start,
               requested_end, symbols_requested, status)
               VALUES ('sec-form345','quarterly',?,?,?,'running')""",
            (qs[0], qs[-1], len(universe)))
        run_id = cur.lastrowid

    total, missing = 0, []
    for i, q in enumerate(qs, 1):
        df = fetch_quarter(q)
        if df is None or df.empty:
            missing.append(q)
            print(f"\r  {i}/{len(qs)} {q}: no data", end="", flush=True)
            continue
        df = df[df["symbol"].isin(universe)]
        if not df.empty:
            df["run_id"] = run_id
            with db.transaction() as con:
                con.executemany(
                    """INSERT OR IGNORE INTO insider_trades
                       (accession,symbol,filing_date,trans_date,trans_code,shares,
                        price,acquired_disposed,owner_name,relationship,run_id)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                    df[["accession","symbol","filing_date","trans_date","trans_code",
                        "shares","price","acquired_disposed","owner_name",
                        "relationship","run_id"]].itertuples(index=False, name=None))
            total += len(df)
        print(f"\r  {i}/{len(qs)} {q}: +{len(df):,} rows (total {total:,})",
              end="", flush=True)
        time.sleep(0.2)
    print()

    with db.transaction() as con:
        con.execute("""UPDATE ingest_runs SET status=?, rows_loaded=?,
                       finished_at=datetime('now'), notes=? WHERE run_id=?""",
                    ("ok" if not missing else "partial", total,
                     f"{len(missing)} quarters unavailable: {missing[:6]}", run_id))
        n = con.execute("SELECT COUNT(*) c FROM insider_trades").fetchone()["c"]
        rng = con.execute("SELECT MIN(filing_date) a, MAX(filing_date) b, "
                          "COUNT(DISTINCT symbol) s FROM insider_trades").fetchone()
    print(f"\ninsider_trades: {n:,} open-market trades, {rng['s']} symbols, "
          f"{rng['a']} to {rng['b']}")
    if missing:
        print(f"quarters unavailable: {missing}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
