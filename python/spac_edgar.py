#!/usr/bin/env python3
"""SPAC trust-value data from SEC EDGAR (trial 60 candidate, docs/SPAC_TRUST_ARB.md).

    python3 python/spac_edgar.py universe   # IPO prospectuses of blank-check companies, 2010-2025
    python3 python/spac_edgar.py terms      # trust per share, deadline, ticker, unit structure
    python3 python/spac_edgar.py facts      # XBRL trust balances and redeemable shares (companyfacts)
    python3 python/spac_edgar.py filings    # every filing each SPAC made (submissions), for proxies and 8-Ks

Reuses edgar_tenders.fetch (SEC fair access: under 8 requests a second, with a
contact User-Agent) and its gzipped cache. Everything lands in data/spac/
(git-ignored).

WHAT IS ENTRY-TIME. The trust value per share known on day t, E_t, uses only
filings accepted before t. XBRL companyfacts give each fact's balance-sheet
date ("end") and its filing date ("filed"); a fact is usable from the trading
day AFTER its filing date, never from the balance-sheet date (avail_from).
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "python"))

import data_access as da
import edgar_tenders as et

CACHE = ROOT / "data" / "spac"
IPO_FIRST, IPO_LAST = "2010-01-01", "2025-12-31"
PROSPECTUS_FORMS = ("424B4", "424B1")


# ------------------------------------------------------------------ universe

def fts(query: str, forms: str, start: str, end: str) -> list[dict]:
    """Every hit of an EDGAR full-text search (100 a page), cached per query."""
    key = re.sub(r"[^A-Za-z0-9]+", "_", f"{query}_{forms}_{start}_{end}")[:150]
    out = CACHE / "fts" / f"{key}.json"
    if out.exists():
        return json.loads(out.read_text())
    hits, frm = [], 0
    while True:
        u = (f"https://efts.sec.gov/LATEST/search-index?q={quote(query)}&forms={forms}"
             f"&dateRange=custom&startdt={start}&enddt={end}&from={frm}")
        h = json.loads(et.fetch(u))["hits"]
        hits += [{"adsh": x["_source"]["adsh"], "ciks": x["_source"]["ciks"],
                  "names": x["_source"]["display_names"], "sics": x["_source"].get("sics", []),
                  "filed": x["_source"]["file_date"], "form": x["_source"]["form"]} for x in h["hits"]]
        frm += 100
        if frm >= h["total"]["value"] or not h["hits"]:
            break
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(hits))
    return hits


def header(path: str) -> dict:
    """SIC, company name and CIK of the filer, from a submission's SGML header."""
    t = et.filing_text(path)[:6000]
    g = lambda p: (re.search(p, t) or [None, None])[1]
    return {"sic": g(r"STANDARD INDUSTRIAL CLASSIFICATION:[^\n\[]*\[(\d{4})\]"),
            "name_at_filing": (g(r"COMPANY CONFORMED NAME:\s*([^\n]+)") or "").strip(),
            "accepted": g(r"<ACCEPTANCE-DATETIME>(\d{14})")}


def universe() -> int:
    """IPO prospectuses (424B4, 424B1) of SIC 6770 filers that describe a trust account.

    Full-text search finds every prospectus mentioning a trust account and a
    business combination; the SIC on the submission's own header (the code at
    filing time, not today's, which changes after a merger) keeps blank checks.
    A SPAC can file more than one final prospectus (a 424B4 and a later 424B3
    sticker); the first is the IPO."""
    import pandas as pd
    md = da.guard("spac_edgar")
    rows = []
    for y in range(int(IPO_FIRST[:4]), int(IPO_LAST[:4]) + 1):
        hits = fts('"trust account" "business combination"', ",".join(PROSPECTUS_FORMS),
                   f"{y}-01-01", f"{y}-12-31")
        rows += hits
        print(f"  {y}: {len(hits)} prospectus documents", flush=True)
    f = pd.DataFrame(rows)
    f = f[f["filed"] <= min(md, IPO_LAST)].drop_duplicates("adsh")
    f["cik"] = f["ciks"].str[0].str.lstrip("0")
    f["path"] = "edgar/data/" + f["cik"] + "/" + f["adsh"] + ".txt"
    f["fts_sic"] = f["sics"].str[0]
    hdr = []
    for i, p in enumerate(f["path"], 1):
        hdr.append(header(p))
        if i % 200 == 0:
            print(f"  headers {i}/{len(f)}", flush=True)
    f = pd.concat([f.reset_index(drop=True), pd.DataFrame(hdr)], axis=1)
    spac = f[f["sic"] == "6770"].sort_values("filed").drop_duplicates("cik")
    spac = spac[["cik", "name_at_filing", "filed", "accepted", "form", "adsh", "path"]].rename(
        columns={"filed": "prospectus_filed"})
    spac.to_csv(CACHE / "universe.csv", index=False)
    print(f"\n{len(f):,} prospectuses; {len(spac):,} SIC 6770 filers (one IPO each)")
    print(spac.groupby(spac["prospectus_filed"].str[:4]).size().to_string())
    return 0


