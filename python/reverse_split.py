#!/usr/bin/env python3
"""Reverse-split round-up arbitrage (candidate trial 60). See docs/REVERSE_SPLIT_ROUNDUP.md.

    python3 python/reverse_split.py crsp        # ONE WRDS connection: events, securities, CIKs, daily quotes
    python3 python/reverse_split.py filings     # EDGAR filings around each event (cached)
    python3 python/reverse_split.py parse       # ratio, effective date, fractional-share class, announcement
    python3 python/reverse_split.py validate    # automated checks and the hand-validation comparison
    python3 python/reverse_split.py gate        # Phase 2 on the design period

THE TRADE. A 1-for-N reverse split whose filing says fractional shares are
ROUNDED UP to a whole share: 1 share bought before the split becomes 1
post-split share, worth about N old ones. One share per account.

ENTRY-TIME. A filing is usable from the day after its acceptance date. The
entry may use only filings usable by the entry day; outcomes (the effective
date as it happened, a cancellation) only settle the position.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "python"))

import data_access as da

CACHE = ROOT / "data" / "rsplit"            # git-ignored: CRSP rows are licensed
CRSP_LAST = "2025-12-31"
FIRST = "2010-01-01"
PRE_DAYS, POST_DAYS = 130, 45              # daily quotes kept around each ex date (calendar days)
EXCHANGES = ("N", "Q", "A")                # NYSE, Nasdaq, NYSE American (AMEX)


# ------------------------------------------------------------------ CRSP (one WRDS connection)

def crsp() -> int:
    """Every reverse split of a CRSP ordinary common share on NYSE, Nasdaq or
    AMEX with an ex date in 2010-2025, its security history, its CIK (CCM ->
    Compustat), and daily quotes from PRE_DAYS before to POST_DAYS after.

    One connection. Each step's output is saved as soon as it arrives, so a
    failure later in the command doesn't lose what came before; nothing is
    retried."""
    import wrds
    from crsp_pull import username
    md = min(da.guard("reverse_split"), CRSP_LAST)
    CACHE.mkdir(parents=True, exist_ok=True)
    db = wrds.Connection(wrds_username=username())
    try:
        for table in ("crsp.stkdistributions", "crsp.dsedist"):     # CIZ (v2), then the legacy table
            try:
                cols = list(db.raw_sql(f"SELECT * FROM {table} LIMIT 1").columns)
                break
            except Exception as e:                                   # not a reconnect: the same session
                print(f"{table}: {type(e).__name__}", flush=True)
                db.connection.rollback() if hasattr(db, "connection") else None
        print(f"{table} columns:", cols, flush=True)
        ex = next(c for c in ("disexdt", "exdt") if c in cols)
        fac = next(c for c in ("disfacshr", "facshr") if c in cols)
        ev = db.raw_sql(f"SELECT * FROM {table} WHERE {ex} BETWEEN '{FIRST}' AND '{md}' "
                        f"AND {fac} < 0 AND {fac} > -1")
        ev.to_parquet(CACHE / "crsp_events_raw.parquet", index=False)
        print(f"{len(ev):,} distribution rows with a share factor in (-1, 0)", flush=True)
        if "distype" in ev:
            print(ev["distype"].value_counts().to_string(), flush=True)
        permnos = sorted(set(ev["permno"].astype(int)))
        si = []
        for i in range(0, len(permnos), 1000):
            ch = ",".join(map(str, permnos[i:i + 1000]))
            si.append(db.raw_sql(f"SELECT * FROM crsp.stksecurityinfohist WHERE permno IN ({ch})"))
        si = pd.concat(si, ignore_index=True)
        si.to_parquet(CACHE / "crsp_secinfo.parquet", index=False)
        ccm = db.raw_sql("SELECT l.gvkey, l.lpermno AS permno, l.linkdt, l.linkenddt, c.cik, c.conm "
                         "FROM crsp.ccmxpf_lnkhist l JOIN comp.company c ON l.gvkey = c.gvkey "
                         "WHERE l.linktype IN ('LU','LC') AND l.linkprim IN ('P','C') "
                         f"AND l.lpermno IN ({','.join(map(str, permnos))})")
        ccm.to_parquet(CACHE / "crsp_ccm.parquet", index=False)
        # ordinary common shares on the three exchanges, on the ex date
        ev[ex] = ev[ex].astype(str)
        si["s"], si["e"] = si["secinfostartdt"].astype(str), si["secinfoenddt"].astype(str)
        m = ev[["permno", ex]].merge(si, on="permno")
        m = m[(m["s"] <= m[ex]) & (m["e"] >= m[ex]) & (m["sharetype"] == "NS") & (m["securitytype"] == "EQTY")
              & (m["securitysubtype"] == "COM") & m["primaryexch"].isin(EXCHANGES)]
        keep = m[["permno", ex]].drop_duplicates()
        print(f"{len(keep):,} events on ordinary common shares on NYSE/Nasdaq/AMEX", flush=True)
        win = keep.groupby("permno")[ex].agg(["min", "max"])
        px = []
        ps = list(win.sort_values("min").index)
        for i in range(0, len(ps), 300):
            ch = ps[i:i + 300]
            lo = (pd.Timestamp(win.loc[ch, "min"].min()) - pd.Timedelta(days=PRE_DAYS)).strftime("%Y-%m-%d")
            hi = min(md, (pd.Timestamp(win.loc[ch, "max"].max()) + pd.Timedelta(days=POST_DAYS)).strftime("%Y-%m-%d"))
            px.append(db.raw_sql("SELECT permno, dlycaldt, dlyprc, dlyopen, dlyclose, dlybid, dlyask, dlyvol, "
                                 "dlyprcvol, dlyfacprc, dlycumfacpr, dlydelflg, ticker, primaryexch FROM crsp.dsf_v2 "
                                 f"WHERE permno IN ({','.join(map(str, ch))}) AND dlycaldt BETWEEN '{lo}' AND '{hi}'"))
            print(f"  quotes {min(i + 300, len(ps)):,}/{len(ps):,} permnos", flush=True)
    finally:
        db.close()
    px = pd.concat(px, ignore_index=True)
    px["dlycaldt"] = px["dlycaldt"].astype(str)
    # only the days around some event
    k = keep.rename(columns={ex: "exdt"})
    w = px.merge(k, on="permno")
    lo = (pd.to_datetime(w["exdt"]) - pd.Timedelta(days=PRE_DAYS)).dt.strftime("%Y-%m-%d")
    hi = (pd.to_datetime(w["exdt"]) + pd.Timedelta(days=POST_DAYS)).dt.strftime("%Y-%m-%d")
    w = w[(w["dlycaldt"] >= lo) & (w["dlycaldt"] <= hi)].drop(columns="exdt").drop_duplicates(["permno", "dlycaldt"])
    w.to_parquet(CACHE / "crsp_daily.parquet", index=False)
    k.to_csv(CACHE / "events_crsp.csv", index=False)
    print(f"{len(w):,} daily rows kept; wrote data/rsplit/")
    return 0


# ------------------------------------------------------------------ events and EDGAR filings

EIGHTK_ITEMS = {"5.03", "3.01", "8.01"}
PROXY_FORMS = ("DEF 14A", "PRE 14A", "DEF 14C", "PRE 14C")
BEFORE, AFTER = 120, 10                     # filing window around the ex date, calendar days
DOCS = CACHE / "docs"


def events() -> pd.DataFrame:
    """One row per CRSP reverse split: permno, ex date, CRSP ratio N, CIK on the ex date."""
    k = pd.read_csv(CACHE / "events_crsp.csv")
    ev = pd.read_parquet(CACHE / "crsp_events_raw.parquet")
    ev["exdt"] = ev["disexdt"].astype(str)
    k = k.merge(ev[["permno", "exdt", "disfacshr", "disdeclaredt"]].drop_duplicates(["permno", "exdt"]),
                on=["permno", "exdt"], how="left")
    k["n_crsp"] = 1 / (1 + k["disfacshr"])
    ccm = pd.read_parquet(CACHE / "crsp_ccm.parquet")
    ccm["s"] = ccm["linkdt"].astype(str)
    ccm["e"] = ccm["linkenddt"].astype(str).where(ccm["linkenddt"].notna(), "2099-12-31")
    m = k.merge(ccm[["permno", "s", "e", "cik", "conm"]], on="permno", how="left")
    m = m[m["cik"].isna() | ((m["s"] <= m["exdt"]) & (m["e"] >= m["exdt"]))]
    m = m.sort_values("cik").drop_duplicates(["permno", "exdt"])
    k = k.merge(m[["permno", "exdt", "cik", "conm"]], on=["permno", "exdt"], how="left")
    si = pd.read_parquet(CACHE / "crsp_secinfo.parquet")
    si["s"], si["e"] = si["secinfostartdt"].astype(str), si["secinfoenddt"].astype(str)
    n = k.merge(si[["permno", "s", "e", "issuernm", "ticker"]], on="permno")
    n = n[(n["s"] <= n["exdt"]) & (n["e"] >= n["exdt"])].drop_duplicates(["permno", "exdt"])
    k = k.merge(n[["permno", "exdt", "issuernm", "ticker"]], on=["permno", "exdt"], how="left")
    k["cik"] = k["cik"].map(lambda c: str(int(c)) if pd.notna(c) else None)
    return k.sort_values(["exdt", "permno"]).reset_index(drop=True)


def submissions(cik: str) -> pd.DataFrame:
    """A company's filings with acceptance times and 8-K items (data.sec.gov,
    cached in data/edgar/submissions by edgar_tenders.submissions)."""
    import json
    import edgar_tenders as et
    et.submissions(cik)
    pages = json.loads((et.CACHE / "submissions" / f"CIK{int(cik):010d}.json").read_text())
    rows = []
    for pg in pages:
        rows.append(pd.DataFrame({k: pg.get(k, [None] * len(pg["form"])) for k in
                                  ("form", "filingDate", "acceptanceDateTime", "accessionNumber", "items",
                                   "primaryDocument")}))
    d = pd.concat(rows, ignore_index=True)
    d["accepted"] = d["acceptanceDateTime"].astype(str).str[:10]
    return d


def scope() -> pd.DataFrame:
    """The filings to read for every event with a CIK: 8-Ks carrying item
    5.03, 3.01 or 8.01, and DEF/PRE 14A/14C proxies and information
    statements, filed from BEFORE days before the ex date to AFTER after."""
    import time
    ev = events()
    out = []
    for cik, g in ev.dropna(subset=["cik"]).groupby("cik"):
        for attempt in range(4):
            try:
                s = submissions(cik)
                break
            except Exception as e:                       # network drop: wait it out
                if attempt == 3:
                    print(f"  submissions failed for CIK {cik}: {e}", flush=True)
                    s = None
                time.sleep(10 * (attempt + 1))
        if s is None:
            continue
        is8k = s["form"].isin(["8-K", "8-K/A"]) & s["items"].fillna("").map(
            lambda x: bool(EIGHTK_ITEMS & set(x.split(","))))
        s = s[is8k | s["form"].isin(PROXY_FORMS)]
        for r in g.itertuples():
            lo = (pd.Timestamp(r.exdt) - pd.Timedelta(days=BEFORE)).strftime("%Y-%m-%d")
            hi = (pd.Timestamp(r.exdt) + pd.Timedelta(days=AFTER)).strftime("%Y-%m-%d")
            f = s[(s["filingDate"] >= lo) & (s["filingDate"] <= hi)]
            out.append(f.assign(permno=r.permno, exdt=r.exdt, cik=cik))
    f = pd.concat(out, ignore_index=True).rename(columns={"accessionNumber": "acc", "filingDate": "filed"})
    f.to_csv(CACHE / "filings.csv", index=False)
    return f


def _doc_path(acc: str) -> Path:
    return DOCS / f"{acc}.gz"


def text(r) -> str:
    """Cleaned text of a filing: the primary document plus every EX-99 exhibit
    (the press release that often carries the fractional-share sentence)."""
    import gzip
    import json
    import re
    import edgar_tenders as et
    import spac_proxy as sp
    out = _doc_path(r["acc"])
    if not out.exists():
        base = f"https://www.sec.gov/Archives/edgar/data/{int(r['cik'])}/{r['acc'].replace('-', '')}/"
        items = json.loads(sp._fetch(base + "index.json"))["directory"]["item"]
        names = [r["primaryDocument"]] if isinstance(r["primaryDocument"], str) else []
        names += [x["name"] for x in items if re.search(r"ex-?99|ex99|exhibit99|dex99", x["name"], re.I)
                  and re.search(r"\.(htm|html|txt)$", x["name"], re.I) and int(x.get("size") or 0) < 5_000_000]
        raw = b"".join(b"\n<DOC>\n" + sp._fetch(base + n) for n in dict.fromkeys(names))
        out.parent.mkdir(parents=True, exist_ok=True)
        tmp = out.with_suffix(".part")
        tmp.write_bytes(gzip.compress(raw))
        tmp.rename(out)
    return et.clean(gzip.decompress(out.read_bytes()).decode("latin-1"))


def filings() -> int:
    """Download every in-scope filing (6 threads under SEC's 8-a-second budget)."""
    import time
    from concurrent.futures import ThreadPoolExecutor
    f = scope().sort_values(["exdt", "acc"]).drop_duplicates("acc")
    f = pd.concat([f[f["exdt"] <= SPLIT["design"][1]], f[f["exdt"] > SPLIT["design"][1]]])   # design first
    todo = [r for _, r in f.iterrows() if not _doc_path(r["acc"]).exists()]
    print(f"{len(f):,} filings in scope; {len(todo):,} not cached", flush=True)

    def one(r) -> int:
        for attempt in range(5):
            try:
                text(r)
                return 0
            except Exception as e:
                if "404" in str(e) or attempt == 4:
                    print(f"  FAILED {r['acc']} {r['form']}: {e}", flush=True)
                    return 1
                time.sleep(15 * (attempt + 1))

    failed = 0
    with ThreadPoolExecutor(6) as ex:
        for i, k in enumerate(ex.map(one, todo), 1):
            failed += k
            if i % 1000 == 0:
                print(f"  {i:,}/{len(todo):,}", flush=True)
    print(f"done; {failed} failed", flush=True)
    return 0


# ------------------------------------------------------------------ parsers (text only)

import re

_W = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
      "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17,
      "eighteen": 18, "nineteen": 19, "twenty": 20, "twenty-five": 25, "thirty": 30, "thirty-five": 35, "forty": 40,
      "forty-five": 45, "fifty": 50, "sixty": 60, "seventy": 70, "seventy-five": 75, "eighty": 80, "ninety": 90,
      "one hundred": 100, "hundred": 100, "one hundred fifty": 150, "two hundred": 200, "two hundred fifty": 250,
      "three hundred": 300, "five hundred": 500, "one thousand": 1000}
