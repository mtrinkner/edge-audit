#!/usr/bin/env python3
"""Prove the ledger detects tampering.

    python3 tests/test_ledger_integrity.py

An append-only claim nobody tested is a claim. Each case below edits a ledger the
way a person actually would when a result disappoints, and asserts the chain
catches it.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "python"))
import ledger  # noqa: E402

results = []


def check(name: str, passed: bool, detail: str = "") -> None:
    results.append((name, passed))
    print(f"  [{'PASS' if passed else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


def fresh(tmp: Path) -> Path:
    p = tmp / "ledger.jsonl"
    ledger.append([{"kind": "prediction", "symbol": s, "y_prob": v}
                   for s, v in [("AAPL", 0.61), ("MSFT", 0.58), ("NVDA", 0.72)]], p)
    ledger.append([{"kind": "result", "symbol": "AAPL", "net_ret": -0.021}], p)
    return p


def main() -> int:
    print("LEDGER INTEGRITY")
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)

        p = fresh(tmp)
        v = ledger.verify(p)
        check("clean ledger verifies", v.ok, f"{v.entries} entries")

        # 1. Edit a losing result into a winner.
        p = fresh(tmp / "a"); (tmp / "a").mkdir(exist_ok=True); p = fresh(tmp / "a")
        rows = ledger.read_all(p)
        rows[-1]["net_ret"] = 0.045
        p.write_text("\n".join(json.dumps(r, sort_keys=True) for r in rows) + "\n")
        v = ledger.verify(p)
        check("edited result is caught", not v.ok, v.reason[:52])

        # 2. Delete an entry that went badly.
        (tmp / "b").mkdir(exist_ok=True); p = fresh(tmp / "b")
        rows = ledger.read_all(p)
        del rows[1]
        p.write_text("\n".join(json.dumps(r, sort_keys=True) for r in rows) + "\n")
        v = ledger.verify(p)
        check("deleted entry is caught", not v.ok, v.reason[:52])

        # 3. Change a prediction after seeing the outcome.
        (tmp / "c").mkdir(exist_ok=True); p = fresh(tmp / "c")
        rows = ledger.read_all(p)
        rows[0]["y_prob"] = 0.99
        p.write_text("\n".join(json.dumps(r, sort_keys=True) for r in rows) + "\n")
        v = ledger.verify(p)
        check("altered prediction is caught", not v.ok and v.first_bad_line == 1,
              f"flagged at line {v.first_bad_line}")

        # 4. Reorder entries to make a sequence look better.
        (tmp / "d").mkdir(exist_ok=True); p = fresh(tmp / "d")
        rows = ledger.read_all(p)
        rows[0], rows[2] = rows[2], rows[0]
        p.write_text("\n".join(json.dumps(r, sort_keys=True) for r in rows) + "\n")
        v = ledger.verify(p)
        check("reordered entries are caught", not v.ok, v.reason[:52])

        # 5. Legitimate appends must still verify.
        (tmp / "e").mkdir(exist_ok=True); p = fresh(tmp / "e")
        ledger.append([{"kind": "result", "symbol": "MSFT", "net_ret": 0.013}], p)
        ledger.append([{"kind": "result", "symbol": "NVDA", "net_ret": -0.008}], p)
        v = ledger.verify(p)
        check("honest appends still verify", v.ok, f"{v.entries} entries")

    failed = [r for r in results if not r[1]]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
