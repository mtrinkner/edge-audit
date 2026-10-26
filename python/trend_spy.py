#!/usr/bin/env python3
"""Trend-following on SPY with a 10-month moving average (trial 47).

    python3 python/trend_spy.py

The most studied technical rule there is. At each month-end, hold SPY next month
if it closed above its 10-month average, otherwise hold T-bills (Faber 2007).
It doesn't claim to beat the market on return. It claims to sidestep the
large crashes and so do better RISK-ADJUSTED, which is what the pass bar tests.

The rule was published from 1973-2012 data, so 1993-2013 here is not evidence.
The test is 2014-2026, untouched by the rule's design. Nothing on or after the
2026-10-07 lockbox seal is read.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import norm

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "python"))

import lockbox

CACHE = ROOT / "data" / "spy_irx_daily.parquet"
SWITCH = 2e-4
HOLDOUT = ("2014-01", "2026-09")


def data(end: str) -> pd.DataFrame:
    if CACHE.exists():
        d = pd.read_parquet(CACHE)
    else:
        import yfinance as yf
        spy = yf.download("SPY", start="1993-01-29", end=end, auto_adjust=True,
                          progress=False)["Close"].squeeze()
        irx = yf.download("^IRX", start="1993-01-01", end=end, auto_adjust=False,
                          progress=False)["Close"].squeeze()
        d = pd.DataFrame({"spy": spy, "irx": irx}).sort_index()
        d["irx"] = d["irx"].ffill()
        d = d.dropna(subset=["spy"])
        d.index = d.index.tz_localize(None)
        d.to_parquet(CACHE)
    return d[d.index < end]


def stats(r: pd.Series, rf: pd.Series, k: int) -> dict:
    ex = r - rf
    w = (1 + r).cumprod()
    return {"ret": (1 + r).prod() ** (k / len(r)) - 1, "vol": r.std() * np.sqrt(k),
            "sharpe": ex.mean() / ex.std() * np.sqrt(k),
            "maxdd": (w / w.cummax() - 1).min()}


def memmel(a: pd.Series, b: pd.Series) -> tuple[float, float]:
    """Jobson-Korkie test of equal Sharpe ratios, with Memmel's correction."""
    n = len(a)
    rho = np.corrcoef(a, b)[0, 1]
    # Memmel (2003): var = (1/n)[2(1-rho) + 0.5(SRa^2 + SRb^2 - 2 SRa SRb rho^2)]
    sra, srb = a.mean() / a.std(), b.mean() / b.std()
    theta = (2 * (1 - rho) + 0.5 * (sra**2 + srb**2 - 2 * sra * srb * rho**2)) / n
    z = (sra - srb) / np.sqrt(theta)
    return z, 2 * norm.sf(abs(z))


def monthly(d: pd.DataFrame) -> pd.DataFrame:
    m = d.resample("ME").last()
    m["r_spy"] = m["spy"].pct_change()
    m["rf"] = (m["irx"].shift(1) / 100) / 12             # known at the prior month-end
    m["sma10"] = m["spy"].rolling(10).mean()
    m["in"] = (m["spy"] > m["sma10"]).shift(1)           # decided at the prior month-end
    m = m.dropna(subset=["r_spy", "rf", "in"])
    m["in"] = m["in"].astype(bool)
    switch = m["in"] != m["in"].shift(1)
    m["r_strat"] = np.where(m["in"], m["r_spy"], m["rf"]) - np.where(switch, SWITCH, 0)
    return m


def daily200(d: pd.DataFrame) -> pd.DataFrame:
    x = d.copy()
    x["r_spy"] = x["spy"].pct_change()
    x["rf"] = (x["irx"].shift(1) / 100) / 252
    x["in"] = (x["spy"] > x["spy"].rolling(200).mean()).shift(1)
    x = x.dropna(subset=["r_spy", "rf", "in"])
    x["in"] = x["in"].astype(bool)
    sw = x["in"] != x["in"].shift(1)
    x["r_strat"] = np.where(x["in"], x["r_spy"], x["rf"]) - np.where(sw, SWITCH, 0)
    return x


def report(label, x, k):
    s, b = stats(x["r_strat"], x["rf"], k), stats(x["r_spy"], x["rf"], k)
    z, p = memmel(x["r_strat"] - x["rf"], x["r_spy"] - x["rf"])
    sw = int((x["in"] != x["in"].shift(1)).sum())
    print(f"  {label}")
    print(f"    {'':<14}{'return':>9}{'vol':>8}{'Sharpe':>8}{'max DD':>9}")
    print(f"    {'trend rule':<14}{s['ret']:>+9.1%}{s['vol']:>8.1%}{s['sharpe']:>8.2f}{s['maxdd']:>9.1%}")
    print(f"    {'buy and hold':<14}{b['ret']:>+9.1%}{b['vol']:>8.1%}{b['sharpe']:>8.2f}{b['maxdd']:>9.1%}")
    print(f"    in the market {x['in'].mean():.0%} of the time, {sw} switches; "
          f"Sharpe difference z={z:+.2f}, p={p:.2f}")
    return s, b


def main() -> int:
    st = lockbox.state()
    end = st["seal_from"]                                 # exclusive: never read the seal
    lockbox.guard((pd.Timestamp(end) - pd.Timedelta(days=1)).date().isoformat(), "trend_spy")
    d = data(end)
    print(f"trial 47: SPY {d.index.min().date()} to {d.index.max().date()}\n")
    m = monthly(d)
    h = m.loc[HOLDOUT[0]:HOLDOUT[1]]
    print("PRE-DECLARED TEST: 10-month average, 2014-01 to 2026-09 holdout")
    s, b = report("holdout", h, 12)
    ok = s["sharpe"] > b["sharpe"] and s["maxdd"] > b["maxdd"]
    print(f"\n  Sharpe higher than holding: {'PASS' if s['sharpe'] > b['sharpe'] else 'FAIL'}")
    print(f"  smaller max drawdown:       {'PASS' if s['maxdd'] > b['maxdd'] else 'FAIL'}")
    print(f"  VERDICT: {'PASS' if ok else 'FAIL'}\n")

    print("descriptive only:")
    report("1993-2013 (the rule's published era, not evidence)", m.loc[:"2013-12"], 12)
    report("full 1993-2026", m, 12)
    x = daily200(d)
    report("200-day daily variant, 2014-2026", x.loc["2014-01-01":], 252)
    report("200-day daily variant, 1993-2013", x.loc[:"2013-12-31"], 252)
    m.to_csv(ROOT / "reports" / "trend_spy_monthly.csv")
    print("\nwrote reports/trend_spy_monthly.csv")
    return 0


if __name__ == "__main__":
    sys.exit(main())