_NUMW = "|".join(sorted((re.escape(k) for k in _W), key=len, reverse=True))
_N = rf"(\d[\d,]*|{_NUMW})"
_RATIO = re.compile(rf"\b(?:1|one)(?:\s*\(1\))?\s*(?:-|\s)?\s*(?:for|to)\s*(?:-|\s)?\s*{_N}(?:\s*\(\d[\d,]*\))?|"
                    rf"\b1\s*:\s*(\d[\d,]*)\b|"
                    rf"\b(?:1|one)\s*(?:\(1\)\s*)?(?:[\w-]+\s+){{0,3}}?shares?\s+(?:of\s+(?:[\w-]+\s+){{0,4}}?)?"
                    rf"(?:for|in\s+exchange\s+for)\s+(?:each|every)\s+{_N}(?:\s*\(\d[\d,]*\))?", re.I)
_RANGE = re.compile(r"\brange\b|\bbetween\b[^.]{0,60}?\band\b|\bnot\s+(?:less|more|greater)\s+than\b|\bup\s+to\b|"
                    r"\bany\s+of\s+the\s+following|\bany\s+whole\s+number|\branging\b|\bratios\b|\bone\s+of\b|"
                    r"\beither\b|\bfrom\s+(?:1|one)[\s-]+(?:for|to)\b|\bto\s+(?:1|one)[\s-]+(?:for|to)\b|\bmaximum\b|"
                    r"\bminimum\b|"
                    r"\bat\s+the\s+discretion\b|\bwithin\s+a\s+range\b", re.I)
