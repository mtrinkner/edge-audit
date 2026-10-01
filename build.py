#!/usr/bin/env python3
"""One command to rebuild the entire project from source.

    python3 build.py              # everything except re-downloading bars
    python3 build.py --ingest     # include the data download
    python3 build.py --from sql   # start at a given stage

Reproducibility is the point. A reviewer should be able to clone this, run one
command, and land on the same numbers. Any stage that cannot be re-run from
source is a stage whose output nobody can check.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PY = sys.executable

STAGES = ["ingest", "sql", "journal", "test", "model", "report"]


def run(label: str, cmd: list[str]) -> float:
    print(f"\n{'=' * 70}\n{label}\n{'=' * 70}", flush=True)
    t0 = time.time()
    proc = subprocess.run(cmd, cwd=ROOT)
    dt = time.time() - t0
    if proc.returncode != 0:
        print(f"\nFAILED: {label} (exit {proc.returncode}) after {dt:.1f}s", file=sys.stderr)
        sys.exit(proc.returncode)
    print(f"\n  done in {dt:.1f}s")
    return dt


def run_sql(label: str, files: list[str]) -> float:
    print(f"\n{'=' * 70}\n{label}\n{'=' * 70}", flush=True)
    t0 = time.time()
    for f in files:
        path = ROOT / "sql" / f
        print(f"  applying {f}", flush=True)
        proc = subprocess.run(
            ["sqlite3", str(ROOT / "data" / "warehouse.db")],
            stdin=path.open(), cwd=ROOT,
        )
        if proc.returncode != 0:
            print(f"\nFAILED applying {f}", file=sys.stderr)
            sys.exit(proc.returncode)
    dt = time.time() - t0
    print(f"\n  done in {dt:.1f}s")
    return dt


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--ingest", action="store_true",
                    help="re-download bars (slow, hits the network)")
    ap.add_argument("--from", dest="start", choices=STAGES, default="sql",
                    help="first stage to run")
    args = ap.parse_args()

    order = STAGES[STAGES.index(args.start):]
    timings: dict[str, float] = {}

    if args.ingest and "ingest" in order:
        timings["ingest"] = run("STAGE 1  ingest daily bars",
                                [PY, "python/ingest_bars.py", "--all"])

    if "sql" in order:
        timings["sql"] = run_sql(
            "STAGE 2  schema, features, materialization, labels",
            ["01_schema.sql", "02_features.sql", "03_materialize.sql", "04_labels.sql"])

    if "journal" in order:
        timings["journal"] = run("STAGE 3  validate and load the Excel journal",
                                 [PY, "python/load_journal.py"])

    if "test" in order:
        timings["test"] = run("STAGE 4  lookahead audit",
                              [PY, "tests/test_no_lookahead.py"])

    if "model" in order:
        timings["model"] = run("STAGE 5  walk-forward modeling",
                               [PY, "python/model.py"])
        timings["backtest"] = run("STAGE 6  cost-aware portfolio backtest",
                                  [PY, "python/backtest.py", "--model", "gbm"])
        run_sql("STAGE 7  analysis views", ["05_metrics.sql"])
        timings["sensitivity"] = run("STAGE 8  parameter robustness sweep",
                                     [PY, "python/sensitivity.py"])
        timings["validate"] = run("STAGE 9  statistical validation (R)",
                                  ["Rscript", "R/validate.R"])

    if "report" in order:
        timings["figures"] = run("STAGE 10  figures (R)", ["Rscript", "R/figures.R"])
        timings["dashboard"] = run("STAGE 11  Excel dashboard",
                                   [PY, "python/export_excel.py"])

    print(f"\n{'=' * 70}\nBUILD COMPLETE\n{'=' * 70}")
    for k, v in timings.items():
        print(f"  {k:<10} {v:6.1f}s")
    print(f"  {'total':<10} {sum(timings.values()):6.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
