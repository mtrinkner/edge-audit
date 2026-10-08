#!/usr/bin/env python3
"""Download earnings announcement history: a second, independent data source.

    python3 python/ingest_earnings.py --all
    python3 python/ingest_earnings.py --symbols AAPL MSFT

WHY EARNINGS AND NOT FUNDAMENTALS. The provider returns only five or six
quarters of financial statements, which cannot support a 2014-2026 study. It
returns roughly fifty earnings announcements per name, back to 2014, each with
the consensus estimate, the reported figure and the surprise. That matches the
price history and is event-dated, so it is the one alternative source here with
both reach and point-in-time integrity.

THE TIMESTAMP IS THE WHOLE POINT. A release at 16:00 ET is after the close, so
the earliest session that can act on it is the NEXT one. A release at 08:00 is
before the open and is actionable the same day. Treating the announcement date
as the trade date hands the strategy a few hours of hindsight on exactly the
days when prices move most, which is enough to fabricate an entire anomaly. The
resolved date is stored as tradeable_from so nothing downstream re-derives it.

KNOWN DATA LIMITATION, STATED UP FRONT. The provider gives one estimate per
event and does not say when that estimate was set. If it reflects a consensus
revised after the fact rather than the one standing before the release, the
surprise is contaminated. This cannot be verified from this source. It is the
single largest threat to any result built on this table, and it is why the
earnings signals are evaluated against price-only baselines rather than reported
on their own.
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timedelta

import pandas as pd

import config
import db

MARKET_CLOSE_HOUR = 16        # ET; releases at or after this trade the next day
BATCH_PAUSE = 0.12            # be polite to the provider


def resolve_tradeable_from(ts: pd.Timestamp, sessions: list[str]) -> str | None:
    """First trading session that could have acted on this release."""
    if ts.tzinfo is not None:
        ts = ts.tz_convert("America/New_York")
    d = ts.date().isoformat()
    after_close = ts.hour >= MARKET_CLOSE_HOUR
    # Walk forward to the first session strictly after the release (or on the
    # same day if it landed before the close and that day traded).
    for s in sessions:
        if after_close:
            if s > d:
                return s
        else:
            if s >= d:
                return s
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--symbols", nargs="*")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--limit", type=int, default=60)
    args = ap.parse_args()
    if not args.symbols and not args.all:
        ap.error("pass --all or --symbols")

    import yfinance as yf
    import warnings
    warnings.filterwarnings("ignore")

    with db.connect() as con:
        sessions = [r["dt"] for r in con.execute(
            "SELECT dt FROM trading_days ORDER BY dt")]
        universe = [r["symbol"] for r in con.execute(
            "SELECT symbol FROM symbols WHERE kind='stock' ORDER BY symbol")]
    symbols = args.symbols or universe
    print(f"fetching earnings for {len(symbols)} symbols "
          f"({len(sessions):,} sessions in the calendar)")

    with db.transaction() as con:
        cur = con.execute(
            """INSERT INTO ingest_runs (source, interval, requested_start,
               requested_end, symbols_requested, status)
               VALUES ('yfinance-earnings','event','2014-01-01',?,?, 'running')""",
            (config.END_DATE.isoformat(), len(symbols)))
        run_id = cur.lastrowid

    rows, failed = [], []
    for i, sym in enumerate(symbols, 1):
        try:
            ed = yf.Ticker(sym).get_earnings_dates(limit=args.limit)
            if ed is None or ed.empty:
                failed.append(sym); continue
            for ts, r in ed.iterrows():
                tf = resolve_tradeable_from(pd.Timestamp(ts), sessions)
                if tf is None:
                    continue
                est, act = r.get("EPS Estimate"), r.get("Reported EPS")
                if pd.isna(act):
                    continue            # not yet reported; nothing to learn from
                rows.append((sym, pd.Timestamp(ts).isoformat(), None,
                             None if pd.isna(est) else float(est),
                             float(act),
                             None if pd.isna(r.get("Surprise(%)")) else float(r["Surprise(%)"]),
                             tf, run_id))
        except Exception:
            failed.append(sym)
        if i % 25 == 0:
            print(f"\r  {i}/{len(symbols)}  rows={len(rows):,}  failed={len(failed)}",
                  end="", flush=True)
        time.sleep(BATCH_PAUSE)
    print(f"\r  {len(symbols)}/{len(symbols)}  rows={len(rows):,}  failed={len(failed)}")

    with db.transaction() as con:
        con.executemany(
            """INSERT INTO earnings (symbol, announced_at, period_end, eps_estimate,
               eps_actual, surprise_pct, tradeable_from, run_id)
               VALUES (?,?,?,?,?,?,?,?)
               ON CONFLICT(symbol, announced_at) DO UPDATE SET
                 eps_estimate=excluded.eps_estimate, eps_actual=excluded.eps_actual,
                 surprise_pct=excluded.surprise_pct,
                 tradeable_from=excluded.tradeable_from, run_id=excluded.run_id""",
            rows)
        con.execute(
            """UPDATE ingest_runs SET status=?, symbols_loaded=?, rows_loaded=?,
               finished_at=datetime('now'), notes=? WHERE run_id=?""",
            ("ok" if not failed else "partial", len(symbols) - len(failed),
             len(rows), f"{len(failed)} symbols returned nothing", run_id))
        n = con.execute("SELECT COUNT(*) c FROM earnings").fetchone()["c"]
        span = con.execute(
            "SELECT MIN(tradeable_from) a, MAX(tradeable_from) b FROM earnings").fetchone()

    print(f"\nearnings table: {n:,} events, {span['a']} to {span['b']}")
    if failed:
        print(f"no data for {len(failed)} symbols (first 10): {failed[:10]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
