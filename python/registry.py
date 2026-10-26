#!/usr/bin/env python3
"""The strategy registry: every idea you test, counted before you like it.

    python3 python/registry.py declare "momentum + earnings gap" --hypothesis "..."
    python3 python/registry.py record  <id> --sharpe 0.84 --note "..."
    python3 python/registry.py abandon <id> --note "looked flat, stopped early"
    python3 python/registry.py status

THE PROBLEM THIS SOLVES. Testing strategies until one looks good guarantees one
will look good. With 348 independent observations, the luckiest of 100 strategies
with ZERO real edge shows a Sharpe around 0.94, which beats the index. Search long
enough and noise hands you a winner.

The correction requires knowing how many times you looked. That number is almost
always undercounted, not from dishonesty but because abandoned attempts do not
feel like trials. You try an idea, it looks flat after five minutes, you move on
and never think of it again. It still counted. Your eye still searched.

So a strategy is declared BEFORE it is run, with a written hypothesis, and gets a
terminal state of recorded or abandoned. Abandoned trials stay in the count
forever. The registry is append-only and hash-chained for the same reason the
forward ledger is: a trial count you can quietly reduce is not a trial count.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone

import config
import ledger

REGISTRY = config.ROOT / "data" / "strategy_registry.jsonl"


def entries() -> list[dict]:
    return ledger.read_all(REGISTRY)


def trials() -> dict:
    """Count every strategy ever declared. This is the N that corrections use."""
    rows = entries()
    declared = [r for r in rows if r["kind"] == "declare"]
    terminal = {r["strategy_id"]: r["kind"] for r in rows if r["kind"] in ("record", "abandon")}
    # A parameter sweep is many trials, not one. Counting a 45-variant sweep as a
    # single look is the most common way a trial count gets quietly understated.
    n_trials = sum(max(1, r.get("variants", 1)) for r in declared)
    return {
        "declared": len(declared),
        "n_variants_total": n_trials,
        "recorded": sum(1 for v in terminal.values() if v == "record"),
        "abandoned": sum(1 for v in terminal.values() if v == "abandon"),
        "open": len(declared) - len(terminal),
        "n_trials": n_trials,           # abandoned attempts count; that is the point
    }


def next_id() -> int:
    return max([r.get("strategy_id", 0) for r in entries()], default=0) + 1


def cmd_declare(args) -> int:
    v = ledger.verify(REGISTRY)
    if not v.ok:
        print(v, file=sys.stderr)
        return 1
    sid = next_id()
    ledger.append([{
        "kind": "declare", "strategy_id": sid, "name": args.name,
        "hypothesis": args.hypothesis,
        "data_window": args.window,
        "variants": args.variants,
        "family": args.family,
        "declared_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
    }], REGISTRY)
    t = trials()
    print(f"declared strategy {sid}: {args.name}")
    print(f"  hypothesis: {args.hypothesis}")
    if args.variants > 1:
        print(f"  counted as {args.variants} trials, because a sweep is a sweep")
    print(f"\ntrials to date: {t['n_trials']}")
    print("Run it now. Whatever happens, close it out with `record` or `abandon`.")
    print("An open trial is still a trial; it counts either way.")
    return 0


def _close(args, kind: str) -> int:
    rows = entries()
    if not any(r["kind"] == "declare" and r.get("strategy_id") == args.strategy_id
               for r in rows):
        print(f"no strategy declared with id {args.strategy_id}", file=sys.stderr)
        return 1
    if any(r["kind"] in ("record", "abandon") and r.get("strategy_id") == args.strategy_id
           for r in rows):
        print(f"strategy {args.strategy_id} is already closed. A trial is closed "
              "once; re-running it under a new id is a new trial.", file=sys.stderr)
        return 1
    if kind == "record" and args.sharpe is not None:
        if args.n_obs is None:
            print("a Sharpe needs --n-obs and --periods-per-year, or it cannot be "
                  "deflated. Trials 27-41 stored 0.0 as a placeholder for 'refuted', "
                  "which is not a measurement. Omit --sharpe if none was measured.",
                  file=sys.stderr)
            return 1
        if args.n_obs < 30:
            print(f"WARNING: a Sharpe on {args.n_obs} observations has a standard "
                  f"error near {1 / args.n_obs ** 0.5 * args.ppy ** 0.5:.2f} annualized; "
                  "it is recorded but should not be read as evidence.", file=sys.stderr)
    e = {"kind": kind, "strategy_id": args.strategy_id, "note": args.note or ""}
    if kind == "record":
        e["sharpe"] = args.sharpe
        e["n_obs"] = args.n_obs
        e["periods_per_year"] = args.ppy
    ledger.append([e], REGISTRY)
    t = trials()
    print(f"{kind}ed strategy {args.strategy_id}")
    if kind == "record" and args.sharpe is not None:
        from deflated_sharpe import deflated_sharpe_ratio, expected_max_sharpe
        n = t["n_trials"]
        # The bar depends on how often the series is sampled. Trial 42 recorded a
        # DAILY series without this, it was scored as 701 WEEKS, and the printout
        # said "clears the bar" at 0.859 when the daily bar was 1.921.
        bar = expected_max_sharpe(n, args.n_obs or 348, args.ppy)
        dsr = deflated_sharpe_ratio(args.sharpe, n, args.n_obs or 348,
                                    periods_per_year=args.ppy)
        print(f"\n  observed Sharpe      {args.sharpe:.3f}")
        print(f"  trials so far        {n}")
        print(f"  expected best by luck{bar:>7.3f}   <- the bar, given how many you tried")
        print(f"  deflated p(real)     {dsr:.3f}")
        print(f"  {'clears the bar' if args.sharpe > bar else 'DOES NOT clear the bar'}")
        print("  (the bar assumes a ZERO-return null. For a long-only stock strategy, compare"
              " against its benchmark instead; trial 48 cleared this bar while adding t=0.48.)")
    return 0


def cmd_annotate(args) -> int:
    """A correction is a new entry pointing at the old one, never an edit, for the
    same reason the registry is hash-chained: history you can rewrite is not one."""
    if not any(r["kind"] == "declare" and r.get("strategy_id") == args.strategy_id
               for r in entries()):
        print(f"no strategy declared with id {args.strategy_id}", file=sys.stderr)
        return 1
    ledger.append([{"kind": "annotation", "strategy_id": args.strategy_id,
                    "note": args.note}], REGISTRY)
    print(f"annotated strategy {args.strategy_id}")
    return 0


def cmd_status(_args) -> int:
    v = ledger.verify(REGISTRY)
    print(v)
    t = trials()
    print(f"\nstrategies declared : {t['declared']}")
    print(f"  recorded          : {t['recorded']}")
    print(f"  abandoned         : {t['abandoned']}")
    print(f"  still open        : {t['open']}")
    print(f"\nN used by corrections: {t['n_trials']}")
    if t["n_trials"]:
        from deflated_sharpe import expected_max_sharpe
        bar = expected_max_sharpe(t["n_trials"], 348)
        print(f"Sharpe a NEW candidate must beat to be interesting: {bar:.3f}")
        print("(that is the median best result from pure noise across this many trials)")
        fams = {d.get("family") for d in entries() if d["kind"] == "declare"}
        try:
            fmap = json.loads((config.ROOT / "data" / "trial_families.json").read_text())
            fams |= {k for k in fmap if not k.startswith("_")}
        except FileNotFoundError:
            pass
        fams.discard(None)
        print(f"  at N = declared studies ({t['declared']}): "
              f"{expected_max_sharpe(t['declared'], 348):.3f}")
        print(f"  at N = information families ({len(fams)}): "
              f"{expected_max_sharpe(len(fams), 348):.3f}")
        print("  (all at 348 weekly observations; pass --periods-per-year when recording."
              " The true effective N lies between the family count and the raw count.)")
    rows = entries()
    decl = [r for r in rows if r["kind"] == "declare"]
    if decl:
        closed = {r["strategy_id"]: r for r in rows if r["kind"] in ("record", "abandon")}
        notes = {}
        for r in rows:
            if r["kind"] == "annotation":
                notes[r["strategy_id"]] = notes.get(r["strategy_id"], 0) + 1
        print(f"\n{'id':>3}  {'state':<10} {'sharpe':>7}  {'notes':>5}  name")
        for d in decl:
            c = closed.get(d["strategy_id"])
            state = c["kind"] if c else "open"
            if c and c.get("sharpe") == 0.0 and not c.get("n_obs"):
                sh = "n/a"          # a placeholder for 'refuted', not a measurement
            else:
                sh = f"{c['sharpe']:.3f}" if c and c.get("sharpe") is not None else "-"
            k = notes.get(d["strategy_id"], 0)
            print(f"{d['strategy_id']:>3}  {state:<10} {sh:>7}  {(str(k) if k else ''):>5}  {d['name']}")
        print("\nnotes = corrections appended later (python3 python/registry.py annotate). "
              "n/a = refuted with no Sharpe measured.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("declare", help="register a strategy BEFORE running it")
    d.add_argument("name")
    d.add_argument("--hypothesis", required=True,
                   help="what you expect and why, in one sentence, written now")
    d.add_argument("--window", default="2014-2025")
    d.add_argument("--variants", type=int, default=1,
                   help="a parameter sweep is this many trials, not one")
    d.add_argument("--family", required=True,
                   help="which information the signal uses; see data/trial_families.json. "
                        "Required since the 2026-10-14 audit found 76%% of all trials "
                        "spent on one family (price patterns) without anyone noticing")
    d.set_defaults(func=cmd_declare)

    r = sub.add_parser("record", help="close a trial with its result")
    r.add_argument("strategy_id", type=int)
    r.add_argument("--sharpe", type=float)
    r.add_argument("--n-obs", type=int, dest="n_obs")
    r.add_argument("--note")
    r.add_argument("--periods-per-year", type=float, dest="ppy", default=252 / 5,
                   help="sampling frequency of the series: 252 daily, 50.4 weekly")
    r.set_defaults(func=lambda a: _close(a, "record"))

    n = sub.add_parser("annotate", help="append a correction to a closed trial; "
                       "the original entry is never edited")
    n.add_argument("strategy_id", type=int)
    n.add_argument("--note", required=True)
    n.set_defaults(func=cmd_annotate)

    a = sub.add_parser("abandon", help="close a trial you stopped early; it still counts")
    a.add_argument("strategy_id", type=int)
    a.add_argument("--note")
    a.set_defaults(func=lambda x: _close(x, "abandon"))

    s = sub.add_parser("status")
    s.set_defaults(func=cmd_status)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
