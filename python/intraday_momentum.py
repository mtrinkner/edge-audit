#!/usr/bin/env python3
"""Intraday market momentum on SPY, QQQ and IWM (trial 43).

    python3 python/intraday_momentum.py

The first audit-era test, chosen for its mechanism rather than its chart. Gao,
Han, Li and Zhou (2018) found that the market's return from the previous close
to the first half-hour predicts the last half-hour. The explanation is that
leveraged ETFs and option dealers must trade in the direction of the day's move
near the close, whatever the price, and late-informed traders act then too.

SPEC, fixed in the registry before this ran:
  r1     = close of the 09:30 bar / previous session close - 1
  trade  = at 15:30, long if r1 > 0, short if r1 < 0; out at the 16:00 close
  sizing = a fixed third of capital per ETF per day
  cost   = 2bp round trip
  pass   = net per-trade clustered t >= 2.0 AND net daily Sharpe > 1.331
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "python"))

import data_access as da
from deflated_sharpe import expected_max_sharpe
from evaluation import breakeven_report, clustered_mean, mde, sharpe

ETFS = ["SPY", "QQQ", "IWM"]
COST = 2e-4
BAR_DECLARED = 1.331


def build() -> pd.DataFrame:
    # Through data_access, which stops at the lockbox seal (added 2026-10-26).
    d = da.parquet("intraday_momentum", "etf_1h_raw")
    d = d[d["symbol"].isin(ETFS)].copy()
    d["ts"] = pd.to_datetime(d["ts"], utc=True).dt.tz_convert("America/New_York")
    d["session"] = d["ts"].dt.date.astype(str)
    d["hm"] = d["ts"].dt.strftime("%H:%M")
    d = d.sort_values(["symbol", "ts"])
    rows = []
    for sym, g in d.groupby("symbol"):
        # The previous close comes from the raw data, so a half-day before a full
        # day still provides one.
        last = g.groupby("session")["close"].last()
        prev = last.shift(1)
        piv = g.pivot_table(index="session", columns="hm", values="close", aggfunc="last")
        need = ["09:30", "14:30", "15:30"]
        piv = piv.dropna(subset=[c for c in need if c in piv])
        x = pd.DataFrame({
            "symbol": sym,
            "r1": piv["09:30"] / prev.reindex(piv.index) - 1.0,
            "entry": piv["14:30"],          # close of the 14:30 bar = 15:30 price
            "exit": piv["15:30"],           # close of the 15:30 bar = 16:00 close
            "spy_day": np.nan,
        }).dropna(subset=["r1"])
        rows.append(x)
    t = pd.concat(rows).reset_index().rename(columns={"index": "session"})
    t["r_last"] = t["exit"] / t["entry"] - 1.0
    t["side"] = np.sign(t["r1"])
    t = t[t["side"] != 0]
    t["gross"] = t["side"] * t["r_last"]
    t["net"] = t["gross"] - COST
    return t


def main() -> int:
    t = build()
    sessions = sorted(t["session"].unique())
    print(f"trial 43: {len(t):,} trades, {len(sessions)} sessions, {t['symbol'].nunique()} ETFs\n")

    g = clustered_mean(t["gross"], t["session"])
    n = clustered_mean(t["net"], t["session"])
    daily = t.groupby("session")["net"].sum() / len(ETFS)   # a fixed third each
    daily = daily.reindex(sessions, fill_value=0.0)
    sr = sharpe(daily, 252)
    px = t["entry"]
    be = breakeven_report(t["gross"], t["session"], px)

    print("PRE-DECLARED TEST")
    print(f"  gross per trade   {g['mean'] * 1e4:+.2f}bp  t={g['t']:.2f}  "
          f"(MDE {mde(g['se']) * 1e4:.2f}bp)")
    print(f"  net per trade     {n['mean'] * 1e4:+.2f}bp  t={n['t']:.2f}   "
          f"{'PASS' if n['t'] >= 2.0 else 'FAIL'}  (needs t >= 2.0)")
    print(f"  net daily Sharpe  {sr:.3f}   {'PASS' if sr > BAR_DECLARED else 'FAIL'}  "
          f"(needs > {BAR_DECLARED}; raw-N bar "
          f"{expected_max_sharpe(838, len(sessions), 252):.3f})")
    ok = n["t"] >= 2.0 and sr > BAR_DECLARED
    print(f"\n  VERDICT: {'PASS' if ok else 'FAIL'}")

    print("\ndescriptive only:")
    print(f"  break-even round trip {be['breakeven_rt_bp']:+.2f}bp "
          f"(lower 95% {be['breakeven_lower95_bp']:+.2f}bp); one-cent floor "
          f"{be['one_cent_floor_rt_bp']:.2f}bp")
    print(f"  hit rate {(t['gross'] > 0).mean():.1%}; gross daily Sharpe "
          f"{sharpe((t.groupby('session')['gross'].sum() / 3).reindex(sessions, fill_value=0), 252):.2f}")
    for sym, x in t.groupby("symbol"):
        c = clustered_mean(x["gross"], x["session"])
        print(f"  {sym}: gross {c['mean'] * 1e4:+.2f}bp t={c['t']:.2f}  ({len(x)} days)")
    t["year"] = t["session"].str[:4]
    for y, x in t.groupby("year"):
        c = clustered_mean(x["gross"], x["session"])
        print(f"  {y}: gross {c['mean'] * 1e4:+.2f}bp t={c['t']:.2f}  ({x['session'].nunique()} sessions)")
    # Conditioning the paper reports: stronger on big first-hour moves. Descriptive.
    t["big"] = t.groupby("symbol")["r1"].transform(lambda s: s.abs() > s.abs().median())
    for b, x in t.groupby("big"):
        c = clustered_mean(x["gross"], x["session"])
        print(f"  first-hour move {'above' if b else 'below'} median: gross "
              f"{c['mean'] * 1e4:+.2f}bp t={c['t']:.2f}")
    spy = t[t["symbol"] == "SPY"].set_index("session")
    beta = np.cov(daily.reindex(spy.index), spy["r_last"])[0, 1] / spy["r_last"].var()
    print(f"  beta of the book to SPY's last half-hour: {beta:+.2f}")
    t.to_csv(ROOT / "reports" / "intraday_momentum.csv", index=False)
    print("\nwrote reports/intraday_momentum.csv")
    return 0


if __name__ == "__main__":
    sys.exit(main())
