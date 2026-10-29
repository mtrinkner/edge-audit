#!/usr/bin/env python3
"""SPAC redemption events from EDGAR filings (trial 60). See docs/SPAC_TRUST_ARB.md.

    python3 python/spac_proxy.py download [design|confirm]   # SPAC-phase proxies and 8-Ks (SEC, cached)
    python3 python/spac_proxy.py parse                       # data/spac/{meetings,deadlines,paid}.csv

What each filing gives, and from when it is usable (the trading day AFTER its
filing date, like every other EDGAR value here):

  definitive proxy (DEF 14A, DEFM14A, DEFR14A, 424B3 merger prospectus):
      meeting date, whether public holders may redeem at it (extension and
      business-combination meetings: yes; an ordinary annual meeting: no),
      the redemption deadline, the stated per-share redemption amount.
  DEFA14A / 8-K before the meeting:
      postponements and adjournments ("postponed to <date>"), applied from
      their own filing date; a revised per-share amount.
  8-K after the meeting (OUTCOME):
      whether an extension was approved and to when (the new charter deadline,
      usable the day after that 8-K), the per-share amount PAID to redeeming
      holders, and the payment date when stated. A liquidation 8-K gives the
      liquidation amount and payment date.
  8-K automatic extension (sponsor deposit, no vote): the new deadline.

Primary documents only for the long forms (DEFM14A, 424B3 run to tens of
megabytes with graphics); full submissions for 8-Ks, whose press release
(EX-99.1) usually carries the numbers. Cached gzipped in data/spac/docs/.
"""

from __future__ import annotations

import gzip
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "python"))

import data_access as da
import edgar_tenders as et
import spac_edgar as se

CACHE = se.CACHE
DOCS = CACHE / "docs"
PROXY_FORMS = ("DEF 14A", "DEFM14A", "DEFR14A", "424B3")
SUPPLEMENT_FORMS = ("DEFA14A",)
EIGHTK_FORMS = ("8-K", "8-K/A")
FULL_SUBMISSION = set(EIGHTK_FORMS) | set(SUPPLEMENT_FORMS)     # proxies: the primary document (can exceed 5 MB)
AFTER_END_DAYS = 60                 # liquidation and redemption-result 8-Ks follow the SPAC phase's end
# 8-K items that can carry a meeting, postponement, extension, redemption or
# liquidation; an 8-K with none of them (only 1.01, 2.02, 5.02, 9.01, ...) is not read.
EIGHTK_ITEMS = {"5.07", "5.03", "8.01", "7.01", "2.01", "3.01"}


# ------------------------------------------------------------------ download

def windows() -> pd.DataFrame:
    """(permno, cik, ipo, end, merged) of every linked SPAC: its SPAC phase,
    from spac_trust's phase_end, else its last CRSP day."""
    import spac_trust as st
    L = pd.read_csv(CACHE / "links.csv", dtype=str)
    F = pd.read_csv(CACHE / "filings.csv", dtype=str)
    ev, ren = st.events(L, F), st.renamed_on(L)
    px = pd.read_parquet(CACHE / "crsp_daily.parquet", columns=["permno", "dlycaldt"])
    last = px.groupby("permno")["dlycaldt"].max()
    cal = np.array(sorted(px["dlycaldt"].unique()))
    rows = []
    for p, e in ev.items():
        if p not in last.index:
            continue
        end, merged = st.phase_end(e, ren.get(p), last[p], cal)
        rows.append({"permno": p, "cik": e["cik"], "ipo": e["ipo"], "end": end or last[p],
                     "last_crsp": last[p], "merged": merged})
    return pd.DataFrame(rows)


