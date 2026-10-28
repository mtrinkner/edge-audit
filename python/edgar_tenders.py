#!/usr/bin/env python3
"""Odd-lot tender offer arbitrage: the deal database (Phase A).

    python3 python/edgar_tenders.py index      # SC TO-I / SC TO-I/A filings, 2010Q1-2026Q3
    python3 python/edgar_tenders.py index --family "SC TO-T"   # third-party offers (merger_arb_tenders.py)
    python3 python/edgar_tenders.py link       # filer CIK -> CRSP permno (WRDS)
    python3 python/edgar_tenders.py download   # cache every candidate submission
    python3 python/edgar_tenders.py parse      # terms and outcome of each offer
    python3 python/edgar_tenders.py eightk     # 8-Ks filed while an offer was open (then parse again)
    python3 python/edgar_tenders.py prices     # CRSP daily prices (WRDS) and the match report

Issuer tender offers (SC TO-I) often buy holders of fewer than 100 shares in
full, without proration (the "odd-lot priority" clause). This module builds the
list of such offers from SEC EDGAR, the terms and outcome of each, and the
CRSP prices around them. Research only.

SEC FAIR ACCESS. At most 10 requests a second (this stays under 8), with a
User-Agent naming a person and contact address. Every response is cached under
data/edgar/ (git-ignored) and never fetched twice. The quarterly form indexes
are about 40MB each and almost entirely other forms, so only their SC TO-I rows
are kept.

LOCKBOX. Nothing filed after the day before the seal is used.
"""

from __future__ import annotations

import html
import re
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "python"))

import data_access as da

USER_AGENT = "Mason Trinkner edge-audit research mtrinkner@wisc.edu"
MIN_INTERVAL = 0.125                     # seconds between requests: 8 a second
CACHE = ROOT / "data" / "edgar"
# Form families: the forms each study keeps from the quarterly indexes, the quarters
# read, and where the kept rows are cached. SC TO-I is trial 58 and keeps its
# original cache paths. SC TO-T (trial 60) also keeps the target's SC 14D9, any
# SC 13E3 (going private) and SC TO-C (pre-commencement communications), because
# they are needed to read those offers and cost nothing extra to keep.
FAMILIES = {
    "SC TO-I": {"forms": ("SC TO-I", "SC TO-I/A"), "first": (2010, 1), "last": (2026, 3),
                "index": "index", "filings": "filings.csv"},
    "SC TO-T": {"forms": ("SC TO-T", "SC TO-T/A", "SC 14D9", "SC 14D9/A", "SC 13E3", "SC 13E3/A", "SC TO-C"),
                "first": (2001, 1), "last": (2026, 3), "index": "index_tot", "filings": "filings_tot.csv"},
}
FORMS = FAMILIES["SC TO-I"]["forms"]
FIRST, LAST = FAMILIES["SC TO-I"]["first"], FAMILIES["SC TO-I"]["last"]

_last_request = 0.0


def fetch(url: str) -> bytes:
    """GET a sec.gov URL, politely. Callers cache; this never does."""
    global _last_request
    wait = MIN_INTERVAL - (time.monotonic() - _last_request)
    if wait > 0:
        time.sleep(wait)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT,
                                               "Accept-Encoding": "identity"})
    for attempt in range(4):
        try:
            _last_request = time.monotonic()
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.read()
        except Exception as e:                       # 429/5xx/timeouts: back off
            if attempt == 3:
                raise
            print(f"  retry {attempt + 1} after {e}", flush=True)
            time.sleep(2 ** attempt * 2)
    raise RuntimeError("unreachable")


def quarters(first=FIRST, last=LAST):
    y, q = first
    while (y, q) <= last:
        yield y, q
        y, q = (y, q + 1) if q < 4 else (y + 1, 1)


def form_index_rows(text: str, forms: tuple) -> list[list[str]]:
    """The rows of a form.idx whose form type is one of `forms`."""
    rows = []
    for line in text.splitlines():
        if not line.startswith(forms):
            continue
        form = line[:12].strip()
        if form not in forms:
            continue
        # Fixed-width columns; the company name can contain spaces, the rest cannot.
        parts = line[12:].rstrip().rsplit(None, 3)
        if len(parts) != 4:
            continue
        company, cik, date, fname = parts
        rows.append([form, company.strip(), cik, date, fname])
    return rows


def index_rows(y: int, q: int, family: str = "SC TO-I") -> list[list[str]]:
    """One family's rows of one quarterly form index: form, company, cik, date, path.

    A filing appears once per party: an SC TO-T is listed under the bidder and
    under the subject company, with the same accession number in both paths."""
    fam = FAMILIES[family]
    path = CACHE / fam["index"] / f"{y}Q{q}.tsv"
    if not path.exists():
        import gzip
        raw = gzip.decompress(fetch(f"https://www.sec.gov/Archives/edgar/full-index/{y}/QTR{q}/form.gz"))
        rows = form_index_rows(raw.decode("latin-1"), fam["forms"])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join("\t".join(r) for r in rows) + "\n")
    return [l.split("\t") for l in path.read_text().splitlines() if l]


