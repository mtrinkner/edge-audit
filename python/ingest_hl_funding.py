#!/usr/bin/env python3
"""Hyperliquid perpetual funding history: the carry trade's raw material.

    python3 python/ingest_hl_funding.py --coins 25

OKX's public funding endpoint retains about a hundred days, which is not enough
to test anything. Binance and Bybit return 451 and 403 from this location.
Hyperliquid serves hourly funding back to May 2023, which is three and a half
years, and it is a public endpoint with no key.

WHAT FUNDING IS. A perpetual swap never expires, so nothing forces it to
converge to spot. The exchange substitutes a periodic payment: when the perp
trades above the index, longs pay shorts, and when it trades below, shorts pay
longs. Hyperliquid settles this hourly. A trader who is short the perp and long
the underlying is delta-neutral and receives the payment as a contractual cash
flow rather than as a forecast.

The economic content is simple and worth stating plainly: in crypto, demand for
levered long exposure persistently exceeds demand for levered short exposure, so
the carry is usually positive. Collecting it is supplying leverage, and the
compensation is for bearing the risk that comes with that, including the risk
that the venue holding your collateral fails. That last risk is not in any
backtest and has materialised repeatedly in this industry.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request

import pandas as pd

import config

URL = "https://api.hyperliquid.xyz/info"
UA = {"User-Agent": "edge-audit research", "Content-Type": "application/json"}
START_MS = 1683849600000          # 2023-05-12, the start of available history


def post(payload: dict, retries: int = 3):
    for a in range(retries):
        try:
            req = urllib.request.Request(URL, data=json.dumps(payload).encode(),
                                         headers=UA, method="POST")
            with urllib.request.urlopen(req, timeout=90) as f:
                return json.loads(f.read().decode())
        except Exception:
            time.sleep(1.0 * (a + 1))
    return []


def universe(n: int) -> list[str]:
    meta = post({"type": "metaAndAssetCtxs"})
    if not meta or len(meta) < 2:
        return []
    names = [u["name"] for u in meta[0]["universe"]]
    ctxs = meta[1]
    rows = []
    for nm, c in zip(names, ctxs):
        try:
            rows.append((nm, float(c.get("dayNtlVlm") or 0)))
        except Exception:
            pass
    rows.sort(key=lambda r: -r[1])
    return [r[0] for r in rows[:n]]


def funding(coin: str) -> pd.DataFrame:
    out, start = [], START_MS
    while True:
        b = post({"type": "fundingHistory", "coin": coin, "startTime": start})
        if not b:
            break
        out.extend(b)
        last = b[-1]["time"]
        if len(b) < 500 or last <= start:
            break
        start = last + 1
        time.sleep(0.06)
    if not out:
        return pd.DataFrame()
    d = pd.DataFrame(out).drop_duplicates("time")
    d["ts"] = pd.to_datetime(d["time"].astype("int64"), unit="ms", utc=True)
    d["rate"] = d["fundingRate"].astype(float)
    d["premium"] = pd.to_numeric(d.get("premium"), errors="coerce")
    d["coin"] = coin
    return d[["coin", "ts", "rate", "premium"]].sort_values("ts")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--coins", type=int, default=25)
    args = ap.parse_args()

    syms = universe(args.coins)
    if not syms:
        print("could not read the Hyperliquid universe", file=sys.stderr)
        return 1
    print(f"{len(syms)} coins by 24h notional volume: {syms[:8]}...")

    frames = []
    for i, c in enumerate(syms, 1):
        d = funding(c)
        if not d.empty:
            frames.append(d)
        print(f"\r  {i}/{len(syms)} {c:<10} rows={sum(len(x) for x in frames):,}",
              end="", flush=True)
    print()

    f = pd.concat(frames, ignore_index=True)
    out = config.DATA / "crypto"
    out.mkdir(exist_ok=True)
    f.to_parquet(out / "hl_funding.parquet", index=False)
    print(f"\nfunding: {len(f):,} hourly prints, {f.coin.nunique()} coins, "
          f"{f.ts.min().date()} to {f.ts.max().date()}")
    ann = f.groupby("coin")["rate"].mean() * 24 * 365
    print(f"\nmean annualized funding by coin (what a short-perp carry would collect):")
    for c, v in ann.sort_values(ascending=False).head(10).items():
        print(f"  {c:<10}{v:>8.1%}")
    print(f"  ...")
    print(f"  {'MEDIAN':<10}{ann.median():>8.1%}")
    print(f"  {'% positive':<10}{(ann > 0).mean():>8.0%}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
