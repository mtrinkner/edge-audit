#!/usr/bin/env python3
"""Daily confluence: a 200-day EMA pullback in an uptrend, oversold RSI, near support (trial 46).

    python3 python/confluence_daily.py

The claim under test, from a pasted article: a strong edge comes from several
signals lining up, such as "a trendline support, a 200-day EMA bounce, and an
oversold RSI". Trial 40 tested confluence among intraday rules and found that
stacking them made results slightly worse. This is the daily setup the article
describes, which had never been tested here.

Every condition is computed on the signal day's close from adjusted prices.
Trades go through python/ta_exits.py unchanged: next-open entry, 2 ATR stop,
3 ATR target, 60-session timeout, ties to the stop, and a benchmark of holding
the same stock for the same days. Data stops the day before the lockbox seal.

FIXED 2026-10-26 (audit): the ta_exits gap fill (stops and targets now fill at
the open when a bar opens through them) and the t-statistic, which clustered
overlapping 60-session trades by entry date and now uses calendar blocks
(evaluation.block_clustered_mean). Earlier output: reports/confluence_daily_pre_2026-10-26.csv.
"""

from __future__ import annotations

import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "python"))

import lockbox
import signals as sg
from evaluation import block_clustered_mean, mde
from ta_exits import TIMEOUT, load_bars, simulate_symbol


def conditions(g: pd.DataFrame) -> dict[str, pd.Series]:
    r = g["adj_close"] / g["close"].replace(0, np.nan)
    c = g["adj_close"]
    lo = g["low"] * r
    ema200 = c.ewm(span=200, adjust=False, min_periods=200).mean()
    sma50 = c.rolling(50, min_periods=50).mean()
    support = lo.shift(6).rolling(55, min_periods=55).min()     # sessions t-60..t-6
    return {
        "uptrend": (ema200 > ema200.shift(20)) & (sma50 > ema200),
        "ema_pullback": (lo <= ema200 * 1.01) & (c >= ema200),
        "rsi_oversold": sg.rsi(c, 14) < 30,
        "near_support": (lo >= support) & (lo <= support * 1.03),
    }


COMBOS = {
    "FULL: uptrend + EMA pullback + RSI<30 + support": ["uptrend", "ema_pullback", "rsi_oversold", "near_support"],
    "uptrend + EMA pullback": ["uptrend", "ema_pullback"],
    "uptrend + EMA pullback + RSI<30": ["uptrend", "ema_pullback", "rsi_oversold"],
    "uptrend + EMA pullback + support": ["uptrend", "ema_pullback", "near_support"],
    "RSI<30 + support": ["rsi_oversold", "near_support"],
    "RSI<30 alone": ["rsi_oversold"],
}


def make(parts):
    def f(g):
        cs = conditions(g)
        m = cs[parts[0]]
        for p in parts[1:]:
            m = m & cs[p]
        return m.fillna(False).astype(bool)
    return f


def main() -> int:
    st = lockbox.state()
    max_date = (pd.Timestamp(st["seal_from"]) - pd.Timedelta(days=1)).date().isoformat()
    lockbox.guard(max_date, "confluence_daily")
    d = load_bars(max_date)
    print(f"trial 46: {d['symbol'].nunique()} stocks, data through {max_date}\n")
    custom = {k: make(v) for k, v in COMBOS.items()}
    parts = []
    for _, g in d.groupby("symbol", sort=False):
        parts += simulate_symbol(g, list(COMBOS), custom)
    t = pd.concat(parts, ignore_index=True)
    t.to_csv(ROOT / "reports" / "confluence_daily_trades.csv", index=False)

    rows = []
    for name in COMBOS:
        x = t[t["signal"] == name]
        if x.empty:
            rows.append({"setup": name, "trades": 0})
            continue
        # Holds overlap for up to TIMEOUT sessions: cluster by blocks that long
        # (fixed 2026-10-26; this used entry-date clusters).
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            ce = block_clustered_mean(x["excess"], x["dt"], TIMEOUT)
            cn = block_clustered_mean(x["net"], x["dt"], TIMEOUT)
        rows.append({"setup": name, "trades": len(x), "blocks": ce["clusters"],
                     "net": cn["mean"], "hold": x["bench"].mean(), "excess": ce["mean"],
                     "t": ce["t"], "mde": mde(ce["se"]),
                     "stop%": (x["outcome"] == "stop").mean(),
                     "target%": (x["outcome"] == "target").mean()})
    r = pd.DataFrame(rows)
    r.to_csv(ROOT / "reports" / "confluence_daily.csv", index=False)

    print(f"{'setup':<50}{'trades':>8}{'net':>8}{'hold':>8}{'excess':>8}{'t':>7}"
          f"{'MDE':>7}{'stop':>6}{'tgt':>6}")
    for _, x in r.iterrows():
        if not x["trades"]:
            print(f"{x['setup']:<50}{0:>8}")
            continue
        print(f"{x['setup']:<50}{int(x['trades']):>8}{x['net']:>+8.2%}{x['hold']:>+8.2%}"
              f"{x['excess']:>+8.2%}{x['t']:>7.2f}{x['mde']:>7.2%}{x['stop%']:>6.0%}"
              f"{x['target%']:>6.0%}")
    f = r.iloc[0]
    print("\nPRE-DECLARED TEST (first row)")
    if f["trades"] < 100:
        print(f"  only {int(f['trades'])} trades: INCONCLUSIVE, as declared")
        return 0
    ok = f["excess"] > 0 and f["t"] >= 2.0 and f["net"] > 0
    print(f"  excess {f['excess']:+.2%} t={f['t']:.2f} (needs > 0, t >= 2); "
          f"net {f['net']:+.2%} (needs > 0)")
    print(f"  VERDICT: {'PASS' if ok else 'FAIL'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
