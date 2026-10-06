#!/usr/bin/env python3
"""Phase 2 — download daily bars and load them into the warehouse idempotently.

    python3 python/ingest_bars.py --snapshot      # the pinned bars, no network
    python3 python/ingest_bars.py --all           # fresh download
    python3 python/ingest_bars.py --symbols AAPL MSFT --start 2020-01-01

Design points worth defending in an interview:

  * Idempotent. Re-running replays the same UPSERT and leaves row counts
    unchanged. Pipelines that create duplicates on retry cannot be trusted.
  * Every load is wrapped in an ingest_run row, so a partial failure is visible
    afterward rather than inferred from a gap in the data.
  * Quality checks run at write time and persist their findings as rows in
    data_quality_issues. The checks look for the failures that actually corrupt
    a backtest: zero volume days, impossible OHLC ordering, extreme one-day
    moves that usually mean an unadjusted split, and calendar gaps.
  * A pinned SNAPSHOT of the exact bars behind the published results ships in
    data/raw/. yfinance revises adjusted closes over time, so a fresh download
    does not reproduce the README numbers. Anyone can check the published result
    with --snapshot, and test whether it survives new data with --all.
  * Raw and adjusted closes are both stored. Returns must be computed from
    adjusted prices or every dividend looks like a loss; position sizing must
    use raw prices or share counts are fictional. Keeping one of the two is a
    silent bug.
"""

from __future__ import annotations

import argparse
import csv
import sys
from datetime import date, datetime
from pathlib import Path

import pandas as pd

import config
import db

SPLIT_MOVE_THRESHOLD = 0.35  # one-day move this large usually means bad adjustment
MAX_CALENDAR_GAP_DAYS = 10   # business-day gap larger than this is suspicious


def load_universe(path: Path = None) -> pd.DataFrame:
    path = path or config.UNIVERSE_CSV
    df = pd.read_csv(path)
    required = {"symbol", "name", "sector", "kind", "inclusion_reason"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"universe.csv missing columns: {sorted(missing)}")
    if df["symbol"].duplicated().any():
        dupes = df.loc[df["symbol"].duplicated(), "symbol"].tolist()
        raise ValueError(f"duplicate symbols in universe.csv: {dupes}")
    return df


def upsert_symbols(con, universe: pd.DataFrame) -> None:
    con.executemany(
        """
        INSERT INTO symbols (symbol, name, sector, kind, inclusion_reason)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(symbol) DO UPDATE SET
            name = excluded.name,
            sector = excluded.sector,
            kind = excluded.kind,
            inclusion_reason = excluded.inclusion_reason
        """,
        universe[["symbol", "name", "sector", "kind", "inclusion_reason"]]
        .itertuples(index=False, name=None),
    )


def download(symbols: list[str], start: date, end: date) -> pd.DataFrame:
    import yfinance as yf

    frames = []
    for i in range(0, len(symbols), config.INGEST.batch_size):
        batch = symbols[i : i + config.INGEST.batch_size]
        print(f"  downloading {len(batch)} symbols: {batch[0]}..{batch[-1]}", flush=True)
        raw = yf.download(
            tickers=batch,
            start=start.isoformat(),
            end=end.isoformat(),
            interval=config.INGEST.interval,
            auto_adjust=config.INGEST.auto_adjust,
            group_by="ticker",
            progress=False,
            threads=True,
        )
        if raw is None or raw.empty:
            print(f"  WARNING empty response for batch {batch}", file=sys.stderr)
            continue
        for sym in batch:
            if isinstance(raw.columns, pd.MultiIndex):
                if sym not in raw.columns.get_level_values(0):
                    continue
                sub = raw[sym].copy()
            else:
                sub = raw.copy()
            sub = sub.dropna(how="all")
            if sub.empty:
                continue
            sub["symbol"] = sym
            frames.append(sub.reset_index())
    if not frames:
        return pd.DataFrame()
    out = pd.concat(frames, ignore_index=True)
    out.columns = [str(c).lower().replace(" ", "_") for c in out.columns]
    return out