def scope(which: str | None = None) -> pd.DataFrame:
    """The filings to read: proxies, supplements and 8-Ks a SPAC filed during its
    SPAC phase, plus AFTER_END_DAYS for the results of its final meeting."""
    md = da.guard("spac_proxy")
    W = windows()
    F = pd.read_csv(CACHE / "filings.csv", dtype=str)
    f = F[F["form"].isin(PROXY_FORMS + SUPPLEMENT_FORMS + EIGHTK_FORMS)].merge(W, on="cik")
    lim = (pd.to_datetime(f["end"]) + pd.Timedelta(days=AFTER_END_DAYS)).dt.strftime("%Y-%m-%d")
    f = f[(f["filed"] > f["ipo"]) & (f["filed"] <= lim) & (f["filed"] <= md)]
    k = f["form"].isin(EIGHTK_FORMS)
    f = f[~k | f["items"].fillna("").map(lambda s: bool(EIGHTK_ITEMS & set(s.split(","))))]
    if which == "design":
        f = f[f["ipo"] <= "2020-12-31"]
    elif which == "confirm":
        f = f[f["ipo"] > "2020-12-31"]
    return f.drop_duplicates("acc").reset_index(drop=True)


def _path(r) -> tuple[str, Path]:
    acc = r["acc"]
    if r["form"] in FULL_SUBMISSION:
        url = f"https://www.sec.gov/Archives/edgar/data/{r['cik']}/{acc}.txt"
    else:
        url = f"https://www.sec.gov/Archives/edgar/data/{r['cik']}/{acc.replace('-', '')}/{r['doc']}"
    return url, DOCS / f"{acc}.gz"


import threading
_LOCK = threading.Lock()


