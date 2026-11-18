#!/usr/bin/env python3
"""Trial 60: reverse-split round-up, event-level test. See docs/REVERSE_SPLIT_ROUNDUP.md.

    python3 python/rsplit_trial60.py design      # design period 2010-2018 (rule v2)
    python3 python/rsplit_trial60.py confirm     # 2019-2025, once, at the frozen tag

The parser and the entry/exit/payoff rules are reverse_split.py's, rule v2.
This module only scores: one payoff per traded event at the primary p, a
one-sided t on the mean with standard errors clustered by ex-date month, the
pass checks (a)-(e) and the reported rows. Prices stay in data/rsplit/
(git-ignored); reports/ gets aggregates only.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "python"))

import reverse_split as rs

MIN_EVENTS = 30                 # check (e): fewer traded events is INCONCLUSIVE, not a pass
T_BAR = 3.0                     # check (a)
DROP_TOP = 5                    # check (b)
P_LOW = 0.2                     # check (c), both classes
RECENT = ("2024-01-01", "2025-12-31")    # check (d)
ACCOUNT_N_TRIALS = None         # the deflated Sharpe uses the registry's N at run time


def trades(period: str) -> pd.DataFrame:
    """Every round-up event the v2 rule trades in a period, one share each."""
    t = rs.simulate(period, rule="v2")
    t.to_csv(rs.CACHE / f"trades_v2_{period}.csv", index=False)          # prices: git-ignored
    return t[t["skipped"].isna()].copy() if "skipped" in t else t


def pnl(t: pd.DataFrame, p: dict | float, fee: float = 0.0, col: str = "rounded") -> pd.Series:
    pp = (lambda c: p.get(c, 0.0)) if isinstance(p, dict) else (lambda c: p)
    return pd.Series([rs.value(r, pp(r["cls"]), fee, col) for r in t.to_dict("records")], index=t.index, dtype=float)


def event_stats(t: pd.DataFrame, x: pd.Series) -> dict:
    """Event-level mean with a one-sided t, standard errors clustered by
    ex-date month (evaluation.clustered_mean), and the reported shape."""
    from evaluation import clustered_mean
    import warnings
    n = int(x.notna().sum())
    out = {"events": n, "mean": float(x.mean()) if n else np.nan, "median": float(x.median()) if n else np.nan,
           "win_rate": float((x > 0).mean()) if n else np.nan,
           "p10": float(x.quantile(0.1)) if n else np.nan, "worst": float(x.min()) if n else np.nan,
           "sd": float(x.std(ddof=1)) if n > 1 else np.nan,
           "top5_share": float(x.nlargest(DROP_TOP).sum() / x[x > 0].sum()) if (x > 0).any() else np.nan,
           "return_on_capital_median": float((x / t.loc[x.index, "ask"]).median()) if n else np.nan}
    if n >= 2:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            c = clustered_mean(x, t.loc[x.index, "exdt"].astype(str).str[:7])
        out.update(t=float(c["t"]), p_one=float(c["p_one"]), clusters=int(c["clusters"]))
    else:
        out.update(t=np.nan, p_one=np.nan, clusters=n)
    return out


def score(period: str, t: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    lo, hi = rs.SPLIT[period]
    years = (pd.Timestamp(hi) - pd.Timestamp(lo)).days / 365.25
    prim = pnl(t, rs.P_PRIMARY)
    rows = []

    def row(label: str, x: pd.Series, sub: pd.DataFrame | None = None) -> None:
        s = event_stats(sub if sub is not None else t, x)
        s["usd_per_yr"] = float(x.sum() / years)
        rows.append({"row": label, "period": period, **s})

    row("primary", prim)
    row("p_1", pnl(t, 1.0))
    row("p_0.2_both", pnl(t, P_LOW))
    row("fee_5", pnl(t, rs.P_PRIMARY, fee=rs.FEE_SENS))
    row("exit_open_minus_half_spread", pnl(t, rs.P_PRIMARY, col="rounded_open"))
    row("exit_5_days_later", pnl(t, rs.P_PRIMARY, col="rounded_5d"))
    keep = prim.drop(prim.nlargest(DROP_TOP).index)
    row(f"drop_top_{DROP_TOP}", keep, t.loc[keep.index])
    rec = t["exdt"].between(*RECENT)
    row("ex_2024_2025", prim[rec], t[rec])
    for c in rs.ROUNDUP:
        m = t["cls"] == c
        row(f"class_{c}", prim[m], t[m])
    for name, v in (("ratio", t["ratio_entry"].astype(float)), ("price", t["ask"].astype(float))):
        if len(t) >= 3:
            q = pd.qcut(v.rank(method="first"), 3, labels=["low", "mid", "high"])
            for k in ("low", "mid", "high"):
                m = q == k
                row(f"{name}_tercile_{k}", prim[m], t[m])
    for y, g in t.groupby(t["exdt"].astype(str).str[:4]):
        row(f"year_{y}", prim[g.index], g)
    out = pd.DataFrame(rows)

    # monthly P&L (zero months included) and the deflated Sharpe at the registry's N, reported only
    months = pd.period_range(lo[:7], hi[:7], freq="M").astype(str)
    mp = prim.groupby(t["exit_day"].astype(str).str[:7]).sum().reindex(months, fill_value=0.0)
    sh = float(mp.mean() / mp.std() * np.sqrt(12)) if mp.std() > 0 else np.nan
    from deflated_sharpe import deflated_sharpe_ratio
    import registry
    n_trials = registry.trials()["n_trials"]
    dsr = float(deflated_sharpe_ratio(sh, n_trials, len(mp), periods_per_year=12)) if sh == sh else np.nan

    p = out.set_index("row")
    a = bool(p.loc["primary", "mean"] > 0 and p.loc["primary", "t"] >= T_BAR)
    b = bool(p.loc[f"drop_top_{DROP_TOP}", "mean"] > 0)
    c = bool(p.loc["p_0.2_both", "mean"] > 0)
    d = bool(p.loc["ex_2024_2025", "events"] > 0 and p.loc["ex_2024_2025", "mean"] > 0) if period == "confirm" else None
    e = bool(p.loc["primary", "events"] >= MIN_EVENTS)
    if period == "confirm":
        verdict = "INCONCLUSIVE" if not e else ("PASS" if a and b and c and d else "FAIL")
    else:
        verdict = "design (no verdict)"
    summary = {"period": period, "verdict": verdict, "a_mean_pos_t_ge_3": a, "b_mean_pos_without_top5": b,
               "c_mean_pos_at_p_0.2": c, "d_2024_2025_mean_pos": d, "e_at_least_30_events": e,
               "monthly_sharpe": sh, "months": len(mp), "n_trials_registry": n_trials, "deflated_sharpe": dsr}
    return out, summary


def main(period: str) -> int:
    rs.require_frozen(period)
    t = trades(period)
    out, summary = score(period, t)
    out.to_csv(ROOT / "reports" / f"rsplit_trial60_{period}.csv", index=False)
    pd.DataFrame([summary]).to_csv(ROOT / "reports" / f"rsplit_trial60_{period}_summary.csv", index=False)
    pd.set_option("display.width", 200)
    print(out.set_index("row")[["events", "mean", "median", "win_rate", "t", "p_one", "top5_share", "usd_per_yr"]]
          .round(3).to_string())
    print()
    for k, v in summary.items():
        print(f"{k}: {v}")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("period", choices=list(rs.SPLIT))
    sys.exit(main(ap.parse_args().period))
