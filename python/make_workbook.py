#!/usr/bin/env python3
"""Phase 1 — generate the Excel workbook that is the human interface.

    python3 python/make_workbook.py            # create, refusing to clobber
    python3 python/make_workbook.py --force    # recreate from scratch

The workbook is generated, not hand-made, for one reason: a hand-made
spreadsheet drifts from the schema it is supposed to feed, and the drift is
discovered as a load failure weeks later. Generating it means the column
contract and the database contract have a single source in version control.

Sheets:
  README          what each sheet is for, and the rules
  Trades          the trade journal; the main thing a human types into
  Observations    manual market-data notes, long format so new fields need no
                  schema change
  Universe        read-only mirror of data/universe.csv for reference
  Validation      dropdown source lists, kept on its own sheet so the data
                  sheets stay clean

Excel-side defenses: typed columns, dropdown validation on every categorical,
numeric bounds on prices and share counts, and computed columns for risk and
R-multiple so a human can see immediately when an entry contradicts itself.
Python-side validation in validate_excel.py is the real gate; the Excel rules
exist to stop the error at the keyboard instead of at the loader.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

import config

TRADE_COLUMNS = [
    ("trade_id", "Unique integer. Never reuse.", 10),
    ("symbol", "Must exist in the Universe sheet.", 10),
    ("direction", "long or short.", 10),
    ("setup", "Which written rule from strategy-rules.md fired.", 24),
    ("opened_at", "Date you entered (YYYY-MM-DD).", 12),
    ("entry", "Fill price you actually got.", 10),
    ("stop", "Protective stop at entry. Required.", 10),
    ("target", "Profit target at entry.", 10),
    ("shares", "Position size, whole shares.", 9),
    ("closed_at", "Date you exited. Blank while open.", 12),
    ("exit", "Exit fill. Blank while open.", 10),
    ("signal_score", "1-10 from the CONFIRM stage.", 12),
    ("followed_plan", "1 if you followed the plan, 0 if not. Be honest.", 13),
    ("notes", "What happened, in your words.", 40),
]

OBS_COLUMNS = [
    ("obs_id", "Unique integer.", 9),
    ("symbol", "Ticker.", 10),
    ("dt", "Date of the observation (YYYY-MM-DD).", 12),
    ("field", "What you measured. Pick from the dropdown.", 22),
    ("value", "Numeric value, if numeric.", 12),
    ("text_value", "Text value, if not numeric.", 24),
    ("source", "Where it came from. Required for provenance.", 18),
]

OBS_FIELDS = [
    "premarket_volume", "relative_volume", "spread_cents", "float_shares",
    "earnings_date", "catalyst", "sector_strength", "market_breadth",
    "support_level", "resistance_level", "notes",
]
SOURCES = ["broker_platform", "tradingview", "exchange_site", "company_filing", "other"]


def build(path: Path, force: bool) -> int:
    if path.exists() and not force:
        print(f"{path} already exists. Pass --force to recreate it.", file=sys.stderr)
        print("Recreating wipes anything you have typed in.", file=sys.stderr)
        return 1

    universe = pd.read_csv(config.UNIVERSE_CSV)
    config.ensure_dirs()

    with pd.ExcelWriter(path, engine="xlsxwriter") as xl:
        wb = xl.book
        fmt_title = wb.add_format({"bold": True, "font_size": 14})
        fmt_head = wb.add_format({"bold": True, "bg_color": "#1F3864",
                                  "font_color": "white", "border": 1,
                                  "text_wrap": True, "valign": "vcenter"})
        fmt_note = wb.add_format({"italic": True, "font_color": "#555555",
                                  "text_wrap": True, "valign": "top"})
        fmt_date = wb.add_format({"num_format": "yyyy-mm-dd", "border": 1})
        fmt_px = wb.add_format({"num_format": "0.00", "border": 1})
        fmt_int = wb.add_format({"num_format": "0", "border": 1})
        fmt_txt = wb.add_format({"border": 1})
        fmt_calc = wb.add_format({"num_format": "0.00", "border": 1,
                                  "bg_color": "#EFEFEF"})
        fmt_wrap = wb.add_format({"text_wrap": True, "valign": "top"})

        # ------------------------------------------------------------- README
        ws = wb.add_worksheet("README")
        xl.sheets["README"] = ws
        ws.set_column("A:A", 22)
        ws.set_column("B:B", 96, fmt_wrap)
        ws.write("A1", "Trading research workbook", fmt_title)
        rows = [
            ("Purpose", "The human interface to the research pipeline. You type trades and "
                        "observations here. Python validates this file against a data contract "
                        "and loads it into the SQLite warehouse. Nothing is loaded until it passes."),
            ("Trades", "One row per paper trade. Leave closed_at and exit blank while a trade is "
                       "open. risk_per_share, dollar_risk, and r_multiple are computed for you; do "
                       "not type over them."),
            ("Observations", "Manual market data in long format: one row per symbol, date, and "
                             "field. Long format means a new field is a new row, not a schema change."),
            ("Universe", "Read-only. Mirrors data/universe.csv. A symbol not listed here will be "
                         "rejected by the validator."),
            ("Validation", "Dropdown source lists. Do not rename or reorder."),
            ("Rule 1", "Every price you type is a fill you actually observed, not an estimate."),
            ("Rule 2", "followed_plan is the most valuable column in the file. A winning trade "
                       "taken outside your rules is a process failure, and the analysis will treat "
                       "it as one."),
            ("Rule 3", "Do not delete rows to tidy history. A deleted losing trade is a lie to "
                       "yourself that the statistics will inherit."),
            ("Load it", "python3 python/validate_excel.py   then   python3 python/load_journal.py"),
        ]
        for i, (k, v) in enumerate(rows, start=3):
            ws.write(f"A{i}", k, fmt_head)
            ws.write(f"B{i}", v, fmt_note)

        # ------------------------------------------------------------- Trades
        ws = wb.add_worksheet("Trades")
        xl.sheets["Trades"] = ws
        for c, (name, note, width) in enumerate(TRADE_COLUMNS):
            ws.write(0, c, name, fmt_head)
            ws.write_comment(0, c, note)
            ws.set_column(c, c, width)
        calc_start = len(TRADE_COLUMNS)
        for c, name in enumerate(["risk_per_share", "dollar_risk", "planned_rr", "r_multiple"]):
            ws.write(0, calc_start + c, name, fmt_head)
            ws.write_comment(0, calc_start + c,
                             "Computed by formula. Do not type here. The loader ignores "
                             "these columns and recomputes in Python, so a broken formula "
                             "cannot corrupt the warehouse.")
            ws.set_column(calc_start + c, calc_start + c, 14)
        ws.freeze_panes(1, 2)
        ws.set_row(0, 30)

        LAST = 500
        ws.data_validation(f"C2:C{LAST}", {"validate": "list", "source": ["long", "short"]})
        ws.data_validation(f"E2:E{LAST}", {"validate": "date",
                                           "criteria": ">=", "value": config.START_DATE,
                                           "error_message": "Enter a real date, YYYY-MM-DD."})
        for col in ("F", "G", "H", "K"):
            ws.data_validation(f"{col}2:{col}{LAST}",
                               {"validate": "decimal", "criteria": ">", "value": 0,
                                "ignore_blank": True,
                                "error_message": "Prices must be positive."})
        ws.data_validation(f"I2:I{LAST}", {"validate": "integer", "criteria": ">", "value": 0,
                                           "error_message": "Shares must be a positive whole number."})
        ws.data_validation(f"L2:L{LAST}", {"validate": "integer", "criteria": "between",
                                           "minimum": 1, "maximum": 10, "ignore_blank": True,
                                           "error_message": "Signal score is 1 to 10."})
        ws.data_validation(f"M2:M{LAST}", {"validate": "list", "source": [1, 0],
                                           "error_message": "1 or 0."})
        ws.data_validation(f"B2:B{LAST}", {"validate": "list",
                                           "source": "=Universe!$A$2:$A$200",
                                           "error_message": "Symbol must be in the Universe sheet."})
        # The cached value is written as "" rather than xlsxwriter's default 0, so a
        # reader that does not evaluate formulas sees these cells as empty instead
        # of as a real zero. pandas does not evaluate formulas.
        for r in range(1, LAST):
            x = r + 1
            ws.write_formula(r, calc_start,
                             f'=IF(COUNT(F{x},G{x})=2,ABS(F{x}-G{x}),"")', fmt_calc, "")
            ws.write_formula(r, calc_start + 1,
                             f'=IF(COUNT(O{x},I{x})=2,O{x}*I{x},"")', fmt_calc, "")
            ws.write_formula(r, calc_start + 2,
                             f'=IF(AND(COUNT(H{x})=1,O{x}>0),ABS(H{x}-F{x})/O{x},"")',
                             fmt_calc, "")
            ws.write_formula(r, calc_start + 3,
                             f'=IF(AND(COUNT(K{x})=1,O{x}>0),'
                             f'IF(C{x}="long",(K{x}-F{x}),(F{x}-K{x}))/O{x},"")',
                             fmt_calc, "")
        # Visual tripwires: a stop on the wrong side of entry, and a plan not followed.
        ws.conditional_format(f"G2:G{LAST}", {
            "type": "formula",
            "criteria": f'=AND($C2="long",$G2>=$F2)',
            "format": wb.add_format({"bg_color": "#FFC7CE", "font_color": "#9C0006"})})
        ws.conditional_format(f"G2:G{LAST}", {
            "type": "formula",
            "criteria": f'=AND($C2="short",$G2<=$F2)',
            "format": wb.add_format({"bg_color": "#FFC7CE", "font_color": "#9C0006"})})
        ws.conditional_format(f"M2:M{LAST}", {
            "type": "cell", "criteria": "==", "value": 0,
            "format": wb.add_format({"bg_color": "#FFEB9C", "font_color": "#9C6500"})})

        # -------------------------------------------------------- Observations
        ws = wb.add_worksheet("Observations")
        xl.sheets["Observations"] = ws
        for c, (name, note, width) in enumerate(OBS_COLUMNS):
            ws.write(0, c, name, fmt_head)
            ws.write_comment(0, c, note)
            ws.set_column(c, c, width)
        ws.freeze_panes(1, 0)
        ws.set_row(0, 30)
        ws.data_validation(f"B2:B{LAST}", {"validate": "list",
                                           "source": "=Universe!$A$2:$A$200"})
        ws.data_validation(f"D2:D{LAST}", {"validate": "list",
                                           "source": "=Validation!$A$2:$A$50"})
        ws.data_validation(f"G2:G{LAST}", {"validate": "list",
                                           "source": "=Validation!$B$2:$B$20"})

        # ------------------------------------------------------------ Universe
        universe.to_excel(xl, sheet_name="Universe", index=False)
        ws = xl.sheets["Universe"]
        for c, name in enumerate(universe.columns):
            ws.write(0, c, name, fmt_head)
        ws.set_column("A:A", 10); ws.set_column("B:B", 38)
        ws.set_column("C:C", 24); ws.set_column("D:D", 8); ws.set_column("E:E", 22)
        ws.freeze_panes(1, 1)
        ws.autofilter(0, 0, len(universe), len(universe.columns) - 1)

        # ---------------------------------------------------------- Validation
        ws = wb.add_worksheet("Validation")
        xl.sheets["Validation"] = ws
        ws.write(0, 0, "obs_field", fmt_head)
        ws.write(0, 1, "source", fmt_head)
        for i, v in enumerate(OBS_FIELDS, start=1):
            ws.write(i, 0, v)
        for i, v in enumerate(SOURCES, start=1):
            ws.write(i, 1, v)
        ws.set_column("A:B", 22)

    print(f"created {path}")
    print(f"  sheets: README, Trades, Observations, Universe ({len(universe)} symbols), Validation")
    print("  next: type a trade, then run python3 python/validate_excel.py")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--force", action="store_true", help="overwrite an existing workbook")
    ap.add_argument("--path", type=Path, default=config.JOURNAL_XLSX)
    args = ap.parse_args()
    return build(args.path, args.force)


if __name__ == "__main__":
    sys.exit(main())