def _fetch(url: str) -> bytes:
    """et.fetch's pacing and User-Agent, without its retries on a 404 (a
    primary document that moved): those cost 14 seconds each."""
    import time
    import urllib.error
    import urllib.request
    with _LOCK:                                           # threads share SEC's 8-a-second budget
        wait = et.MIN_INTERVAL - (time.monotonic() - et._last_request)
        if wait > 0:
            time.sleep(wait)
        et._last_request = time.monotonic()
    req = urllib.request.Request(url, headers={"User-Agent": et.USER_AGENT, "Accept-Encoding": "identity"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.read()
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise
        return et.fetch(url)                              # 429/5xx: et.fetch's back-off


def text(r) -> str:
    """Cleaned text of a filing (every document of a full submission, joined)."""
    url, out = _path(r)
    if not out.exists() and r["form"] in FULL_SUBMISSION:
        # The filing's text documents only (8-K body, EX-99 press releases):
        # the full submission also carries uuencoded images, ~10x the bytes.
        import json
        base = f"https://www.sec.gov/Archives/edgar/data/{r['cik']}/{r['acc'].replace('-', '')}/"
        idx = json.loads(_fetch(base + "index.json"))["directory"]["item"]
        names = [x["name"] for x in idx if re.search(r"\.(htm|html|txt)$", x["name"], re.I)
                 and not x["name"].startswith(r["acc"]) and "index" not in x["name"].lower()
                 and int(x.get("size") or 0) < 5_000_000]
        raw = b"".join(b"<DOCUMENT>\n<TYPE>TEXT\n<TEXT>\n" + _fetch(base + n) + b"\n</TEXT>\n</DOCUMENT>\n"
                       for n in names)
        out.parent.mkdir(parents=True, exist_ok=True)
        tmp = out.with_suffix(".part")
        tmp.write_bytes(gzip.compress(raw))
        tmp.rename(out)
    if not out.exists():
        try:
            raw = _fetch(url)
        except Exception as e:                            # primary document missing: the full submission
            if "404" not in str(e) or url.endswith(".txt"):
                raise
            raw = _fetch(f"https://www.sec.gov/Archives/edgar/data/{r['cik']}/{r['acc']}.txt")
        out.parent.mkdir(parents=True, exist_ok=True)
        tmp = out.with_suffix(".part")
        tmp.write_bytes(gzip.compress(raw))
        tmp.rename(out)
    raw = gzip.decompress(out.read_bytes()).decode("latin-1")
    if "<DOCUMENT>" in raw:
        parts = []
        for d in re.findall(r"<DOCUMENT>(.*?)</DOCUMENT>", raw, re.S):
            ty = (re.search(r"<TYPE>([^\n]+)", d) or [None, ""])[1].strip().upper()
            body = re.search(r"<TEXT>(.*)</TEXT>", d, re.S)
            body = body.group(1) if body else ""
            if ty in ("GRAPHIC", "ZIP", "XML", "JSON", "EXCEL") or ty.startswith("EX-101") or "begin 6" in body[:300]:
                continue
            parts.append(et.clean(body))
        return "\n".join(parts)
    return et.clean(raw)


def download(which: str | None = None) -> int:
    f = scope(which)
    todo = [r for _, r in f.iterrows() if not _path(r)[1].exists()]
    print(f"{len(f):,} filings in scope ({which or 'all'}); {len(todo):,} not cached", flush=True)
    import time
    from concurrent.futures import ThreadPoolExecutor

    def one(r) -> int:
        for attempt in range(6):
            try:
                text(r)
                return 0
            except Exception as e:                   # DNS or connection drop: wait it out
                if "404" in str(e) or attempt == 5:
                    print(f"  FAILED {r['acc']} {r['form']}: {e}", flush=True)
                    return 1
                time.sleep(15 * (attempt + 1))

    failed = 0
    with ThreadPoolExecutor(6) as ex:             # latency-bound; the lock keeps it under 8 requests a second
        for i, k in enumerate(ex.map(one, todo), 1):
            failed += k
            if i % 500 == 0:
                print(f"  {i:,}/{len(todo):,}", flush=True)
    print(f"done; {failed} failed", flush=True)
    return 0


# ------------------------------------------------------------------ parsers

MONTH = r"(?:January|February|March|April|May|June|July|August|September|October|November|December)"
DATE_RE = rf"{MONTH}\s+\d{{1,2}},?\s+\d{{4}}"
_HELD = re.compile(rf"(?:to|will|shall)\s+be\s+held\b.{{0,300}}?({DATE_RE})", re.I | re.S)
_MEETING_ON = re.compile(rf"(?:special|annual|extraordinary\s+general|general)\s+meeting[^.]{{0,200}}?\bon\s+"
                         rf"(?:\w+day,\s+)?({DATE_RE})", re.I)
_DEADLINE = re.compile(rf"(?:redeem|redemption|tender)[^$]{{0,600}}?(?:5:00|4:30|4:00|5\s*p\.m|five)\s*.{{0,60}}?"
                       rf"(?:on|,)\s+(?:\w+day,\s+)?({DATE_RE})", re.I | re.S)
_EXTENSION = re.compile(r"extend\s+(?:the\s+)?(?:date|period|deadline|time)|extension\s+(?:amendment|proposal)|"
                        r"\bextension\s+of\s+the\s+(?:date|period|deadline|time)", re.I)
_BC = re.compile(r"business\s+combination\s+proposal|approve\s+(?:and\s+adopt\s+)?(?:the\s+)?(?:merger|business\s+combination|"
                 r"acquisition|share\s+exchange|transaction)\s+agreement|adopt\s+the\s+(?:merger|business\s+combination)\s+agreement",
                 re.I)
# Older SPACs call it a conversion right; either way holders get the pro rata trust.
_REDEEM_RIGHT = re.compile(r"redemption\s+rights?|conversion\s+rights?|(?:right|entitled|elect|may|request)\s+(?:to\s+)?"
                           r"(?:have\s+.{0,60}?)?(?:redeem|convert)", re.I)
_AMOUNT = re.compile(r"\$\s?(\d{1,2}\.\d{2,4})\)?\s*(?:per|for\s+each)\s+(?:public\s+|Class\s+A\s+|ordinary\s+|issued\s+and\s+outstanding\s+)?"
                     r"(?:share|unit)", re.I)
_REDEMPTION_CTX = re.compile(r"redemption|redeem|pro\s+rata|trust\s+account|liquidat", re.I)


def _flat(s: str) -> str:
    s = re.sub(r"\s+", " ", s)
    return re.sub(r"\b([ap])\.\s?m\.", r"\1m", s, flags=re.I)     # "10:00 a.m." ends no sentence


def _iso(s: str) -> str | None:
    d = et._dates(s.replace(" ,", ","))
    if d:
        return d[0]
    m = re.match(rf"({MONTH})\s+(\d{{1,2}}),?\s+(\d{{4}})", s, re.I)
    try:
        return pd.Timestamp(f"{m.group(1)} {m.group(2)} {m.group(3)}").strftime("%Y-%m-%d") if m else None
    except ValueError:
        return None


def amounts_near_redemption(flat: str, lo=5.0, hi=12.5, window=250) -> list[float]:
    """Per-share dollar amounts ($X per share) with redemption or trust language
    within `window` characters before them, in order of appearance. Par values
    ($0.0001) and warrant prices fall outside [lo, hi]."""
    out = []
    for m in _AMOUNT.finditer(flat):
        v = float(m.group(1))
        near = flat[max(0, m.start() - 120): m.end() + 40]
        if _NOT_REDEMPTION.search(near) or re.search(r"conversion\s+price", near, re.I):
            continue                                  # $11.50 warrant strikes, unit and PIPE prices
        if lo <= v <= hi and _REDEMPTION_CTX.search(flat[max(0, m.start() - window): m.end() + 60]):
            out.append(v)
    return out


_PER_SHARE_PRICE = re.compile(r"per\s?-\s?share\s+(?:redemption\s+)?(?:price|amount)[^$]{0,200}?(?:redeem|redemption|trust)"
                              r"[^$]{0,200}?\$\s?(\d{1,2}\.\d{2,4})", re.I)


def stated_amount(flat: str) -> float | None:
    """The per-share redemption amount a proxy states.

    Proxies repeat a generic "$10.00 per share" (the IPO deposit) next to the
    current estimate "approximately $10.08 per share, based on the trust as of
    <date>". The most common value that is NOT the round IPO amount wins when
    there is one; else the most common value."""
    cur = re.search(r"current\s+(?:conversion|redemption)\s+(?:amount|price)\s+(?:of\s+)?(?:approximately\s+)?"
                    r"\$\s?(\d{1,2}\.\d{2,4})", flat[:400000], re.I)
    if cur and 5 <= float(cur.group(1)) <= 12.5:
        return float(cur.group(1))                # an extension proxy: what redeeming now pays, not the
                                                  # post-contribution figure for a later meeting
    # "the per-share price at which public shares will be redeemed ... will be approximately $10.44"
    pr = [float(m.group(1)) for m in _PER_SHARE_PRICE.finditer(flat[:400000])
          if 5 <= float(m.group(1)) <= 12.5 and not _NOT_REDEMPTION.search(flat[max(0, m.start(1) - 120): m.end()])]
    if pr:
        return float(pd.Series(pr).value_counts().index[0])
    v = amounts_near_redemption(flat[:400000])
    if not v:
        return None
    s = pd.Series(v)
    odd = s[(s * 100 % 100).round(4) != 0]      # not a round $10.00 / $11.00
    s = odd if len(odd) else s
    return float(s.value_counts().index[0])


def parse_proxy(t: str, form: str) -> dict:
    """Meeting date, redemption right, redemption deadline and stated amount
    from a definitive proxy. A 424B3 counts only if it opens as a meeting
    notice (else it is a resale or other prospectus)."""
    flat = _flat(t)
    head = flat[:60000]
    m = _HELD.search(head) or _MEETING_ON.search(head)
    meeting = _iso(m.group(1)) if m else None
    if form == "424B3" and not re.search(r"special\s+meeting|extraordinary\s+general\s+meeting", flat[:30000], re.I):
        return {"meeting": None, "kind": "not_a_proxy"}
    if form in ("DEFM14A", "424B3"):
        kind = "business_combination"
    elif _EXTENSION.search(head):
        kind = "extension"
    elif _BC.search(head):
        kind = "business_combination"
    else:
        kind = "annual"
    right = kind != "annual" and bool(_REDEEM_RIGHT.search(flat[:400000]))
    dl = _DEADLINE.search(flat[:400000])
    deadline = _iso(dl.group(1)) if dl else None
    if deadline and meeting and not (pd.Timestamp(meeting) - pd.Timedelta(days=10) <= pd.Timestamp(deadline) <= pd.Timestamp(meeting)):
        deadline = None                                    # a different date in the same sentence
    return {"meeting": meeting, "kind": kind, "redemption_right": right, "redemption_deadline": deadline,
            "stated_amount": stated_amount(flat)}


_POSTPONE = re.compile(rf"(?:postpone|adjourn|reschedul|reconven)\w*[^.]{{0,300}}?(?:to|until|on|for)\s+(?:\w+day,\s+)?({DATE_RE})", re.I)
_EXT_DONE = re.compile(rf"(?:approved|deposited|has\s+(?:been\s+)?extended|have\s+extended|elected\s+to\s+extend|"
                       rf"extended\s+the|extension\s+(?:was|has\s+been)\s+(?:approved|effected))"
                       rf"[^.]{{0,500}}?(?:to|until|through)\s+({DATE_RE})", re.I)
_LIQ = re.compile(r"will\s+not\s+(?:be\s+able\s+to\s+)?(?:consummate|complete)\s+(?:an|its|a)\s+(?:initial\s+)?business\s+combination|"
                  r"redeem\s+all\s+(?:of\s+)?(?:the\s+)?(?:its\s+)?outstanding\s+(?:public\s+)?(?:shares|ordinary|Class|common)|"
                  r"(?:dissolve|dissolution)\s+and\s+liquidat|wind\s+up|wind-up", re.I)
_LIQ_DATE = re.compile(rf"(?:on\s+or\s+about|as\s+of|on|effective)\s+(?:\w+day,\s+)?({DATE_RE})", re.I)
_REDEEMED = re.compile(r"(\d{1,3}(?:,\d{3})+|\d+)\s+(?:of\s+the\s+Company's\s+)?(?:public\s+|Class\s+A\s+|ordinary\s+)*(?:shares|ordinary\s+shares)"
                       r"[^.]{0,250}?(?:redeem|redemption|tendered)[^.]{0,250}?\$\s?([\d,]+(?:\.\d+)?)\s*(million)?", re.I)


_REDEEMED_ON = re.compile(rf"(?:be|been|were|was)\s+redeemed\s+(?:on|as\s+of)\s+(?:or\s+about\s+)?(?:\w+day,\s+)?({DATE_RE})"
                          rf"|redemption\s+date[^.]{{0,40}}?({DATE_RE})", re.I)
_PAID = re.compile(r"(?:redeemed|redemption\s+price|conversion\s+amount|redemption\s+amount|redemption\s+payment|"
                   r"exercised\s+their\s+right\s+to\s+redeem)[^.$]{0,160}?\$\s?(\d{1,2}\.\d{2,4})\s*(?:per|for\s+each)", re.I)
_NOT_REDEMPTION = re.compile(r"warrant|exercise|strike|subscription|PIPE|private\s+placement|purchase\s+price|"
                             r"offering\s+price|per\s+unit|earn-?out|trading\s+price|closing\s+price|equals\s+or\s+exceeds", re.I)


_LIQ_AMOUNT = re.compile(r"per[- ]share\s+(?:redemption|liquidation)\s+(?:price|amount)[^$]{0,120}?\$\s?(\d{1,2}\.\d{2,4})", re.I)


def paid_amount(flat: str) -> float | None:
    """The per-share amount an 8-K says redeeming holders were (or will be)
    paid: tied to "redeemed", "redemption price" or "conversion amount", never a
    forward estimate ("will be approximately $10.38 ... at liquidation")."""
    for m in _PAID.finditer(flat):
        v = float(m.group(1))
        before = flat[max(0, m.start() - 60): m.start(1)]
        if _NOT_REDEMPTION.search(flat[max(0, m.start(1) - 120): m.end() + 30]):
            continue                                       # $11.50 warrant strikes, PIPE prices, price triggers
        if 5 <= v <= 12.5 and not re.search(r"will\s+be\s+approximately|would\s+be|anticipate|expect|estimate", before, re.I):
            return v
    return None


def parse_8k(t: str, filed: str) -> dict:
    """What an 8-K (or DEFA14A) says about redemption events. Any date it gives
    is used only from the day after `filed`."""
    flat = _flat(t)
    out = {}
    later = lambda d: d and d > filed
    pp = [_iso(m.group(1)) for m in _POSTPONE.finditer(flat)]
    pp = [d for d in pp if later(d) and d <= str(pd.Timestamp(filed) + pd.Timedelta(days=120))[:10]]
    if pp:
        out["postponed_to"] = max(pp)
    ex = [_iso(m.group(1)) for m in _EXT_DONE.finditer(flat) if re.search(r"extend|extension", m.group(0), re.I)]
    ex = [d for d in ex if later(d)]
    if ex:
        out["extended_to"] = max(ex)
    liq = _LIQ.search(flat)
    if liq:
        out["liquidation"] = True
        win = flat[max(0, liq.start() - 1500): liq.end() + 1500]
        # the last "will be redeemed on <date>" is the payment of the remaining shares
        rd = [_iso(m.group(1) or m.group(2)) for m in _REDEEMED_ON.finditer(flat)]
        rd = [d for d in rd if d and d >= filed]
        ld = [d for d in (_iso(m.group(1)) for m in _LIQ_DATE.finditer(win)) if d and d >= filed]
        if rd or ld:
            out["liquidation_date"] = max(rd) if rd else min(ld)
    if liq:
        # a liquidation notice states its amount ahead of payment, so forward wording is allowed
        la = _LIQ_AMOUNT.search(flat)
        if la and 5 <= float(la.group(1)) <= 12.5:
            out["liquidation_amount"] = float(la.group(1))
    paid = paid_amount(flat)
    am = amounts_near_redemption(flat, window=300)
    if paid:
        out["per_share"], out["per_share_from"] = paid, "redemption_price"
    elif am:
        out["per_share"] = float(pd.Series(am).value_counts().index[0])
    else:
        m = _REDEEMED.search(flat)
        if m:
            sh = float(m.group(1).replace(",", ""))
            usd = float(m.group(2).replace(",", "")) * (1e6 if m.group(3) else 1)
            if sh > 0 and 5 <= usd / sh <= 12.5:
                out["per_share"] = round(usd / sh, 4)
                out["per_share_from"] = "aggregate"
    return out


# ------------------------------------------------------------------ parse every cached filing

def _parse_one(r: dict) -> dict:
    out = {k: r[k] for k in ("cik", "permno", "acc", "form", "filed", "items", "report")}
    try:
        if not _path(r)[1].exists():
            return {**out, "status": "not_downloaded"}
        t = text(r)
        if r["form"] in PROXY_FORMS:
            return {**out, "status": "ok", "kind_of_filing": "proxy", **parse_proxy(t, r["form"])}
        return {**out, "status": "ok", "kind_of_filing": "update", **parse_8k(t, r["filed"])}
    except Exception as e:                                # recorded, never silently dropped
        return {**out, "status": f"error {type(e).__name__}: {e}"}


def parse(which: str | None = None) -> int:
    """data/spac/proxy_events.csv: one row per filing in scope, with what it says."""
    from concurrent.futures import ProcessPoolExecutor
    f = scope(which)
    with ProcessPoolExecutor(8) as ex:
        rows = list(ex.map(_parse_one, [r.to_dict() for _, r in f.iterrows()], chunksize=16))
    e = pd.DataFrame(rows)
    name = f"proxy_events{'_' + which if which else ''}.csv"
    e.to_csv(CACHE / name, index=False)
    print(e["status"].str[:20].value_counts().to_string())
    p = e[e["kind_of_filing"] == "proxy"]
    print(f"{len(p):,} proxies: meeting date {p['meeting'].notna().mean():.1%}; "
          f"stated amount {p['stated_amount'].notna().mean():.1%}; deadline {p['redemption_deadline'].notna().mean():.1%}")
    print(p["kind"].value_counts().to_string())
    print(f"wrote data/spac/{name}")
    return 0


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "parse"
    arg = sys.argv[2] if len(sys.argv) > 2 else None
    sys.exit({"download": download, "parse": parse}[cmd](*([arg] if arg else [])))
