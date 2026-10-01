#!/usr/bin/env python3
"""Phase 1 — the data contract between Excel and the warehouse.

    python3 python/validate_excel.py             # report problems, change nothing
    python3 python/validate_excel.py --strict    # exit 1 on warnings too

Nothing reaches the database until this passes. Every finding names the exact
spreadsheet row so a human can go fix it, which is the difference between a
validator people use and one they route around.

The checks are split into errors, which block the load, and warnings, which are
suspicious but legal. Treating every anomaly as fatal trains people to pass
--force; treating none as fatal lets corrupt rows into the sample.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

import pandas as pd

import config


# Columns a human types. Rows blank across ALL of these are empty rows, even
# though the computed formula columns next to them hold a cached value. Judging
# emptiness on every column makes the 500 pre-filled formula rows look like 500
# incomplete trades.
TRADE_INPUT_COLS = ["trade_id", "symbol", "direction", "setup", "opened_at",
                    "entry", "stop", "target", "shares", "closed_at", "exit",
                    "signal_score", "followed_plan", "notes"]
OBS_INPUT_COLS = ["obs_id", "symbol", "dt", "field", "value", "text_value", "source"]
MAX_SHOWN = 25


def drop_blank_rows(df: pd.DataFrame, input_cols: list[str]) -> pd.DataFrame:
    """Keep only rows with at least one human-entered value."""
    present = [c for c in input_cols if c in df.columns]
    if not present:
        return df.iloc[0:0]
    sub = df[present]
    # Build the mask column by column and stay in boolean dtype throughout.
    # Combining a boolean frame with a string comparison via | is deprecated in
    # pandas 4 and raises later, so each column is reduced to bool first.
    blank = pd.DataFrame(
        {c: sub[c].isna() | (sub[c].astype("string").str.strip() == "").fillna(True)
         for c in present},
        index=sub.index,
    )
    return df[~blank.all(axis=1)].copy()


@dataclass
class Report:
    errors: list[tuple[int, str, str]] = field(default_factory=list)
    warnings: list[tuple[int, str, str]] = field(default_factory=list)

    def err(self, row: int, col: str, msg: str) -> None:
        self.errors.append((row, col, msg))

    def warn(self, row: int, col: str, msg: str) -> None:
        self.warnings.append((row, col, msg))

    @property
    def ok(self) -> bool:
        return not self.errors


def _excel_row(idx: int) -> int:
    """pandas index to the row number a human sees (header is row 1)."""
    return idx + 2


def _as_text(v) -> str:
    """Coerce a cell to clean text. Pandas turns an empty cell into NaN, and
    str(NaN) is the truthy string "nan", so a naive truthiness check treats a
    blank cell as filled. This bug was caught by the validator's own test
    fixture, which planted a row with no value and no text value and saw only
    one of the two expected errors."""
    if v is None:
        return ""
    try:
        if pd.isna(v):
            return ""
    except (TypeError, ValueError):
        pass
    t = str(v).strip()
    return "" if t.lower() in {"nan", "none", "nat"} else t


def _as_date(v) -> date | None:
    if v is None or (isinstance(v, float) and pd.isna(v)) or v == "":
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    try:
        return pd.to_datetime(v).date()
    except Exception:
        return None


def validate_trades(df: pd.DataFrame, valid_symbols: set[str], rep: Report) -> pd.DataFrame:
    required = ["trade_id", "symbol", "direction", "opened_at", "entry", "stop", "shares"]
    for c in required:
        if c not in df.columns:
            rep.err(1, c, "required column missing from the Trades sheet")
    if rep.errors:
        return pd.DataFrame()

    df = drop_blank_rows(df, TRADE_INPUT_COLS)
    if df.empty:
        return df

    seen_ids: dict = {}
    for i, r in df.iterrows():
        row = _excel_row(i)

        tid = r.get("trade_id")
        if pd.isna(tid):
            rep.err(row, "trade_id", "blank trade_id")
        else:
            try:
                tid = int(tid)
                if tid in seen_ids:
                    rep.err(row, "trade_id",
                            f"duplicate of row {seen_ids[tid]}; ids must be unique")
                seen_ids[tid] = row
            except (TypeError, ValueError):
                rep.err(row, "trade_id", f"not an integer: {tid!r}")

        sym = _as_text(r.get("symbol")).upper()
        if not sym:
            rep.err(row, "symbol", "blank symbol")
        elif sym not in valid_symbols:
            rep.err(row, "symbol",
                    f"{sym} is not in universe.csv; add it there before logging trades")

        direction = _as_text(r.get("direction")).lower()
        if direction not in {"long", "short"}:
            rep.err(row, "direction", f"must be long or short, got {direction!r}")

        opened = _as_date(r.get("opened_at"))
        if opened is None:
            rep.err(row, "opened_at", f"unparseable date: {r.get('opened_at')!r}")
        elif opened > date.today():
            rep.err(row, "opened_at", f"{opened} is in the future")

        entry, stop, target = r.get("entry"), r.get("stop"), r.get("target")
        for name, v in (("entry", entry), ("stop", stop)):
            if pd.isna(v):
                rep.err(row, name, "required and blank")
            elif float(v) <= 0:
                rep.err(row, name, f"must be positive, got {v}")

        if not pd.isna(entry) and not pd.isna(stop) and direction in {"long", "short"}:
            e, s = float(entry), float(stop)
            if direction == "long" and s >= e:
                rep.err(row, "stop", f"long trade with stop {s} at or above entry {e}")
            if direction == "short" and s <= e:
                rep.err(row, "stop", f"short trade with stop {s} at or below entry {e}")
            if e > 0 and abs(e - s) / e > 0.25:
                rep.warn(row, "stop", f"stop is {abs(e-s)/e:.0%} away from entry; verify")

        if not pd.isna(target) and not pd.isna(entry) and direction in {"long", "short"}:
            t, e = float(target), float(entry)
            if direction == "long" and t <= e:
                rep.err(row, "target", f"long target {t} at or below entry {e}")
            if direction == "short" and t >= e:
                rep.err(row, "target", f"short target {t} at or above entry {e}")

        shares = r.get("shares")
        if pd.isna(shares):
            rep.err(row, "shares", "required and blank")
        else:
            try:
                sh = int(shares)
                if sh <= 0:
                    rep.err(row, "shares", f"must be positive, got {sh}")
                elif not pd.isna(entry) and float(entry) * sh > config.ACCOUNT_SIZE * 4:
                    rep.warn(row, "shares",
                             f"notional {float(entry)*sh:,.0f} is over 4x the "
                             f"{config.ACCOUNT_SIZE:,.0f} account; check the size")
            except (TypeError, ValueError):
                rep.err(row, "shares", f"not an integer: {shares!r}")

        closed, exit_px = _as_date(r.get("closed_at")), r.get("exit")
        has_close, has_exit = closed is not None, not pd.isna(exit_px)
        if has_close != has_exit:
            rep.err(row, "closed_at/exit",
                    "a closed trade needs both closed_at and exit; an open trade needs neither")
        if has_close and opened and closed < opened:
            rep.err(row, "closed_at", f"{closed} is before opened_at {opened}")
        if has_exit and float(exit_px) <= 0:
            rep.err(row, "exit", f"must be positive, got {exit_px}")

        score = r.get("signal_score")
        if not pd.isna(score):
            try:
                sc = int(score)
                if not 1 <= sc <= 10:
                    rep.err(row, "signal_score", f"must be 1-10, got {sc}")
            except (TypeError, ValueError):
                rep.err(row, "signal_score", f"not an integer: {score!r}")

        fp = r.get("followed_plan")
        if pd.isna(fp):
            rep.warn(row, "followed_plan",
                     "blank; this is the most useful column in the file, fill it in")
        elif int(fp) not in (0, 1):
            rep.err(row, "followed_plan", f"must be 1 or 0, got {fp}")

    return df


def validate_observations(df: pd.DataFrame, valid_symbols: set[str], rep: Report) -> pd.DataFrame:
    df = drop_blank_rows(df, OBS_INPUT_COLS)
    if df.empty:
        return df
    seen: dict = {}
    for i, r in df.iterrows():
        row = _excel_row(i)
        sym = _as_text(r.get("symbol")).upper()
        if sym and sym not in valid_symbols:
            rep.err(row, "symbol", f"{sym} is not in universe.csv")
        dt = _as_date(r.get("dt"))
        if dt is None:
            rep.err(row, "dt", f"unparseable date: {r.get('dt')!r}")
        fld = _as_text(r.get("field"))
        if not fld:
            rep.err(row, "field", "blank field name")
        if pd.isna(r.get("value")) and not _as_text(r.get("text_value")):
            rep.err(row, "value", "both value and text_value are blank")
        src = _as_text(r.get("source"))
        if not src:
            rep.err(row, "source", "blank; provenance is required for manual data")
        key = (sym, str(dt), fld)
        if key in seen:
            rep.err(row, "symbol/dt/field",
                    f"duplicate of row {seen[key]}; one value per symbol, date, field")
        seen[key] = row
    return df


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--path", type=Path, default=config.JOURNAL_XLSX)
    ap.add_argument("--strict", action="store_true", help="treat warnings as failures")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args()

    if not args.path.exists():
        print(f"{args.path} not found. Create it with python3 python/make_workbook.py",
              file=sys.stderr)
        return 1

    book = pd.read_excel(args.path, sheet_name=None)
    missing_sheets = {"Trades", "Observations", "Universe"} - set(book)
    if missing_sheets:
        print(f"workbook is missing sheets: {sorted(missing_sheets)}", file=sys.stderr)
        return 1

    valid_symbols = set(pd.read_csv(config.UNIVERSE_CSV)["symbol"].str.upper())
    rep = Report()
    trades = validate_trades(book["Trades"], valid_symbols, rep)
    obs = validate_observations(book["Observations"], valid_symbols, rep)

    if args.json:
        print(json.dumps({
            "ok": rep.ok, "trades": len(trades), "observations": len(obs),
            "errors": [{"row": r, "column": c, "message": m} for r, c, m in rep.errors],
            "warnings": [{"row": r, "column": c, "message": m} for r, c, m in rep.warnings],
        }, indent=2))
    else:
        print(f"validating {args.path.name}")
        print(f"  Trades: {len(trades)} rows | Observations: {len(obs)} rows")
        def show(title: str, items: list[tuple[int, str, str]]) -> None:
            print(f"\n{title}")
            for r, c, m in items[:MAX_SHOWN]:
                print(f"  row {r:>4}  {c:<16} {m}")
            if len(items) > MAX_SHOWN:
                rest = len(items) - MAX_SHOWN
                print(f"  ... and {rest} more. Counts by column:")
                counts: dict[str, int] = {}
                for _, c, _ in items:
                    counts[c] = counts.get(c, 0) + 1
                for c, n in sorted(counts.items(), key=lambda kv: -kv[1]):
                    print(f"       {c:<18} {n}")
                print("  Fix the most common column first; many of these are the same mistake.")

        if rep.errors:
            show(f"{len(rep.errors)} ERROR(S) — nothing will be loaded:", rep.errors)
        if rep.warnings:
            show(f"{len(rep.warnings)} warning(s):", rep.warnings)
        if rep.ok and not rep.warnings:
            print("\nclean. safe to load with python3 python/load_journal.py")
        elif rep.ok:
            print("\nno blocking errors. safe to load.")

    if not rep.ok:
        return 1
    return 1 if (args.strict and rep.warnings) else 0


if __name__ == "__main__":
    sys.exit(main())
