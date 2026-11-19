#!/usr/bin/env python3
"""Step 4 paper screen: opportunity counts from EDGAR metadata only.
Protocol: docs/specs/step4_paper_screen.md. No prices are read here.

    python3 python/screen_step4.py index     # NT 10-K and 8-K rows of every quarterly form index
    python3 python/screen_step4.py items     # 8-K item codes per filer (data.sec.gov submissions)
    python3 python/screen_step4.py events    # the event list of each candidate (EDGAR only)
    python3 python/screen_step4.py sizes     # one WRDS connection: CIK -> permno and pre-event market cap
    python3 python/screen_step4.py screen    # data/screen_step4.csv -> reports/screen_step4.csv

Reuses edgar_tenders' polite fetch (same User-Agent, at most 8 requests a
second). Every response is cached under data/edgar/ (git-ignored); the
quarterly indexes keep only the forms below. Nothing after the lockbox date
is counted.
"""

from __future__ import annotations

import gzip
import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "python"))

import data_access as da
import edgar_tenders as et

FORMS = ("NT 10-K", "NT 10-K/A", "8-K", "8-K/A")
FIRST, LAST = (2010, 1), (2026, 3)
IDX = et.CACHE / "index_distress"
SUBS = et.CACHE / "submissions"


def index_rows(y: int, q: int) -> pd.DataFrame:
    path = IDX / f"{y}Q{q}.tsv"
    if not path.exists():
        raw = gzip.decompress(et.fetch(f"https://www.sec.gov/Archives/edgar/full-index/{y}/QTR{q}/form.gz"))
        rows = et.form_index_rows(raw.decode("latin-1"), FORMS)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join("\t".join(r) for r in rows) + "\n")
    return pd.read_csv(path, sep="\t", header=None, names=["form", "company", "cik", "date", "path"], dtype=str)


def build_index() -> pd.DataFrame:
    md = da.guard("screen_step4")
    parts = []
    for y, q in et.quarters(FIRST, LAST):
        r = index_rows(y, q)
        parts.append(r)
        print(f"  {y}Q{q}: {len(r)} rows", flush=True)
    f = pd.concat(parts)
    return f[f["date"] <= md].drop_duplicates("path")


def submissions(cik: str) -> list[dict]:
    """Every 8-K of one filer with its items, from the submissions JSON and its older pages."""
    SUBS.mkdir(parents=True, exist_ok=True)

    def get(name: str) -> dict:
        p = SUBS / name
        if not p.exists():
            p.write_bytes(gzip.compress(et.fetch(f"https://data.sec.gov/submissions/{name}")))
        return json.loads(gzip.decompress(p.read_bytes()))

    top = get(f"CIK{int(cik):010d}.json")
    pages = [top["filings"]["recent"]] + [get(f["name"]) for f in top["filings"].get("files", [])]
    out = []
    for pg in pages:
        for form, date, items, acc in zip(pg["form"], pg["filingDate"], pg["items"], pg["accessionNumber"]):
            if form in ("8-K", "8-K/A"):
                out.append({"cik": cik, "form": form, "date": date, "items": items, "accession": acc})
    return out


def build_items(idx: pd.DataFrame) -> pd.DataFrame:
    ciks = sorted(idx.loc[idx["form"].isin(["8-K", "8-K/A"]), "cik"].unique(), key=int)
    rows = []
    for i, c in enumerate(ciks):
        try:
            rows += submissions(c)
        except Exception as e:                       # a filer with no submissions file
            print(f"  {c}: {e}", flush=True)
        if i % 500 == 0:
            print(f"  {i}/{len(ciks)} filers", flush=True)
    return pd.DataFrame(rows)


