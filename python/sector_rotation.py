#!/usr/bin/env python3
"""Sector rotation by relative strength (trial 48).

    python3 python/sector_rotation.py

Each month, hold the 3 of the 9 original SPDR sector ETFs with the best return
from 12 months ago to 1 month ago (Moskowitz & Grinblatt 1999). The fair
benchmark is the same 9 sectors equal-weighted: same assets, no signal. The
whole sample is after the 1999 publication. Nothing on or after the
2026-10-07 lockbox seal is read.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "python"))

import lockbox

SECTORS = ["XLB", "XLE", "XLF", "XLI", "XLK", "XLP", "XLU", "XLV", "XLY"]
TOP = 3
COST = 2e-4
CACHE = ROOT / "data" / "sector_etfs_daily.parquet"


def data(end: str) -> pd.DataFrame:
    if CACHE.exists():
        d = pd.read_parquet(CACHE)
    else:
        import yfinance as yf
        d = yf.download(SECTORS + ["SPY"], start="1998-12-22", end=end,
                        auto_adjust=True, progress=False)["Close"]
        d.index = d.index.tz_localize(None)
        d.to_parquet(CACHE)
    return d[d.index < end]


def book(m: pd.DataFrame, top: int) -> pd.DataFrame:
    r = m[SECTORS].pct_change()
    mom = m[SECTORS].shift(1) / m[SECTORS].shift(12) - 1      # 12-1 month return
    w = pd.DataFrame(0.0, index=m.index, columns=SECTORS)
    for dt, row in mom.iterrows():
        if row.notna().sum() == len(SECTORS):
            w.loc[dt, row.nlargest(top).index] = 1.0 / top
    w_held = w.shift(1)                                        # decided at the prior month-end
    turn = (w_held - w_held.shift(1)).abs().sum(axis=1)
    strat = (w_held * r).sum(axis=1) - turn * COST
    ew = r.mean(axis=1)
    ok = w_held.sum(axis=1) > 0.99
    return pd.DataFrame({"strat": strat, "ew": ew, "spy": m["SPY"].pct_change()})[ok]


def line(label, s):
    w = (1 + s).cumprod()
    print(f"    {label:<16}{(1 + s).prod() ** (12 / len(s)) - 1:>+8.1%}"
          f"{s.std() * 12 ** .5:>8.1%}{s.mean() / s.std() * 12 ** .5:>8.2f}"
          f"{(w / w.cummax() - 1).min():>9.1%}")


def show(label, b):
    ex = b["strat"] - b["ew"]
    t = ex.mean() / (ex.std() / np.sqrt(len(ex)))
    print(f"  {label}  ({len(b)} months)")
    print(f"    {'':<16}{'return':>8}{'vol':>8}{'Sharpe':>8}{'max DD':>9}")
    line("top-3 sectors", b["strat"])
    line("all 9 equal", b["ew"])
    line("SPY", b["spy"])
    print(f"    excess over all-9: {ex.mean() * 12:+.2%}/yr, t={t:.2f}")
    return t


def main() -> int:
    end = lockbox.state()["seal_from"]
    lockbox.guard((pd.Timestamp(end) - pd.Timedelta(days=1)).date().isoformat(),
                  "sector_rotation")
    m = data(end).resample("ME").last()
    b = book(m, TOP)
    print(f"trial 48: {b.index.min():%Y-%m} to {b.index.max():%Y-%m}\n")
    print("PRE-DECLARED TEST")
    t = show("1999-2026, all post-publication", b)
    s_sh = b["strat"].mean() / b["strat"].std()
    e_sh = b["ew"].mean() / b["ew"].std()
    ok = t >= 2.0 and s_sh > e_sh
    print(f"\n  excess t >= 2.0:            {'PASS' if t >= 2 else 'FAIL'}")
    print(f"  Sharpe above all-9 equal:   {'PASS' if s_sh > e_sh else 'FAIL'}")
    print(f"  VERDICT: {'PASS' if ok else 'FAIL'}\n")
    print("descriptive only:")
    show("2014-2026", b.loc["2014-01":])
    for k in (1, 5):
        show(f"top-{k} variant, 1999-2026", book(m, k))
    b.to_csv(ROOT / "reports" / "sector_rotation.csv")
    print("\nwrote reports/sector_rotation.csv")
    return 0


if __name__ == "__main__":
    sys.exit(main())