_MONTH = r"(?:January|February|March|April|May|June|July|August|September|October|November|December)"
_DATE = rf"{_MONTH}\s+\d{{1,2}},?\s+\d{{4}}"
_SPLIT_ADJ = re.compile(rf"(?:split[\s-]+adjusted|post[\s-]+(?:reverse[\s-]+)?split)\s+basis[^.]{{0,160}}?({_DATE})|"
                        rf"({_DATE})[^.]{{0,120}}?(?:split[\s-]+adjusted|post[\s-]+(?:reverse[\s-]+)?split)\s+basis", re.I)
_EFFECTIVE = re.compile(rf"reverse\s+(?:stock\s+)?split[^.]{{0,200}}?(?:effective|become\s+effective|take\s+effect)"
                        rf"[^.]{{0,120}}?({_DATE})|(?:effective|take\s+effect)[^.]{{0,80}}?({_DATE})[^.]{{0,120}}?"
                        rf"reverse\s+(?:stock\s+)?split|"
                        rf"({_DATE})[^.]{{0,30}}?\(?the\s+[\"\u201c]?effective\s+(?:date|time)|"
                        rf"(?:on|as\s+of)\s+({_DATE})[^.]{{0,40}}?reverse\s+(?:stock\s+)?split[^.]{{0,80}}?"
                        rf"(?:was|became|is|will\s+be(?:come)?)\s+effective", re.I)