def tender_targets() -> pd.DataFrame:
    """Original SC TO-T offers and their target: the party that files an SC 14D9
    within 10 days before to 30 days after the offer. One row per offer."""
    f = pd.read_csv(et.CACHE / "filings_tot.csv", dtype=str)
    to = f[f["form"] == "SC TO-T"]
    d9 = f[f["form"] == "SC 14D9"][["cik", "date"]].rename(columns={"date": "d9"})
    m = to.merge(d9, on="cik")
    dd = (pd.to_datetime(m["d9"]) - pd.to_datetime(m["date"])).dt.days
    m = m[(dd >= -10) & (dd <= 30)]
    t = m.sort_values("d9").drop_duplicates("accession")[["accession", "cik", "company", "date"]]
    return t.reset_index(drop=True)


def events() -> pd.DataFrame:
    """Every candidate's events from EDGAR metadata: candidate, cik/permno, date."""
    md = da.guard("screen_step4")
    out = []
    d = pd.read_csv(et.CACHE / "deals.csv", dtype=str)
    cef = d[(d["nav_priced"] == "True") & d["permno"].notna()]
    out.append(pd.DataFrame({"candidate": "cef_tender", "cik": cef["cik"], "permno": cef["permno"].astype(float),
                             "date": cef["date"], "id": cef["path"]}))
    t = tender_targets()
    out.append(pd.DataFrame({"candidate": "cash_tender", "cik": t["cik"], "permno": float("nan"),
                             "date": t["date"], "id": t["accession"]}))
    idx = build_index()
    nt = idx[idx["form"] == "NT 10-K"]
    out.append(pd.DataFrame({"candidate": "nt_10k", "cik": nt["cik"], "permno": float("nan"),
                             "date": nt["date"], "id": nt["path"]}))
    it = pd.read_parquet(et.CACHE / "eightk_items.parquet")
    it = it[(it["form"] == "8-K") & it["items"].fillna("").str.contains(r"\b3\.01\b")]
    out.append(pd.DataFrame({"candidate": "item_3_01", "cik": it["cik"], "permno": float("nan"),
                             "date": it["date"], "id": it["accession"]}))
    e = pd.concat(out, ignore_index=True)
    e = e[(e["date"] >= "2010-01-01") & (e["date"] <= md)]
    return e.drop_duplicates(["candidate", "id"]).reset_index(drop=True)


def sizes(e: pd.DataFrame) -> pd.DataFrame:
    """ONE WRDS connection. CIK -> gvkey (comp.company) -> permno (CCM primary
    link valid on the date), then CRSP's market cap on the last trading day
    in the 10 days before the event. Market cap only: no returns."""
    import wrds
    from crsp_pull import username
    e = e.copy()
    e["cik10"] = e["cik"].astype(str).str.zfill(10)
    ciks = ",".join(f"'{c}'" for c in sorted(set(e.loc[e["permno"].isna(), "cik10"])))
    db = wrds.Connection(wrds_username=username())
    try:
        comp = db.raw_sql(f"SELECT gvkey, cik FROM comp.company WHERE cik IN ({ciks})")
        lk = db.raw_sql("SELECT gvkey, lpermno AS permno, linkdt, linkenddt FROM crsp.ccmxpf_lnkhist "
                        "WHERE linktype IN ('LU','LC') AND linkprim IN ('P','C') AND lpermno IS NOT NULL")
        m = e[e["permno"].isna()].drop(columns="permno").merge(comp, left_on="cik10", right_on="cik",
                                                               how="left", suffixes=("", "_c"))
        m = m.merge(lk, on="gvkey", how="left")
        ok = (m["linkdt"].astype(str) <= m["date"]) & (m["linkenddt"].isna() | (m["linkenddt"].astype(str) >= m["date"]))
        m = m[ok].drop_duplicates(["candidate", "id"])
        linked = pd.concat([e[e["permno"].notna()], m[e.columns]], ignore_index=True)
        pairs = linked[["permno", "date"]].drop_duplicates()
        vals = ",".join(f"({int(p)},'{d}'::date)" for p, d in pairs.itertuples(index=False))
        cap = db.raw_sql(f"WITH ev(permno, d) AS (VALUES {vals}) "
                         "SELECT ev.permno, ev.d::text AS date, s.dlycaldt::text AS capdt, s.dlycap, s.primaryexch "
                         "FROM ev JOIN crsp.dsf_v2 s ON s.permno = ev.permno "
                         "AND s.dlycaldt BETWEEN ev.d - 10 AND ev.d - 1")
    finally:
        db.close()
    cap = cap.sort_values("capdt").drop_duplicates(["permno", "date"], keep="last")
    out = e.drop(columns="permno").merge(linked[["candidate", "id", "permno"]], on=["candidate", "id"], how="left")
    out = out.merge(cap[["permno", "date", "dlycap", "primaryexch"]], on=["permno", "date"], how="left")
    return out.drop(columns="cik10")


