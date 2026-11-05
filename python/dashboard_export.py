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

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "python"))

from deflated_sharpe import expected_max_sharpe

OUT = ROOT / "reports" / "dashboard"
FAMILY_LABEL = {
    "price_ta": "Price patterns", "xs_factors": "Cross-sectional factors",
    "earnings_info": "Earnings information", "flows_altdata": "Flows and alt data",
    "microstructure": "Microstructure", "calendar": "Calendar effects",
    "crypto_carry": "Crypto carry", "structural": "Structural arbitrage",
}
FAMILIES = ROOT / "data" / "trial_families.json"
ACCOUNT = 25_000                    # the account every dollar figure assumes


def _entry(prefix: str) -> str:
    """The full hash of the registry entry whose hash starts with prefix, so a
    source cites an entry that exists."""
    hits = [json.loads(l)["entry_hash"] for l in open(ROOT / "data" / "strategy_registry.jsonl")
            if json.loads(l)["entry_hash"].startswith(prefix)]
    if len(hits) != 1:
        raise KeyError(f"registry entry {prefix}: {len(hits)} matches")
    return hits[0]


def family_status():
    """Every family in trial_families.json, with its label and whether it is
    closed to further trials."""
    fam = json.load(open(FAMILIES))
    missing = [k for k in fam if not k.startswith("_") and k not in FAMILY_LABEL]
    if missing:
        raise KeyError(f"no FAMILY_LABEL for {missing}")
    return [{"family": k, "label": FAMILY_LABEL[k], "status": "closed" if v.get("closed") else "open",
             "closed_note": v.get("closed", ""), "source": "data/trial_families.json"}
            for k, v in fam.items() if not k.startswith("_")]


