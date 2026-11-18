#!/usr/bin/env python3
"""Pre-declaration gate, since 2026-11-18: G-REAL and G-WORTH.
See docs/METHODOLOGY.md, "Pre-declaration gate (since 2026-11-18)".

    python3 python/gate.py real --kind event --design-t 5.11 --k 1 --n-design 14 --n-confirm 32.3
    python3 python/gate.py real --kind portfolio --cited-sharpe 0.24 --years 13
    python3 python/gate.py worth --usd 7 --ceiling 12
    python3 python/gate.py worth --usd 540 --role component --overlap 1.0
    python3 python/gate.py rescreen          # data/gate_rescreen.csv -> reports/gate_rescreen.csv

G-REAL asks whether a pre-registered test can confirm the idea. It uses the
same statistic the trial will declare as primary:
- an event trade: the event-level t, clustered by ex-date month;
- a portfolio: the calendar-time t of monthly returns.

The prior t comes from one of two places.
- A cited published estimate, after decay and costs: for a portfolio,
  Sharpe x sqrt(confirmation years).
- The design estimate corrected for search:
  (design t - E[max of K standard normals]) x (1 - 0.26),
  where K is the number of rule variants evaluated on the design period
  (K = 1 subtracts nothing). The 26% is the average out-of-sample decline in
  McLean & Pontiff (2016), "Does Academic Research Destroy Stock Return
  Predictability?", Journal of Finance 71(1), 5-32: returns are 26% lower out
  of sample and 58% lower after publication.

It is then scaled to the confirmation, prior t x sqrt(n_confirm / n_design),
with n from information known before pricing (filing counts, years), and
P(pass) = 1 - Phi(3 - scaled t). Declare only if P(pass) >= 0.25.

G-WORTH asks whether it is worth having.
- Standalone, Tier 2: >= $1,500 a year on $25k, ceiling first, then the rule.
- A book component: >= $100 a year, with its expected capital overlap with
  the book's other pieces reported.

A trial may be declared when G-REAL passes and either G-WORTH passes or the
owner declares it research-grade in writing (Tier 1 only).
"""

from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import integrate, stats

ROOT = Path(__file__).resolve().parent.parent

T_BAR = 3.0
P_MIN = 0.25
# McLean & Pontiff (2016): returns are 26% lower out of sample, 58% lower after
# publication. The shrink regime is fixed by what the design period is, never
# chosen to fit outcomes (docs/METHODOLOGY.md, "The shrink rule"):
SHRINK = {"own_design": 0.26,      # no overlap: the default
          "replication": 0.58}     # the design period overlaps the sample period of the
                                   # specific paper whose signal definition the study uses
OOS_SHRINK = SHRINK["own_design"]
TIER2_USD = 1500.0
COMPONENT_USD = 100.0
ACCOUNT = 25_000

STATISTIC = {"event": "event-level t, clustered by ex-date month",
             "portfolio": "calendar-time t of monthly returns"}


def statistic(kind: str) -> str:
    """The primary statistic a trial of this kind declares, and so the one
    G-REAL scales."""
    if kind not in STATISTIC:
        raise ValueError(f"kind must be one of {sorted(STATISTIC)}, not {kind!r}")
    return STATISTIC[kind]


def expected_max_normal(k: int) -> float:
    """E[max of k independent standard normals], by integration."""
    k = int(k)
    if k < 1:
        raise ValueError("k >= 1")
    if k == 1:
        return 0.0
    f = lambda x: x * k * stats.norm.pdf(x) * stats.norm.cdf(x) ** (k - 1)
    return float(integrate.quad(f, -np.inf, np.inf)[0])


def shrink_for(regime: str) -> float:
    if regime not in SHRINK:
        raise ValueError(f"shrink regime must be one of {sorted(SHRINK)}, not {regime!r}")
    return SHRINK[regime]


