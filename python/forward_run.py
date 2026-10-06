#!/usr/bin/env python3
"""The monthly forward test: score a frozen model on data it has never seen.

    python3 python/forward_run.py                 # fetch, predict, score, report
    python3 python/forward_run.py --no-fetch      # use bars already loaded
    python3 python/forward_run.py --report-only   # just read the ledger

Run this once a month. It does four things, in this order:

  1. Pulls bars since the last run and rebuilds features.
  2. Scores the FROZEN model on every date after the training cutoff that is not
     already in the ledger, and writes those predictions down BEFORE their
     outcomes exist.
  3. Scores predictions whose 5-day horizon has now elapsed, appending results.
  4. Reports the record so far.

TWO KINDS OF EVIDENCE, AND THE DIFFERENCE MATTERS. Dates between the training
cutoff and the first run of this script already existed when the model was
frozen. Scoring them is a legitimate out-of-sample holdout, but it is NOT
pre-registered: the data was sitting there, and had the result been bad the
temptation to adjust something before "starting" would have been real. Those
entries are labeled `holdout`.

Every date scored after that is labeled `live`. Those are predictions written
down before the market moved, and they are the only ones that count as a
pre-registered forward test. The report keeps the two apart and always will,
because merging them would quietly launder the weaker evidence into the stronger
category.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pickle
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

import config
import db
import ledger


def bootstrap_excess(per_date: pd.Series, bench: pd.Series,
                     reps: int = 5000, mean_block: int = 10) -> dict:
    """Stationary bootstrap of the excess return over the benchmark.

    This lives inside the report on purpose. A mean return shown on its own
    invites the reader to believe it, and over a few dozen observations a
    healthy-looking mean is routine noise. The p-value and the interval are the
    context that make the mean interpretable, so they are printed together or
    not at all.
    """
    common = per_date.index.intersection(bench.index)
    d = (per_date.loc[common] - bench.loc[common]).dropna().to_numpy()
    if len(d) < 10:
        return {"n": len(d), "mean": float(d.mean()) if len(d) else float("nan"),
                "p": None, "lo": None, "hi": None}
    rng = np.random.default_rng(config.RANDOM_SEED)

    def idx(n: int) -> np.ndarray:
        out: list[int] = []
        pr = 1.0 / mean_block
        while len(out) < n:
            start = int(rng.integers(n))
            out.extend(((start + np.arange(int(rng.geometric(pr)))) % n).tolist())
        return np.array(out[:n])

    centered = d - d.mean()
    boot = np.array([centered[idx(len(d))].mean() for _ in range(reps)])
    lo, hi = np.percentile(boot + d.mean(), [2.5, 97.5])
    return {"n": len(d), "mean": float(d.mean()),
            "p": float((np.abs(boot) >= abs(d.mean())).mean()),
            "lo": float(lo), "hi": float(hi)}

MODELS = config.ROOT / "models"


def load_frozen(version: str) -> tuple[dict, dict]:
    mpath, jpath = MODELS / f"gbm_{version}.pkl", MODELS / f"gbm_{version}.json"
    if not mpath.exists():
        raise SystemExit(f"no frozen model at {mpath}. Run python3 python/freeze_model.py")
    manifest = json.loads(jpath.read_text())
    digest = hashlib.sha256(mpath.read_bytes()).hexdigest()
    if digest != manifest["model_sha256"]:
        raise SystemExit(
            f"MODEL HASH MISMATCH for {version}.\n"
            f"  manifest: {manifest['model_sha256']}\n"
            f"  on disk:  {digest}\n"
            "The frozen model has changed since it was registered. Every result "
            "attached to this version is now unverifiable. Freeze a new version "
            "rather than continuing this record."
        )
    with mpath.open("rb") as fh:
        bundle = pickle.load(fh)
    return bundle, manifest


def refresh_bars(cutoff: str) -> None:
    """Pull bars from shortly before the cutoff through today, then rebuild."""
    start = (datetime.fromisoformat(cutoff).date() - timedelta(days=400)).isoformat()
    end = (date.today() + timedelta(days=1)).isoformat()
    print(f"  fetching bars {start} to {end}")
    r = subprocess.run([sys.executable, "python/ingest_bars.py", "--all",
                        "--start", start, "--end", end],
                       cwd=config.ROOT, capture_output=True, text=True)
    for ln in r.stdout.splitlines():
        if ln.strip().startswith(("loaded", "calendar")):
            print(f"    {ln.strip()}")
    if r.returncode != 0:
        print(r.stderr[-800:], file=sys.stderr)
        raise SystemExit("bar refresh failed")
    for f in ("02_features.sql", "03_materialize.sql", "04_labels.sql"):
        subprocess.run(["sqlite3", str(config.DB_PATH)],
                       stdin=(config.SQL_DIR / f).open(), cwd=config.ROOT, check=True)
    print("    features and labels rebuilt")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--version", default="v1")
    ap.add_argument("--no-fetch", action="store_true")
    ap.add_argument("--report-only", action="store_true")
    args = ap.parse_args()

    v = ledger.verify()
    print(v)
    if not v.ok:
        print("\nRefusing to append to a broken ledger. The record is only worth "
              "something if it is intact.", file=sys.stderr)
        return 1

    bundle, manifest = load_frozen(args.version)
    cutoff = manifest["train_cutoff"]
    rules = manifest["decision_rules"]
    thr = rules["probability_threshold"]
    topn = rules["max_new_positions_per_day"]
    horizon = rules["holding_days"]
    print(f"model {args.version} verified, trained through {cutoff}")

    if not args.report_only:
        if not args.no_fetch:
            refresh_bars(cutoff)

        rows = ledger.read_all()
        predicted = {(r["dt"], r["symbol"]) for r in rows if r["kind"] == "prediction"}
        scored = {(r["dt"], r["symbol"]) for r in rows if r["kind"] == "result"}
        first_run = next((r["written_at"][:10] for r in rows), None)

        feats = ", ".join(manifest["features"])
        with db.connect() as con:
            panel = pd.read_sql(
                f"SELECT symbol, dt, close, atr_14, {feats} FROM feature_panel "
                "WHERE dt > ? ORDER BY dt, symbol", con, params=(cutoff,))
            lab = pd.read_sql(
                "SELECT symbol, dt, fwd_ret_5d, fwd_ret_5d_net, label_win_5d, next_open "
                "FROM labels WHERE dt > ?", con, params=(cutoff,))

        if panel.empty:
            print("\nno bars after the training cutoff yet. Nothing to predict.")
            return 0

        # ---------------- step 1: predictions, written before outcomes exist
        X = panel[manifest["features"]].to_numpy(float)
        ok = ~np.isnan(X).all(axis=1)
        proba = np.full(len(panel), np.nan)
        proba[ok] = bundle["clf"].predict_proba(bundle["imputer"].transform(X[ok]))[:, 1]
        panel["y_prob"] = proba
        panel = panel.dropna(subset=["y_prob"])

        today = date.today().isoformat()
        new_preds = []
        for dt_, g in panel.groupby("dt"):
            if dt_ >= today:
                continue                      # today's bar is not final
            picks = g[g["y_prob"] >= thr].nlargest(topn, "y_prob")
            for r in picks.itertuples():
                if (r.dt, r.symbol) in predicted:
                    continue
                # Anything whose date already existed at first run is holdout.
                stage = "live" if (first_run and r.dt >= first_run) else "holdout"
                new_preds.append({
                    "kind": "prediction", "model_version": args.version,
                    "dt": r.dt, "symbol": r.symbol,
                    "y_prob": round(float(r.y_prob), 6),
                    "close": round(float(r.close), 4),
                    "atr_14": round(float(r.atr_14), 4) if pd.notna(r.atr_14) else None,
                    "stage": stage, "threshold": thr, "horizon_days": horizon,
                })
        if new_preds:
            # Chronological order so the chain reads as a timeline.
            new_preds.sort(key=lambda e: (e["dt"], e["symbol"]))
            ledger.append(new_preds)
            byst = pd.Series([p["stage"] for p in new_preds]).value_counts().to_dict()
            print(f"\nwrote {len(new_preds)} new predictions {byst}")
        else:
            print("\nno new predictions to write")

        # ---------------- step 2: score whatever has matured
        rows = ledger.read_all()
        lab_idx = lab.set_index(["symbol", "dt"])
        new_results = []
        for r in rows:
            if r["kind"] != "prediction" or (r["dt"], r["symbol"]) in scored:
                continue
            key = (r["symbol"], r["dt"])
            if key not in lab_idx.index:
                continue
            L = lab_idx.loc[key]
            if pd.isna(L["fwd_ret_5d_net"]):
                continue                      # horizon has not elapsed yet
            new_results.append({
                "kind": "result", "model_version": args.version,
                "dt": r["dt"], "symbol": r["symbol"], "stage": r["stage"],
                "y_prob": r["y_prob"],
                "ret_gross": round(float(L["fwd_ret_5d"]), 6),
                "ret_net": round(float(L["fwd_ret_5d_net"]), 6),
                "win": int(L["label_win_5d"]),
                "predicted_at": r["written_at"],
            })
        if new_results:
            new_results.sort(key=lambda e: (e["dt"], e["symbol"]))
            ledger.append(new_results)
            print(f"scored {len(new_results)} matured predictions")
        else:
            print("nothing matured since the last run")

    # ---------------- step 3: the record
    rows = ledger.read_all()
    res = pd.DataFrame([r for r in rows if r["kind"] == "result"])
    pend = [r for r in rows if r["kind"] == "prediction"
            and not any(x["kind"] == "result" and x["dt"] == r["dt"]
                        and x["symbol"] == r["symbol"] for x in rows)]

    print("\n" + "=" * 72)
    print("FORWARD RECORD")
    print("=" * 72)
    if res.empty:
        print("no matured results yet")
    else:
        bench = None
        with db.connect() as con:
            b = pd.read_sql(
                "SELECT AVG(fwd_ret_5d_net) m FROM labels WHERE symbol='SPY' AND dt > ?",
                con, params=(res["dt"].min(),)).m[0]
            bench = float(b) if b is not None else None
        print(f"{'stage':<10}{'trades':>8}{'dates':>8}{'eff obs':>9}"
              f"{'mean net':>11}{'win rate':>10}{'vs SPY':>10}")
        for stage in ("holdout", "live"):
            s = res[res["stage"] == stage]
            if s.empty:
                print(f"{stage:<10}{'0':>8}{'-':>8}{'-':>9}{'-':>11}{'-':>10}{'-':>10}")
                continue
            per_date = s.groupby("dt")["ret_net"].mean()
            eff = len(per_date) / 5
            excess = (per_date.mean() - bench) * 100 if bench is not None else float("nan")
            print(f"{stage:<10}{len(s):>8}{len(per_date):>8}{eff:>9.1f}"
                  f"{per_date.mean()*100:>10.3f}%{s['win'].mean():>9.1%}"
                  f"{excess:>+9.3f}%")
        # The significance test, printed next to the mean it qualifies.
        with db.connect() as con:
            spy = pd.read_sql(
                "SELECT dt, fwd_ret_5d_net r FROM labels WHERE symbol='SPY' "
                "AND dt >= ? AND dt <= ?", con,
                params=(res["dt"].min(), res["dt"].max())).set_index("dt")["r"]
        print()
        for stage in ("holdout", "live"):
            s_ = res[res["stage"] == stage]
            if s_.empty:
                continue
            b = bootstrap_excess(s_.groupby("dt")["ret_net"].mean(), spy)
            if b["p"] is None:
                print(f"{stage}: {b['n']} dates, too few to test")
                continue
            verdict = ("distinguishable from chance" if b["p"] < 0.05
                       else "NOT distinguishable from chance")
            print(f"{stage}: excess {b['mean']*100:+.3f}% per 5 days, "
                  f"p = {b['p']:.3f}, 95% CI [{b['lo']*100:+.3f}%, {b['hi']*100:+.3f}%]")
            print(f"  {verdict}")

        print(f"\npending (horizon not elapsed): {len(pend)}")
        live = res[res["stage"] == "live"]
        n_eff = live.groupby("dt").ngroups / 5 if not live.empty else 0
        print(f"pre-registered effective observations: {n_eff:.1f}")
        if n_eff < 30:
            print(f"  Far too few to conclude anything. At roughly 4 per month this")
            print(f"  reaches 30 in about {max(0, (30 - n_eff) / 4):.0f} months. Until")
            print(f"  then the only correct reading of this table is 'not yet known'.")
    print(f"\nledger: {len(rows)} entries at {ledger.LEDGER}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
