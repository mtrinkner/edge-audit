#!/usr/bin/env python3
"""Data you are not allowed to look at until you have one final answer.

    python3 python/lockbox.py status
    python3 python/lockbox.py seal --from 2026-10-07
    python3 python/lockbox.py check --max-date 2026-09-30
    python3 python/lockbox.py open --strategy-id 7 --reason "final candidate"

WHY. The registry counts how many strategies you tried and deflated Sharpe raises
the bar accordingly, but both are estimates. The only thing that settles the
question is data your search never touched. One look, one answer, no revisions.

AN HONEST LIMITATION, STATED PLAINLY. Every bar from 2014 to 2026 in this project
has already been used. The backtest searched it, the parameter sweep searched it
45 more times, and the forward test scored 2026 as a holdout. None of it can
serve as a clean lockbox now, and pretending otherwise would be the exact
self-deception this file exists to prevent.

So the lockbox is forward dated. It seals everything from the day you declare it,
and it fills up as time passes. That is slower than carving a slice out of
history, and it is the only version that is actually clean.

Opening is a one-way door. It is recorded in the registry with the strategy it
was opened for, and from then on that data is burned for every future search.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime, timezone

import config
import ledger
import registry


class LockboxViolation(Exception):
    """Raised when a search touches sealed data."""


def _events() -> list[dict]:
    return [r for r in registry.entries() if r["kind"] in ("seal", "open")]


def state() -> dict:
    seals = [r for r in _events() if r["kind"] == "seal"]
    opens = [r for r in _events() if r["kind"] == "open"]
    return {
        "sealed": bool(seals),
        "seal_from": seals[-1]["seal_from"] if seals else None,
        "sealed_at": seals[-1]["written_at"] if seals else None,
        "opened": [{"at": o["written_at"], "strategy_id": o.get("strategy_id"),
                    "reason": o.get("reason")} for o in opens],
    }


def guard(max_date: str | date, context: str = "search") -> None:
    """Raise if `max_date` reaches into sealed data. Call this from any search."""
    s = state()
    if not s["sealed"]:
        return
    if s["opened"]:
        return                      # already burned; nothing left to protect
    md = max_date.isoformat() if isinstance(max_date, date) else str(max_date)
    if md >= s["seal_from"]:
        raise LockboxViolation(
            f"{context} tried to read data through {md}, but the lockbox seals "
            f"everything from {s['seal_from']}.\n"
            f"Sealed on {s['sealed_at']}. Restrict the query to earlier dates, or "
            f"open the lockbox deliberately with:\n"
            f"  python3 python/lockbox.py open --strategy-id <id> --reason \"...\"\n"
            f"Opening is permanent and burns this data for every future search."
        )


def cmd_seal(args) -> int:
    s = state()
    if s["sealed"] and not args.force:
        print(f"already sealed from {s['seal_from']} on {s['sealed_at']}", file=sys.stderr)
        print("Re-sealing would reset a commitment you already made. Pass --force "
              "only if you understand that.", file=sys.stderr)
        return 1
    frm = args.frm or date.today().isoformat()
    ledger.append([{
        "kind": "seal", "seal_from": frm,
        "note": args.note or "",
        "declared_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
    }], registry.REGISTRY)
    print(f"lockbox sealed from {frm}")
    print("Nothing in the search path may read data on or after that date.")
    print("It fills as time passes. Check back when you have a final candidate.")
    return 0


def cmd_open(args) -> int:
    s = state()
    if not s["sealed"]:
        print("nothing is sealed", file=sys.stderr)
        return 1
    if s["opened"]:
        print(f"already opened on {s['opened'][-1]['at']}. There is nothing left "
              "to protect; this data is burned.", file=sys.stderr)
        return 1
    t = registry.trials()
    print(f"About to open the lockbox for strategy {args.strategy_id}.")
    print(f"  sealed from     {s['seal_from']}")
    print(f"  trials searched {t['n_trials']}")
    print("  This is permanent. After this, the data is part of your search\n"
          "  history and can never again serve as clean evidence.")
    ledger.append([{
        "kind": "open", "strategy_id": args.strategy_id,
        "reason": args.reason, "trials_at_open": t["n_trials"],
        "seal_from": s["seal_from"],
    }], registry.REGISTRY)
    print("\nopened and recorded. Whatever it says is the answer. No second look.")
    return 0


def cmd_check(args) -> int:
    try:
        guard(args.max_date, "check")
    except LockboxViolation as e:
        print(e, file=sys.stderr)
        return 1
    print(f"ok: {args.max_date} does not touch the lockbox")
    return 0


def cmd_status(_args) -> int:
    v = ledger.verify(registry.REGISTRY)
    print(v)
    s = state()
    if not s["sealed"]:
        print("\nno lockbox sealed yet")
        print("  python3 python/lockbox.py seal")
        return 0
    print(f"\nsealed from : {s['seal_from']}")
    print(f"sealed at   : {s['sealed_at']}")
    today = date.today().isoformat()
    if today > s["seal_from"]:
        days = (date.today() - datetime.fromisoformat(s["seal_from"]).date()).days
        print(f"accumulating: {days} calendar days of sealed data so far")
    if s["opened"]:
        o = s["opened"][-1]
        print(f"\nOPENED on {o['at']} for strategy {o['strategy_id']}: {o['reason']}")
        print("This data is burned. A new clean test needs a new seal and new time.")
    else:
        print("status      : INTACT, never opened")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("seal"); s.add_argument("--from", dest="frm")
    s.add_argument("--note"); s.add_argument("--force", action="store_true")
    s.set_defaults(func=cmd_seal)
    o = sub.add_parser("open"); o.add_argument("--strategy-id", type=int, required=True)
    o.add_argument("--reason", required=True); o.set_defaults(func=cmd_open)
    c = sub.add_parser("check"); c.add_argument("--max-date", required=True)
    c.set_defaults(func=cmd_check)
    st = sub.add_parser("status"); st.set_defaults(func=cmd_status)
    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
