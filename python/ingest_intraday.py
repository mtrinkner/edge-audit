#!/usr/bin/env python3
"""Hourly bars, two years, so day-trading strategies can actually be tested.

    python3 python/ingest_intraday.py
    python3 python/ingest_intraday.py --chunk 40 --resume

WHY THIS EXISTS

Every result in this project until now used DAILY bars, one row per symbol per
day. The 41 technical signals were tested with a 2 ATR stop and a 3 ATR target
and came out holding for a median of ELEVEN sessions. That is swing trading. A
day trade opens and closes inside one session, and daily bars collapse the entire
session into a single row, so the strategy lives in the part of the data that is
missing.

WHAT THE FREE DATA ACTUALLY ALLOWS, measured rather than assumed:

    interval   history    bars per symbol
    1 minute     7 days             2,485
    5 minute    60 days             4,632
    1 hour     730 days             5,069

Sixty days of 5-minute data is about 43 trading days: one market regime, with no
way to check whether a result survives a different one. Two years of hourly bars
is the only free option with enough CALENDAR time to test anything, so that is
what this pulls. The cost is resolution: 7 bars a session, 09:30 through 15:30.
That supports same-day entry and exit rules, opening-range breaks and midday
reversion. It cannot test anything that happens inside an hour.

ONE GENUINE ADVANTAGE. All 381 registered trials so far ran on daily bars.
Hourly data has never been searched by this project, so a test here is closer to
out-of-sample than anything achievable by reorganising the daily work.

PRICES ARE AUTO-ADJUSTED. A split inside the two-year window would otherwise put
a 50% gap in the middle of a session and trip every stop in the sample. Dividend
adjustment also slightly distorts same-day returns on ex-dividend days, which is
the standard tradeoff and is accepted here.

WRITES INCREMENTALLY. One parquet per chunk of symbols, so an interrupted run
keeps what it already fetched and --resume skips those chunks. An earlier ingest
in this project held an hour of work in memory and lost all of it.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

import db

ROOT = Path(__file__).resolve().parent.parent
PARTS = ROOT / "data" / "intraday_parts"
OUT = ROOT / "data" / "intraday_1h.parquet"
INTERVAL = "1h"
PERIOD = "730d"
SESSION_BARS = 7


def universe() -> list[str]:
    with db.connect() as con:
        rows = con.execute(
            "SELECT symbol FROM symbols WHERE kind='stock' ORDER BY symbol"
        ).fetchall()
    return [r[0] for r in rows]


def fetch(syms: list[str]) -> pd.DataFrame:
    import yfinance as yf
    d = yf.download(syms, interval=INTERVAL, period=PERIOD, progress=False,
                    auto_adjust=True, group_by="ticker", threads=True)
    if d.empty:
        return pd.DataFrame()
    out = []
    for s in syms:
        try:
            g = d[s] if isinstance(d.columns, pd.MultiIndex) else d
        except KeyError:
            continue
        g = g.dropna(subset=["Close"])
        if g.empty:
            continue
        out.append(pd.DataFrame({
            "symbol": s,
            "ts": g.index,
            "open": g["Open"].to_numpy(), "high": g["High"].to_numpy(),
            "low": g["Low"].to_numpy(), "close": g["Close"].to_numpy(),
            "volume": g["Volume"].to_numpy(),
        }))
    return pd.concat(out, ignore_index=True) if out else pd.DataFrame()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--chunk", type=int, default=40)
    ap.add_argument("--resume", action="store_true")
    ap.add_argument("--limit", type=int, default=None,
                    help="only the first N symbols, for a quick check")
    args = ap.parse_args()

    syms = universe()
    if args.limit:
        syms = syms[: args.limit]
    PARTS.mkdir(parents=True, exist_ok=True)
    chunks = [syms[i:i + args.chunk] for i in range(0, len(syms), args.chunk)]
    print(f"{len(syms)} symbols in {len(chunks)} chunks of {args.chunk}, "
          f"{INTERVAL} bars over {PERIOD}\n")

    for i, ch in enumerate(chunks, 1):
        part = PARTS / f"part_{i:04d}.parquet"
        if args.resume and part.exists():
            continue
        for attempt in range(4):
            try:
                d = fetch(ch)
                break
            except Exception as e:
                if attempt == 3:
                    print(f"  chunk {i}: FAILED {type(e).__name__}")
                    d = pd.DataFrame()
                    break
                time.sleep(4 * 2 ** attempt)
        if not d.empty:
            d.to_parquet(part, index=False)
        if i % 5 == 0 or i == len(chunks):
            got = len(d) if not d.empty else 0
            print(f"  chunk {i}/{len(chunks)}  {got:>7,} rows  "
                  f"({ch[0]}..{ch[-1]})")
        time.sleep(0.6)

    parts = sorted(PARTS.glob("part_*.parquet"))
    if not parts:
        print("nothing fetched")
        return 1
    d = pd.concat([pd.read_parquet(p) for p in parts], ignore_index=True)

    # Session date and position within the session, which is what every
    # intraday rule keys on. The timezone must survive the round trip or
    # 09:30 New York silently becomes 14:30 UTC and every rule misfires.
    d["ts"] = pd.to_datetime(d["ts"], utc=True).dt.tz_convert("America/New_York")
    d["session"] = d["ts"].dt.date.astype(str)
    d = d.sort_values(["symbol", "ts"]).reset_index(drop=True)
    d["bar"] = d.groupby(["symbol", "session"]).cumcount()
    d["bars_in_session"] = d.groupby(["symbol", "session"])["bar"].transform("size")

    d.to_parquet(OUT, index=False)
    full = d[d["bars_in_session"] == SESSION_BARS]
    print(f"\n{len(d):,} hourly bars, {d['symbol'].nunique()} symbols")
    print(f"  {d['session'].nunique()} sessions, "
          f"{d['session'].min()} to {d['session'].max()}")
    print(f"  full {SESSION_BARS}-bar sessions: {full['session'].nunique()} "
          f"({len(full):,} rows)")
    print(f"\n  bars per session distribution:")
    for n, c in d.groupby(["symbol", "session"])["bar"].size().value_counts().head(8).items():
        print(f"    {n} bars: {c:,} sessions")
    print(f"\nwrote {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