def build_index(family: str = "SC TO-I") -> int:
    import pandas as pd
    md = da.guard("edgar_tenders")
    fam = FAMILIES[family]
    rows = []
    for y, q in quarters(fam["first"], fam["last"]):
        r = index_rows(y, q, family)
        rows += r
        print(f"  {y}Q{q}: {len(r)} {family} family rows", flush=True)
    f = pd.DataFrame(rows, columns=["form", "company", "cik", "date", "path"])
    f = f[f["date"] <= md].drop_duplicates("path").sort_values(["cik", "date"])
    if family != "SC TO-I":                  # one row per party; merger_arb_tenders dedupes by accession
        f["accession"] = f["path"].str.rsplit("/", n=1).str[-1].str.replace(".txt", "", regex=False)
        f.to_csv(CACHE / fam["filings"], index=False)
        print(f"\n{len(f):,} rows, {f['accession'].nunique():,} filings through {md}:")
        print(f.drop_duplicates("accession")["form"].value_counts().to_string())
        return 0
    f.to_csv(CACHE / fam["filings"], index=False)
    print(f"\n{len(f):,} filings through {md}: "
          f"{(f['form'] == 'SC TO-I').sum():,} originals, "
          f"{(f['form'] == 'SC TO-I/A').sum():,} amendments, {f['cik'].nunique():,} filers")
    return 0


def link() -> int:
    """Filer CIK -> CRSP permno on each original filing's date.

    Compustat company.cik -> gvkey, then the CCM link (primary LU/LC links valid
    on the date). Only links to a CRSP ordinary common share survive. Most SC
    TO-I filers are closed-end and interval funds with no CCM link; dropping
    them here is what keeps the document downloads proportionate."""
    import pandas as pd
    import wrds
    from crsp_pull import username
    f = pd.read_csv(CACHE / "filings.csv", dtype=str)
    orig = f[f["form"] == "SC TO-I"][["cik", "date", "path", "company"]].copy()
    orig["cik10"] = orig["cik"].str.zfill(10)
    db = wrds.Connection(wrds_username=username())
    ciks = ",".join(f"'{c}'" for c in sorted(set(orig["cik10"])))
    comp = db.raw_sql(f"SELECT gvkey, cik FROM comp.company WHERE cik IN ({ciks})")
    lk = db.raw_sql("SELECT gvkey, lpermno AS permno, linkdt, linkenddt FROM crsp.ccmxpf_lnkhist "
                    "WHERE linktype IN ('LU','LC') AND linkprim IN ('P','C') AND lpermno IS NOT NULL")
    permnos = ",".join(str(int(p)) for p in lk["permno"].dropna().unique())
    names = db.raw_sql("SELECT permno, secinfostartdt, secinfoenddt, ticker, sharetype, "
                       "securitytype, securitysubtype FROM crsp.stksecurityinfohist "
                       f"WHERE permno IN ({permnos})")
    db.close()
    m = orig.merge(comp, left_on="cik10", right_on="cik", how="left", suffixes=("", "_c"))
    m = m.merge(lk, on="gvkey", how="left")
    d = m["date"]
    ok = (m["linkdt"].astype(str) <= d) & ((m["linkenddt"].isna()) | (m["linkenddt"].astype(str) >= d))
    m.loc[~ok, "permno"] = pd.NA        # a link outside the filing date is no link
    # Keep a permno only if CRSP calls it an ordinary common share on that date.
    names["s"], names["e"] = names["secinfostartdt"].astype(str), names["secinfoenddt"].astype(str)
    com = names[(names["sharetype"] == "NS") & (names["securitytype"] == "EQTY")
                & (names["securitysubtype"] == "COM")]
    j = m.merge(com[["permno", "s", "e", "ticker"]], on="permno", how="left")
    j = j[j["s"].isna() | ((j["s"] <= j["date"]) & (j["e"] >= j["date"]))]
    j["common"] = j["s"].notna()
    j = (j.sort_values(["path", "common"], ascending=[True, False])
           .drop_duplicates("path")[["path", "cik", "company", "date", "gvkey", "permno",
                                     "ticker", "common"]])
    j.to_csv(CACHE / "links.csv", index=False)
    print(f"{len(j):,} original SC TO-I filings; {j['gvkey'].notna().sum():,} have a Compustat "
          f"gvkey; {j['permno'].notna().sum():,} a CCM permno; {j['common'].sum():,} are CRSP "
          f"common stock ({j[j['common']]['cik'].nunique():,} issuers)")
    return 0


# Interval funds, BDCs and other pooled vehicles file most SC TO-Is. They never
# issue a common stock with an odd-lot clause worth buying, so a filer with no
# Compustat record whose name looks like a fund is not downloaded.
FUNDLIKE = (r"FUND|PORTFOLIO|MULTI-STRATEGY|MULTI STRATEGY|ABSOLUTE RETURN|HEDGE|MASTER|"
            r"INTERVAL|ALTERNATIVE|INCOME|CREDIT|LENDING|PRIVATE EQUITY|INFRASTRUCTURE|"
            r"\bL\.?P\.?\b|\bLLC\b|INVESTMENT CORP|BUSINESS DEVELOPMENT|BDC|REIT|REAL ESTATE|"
            r"PROPERTIES|TRUST")


def candidates():
    """Original filings worth reading, and every amendment by the same filers."""
    import pandas as pd
    j = pd.read_csv(CACHE / "links.csv", dtype=str)
    fund = j["company"].str.contains(FUNDLIKE, case=False, regex=True)
    orig = j[j["gvkey"].notna() | ~fund]
    f = pd.read_csv(CACHE / "filings.csv", dtype=str)
    amend = f[(f["form"] == "SC TO-I/A") & f["cik"].isin(set(orig["cik"]))]
    return orig, amend


def filing_text(path: str) -> str:
    """The full submission (every document and exhibit), cached gzipped."""
    import gzip
    out = CACHE / "filings" / (Path(path).name + ".gz")
    if not out.exists():
        raw = fetch("https://www.sec.gov/Archives/" + path)
        out.parent.mkdir(parents=True, exist_ok=True)
        tmp = out.with_suffix(".part")
        tmp.write_bytes(gzip.compress(raw))
        tmp.rename(out)
    return gzip.decompress(out.read_bytes()).decode("latin-1")