def prior_t_from_design(design_t: float, k: int, regime: str = "own_design") -> float:
    return (design_t - expected_max_normal(k)) * (1 - shrink_for(regime))


def prior_t_from_sharpe(sharpe: float, years: float) -> float:
    """A cited after-decay, after-cost annual Sharpe as a calendar-time t."""
    return sharpe * math.sqrt(years)


def scale(prior_t: float, n_confirm: float, n_design: float) -> float:
    return prior_t * math.sqrt(n_confirm / n_design)


def expected_clusters(n: float, months: int) -> float:
    """Expected number of distinct months holding at least one of n events
    spread evenly over the confirmation's months: M(1 - (1 - 1/M)^n)."""
    return months * (1 - (1 - 1 / months) ** n)


def p_pass(scaled_t: float, bar: float = T_BAR) -> float:
    return float(1 - stats.norm.cdf(bar - scaled_t))


def g_real(kind: str, *, design_t: float | None = None, k: int = 1, n_design: float | None = None,
           n_confirm: float | None = None, cited_sharpe: float | None = None,
           years: float | None = None, regime: str = "own_design",
           design_clusters: float | None = None, confirm_months: int | None = None) -> dict:
    """G-REAL from a design estimate (design_t, k, n_design, n_confirm, regime) or a
    cited portfolio Sharpe (cited_sharpe, years)."""
    stat = statistic(kind)
    if cited_sharpe is not None:
        if kind != "portfolio":
            raise ValueError("a cited Sharpe is a portfolio statistic")
        prior = scaled = prior_t_from_sharpe(cited_sharpe, years)       # already the confirmation's t
        src = "cited"
    else:
        prior = prior_t_from_design(design_t, k, regime)
        src = "design"
    by_events = by_clusters = None
    if src == "design":
        by_events = scale(prior, n_confirm, n_design)
        scaled = by_events
        # an event trade's t is clustered by month: scale by expected clusters too, and keep the smaller
        if kind == "event" and design_clusters and confirm_months:
            by_clusters = scale(prior, expected_clusters(n_confirm, confirm_months), design_clusters)
            scaled = min(by_events, by_clusters)
    p = p_pass(scaled)
    return {"statistic": stat, "prior_source": src, "regime": regime if src == "design" else "cited",
            "e_max_k": expected_max_normal(k) if src == "design" else 0.0, "prior_t": prior,
            "scaled_t_events": by_events, "scaled_t_clusters": by_clusters, "scaled_t": scaled,
            "p_pass": p, "g_real": p >= P_MIN}


def project(usd_per_unit: float, units_per_yr: float, cap_usd: float | None = None) -> float:
    """Projected $/yr: design dollars per event (or per month) times the
    expected current rate, from information known before pricing; capped by
    capacity (the account's capital) when a cap is given."""
    v = usd_per_unit * units_per_yr
    return min(v, cap_usd) if cap_usd is not None else v


def g_worth(usd: float, *, projected: float | None = None, ceiling: float | None = None,
            role: str = "standalone", overlap: float | None = None) -> dict:
    """The bar is applied to the projection (the design dollars if none is
    given) AND to the ceiling (projected the same way); says which decided.
    A ceiling under the bar kills whatever the rule shows."""
    bar = {"standalone": TIER2_USD, "component": COMPONENT_USD}.get(role)
    if bar is None:
        raise ValueError("role is standalone or component")
    rule = usd if projected is None else projected
    label = "design" if projected is None else "projection"
    out = {"role": role, "design_usd": usd, "projected_usd": projected, "ceiling_usd": ceiling, "overlap": overlap}
    if ceiling is not None and ceiling < bar:
        return {**out, "g_worth": False, "decided_by": "ceiling",
                "reason": f"ceiling ${ceiling:,.0f}/yr < ${bar:,.0f}"}
    ok = rule >= bar
    reason = f"{label} ${rule:,.0f}/yr {'>=' if ok else '<'} ${bar:,.0f}"
    if ceiling is None:
        reason += "; no ceiling computed"
    if role == "component":
        reason += "; overlap " + ("not stated" if overlap is None else f"{overlap:.0%} of its capital competes with the book")
    return {**out, "g_worth": ok, "decided_by": label, "reason": reason}