def edge_map():
    """One row per strategy: average capital deployed on a $25k account, excess
    return over T-bills on that capital, and dollars a year. Repo rows are
    computed from result files; literature rows come from data/edge_map_curated.csv.
    Writes data/edge_map.csv as well."""
    rows = []
    ff = pd.read_parquet(ROOT / "data" / "pead" / "ff_daily.parquet")
    ff[["mktrf", "rf"]] = ff[["mktrf", "rf"]].astype(float)
    rf = ff.set_index("date")["rf"]

    # Odd-lot tenders, entries below the guaranteed floor minus the $40 fee a
    # share (trial 58's descriptive cut), both periods pooled
    deals = pd.read_csv(ROOT / "data" / "edgar" / "deals.csv")[["path", "price", "price_lo"]].drop_duplicates("path")
    profit = tbill = cap_years = 0.0
    n = 0
    for period in ("design", "confirm"):
        t = pd.read_csv(ROOT / "data" / "edgar" / f"odd_lot_{period}_deals.csv")
        t = t[t["variant"] == "E1"].merge(deals, on="path", how="left")
        floor = t["price_lo"].where(t["offer_type"] == "dutch", t["price"])
        keep = (t["entry"] < floor - 40 / 99) & (t["currency"] == "USD") \
            & ~t["company"].str.upper().str.contains("IMPERIAL OIL")
        if "split_adjusted" in t:
            keep &= t["split_adjusted"].ne(True)
        s = t[keep]
        n += len(s)
        profit += s["profit_40"].sum()
        tbill += sum(r.capital * rf[(rf.index > r.entry_day) & (rf.index <= r.exit_day)].sum() for r in s.itertuples())
        cap_years += (s["capital"] * s["days_locked"].clip(lower=1) / 365).sum()
    years = 16                       # 2010-2018 and 2019-2025
    cap, usd = cap_years / years, (profit - tbill) / years
    rows.append({"name": "Odd-lot tenders below the floor", "short": "Odd-lot tenders", "kind": "contract", "status": "recorded FAIL",
                 "label": "descriptive subset of trial 58", "capital_usd": cap, "usd_per_yr": usd,
                 "basis": f"{n} deals 2010-2025 bought below the guaranteed price minus the $40 fee a share; "
                          "T-bills on the capital while locked subtracted",
                 "source": f"registry {_entry('d30a11881fc0')}; docs/ODD_LOT_TENDERS.md, Looked at after the fact; "
                           "capital and T-bills aggregated from data/edgar/odd_lot_*_deals.csv"})

    # SPAC trust arb, the declared rule's final design run, and its redemption floor alone
    g = pd.read_csv(ROOT / "reports" / "spac_trial_gate.csv").set_index("run").loc["design_gate"]
    spac_cap = float(g["avg_invested"]) * ACCOUNT
    rows.append({"name": "SPAC trust arb (rule, run 4)", "short": "SPAC rule", "kind": "contract", "status": "gate-killed", "label": "",
                 "capital_usd": spac_cap, "usd_per_yr": float(g["excess_usd_per_yr"]),
                 "basis": "design 2010-2020, excess over T-bills; capital = average share invested x $25k",
                 "source": "reports/spac_trial_gate.csv, row design_gate (excess_usd_per_yr, avg_invested)"})
    rows.append({"name": "SPAC redemption floor only", "short": "SPAC floor", "kind": "contract", "status": "gate-killed", "label": "",
                 "capital_usd": spac_cap, "usd_per_yr": 454 / 11,
                 "basis": "run 4 redemption PnL, $454 over 11 years, before T-bills; capital is the whole run's "
                          "average (the redemption trades' own share is not exported)",
                 "source": "docs/SPAC_TRUST_ARB.md, Main conclusion (run 4 table, Redemptions row)"})

    # PEAD, trial 59 confirmation, long-short net of costs
    p = pd.read_csv(ROOT / "reports" / "pead_confirm.csv").set_index("series").loc["v1_net"]
    rows.append({"name": "PEAD (trial 59)", "short": "PEAD", "kind": "anomaly", "status": "recorded FAIL", "label": "",
                 "capital_usd": ACCOUNT, "usd_per_yr": float(p["annualised"]) * ACCOUNT,
                 "basis": "confirmation 2013-2025, V1 long-short net of costs, annualised; a long-short "
                          "return, so no T-bill subtraction",
                 "source": "reports/pead_confirm.csv, row v1_net, column annualised"})

    # Holding the market, for scale
    m = ff[(ff["date"] >= "2013-01-01") & (ff["date"] <= "2025-12-31")]
    mkt = (1 + m["mktrf"]).prod() ** (1 / 13) - 1
    rows.append({"name": "Holding the market", "short": "Market", "kind": "reference", "status": "literature",
                 "label": "risk premium, not an edge", "capital_usd": ACCOUNT, "usd_per_yr": float(mkt) * ACCOUNT,
                 "basis": "Fama-French Mkt-RF compounded 2013-2025 (13 years), annualised",
                 "source": "data/pead/ff_daily.parquet (Ken French data library daily factors), column mktrf"})

    # Reverse-split round-up: gate-killed 2026-11-02
    gr = pd.read_csv(ROOT / "reports" / "rsplit_gate.csv").set_index("run").loc["primary"]
    tr = pd.read_csv(ROOT / "data" / "rsplit" / "trades_design_gate.csv")
    tr = tr[tr["skipped"].isna()]
    held = (pd.to_datetime(tr["exit_day"]) - pd.to_datetime(tr["entry_day"])).dt.days.clip(lower=1)
    rcap = float((tr["ask"] * held / 365).sum() / 9)
    rows.append({"name": "Reverse-split round-up", "short": "Reverse split", "kind": "contract", "status": "gate-killed", "label": "",
                 "capital_usd": rcap, "usd_per_yr": float(gr["usd_per_yr"]),
                 "basis": f"design 2010-2018 at the primary round-up probability; one share an event, "
                          f"{int(gr['events_traded'])} events, median {held.median():.0f} days held",
                 "source": "reports/rsplit_gate.csv, row primary (usd_per_yr); capital aggregated from "
                           "data/rsplit/trades_design_gate.csv"})

    for r in rows:
        r["excess_pct_yr"] = r["usd_per_yr"] / r["capital_usd"] * 100
    cur = pd.read_csv(ROOT / "data" / "edge_map_curated.csv").fillna("")
    out = pd.DataFrame(rows + cur.to_dict("records"))
    out = out[["name", "short", "kind", "status", "label", "capital_usd", "excess_pct_yr", "usd_per_yr", "basis", "source"]]
    for c, d in (("capital_usd", 2), ("excess_pct_yr", 3), ("usd_per_yr", 1)):
        out[c] = out[c].astype(float).round(d)
    out.to_csv(ROOT / "data" / "edge_map.csv", index=False)
    return out.to_dict("records")