def download() -> int:
    orig, amend = candidates()
    paths = list(orig["path"]) + list(amend["path"])
    todo = [p for p in paths if not (CACHE / "filings" / (Path(p).name + ".gz")).exists()]
    print(f"{len(orig):,} offers + {len(amend):,} amendments; {len(todo):,} not yet cached", flush=True)
    for i, p in enumerate(todo, 1):
        filing_text(p)
        if i % 250 == 0:
            print(f"  {i:,}/{len(todo):,}", flush=True)
    return 0


def in_scope(d):
    """Cash offers for a common or ordinary share: the offers this study can trade."""
    return d[(d["security"] == "common") & ~d["exchange"].astype(bool) & d["offer_type"].isin(["dutch", "fixed"])]


def submissions(cik: str) -> list[dict]:
    """Every filing a company has made, from data.sec.gov (cached)."""
    import json
    out = CACHE / "submissions" / f"CIK{int(cik):010d}.json"
    if not out.exists():
        out.parent.mkdir(parents=True, exist_ok=True)
        j = json.loads(fetch(f"https://data.sec.gov/submissions/CIK{int(cik):010d}.json"))
        pages = [j["filings"]["recent"]]
        for f in j["filings"].get("files", []):
            pages.append(json.loads(fetch("https://data.sec.gov/submissions/" + f["name"])))
        out.write_text(json.dumps(pages))
    rows = []
    for pg in json.loads(out.read_text()):
        rows += [{"form": f, "date": d, "acc": a} for f, d, a in zip(pg["form"], pg["filingDate"], pg["accessionNumber"])]
    return rows


def eightk() -> int:
    """8-Ks filed while each offer was open, or soon after it.

    Many issuers announce the final results (and some, extensions or a
    termination) only in an 8-K press release that the SC TO-I/A incorporates
    by reference. Those are read too."""
    import pandas as pd
    md = da.guard("edgar_tenders")
    d = in_scope(pd.read_csv(CACHE / "deals.csv", dtype={"cik": str}))
    rows = []
    for cik, g in d.groupby("cik"):
        subs = [x for x in submissions(cik) if x["form"] in ("8-K", "8-K/A") and x["date"] <= md]
        for x in subs:
            if any(o <= x["date"] <= _add_days(o, 150) for o in g["date"]):
                rows.append({"cik": cik, "date": x["date"],
                             "path": f"edgar/data/{int(cik)}/{x['acc']}.txt"})
    k = pd.DataFrame(rows).drop_duplicates("path")
    k.to_csv(CACHE / "eightk.csv", index=False)
    print(f"{d['cik'].nunique()} issuers, {len(k):,} 8-Ks inside an offer window", flush=True)
    for i, p in enumerate(k["path"], 1):
        filing_text(p)
        if i % 250 == 0:
            print(f"  {i:,}/{len(k):,}", flush=True)
    return 0


# ------------------------------------------------------------------ parsing
#
# Regular expressions over the cleaned text of each submission. Every field is
# checked by hand against a random sample of offers (validate), and a field
# that cannot be read is left blank, never guessed.

MONTHS = ("january february march april may june july august september "
          "october november december").split()
DATE = (r"(?:(?:mon|tues|wednes|thurs|fri|satur|sun)day,?\s+)?"
        r"(" + "|".join(MONTHS) + r")\.?\s+(\d{1,2})\s*,?\s*(\d{4})")
NUM = r"(?:US|U\.S\.|C|CDN|CA)?\$\s?(\d[\d,]*(?:\.\d+)?)"


def _iso(m) -> str:
    return f"{int(m.group(3)):04d}-{MONTHS.index(m.group(1).lower()) + 1:02d}-{int(m.group(2)):02d}"


def _dates(s: str) -> list[str]:
    out = []
    for m in re.finditer(DATE, s, re.I):
        try:
            out.append(_iso(m))
        except ValueError:
            pass
    return out


def _num(x: str) -> float:
    return float(x.replace(",", "").rstrip("."))


def clean(s: str) -> str:
    """HTML (or SGML plain text) to plain text, one paragraph per line."""
    s = re.sub(r"(?is)<(script|style).*?</\1>", " ", s)
    s = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</tr>|</h\d>", "\n", s)
    s = re.sub(r"<[^>]+>", " ", s)
    s = html.unescape(s).replace("\xa0", " ").replace("​", " ")
    s = s.replace("’", "'").replace("“", '"').replace("”", '"')
    s = re.sub(r"[ \t\r\f\v]+", " ", s)
    return re.sub(r"\n\s*\n+", "\n", s)


def documents(path: str) -> list[tuple[str, str]]:
    """(type, cleaned text) for each text document in a submission."""
    out = []
    for d in re.findall(r"<DOCUMENT>(.*?)</DOCUMENT>", filing_text(path), re.S):
        ty = re.search(r"<TYPE>([^\n]+)", d).group(1).strip().upper()
        body = re.search(r"<TEXT>(.*)</TEXT>", d, re.S)
        body = body.group(1) if body else ""
        if ty in ("GRAPHIC", "ZIP", "XML") or ty.startswith("EX-FILING") or "begin 6" in body[:300]:
            continue
        out.append((ty, clean(body)))
    return out


_NOT_OTP = re.compile(r"letter\s+of\s+transmittal|guaranteed\s+delivery|letter\s+to\s+(?:brokers|clients)|"
                      r"for\s+immediate\s+release|press\s+release|news\s+release", re.I)


def offer_document(ds: list[tuple[str, str]]) -> str:
    """The Offer to Purchase: the longest exhibit that opens as one; else the schedule itself."""
    best = ""
    for ty, b in ds[1:]:
        m = re.search(r"offer\s+to\s+(?:purchase|exchange)", b[:5000], re.I)
        x = _NOT_OTP.search(b[:5000])
        # A letter of transmittal or press release names the offer after its own title.
        if m and (x is None or m.start() < x.start()) and len(b) > len(best):
            best = b
    return best or (ds[0][1] if ds else "")


