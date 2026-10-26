#!/usr/bin/env python3
"""Why did 837 trials find nothing? Reproduces every number in docs/AUDIT_2026-10-14.md.

    python3 python/audit_root_causes.py

Runs NO new strategy. It re-measures existing results with the corrected
evaluator, checks the cost model against a hard lower bound, measures statistical
power, and counts where the trial budget actually went.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "python"))

from deflated_sharpe import expected_max_sharpe
from evaluation import clustered_mean, mde


def section(t):
    print("\n" + "=" * 92 + f"\n{t}\n" + "=" * 92)


def budget():
    section("1. WHERE THE TRIAL BUDGET WENT")
    reg = [json.loads(l) for l in open(ROOT / "data" / "strategy_registry.jsonl")]
    dec = {r["strategy_id"]: r for r in reg if r["kind"] == "declare"}
    rec = {r["strategy_id"]: r for r in reg if r["kind"] in ("record", "abandon")}
    fam = json.load(open(ROOT / "data" / "trial_families.json"))
    rows = []
    for k, v in fam.items():
        if k.startswith("_"):
            continue
        var = sum(max(1, dec[i].get("variants", 1)) for i in v["ids"])
        rows.append((k, len(v["ids"]), var))
    t = pd.DataFrame(rows, columns=["family", "studies", "trials"]).sort_values("trials", ascending=False)
    t["share"] = t["trials"] / t["trials"].sum()
    print(t.to_string(index=False, formatters={"share": "{:.0%}".format}))
    n_raw, n_dec, n_fam = int(t["trials"].sum()), len(dec), len(t)
    print(f"\nluck bar (weekly, 348 obs) at N = raw trials {n_raw}: "
          f"{expected_max_sharpe(n_raw, 348):.3f}")
    print(f"luck bar at N = declared studies {n_dec}:          {expected_max_sharpe(n_dec, 348):.3f}")
    print(f"luck bar at N = information families {n_fam}:      {expected_max_sharpe(n_fam, 348):.3f}")
    print("The true effective N lies between: 300 heatmap cells sharing one set of\n"
          "entries are not 300 independent tries, but 42 studies are not 7 either.")

    section("2. REGISTRY DATA QUALITY")
    bad_n, zero, ok = [], [], []
    for i, r in rec.items():
        if r["kind"] != "record":
            continue
        sh, n = r.get("sharpe"), r.get("n_obs")
        if sh == 0.0 and not n:
            zero.append(i)
        elif n is not None and n < 30:
            bad_n.append((i, sh, n))
        else:
            ok.append(i)
    print(f"records storing 0.0 as a placeholder for 'refuted' (no Sharpe measured): {len(zero)}")
    print(f"records with a Sharpe on fewer than 30 observations: {len(bad_n)}  {bad_n}")
    print(f"records usable for deflation as stored: {len(ok)}")


def intraday():
    section("3. THE DAY-TRADING RULES RE-SCORED CAUSALLY, AT ZERO COST")
    d = pd.read_parquet(ROOT / "data" / "intraday_1h.parquet",
                        columns=["symbol", "session", "bar", "close", "bars_in_session"])
    d = d[d["bars_in_session"] == 7]
    d["g"] = d.groupby(["symbol", "session"])["close"].transform("last") / d["close"] - 1
    t = pd.read_parquet(ROOT / "data" / "intraday_trades.parquet",
                        columns=["rule", "symbol", "session", "bar"])
    t = t.join(d.set_index(["symbol", "session", "bar"])["g"],
               on=["symbol", "session", "bar"]).dropna()
    rows = []
    for rule, x in t.groupby("rule"):
        c = clustered_mean(x["g"], x["session"])
        void = x.groupby("session")["g"].mean().mean()
        rows.append({"rule": rule, "trades": c["trades"], "gross_bp": c["mean"] * 1e4,
                     "t": c["t"], "mde_bp": mde(c["se"]) * 1e4,
                     "void_per_day_bp": void * 1e4})
    r = pd.DataFrame(rows).sort_values("gross_bp", ascending=False)
    print(r.round(2).to_string(index=False))
    print(f"\nrules with |t| > 1.96 at zero cost: {(r['t'].abs() > 1.96).sum()} of 12 "
          f"(positive: {((r['t'] > 1.96)).sum()})")
    print(f"median minimum detectable effect: {r['mde_bp'].median():.1f}bp; "
          f"smallest {r['mde_bp'].min():.1f}bp")
    print("void_per_day_bp is the non-causal daily-budget number trials 38-41 reported.")
    r.to_csv(ROOT / "reports" / "audit_intraday_causal.csv", index=False)


def costs():
    section("4. IS THE COST MODEL BIASED? EDGE AGAINST THE ONE-CENT FLOOR")
    e = pd.read_parquet(ROOT / "data" / "edge_spreads.parquet")
    d = pd.read_parquet(ROOT / "data" / "intraday_1h.parquet", columns=["symbol", "close"])
    px = d.groupby("symbol")["close"].median()
    rt = e.groupby("symbol")["edge_full"].median() * 1e4
    j = pd.DataFrame({"edge_rt": rt, "floor_rt": 0.01 / px * 1e4}).dropna()
    j["ratio"] = j["edge_rt"] / j["floor_rt"]
    j["price_band"] = pd.cut(px.reindex(j.index), [0, 20, 50, 100, 200, 1e6],
                             labels=["<$20", "$20-50", "$50-100", "$100-200", ">$200"])
    print(j.groupby("price_band", observed=True)[["edge_rt", "floor_rt", "ratio"]]
            .median().round(2).to_string())
    print("\nThe floor is a hard lower bound: no stock trades inside one cent. For a")
    print("$500 stock a one-cent spread is 0.2bp, and EDGE on hourly bars reports ~10x")
    print("that for SPY. So costs are OVERSTATED for expensive liquid names. Section 3")
    print("shows why it did not matter: the rules earn nothing even at zero cost.")


def main() -> int:
    budget()
    intraday()
    costs()
    return 0


if __name__ == "__main__":
    sys.exit(main())
