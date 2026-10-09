#!/usr/bin/env python3
"""A point-in-time Hyperliquid universe, delisted coins included.

    python3 crypto/ingest_hl_universe.py
    python3 crypto/ingest_hl_universe.py --funding-only   # resume the slow part

Strategies 20-22 ran on today's 16 volume leaders. That is a universe of
survivors: a coin is a leader today partly because it went up, and its funding
was high on the way. Every result built on it inherits that.

Hyperliquid's meta still lists delisted perps (flagged isDelisted) and still
serves their candles and funding. So the universe can be rebuilt as it would
have looked at the time: at each week start, the top 20 listed coins by trailing
30-day notional volume, using only data from before that week. Size and window
were fixed in the registry before this was run.

Writes, to crypto/data/:
    hl_candles.parquet      daily perp candles, every coin in meta
    hl_universe.parquet     week, coin, rank
    hl_funding_pit.parquet  hourly funding for every coin that ever made the cut
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import pandas as pd

from ingest_hl_funding import START_MS, funding, post

DATA = Path(__file__).resolve().parent / "data"
CACHE = DATA / "hl_funding_cache"   # one file per coin, so a killed run resumes
TOP_N = 20
VOLUME_DAYS = 30


def candles(coin: str) -> pd.DataFrame:
    end = int(time.time() * 1000)
    c = post({"type": "candleSnapshot",
              "req": {"coin": coin, "interval": "1d", "startTime": START_MS, "endTime": end}})
    if not c:
        return pd.DataFrame()
    d = pd.DataFrame(c)
    d["day"] = pd.to_datetime(d["t"].astype("int64"), unit="ms", utc=True)
    d["close"] = d["c"].astype(float)
    d["notional"] = d["v"].astype(float) * d["close"]
    d["coin"] = coin
    return d[["coin", "day", "close", "notional"]]


def universe(c: pd.DataFrame) -> pd.DataFrame:
    """Top TOP_N by trailing notional volume, decided before each week opens."""
    vol = c.pivot(index="day", columns="coin", values="notional").sort_index()
    # Through yesterday only. A coin with no candle yesterday is not listed.
    trail = vol.rolling(f"{VOLUME_DAYS}D").sum().shift(1)
    listed = vol.notna().shift(1, fill_value=False)
    trail = trail.where(listed)
    mondays = trail.index[trail.index.dayofweek == 0]
    rows = []
    for d in mondays:
        top = trail.loc[d].dropna().nlargest(TOP_N)
        rows += [{"week_start": d, "coin": k, "rank": i + 1} for i, k in enumerate(top.index)]
    return pd.DataFrame(rows)


def pull_funding(coins: list[str]) -> pd.DataFrame:
    """About 60 requests per coin and 110 coins is most of an hour, so each coin
    is cached as it finishes and a restart picks up where the last one died."""
    CACHE.mkdir(exist_ok=True)
    frames = []
    for i, k in enumerate(coins, 1):
        path = CACHE / f"{k}.parquet"
        if path.exists():
            d = pd.read_parquet(path)
        else:
            d = funding(k)
            d.to_parquet(path, index=False)
        frames.append(d)
        print(f"  funding {i}/{len(coins)} {k:<10} rows={sum(len(x) for x in frames):,}",
              flush=True)
    return pd.concat([x for x in frames if not x.empty], ignore_index=True)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--funding-only", action="store_true",
                    help="reuse the saved candles and universe, pull only funding")
    args = ap.parse_args()

    if args.funding_only:
        u = pd.read_parquet(DATA / "hl_universe.parquet")
    else:
        meta = post({"type": "meta"})["universe"]
        names = [m["name"] for m in meta]
        delisted = {m["name"] for m in meta if m.get("isDelisted")}
        print(f"{len(names)} perps in meta, {len(delisted)} flagged delisted")

        frames = []
        for i, nm in enumerate(names, 1):
            frames.append(candles(nm))
            print(f"\r  candles {i}/{len(names)} {nm:<10}", end="", flush=True)
            time.sleep(0.15)
        print()
        c = pd.concat([f for f in frames if not f.empty], ignore_index=True)
        c["delisted"] = c["coin"].isin(delisted)
        c.to_parquet(DATA / "hl_candles.parquet", index=False)
        print(f"candles: {len(c):,} coin-days, {c.coin.nunique()} coins")

        u = universe(c)
        u.to_parquet(DATA / "hl_universe.parquet", index=False)
        ever = sorted(u["coin"].unique())
        gone = [k for k in ever if k in delisted]
        print(f"universe: {u.week_start.nunique()} weeks, {len(ever)} coins ever in the top "
              f"{TOP_N}, {len(gone)} of them since delisted: {gone}")

    f = pull_funding(sorted(u["coin"].unique()))
    f.to_parquet(DATA / "hl_funding_pit.parquet", index=False)
    print(f"funding: {len(f):,} prints, {f.coin.nunique()} coins")
    return 0


if __name__ == "__main__":
    sys.exit(main())