# ------------------------------------------------------------------ prospectus terms

_TRUST_PU = re.compile(r"\(\s*\$\s?(\d{1,2}\.\d{2,3})\s+per\s+(?:unit|share|public\s+share)[^)]{0,80}\)"
                       r"[^.$]{0,120}?(?:will\s+be|is|are|has\s+been|shall\s+be)\s+(?:deposited|placed|held)"
                       r"[^.$]{0,80}?trust\s+account", re.I)
_TRUST_PU2 = re.compile(r"\$\s?(\d{1,2}\.\d{2,3})\s+per\s+(?:unit|share|public\s+share)[^.$]{0,160}?trust\s+account"
                        r"|trust\s+account[^.$]{0,160}?\$\s?(\d{1,2}\.\d{2,3})\s+per\s+(?:unit|public\s+share|share)", re.I)
_MONTHS = re.compile(r"within\s+(\d{1,2}|twelve|fifteen|eighteen|twenty[\s-]+one|twenty[\s-]+four|twenty[\s-]+one)\s+months"
                     r"\s+(?:\([^)]{0,200}\)\s+)?(?:from|after|of)\s+(?:the\s+)?(?:closing|consummation|completion|date)", re.I)
_WORDS = {"twelve": 12, "fifteen": 15, "eighteen": 18, "twenty one": 21, "twenty-one": 21, "twenty four": 24, "twenty-four": 24}
_UNIT_SYM = re.compile(r"units?\b[^.]{0,200}?under\s+the\s+(?:ticker\s+)?symbol\s+[\"“]?\s*([A-Z]{2,6})(?:\s*\.?\s*U)?[\"”.,\s]", re.S)
_EXCH = re.compile(r"(NYSE\s+American|NYSE\s+MKT|NYSE\s+Amex|New\s+York\s+Stock\s+Exchange|NYSE|Nasdaq|NASDAQ|American\s+Stock\s+Exchange)")
_UNIT_HAS = re.compile(r"each\s+unit\s+(?:consists|will\s+consist|consisting)\s+of\s+([^.]{0,300}(?:\.\d+[^.]{0,200})?)", re.I)


def _months(x: str) -> int:
    x = re.sub(r"\s+", " ", x.lower())
    return int(x) if x.isdigit() else _WORDS.get(x, _WORDS.get(x.replace(" ", "-")))


def parse_terms(path: str) -> dict:
    """Entry-time terms from the IPO prospectus: trust per share at the IPO, the
    charter deadline in months, the unit symbol and what a unit holds."""
    text = "\n".join(b for _, b in et.documents(path))
    flat = re.sub(r"\s+", " ", text).replace("U.S.", "US")       # "a U.S.-based trust account" has periods
    m = _TRUST_PU.search(flat[:200000]) or _TRUST_PU2.search(flat[:200000])
    tpu = float(next(g for g in m.groups() if g)) if m else None
    mm = _MONTHS.search(flat)
    us = _UNIT_SYM.search(flat[:300000])
    sym = us.group(1) if us else None
    if sym and sym.endswith("U") and len(sym) >= 4:
        sym = sym[:-1]                                   # "ABCDU" -> common "ABCD"
    ex = _EXCH.search(flat[:20000])
    uh = _UNIT_HAS.search(flat)
    comp = uh.group(1).lower() if uh else ""
    return {"trust_per_share_ipo": tpu if tpu and 9.0 <= tpu <= 11.5 else None,
            "deadline_months": _months(mm.group(1)) if mm else None,
            "common_symbol": sym,
            "exchange": (ex.group(1) if ex else None),
            "unit_has_warrant": "warrant" in comp, "unit_has_right": bool(re.search(r"\bright", comp)),
            "unit_text": comp[:160]}


def _terms_one(path):
    try:
        return {"path": path, **parse_terms(path), "error": ""}
    except Exception as e:                                # recorded, never silently dropped
        return {"path": path, "error": f"{type(e).__name__}: {e}"}


def terms() -> int:
    import pandas as pd
    from concurrent.futures import ProcessPoolExecutor
    u = pd.read_csv(CACHE / "universe.csv", dtype=str)
    with ProcessPoolExecutor(8) as ex:
        rows = list(ex.map(_terms_one, list(u["path"]), chunksize=8))
    t = u.merge(pd.DataFrame(rows), on="path")
    t.to_csv(CACHE / "terms.csv", index=False)
    print(f"{len(t)} prospectuses; trust/share {t['trust_per_share_ipo'].notna().mean():.1%}, "
          f"deadline {t['deadline_months'].notna().mean():.1%}, symbol {t['common_symbol'].notna().mean():.1%}; "
          f"{(t['error'] != '').sum()} errors")
    print(t["trust_per_share_ipo"].value_counts().head(8).to_string())
    print(t["deadline_months"].value_counts().head(8).to_string())
    return 0


# ------------------------------------------------------------------ XBRL trust facts