def title_of_class(main: str) -> str:
    i = main.lower().find("(title of class")
    if i < 0:
        return ""
    return re.sub(r"\s+", " ", main[max(0, i - 400):i].split(")")[-1]).strip()


def security(title: str, head: str) -> str:
    t = (title + " " + head[:600]).lower()
    for k, pat in [("notes", r"\bnotes?\b|debenture|\bbonds?\b"), ("options", r"\boptions?\b"),
                   ("warrants", r"warrant"), ("preferred", r"preferred|preference"),
                   ("units", r"\bunits?\b"), ("rights", r"\brights\b")]:
        if re.search(pat, title.lower()) or (not title and re.search(pat, t)):
            return k
    if re.search(r"common|ordinary|shares|stock|beneficial interest", t):
        return "common"
    return "other"


def parse_offer(path: str, filed: str = "") -> dict:
    ds = documents(path)
    main = ds[0][1] if ds else ""
    otp = offer_document(ds)
    m = re.search(r"offer\s+to\s+(?:purchase|exchange)", otp, re.I)
    head = otp[m.start():m.start() + 6000] if m else otp[:6000]
    r = {"title_class": title_of_class(main)[:120], "otp_chars": len(otp)}
    cu = re.search(r"([0-9A-Z]{6}[\s-]?[0-9A-Z]{2}[\s-]?[0-9A-Z]?)\s*(?:\n\s*)?\(\s*CUSIP", main[:20000])
    r["cusip8"] = re.sub(r"[\s-]", "", cu.group(1))[:8] if cu else None
    tk = (re.search(r"(?:under\s+the\s+(?:trading\s+)?symbols?|ticker\s+symbol)\s*:?\s*\"?\s*([A-Z][A-Z.\-]{0,6})\b", otp)
          or re.search(r"\((?:NYSE|NASDAQ|Nasdaq|AMEX|NYSE\s+American|NYSE\s+MKT|NYSE\s+Amex)\s*:\s*([A-Z][A-Z.\-]{0,6})\)", otp))
    r["symbol"] = tk.group(1) if tk else None
    r["security"] = security(r["title_class"], head)
    r["exchange"] = bool(re.search(r"offer\s+to\s+exchange", head[:300], re.I)
                         or (otp is main and re.search(r"exchange\s+offer|offer\s+to\s+exchange|split-off", main, re.I)))
    r["nav_priced"] = bool(re.search(r"net\s+asset\s+value", head[:2500], re.I))
    lo = hi = px = None
    AND = r"(?:\s+per\s+(?:[a-z]+\s+){0,3}?share)?\s*,?\s*(?:and|or|nor)\s+(?:not\s+)?"
    MORE = r"(?:(?:greater|more)\s+than|in\s+excess\s+of)"
    dm = (re.search(rf"n(?:ot|either)\s+{MORE}\s+{NUM}{AND}less\s+than\s+{NUM}", head, re.I)
          or re.search(rf"not\s+less\s+than\s+{NUM}{AND}{MORE}\s+{NUM}", head, re.I)
          or re.search(rf"(?:price\s+range|range\s+of\s+prices?|prices?)\s+(?:of|between|from)\s+{NUM}\s+(?:and|to)\s+{NUM}", head, re.I))
    if re.search(r"permitted\s+range|final\s+multiple", head, re.I):
        dm = None                              # a Dutch auction over a multiple of a reference price (Altaba)
        r["offer_type"] = "dutch"
    elif dm:
        a, b = _num(dm.group(1)), _num(dm.group(2))
        lo, hi = min(a, b), max(a, b)
        r["offer_type"] = "dutch"
    else:
        fm = (re.search(rf"(?:purchase|offer|cash|tender)\s+price\s+of\s+{NUM}\s+per\s+(?:share|unit)", head, re.I)
              or re.search(rf"\bat\s+(?:a\s+price\s+of\s+)?{NUM}\s+per\s+(?:share|unit)", head, re.I)
              or re.search(rf"\bfor\s+{NUM}\s+per\s+(?:share|unit)", head, re.I))
        if fm:
            px = _num(fm.group(1))
        r["offer_type"] = "fixed" if px is not None else ("nav" if r["nav_priced"] else "")
    r.update(price=px, price_lo=lo, price_hi=hi)
    sm = re.search(r"up\s+to\s+(?:a\s+maximum\s+of\s+)?(?:an\s+aggregate\s+of\s+)?(\d[\d,]{3,}|\d+(?:\.\d+)?\s+million)\s+"
                   r"(?:of\s+(?:its|the|our)\s+)?(?:outstanding\s+)?(?:[A-Z]\s+)?(?:shares|common|ordinary|class|units)", head, re.I)
    r["shares_sought"] = ((_num(sm.group(1).split()[0]) * (1e6 if "million" in sm.group(1).lower() else 1)) if sm else None)
    vm = re.search(rf"(?:up\s+to|for\s+not\s+more\s+than|not\s+exceeding|(?:aggregate\s+)?purchase\s+price\s+of\s+(?:not?\s+(?:more|greater)\s+than|up\s+to))\s+{NUM}\s*(million|billion)?(?![\d.,]*\s*per\b)", head, re.I)
    # The cover states the offer in shares or in dollars; "sought" is whichever it leads with.
    r["sought_unit"] = ("shares" if sm and (not vm or sm.start() < vm.start()) else "dollars" if vm else None)
    r["dollars_sought"] = (_num(vm.group(1)) * {"million": 1e6, "billion": 1e9}.get((vm.group(2) or "").lower(), 1)
                           if vm else None)
    # Odd lots: the clause, and the threshold ("fewer than 100 Shares").
    odd = [x.start() for x in re.finditer(r"odd[\s\-‐-—]*lots?\b", otp, re.I)]
    thr = None
    for i in odd:
        tm = re.search(r"(?:fewer|less)\s+than\s+(\d{2,4}|one\s+hundred)\s+(?:\(\d+\)\s+)?(?:[\w-]+\s+){0,3}?(?:shares|units)", otp[i:i + 1500], re.I)
        if tm:
            thr = 100 if "hundred" in tm.group(1).lower() else int(tm.group(1))
            break
    # An odd-lot offer buys only from holders of record of fewer than 100 shares on a past record date.
    r["odd_lot_only"] = bool(re.search(r"(?:held|owned)\s+by\s+(?:holders|stockholders|shareholders)\s+of\s+(?:\d{2,3}|ninety-nine)\s+or\s+fewer"
                                      r"|odd[\s-]*lot\s+tender\s+offer|held\s+(?:of\s+record\s+)?by\s+odd[\s-]*lot", head[:1500], re.I))
    r["odd_lot_mentioned"] = bool(odd)
    r["odd_lot_threshold"] = thr
    r["odd_lot_priority"] = bool(odd) and thr is not None and bool(
        re.search(r"odd[\s\-‐-—]*lot[^.]{0,200}(?:priority|before\s+proration|not\s+be\s+subject\s+to\s+proration|"
                  r"without\s+(?:regard\s+to\s+)?proration|accept\s+for\s+purchase\s+all)|priority[^.]{0,120}odd[\s\-]*lot",
                  otp, re.I))
    r["expiration"] = None
    for em in re.finditer(r"expire[sd]?\b.{0,250}?" + DATE, head, re.I | re.S):
        x = _iso(re.search(DATE, em.group(0), re.I))
        if x >= filed:                     # a blank cover date (", , 2010") must not pick up an earlier one
            r["expiration"] = x
            break
    dt = re.search(r"dated\s+" + DATE, otp[:4000], re.I)
    r["otp_dated"] = _iso(re.search(DATE, dt.group(0), re.I)) if dt else None
    return r


