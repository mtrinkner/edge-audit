#!/usr/bin/env python3
"""Freeze a model so a forward test has something honest to test.

    python3 python/freeze_model.py
    python3 python/freeze_model.py --cutoff 2025-12-31 --version v1

Trains once on everything up to a cutoff date, then writes the fitted model to
disk along with a manifest recording exactly what it is: the training window,
the feature list, the hyperparameters, the decision rules, and a SHA-256 of the
model file itself.

WHY A FROZEN MODEL IS THE WHOLE POINT. A backtest re-run monthly is the same
question asked twelve times a year against the same history, and asking until
something clears p < 0.05 is how false results get published. A frozen model
asked about data that did not exist when it was frozen is a genuine out-of-sample
test, and the evidence accumulates instead of being re-mined.

The manifest hash is what makes that claim checkable. forward_run.py refuses to
score with a model whose bytes do not match the hash it was registered under, so
"I retrained it halfway through and forgot" is detectable rather than deniable.

Retraining is allowed. Retraining QUIETLY is not: a new fit gets a new version,
and its forward record starts over at zero.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pickle
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

import config
import db
from model import FEATURES, TARGET

MODELS = config.ROOT / "models"

# The decision rules are frozen alongside the model. A strategy is the model AND
# the rules that turn its output into a position; freezing only half leaves the
# half that is easiest to fiddle with unconstrained.
DECISION_RULES = {
    "probability_threshold": 0.55,
    "max_new_positions_per_day": 3,
    "holding_days": config.LABEL_HORIZON_DAYS,
    "stop_atr_multiple": 1.5,
    "min_stop_pct_of_price": 2.0,   # derived from risk % / exposure cap
    "entry": "next session open",
    "direction": "long only",
}


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    h.update(p.read_bytes())
    return h.hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--cutoff", default="2025-12-31",
                    help="train on data up to and including this date")
    ap.add_argument("--version", default="v1")
    ap.add_argument("--force", action="store_true",
                    help="overwrite an existing version of the same name")
    args = ap.parse_args()

    MODELS.mkdir(exist_ok=True)
    model_path = MODELS / f"gbm_{args.version}.pkl"
    manifest_path = MODELS / f"gbm_{args.version}.json"

    if model_path.exists() and not args.force:
        print(f"{model_path.name} already exists.", file=sys.stderr)
        print("A frozen model is meant to stay frozen. If you really want to "
              "retrain, use a NEW --version so the old forward record stays "
              "attached to the model that produced it, or pass --force to "
              "deliberately discard it.", file=sys.stderr)
        return 1

    with db.connect() as con:
        cols = ", ".join(["symbol", "dt", TARGET] + FEATURES)
        df = pd.read_sql(
            f"SELECT {cols} FROM v_model_rows WHERE dt <= ? ORDER BY dt",
            con, params=(args.cutoff,))

    if df.empty:
        print(f"no training rows on or before {args.cutoff}", file=sys.stderr)
        return 1

    from sklearn.ensemble import HistGradientBoostingClassifier
    from sklearn.impute import SimpleImputer

    X = df[FEATURES].to_numpy(float)
    y = df[TARGET].to_numpy(int)
    imp = SimpleImputer(strategy="median").fit(X)
    params = dict(max_iter=300, learning_rate=0.05, max_depth=4,
                  min_samples_leaf=200, l2_regularization=1.0,
                  early_stopping=True, validation_fraction=0.15,
                  random_state=config.RANDOM_SEED)
    clf = HistGradientBoostingClassifier(**params).fit(imp.transform(X), y)

    with model_path.open("wb") as fh:
        pickle.dump({"imputer": imp, "clf": clf, "features": FEATURES}, fh)

    manifest = {
        "version": args.version,
        "frozen_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "train_cutoff": args.cutoff,
        "train_start": str(df["dt"].min()),
        "train_rows": int(len(df)),
        "train_symbols": int(df["symbol"].nunique()),
        "train_base_rate": round(float(y.mean()), 6),
        "target": TARGET,
        "features": FEATURES,
        "hyperparameters": params,
        "decision_rules": DECISION_RULES,
        "cost_model": config.COSTS.__dict__,
        "model_file": model_path.name,
        "model_sha256": sha256_file(model_path),
    }
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")

    print(f"froze {args.version}")
    print(f"  trained on {len(df):,} rows, {df['dt'].min()} to {args.cutoff}")
    print(f"  base rate {y.mean():.4f}  across {df['symbol'].nunique()} symbols")
    print(f"  model   {model_path}")
    print(f"  sha256  {manifest['model_sha256'][:32]}...")
    print(f"  rules   threshold {DECISION_RULES['probability_threshold']}, "
          f"{DECISION_RULES['max_new_positions_per_day']}/day, "
          f"{DECISION_RULES['holding_days']}-day hold")
    print("\nThis model is now fixed. Any change to it, the features, or the")
    print("decision rules requires a new --version and restarts the record.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