_CANCEL = re.compile(r"(?:abandon|withdr[ae]w|will\s+not\s+(?:effect|implement|proceed)|decided\s+not\s+to\s+"
                     r"(?:effect|implement|proceed)|postpone|delay|cancel)\w*[^.]{0,120}?reverse\s+(?:stock\s+)?split|"
                     r"reverse\s+(?:stock\s+)?split[^.]{0,120}?(?:has\s+been|was|will\s+be)\s+(?:abandoned|"
                     r"withdrawn|postponed|delayed|cancell?ed)", re.I)

CLASSES = ("ROUNDUP_HOLDER", "ROUNDUP_PARTICIPANT", "CASH", "DOWN", "UNKNOWN")
_FRAC = re.compile(r"fraction(?:al)?\s+(?:[\w\"\u201c\u201d-]+\s+){0,2}?(?:shares?|interests?)|fraction\s+of\s+a\s+share|"
                   r"fractional\s+post", re.I)
_UP = re.compile(r"round(?:ed|ing)?\s+up|round(?:ed|ing)?\s+upward|next\s+(?:higher\s+|largest\s+)?whole\s+(?:share|number)|"
                 r"(?:receive|issue[ds]?|entitled\s+to)\s+(?:one\s+|a\s+|1\s+)?(?:full|whole)\s+share|"
                 r"rounded\s+to\s+(?:the\s+)?next\s+whole|in\s+lieu\s+of\s+(?:such\s+|any\s+)?(?:a\s+)?fractional\s+"
                 r"shares?[^.]{0,40}?(?:one|a|1)\s+(?:full|whole)\s+share|(?:an\s+)?additional\s+(?:whole\s+|full\s+)?share", re.I)
_PARTICIPANT = re.compile(r"participant\s+level|at\s+the\s+(?:dtc\s+)?participant|(?:dtc|depository)\s+participant|"
                          # the round-up's recipient named as a holder of record, not "the number of holders of record"
                          r"(?:any|each|a|to|such)\s+(?:stock|share)?holders?\s+of\s+record|(?:stock|share)?holders?\s+of\s+"
                          r"record\s+(?:who|that|will|shall)|record\s+(?:stock|share)?holders?\s+(?:who|will|shall)|"
                          r"registered\s+(?:stock|share)?holders?\s+(?:who|will|shall)|"
                          r"\brecord\s+holders?\s+only|holders?\s+of\s+record\s+only|only\s+(?:to\s+)?(?:registered|"
                          r"record)\s+(?:holders|stockholders|shareholders)|beneficial\s+(?:holders|owners)[^.]{0,120}?"
                          r"(?:may|might)\s+(?:not|receive\s+cash)", re.I)
_CASH = re.compile(r"cash\s+(?:payment\s+)?in\s+lieu|in\s+lieu\s+of\s+(?:such\s+|any\s+)?fractional[^.]{0,80}?cash|"
                   r"(?:paid|settled|payable)\s+in\s+cash|cash(?:ed)?[\s-]+out|net\s+proceeds|paid\s+by\s+check|"
                   r"aggregate[^.]{0,120}?(?:sell|sold)|(?:sell|sold)[^.]{0,80}?on\s+behalf\s+of|"
                   r"(?:receive|paid|pay)\s+(?:a\s+)?cash|cash\s+payment|\bcash\b[^.]{0,40}?(?:equal|based\s+on|"
                   r"determined)", re.I)
_NO_CASH = re.compile(r"\bno\s+cash\b|\bnot\s+(?:receive|be\s+paid)[^.]{0,20}?cash|without\s+(?:the\s+)?payment\s+of\s+cash|"
                      r"instead\s+of\s+cash", re.I)
_DOWN = re.compile(r"round(?:ed|ing)?\s+down|round(?:ed|ing)?\s+downward|eliminated\s+(?:and|without)|disregarded|"
                   r"cancell?ed\s+without", re.I)          # "rounded to the nearest" is neither up nor down: UNKNOWN
# sentences about other instruments or transactions say nothing about the split's treatment
_OTHER = re.compile(r"\boptions?\b|\bawards?\b|\bwarrants?\b|restricted\s+stock|equity\s+(?:incentive\s+)?plans?|"
                    r"spin[\s-]?off|\bdistribution\b|\bmergers?\b|exchange\s+ratio|\bnotes?\b|preferred", re.I)
_SPLIT_WORD = re.compile(r"reverse|split|consolidat|combin", re.I)
# a reverse split followed by a forward split cashes out holders below the
# reverse ratio: a 1-share holder gets cash whatever happens to larger holders
_CASH_OUT = re.compile(r"reverse\s*/\s*forward|reverse\s+(?:stock\s+)?split\s+(?:followed|immediately\s+followed)\s+by\s+"
                       r"a\s+forward|cashed[\s-]+out|cash[\s-]+out\s+(?:of\s+)?(?:fractional|holders|stockholders|shareholders)", re.I)


def sentences(t: str) -> list[str]:
    import spac_proxy as sp
    flat = sp._flat(t)
    flat = re.sub(r"\b(Inc|Corp|Co|Ltd|No|U\.S|Mr|Ms|Dr)\.", r"\1", flat)
    return re.split(r"(?<=[.;])\s+(?=[A-Z(\"])", flat)


_NEAREST = re.compile(r"round(?:ed|ing)?\s+(?:off\s+)?to\s+the\s+nearest", re.I)
_INSTEAD = re.compile(r"^\s*(?:instead|rather|in\s+lieu)", re.I)
_OF_RECORD = re.compile(r"(?:stock|share)?holders?\s+of\s+record|record\s+(?:stock|share)?holders?|registered\s+"
                        r"(?:stock|share)?holders?", re.I)