def normalize(df: pd.DataFrame) -> pd.DataFrame:
    """Map provider columns onto the warehouse contract and drop unusable rows."""
    if df.empty:
        return df
    rename = {"date": "dt", "adj_close": "adj_close", "close": "close"}
    df = df.rename(columns=rename)
    if "adj_close" not in df.columns:
        # auto_adjust=True collapses close and adj_close into one column.
        df["adj_close"] = df["close"]
    df["dt"] = pd.to_datetime(df["dt"], utc=True).dt.tz_localize(None).dt.date.astype(str)
    keep = ["symbol", "dt", "open", "high", "low", "close", "adj_close", "volume"]
    df = df[keep]
    before = len(df)
    df = df.dropna(subset=["open", "high", "low", "close", "adj_close"])
    df = df[(df[["open", "high", "low", "close", "adj_close"]] > 0).all(axis=1)]
    df["volume"] = df["volume"].fillna(0).astype("int64")
    df = df.drop_duplicates(subset=["symbol", "dt"], keep="last")
    dropped = before - len(df)
    if dropped:
        print(f"  dropped {dropped} unusable rows (null or non-positive prices)")
    return df.sort_values(["symbol", "dt"]).reset_index(drop=True)


def quality_checks(df: pd.DataFrame) -> list[tuple]:
    """Return (symbol, dt, severity, issue, detail) rows. Checks, not opinions."""
    issues: list[tuple] = []
    if df.empty:
        return issues

    bad_ohlc = df[
        (df["high"] < df["low"])
        | (df["high"] < df["open"])
        | (df["high"] < df["close"])
        | (df["low"] > df["open"])
        | (df["low"] > df["close"])
    ]
    for r in bad_ohlc.itertuples():
        issues.append(
            (r.symbol, r.dt, "error", "ohlc_ordering",
             f"o={r.open} h={r.high} l={r.low} c={r.close}")
        )

    zero_vol = df[df["volume"] == 0]
    for r in zero_vol.itertuples():
        issues.append((r.symbol, r.dt, "warn", "zero_volume", "volume == 0"))

    for sym, g in df.groupby("symbol", sort=False):
        g = g.sort_values("dt")
        move = g["adj_close"].pct_change().abs()
        for dt_, m in zip(g["dt"].iloc[1:], move.iloc[1:]):
            if pd.notna(m) and m > SPLIT_MOVE_THRESHOLD:
                issues.append(
                    (sym, dt_, "warn", "extreme_move",
                     f"one-day adj_close move of {m:.1%}; check split adjustment")
                )
        gaps = pd.to_datetime(g["dt"]).diff().dt.days
        for dt_, gap in zip(g["dt"].iloc[1:], gaps.iloc[1:]):
            if pd.notna(gap) and gap > MAX_CALENDAR_GAP_DAYS:
                issues.append(
                    (sym, dt_, "warn", "calendar_gap",
                     f"{int(gap)} calendar days since previous bar")
                )
    return issues


