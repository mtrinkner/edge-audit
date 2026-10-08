#!/usr/bin/env python3
"""OKX perpetual funding rates and prices for the cash-and-carry test.

    python3 crypto/ingest_okx.py --top 40 --years 3

WHY THIS MARKET. Everything else in this project tried to predict a price. This
does not. A perpetual swap has no expiry date, so the exchange tethers it to spot
with a funding payment every eight hours: when the perp trades above spot, longs
pay shorts, and when it trades below, shorts pay longs. Holding a short perp
against a long spot position is delta-neutral, and the funding arrives as a
contractual cash flow rather than as a forecast that might be wrong.

WHAT IT ACTUALLY IS. A risk premium, not an edge. Levered long demand in crypto
persistently exceeds levered short demand, and the funding rate is what longs pay
for that leverage. Collecting it means supplying leverage to a crowd that wants
it, which is a real service with real risk attached.

WHAT THE BACKTEST CANNOT CAPTURE, stated here rather than buried: exchange
counterparty risk. The position requires collateral sitting on a venue, and the
history of this industry includes venues that stopped honouring withdrawals
overnight. No funding rate compensates for that in a spreadsheet, and the
strategy's worst realistic outcome is not a drawdown but a total loss that no
price series would ever show.

Binance and Bybit return 451 and 403 from this location, so OKX is the venue.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

DATA = Path(__file__).resolve().parent / "data"


UA = {"User-Agent": "Mozilla/5.0 (edge-audit research)"}
BASE = "https://www.okx.com/api/v5"


def get(path: str, retries: int = 3):
    for a in range(retries):
        try:
            req = urllib.request.Request(BASE + path, headers=UA)
            with urllib.request.urlopen(req, timeout=60) as f:
                return json.loads(f.read().decode()).get("data", [])
        except Exception:
            time.sleep(1.0 * (a + 1))
    return []


def top_symbols(n: int) -> list[str]:
    tick = get("/market/tickers?instType=SWAP")
    # volCcy24h is in coins, not dollars. Ranking on it alone puts sub-penny
    # meme coins at the top and leaves BTC and ETH out entirely, which is what
    # the first run of this did. Multiply by the last price to rank on notional.
    rows = [(t["instId"], float(t.get("volCcy24h") or 0) * float(t.get("last") or 0))
            for t in tick if t["instId"].endswith("-USDT-SWAP")]
    rows.sort(key=lambda x: -x[1])
    return [r[0] for r in rows[:n]]


def funding_history(inst: str, years: float) -> pd.DataFrame:
    cutoff = (datetime.now(timezone.utc).timestamp() - years * 365 * 86400) * 1000
    out, after = [], None
    while True:
        p = f"/public/funding-rate-history?instId={inst}&limit=100"
        if after:
            p += f"&after={after}"
        batch = get(p)
        if not batch:
            break
        out.extend(batch)
        after = batch[-1]["fundingTime"]
        if float(after) < cutoff or len(batch) < 100:
            break
        time.sleep(0.12)
    if not out:
        return pd.DataFrame()
    d = pd.DataFrame(out)
    d["ts"] = pd.to_datetime(d["fundingTime"].astype("int64"), unit="ms", utc=True)
    d["rate"] = d["fundingRate"].astype(float)
    d["inst"] = inst
    return d[["inst", "ts", "rate"]][d["ts"] >= pd.Timestamp(cutoff, unit="ms", tz="UTC")]


def candles(inst: str, years: float) -> pd.DataFrame:
    cutoff = (datetime.now(timezone.utc).timestamp() - years * 365 * 86400) * 1000
    out, after = [], None
    while True:
        p = f"/market/history-candles?instId={inst}&bar=1D&limit=100"
        if after:
            p += f"&after={after}"
        batch = get(p)
        if not batch:
            break
        out.extend(batch)
        after = batch[-1][0]
        if float(after) < cutoff or len(batch) < 100:
            break
        time.sleep(0.12)
    if not out:
        return pd.DataFrame()
    d = pd.DataFrame(out).iloc[:, :6]
    d.columns = ["ms", "open", "high", "low", "close", "vol"]
    d["ts"] = pd.to_datetime(d["ms"].astype("int64"), unit="ms", utc=True)
    for c in ("open", "high", "low", "close", "vol"):
        d[c] = d[c].astype(float)
    d["inst"] = inst
    return d[["inst", "ts", "open", "high", "low", "close", "vol"]]


def basis(years: float) -> int:
    """Daily perp and spot closes on OKX for the coins in the Hyperliquid funding
    file. A delta-neutral carry earns funding but also marks the perp-spot spread
    to market every day, and that spread is where the trade's actual risk lives."""
    coins = sorted(pd.read_parquet(DATA / "hl_funding.parquet")
                   ["coin"].unique())
    frames = []
    for i, c in enumerate(coins, 1):
        p = candles(f"{c}-USDT-SWAP", years)
        s = candles(f"{c}-USDT", years)
        if p.empty or s.empty:
            print(f"\n  {c}: not listed on OKX as both perp and spot, skipped")
            continue
        m = p[["ts", "close"]].merge(s[["ts", "close"]], on="ts", suffixes=("_perp", "_spot"))
        m["coin"] = c
        frames.append(m)
        print(f"\r  {i}/{len(coins)} {c:<8}", end="", flush=True)
    print()
    b = pd.concat(frames, ignore_index=True).sort_values(["coin", "ts"])
    b.to_parquet(DATA / "okx_basis.parquet", index=False)
    print(f"basis: {len(b):,} coin-days, {b.coin.nunique()} coins, "
          f"{b.ts.min().date()} to {b.ts.max().date()}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--top", type=int, default=40)
    ap.add_argument("--years", type=float, default=3.0)
    ap.add_argument("--basis", action="store_true",
                    help="only pull perp and spot candles for the Hyperliquid coins, "
                         "to measure the basis the funding series leaves out")
    args = ap.parse_args()

    if args.basis:
        return basis(args.years)

    syms = top_symbols(args.top)
    print(f"{len(syms)} USDT perps by 24h volume; pulling {args.years}y of funding")
    F, P, S = [], [], []
    for i, inst in enumerate(syms, 1):
        f = funding_history(inst, args.years)
        if not f.empty:
            F.append(f)
        P.append(candles(inst, args.years))
        S.append(candles(inst.replace("-SWAP", ""), args.years))
        print(f"\r  {i}/{len(syms)} {inst:<18} funding={sum(len(x) for x in F):,}",
              end="", flush=True)
    print()

    fund = pd.concat([x for x in F if not x.empty], ignore_index=True)
    perp = pd.concat([x for x in P if not x.empty], ignore_index=True)
    spot = pd.concat([x for x in S if not x.empty], ignore_index=True)
    out = DATA
    out.mkdir(exist_ok=True)
    fund.to_parquet(out / "okx_funding.parquet", index=False)
    perp.to_parquet(out / "okx_perp.parquet", index=False)
    spot.to_parquet(out / "okx_spot.parquet", index=False)
    print(f"\nfunding {len(fund):,} prints, {fund.inst.nunique()} instruments, "
          f"{fund.ts.min().date()} to {fund.ts.max().date()}")
    print(f"perp    {len(perp):,} daily candles")
    print(f"spot    {len(spot):,} daily candles, {spot.inst.nunique()} instruments")
    return 0


if __name__ == "__main__":
    sys.exit(main())