def sentence_class(s: str, prev: str = "") -> str | None:
    """One sentence's fractional-share treatment, or None if it decides nothing.

    Sentences about options, awards, warrants, notes, a spin-off or a merger
    decide nothing. "Rounded to the nearest whole share" is neither up nor
    down. A round-up for holders "of record" is participant-level: a customer
    holding through a broker isn't a holder of record; so is an "Instead, ...
    rounded up" whose previous sentence limits the subject to holders of
    record."""
    if not _FRAC.search(s):
        return None
    s = s[:1500]
    if _OTHER.search(s):
        return None
    up = _UP.search(s)
    if up and _NEAREST.search(s) and not re.search(r"round(?:ed|ing)?\s+up(?:ward)?\s+to", s, re.I):
        return None                                  # "rounded to the nearest ... (one half rounded upward)"
    near = re.search(r"(?:of\s+record|record\s+holder|registered\s+holder).{0,150}?fraction|fraction.{0,150}?"
                     r"(?:of\s+record|record\s+holder|registered\s+holder)", prev[-400:], re.I)
    part = _PARTICIPANT.search(s) or (up and _INSTEAD.search(s) and near)
    cash = bool(_CASH.search(s)) and not _NO_CASH.search(s)
    if up and part:
        return "ROUNDUP_PARTICIPANT"
    if up and not cash:
        return "ROUNDUP_HOLDER"
    if up and cash:
        return "MIXED"                               # e.g. "cash, or rounded up at the board's election"
    if cash:
        return "CASH"
    if _DOWN.search(s):
        return "DOWN"
    return None


def fractional_class(t: str) -> tuple[str, str | None]:
    """The treatment of fractional shares a filing states, and the sentence it
    came from.

    Every sentence mentioning fractional shares is classified. One class
    throughout: that class. A round-up qualified elsewhere as participant-
    level: ROUNDUP_PARTICIPANT. Anything else that mixes round-up with cash or
    rounding down, or a MIXED sentence: UNKNOWN, since the filing doesn't
    commit to one treatment."""
    if _CASH_OUT.search(t):
        m = _CASH_OUT.search(t)
        return "CASH", t[max(0, m.start() - 200): m.end() + 200]
    ss = sentences(t)
    got = [(c, s) for i, s in enumerate(ss) for c in [sentence_class(s, ss[i - 1] if i else "")] if c]
    if not got:
        return "UNKNOWN", None
    n = pd.Series([c for c, _ in got]).value_counts()
    if "ROUNDUP_PARTICIPANT" in n and n.drop(["ROUNDUP_PARTICIPANT", "ROUNDUP_HOLDER"], errors="ignore").sum() == 0:
        n = pd.Series({"ROUNDUP_PARTICIPANT": n.sum()})
    # one treatment stated at least twice as often as every other: that one
    top = n.index[0]
    if top != "MIXED" and (len(n) == 1 or n.iloc[0] >= 2 * n.iloc[1:].sum()):
        return top, next(s for k, s in got if k == top)[:600]
    return "UNKNOWN", got[0][1][:600]


def _n(x: str) -> int | None:
    x = x.lower().replace(",", "").strip()
    return int(x) if x.isdigit() else _W.get(x)


def ratio(t: str) -> tuple[int | None, bool]:
    """(N, is_range) for the 1-for-N reverse split a filing describes.

    A ratio quoted inside a range ("not less than 1-for-3 and not more than
    1-for-6") doesn't count. A filing often states both the approved range
    and the ratio the board then chose: the chosen one wins. Only range
    ratios: (None, True)."""
    import spac_proxy as sp
    flat = sp._flat(t)
    final, rng = [], False
    ms = [m for m in _RATIO.finditer(flat)]
    for i, m in enumerate(ms):
        n = _n(next(g for g in m.groups() if g))
        ctx = flat[max(0, m.start() - 250): m.end() + 250]
        if not (n and 2 <= n <= 10000 and re.search(r"reverse|consolidat|combin", ctx, re.I)):
            continue
        near = [x for x in ms if x is not m and abs(x.start() - m.start()) < 120]   # "1 for 2, 1 for 5, ..."
        if _RANGE.search(flat[max(0, m.start() - 150): m.end() + 40]) or near:
            rng = True
        else:
            final.append(n)
    if final:
        return pd.Series(final).value_counts().index[0], False
    return None, rng


_AFTER_CLOSE = re.compile(r"(?:[4-9]|1[01])(?::\d\d)?\s*pm|11:59|close\s+of\s+(?:business|trading|the\s+market)|"
                          r"after\s+(?:the\s+)?(?:market\s+)?close|midnight", re.I)


def effective_date(t: str) -> str | None:
    """The first day the shares trade split-adjusted, as the filing states it.
    A stated split-adjusted trading date wins. Otherwise the legal effective
    date, moved to the next trading day when the time is after the close
    ("5:00 p.m.", "11:59 p.m.", "close of business") or the date isn't a
    trading day."""
    import spac_proxy as sp
    flat = sp._flat(t)
    for m in _SPLIT_ADJ.finditer(flat):
        d = sp._iso(m.group(1) or m.group(2))
        if d:
            return bday(d, 0)
    for m in _EFFECTIVE.finditer(flat):
        d = sp._iso(next(g for g in m.groups() if g))
        if d:
            late = _AFTER_CLOSE.search(flat[max(0, m.start()): m.end() + 60])
            return bday(d, 1) if late and bday(d, 0) == d else bday(d, 0)
    return None


def cancelled(t: str) -> bool:
    import spac_proxy as sp
    return bool(_CANCEL.search(sp._flat(t)))


def parse_filing(t: str) -> dict:
    c, sent = fractional_class(t)
    n, rng = ratio(t)
    return {"frac_class": c, "frac_sentence": sent, "ratio": n, "ratio_is_range": rng,
            "effective": effective_date(t), "cancel": cancelled(t)}


def _parse_one(r: dict) -> dict:
    out = {k: r[k] for k in ("permno", "exdt", "cik", "acc", "form", "filed", "accepted", "items")}
    try:
        if not _doc_path(r["acc"]).exists():
            return {**out, "status": "not_downloaded"}
        return {**out, "status": "ok", **parse_filing(text(r))}
    except Exception as e:                                # recorded, never silently dropped
        return {**out, "status": f"error {type(e).__name__}: {e}"}