# The offer itself, terminated: "terminated its tender offer", "the Offer has been terminated".
_OFFER = r"(?:the\s+|its\s+|our\s+)(?:modified\s+)?(?:\"?dutch\s+auction\"?\s+)?(?:self[\s-]+)?(?:issuer\s+)?(?:tender\s+)?offer\b"
_TERMINATED = re.compile(
    r"(?:terminated|terminating|announc\w+\s+(?:the\s+)?termination\s+of|withdrew|withdrawn)\s+" + _OFFER +
    r"|\boffer\s+(?:has\s+been|was|is|had\s+been|is\s+hereby|has\s+been\s+hereby)\s+(?:terminated|withdrawn)"
    r"|(?:decided|determined|elected)\s+to\s+(?:terminate|withdraw)\s+" + _OFFER, re.I)
# An extension of this offer, not of a loan or a deadline elsewhere.
_EXTENDED = re.compile(r"extend\w*\s+(?:the\s+)?(?:expiration|offer|tender)|extend\w*\s+(?:the\s+|its\s+)(?:\w+\s+){0,4}offer"
                       r"|(?:expiration\s+(?:date|time)|offer)[^.]{0,80}?(?:has\s+been|was|is\s+being|being)\s+extended", re.I)


def parse_amendments(paths: list[str], dates: list[str], expiration: str | None, filed: str,
                     eightks: frozenset = frozenset()) -> dict:
    """Extensions and the outcome, read from the offer's amendments and 8-Ks in filing order.

    Each document's facts are collected first. The final expiration comes from
    the extensions (or an "expired ... on <date>" statement); results are then
    read only from documents filed within 20 days of it, so a later 8-K that
    mentions the buyback (an "average purchase price" in an earnings release)
    cannot replace the tender price. Nothing after a termination is read.
    Extensions count only from SC TO-I/A amendments, which Rule 13e-4 requires
    for any extension; an 8-K can describe someone else's offer."""
    exp0 = expiration or filed
    docs_ = []
    for path, d in sorted(zip(paths, dates), key=lambda x: x[1]):
        text = "\n".join(b for _, b in documents(path))
        if not re.search(r"tender\s+offer|offer\s+to\s+purchase", text, re.I):
            continue                                  # an 8-K about something else
        f = {"d": d, "ext": [], "expired": [], "prices": [], "accepted": [], "prorations": [], "pay": [],
             "prelim_prorations": [], "final_results": False, "no_proration": False, "costs": [], "tendered": None,
             "final_prices": [],
             "terminated": False,
             "final_box": bool(re.search(r"final\s+amendment\s+reporting\s+the\s+results[^:]{0,40}:?\s*"
                                         r"[x\u00fd\u2612\u00fe\u2611\u25a0]", text, re.I))}
        for sent in re.split(r"(?<=[.;])\s+(?=[A-Z(])", text):
            low = sent.lower()
            if len(sent) > 1500 or ("offer" not in low and "expir" not in low and "shares" not in low):
                continue
            ds_ = _dates(sent)
            if ds_ and path not in eightks and _EXTENDED.search(sent):
                f["ext"] += [x for x in ds_ if exp0 < x <= _add_days(exp0, 150)]
            for xm in re.finditer(r"\bexpired\b[^;]{0,160}?" + DATE, sent, re.I):     # "5:00 p.m." has periods
                x = _iso(re.search(DATE, xm.group(0)[7:], re.I))     # the date after "expired"
                if _add_days(exp0, -10) <= x <= _add_days(exp0, 200) and x <= d:
                    f["expired"].append(x)
            if _TERMINATED.search(sent) and not re.search(
                    r"\b(?:may|might|could|would|right\s+to|unless|if|whether|not\s+been|until|in\s+the\s+event|upon|once)\b[^.]{0,60}(?:terminat|withdr)", low):
                # Dated by the latest date the sentence gives ("On March 11, 2014, the Company withdrew ..."), else the filing.
                f["terminated"] = max([x for x in ds_ if _add_days(d, -7) <= x <= d] or [d])
            if re.search(r"accepted|purchased|will\s+(?:purchase|acquire|buy)|final|results", low):
                for pm in re.finditer(rf"(?:price\s+(?:of|was|is|equal\s+to)|\bat)\s+{NUM}\s+per\s+(?:[\w-]+\s+){{0,3}}?share", sent, re.I):
                    if not re.search(r"clos(?:e|ing)|last\s+(?:reported\s+)?sale|traded|market\s+price|average|minimum|maximum|would|could|\bmay\b|\bif\b|should|assum",
                                     sent[max(0, pm.start() - 120):pm.start()], re.I):
                        f["prices"].append(_num(pm.group(1)))
                        if re.search(r"final|accepted|has\s+(?:purchased|acquired|repurchased)", low) and not re.search(r"prelim|expect", low):
                            f["final_prices"].append(_num(pm.group(1)))
                am = re.search(r"(?:accepted|accept|purchased?|repurchased?|acquired?|buy)\s+(?:for\s+(?:purchase|payment)\s+)?"
                               r"(?:a\s+total\s+of\s+|an\s+aggregate\s+of\s+|approximately\s+)*(\d[\d,]{2,})\s+(?:shares|units)", sent, re.I)
                if am:
                    f["accepted"].append(_num(am.group(1)))
            if re.search(r"principal\s+amount|\bnotes\b|debentures", low):
                continue                              # a concurrent debt tender, not this offer
            if re.search(r"final\s+results|final\s+count", low):
                f["final_results"] = True
            pr = (re.search(r"proration\s+factor[^.%]{0,140}?(\d{1,3}(?:\.\d+)?)\s*(?:%|percent)", sent, re.I)
                  or re.search(r"(\d{1,3}(?:\.\d+)?)\s*(?:%|percent)\s+of\s+(?:their|the|all)\s+(?:properly\s+)?(?:tendered\s+)?shares"
                               r"[^.]{0,60}(?:purchased|accepted)", sent, re.I) if re.search(r"prorat|oversubscri", low) else None)
            if not pr and re.search(r"pro\s+rata", low):
                pr = re.search(r"(\d{1,3}(?:\.\d+)?)\s*(?:%|percent)\s+of\s+(?:the\s+|their\s+)?(?:[\w-]+\s+){0,4}?shares\s+"
                               r"(?:of\s+[\w\s]{0,20}?)?(?:that\s+were\s+)?(?:properly|validly)\s+tendered", sent, re.I)
            if pr:
                f["prelim_prorations" if re.search(r"prelim|expect", low) else "prorations"].append(float(pr.group(1)))
            tn = re.search(r"(?:total|aggregate)\s+of\s+(?:approximately\s+)?(\d[\d,]{2,})\s+(?:[\w-]+\s+){0,4}?shares[^.]{0,120}?(?:properly|validly)\s+tendered", sent, re.I)
            if tn and not re.search(r"prelim|expect", low):
                f["tendered"] = _num(tn.group(1))
            if re.search(r"no\s+proration|not\s+(?:be\s+)?(?:subject\s+to\s+)?prorat|without\s+(?:any\s+)?proration|not\s+fully\s+subscribed|undersubscribed", low):
                f["no_proration"] = True
            for cm in re.finditer(rf"(?:aggregate\s+(?:purchase\s+price|cost)|total\s+cost)\s+of\s+(?:approximately\s+)?{NUM}(?!\s*(?:million|billion))", sent, re.I):
                f["costs"].append(_num(cm.group(1)))
            pm_ = re.search(r"(?:payment\s+for\s+(?:the\s+|all\s+)?(?:\w+\s+){0,2}?shares|made\s+payment|paid\s+for\s+(?:the\s+)?shares|settle(?:d|ment))"
                            r"[^.]{0,160}?\b(?:on|by|on\s+or\s+about)\s+" + DATE, sent, re.I)
            if pm_:
                x = _iso(re.search(DATE, pm_.group(0), re.I))
                if exp0 <= x <= _add_days(exp0, 200):
                    f["pay"].append(x)
        docs_.append(f)
        if f["terminated"]:
            break                                     # nothing after a termination
    ext = [x for f in docs_ for x in f["ext"]]
    expired = next((f["expired"] for f in docs_ if f["expired"]), [])   # the first report; later 8-Ks misstate it
    final_exp = max([exp0] + ext) if expiration else None
    if expired:
        final_exp = max(expired)
    cut = _add_days(final_exp or exp0, 20)
    res = [f for f in docs_ if _add_days(exp0, -3) <= f["d"] <= cut]
    last = lambda k: next((f[k][-1] for f in reversed(res) if f[k]), None)
    first_of_last = lambda k: next((f[k][0] for f in reversed(res) if f[k]), None)
    term = next((f["terminated"] for f in docs_ if f["terminated"]), None)
    # The price: the first final figure in the earliest document that reports one. Later filings
    # can quote other prices "for" the shares (Casey's 2010: Couche-Tard's $45 hostile bid after
    # the $38 tender closed). Else the preliminary figure from the latest document.
    prices = next((f["final_prices"][0] for f in res if f["final_prices"]), None)
    if prices is None:
        prices = first_of_last("prices")
    accepted = first_of_last("accepted")
    # Proration: the final factor; else none if the final results say none (or were announced without one); else the preliminary one.
    prorations = last("prorations")
    tendered = next((f["tendered"] for f in reversed(res) if f["tendered"]), None)
    all_taken = any(f["no_proration"] for f in res) or (tendered is not None and accepted is not None and accepted >= tendered)
    if prorations is None and not all_taken:
        prorations = last("prelim_prorations")      # the final release gave none; the preliminary one stands
    if all_taken and not last("prorations"):
        prorations = None
    # A results release that gives only the shares bought and their exact total cost implies the price.
    cost = last("costs")
    implied = round(cost / accepted, 2) if (prices is None and cost and accepted) else None
    pay = [x for f in res for x in f["pay"] if x >= (final_exp or exp0)]
    final_box = any(f["final_box"] for f in docs_)
    outcome = ("terminated" if term and prices is None and accepted is None else
               "completed" if (prices is not None or accepted is not None or prorations is not None or final_box)
               else "unknown")
    # When each extension became public: (amendment filing date, new expiration). Entry rule E2
    # may use only the expiration in force on its entry day, so it needs this, not the final one.
    timeline = ";".join(f"{f['d']}>{max(f['ext'])}" for f in docs_ if f["ext"])
    return {"n_followups": len(docs_), "extensions": len(set(ext)), "final_expiration": final_exp,
            "ext_timeline": timeline,
            "outcome": outcome, "terminated_on": term if outcome == "terminated" else None,
            "final_price": prices, "implied_price": implied, "shares_accepted": accepted, "proration_pct": prorations,
            "payment_date": min(pay) if pay else None, "final_box": final_box}


