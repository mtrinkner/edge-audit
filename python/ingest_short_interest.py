#!/usr/bin/env python3
"""FINRA consolidated short interest, lagged to when it was actually published.

    python3 python/ingest_short_interest.py

Short interest is reported twice a month: the aggregate shares sold short in each
name as of a settlement date. It is a different kind of measurement from anything
else in this project, being a record of positioning by a particular group of
participants rather than a function of past prices.

THE LAG IS THE WHOLE METHODOLOGICAL POINT. FINRA publishes the settlement date
but not the dissemination date, and dissemination happens roughly eight business
days later. A backtest that acts on a settlement date is acting on information
that did not exist for another week and a half, on precisely the dates when
positioning data moves prices. A conservative ten-session lag is applied and
resolved against the real trading calendar, so the strategy is late rather than
clairvoyant.

The API filters server-side by symbol, so this pulls only the universe rather
than all twenty thousand listed names.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request

import pandas as pd

import config
import db

UA = "Mason Trinkner edge-audit research mtrinkner@wisc.edu"
URL = "https://api.finra.org/data/group/otcMarket/name/consolidatedShortInterest"
PAGE = 5000
PUBLICATION_LAG_SESSIONS = 10


def post(payload: dict, retries: int = 3):
    for a in range(retries):
        try:
            req = urllib.request.Request(
                URL, data=json.dumps(payload).encode(),
                headers={"User-Agent": UA, "Content-Type": "application/json",
                         "Accept": "application/json"}, method="POST")
            with urllib.request.urlopen(req, timeout=180) as f:
                return json.loads(f.read().decode("utf-8", "ignore"))
        except Exception:
            time.sleep(2 * (a + 1))
    return []


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--chunk", type=int, default=400)
    args = ap.parse_args()

    with db.connect() as con:
        symbols = [r["symbol"] for r in con.execute(
            "SELECT symbol FROM symbols WHERE kind='stock' ORDER BY symbol")]
        sessions = [r["dt"] for r in con.execute(
            "SELECT dt FROM trading_days ORDER BY dt")]
    print(f"fetching short interest for {len(symbols)} symbols")

    with db.transaction() as con:
        cur = con.execute(
            """INSERT INTO ingest_runs (source, interval, requested_start,
               requested_end, symbols_requested, status)
               VALUES ('finra-short-interest','biweekly','2014-01-01',?,?,'running')""",
            (config.END_DATE.isoformat(), len(symbols)))
        run_id = cur.lastrowid

    # PAGINATE BY MONTH, NOT BY OFFSET.
    #
    # The first version walked `offset` forward until the API returned nothing.
    # The API caps how far an offset can reach, so it stopped early and silently,
    # and the result looked like "FINRA only retains data back to 2021". It does
    # not: the archive goes back to 2017-12-29. A silent truncation that looks
    # like a data limitation is worse than an error, because it gets written into
    # the README as a fact about the world.
    #
    # Querying one month at a time bounds every request to roughly two settlement
    # dates times the symbol chunk, which always fits in a single page and never
    # touches the offset cap.
    rows = []
    chunks = [symbols[i:i + args.chunk] for i in range(0, len(symbols), args.chunk)]
    months = pd.date_range("2017-12-01", "2026-10-01", freq="MS")
    for ci, chunk in enumerate(chunks, 1):
        for mi, m0 in enumerate(months):
            m1 = m0 + pd.offsets.MonthEnd(1) + pd.Timedelta(days=1)
            batch = post({
                "limit": PAGE,
                "domainFilters": [{"fieldName": "symbolCode", "values": chunk}],
                "compareFilters": [
                    {"fieldName": "settlementDate",
                     "fieldValue": (m0 - pd.Timedelta(days=1)).date().isoformat(),
                     "compareType": "GREATER"},
                    {"fieldName": "settlementDate",
                     "fieldValue": m1.date().isoformat(), "compareType": "LESSER"}],
            })
            if batch:
                rows.extend(batch)
            if mi % 12 == 0:
                print(f"\r  chunk {ci}/{len(chunks)}  {m0.date()}  total {len(rows):,}",
                      end="", flush=True)
            time.sleep(0.1)
    print()

    if not rows:
        print("no data returned", file=sys.stderr)
        return 1

    df = pd.DataFrame(rows)
    df = df.rename(columns={
        "symbolCode": "symbol", "settlementDate": "settlement_date",
        "currentShortPositionQuantity": "short_shares",
        "previousShortPositionQuantity": "prev_short",
        "averageDailyVolumeQuantity": "avg_daily_vol",
        "daysToCoverQuantity": "days_to_cover"})
    keep = ["symbol", "settlement_date", "short_shares", "prev_short",
            "avg_daily_vol", "days_to_cover"]
    df = df[[c for c in keep if c in df.columns]].dropna(subset=["symbol", "settlement_date"])
    df["symbol"] = df["symbol"].str.upper().str.replace(".", "-", regex=False)
    df["settlement_date"] = df["settlement_date"].astype(str).str.slice(0, 10)

    # Resolve the publication lag against the real calendar.
    import bisect
    def tradeable(d: str) -> str | None:
        i = bisect.bisect_left(sessions, d)
        j = i + PUBLICATION_LAG_SESSIONS
        return sessions[j] if j < len(sessions) else None
    df["tradeable_from"] = df["settlement_date"].map(tradeable)
    df = df.dropna(subset=["tradeable_from"]).drop_duplicates(["symbol", "settlement_date"])
    df["run_id"] = run_id

    with db.transaction() as con:
        con.executemany(
            """INSERT INTO short_interest (symbol, settlement_date, tradeable_from,
               short_shares, prev_short, avg_daily_vol, days_to_cover, run_id)
               VALUES (?,?,?,?,?,?,?,?)
               ON CONFLICT(symbol, settlement_date) DO UPDATE SET
                 short_shares=excluded.short_shares, prev_short=excluded.prev_short,
                 avg_daily_vol=excluded.avg_daily_vol,
                 days_to_cover=excluded.days_to_cover""",
            df[["symbol","settlement_date","tradeable_from","short_shares","prev_short",
                "avg_daily_vol","days_to_cover","run_id"]].itertuples(index=False, name=None))
        con.execute("""UPDATE ingest_runs SET status='ok', rows_loaded=?,
                       finished_at=datetime('now') WHERE run_id=?""", (len(df), run_id))
        n = con.execute("SELECT COUNT(*) c FROM short_interest").fetchone()["c"]
        rng = con.execute("SELECT MIN(settlement_date) a, MAX(settlement_date) b, "
                          "COUNT(DISTINCT symbol) s FROM short_interest").fetchone()
    print(f"\nshort_interest: {n:,} observations, {rng['s']} symbols, "
          f"{rng['a']} to {rng['b']}  (lag {PUBLICATION_LAG_SESSIONS} sessions)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
