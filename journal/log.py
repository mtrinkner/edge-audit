#!/usr/bin/env python3
"""Paper trade log for the daytrade skill.

    python3 log.py open  --json '{"symbol":"AAPL","direction":"long", ...}'
    python3 log.py close --id 3 --exit 199.10 --note "target hit"
    python3 log.py list  [--all]
    python3 log.py stats [--days 30]

Every row is one JSON object per line in trades.jsonl. Nothing here touches a
broker. Fills are whatever you type in after your paper platform reports them.
"""

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone

DATA_DIR = os.environ.get(
    "DAYTRADE_DIR", os.path.expanduser("~/ClaudeCode/trading-bot/journal")
)
LOG_PATH = os.path.join(DATA_DIR, "trades.jsonl")

REQUIRED = ["symbol", "direction", "entry", "stop", "shares"]


def now():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def read_log():
    if not os.path.exists(LOG_PATH):
        return []
    rows = []
    with open(LOG_PATH) as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def write_log(rows):
    os.makedirs(DATA_DIR, exist_ok=True)
    tmp = LOG_PATH + ".tmp"
    with open(tmp, "w") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")
    os.replace(tmp, LOG_PATH)


def cmd_open(args):
    payload = json.loads(args.json)
    missing = [k for k in REQUIRED if payload.get(k) in (None, "")]
    if missing:
        print(json.dumps({"ok": False, "missing_fields": missing}, indent=2))
        return 1
    rows = read_log()
    row = {
        "id": max([r.get("id", 0) for r in rows], default=0) + 1,
        "status": "open",
        "mode": payload.get("mode", "paper"),
        "opened_at": now(),
    }
    row.update(payload)
    row["status"] = "open"
    rows.append(row)
    write_log(rows)
    print(json.dumps({"ok": True, "trade": row}, indent=2))
    return 0


def cmd_close(args):
    rows = read_log()
    match = [r for r in rows if r.get("id") == args.id]
    if not match:
        print(json.dumps({"ok": False, "error": f"no trade with id {args.id}"}))
        return 1
    row = match[0]
    if row.get("status") == "closed":
        print(json.dumps({"ok": False, "error": f"trade {args.id} is already closed"}))
        return 1

    shares = float(row["shares"])
    entry = float(row["entry"])
    sign = 1 if row["direction"] == "long" else -1
    pnl = round((args.exit - entry) * sign * shares, 2)
    risk_per_share = abs(entry - float(row["stop"]))
    r_multiple = round(pnl / (risk_per_share * shares), 2) if risk_per_share else None

    row.update(
        {
            "status": "closed",
            "exit": args.exit,
            "pnl": pnl,
            "r_multiple": r_multiple,
            "closed_at": now(),
            "close_note": args.note or "",
            "followed_plan": args.followed_plan,
        }
    )
    write_log(rows)
    print(json.dumps({"ok": True, "trade": row}, indent=2))
    return 0


def cmd_list(args):
    rows = read_log()
    if not args.all:
        rows = [r for r in rows if r.get("status") == "open"]
    print(json.dumps(rows, indent=2))
    return 0


def cmd_stats(args):
    rows = [r for r in read_log() if r.get("status") == "closed"]
    if args.days:
        cutoff = datetime.now(timezone.utc).astimezone() - timedelta(days=args.days)
        rows = [
            r
            for r in rows
            if r.get("closed_at") and datetime.fromisoformat(r["closed_at"]) >= cutoff
        ]
    if not rows:
        print(json.dumps({"trades": 0, "note": "no closed trades in this window"}, indent=2))
        return 0

    wins = [r for r in rows if float(r.get("pnl", 0)) > 0]
    losses = [r for r in rows if float(r.get("pnl", 0)) < 0]
    gross_win = sum(float(r["pnl"]) for r in wins)
    gross_loss = abs(sum(float(r["pnl"]) for r in losses))

    equity, peak, max_dd = 0.0, 0.0, 0.0
    for r in sorted(rows, key=lambda x: x.get("closed_at", "")):
        equity += float(r.get("pnl", 0))
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)

    followed = [r for r in rows if r.get("followed_plan") is not None]
    out = {
        "window_days": args.days,
        "trades": len(rows),
        "win_rate_pct": round(len(wins) / len(rows) * 100, 1),
        "average_win": round(gross_win / len(wins), 2) if wins else 0.0,
        "average_loss": round(-gross_loss / len(losses), 2) if losses else 0.0,
        "profit_factor": round(gross_win / gross_loss, 2) if gross_loss else None,
        "net_pnl": round(sum(float(r["pnl"]) for r in rows), 2),
        "average_r": round(
            sum(float(r["r_multiple"]) for r in rows if r.get("r_multiple") is not None)
            / max(1, len([r for r in rows if r.get("r_multiple") is not None])),
            2,
        ),
        "max_drawdown": round(max_dd, 2),
        "plan_adherence_pct": (
            round(len([r for r in followed if r["followed_plan"]]) / len(followed) * 100, 1)
            if followed
            else None
        ),
    }
    print(json.dumps(out, indent=2))
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    o = sub.add_parser("open")
    o.add_argument("--json", required=True, help="trade fields as a JSON object")
    o.set_defaults(func=cmd_open)

    c = sub.add_parser("close")
    c.add_argument("--id", type=int, required=True)
    c.add_argument("--exit", type=float, required=True)
    c.add_argument("--note")
    c.add_argument("--followed-plan", dest="followed_plan", type=int, choices=[0, 1])
    c.set_defaults(func=cmd_close)

    l = sub.add_parser("list")
    l.add_argument("--all", action="store_true")
    l.set_defaults(func=cmd_list)

    s = sub.add_parser("stats")
    s.add_argument("--days", type=int, default=30)
    s.set_defaults(func=cmd_stats)

    args = ap.parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