def funnel():
    """The search as stages: strategies counted, declared, closed, passed, plus
    ideas killed at the pre-declaration gate; and the same by family."""
    import registry
    t = registry.trials()
    kills = pd.read_csv(ROOT / "data" / "gate_kills.csv")
    src = "python/registry.py trials() on data/strategy_registry.jsonl"
    stages = [("Strategies counted (N)", t["n_trials"], src),
              ("Studies declared", t["declared"], src),
              ("Recorded", t["recorded"], src),
              ("Abandoned", t["abandoned"], src),
              ("Passed", 0, src + "; no record or annotation reports a pass"),
              ("Killed at the gate, no trial spent", len(kills), "data/gate_kills.csv")]
    rows = [{"part": "stage", "order": i, "stage": s, "count": int(c), "source": so}
            for i, (s, c, so) in enumerate(stages)]
    fam = json.load(open(FAMILIES))
    fam_of = {i: k for k, v in fam.items() if not k.startswith("_") for i in v["ids"]}
    reg = registry.entries()
    term = {r["strategy_id"]: r["kind"] for r in reg if r["kind"] in ("record", "abandon")}
    by = {}
    for d in (r for r in reg if r["kind"] == "declare"):
        i = int(d["strategy_id"])
        k = d.get("family") or fam_of.get(i, "")
        b = by.setdefault(k, {"studies": 0, "trials": 0, "recorded": 0, "abandoned": 0})
        b["studies"] += 1
        b["trials"] += max(1, int(d.get("variants", 1)))
        if term.get(d["strategy_id"]) == "record":
            b["recorded"] += 1
        elif term.get(d["strategy_id"]) == "abandon":
            b["abandoned"] += 1
    gk = kills.groupby("family").size().to_dict()
    for k in fam:
        if k.startswith("_"):
            continue
        b = by.get(k, {"studies": 0, "trials": 0, "recorded": 0, "abandoned": 0})
        rows.append({"part": "family", "family": k, "label": FAMILY_LABEL[k], **b, "passed": 0,
                     "gate_kills": int(gk.get(k, 0)), "status": "closed" if fam[k].get("closed") else "open",
                     "source": src + "; data/trial_families.json; data/gate_kills.csv"})
    return rows


def trial_outcomes():
    """Design against confirmation, curated with registry hashes, plus the luck
    bar `registry.py status` prints."""
    import registry
    o = pd.read_csv(ROOT / "data" / "trial_outcomes.csv")
    bar = round(expected_max_sharpe(registry.trials()["n_trials"], 348), 3)
    rows = o.to_dict("records")
    for r in rows:
        r["family"] = FAMILY_LABEL.get(r["family"], r["family"])
    rows.append({"trial": None, "family": "", "metric": "luck bar", "design": bar, "confirm": bar,
                 "design_period": "", "confirm_period": "",
                 "source": "python/registry.py status: expected_max_sharpe(N used by corrections, 348)"})
    return rows


def decay():
    d = pd.read_csv(ROOT / "data" / "decay.csv")
    return [{k: (None if isinstance(v, float) and np.isnan(v) else v) for k, v in r.items()}
            for r in d.to_dict("records")]


def bug_ledger():
    b = pd.read_csv(ROOT / "data" / "bug_ledger.csv", dtype={"trial": str}).sort_values("date", ascending=False)
    b.insert(0, "n", range(1, len(b) + 1))
    return [{k: (None if isinstance(v, float) and np.isnan(v) else v) for k, v in r.items()}
            for r in b.to_dict("records")]


def candidates():
    c = json.load(open(ROOT / "data" / "candidates.json"))
    return [{"name": c["name"], "doc": c["doc"], "candidate_status": c["status"], **k} for k in c["checks"]]


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
     "before": "+6.5bp edge", "after": "+0.2bp", "direction": "Made results look better"},
    {"n": 2, "found": "Daily budget weighting", "what": "Split each day's money across signals that hadn't fired yet",
     "before": "Sharpe 1.19", "after": "Sharpe -1.27", "direction": "Made results look better"},
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
                       ("growth", growth()), ("bugs", BUGS), ("cbm57", cbm57()),
                       ("families", family_status()), ("edge_map", edge_map()), ("funnel", funnel()),
                       ("trial_outcomes", trial_outcomes()), ("decay", decay()),
                       ("bug_ledger", bug_ledger()), ("candidates", candidates())):
        (OUT / f"{name}.json").write_text(json.dumps(rows, indent=1))
        print(f"{name}: {len(rows)} rows")
    (OUT / "updated.txt").write_text(now)
    print("updated", now)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
