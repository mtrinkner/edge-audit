#!/usr/bin/env python3
"""Download SEC XBRL company facts: as-filed fundamentals with real filing dates.

    python3 python/ingest_fundamentals.py --all
    python3 python/ingest_fundamentals.py --symbols AAPL MSFT

WHY THIS SOURCE. The price provider returns five quarters of financials, which
cannot support a 2014-2026 study. SEC EDGAR returns every figure a company ever
reported in XBRL, each stamped with the date it was filed. That `filed` field is
the difference between a point-in-time study and a fiction: a quarter ending
2026-06-27 did not become public until 2026-07-31, and a backtest that uses it on
June 28th is reading a filing that did not exist.

Only the concepts needed for documented anomalies are kept, rather than all ~500
available, because storing everything invites fishing through it later and the
registry would never know.

Rate limiting follows the SEC's published guidance: a descriptive User-Agent and
under ten requests a second. The alternative `frames` endpoint is far cheaper per
request but omits `filed` entirely, which makes it useless here.
"""

from __future__ import annotations

import argparse
import gzip
import json
import sys
import time
import urllib.error
import urllib.request

import pandas as pd

import config
import db

USER_AGENT = "Mason Trinkner edge-audit research mtrinkner@wisc.edu"
RATE_SLEEP = 0.12

# Concept -> namespace. Several have more than one spelling across filers, so
# alternates are tried in order and the first that appears is used.
CONCEPTS = {
    "NetIncomeLoss": ["us-gaap"],
    "StockholdersEquity": ["us-gaap"],
    "Assets": ["us-gaap"],
    "GrossProfit": ["us-gaap"],
    "NetCashProvidedByUsedInOperatingActivities": ["us-gaap"],
    "CommonStockSharesOutstanding": ["us-gaap"],
    "WeightedAverageNumberOfDilutedSharesOutstanding": ["us-gaap"],
    "EntityCommonStockSharesOutstanding": ["dei"],
}


def fetch(url: str, retries: int = 3) -> dict | None:
    for attempt in range(retries):
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": USER_AGENT, "Accept-Encoding": "gzip"})
            with urllib.request.urlopen(req, timeout=90) as f:
                raw = f.read()
                if f.headers.get("Content-Encoding") == "gzip":
                    raw = gzip.decompress(raw)
                return json.loads(raw)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            time.sleep(1.5 * (attempt + 1))
        except Exception:
            time.sleep(1.5 * (attempt + 1))
    return None


def ticker_cik_map() -> dict[str, str]:
    d = fetch("https://www.sec.gov/files/company_tickers.json")
    if not d:
        raise SystemExit("could not fetch the SEC ticker map")
    return {v["ticker"].replace(".", "-"): f"{int(v['cik_str']):010d}"
            for v in d.values()}


def extract(cf: dict, symbol: str, run_id: int) -> list[tuple]:
    rows = []
    facts = cf.get("facts", {})
    for concept, namespaces in CONCEPTS.items():
        for ns in namespaces:
            block = facts.get(ns, {}).get(concept)
            if not block:
                continue
            for unit, recs in block.get("units", {}).items():
                if unit not in ("USD", "shares", "USD/shares"):
                    continue
                for r in recs:
                    if "filed" not in r or "end" not in r or r.get("val") is None:
                        continue
                    if r.get("form") not in ("10-K", "10-Q", "10-K/A", "10-Q/A", "20-F"):
                        continue
                    rows.append((symbol, concept, r.get("start"), r["end"],
                                 r["filed"], float(r["val"]), r.get("form"),
                                 r.get("fy"), r.get("fp"), run_id))
            break
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--symbols", nargs="*")
    ap.add_argument("--all", action="store_true")
    args = ap.parse_args()
    if not args.symbols and not args.all:
        ap.error("pass --all or --symbols")

    with db.connect() as con:
        universe = [r["symbol"] for r in con.execute(
            "SELECT symbol FROM symbols WHERE kind='stock' ORDER BY symbol")]
    symbols = args.symbols or universe

    print("fetching the SEC ticker to CIK map")
    cmap = ticker_cik_map()
    matched = {s: cmap[s] for s in symbols if s in cmap}
    print(f"  {len(matched)}/{len(symbols)} symbols matched to a CIK")

    with db.transaction() as con:
        cur = con.execute(
            """INSERT INTO ingest_runs (source, interval, requested_start,
               requested_end, symbols_requested, status)
               VALUES ('sec-xbrl','filing','2010-01-01',?,?,'running')""",
            (config.END_DATE.isoformat(), len(matched)))
        run_id = cur.lastrowid

    all_rows, failed = [], []
    for i, (sym, cik) in enumerate(matched.items(), 1):
        cf = fetch(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json")
        if not cf:
            failed.append(sym)
        else:
            all_rows.extend(extract(cf, sym, run_id))
        if i % 25 == 0:
            print(f"\r  {i}/{len(matched)}  rows={len(all_rows):,}  "
                  f"failed={len(failed)}", end="", flush=True)
        time.sleep(RATE_SLEEP)
    print(f"\r  {len(matched)}/{len(matched)}  rows={len(all_rows):,}  "
          f"failed={len(failed)}")

    with db.transaction() as con:
        for i in range(0, len(all_rows), 20000):
            con.executemany(
                """INSERT INTO fundamentals (symbol, concept, period_start,
                   period_end, filed, val, form, fy, fp, run_id)
                   VALUES (?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(symbol, concept, period_end, filed)
                   DO UPDATE SET val=excluded.val, form=excluded.form""",
                all_rows[i:i + 20000])
        con.execute(
            """UPDATE ingest_runs SET status=?, symbols_loaded=?, rows_loaded=?,
               finished_at=datetime('now'), notes=? WHERE run_id=?""",
            ("ok" if not failed else "partial", len(matched) - len(failed),
             len(all_rows), f"{len(failed)} symbols with no XBRL", run_id))
        n = con.execute("SELECT COUNT(*) c FROM fundamentals").fetchone()["c"]
        rng = con.execute(
            "SELECT MIN(filed) a, MAX(filed) b FROM fundamentals").fetchone()
    print(f"\nfundamentals: {n:,} facts, filed {rng['a']} to {rng['b']}")
    if failed:
        print(f"no XBRL for {len(failed)}: {failed[:8]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