TRUST_TAGS = ("AssetsHeldInTrustNoncurrent", "AssetsHeldInTrust", "AssetsHeldInTrustCurrent")
SHARE_TAG, PRICE_TAG = "TemporaryEquitySharesOutstanding", "TemporaryEquityRedemptionPricePerShare"


def companyfacts(cik: str) -> dict:
    out = CACHE / "companyfacts" / f"CIK{int(cik):010d}.json"
    if not out.exists():
        out.parent.mkdir(parents=True, exist_ok=True)
        try:
            raw = et.fetch(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{int(cik):010d}.json")
        except Exception as e:                     # 404: the filer never filed XBRL
            if "404" not in str(e):
                raise
            raw = b"{}"
        out.write_bytes(raw)
    return json.loads(out.read_text() or "{}")


def trust_facts(cf: dict) -> list[dict]:
    """One row per filing: the trust balance, redeemable shares and stated
    redemption price for the filing's own balance-sheet date (the latest "end"
    it reports), with the date it was filed."""
    g = cf.get("facts", {}).get("us-gaap", {})
    by = {}
    for tag in (*TRUST_TAGS, SHARE_TAG, PRICE_TAG):
        for unit, vals in g.get(tag, {}).get("units", {}).items():
            for v in vals:
                if v.get("form", "").startswith(("10-Q", "10-K")) and "end" in v and "start" not in v:
                    r = by.setdefault(v["accn"], {"accn": v["accn"], "filed": v["filed"], "form": v["form"], "end": v["end"]})
                    if v["end"] > r["end"]:
                        r.update(end=v["end"])
                        for k in list(r):
                            if k not in ("accn", "filed", "form", "end"):
                                r.pop(k)
                    if v["end"] == r["end"] and not (tag in TRUST_TAGS and not v["val"]):
                        r.setdefault(tag, v["val"])
    rows = []
    for r in by.values():
        r["trust"] = next((r[t] for t in TRUST_TAGS if r.get(t)), None)
        rows.append({k: r.get(k) for k in ("accn", "filed", "form", "end", "trust", SHARE_TAG, PRICE_TAG)})
    return rows


def facts() -> int:
    import pandas as pd
    u = pd.read_csv(CACHE / "universe.csv", dtype=str)
    rows = []
    for i, cik in enumerate(u["cik"], 1):
        rows += [{"cik": cik, **r} for r in trust_facts(companyfacts(cik))]
        if i % 200 == 0:
            print(f"  {i}/{len(u)}", flush=True)
    f = pd.DataFrame(rows).sort_values(["cik", "filed"])
    f.to_csv(CACHE / "trust_facts.csv", index=False)
    has = f.dropna(subset=["trust"]).groupby("cik").size()
    print(f"{len(f):,} filings with trust facts; {len(has)} of {len(u)} SPACs have an XBRL trust balance")
    return 0


# ------------------------------------------------------------------ filing lists

def submissions(cik: str) -> list[dict]:
    """Every filing a CIK has made (data.sec.gov submissions, cached), with the
    8-K item numbers and the event (report) date: item 5.07 is a shareholder
    vote, 2.01 a completed acquisition."""
    out = CACHE / "submissions" / f"CIK{int(cik):010d}.json"
    if not out.exists():
        out.parent.mkdir(parents=True, exist_ok=True)
        j = json.loads(et.fetch(f"https://data.sec.gov/submissions/CIK{int(cik):010d}.json"))
        pages = [j["filings"]["recent"]]
        for f in j["filings"].get("files", []):
            pages.append(json.loads(et.fetch("https://data.sec.gov/submissions/" + f["name"])))
        out.write_text(json.dumps(pages))
    rows = []
    for pg in json.loads(out.read_text()):
        n = len(pg["form"])
        get = lambda k: pg.get(k, [""] * n)
        for i in range(n):
            rows.append({"form": pg["form"][i], "filed": pg["filingDate"][i], "report": get("reportDate")[i],
                         "items": get("items")[i], "accepted": get("acceptanceDateTime")[i],
                         "acc": pg["accessionNumber"][i], "doc": get("primaryDocument")[i]})
    return rows


def filings() -> int:
    import pandas as pd
    md = da.guard("spac_edgar")
    u = pd.read_csv(CACHE / "universe.csv", dtype=str)
    rows = []
    for i, cik in enumerate(u["cik"], 1):
        rows += [{"cik": cik, **r} for r in submissions(cik) if r["filed"] <= md]
        if i % 200 == 0:
            print(f"  {i}/{len(u)}", flush=True)
    f = pd.DataFrame(rows)
    f.to_csv(CACHE / "filings.csv", index=False)
    v = f[f["form"].str.startswith("8-K") & f["items"].str.contains("5.07", na=False)]
    print(f"{len(f):,} filings; {len(v):,} vote 8-Ks (item 5.07) from {v['cik'].nunique()} SPACs; "
          f"{f[f['items'].str.contains('2.01', na=False)]['cik'].nunique()} with a completed-acquisition 8-K")
    return 0


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "universe"
    sys.exit({"universe": universe, "terms": terms, "facts": facts, "filings": filings}[cmd]())