def may_declare(real: dict, worth: dict, research_grade_in_writing: bool = False) -> bool:
    return bool(real["g_real"] and (worth["g_worth"] or research_grade_in_writing))


# ------------------------------------------------------------------ re-screen of past kills

def rescreen(path: Path = ROOT / "data" / "gate_rescreen.csv") -> pd.DataFrame:
    """Every past kill under the new gate. Records all, declares nothing."""
    d = pd.read_csv(path)
    num = lambda v: None if pd.isna(v) else float(v)
    rows = []
    for r in d.to_dict("records"):
        if pd.notna(r["cited_sharpe"]):
            g = g_real(r["kind"], cited_sharpe=float(r["cited_sharpe"]), years=float(r["years"]))
        else:
            g = g_real(r["kind"], design_t=float(r["design_t"]), k=int(r["k"]), regime=r["regime"],
                       n_design=float(r["n_design"]), n_confirm=float(r["n_confirm"]),
                       design_clusters=num(r["design_clusters"]),
                       confirm_months=None if pd.isna(r["confirm_months"]) else int(r["confirm_months"]))
        proj = project(float(r["usd_per_unit"]), float(r["units_per_yr_current"]), num(r["capacity_cap_usd"]))
        ceil = (None if pd.isna(r["ceiling_usd_per_unit"]) else
                project(float(r["ceiling_usd_per_unit"]), float(r["ceiling_units_per_yr_current"])))
        ws = g_worth(float(r["usd_per_yr"]), projected=proj, ceiling=ceil, role="standalone")
        wc = g_worth(float(r["usd_per_yr"]), projected=proj, ceiling=ceil, role="component", overlap=num(r["overlap"]))
        rows.append({"idea": r["idea"], "old_verdict": r["old_verdict"], **{k: g[k] for k in
                     ("statistic", "regime", "e_max_k", "prior_t", "scaled_t_events", "scaled_t_clusters",
                      "scaled_t", "p_pass", "g_real")},
                     "design_usd": float(r["usd_per_yr"]), "projected_usd": proj, "projected_ceiling_usd": ceil,
                     "projection_arithmetic": r["projection_arithmetic"],
                     "g_worth_standalone": ws["g_worth"], "standalone_decided_by": ws["decided_by"],
                     "standalone_reason": ws["reason"],
                     "g_worth_component": wc["g_worth"], "component_decided_by": wc["decided_by"],
                     "component_reason": wc["reason"],
                     "new_verdict": ("declarable" if may_declare(g, ws) else
                                     "declarable as a book component" if may_declare(g, wc) else
                                     "research-grade only (G-REAL passes; owner must declare in writing)"
                                     if g["g_real"] else "kill (G-REAL fails)"),
                     "source": r["source"]})
    out = pd.DataFrame(rows)
    out.to_csv(ROOT / "reports" / "gate_rescreen.csv", index=False)
    return out


# ------------------------------------------------------------------ calibration on known outcomes