def parse() -> int:
    """data/rsplit/parsed.csv: one row per (event, filing) with what the filing says."""
    from concurrent.futures import ProcessPoolExecutor
    f = pd.read_csv(CACHE / "filings.csv", dtype={"cik": str, "items": str})
    with ProcessPoolExecutor(8) as ex:
        rows = list(ex.map(_parse_one, [r.to_dict() for _, r in f.iterrows()], chunksize=32))
    p = pd.DataFrame(rows)
    p.to_csv(CACHE / "parsed.csv", index=False)
    print(p["status"].str[:20].value_counts().to_string())
    print(p.loc[p["status"] == "ok", "frac_class"].value_counts().to_string())
    return 0


# ------------------------------------------------------------------ what was known when

SPLIT = {"design": ("2010-01-01", "2018-12-31"), "confirm": ("2019-01-01", "2025-12-31")}
ROUNDUP = ("ROUNDUP_HOLDER", "ROUNDUP_PARTICIPANT")


def calendar() -> np.ndarray:
    ff = pd.read_parquet(ROOT / "data" / "pead" / "ff_daily.parquet")
    return np.array(sorted(ff["date"].astype(str)))


CAL = None


def bday(d: str, n: int) -> str:
    """n trading days after d (d itself if it is one and n = 0)."""
    global CAL
    if CAL is None:
        CAL = calendar()
    i = np.searchsorted(CAL, d)
    return str(CAL[min(max(i + n, 0), len(CAL) - 1)])


def settlement_days(trade_date: str) -> int:
    """T+3 before 2017-09-05, T+2 until 2024-05-28, T+1 after (odd_lot_study.py)."""
    from odd_lot_study import settlement_days as sd
    return sd(trade_date)


def timeline(p: pd.DataFrame) -> list[dict]:
    """An event's filings in acceptance order, each usable from the trading day
    after its acceptance date."""
    p = p[p["status"] == "ok"].sort_values(["accepted", "acc"])
    return [{"usable": bday(r.accepted, 1) if bday(r.accepted, 0) == r.accepted else bday(r.accepted, 0),
             "accepted": r.accepted, "acc": r.acc, "form": r.form,
             "cls": r.frac_class if isinstance(r.frac_class, str) else "UNKNOWN",
             # a proxy proposes (its ratios match CRSP 56% of the time in the design period, 8-Ks 95%):
             # only an 8-K states the terms the board chose
             "ratio": None if pd.isna(r.ratio) or r.ratio_is_range or not str(r.form).startswith("8-K") else int(r.ratio),
             "effective": r.effective if isinstance(r.effective, str) else None,
             "cancel": bool(r.cancel)} for r in p.itertuples()]


def known_at(tl: list[dict], t: str) -> dict:
    """Terms known on trading day t: the latest stated class, ratio and
    effective date among filings usable by t, and the announcement (the first
    filing stating both a ratio and an effective date)."""
    k = {"cls": "UNKNOWN", "ratio": None, "effective": None, "announced": None, "cancelled": False}
    for f in tl:
        if f["usable"] > t:
            break
        if f["cls"] != "UNKNOWN":
            k["cls"] = f["cls"]
        if f["ratio"]:
            k["ratio"] = f["ratio"]
        if f["effective"]:
            k["effective"] = f["effective"]
        if k["announced"] is None and f["ratio"] and f["effective"]:
            k["announced"] = f["usable"]
        k["cancelled"] = k["cancelled"] or f["cancel"]
    return k


# ------------------------------------------------------------------ payoff of one event

STUB = 0.90                        # spac_trial.py: a bid under 90% of the ask is a stub, not a quote
P_PRIMARY = {"ROUNDUP_HOLDER": 0.8, "ROUNDUP_PARTICIPANT": 0.4}   # ASSUMPTION: no published source
P_SENS = (1.0, 0.5, 0.2)
FEE_SENS = 5.0                     # $ a trade (sensitivity); primary $0
EXIT_LAG = 5                       # the "5 trading days later" exit sensitivity


def quotes(px: pd.DataFrame) -> pd.DataFrame:
    """Valid closing quotes only: a stub (bid < 90% of ask) is no quote."""
    q = px.copy()
    for c in ("dlybid", "dlyask", "dlyopen", "dlyprc"):
        q[c] = pd.to_numeric(q[c], errors="coerce").astype(float)
    ok = (q["dlybid"] > 0) & (q["dlyask"] >= q["dlybid"]) & (q["dlybid"] >= STUB * q["dlyask"])
    q["bid"], q["ask"] = q["dlybid"].where(ok), q["dlyask"].where(ok)
    return q.set_index("dlycaldt").sort_index()


def entry_day(tl: list[dict], days: list[str]) -> tuple[str | None, dict | None]:
    """The entry day, decided one day at a time with what is known that day:
    the first day on which today's trade settles before the known effective
    date and tomorrow's would not (so today is the last chance), no earlier
    than the day the announcement is usable. None if no such day exists.

    Choosing "the last qualifying day" over the whole window instead would
    use a later postponement to pick the entry: a lookahead."""
    for d in days:
        k = known_at(tl, d)
        if not k["announced"] or d < k["announced"] or not k["effective"] or k["cancelled"]:
            continue
        nxt = bday(d, 1)
        if bday(d, settlement_days(d)) < k["effective"] <= bday(nxt, settlement_days(nxt)):
            return d, k
    return None, None


def first_bid_on_or_after(q: pd.DataFrame, d: str, col: str = "bid") -> tuple[str | None, float]:
    s = q.loc[q.index >= d, col].dropna()
    return (s.index[0], float(s.iloc[0])) if len(s) else (None, np.nan)