def rebuild_trading_days(con) -> int:
    """Derive the calendar from observed bars and assign a dense ordinal index.

    day_index is what makes forward returns correct. Lagging by calendar date
    silently skips holidays; lagging by row number inside a symbol breaks when a
    symbol is missing a day the rest of the market traded.
    """
    con.execute("DELETE FROM trading_days")
    con.execute(
        """
        INSERT INTO trading_days (dt, day_index, year, month, dow)
        SELECT dt,
               ROW_NUMBER() OVER (ORDER BY dt) - 1          AS day_index,
               CAST(strftime('%Y', dt) AS INTEGER)          AS year,
               CAST(strftime('%m', dt) AS INTEGER)          AS month,
               CAST(strftime('%w', dt) AS INTEGER)          AS dow
        FROM (SELECT DISTINCT dt FROM bars)
        ORDER BY dt
        """
    )
    return con.execute("SELECT COUNT(*) n FROM trading_days").fetchone()["n"]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--symbols", nargs="*", help="subset of the universe to load")
    ap.add_argument("--all", action="store_true", help="load the full universe")
    ap.add_argument("--start", default=config.START_DATE.isoformat())
    ap.add_argument("--end", default=config.END_DATE.isoformat())
    ap.add_argument("--save-raw", action="store_true", help="also write a CSV snapshot")
    ap.add_argument("--snapshot", action="store_true",
                    help="load the pinned snapshot instead of downloading")
    args = ap.parse_args()

    if not args.symbols and not args.all and not args.snapshot:
        ap.error("pass --snapshot, --all, or --symbols")

    config.ensure_dirs()
    db.init_schema()
    universe = load_universe()
    symbols = args.symbols if args.symbols else universe["symbol"].tolist()
    unknown = sorted(set(symbols) - set(universe["symbol"]))
    if unknown:
        ap.error(f"symbols not in universe.csv: {unknown}. Add them there first.")

    start = datetime.fromisoformat(args.start).date()
    end = datetime.fromisoformat(args.end).date()

    with db.transaction() as con:
        upsert_symbols(con, universe)
        cur = con.execute(
            """
            INSERT INTO ingest_runs
                (source, interval, requested_start, requested_end,
                 symbols_requested, status)
            VALUES (?, ?, ?, ?, ?, 'running')
            """,
            (config.INGEST.source, config.INGEST.interval,
             start.isoformat(), end.isoformat(), len(symbols)),
        )
        run_id = cur.lastrowid
    print(f"ingest run {run_id}: {len(symbols)} symbols, {start} to {end}")

    try:
        if args.snapshot:
            snap = config.RAW / "bars_snapshot.csv.gz"
            if not snap.exists():
                raise RuntimeError(f"{snap} not found")
            print(f"  loading pinned snapshot {snap.name} (no network)")
            df = pd.read_csv(snap, compression="gzip")
            df = df[["symbol", "dt", "open", "high", "low", "close",
                     "adj_close", "volume"]]
        else:
            raw = download(symbols, start, end)
            df = normalize(raw)
        if df.empty:
            raise RuntimeError("no rows returned from the data source")

        issues = quality_checks(df)
        hard_errors = [i for i in issues if i[2] == "error"]
        if hard_errors:
            df = df.merge(
                pd.DataFrame(
                    [(i[0], i[1]) for i in hard_errors], columns=["symbol", "dt"]
                ).assign(_bad=1),
                on=["symbol", "dt"],
                how="left",
            )
            df = df[df["_bad"].isna()].drop(columns=["_bad"])
            print(f"  quarantined {len(hard_errors)} rows failing OHLC checks")

        df["run_id"] = run_id
        with db.transaction() as con:
            con.executemany(
                """
                INSERT INTO bars
                    (symbol, dt, open, high, low, close, adj_close, volume, run_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(symbol, dt) DO UPDATE SET
                    open=excluded.open, high=excluded.high, low=excluded.low,
                    close=excluded.close, adj_close=excluded.adj_close,
                    volume=excluded.volume, run_id=excluded.run_id
                """,
                df[["symbol", "dt", "open", "high", "low", "close",
                    "adj_close", "volume", "run_id"]].itertuples(index=False, name=None),
            )
            if issues:
                con.executemany(
                    """INSERT INTO data_quality_issues
                       (run_id, symbol, dt, severity, issue, detail)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    [(run_id, *i) for i in issues],
                )
            n_days = rebuild_trading_days(con)
            con.execute(
                """UPDATE ingest_runs
                   SET status=?, symbols_loaded=?, rows_loaded=?,
                       finished_at=datetime('now'), notes=?
                   WHERE run_id=?""",
                ("ok" if not hard_errors else "partial",
                 df["symbol"].nunique(), len(df),
                 f"{len(issues)} quality issues; {n_days} trading days", run_id),
            )

        if args.save_raw:
            snap = config.RAW / f"bars_run{run_id}.csv"
            df.to_csv(snap, index=False)
            print(f"  raw snapshot -> {snap}")

        print(f"\nloaded {len(df):,} rows for {df['symbol'].nunique()} symbols")
        print(f"calendar: {n_days:,} trading days")
        sev = pd.DataFrame(issues, columns=["symbol", "dt", "severity", "issue", "detail"])
        if not sev.empty:
            print("\nquality issues by type:")
            print(sev.groupby(["severity", "issue"]).size().to_string())
        return 0

    except Exception as exc:
        with db.transaction() as con:
            con.execute(
                """UPDATE ingest_runs SET status='failed',
                   finished_at=datetime('now'), notes=? WHERE run_id=?""",
                (str(exc)[:500], run_id),
            )
        print(f"ingest run {run_id} FAILED: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
