#!/usr/bin/env python3
"""Charting by Machines on CRSP (trial 57).

    python3 python/cbm_crsp.py panel     # CRSP -> the paper's inputs, one row per stock-month
    python3 python/cbm_crsp.py gate1     # momentum and reversal vs paper Table A12 (no ML)
    python3 python/cbm_crsp.py gate2     # retrain 1927-2004 and 1927-2014, compare with the authors
    python3 python/cbm_crsp.py costs     # real turnover and costs, 2005-2022
    python3 python/cbm_crsp.py final     # fit 1927-2022 and freeze
    python3 python/cbm_crsp.py test      # 2023-01 to 2025-12, scored once

Sample and variables follow the authors' SampleForFits.R, rebuilt on CRSP's
current CIZ files (python/crsp_pull.py): for return month t, a stock needs a
return in each of months t-12..t-1 and a market cap at the end of t-1.
  CumRet_k  cumulative return over months t-12 .. t-12+k-1, in percent
  RetRF     month-t return minus the risk-free rate, in percent
  Mom       CumRet11 (t-12 .. t-2);  Rev  month t-1 return
  NYSE      primary exchange N at the end of t-1
  MktCapM   |price| x shares at the end of t-1
msf_v2 returns already include delisting returns; the paper applied its own
-30%/-100% rule to legacy files. The model and portfolio code are reused from
python/cbm_replication.py so the PyTorch replica is the one already tested.

FIXED 2026-10-26 (audit): net returns now also subtract a borrow fee on the
short decile (config.COSTS.borrow_bps_annual = 50bp a year, about 4bp a month;
small hard-to-borrow losers cost more, so this is a floor). The earlier outputs
are kept as reports/cbm_crsp_*_pre_2026-10-26.csv.
"""

from __future__ import annotations

import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "python"))

import cbm_replication as cbm
import config
import data_access as da
from cbm_replication import F, legs, line, nw_t

warnings.filterwarnings("ignore", category=FutureWarning)
warnings.filterwarnings("ignore", category=UserWarning)
C = ROOT / "data" / "crsp"
PANEL = C / "panel.parquet"