def _add_days(iso: str, n: int) -> str:
    import datetime as _dt
    return (_dt.date.fromisoformat(iso) + _dt.timedelta(days=n)).isoformat()


def _parse_one(args):
    path, apaths, adates, filed, eightks = args
    try:
        r = parse_offer(path, filed)
        r.update(parse_amendments(apaths, adates, r["expiration"], filed, eightks))
        lo, hi = (r["price_lo"], r["price_hi"]) if r["offer_type"] == "dutch" else (r["price"], r["price"])
        ip = r.pop("implied_price")
        if r["final_price"] is None and ip is not None and lo is not None and lo - 0.005 <= ip <= hi + 0.005:
            r["final_price"] = ip
        r["error"] = ""
    except Exception as e:                                    # recorded, never silently dropped
        r = {"error": f"{type(e).__name__}: {e}"}
    r["path"] = path
    return r


def parse() -> int:
    """Every candidate offer's terms and outcome -> data/edgar/deals.csv."""
    import pandas as pd
    from concurrent.futures import ProcessPoolExecutor
    orig, amend = candidates()
    k = CACHE / "eightk.csv"
    eightks = frozenset()
    if k.exists():                         # results announced only in an 8-K
        k = pd.read_csv(k, dtype=str)
        eightks = frozenset(k["path"])
        amend = pd.concat([amend, k])
    amend = amend.sort_values("date")
    jobs = []
    for _, o in orig.iterrows():
        # An amendment belongs to the filer's latest offer filed on or before it.
        later = orig[(orig["cik"] == o["cik"]) & (orig["date"] > o["date"])]["date"]
        stop = min(later) if len(later) else "9999"
        a = amend[(amend["cik"] == o["cik"]) & (amend["date"] >= o["date"]) & (amend["date"] < stop)
                  & (amend["date"] <= _add_days(o["date"], 365)) & (amend["path"] != o["path"])]
        jobs.append((o["path"], list(a["path"]), list(a["date"]), o["date"], eightks & set(a["path"])))
    with ProcessPoolExecutor(8) as ex:
        rows = list(ex.map(_parse_one, jobs, chunksize=8))
    d = orig.merge(pd.DataFrame(rows), on="path", how="left")
    d.to_csv(CACHE / "deals.csv", index=False)
    print(f"{len(d):,} offers parsed; {(d['error'] != '').sum()} errors")
    print(d.groupby(["security", "offer_type"], dropna=False).size().to_string())
    return 0