def payoff(ev: dict, tl: list[dict], q: pd.DataFrame, perfect: bool = False) -> dict | None:
    """One share through one event. Rounded up: the post-split share at the
    bid. Not rounded up: cash for 1/N of a share at that bid / N. A change of
    terms or a cancellation after entry: sold at the bid on the first trading
    day the filing is usable. Settling on or after the actual effective date:
    no round-up (counted)."""
    eff = ev["exdt"]                                    # the actual first split-adjusted day (CRSP)
    days = [d for d in q.index if d < eff]
    if perfect:
        cands = [(d, known_at(tl, d)) for d in days]
        cands = [(d, k) for d, k in cands if k["announced"] and d >= k["announced"] and k["effective"]
                 and bday(d, settlement_days(d)) < k["effective"] and q.at[d, "ask"] == q.at[d, "ask"]]
        if not cands:
            return None
        d, k = min(cands, key=lambda x: q.at[x[0], "ask"])
    else:
        d, k = entry_day(tl, days)
        if d is None:
            return None
    if k["cls"] not in ROUNDUP:
        return None
    ask = q.at[d, "ask"] if d in q.index else np.nan
    if not ask == ask:
        return {"entry_day": d, "skipped": "no_valid_quote"}
    n = k["ratio"] or ev["n_crsp"]
    out = {"entry_day": d, "cls": k["cls"], "ratio_entry": n, "eff_known": k["effective"], "ask": ask}
    # a later filing that cancels or changes the terms, usable before the split
    for f in tl:
        if d < f["usable"] < eff and (f["cancel"] or (f["ratio"] and f["ratio"] != k["ratio"])
                                      or (f["effective"] and f["effective"] != k["effective"])
                                      or f["cls"] in ("CASH", "DOWN")):
            x, b = first_bid_on_or_after(q, f["usable"])
            if x is not None and x < eff:
                return {**out, "exit": "changed", "exit_day": x, "rounded": b, "cash": b, "settle_miss": False}
    settle_miss = bday(d, settlement_days(d)) >= eff
    x, b = first_bid_on_or_after(q, eff)
    if x is None:
        return {**out, "skipped": "no_exit_quote"}
    o = q.loc[q.index >= eff].iloc[0]
    open_mid = o["dlyopen"] - (o["ask"] - o["bid"]) / 2 if o["bid"] == o["bid"] else np.nan
    later = [i for i in q.index if i >= eff]
    x5, b5 = first_bid_on_or_after(q, later[min(EXIT_LAG, len(later) - 1)]) if later else (None, np.nan)
    return {**out, "exit": "split", "exit_day": x, "rounded": b, "cash": b / ev["n_crsp"], "settle_miss": settle_miss,
            "rounded_open": open_mid, "rounded_5d": b5, "n_crsp": ev["n_crsp"]}


def value(r: dict, p: float, fee: float = 0.0, col: str = "rounded") -> float:
    """Expected P&L of one event at round-up probability p."""
    if r.get("exit") == "changed":
        return r["rounded"] - r["ask"] - 2 * fee
    if r["settle_miss"]:
        p = 0.0
    up = r[col] - r["ask"] - 2 * fee
    cash = r[col] / r["n_crsp"] - r["ask"] - fee
    return p * up + (1 - p) * cash


def break_even_p(r: dict) -> float:
    """The p at which the event's expected P&L is zero."""
    up, cash = r["rounded"] - r["ask"], r["rounded"] / r["n_crsp"] - r["ask"]
    return (0 - cash) / (up - cash) if up != cash else np.nan


# ------------------------------------------------------------------ event table, checks, gate

def event_table() -> pd.DataFrame:
    """Per event: the terms known the day before the split (class, ratio,
    effective date), the announcement day, and whether the CRSP factor
    matches the filing ratio. No prices."""
    ev = events()
    P = pd.read_csv(CACHE / "parsed.csv", dtype={"cik": str, "items": str})
    by = {k: g for k, g in P.groupby(["permno", "exdt"])}
    rows = []
    for r in ev.itertuples():
        g = by.get((r.permno, r.exdt))
        tl = timeline(g) if g is not None else []
        k = known_at(tl, bday(r.exdt, -1))
        rows.append({"permno": r.permno, "exdt": r.exdt, "cik": r.cik, "issuer": r.issuernm, "n_crsp": r.n_crsp,
                     "filings": len(tl), "cls": k["cls"], "ratio": k["ratio"], "effective": k["effective"],
                     "announced": k["announced"], "cancel_seen": k["cancelled"],
                     "ratio_match": (abs(k["ratio"] - r.n_crsp) / r.n_crsp < 0.02) if k["ratio"] else np.nan,
                     "effective_match": (k["effective"] == r.exdt) if k["effective"] else np.nan,
                     "period": "design" if r.exdt <= SPLIT["design"][1] else "confirm"})
    return pd.DataFrame(rows)


def validate() -> int:
    """Automated checks (rates and the factor mismatch list; no prices), and
    the class counts per year for both periods (counts only)."""
    e = event_table()
    e.to_csv(CACHE / "event_table.csv", index=False)
    out = []
    for per, g in e.groupby("period"):
        out.append({"period": per, "events": len(g), "with_cik": float(g["cik"].notna().mean()),
                    "with_filings": float((g["filings"] > 0).mean()),
                    "with_announcement": float(g["announced"].notna().mean()),
                    "ratio_found": float(g["ratio"].notna().mean()),
                    "ratio_matches_crsp": float(g["ratio_match"].dropna().astype(float).mean()),
                    "effective_found": float(g["effective"].notna().mean()),
                    "effective_matches_crsp": float(g["effective_match"].dropna().astype(float).mean()),
                    **{f"cls_{c}": int((g["cls"] == c).sum()) for c in CLASSES}})
    v = pd.DataFrame(out)
    print(v.T.to_string())
    v.to_csv(ROOT / "reports" / "rsplit_events_check.csv", index=False)
    mm = e[e["ratio_match"] == False][["permno", "exdt", "issuer", "n_crsp", "ratio", "period"]]  # noqa: E712
    mm.to_csv(CACHE / "factor_mismatches.csv", index=False)       # CRSP rows: git-ignored, never in reports/
    v["factor_mismatches"] = v["period"].map(mm["period"].value_counts()).fillna(0).astype(int)
    v.to_csv(ROOT / "reports" / "rsplit_events_check.csv", index=False)
    print(f"{len(mm)} CRSP factor / filing ratio mismatches -> data/rsplit/factor_mismatches.csv (git-ignored)")
    c = e.assign(year=e["exdt"].str[:4]).pivot_table(index="year", columns="cls", values="permno", aggfunc="count",
                                                       fill_value=0)
    c.to_csv(ROOT / "reports" / "rsplit_class_counts.csv")
    print(c.to_string())
    return 0