def screen(path: Path = ROOT / "data" / "screen_step4.csv") -> pd.DataFrame:
    """G-REAL and G-WORTH for every row of the screen's inputs
    (docs/specs/step4_paper_screen.md). Records all; declares nothing."""
    import gate as g
    d = pd.read_csv(path)
    num = lambda v: None if pd.isna(v) else float(v)
    rows = []
    for r in d.to_dict("records"):
        out = {"candidate": r["candidate"], "row": r["row"], "side": r["side"]}
        if not g.tradeable_retail(r["side"]):
            rows.append({**out, "g_real": False, "binding": "not tradeable retail (short side, no borrow data)"})
            continue
        if pd.notna(r["cited_mean_pct"]):
            gr = g.g_real_cited_event(r["cited_mean_pct"], r["cited_sd_pct"], r["n_confirm"],
                                      int(r["confirm_months"]), rho=float(r["rho"]), side=r["side"])
        elif r["candidate"] == "small_cash_merger_arb":
            gr = g.g_real("portfolio", cited_sharpe=0.24, years=13)
        else:
            rows.append({**out, "g_real": False, "binding": "no published estimate: design study on 2010-2018"})
            continue
        if pd.notna(r["usd_per_event"]):
            proj = g.project(r["usd_per_event"], r["events_per_yr_current"], num(r["capacity_cap_usd"]))
        elif pd.notna(r["capacity_cap_usd"]):
            proj = float(r["capacity_cap_usd"])
        else:
            proj = 540.0                                   # merger arb, re-screen base case
        ceil = (g.project(r["ceiling_usd_per_event"], r["events_per_yr_current"])
                if pd.notna(r["ceiling_usd_per_event"]) else None)
        w = g.g_worth(proj, projected=proj, ceiling=ceil)
        wc = g.g_worth(proj, projected=proj, ceiling=ceil, role="component")
        out.update(rho=gr.get("rho"), prior_t=gr["prior_t"], scaled_t_events=gr["scaled_t_events"],
                   scaled_t_clusters=gr["scaled_t_clusters"], scaled_t=gr["scaled_t"], p_pass=gr["p_pass"],
                   g_real=gr["g_real"], projected_usd=proj, ceiling_usd=ceil,
                   g_worth_standalone=w["g_worth"], g_worth_component=wc["g_worth"],
                   binding="passes" if gr["g_real"] and (w["g_worth"] or wc["g_worth"]) else
                   ("G-REAL" if not gr["g_real"] else "G-WORTH"))
        rows.append(out)
    return pd.DataFrame(rows)


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "index":
        f = build_index()
        print(f.groupby([f["date"].str[:4], "form"]).size().unstack(fill_value=0))
    elif cmd == "items":
        it = build_items(build_index())
        it.to_parquet(et.CACHE / "eightk_items.parquet", index=False)
        print(len(it), "8-K rows with items")
    elif cmd == "events":
        e = events()
        print(e.groupby([e["date"].str[:4], "candidate"]).size().unstack(fill_value=0))
    elif cmd == "sizes":
        z = sizes(events())
        z.to_parquet(et.CACHE / "screen_sizes.parquet", index=False)      # CRSP-derived: git-ignored
        print(z.groupby("candidate").agg(events=("id", "size"), linked=("permno", "count"),
                                         with_cap=("dlycap", "count")))
    elif cmd == "screen":
        r = screen()
        r.to_csv(ROOT / "reports" / "screen_step4.csv", index=False)
        print(r.to_string(index=False))
    else:
        sys.exit(__doc__)
