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
OOS_SHRINK = 0.26               # McLean & Pontiff (2016): out-of-sample decline
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


def prior_t_from_design(design_t: float, k: int, shrink: float = OOS_SHRINK) -> float:
    return (design_t - expected_max_normal(k)) * (1 - shrink)


def prior_t_from_sharpe(sharpe: float, years: float) -> float:
    """A cited after-decay, after-cost annual Sharpe as a calendar-time t."""
    return sharpe * math.sqrt(years)


def scale(prior_t: float, n_confirm: float, n_design: float) -> float:
    return prior_t * math.sqrt(n_confirm / n_design)


def p_pass(scaled_t: float, bar: float = T_BAR) -> float:
    return float(1 - stats.norm.cdf(bar - scaled_t))


def g_real(kind: str, *, design_t: float | None = None, k: int = 1, n_design: float | None = None,
           n_confirm: float | None = None, cited_sharpe: float | None = None,
           years: float | None = None) -> dict:
    """G-REAL from a design estimate (design_t, k, n_design, n_confirm) or a
    cited portfolio Sharpe (cited_sharpe, years)."""
    stat = statistic(kind)
    if cited_sharpe is not None:
        if kind != "portfolio":
            raise ValueError("a cited Sharpe is a portfolio statistic")
        prior = scaled = prior_t_from_sharpe(cited_sharpe, years)       # already the confirmation's t
        src = "cited"
    else:
        prior = prior_t_from_design(design_t, k)
        scaled, src = scale(prior, n_confirm, n_design), "design"
    p = p_pass(scaled)
    return {"statistic": stat, "prior_source": src, "e_max_k": expected_max_normal(k) if src == "design" else 0.0,
            "prior_t": prior, "scaled_t": scaled, "p_pass": p, "g_real": p >= P_MIN}


def g_worth(usd: float, *, ceiling: float | None = None, role: str = "standalone",
            overlap: float | None = None) -> dict:
    """Standalone: ceiling first (a ceiling under the bar kills), then the
    rule. Component: the rule's dollars, with capital overlap reported."""
    if role == "standalone":
        if ceiling is not None and ceiling < TIER2_USD:
            return {"role": role, "g_worth": False, "reason": f"ceiling ${ceiling:,.0f}/yr < ${TIER2_USD:,.0f}"}
        ok = usd >= TIER2_USD
        return {"role": role, "g_worth": ok,
                "reason": f"rule ${usd:,.0f}/yr {'>=' if ok else '<'} ${TIER2_USD:,.0f}"}
    if role == "component":
        ok = usd >= COMPONENT_USD
        ov = "not stated" if overlap is None else f"{overlap:.0%} of its capital competes with the book"
        return {"role": role, "g_worth": ok, "overlap": overlap,
                "reason": f"rule ${usd:,.0f}/yr {'>=' if ok else '<'} ${COMPONENT_USD:,.0f}; overlap {ov}"}
    raise ValueError("role is standalone or component")


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
            g = g_real(r["kind"], design_t=float(r["design_t"]), k=int(r["k"]),
                       n_design=float(r["n_design"]), n_confirm=float(r["n_confirm"]))
        ws = g_worth(float(r["usd_per_yr"]), ceiling=num(r["ceiling_usd"]), role="standalone")
        wc = g_worth(float(r["usd_per_yr"]), role="component", overlap=num(r["overlap"]))
        rows.append({"idea": r["idea"], "old_verdict": r["old_verdict"], **{k: g[k] for k in
                     ("statistic", "prior_source", "e_max_k", "prior_t", "scaled_t", "p_pass", "g_real")},
                     "g_worth_standalone": ws["g_worth"], "standalone_reason": ws["reason"],
                     "g_worth_component": wc["g_worth"], "component_reason": wc["reason"],
                     "new_verdict": ("declarable" if may_declare(g, ws) else
                                     "declarable as a book component" if may_declare(g, wc) else
                                     "research-grade only (G-REAL passes; owner must declare in writing)"
                                     if g["g_real"] else "kill (G-REAL fails)"),
                     "source": r["source"]})
    out = pd.DataFrame(rows)
    out.to_csv(ROOT / "reports" / "gate_rescreen.csv", index=False)
    return out


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
    w = sub.add_parser("worth")
    w.add_argument("--usd", type=float, required=True, help="the rule's $/yr on $25k")
    w.add_argument("--ceiling", type=float)
    w.add_argument("--role", default="standalone", choices=["standalone", "component"])
    w.add_argument("--overlap", type=float, help="component: share of its capital competing with the book")
    sub.add_parser("rescreen")
    a = ap.parse_args()
    if a.cmd == "real":
        out = g_real(a.kind, design_t=a.design_t, k=a.k, n_design=a.n_design, n_confirm=a.n_confirm,
                     cited_sharpe=a.cited_sharpe, years=a.years)
    elif a.cmd == "worth":
        out = g_worth(a.usd, ceiling=a.ceiling, role=a.role, overlap=a.overlap)
    else:
        pd.set_option("display.width", 250)
        out = rescreen()
        print(out[["idea", "old_verdict", "prior_t", "scaled_t", "p_pass", "g_real", "g_worth_standalone",
                   "g_worth_component", "new_verdict"]].round(3).to_string())
        return 0
    for k, v in out.items():
        print(f"{k}: {round(v, 4) if isinstance(v, float) else v}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
