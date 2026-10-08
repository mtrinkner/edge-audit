#!/usr/bin/env python3
"""Freeze a RULE-based strategy, the way freeze_model.py freezes a fitted one.

    python3 python/freeze_strategy.py --version short_v2

v1 is a fitted model, so freezing it means pickling the fitted object and hashing
the bytes. A rule-based strategy has no fitted object: it is a signal definition
plus a set of decisions about universe, neutralization, bucketing and horizon.
Every one of those is a dial that could be turned after a bad quarter, which is
exactly what has to be nailed down.

So this hashes two things together: the parameter spec, and the source of the
modules that implement it. If either changes, the hash changes and the forward
record for that version is void. That makes "I just tweaked the threshold a
little" detectable rather than deniable.

WHAT IS BEING FROZEN HERE. Short interest relative to average daily volume,
ranked within sector, residualised against market beta, size and momentum,
quintile long/short, 63-day hold, costs charged at each stock's own modelled
half-spread. It measured a Sharpe of 1.66 over 2021-2026 on 22 independent
observations, with a deflated probability of 0.052 against 212 registered trials.
It fails that bar. It is frozen precisely because it fails on sample size rather
than on robustness, and sample size is the one objection that time answers.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import config

MODELS = config.ROOT / "models"

SPEC = {
    "short_v2": {
        "description": "Short interest / ADV, sector-ranked, factor-residualised, "
                       "quintile long-short",
        "signal": {
            "source": "FINRA consolidated short interest",
            "field": "short_shares / (avg_daily_vol * 21)",
            "orientation": -1,
            "comment": "heavily shorted names underperform (Boehmer, Jones & Zhang). "
                       "orientation fixed from the literature, not from the sample.",
            "publication_lag_sessions": 10,
        },
        "universe": {
            "members": "S&P 500 + 400 + 600 as of 2026-10",
            "min_adv_usd": 5_000_000,
            "survivorship_bias": True,
        },
        "construction": {
            "rank_within": ["date", "sector"],
            "residualise_against": ["beta", "size", "mom126"],
            "buckets": 5,
            "legs": "long top quintile, short bottom quintile",
            "weighting": "equal within bucket, sectors equally weighted",
            "holding_days": 63,
        },
        "costs": {
            "model": "tick floor + inverse-sqrt-volume, anchored to 2bp for large caps",
            "charged": "both sides, both legs, at each stock's own half-spread",
        },
        "backtest": {
            "window": "2021-03-29 to 2026-07-08",
            "sharpe": 1.66, "t_stat": 3.90,
            "ret_ann": 0.0298, "vol_ann": 0.0204, "max_dd": 0.0153,
            "independent_observations": 22,
            "trials_registered_at_freeze": 212,
            "deflated_probability": 0.052,
            "verdict": "fails the luck bar on sample size; frozen to find out if "
                       "that changes with data that did not exist at freeze time",
        },
        "live_sizing": {
            "gross_exposure_usd": 5_000,
            "comment": "deliberately small. this is a measurement, not a position. "
                       "sizing is a parameter of the record, so changing it later "
                       "requires a new version.",
        },
        "implemented_by": ["python/alt_signals.py", "python/portfolio.py",
                           "python/spread_estimator.py"],
    }
}


def sha256_of(paths: list[str], spec: dict) -> str:
    h = hashlib.sha256()
    h.update(json.dumps(spec, sort_keys=True).encode())
    for p in sorted(paths):
        h.update((config.ROOT / p).read_bytes())
    return h.hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--version", default="short_v2")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    if args.version not in SPEC:
        print(f"no spec named {args.version}. known: {list(SPEC)}", file=sys.stderr)
        return 1
    MODELS.mkdir(exist_ok=True)
    out = MODELS / f"{args.version}.json"
    if out.exists() and not args.force:
        print(f"{out.name} already exists. A frozen strategy is meant to stay frozen; "
              "use a new --version rather than overwriting a live record.",
              file=sys.stderr)
        return 1

    spec = SPEC[args.version]
    manifest = {
        "version": args.version,
        "kind": "rule-based",
        "frozen_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        **spec,
        "code_sha256": sha256_of(spec["implemented_by"], spec),
    }
    out.write_text(json.dumps(manifest, indent=2) + "\n")

    print(f"froze {args.version}")
    print(f"  {spec['description']}")
    print(f"  backtest  Sharpe {spec['backtest']['sharpe']}, "
          f"{spec['backtest']['independent_observations']} independent observations, "
          f"deflated p {spec['backtest']['deflated_probability']}")
    print(f"  sizing    ${spec['live_sizing']['gross_exposure_usd']:,} gross")
    print(f"  sha256    {manifest['code_sha256'][:40]}...")
    print(f"  manifest  {out}")
    print("\nThe spec AND the source of the three modules that implement it are")
    print("inside that hash. Change a threshold, a factor, or the horizon and the")
    print("hash moves, which voids this version's forward record rather than")
    print("quietly extending it.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
