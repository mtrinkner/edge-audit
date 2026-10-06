"""Append-only, hash-chained ledger for the forward test.

The ledger is the evidence. Everything that makes a forward test worth more than
a backtest depends on it being written before outcomes are known and never
quietly edited afterward.

So each entry carries the SHA-256 of the entry before it. Changing or deleting
any past row breaks every hash after it, and `verify()` reports exactly where.
This does not make tampering impossible, since someone could rewrite the whole
chain. It makes CASUAL tampering detectable, which is the realistic failure:
deleting a bad month, nudging a threshold after seeing results, re-running a
prediction that went wrong. A record you cannot check is a record that quietly
becomes a story about how well things went.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import config

LEDGER = config.ROOT / "data" / "forward_ledger.jsonl"
GENESIS = "0" * 64


def _canonical(entry: dict) -> str:
    """Hash input excludes the hash fields themselves, with sorted keys so the
    digest does not depend on dict insertion order."""
    payload = {k: v for k, v in entry.items() if k not in ("entry_hash", "prev_hash")}
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def entry_hash(entry: dict, prev_hash: str) -> str:
    h = hashlib.sha256()
    h.update(prev_hash.encode())
    h.update(_canonical(entry).encode())
    return h.hexdigest()


def read_all(path: Path | None = None) -> list[dict]:
    path = path or LEDGER
    if not path.exists():
        return []
    rows = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def last_hash(path: Path | None = None) -> str:
    rows = read_all(path)
    return rows[-1]["entry_hash"] if rows else GENESIS


def append(entries: list[dict], path: Path | None = None) -> int:
    """Append entries, chaining each to the previous. Returns how many landed."""
    path = path or LEDGER
    path.parent.mkdir(parents=True, exist_ok=True)
    prev = last_hash(path)
    lines = []
    for e in entries:
        e = dict(e)
        e.setdefault("written_at",
                     datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"))
        e["prev_hash"] = prev
        e["entry_hash"] = entry_hash(e, prev)
        prev = e["entry_hash"]
        lines.append(json.dumps(e, sort_keys=True))
    with path.open("a") as fh:
        for ln in lines:
            fh.write(ln + "\n")
    return len(lines)


@dataclass
class VerifyResult:
    ok: bool
    entries: int
    first_bad_line: int | None = None
    reason: str = ""

    def __str__(self) -> str:
        if self.ok:
            return f"ledger intact: {self.entries} entries, hash chain verified"
        return (f"LEDGER BROKEN at line {self.first_bad_line} of {self.entries}: "
                f"{self.reason}")


def verify(path: Path | None = None) -> VerifyResult:
    rows = read_all(path)
    prev = GENESIS
    for i, e in enumerate(rows, start=1):
        if e.get("prev_hash") != prev:
            return VerifyResult(False, len(rows), i,
                                "prev_hash does not match the preceding entry; "
                                "an earlier entry was edited or removed")
        if entry_hash(e, prev) != e.get("entry_hash"):
            return VerifyResult(False, len(rows), i,
                                "entry_hash does not match the entry contents; "
                                "this row was modified after it was written")
        prev = e["entry_hash"]
    return VerifyResult(True, len(rows))