# ------------------------------------------------------------------ CRSP prices

_NAME_STOP = r"\b(INC|CORP|CORPORATION|CO|COMPANY|LTD|LIMITED|PLC|THE|GROUP|HOLDINGS?|INTERNATIONAL|NEW|DE|LLC|LP|SA|NV|AG)\b"


def same_issuer(filer: str, crsp_name: str) -> bool:
    """Does the CRSP issuer name on the filing date belong to the filer?

    A cover CUSIP can be wrong: Oppenheimer's 2023 Schedule TO gives 671005106,
    which CRSP assigns to its SPAC, OPY Acquisition Corp I. CRSP spells names
    differently ("S L M CORP", "BLOCK H & R INC"), so compare squashed names by
    prefix and containment, then by shared words of three or more letters."""
    if not isinstance(crsp_name, str) or not crsp_name.strip():
        return True                                    # nothing to compare
    words = lambda x: re.sub(_NAME_STOP, " ", re.sub(r"[^A-Z0-9 ]", " ", str(x).upper()))
    a, b = (re.sub(r"[^A-Z0-9]", "", words(x)) for x in (filer, crsp_name))
    if a[:4] == b[:4] or a in b or b in a:
        return True
    return bool({w for w in words(filer).split() if len(w) >= 3} & {w for w in words(crsp_name).split() if len(w) >= 3})


