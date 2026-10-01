#!/usr/bin/env python3
"""Position sizing and risk-limit math for the daytrade skill.

All numbers the trade plan depends on are computed here, never estimated by
the model. Usage:

    python3 risk.py plan --symbol AAPL --direction long \
        --entry 195.40 --stop 193.80 --target 199.20

    python3 risk.py status        # today's realized P/L vs limits
    python3 risk.py profile       # show the active risk profile
"""

import argparse
import json
import os
import sys
from datetime import date

DATA_DIR = os.environ.get(
    "DAYTRADE_DIR", os.path.expanduser("~/ClaudeCode/trading-bot/journal")
)
PROFILE_PATH = os.path.join(DATA_DIR, "risk-profile.json")
LOG_PATH = os.path.join(DATA_DIR, "trades.jsonl")

DEFAULT_PROFILE = {
    "account_size": 10000.0,
    "max_risk_pct_per_trade": 1.0,
    "daily_loss_limit": 300.0,
    "max_open_positions": 3,
    "max_exposure_pct": 50.0,
    "min_risk_reward": 2.0,
    "mode": "paper",
}


def load_profile():
    if not os.path.exists(PROFILE_PATH):
        return dict(DEFAULT_PROFILE), False
    with open(PROFILE_PATH) as fh:
        profile = dict(DEFAULT_PROFILE)
        profile.update(json.load(fh))
    return profile, True


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


def today_realized(rows):
    today = date.today().isoformat()
    return sum(
        float(r.get("pnl") or 0.0)
        for r in rows
        if r.get("status") == "closed" and str(r.get("closed_at", ""))[:10] == today
    )


def open_positions(rows):
    return [r for r in rows if r.get("status") == "open"]


def plan(args):
    profile, found = load_profile()
    if args.account is not None:
        profile["account_size"] = args.account
    if args.risk_pct is not None:
        profile["max_risk_pct_per_trade"] = args.risk_pct

    entry, stop, target = args.entry, args.stop, args.target
    direction = args.direction.lower()

    errors = []
    if direction == "long" and stop >= entry:
        errors.append("Long trade needs stop below entry.")
    if direction == "short" and stop <= entry:
        errors.append("Short trade needs stop above entry.")
    if target is not None:
        if direction == "long" and target <= entry:
            errors.append("Long trade needs target above entry.")
        if direction == "short" and target >= entry:
            errors.append("Short trade needs target below entry.")
    if errors:
        print(json.dumps({"ok": False, "errors": errors}, indent=2))
        return 1

    risk_per_share = abs(entry - stop)
    risk_budget = profile["account_size"] * profile["max_risk_pct_per_trade"] / 100.0
    shares = int(risk_budget // risk_per_share) if risk_per_share > 0 else 0
    actual_risk = round(shares * risk_per_share, 2)
    notional = round(shares * entry, 2)
    exposure_pct = round(notional / profile["account_size"] * 100.0, 2) if profile["account_size"] else 0.0

    reward_per_share = abs(target - entry) if target is not None else None
    rr = round(reward_per_share / risk_per_share, 2) if reward_per_share else None
    reward_total = round(shares * reward_per_share, 2) if reward_per_share else None

    rows = read_log()
    realized = today_realized(rows)
    open_count = len(open_positions(rows))
    open_notional = sum(
        float(r.get("shares") or 0) * float(r.get("entry") or 0) for r in open_positions(rows)
    )
    total_exposure_pct = (
        round((open_notional + notional) / profile["account_size"] * 100.0, 2)
        if profile["account_size"]
        else 0.0
    )

    breaches = []
    if shares < 1:
        breaches.append(
            "Position size rounds to 0 shares. Stop is too wide for the risk budget."
        )
    if rr is not None and rr < profile["min_risk_reward"]:
        breaches.append(
            f"Risk-to-reward {rr} is below the {profile['min_risk_reward']} minimum."
        )
    if open_count >= profile["max_open_positions"]:
        breaches.append(
            f"Already at max open positions ({open_count}/{profile['max_open_positions']})."
        )
    if total_exposure_pct > profile["max_exposure_pct"]:
        breaches.append(
            f"Total exposure {total_exposure_pct}% exceeds the {profile['max_exposure_pct']}% cap."
        )
    remaining = profile["daily_loss_limit"] + realized
    if realized <= -profile["daily_loss_limit"]:
        breaches.append(
            f"Daily loss limit hit (realized {realized:.2f}). No more trades today."
        )
    elif actual_risk > remaining:
        breaches.append(
            f"Risking {actual_risk:.2f} leaves the daily loss limit "
            f"({remaining:.2f} of headroom left)."
        )

    out = {
        "ok": True,
        "profile_found": found,
        "mode": profile["mode"],
        "symbol": args.symbol,
        "direction": direction,
        "entry": entry,
        "stop": stop,
        "target": target,
        "risk_per_share": round(risk_per_share, 4),
        "risk_budget": round(risk_budget, 2),
        "shares": shares,
        "dollar_risk": actual_risk,
        "dollar_reward": reward_total,
        "risk_reward": rr,
        "notional": notional,
        "position_exposure_pct": exposure_pct,
        "total_exposure_pct": total_exposure_pct,
        "open_positions": open_count,
        "realized_today": round(realized, 2),
        "daily_loss_headroom": round(remaining, 2),
        "limit_breaches": breaches,
        "verdict": "REJECTED" if breaches else "WITHIN LIMITS",
    }
    print(json.dumps(out, indent=2))
    return 0


def status(_args):
    profile, found = load_profile()
    rows = read_log()
    realized = today_realized(rows)
    opens = open_positions(rows)
    out = {
        "profile_found": found,
        "mode": profile["mode"],
        "date": date.today().isoformat(),
        "realized_today": round(realized, 2),
        "daily_loss_limit": profile["daily_loss_limit"],
        "daily_loss_headroom": round(profile["daily_loss_limit"] + realized, 2),
        "halted": realized <= -profile["daily_loss_limit"],
        "open_positions": len(opens),
        "max_open_positions": profile["max_open_positions"],
        "open_symbols": [r.get("symbol") for r in opens],
        "total_trades_logged": len(rows),
    }
    print(json.dumps(out, indent=2))
    return 0


def show_profile(_args):
    profile, found = load_profile()
    profile["_profile_path"] = PROFILE_PATH
    profile["_using_defaults"] = not found
    print(json.dumps(profile, indent=2))
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("plan", help="size a trade and check it against every limit")
    p.add_argument("--symbol", required=True)
    p.add_argument("--direction", required=True, choices=["long", "short"])
    p.add_argument("--entry", type=float, required=True)
    p.add_argument("--stop", type=float, required=True)
    p.add_argument("--target", type=float)
    p.add_argument("--account", type=float)
    p.add_argument("--risk-pct", type=float)
    p.set_defaults(func=plan)

    s = sub.add_parser("status", help="today's realized P/L and limit headroom")
    s.set_defaults(func=status)

    f = sub.add_parser("profile", help="print the active risk profile")
    f.set_defaults(func=show_profile)

    args = ap.parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