def simulate(period: str, perfect: bool = False) -> pd.DataFrame:
    """The payoff of every round-up event in a period (one share each)."""
    lo, hi = SPLIT[period]
    md = da.guard("reverse_split")
    e = pd.read_csv(CACHE / "event_table.csv")
    e = e[(e["exdt"] >= lo) & (e["exdt"] <= min(hi, md))]
    ev = events().set_index(["permno", "exdt"])
    P = pd.read_csv(CACHE / "parsed.csv", dtype={"cik": str, "items": str})
    by = {k: g for k, g in P.groupby(["permno", "exdt"])}
    px = pd.read_parquet(CACHE / "crsp_daily.parquet")
    pg = {p: g for p, g in px.groupby("permno")}
    rows = []
    for r in e.itertuples():
        g = by.get((r.permno, r.exdt))
        if g is None or r.permno not in pg:
            continue
        tl = timeline(g)
        lo_, hi_ = bday(r.exdt, -100), bday(r.exdt, 15)
        q = quotes(pg[r.permno][(pg[r.permno]["dlycaldt"] >= lo_) & (pg[r.permno]["dlycaldt"] <= hi_)])
        o = payoff({"exdt": r.exdt, "n_crsp": r.n_crsp}, tl, q, perfect=perfect)
        if o is not None:
            rows.append({"permno": r.permno, "exdt": r.exdt, **o})
    return pd.DataFrame(rows)


def monthly(t: pd.DataFrame, pnl: pd.Series, lo: str, hi: str) -> pd.Series:
    months = pd.period_range(lo[:7], hi[:7], freq="M").astype(str)
    m = pnl.groupby(t["exit_day"].str[:7]).sum()
    return m.reindex(months, fill_value=0.0)


def summarize(t: pd.DataFrame, period: str, p: dict, fee: float = 0.0, col: str = "rounded",
              label: str = "") -> dict:
    from deflated_sharpe import pass_probability
    lo, hi = SPLIT[period]
    tr = t[t.get("skipped").isna()] if "skipped" in t else t
    pnl = pd.Series([value(r, p.get(r["cls"], 0.0), fee, col) for r in tr.to_dict("records")], index=tr.index)
    m = monthly(tr, pnl, lo, hi)
    years = len(m) / 12
    sd = m.std()
    sharpe = m.mean() / sd * np.sqrt(12) if sd > 0 else np.nan
    row = {"run": label, "period": period, "events_traded": len(tr), "skipped": int(len(t) - len(tr)),
           "usd_per_yr": float(pnl.sum() / years), "monthly_sharpe": float(sharpe),
           "prior_sharpe": float(sharpe / 2), "p_pass_7y": float(pass_probability(sharpe / 2, 7.0)),
           "median_event": float(pnl.median()) if len(pnl) else np.nan,
           "p10_event": float(pnl.quantile(0.1)) if len(pnl) else np.nan,
           "worst_event": float(pnl.min()) if len(pnl) else np.nan,
           "return_on_capital_median": float((pnl / tr["ask"]).median()) if len(pnl) else np.nan,
           "changed_exits": int((tr["exit"] == "changed").sum()), "settle_misses": int(tr["settle_miss"].sum()),
           "top5_share": float(pnl.nlargest(5).sum() / pnl[pnl > 0].sum()) if (pnl > 0).any() else np.nan}
    for c in ROUNDUP:
        x = tr[tr["cls"] == c]
        row[f"events_{c}"] = len(x)
        if len(x):
            up = (x[col] - x["ask"]).sum()
            cash = (x[col] / x["n_crsp"].fillna(1) - x["ask"]).sum()
            row[f"break_even_p_{c}"] = float(-cash / (up - cash)) if up != cash else np.nan
    return row


def gate() -> int:
    """Phase 2, design period: (a) ceiling at p = 1, then the declared rule at
    the primary p, against $500/yr; (b) halved design Sharpe into P(pass)
    over 7 confirmation years; (c) return on capital (benchmark zero)."""
    t = simulate("design")
    c = simulate("design", perfect=True)
    t.to_csv(CACHE / "trades_design_gate.csv", index=False)
    rows = [summarize(c, "design", {k: 1.0 for k in ROUNDUP}, label="ceiling_p1_perfect"),
            summarize(t, "design", P_PRIMARY, label="primary")]
    for pp in P_SENS:
        rows.append(summarize(t, "design", {k: pp for k in ROUNDUP}, label=f"p_{pp}"))
    rows += [summarize(t, "design", P_PRIMARY, fee=FEE_SENS, label="fee_5"),
             summarize(t, "design", P_PRIMARY, col="rounded_open", label="exit_open_minus_half_spread"),
             summarize(t, "design", P_PRIMARY, col="rounded_5d", label="exit_5_days_later")]
    g = pd.DataFrame(rows)
    g.to_csv(ROOT / "reports" / "rsplit_gate.csv", index=False)
    print(g.T.to_string())
    a = g.iloc[0]["usd_per_yr"] >= 500 and g.iloc[1]["usd_per_yr"] >= 500
    b = g.iloc[1]["p_pass_7y"] >= 0.25
    print(f"\n(a) ceiling ${g.iloc[0]['usd_per_yr']:.0f}/yr, rule ${g.iloc[1]['usd_per_yr']:.0f}/yr: "
          f"{'PASS' if a else 'KILL'}   (b) P(pass) {g.iloc[1]['p_pass_7y']:.3f}: {'PASS' if b else 'KILL'}")
    return 0


def main(a) -> int:
    return {"crsp": crsp, "filings": filings, "parse": parse, "validate": validate, "gate": gate}[a.cmd]()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd")
    sys.exit(main(ap.parse_args()))