def calibrate(path: Path = ROOT / "data" / "gate_calibration.csv") -> tuple[pd.DataFrame, dict]:
    """Predicted P(pass) under the gate for every declared trial with both a
    design and a confirmation result, against what happened. No parameter is
    fitted to this table."""
    d = pd.read_csv(path)
    num = lambda v: None if pd.isna(v) else float(v)
    rows = []
    for r in d.to_dict("records"):
        g = g_real(r["kind"], design_t=float(r["design_t"]), k=int(r["k"]), regime=r["shrink_regime"],
                   n_design=float(r["n_design"]), n_confirm=float(r["n_confirm"]),
                   design_clusters=num(r.get("design_clusters")),
                   confirm_months=None if pd.isna(r.get("confirm_months")) else int(r["confirm_months"]))
        actual = float(r["actual_t"]) >= T_BAR
        pred = g["p_pass"] >= P_MIN
        rows.append({**r, "prior_t": g["prior_t"], "scaled_t_events": g["scaled_t_events"],
                     "scaled_t_clusters": g["scaled_t_clusters"], "scaled_t": g["scaled_t"],
                     "predicted_p_pass": g["p_pass"], "predicted_pass": pred, "actual_pass": actual,
                     "outcome": ("true pass" if pred and actual else "false pass" if pred else
                                 "false kill" if actual else "true kill")})
    out = pd.DataFrame(rows)
    out.to_csv(ROOT / "reports" / "gate_calibration.csv", index=False)
    s = {"cases": len(out), "predicted_passes": int(out["predicted_pass"].sum()),
         "false_passes": int((out["outcome"] == "false pass").sum()),
         "false_kills": int((out["outcome"] == "false kill").sum()),
         "brier": float(((out["predicted_p_pass"] - out["actual_pass"].astype(float)) ** 2).mean())}
    return out, s


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("real")
    r.add_argument("--kind", required=True, choices=sorted(STATISTIC))
    r.add_argument("--design-t", type=float)
    r.add_argument("--k", type=int, default=1, help="rule variants evaluated on the design period")
    r.add_argument("--n-design", type=float)
    r.add_argument("--n-confirm", type=float, help="expected, from information known before pricing")
    r.add_argument("--cited-sharpe", type=float, help="portfolio only: after decay and costs")
    r.add_argument("--years", type=float, help="with --cited-sharpe: confirmation years")
    r.add_argument("--regime", default="own_design", choices=sorted(SHRINK))
    r.add_argument("--design-clusters", type=float, help="event trades: month clusters in the design")
    r.add_argument("--confirm-months", type=int, help="event trades: months in the confirmation")
    w = sub.add_parser("worth")
    w.add_argument("--usd", type=float, required=True, help="the rule's design $/yr on $25k")
    w.add_argument("--projected", type=float, help="projection: design $/unit x current rate (see project)")
    w.add_argument("--ceiling", type=float, help="the ceiling, projected the same way")
    w.add_argument("--role", default="standalone", choices=["standalone", "component"])
    w.add_argument("--overlap", type=float, help="component: share of its capital competing with the book")
    sub.add_parser("rescreen")
    sub.add_parser("calibrate")
    a = ap.parse_args()
    if a.cmd == "real":
        out = g_real(a.kind, design_t=a.design_t, k=a.k, n_design=a.n_design, n_confirm=a.n_confirm,
                     cited_sharpe=a.cited_sharpe, years=a.years, regime=a.regime,
                     design_clusters=a.design_clusters, confirm_months=a.confirm_months)
    elif a.cmd == "worth":
        out = g_worth(a.usd, projected=a.projected, ceiling=a.ceiling, role=a.role, overlap=a.overlap)
    elif a.cmd == "calibrate":
        pd.set_option("display.width", 250)
        out, summ = calibrate()
        print(out[["trial", "kind", "shrink_regime", "design_t", "k", "prior_t", "scaled_t", "predicted_p_pass",
                   "actual_t", "outcome"]].round(3).to_string(index=False))
        print(summ)
        return 0
    else:
        pd.set_option("display.width", 250)
        out = rescreen()
        print(out[["idea", "prior_t", "scaled_t_events", "scaled_t_clusters", "p_pass", "g_real", "design_usd",
                   "projected_usd", "projected_ceiling_usd", "g_worth_standalone", "g_worth_component",
                   "new_verdict"]].round(3).to_string())
        return 0
    for k, v in out.items():
        print(f"{k}: {round(v, 4) if isinstance(v, float) else v}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
