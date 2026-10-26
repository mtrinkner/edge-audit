#!/usr/bin/env python3
"""Export what the Edge Audit Live dashboard shows, as small JSON files.

    python3 python/dashboard_export.py

Writes reports/dashboard/*.json from the trial registry, the running trial-57
pipeline and finished result files. Only results go out: no CRSP rows, no
prices (the WRDS licence forbids redistributing them), no credentials.
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "python"))

from deflated_sharpe import expected_max_sharpe

OUT = ROOT / "reports" / "dashboard"
FAMILY_LABEL = {
    "price_ta": "Price patterns", "xs_factors": "Cross-sectional factors",
    "earnings_info": "Earnings information", "flows_altdata": "Flows and alt data",
    "microstructure": "Microstructure", "calendar": "Calendar effects",
    "crypto_carry": "Crypto carry",
}


def trials():
    reg = [json.loads(l) for l in open(ROOT / "data" / "strategy_registry.jsonl")]
    fam = json.load(open(ROOT / "data" / "trial_families.json"))
    fam_of = {i: k for k, v in fam.items() if not k.startswith("_") for i in v["ids"]}
    dec = [r for r in reg if r["kind"] == "declare"]
    closed = {r["strategy_id"]: r for r in reg if r["kind"] in ("record", "abandon")}
    notes = {}
    for r in reg:
        if r["kind"] == "annotation":
            notes[r["strategy_id"]] = notes.get(r["strategy_id"], 0) + 1
    rows, cum = [], 0
    for d in dec:
        i = int(d["strategy_id"])
        v = max(1, int(d.get("variants", 1)))
        cum += v
        c = closed.get(d["strategy_id"])
        state = {"record": "Recorded", "abandon": "Abandoned"}.get(c["kind"], "") if c else "Running"
        note = (c or {}).get("note", "") or ""
        sh = (c or {}).get("sharpe")
        placeholder = sh == 0.0 and not (c or {}).get("n_obs")
        rows.append({
            "id": i,
            "name": d["name"],
            "family": FAMILY_LABEL.get(d.get("family") or fam_of.get(i, ""), "Other"),
            "declared": d["declared_at"][:10],
            "variants": v,
            "trials_to_date": cum,
            "luck_bar": round(expected_max_sharpe(cum, 348), 3),
            "state": state,
            "sharpe": None if (sh is None or placeholder) else round(float(sh), 3),
            "corrections": notes.get(d["strategy_id"], 0),
            "result": note[:260] + ("…" if len(note) > 260 else ""),
        })
    return rows


def running():
    """Where the trial-57 pipeline is. Counts finished model files per fit."""
    cbm = ROOT / "data" / "cbm"
    try:
        ps = subprocess.run(["ps", "-axo", "stat,command"], capture_output=True, text=True).stdout
    except Exception:
        ps = ""
    lines = [l for l in ps.splitlines() if "cbm_crsp.py" in l and "grep" not in l]
    if not lines:
        state = "Stopped"
    elif any(l.strip().startswith("T") for l in lines):
        state = "Paused"
    else:
        state = "Running"
    gate1 = (ROOT / "data" / "crsp" / "gate1_ok.txt")
    gate2 = (ROOT / "data" / "crsp" / "gate2_ok.txt")
    rows = [{"step": "Gate 1: CRSP data matches the paper", "order": 1,
             "done": 1 if gate1.exists() and gate1.read_text() == "1" else 0, "total": 1}]
    for order, (tag, label) in enumerate((("crsp_fit2004", "Fit models on 1927–2004"),
                                          ("crsp_fit2014", "Fit models on 1927–2014"),
                                          ("crsp_fit2022", "Fit models on 1927–2022")), start=2):
        n = len(list(cbm.glob(f"{tag}_run*.pt")))
        rows.append({"step": label, "order": order, "done": n, "total": 30})
        if tag == "crsp_fit2014":
            rows.append({"step": "Gate 2: our model matches the authors'", "order": 3.5,
                         "done": 1 if gate2.exists() and gate2.read_text() == "1" else 0,
                         "total": 1})
    rows.append({"step": "Out-of-sample test, 2023–2025", "order": 5,
                 "done": 1 if (ROOT / "reports" / "cbm_crsp_test.csv").exists() else 0,
                 "total": 1})
    rows.sort(key=lambda r: r["order"])
    for r in rows:
        r["pipeline_state"] = state
        r["trial"] = 57
    return rows


def oos56():
    f = ROOT / "reports" / "cbm_oos_monthly.csv"
    if not f.exists():
        return []
    o = pd.read_csv(f)
    return [{"month": f"{int(m) // 100}-{int(m) % 100:02d}", "gross": round(g, 3), "net": round(n, 3)}
            for m, g, n in zip(o["Month"], o["gross"], o["net"])]


def signals41():
    r = pd.read_csv(ROOT / "reports" / "robustness_ta.csv")
    return [{"signal": s, "trades": int(n), "excess_pct": round(e * 100, 3), "t": round(t, 2)}
            for s, n, e, t in zip(r["signal"], r["trades"], r["trimmed"], r["t_trim"])]


def intraday12():
    r = pd.read_csv(ROOT / "reports" / "audit_intraday_causal.csv")
    return [{"rule": a, "trades": int(n), "gross_bp": round(g, 2), "t": round(t, 2), "mde_bp": round(m, 2)}
            for a, n, g, t, m in zip(r["rule"], r["trades"], r["gross_bp"], r["t"], r["mde_bp"])]


def growth():
    """Growth of $1 for the four closest calls against their benchmarks, monthly points."""
    rows = []
    def add(study, dates, strat, bench, s_name, b_name):
        w_s, w_b = (1 + strat).cumprod(), (1 + bench).cumprod()
        for d, a, b in zip(dates, w_s, w_b):
            rows.append({"study": study, "date": d, "series": s_name, "value": round(float(a), 4)})
            rows.append({"study": study, "date": d, "series": b_name, "value": round(float(b), 4)})
    o = pd.read_csv(ROOT / "reports" / "overnight_auction.csv")
    o["m"] = o["dt"].str[:7]
    om = o.groupby("m")[["overnight_net", "hold"]].agg(lambda x: (1 + x).prod() - 1)
    add("overnight", [m + "-01" for m in om.index], om["overnight_net"], om["hold"],
        "Overnight only, after costs", "Hold the same stocks")
    t = pd.read_csv(ROOT / "reports" / "trend_spy_monthly.csv")
    add("trend", t["Date"].str[:10].tolist(), t["r_strat"], t["r_spy"], "10-month trend rule", "Buy and hold SPY")
    sr = pd.read_csv(ROOT / "reports" / "sector_rotation.csv")
    add("sectors", sr["Date"].str[:10].tolist(), sr["strat"], sr["ew"], "Top 3 sectors", "All 9 sectors equal")
    rv = pd.read_csv(ROOT / "reports" / "reversal_weekly.csv")
    rv["m"] = rv["dt"].str[:7]
    rm = rv.groupby("m")[["losers_net", "universe"]].agg(lambda x: (1 + x).prod() - 1)
    add("reversal", [m + "-01" for m in rm.index], rm["losers_net"], rm["universe"],
        "Last week's losers, after costs", "All 500 stocks")
    return rows


def cbm57():
    """Trial 57: our CRSP replica's monthly 10-1 return before and after measured
    costs, 2005-2025, beside the authors' own published 10-1 where it exists.
    Portfolio returns only: no CRSP stock rows leave the laptop."""
    from cbm_crsp import authors_10_1
    a = authors_10_1()
    hist = pd.read_csv(ROOT / "reports" / "cbm_crsp_costs.csv")
    test = pd.read_csv(ROOT / "reports" / "cbm_crsp_test.csv")
    rows = []
    for df, period in ((hist, "Paper's own years"), (test, "After the paper (out of sample)")):
        for m, g, n in zip(df["Month"], df["gross"], df["net"]):
            au = a.get(int(m))
            rows.append({"month": f"{int(m) // 100}-{int(m) % 100:02d}", "period": period,
                         "ours_gross": round(float(g), 3), "ours_net": round(float(n), 3),
                         "authors_gross": None if au is None or pd.isna(au) else round(float(au), 3)})
    return rows


BUGS = [
    {"n": 1, "found": "Day-trading volume signal", "what": "Average volume included hours later the same day",
     "before": "+12.6bp edge", "after": "-0.7bp", "direction": "Made results look better"},
    {"n": 2, "found": "Daily budget weighting", "what": "Split each day's money across signals that hadn't fired yet",
     "before": "Sharpe 1.52", "after": "Sharpe -1.27", "direction": "Made results look better"},
    {"n": 3, "found": "Mismatched t-statistic", "what": "Printed one average next to the t-stat of another",
     "before": "t = 5.11", "after": "t = 0.26", "direction": "Made results look better"},
    {"n": 4, "found": "Spread estimator", "what": "Returned a noise floor, not a spread, on hourly bars",
     "before": "SPY 1.64bp", "after": "true ~0.17bp", "direction": "Made results look worse"},
    {"n": 5, "found": "Truncated download", "what": "Short-interest history stopped at 2021",
     "before": "Sharpe 1.66", "after": "Sharpe 0.24", "direction": "Made results look better"},
    {"n": 6, "found": "Registry frequency", "what": "Scored daily data as weekly",
     "before": "'clears the bar'", "after": "fails (bar 1.92)", "direction": "Made results look better"},
    {"n": 7, "found": "Bankruptcy re-stitch", "what": "Old and new CHRD shares joined in the price data",
     "before": "+22,500% week", "after": "removed", "direction": "Distorted both ways"},
    {"n": 8, "found": "Per-stock trade filter", "what": "Silently deleted the golden cross from the study",
     "before": "signal missing", "after": "restored", "direction": "Hid a result"},
    {"n": 9, "found": "Paper's validation split", "what": "Chart AI learned which week, not which stock",
     "before": "best validation score", "after": "no stock-picking skill", "direction": "Made results look better"},
]


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for name, rows in (("trials", trials()), ("pipeline", running()), ("oos56", oos56()),
                       ("signals41", signals41()), ("intraday12", intraday12()),
                       ("growth", growth()), ("bugs", BUGS), ("cbm57", cbm57())):
        (OUT / f"{name}.json").write_text(json.dumps(rows, indent=1))
        print(f"{name}: {len(rows)} rows")
    (OUT / "updated.txt").write_text(now)
    print("updated", now)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