def _before_seal(m: pd.DataFrame) -> pd.DataFrame:
    """Keep only whole months that end before the lockbox seal (added 2026-10-26;
    CRSP ends 2025-12, so this changes nothing today but guards any refresh)."""
    md = da.guard("cbm_crsp")
    month_end = (m["Month"] // 100).astype(str) + "-" + (m["Month"] % 100).map("{:02d}".format) + "-31"
    return m[month_end <= md]


def panel() -> int:
    m = _before_seal(pd.read_parquet(C / "msf.parquet"))
    rf = pd.read_csv(C / "rf.csv", dtype={"date": str})
    rf["Month"] = (rf["date"].str[:4] + rf["date"].str[5:7]).astype(int)
    m = m.merge(rf[["Month", "rf"]], on="Month", how="left")
    m = m.sort_values(["permno", "Month"]).reset_index(drop=True)
    m["mi"] = (m["Month"] // 100) * 12 + m["Month"] % 100
    g = m.groupby("permno")
    lag = {k: g["mthret"].shift(k) for k in range(1, 13)}          # lag k = month t-k
    contiguous = (m["mi"] - g["mi"].shift(12)) == 12
    cap_prev = g["mthcap"].shift(1)
    exch_prev = g["primaryexch"].shift(1)
    rets = np.column_stack([lag[13 - j] for j in range(1, 13)])     # months t-12 .. t-1
    cum = (np.cumprod(1 + rets, axis=1) - 1) * 100
    p = pd.DataFrame(cum, columns=F, index=m.index)
    p["Month"], p["permno"] = m["Month"], m["permno"]
    p["RetRF"] = (m["mthret"] - m["rf"]) * 100
    p["MktCapM"] = cap_prev
    p["NYSESample"] = (exch_prev == "N").astype(int)
    p["Mom"] = p["CumRet11"]
    p["Rev"] = lag[1] * 100
    ok = contiguous & np.isfinite(cum).all(axis=1) & cap_prev.gt(0) & exch_prev.notna()
    p = p[ok].reset_index(drop=True)
    nyse = p[p["NYSESample"] == 1]
    for q, name in ((0.2, "MktCapGTNYSE20PctlSample"),):
        cut = nyse.groupby("Month")["MktCapM"].quantile(q).rename("cut")
        p = p.join(cut, on="Month")
        p[name] = (p["MktCapM"] > p["cut"]).astype(int)
        p = p.drop(columns="cut")
    p["Top500"] = (p.groupby("Month")["MktCapM"].rank(ascending=False) <= 500).astype(int)
    p.to_parquet(PANEL, index=False)
    print(f"{len(p):,} stock-months, {p['Month'].min()}-{p['Month'].max()}, "
          f"{p['permno'].nunique():,} stocks; median {p.groupby('Month').size().median():.0f} a month")
    return 0


def load():
    return _before_seal(pd.read_parquet(PANEL))


def gate1() -> int:
    p = load()
    s = p[(p["Month"] >= 196307) & (p["Month"] <= 202212)]
    print("GATE 1: value-weighted 10-1, NYSE breakpoints, 1963-07 to 2022-12 (paper Table A12)")
    ok = True
    for var, target in (("Mom", 1.21), ("Rev", -0.34)):
        out, _ = legs(s, var, "MktCapM", bp_mask=s["NYSESample"] == 1)
        line(f"{var} (paper {target:+.2f})", out["gross"])
        hit = abs(out["gross"].mean() - target) <= 0.25
        ok &= hit
        print(f"     {'within' if hit else 'OUTSIDE'} 0.25 of {target:+.2f}")
    print(f"\n  GATE 1 {'PASSED' if ok else 'FAILED'}")
    (C / "gate1_ok.txt").write_text("1" if ok else "0")
    return 0


def authors_10_1() -> pd.Series:
    """The authors' real 10-1 monthly returns (their published decile returns)."""
    d = pd.read_csv(ROOT / "data" / "cbm_raw" / "Data" / "DailyMLERPortfolioExcessReturns.csv",
                    dtype={"date": str})
    d["Month"] = (d["date"].str[:4] + d["date"].str[5:7]).astype(int)
    p = d.pivot_table(index=["Month", "date"], columns="MLERPort", values="RetRF") / 100
    m = (1 + p).groupby(level=0).prod() - 1
    return (m[10] - m[1]) * 100


def score(p, tag, lo, hi):
    s = p[(p["Month"] >= lo) & (p["Month"] <= hi)].copy()
    s["MLER"] = cbm.predict(s[F].to_numpy(), tag)
    return s


def gate2() -> int:
    if (C / "gate1_ok.txt").read_text() != "1":
        print("REFUSING: gate 1 did not pass.")
        return 1
    p = load()
    theirs = authors_10_1()
    ok, ours_all = True, []
    for tag, fit_end, lo, hi, target in (("crsp_fit2004", 200412, 200501, 201412, -0.09),
                                         ("crsp_fit2014", 201412, 201501, 202212, 1.17)):
        cbm.train_ensemble(p[p["Month"] <= fit_end], tag)
        s = score(p, tag, lo, hi)
        out, _ = legs(s, "MLER", "MktCapM", bp_mask=s["NYSESample"] == 1)
        out["gross"].rename(tag).to_csv(C / f"{tag}_10_1.csv")
        ours_all.append(out["gross"])
        line(f"ours  {lo // 100}-{hi // 100}", out["gross"])
        line(f"theirs {lo // 100}-{hi // 100}", theirs.loc[lo:hi])
        hit = abs(out["gross"].mean() - target) <= 0.40
        ok &= hit
        print(f"     mean {'within' if hit else 'OUTSIDE'} 0.40 of {target:+.2f}")
    ours = pd.concat(ours_all)
    rho = ours.corr(theirs.reindex(ours.index))
    print(f"\n  monthly correlation with the authors' 10-1, 2005-2022: {rho:.2f} (gate >= 0.60)")
    ok &= rho >= 0.60
    print(f"  GATE 2 {'PASSED' if ok else 'FAILED'}")
    (C / "gate2_ok.txt").write_text("1" if ok else "0")
    return 0


def turnover_costs(s, cost_col="cost"):
    """Value-weighted 10-1 with drift-adjusted turnover. Returns monthly frame."""
    out, df = legs(s, "MLER", "MktCapM", bp_mask=s["NYSESample"] == 1)
    df["c"] = np.where(df["MktCapGTNYSE20PctlSample"] == 1, 0.0010, 0.0020)
    for d, name in ((9, "10"), (0, "1")):
        x = df[df["dec"] == d]
        w = x.pivot_table(index="Month", columns="permno", values="w", aggfunc="sum").fillna(0.0)
        r = x.pivot_table(index="Month", columns="permno", values="RetRF",
                          aggfunc="sum").reindex_like(w).fillna(0.0) / 100
        c = x.pivot_table(index="Month", columns="permno", values="c",
                          aggfunc="last").reindex_like(w).ffill().fillna(0.0020)
        drift = (w * (1 + r)).shift(1)
        drift = drift.div(drift.sum(axis=1), axis=0).fillna(0.0)
        trade = (w - drift).abs()
        trade.iloc[0] = w.iloc[0]
        out["turn" + name] = trade.sum(axis=1)
        out["c" + name] = (trade * c).sum(axis=1) * 100
    # Borrow on the short decile, a month at a time, in percent (added 2026-10-26;
    # the net figures before that date charge trading costs only).
    out["borrow"] = config.COSTS.borrow_bps_annual / 100 / 12
    out["net"] = out["gross"] - out["c10"] - out["c1"] - out["borrow"]
    return out


def costs() -> int:
    if (C / "gate2_ok.txt").read_text() != "1":
        print("REFUSING: gate 2 did not pass.")
        return 1
    p = load()
    parts = []
    for tag, lo, hi in (("crsp_fit2004", 200501, 201412), ("crsp_fit2014", 201501, 202212)):
        parts.append(turnover_costs(score(p, tag, lo, hi)))
    out = pd.concat(parts)
    out.to_csv(ROOT / "reports" / "cbm_crsp_costs.csv")
    print("REAL COSTS, our verified replica, 10bp/20bp per unit traded")
    for lo, hi in ((200501, 201412), (201501, 202212)):
        s = out.loc[lo:hi]
        line(f"gross {lo // 100}-{hi // 100}", s["gross"])
        line(f"net   {lo // 100}-{hi // 100}", s["net"])
        print(f"     turnover per leg: long {s['turn10'].mean():.0%}, short {s['turn1'].mean():.0%}; "
              f"cost {(s['c10'] + s['c1']).mean():.2f}%/mo")
    return 0


def final() -> int:
    if (C / "gate2_ok.txt").read_text() != "1":
        print("REFUSING: gate 2 did not pass.")
        return 1
    p = load()
    cbm.train_ensemble(p[p["Month"] <= 202212], "crsp_fit2022")
    return 0


def test() -> int:
    from evaluation import clustered_mean
    if (C / "gate2_ok.txt").read_text() != "1":
        print("REFUSING: gate 2 did not pass.")
        return 1
    p = load()
    out = turnover_costs(score(p, "crsp_fit2022", 202301, 202512))
    out.to_csv(ROOT / "reports" / "cbm_crsp_test.csv")
    g, n = out["gross"], out["net"]
    print(f"TRIAL 57 OUT OF SAMPLE: {len(g)} months {g.index.min()}-{g.index.max()}, all CRSP stocks")
    line("gross 10-1 (value-weighted, NYSE breakpoints)", g)
    line("net 10-1", n)
    pg, pn = nw_t(g) >= 2, nw_t(n) >= 2
    print(f"\n  gross t >= 2: {'PASS' if pg else 'FAIL'}   net t >= 2: {'PASS' if pn else 'FAIL'}")
    print(f"  VERDICT: {'PASS' if pg and pn else 'FAIL'}\n")
    print("descriptive only:")
    print(f"  minimum detectable effect: about {2.8 * g.std() / np.sqrt(len(g)):.2f}%/month")
    c = clustered_mean(g, pd.Series(g.index))
    print(f"  concentration: top-5 months carry {c['top5_share']:.0%}; trimmed mean "
          f"{c['trimmed_mean']:+.2f}%, alarm={c['alarm']}")
    print(f"  turnover per leg: long {out['turn10'].mean():.0%}, short {out['turn1'].mean():.0%}")
    s = score(p, "crsp_fit2022", 202301, 202512)
    t5, _ = legs(s[s["Top500"] == 1], "MLER", "MktCapM")
    line("Top 500 gross 10-1", t5["gross"])
    hist = pd.read_csv(ROOT / "reports" / "cbm_crsp_costs.csv", index_col=0)
    pooled = pd.concat([hist.loc[201501:202212, "net"], n])
    line("pooled net 2015-2025 (overlaps the paper; not out of sample)", pooled)
    return 0


def train(tag: str, fit_end: str) -> int:
    """Train one fit in its own process. Fits run back-to-back in a single process
    ran the 8GB laptop's GPU out of memory on the second fit, every time: the first
    fit's data and models were never released. A fresh process per fit avoids it."""
    if (C / "gate1_ok.txt").read_text() != "1":
        print("REFUSING: gate 1 did not pass.")
        return 1
    p = load()
    cbm.train_ensemble(p[p["Month"] <= int(fit_end)], tag)
    return 0


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "gate1"
    if cmd == "train":
        sys.exit(train(sys.argv[2], sys.argv[3]))
    sys.exit({"panel": panel, "gate1": gate1, "gate2": gate2, "costs": costs,
              "final": final, "test": test}[cmd]())
