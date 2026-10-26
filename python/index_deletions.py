#!/usr/bin/env python3
"""S&P 500 deletions, bought in the index funds' rebalance auction (trial 45).

    python3 python/index_deletions.py

Index funds must sell a deleted stock, and they sell it in the closing auction
of the last session BEFORE the effective date. Whoever buys in that auction
provides the liquidity, and should be paid by a bounce once the forced selling
stops. Trial 19 bought one session later and found nothing significant, which a
fast bounce would explain. This moves the entry to the rebalance close itself.

The event list is Wikipedia's S&P 500 change table, taken from revision
1368152951 (2026-08-07) because the current page no longer carries it. Only
deletions explicitly made for market capitalization or representation are used.
Acquisitions are excluded: their price is pinned to deal terms.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "python"))

import db
from evaluation import clustered_mean, mde

HOLD = 5
COST = 20e-4
CACHE = ROOT / "data" / "deletion_prices.parquet"


def events() -> tuple[pd.DataFrame, pd.DataFrame]:
    t = pd.read_csv(ROOT / "data" / "sp500_changes_raw.csv")
    t["eff"] = pd.to_datetime(t["Effective Date"], errors="coerce")
    t = t[t["eff"] >= "2014-01-01"]
    r = t["Reason"].fillna("").str.lower()
    mcap = r.str.contains("market cap|market capitalization|representative")
    dele = t[t["Removed_Ticker"].notna() & mcap].copy()
    dele["symbol"] = dele["Removed_Ticker"].str.replace(".", "-", regex=False).str.strip()
    add = t[t["Added_Ticker"].notna()].copy()
    add["symbol"] = add["Added_Ticker"].str.replace(".", "-", regex=False).str.strip()
    return dele[["symbol", "eff"]], add[["symbol", "eff"]]


def prices(symbols: set) -> pd.DataFrame:
    with db.connect() as con:
        q = ",".join(f"'{s}'" for s in symbols | {"SPY"})
        p = pd.read_sql(f"SELECT symbol, dt, adj_close FROM bars WHERE symbol IN ({q})", con)
    missing = sorted(symbols - set(p["symbol"]))
    extra = pd.read_parquet(CACHE) if CACHE.exists() else pd.DataFrame(
        columns=["symbol", "dt", "adj_close"])
    need = [s for s in missing if s not in set(extra["symbol"])]
    if need:
        import yfinance as yf
        y = yf.download(need, start="2013-06-01", end="2026-10-10", auto_adjust=True,
                        progress=False, group_by="ticker", threads=True)
        rows = []
        for s in need:
            try:
                c = (y[s] if isinstance(y.columns, pd.MultiIndex) else y)["Close"].dropna()
            except KeyError:
                continue
            rows.append(pd.DataFrame({"symbol": s, "dt": c.index.strftime("%Y-%m-%d"),
                                      "adj_close": c.to_numpy()}))
        if rows:
            extra = pd.concat([extra] + rows, ignore_index=True)
            extra.to_parquet(CACHE, index=False)
    return pd.concat([p, extra], ignore_index=True)


def event_returns(ev: pd.DataFrame, px: pd.DataFrame, offset: int, hold: int,
                  sign: float) -> pd.DataFrame:
    """offset 0 = enter at the close of the last session BEFORE the effective date
    (the rebalance close); offset 1 = the effective-date close (trial 19)."""
    spy = px[px["symbol"] == "SPY"].set_index("dt")["adj_close"].sort_index()
    cal = list(spy.index)
    wide = {s: g.set_index("dt")["adj_close"] for s, g in px.groupby("symbol")}
    out = []
    for s, eff in zip(ev["symbol"], ev["eff"]):
        e = eff.strftime("%Y-%m-%d")
        i = np.searchsorted(cal, e) - 1 + offset      # last session before eff, + offset
        if i < 0 or i + hold >= len(cal):
            continue
        d0, d1 = cal[i], cal[i + hold]
        p = wide.get(s)
        if p is None or d0 not in p.index or d1 not in p.index:
            out.append((s, e, d0, np.nan))
            continue
        r = p[d1] / p[d0] - 1 - (spy[d1] / spy[d0] - 1)
        out.append((s, e, d0, sign * r - COST))
    return pd.DataFrame(out, columns=["symbol", "eff", "entry", "net"])


def show(label, x):
    x = x.dropna(subset=["net"])
    c = clustered_mean(x["net"], x["entry"])
    print(f"  {label:<44}{c['mean'] * 100:+.2f}%  t={c['t']:.2f}  n={c['trades']}  "
          f"MDE {mde(c['se']) * 100:.2f}%  hit {(x['net'] > 0).mean():.0%}")
    return c


def main() -> int:
    dele, add = events()
    px = prices(set(dele["symbol"]) | set(add["symbol"]))
    print(f"trial 45: {len(dele)} market-cap deletions since 2014\n")

    prim = event_returns(dele, px, 0, HOLD, +1.0)
    lost = prim["net"].isna().sum()
    print(f"  priced at entry and exit: {prim['net'].notna().sum()} of {len(prim)} "
          f"({lost} unpriceable, dropped)\n")
    print("PRE-DECLARED TEST (buy at the rebalance close, hold 5 sessions, minus SPY, net 20bp)")
    c = show("deletions, rebalance close, 5 sessions", prim)
    ok = c["mean"] > 0 and c["t"] >= 2.0
    print(f"\n  VERDICT: {'PASS' if ok else 'FAIL'}")

    print("\ndescriptive only:")
    for h in (1, 10):
        show(f"deletions, rebalance close, {h} session(s)", event_returns(dele, px, 0, h, +1.0))
    show("deletions, trial 19 timing (effective close), 5", event_returns(dele, px, 1, HOLD, +1.0))
    show("additions SHORTED at rebalance close, 5", event_returns(add, px, 0, HOLD, -1.0))
    prim["year"] = prim["eff"].str[:4]
    for y, g in prim.dropna().groupby(prim["eff"].str[:4].astype(int) // 4 * 4):
        print(f"  {y}-{y + 3}: {g['net'].mean() * 100:+.2f}% (n={len(g)})")
    prim.to_csv(ROOT / "reports" / "index_deletions.csv", index=False)
    print("\nwrote reports/index_deletions.csv")
    return 0


if __name__ == "__main__":
    sys.exit(main())
