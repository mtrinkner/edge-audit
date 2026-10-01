#!/usr/bin/env python3
"""Phase 1 — load the validated Excel journal into the warehouse.

    python3 python/load_journal.py

Refuses to load a workbook that fails validation. The validator is imported and
run here rather than trusted to have been run earlier, because "I ran it before"
is not a guarantee anyone can check later.

Derived numbers (risk per share, P/L, R multiple) are recomputed in Python and
written to the warehouse. The Excel formula columns are deliberately ignored: a
spreadsheet formula can be overtyped by accident, and a warehouse that inherits
an overtyped cell inherits a silent error.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

import config
import db
import validate_excel as ve


def to_rows(df: pd.DataFrame) -> list[dict]:
    out = []
    for i, r in df.iterrows():
        out.append({**r.to_dict(), "source_row": ve._excel_row(i)})
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--path", type=Path, default=config.JOURNAL_XLSX)
    ap.add_argument("--replace", action="store_true",
                    help="clear journal tables first; the workbook is the source of truth")
    args = ap.parse_args()

    if not args.path.exists():
        print(f"{args.path} not found", file=sys.stderr)
        return 1

    book = pd.read_excel(args.path, sheet_name=None)
    valid_symbols = set(pd.read_csv(config.UNIVERSE_CSV)["symbol"].str.upper())
    rep = ve.Report()
    trades = ve.validate_trades(book["Trades"], valid_symbols, rep)
    obs = ve.validate_observations(book["Observations"], valid_symbols, rep)

    if not rep.ok:
        print(f"validation failed with {len(rep.errors)} error(s). Nothing loaded.",
              file=sys.stderr)
        print("Run python3 python/validate_excel.py to see them.", file=sys.stderr)
        return 1

    if trades.empty and obs.empty:
        print("workbook has no rows to load yet")
        return 0

    db.init_schema()
    with db.transaction() as con:
        if args.replace:
            con.execute("DELETE FROM journal_trades")
            con.execute("DELETE FROM journal_observations")

        n_t = 0
        for row in to_rows(trades):
            direction = ve._as_text(row.get("direction")).lower()
            entry = float(row["entry"])
            stop = float(row["stop"])
            shares = int(row["shares"])
            con.execute(
                """INSERT INTO journal_trades
                   (trade_id, symbol, direction, setup, opened_at, entry, stop,
                    target, shares, closed_at, exit, signal_score, followed_plan,
                    notes, source_row)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(trade_id) DO UPDATE SET
                     symbol=excluded.symbol, direction=excluded.direction,
                     setup=excluded.setup, opened_at=excluded.opened_at,
                     entry=excluded.entry, stop=excluded.stop, target=excluded.target,
                     shares=excluded.shares, closed_at=excluded.closed_at,
                     exit=excluded.exit, signal_score=excluded.signal_score,
                     followed_plan=excluded.followed_plan, notes=excluded.notes,
                     source_row=excluded.source_row""",
                (int(row["trade_id"]), ve._as_text(row["symbol"]).upper(), direction,
                 ve._as_text(row.get("setup")) or None,
                 str(ve._as_date(row["opened_at"])), entry, stop,
                 float(row["target"]) if not pd.isna(row.get("target")) else None,
                 shares,
                 str(ve._as_date(row.get("closed_at"))) if ve._as_date(row.get("closed_at")) else None,
                 float(row["exit"]) if not pd.isna(row.get("exit")) else None,
                 int(row["signal_score"]) if not pd.isna(row.get("signal_score")) else None,
                 int(row["followed_plan"]) if not pd.isna(row.get("followed_plan")) else None,
                 ve._as_text(row.get("notes")) or None,
                 row["source_row"]),
            )
            n_t += 1

        n_o = 0
        for row in to_rows(obs):
            con.execute(
                """INSERT INTO journal_observations
                   (obs_id, symbol, dt, field, value, text_value, source, source_row)
                   VALUES (?,?,?,?,?,?,?,?)
                   ON CONFLICT(symbol, dt, field) DO UPDATE SET
                     value=excluded.value, text_value=excluded.text_value,
                     source=excluded.source, source_row=excluded.source_row""",
                (int(row["obs_id"]) if not pd.isna(row.get("obs_id")) else None,
                 ve._as_text(row["symbol"]).upper(), str(ve._as_date(row["dt"])),
                 ve._as_text(row["field"]),
                 float(row["value"]) if not pd.isna(row.get("value")) else None,
                 ve._as_text(row.get("text_value")) or None,
                 ve._as_text(row["source"]), row["source_row"]),
            )
            n_o += 1

    # Derived performance, recomputed in SQL off the loaded facts so the
    # definition lives next to the data rather than in a spreadsheet cell.
    with db.transaction() as con:
        con.executescript("""
        DROP VIEW IF EXISTS v_journal_performance;
        CREATE VIEW v_journal_performance AS
        SELECT t.*,
               ABS(t.entry - t.stop) AS risk_per_share,
               ABS(t.entry - t.stop) * t.shares AS dollar_risk,
               CASE WHEN t.target IS NOT NULL AND ABS(t.entry - t.stop) > 0
                    THEN ABS(t.target - t.entry) / ABS(t.entry - t.stop) END AS planned_rr,
               CASE WHEN t.exit IS NOT NULL
                    THEN (CASE WHEN t.direction='long' THEN t.exit - t.entry
                               ELSE t.entry - t.exit END) * t.shares END AS gross_pnl,
               CASE WHEN t.exit IS NOT NULL AND ABS(t.entry - t.stop) > 0
                    THEN (CASE WHEN t.direction='long' THEN t.exit - t.entry
                               ELSE t.entry - t.exit END) / ABS(t.entry - t.stop)
               END AS r_multiple,
               CASE WHEN t.exit IS NULL THEN 'open' ELSE 'closed' END AS state
        FROM journal_trades t;
        """)
        closed = con.execute(
            "SELECT COUNT(*) n FROM v_journal_performance WHERE state='closed'").fetchone()["n"]

    print(f"loaded {n_t} trade(s) and {n_o} observation(s)")
    print(f"  {closed} closed trade(s) available in v_journal_performance")
    return 0


if __name__ == "__main__":
    sys.exit(main())
