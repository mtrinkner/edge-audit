#!/usr/bin/env python3
"""One pre-declared replication on ETFs this project had never seen (trial 42).

    python3 python/replicate_etf.py

Trial 41 found vwap_reversion_long net-positive per day on mega-caps and on 24
ETFs, with the ETF version at an annualized Sharpe of 1.52, above the luck bar.
But it failed the bar declared for that trial, it was 1 of 24 variants, and the
daily-budget weighting was suggested by earlier looks at the data. So the whole
specification was frozen in the registry and run once, on 24 different ETFs,
whose data was pulled only after the declaration.

The daily series counts a session with no signal as ZERO, not as missing. Trial
41's 1.52 skipped those days, which flatters the Sharpe slightly.

PASS, both required: Sharpe above the registry luck bar at record time, and a
one-sided t-test of the mean daily return with p < 0.05.

RESULT AND A FLAW IN THE TEST ITSELF. Sharpe 1.190, failing the declared 1.219
(which was computed at the registry's weekly default; the correct daily bar was
1.921). Worse, the equal split across the day's signals needs to know at entry
how many will fire later, and that count predicts the outcome. Taking only the
day's first signal, which is causal, gives Sharpe -1.27.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import t as tdist

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "python"))

from cheap_venues import edge_costs, sessionize, signals

RULE = "vwap_reversion_long"
LUCK_BAR = 1.219


def main() -> int:
    d = sessionize(pd.read_parquet(ROOT / "data" / "etf2_1h_raw.parquet"))
    print(f"{d['symbol'].nunique()} fresh ETFs, {d['session'].nunique()} complete sessions\n")
    sig = signals(d)
    sig = sig[sig["rule"] == RULE]
    cost = edge_costs(d)
    print(f"  median EDGE round trip: {np.median(list(cost.values())) * 1e4:.2f}bp")

    last = d.groupby(["symbol", "session"])["close"].transform("last")
    d["gross"] = last / d["close"] - 1.0
    d["cost"] = [cost.get((s, x[:7]), np.nan) for s, x in zip(d["symbol"], d["session"])]
    x = sig.join(d.set_index(["symbol", "session", "bar"])[["gross", "cost"]],
                 on=["symbol", "session", "bar"]).dropna()
    x["net"] = x["gross"] - x["cost"]

    # Every session for which a cost could exist (from the second month on).
    universe = sorted(d.loc[d["cost"].notna(), "session"].unique())
    daily = x.groupby("session")["net"].mean().reindex(universe, fill_value=0.0)
    n = len(daily)
    mu, sd = daily.mean(), daily.std(ddof=1)
    sharpe = mu / sd * np.sqrt(252)
    tt = mu / (sd / np.sqrt(n))
    p = tdist.sf(tt, n - 1)

    pd.DataFrame({"session": daily.index, "net": daily.to_numpy()}).to_csv(
        ROOT / "reports" / "replicate_etf_daily.csv", index=False)
    x.to_csv(ROOT / "reports" / "replicate_etf_trades.csv", index=False)

    print(f"  trades {len(x):,} on {int((daily != 0).sum())} of {n} sessions\n")
    print("PRE-DECLARED TEST")
    print(f"  mean daily net      {mu * 1e4:+.2f}bp")
    print(f"  annualized Sharpe   {sharpe:.3f}   (bar {LUCK_BAR})   "
          f"{'PASS' if sharpe > LUCK_BAR else 'FAIL'}")
    print(f"  one-sided t, p      t={tt:.2f}  p={p:.4f}   {'PASS' if p < 0.05 else 'FAIL'}")
    verdict = sharpe > LUCK_BAR and p < 0.05
    print(f"\n  VERDICT: {'REPLICATED' if verdict else 'DID NOT REPLICATE'}")

    print("\ndescriptive only, not the pass bar:")
    print(f"  per trade: gross {x['gross'].mean() * 1e4:+.2f}bp, cost {x['cost'].mean() * 1e4:.2f}bp, "
          f"net {x['net'].mean() * 1e4:+.2f}bp")
    yr = daily.groupby(pd.Index(daily.index).str[:4]).agg(["mean", "std", "size"])
    for y, r in yr.iterrows():
        print(f"  {y}: mean {r['mean'] * 1e4:+.2f}bp/day, "
              f"Sharpe {r['mean'] / r['std'] * np.sqrt(252):+.2f}, {int(r['size'])} sessions")
    by = x.groupby("symbol")["net"].agg(["mean", "size"]).sort_values("mean")
    print(f"  ETFs with positive mean net per trade: {(by['mean'] > 0).sum()} of {len(by)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
