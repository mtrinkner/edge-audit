#!/usr/bin/env python3
"""Three checks on the breadth run, any one of which can kill its top rows.

    python3 python/breadth_checks.py

Run this after breadth.py. It reads reports/breadth.csv and the cached panel.

1. HOW MUCH VARIANCE DID RESIDUALIZATION REMOVE. If the factor set explains
   nearly all of a signal, what survives is float noise, and ranking noise can
   still throw an IC by luck. This is the check that decides whether the top row
   of the breadth table is a measurement or an artifact. It also doubles as a
   control: the price scores SHOULD lose most of their variance, since they are
   largely the factors being removed. If they do not, the regression is broken.

2. MULTIPLE TESTING. Sixty-four variants went into one run. Benjamini-Hochberg
   at FDR 5%, plus the expected maximum |t| from that many pure-noise tests, so
   the best t-stat can be read against what noise alone would have produced.

3. PERSISTENCE-ADJUSTED BREADTH. A score with autocorrelation 0.9 at the
   rebalance horizon is one bet restated, not twelve, so 252/h overstates
   independent decisions per year. The AR(1) variance-ratio deflation
   (1-rho)/(1+rho) is applied and the predicted IR is re-scored against realized
   gross Sharpe. Reported whether or not it improves the fit: on this data it
   did NOT, which is a result about the adjustment and is left standing.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np
import pandas as pd
import pyarrow.parquet as pq, pyarrow.compute as pc
from breadth import SLEEVES, FACTORS, Resid, zs, rank_ic

ROOT = Path(__file__).resolve().parent.parent
res = pd.read_csv(ROOT / "reports" / "breadth.csv")

print("=" * 78)
print("1. HOW MUCH VARIANCE DID RESIDUALIZATION ACTUALLY REMOVE")
print("=" * 78)
sig = [c for v in SLEEVES.values() for c in v]
need = list(dict.fromkeys(["symbol","dt","sector","adv","vol63","half_bps",
                           "fwd_21"] + FACTORS + sig))
have = set(pq.ParquetFile(ROOT / "data" / "breadth_panel.parquet").schema_arrow.names)
t = pq.read_table(ROOT / "data" / "breadth_panel.parquet", columns=[c for c in need if c in have])
t = t.filter(pc.greater_equal(t["adv"], 5e6))
p = t.to_pandas(); del t
f64 = [c for c in p.columns if p[c].dtype == "float64"]
p[f64] = p[f64].astype("float32")
p = p.sort_values(["dt","symbol"]).reset_index(drop=True)

cols, labels = [], []
for sl, mem in SLEEVES.items():
    mem = [m for m in mem if m in p.columns]
    zc = []
    for m in mem:
        p[f"z_{m}"] = zs(p[m], [p["dt"], p["sector"]]); zc.append(f"z_{m}")
        cols.append(f"z_{m}"); labels.append(m)
    p[f"c_{sl}"] = p[zc].mean(axis=1, skipna=True).astype("float32")
    cols.append(f"c_{sl}"); labels.append(f"c_{sl}")
sc = [f"c_{k}" for k in SLEEVES if f"c_{k}" in p.columns]
p["c_all"] = p[sc].mean(axis=1, skipna=True).astype("float32")
cols.append("c_all"); labels.append("c_all")

R = Resid(p, FACTORS)
fwd = p["fwd_21"].to_numpy(dtype=float)
rows = []
for name, col in zip(labels, cols):
    y = p[col].to_numpy(dtype=np.float32)
    r = R.of(y).astype(float)
    ok = np.isfinite(y) & np.isfinite(r)
    v_y, v_r = np.var(y[ok].astype(float)), np.var(r[ok])
    rows.append({"signal": name, "var_before": v_y, "var_after": v_r,
                 "r2_removed": 1 - v_r / v_y if v_y > 0 else np.nan,
                 "resid_std": np.sqrt(v_r)})
    del y, r
d = pd.DataFrame(rows).sort_values("r2_removed", ascending=False)
print(f"{'signal':<24}{'var before':>12}{'var after':>12}{'R2 removed':>12}")
for _, r in d.iterrows():
    flag = "  <-- almost nothing left" if r.r2_removed > 0.98 else ""
    print(f"{r.signal:<24}{r.var_before:>12.4f}{r.var_after:>12.4f}"
          f"{r.r2_removed:>11.1%}{flag}")
d.to_csv(ROOT / "reports" / "resid_r2.csv", index=False)

print()
print("=" * 78)
print("2. MULTIPLE TESTING ACROSS THE 64 VARIANTS IN THIS RUN")
print("=" * 78)
from scipy import stats
sub = res.dropna(subset=["ic_t"]).copy()
sub["p"] = 2 * (1 - stats.norm.cdf(sub["ic_t"].abs()))
sub = sub.sort_values("p")
m = len(sub)
sub["bh_crit"] = (np.arange(1, m + 1) / m) * 0.05
sub["passes_bh"] = sub["p"] <= sub["bh_crit"]
print(f"Benjamini-Hochberg at FDR 5% across {m} tests")
print(f"{'signal':<24}{'var':<7}{'IC':>8}{'t':>7}{'p':>9}{'BH crit':>10}{'pass':>6}")
for _, r in sub.head(10).iterrows():
    print(f"{r.signal:<24}{r.variant:<7}{r.ic:>8.4f}{r.ic_t:>7.2f}"
          f"{r.p:>9.4f}{r.bh_crit:>10.4f}{'YES' if r.passes_bh else 'no':>6}")
print(f"\nsurvivors: {int(sub.passes_bh.sum())} of {m}")
print(f"expected max |t| from 64 pure-noise tests: "
      f"{stats.norm.ppf(1 - 0.5/64):.2f}")

print()
print("=" * 78)
print("3. PERSISTENCE-ADJUSTED BREADTH VS WHAT THE BOOK DELIVERED")
print("=" * 78)
N_EFF, H = 100.0, 21
reb = 252 / H
print(f"effective names {N_EFF:.0f}, nominal rebalances/yr {reb:.0f}, "
      f"nominal breadth {N_EFF*reb:.0f}")
print(f"\n{'signal':<24}{'var':<7}{'rho':>6}{'indep reb':>11}{'breadth':>9}"
      f"{'IR naive':>10}{'IR adj':>8}{'Shp gr':>8}{'Shp net':>9}")
k = res.dropna(subset=["ic","rho_h"]).copy()
# An AR(1) score at lag h has independent-innovation share (1-rho)/(1+rho);
# this is the standard variance-ratio deflation, not a new invention.
k["indep_reb"] = reb * (1 - k["rho_h"].clip(-0.99, 0.99)) / (1 + k["rho_h"].clip(-0.99, 0.99))
k["breadth_adj"] = N_EFF * k["indep_reb"].clip(lower=0.1)
k["ir_adj"] = k["ic"] * np.sqrt(k["breadth_adj"])
show = k[k.signal.astype(str).str.startswith("c_") | (k.ic_t.abs() > 2)]
for _, r in show.iterrows():
    print(f"{str(r.signal):<24}{r.variant:<7}{r['rho_h']:>6.2f}"
          f"{r.indep_reb:>11.1f}{r.breadth_adj:>9.0f}"
          f"{r.ir_predicted:>10.2f}{r.ir_adj:>8.2f}"
          f"{r.sharpe_gross:>8.2f}{r.sharpe_net:>9.2f}")
err_naive = (k["ir_predicted"] - k["sharpe_gross"]).abs().median()
err_adj = (k["ir_adj"] - k["sharpe_gross"]).abs().median()
print(f"\nmedian |predicted - realized gross| across all {len(k)} variants")
print(f"  naive breadth (252/h):        {err_naive:.3f}")
print(f"  persistence-adjusted breadth: {err_adj:.3f}")
k.to_csv(ROOT / "reports" / "breadth_adj.csv", index=False)