def prices() -> int:
    """CRSP daily prices for every in-scope offer (WRDS), and the match report.

    Link order, each checked on the filing date:
      1. the CUSIP on the Schedule TO cover, matched to a CRSP security whose
         issuer name on that date is the filer's (same_issuer);
      2. the CCM permno found by link() (CIK -> gvkey), if the CUSIP finds nothing;
      3. the trading symbol the Offer to Purchase names, matched to a CRSP
         ordinary share carrying that ticker.
    The CUSIP comes first because CIK -> gvkey can land on a merger successor
    (Chambers Street -> Gramercy, Healthcare Trust of America -> Healthcare
    Realty) and a tracking stock has its own permno (FNFV inside FNF).
    Daily rows go to data/edgar/crsp_daily.parquet, which is git-ignored: CRSP
    data is licensed and never committed."""
    import pandas as pd
    import wrds
    from crsp_pull import username
    md = da.guard("edgar_tenders")
    d = in_scope(pd.read_csv(CACHE / "deals.csv", dtype={"cik": str, "cusip8": str}))
    db = wrds.Connection(wrds_username=username())
    cols = "permno, ticker, cusip, issuernm, secinfostartdt, secinfoenddt, sharetype, securitytype, securitysubtype"
    cus = ",".join(f"'{c}'" for c in sorted(set(d["cusip8"].dropna())))
    tks = ",".join(f"'{t}'" for t in sorted(set(d["symbol"].dropna())))
    si = db.raw_sql(f"SELECT {cols} FROM crsp.stksecurityinfohist WHERE cusip IN ({cus}) OR ticker IN ({tks})")
    si["s"], si["e"] = si["secinfostartdt"].astype(str), si["secinfoenddt"].astype(str)
    def on(r, m):
        m = m[(m["s"] <= r["date"]) & (m["e"] >= r["date"])]
        if m["permno"].nunique() != 1 or not same_issuer(r["company"], m["issuernm"].iloc[0]):
            return None
        return m["permno"].iloc[0]
    d["permno"] = pd.to_numeric(d["permno"], errors="coerce")
    method, perm = [], []
    for _, r in d.iterrows():
        p = on(r, si[si["cusip"] == r["cusip8"]]) if isinstance(r["cusip8"], str) else None
        if p is not None:
            method.append("cusip"); perm.append(p); continue
        if pd.notna(r["permno"]):            # no CUSIP, or one CRSP doesn't carry (the ordinary share behind an ADR)
            method.append("ccm"); perm.append(r["permno"]); continue
        com = si[(si["sharetype"] == "NS") & (si["securitytype"] == "EQTY") & (si["securitysubtype"] == "COM")]
        p = on(r, com[com["ticker"] == r["symbol"]]) if isinstance(r["symbol"], str) else None
        method.append("ticker" if p is not None else None); perm.append(p)
    d["link_method"], d["permno"] = method, pd.to_numeric(pd.Series(perm, index=d.index), errors="coerce")
    linked = d[d["permno"].notna()]
    permnos = ",".join(str(int(p)) for p in sorted(set(linked["permno"])))
    px = db.raw_sql("SELECT permno, dlycaldt, dlyprc, dlyclose, dlyopen, dlyvol, dlyprcvol, dlybid, dlyask, "
                    "dlyfacprc, dlycumfacpr, dlydelflg, ticker, cusip, primaryexch FROM crsp.dsf_v2 "
                    f"WHERE permno IN ({permnos}) AND dlycaldt BETWEEN '2009-11-01' AND '{md}'",
                    date_cols=["dlycaldt"])
    db.close()
    # Keep only the days around some offer: 60 calendar days before the filing to 150 after.
    win = pd.concat([pd.DataFrame({"permno": int(r.permno), "dlycaldt": pd.date_range(
        pd.Timestamp(r.date) - pd.Timedelta(days=60), pd.Timestamp(r.date) + pd.Timedelta(days=150))})
        for r in linked.itertuples()]).drop_duplicates()
    px = px.merge(win, on=["permno", "dlycaldt"])
    px.to_parquet(CACHE / "crsp_daily.parquet", index=False)
    last = px["dlycaldt"].max().date().isoformat()
    has = px.groupby("permno")["dlycaldt"].agg(["min", "max"])
    def priced(r):
        if pd.isna(r.permno) or int(r.permno) not in has.index:
            return False
        return has.loc[int(r.permno), "min"] <= pd.Timestamp(r.date) <= has.loc[int(r.permno), "max"]
    d["crsp_priced"] = [priced(r) for r in d.itertuples()]
    d[["path", "permno", "link_method", "crsp_priced"]].to_csv(CACHE / "deal_links.csv", index=False)
    print(f"{len(d)} in-scope offers: {d['cusip8'].notna().sum()} with a CUSIP read from the cover; linked by "
          + ", ".join(f"{k} {v}" for k, v in d["link_method"].value_counts().items())
          + f"; {d['permno'].isna().sum()} unlinked; {d['crsp_priced'].sum()} have CRSP prices on the filing date "
          f"(CRSP ends {last})")
    un = d[~d["crsp_priced"]][["company", "cik", "date", "symbol", "cusip8", "offer_type", "odd_lot_priority",
                                "link_method"]]
    un.to_csv(ROOT / "reports" / "odd_lot_unmatched.csv", index=False)
    print(f"unmatched list: reports/odd_lot_unmatched.csv ({len(un)} offers)")
    return 0


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", nargs="?", default="index",
                    choices=["index", "link", "download", "eightk", "parse", "prices"])
    ap.add_argument("--family", default="SC TO-I", choices=list(FAMILIES),
                    help="form family for index (the other commands are SC TO-I only)")
    a = ap.parse_args()
    if a.cmd == "index":
        sys.exit(build_index(a.family))
    sys.exit({"link": link, "download": download, "eightk": eightk, "parse": parse, "prices": prices}[a.cmd]())
