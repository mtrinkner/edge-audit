#!/usr/bin/env python3
"""SPAC trust-value arbitrage (trial 60 candidate). See docs/SPAC_TRUST_ARB.md.

    python3 python/spac_trust.py crsp     # ONE WRDS connection: security history, CCM links, daily prices
    python3 python/spac_trust.py link     # SPAC -> CRSP common-share permno (no WRDS)
    python3 python/spac_trust.py gate     # the pre-declaration gate, entry-time data only

The EDGAR side (universe, prospectus terms, XBRL trust facts) is in
spac_edgar.py. CRSP rows are licensed and stay in data/spac/ (git-ignored);
reports/ gets aggregates only.

WRDS sends a Duo push on every connection, so `crsp` opens exactly one and
runs every query inside it. It is never retried automatically.
"""

from __future__ import annotations

import sys
from pathlib import Path

import re

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "python"))

import data_access as da
import edgar_tenders as et
import spac_edgar as se

CACHE = se.CACHE
CRSP_LAST = "2025-12-31"


# ------------------------------------------------------------------ CRSP (one WRDS connection)

def crsp() -> int:
    import wrds
    from crsp_pull import username
    md = min(da.guard("spac_trust"), CRSP_LAST)
    t = pd.read_csv(CACHE / "terms.csv", dtype=str)
    syms = sorted({s for s in t["common_symbol"].dropna()} | {s + "U" for s in t["common_symbol"].dropna()})
    ciks = ",".join(f"'{c.zfill(10)}'" for c in sorted(set(t["cik"])))
    db = wrds.Connection(wrds_username=username())
    try:
        cols = set(db.raw_sql("SELECT * FROM crsp.stksecurityinfohist LIMIT 1").columns)
        sic = "OR siccd = 6770" if "siccd" in cols else ""
        q = ",".join(f"'{s}'" for s in syms)
        si = db.raw_sql(f"SELECT * FROM crsp.stksecurityinfohist WHERE secinfoenddt >= '2009-06-01' "
                        f"AND (ticker IN ({q}) {sic})")
        # Every security of the same issuers: units, warrants and rights are recorded separately.
        if "permco" in cols and len(si):
            permcos = ",".join(str(int(p)) for p in si["permco"].dropna().unique())
            allsec = db.raw_sql(f"SELECT * FROM crsp.stksecurityinfohist WHERE permco IN ({permcos}) "
                                f"AND secinfoenddt >= '2009-06-01'")
        else:
            allsec = si
        comp = db.raw_sql(f"SELECT gvkey, cik FROM comp.company WHERE cik IN ({ciks})")
        ccm = db.raw_sql("SELECT gvkey, lpermno AS permno, linkdt, linkenddt FROM crsp.ccmxpf_lnkhist "
                         "WHERE linktype IN ('LU','LC') AND linkprim IN ('P','C') AND lpermno IS NOT NULL "
                         f"AND gvkey IN (SELECT gvkey FROM comp.company WHERE cik IN ({ciks}))")
        permnos = sorted(set(allsec["permno"].dropna().astype(int)) | set(ccm["permno"].dropna().astype(int)))
        px = []
        for i in range(0, len(permnos), 500):
            chunk = ",".join(str(p) for p in permnos[i:i + 500])
            px.append(db.raw_sql("SELECT permno, dlycaldt, dlyprc, dlyopen, dlybid, dlyask, dlyvol, dlyprcvol, "
                                 "dlyret, dlycumfacpr, dlydelflg FROM crsp.dsf_v2 "
                                 f"WHERE permno IN ({chunk}) AND dlycaldt BETWEEN '2009-10-01' AND '{md}'"))
    finally:
        db.close()
    CACHE.mkdir(parents=True, exist_ok=True)
    pd.to_pickle({"si": si, "allsec": allsec, "comp": comp, "ccm": ccm, "px": px}, CACHE / "crsp_raw.pkl")
    for name, d in (("crsp_secinfo", allsec), ("crsp_comp", comp), ("crsp_ccm", ccm)):
        d.to_parquet(CACHE / f"{name}.parquet", index=False)
    px = pd.concat(px, ignore_index=True)
    px["dlycaldt"] = px["dlycaldt"].astype(str)
    px.to_parquet(CACHE / "crsp_daily.parquet", index=False)
    print(f"{len(si):,} matching security rows ({'with' if sic else 'without'} the SIC 6770 filter); "
          f"{len(allsec):,} rows for the same issuers; {len(permnos):,} permnos; "
          f"{len(px):,} daily rows through {px['dlycaldt'].max()}")
    return 0


# ------------------------------------------------------------------ link

def link() -> int:
    """Each SPAC's common-share permno.

    1. the common symbol from the prospectus, on a CRSP ordinary common share
       listed within 400 days of the prospectus, whose issuer name then is the
       SPAC's (edgar_tenders.same_issuer): tickers are recycled, so the symbol
       alone never decides;
    2. else the CCM permno of the SPAC's CIK (Compustat), with the same name check.
    CRSP carries no SPAC units, warrants or rights (checked: no EQTY security
    named as one), so the common share is the only permno to record."""
    t = pd.read_csv(CACHE / "terms.csv", dtype=str)
    a = pd.read_parquet(CACHE / "crsp_secinfo.parquet")
    a = a[(a["securitytype"] == "EQTY") & (a["securitysubtype"] == "COM") & a["sharetype"].isin(["NS", "AD"])].copy()
    a["s"], a["e"] = a["secinfostartdt"].astype(str), a["secinfoenddt"].astype(str)
    comp = pd.read_parquet(CACHE / "crsp_comp.parquet")
    ccm = pd.read_parquet(CACHE / "crsp_ccm.parquet").merge(comp, on="gvkey")
    ccm["cik"] = ccm["cik"].astype(str).str.lstrip("0")

    def check(r, m):
        lo, hi = r["prospectus_filed"], et._add_days(r["prospectus_filed"], 400)
        m = m[(m["s"] <= hi) & (m["e"] >= lo)]
        m = m[np.array([et.same_issuer(r["name_at_filing"], n) for n in m["issuernm"]], dtype=bool)]
        return int(m["permno"].iloc[0]) if m["permno"].nunique() == 1 else None

    out = []
    for _, r in t.iterrows():
        p, how = None, None
        if isinstance(r["common_symbol"], str):
            p = check(r, a[a["ticker"] == r["common_symbol"]])
            how = "ticker+name" if p else None
        if p is None:
            pc = set(ccm.loc[ccm["cik"] == r["cik"], "permno"].dropna().astype(int))
            p = check(r, a[a["permno"].isin(pc)]) if pc else None
            how = "ccm+name" if p else None
        out.append({"cik": r["cik"], "permno": p, "link_method": how})
    L = t.merge(pd.DataFrame(out), on="cik")
    L["permno"] = pd.to_numeric(L["permno"]).astype("Int64")
    px = pd.read_parquet(CACHE / "crsp_daily.parquet", columns=["permno", "dlycaldt"])
    first = px.groupby("permno")["dlycaldt"].min()
    L["crsp_first_day"] = L["permno"].map(lambda x: first.get(int(x)) if pd.notna(x) else None)
    L.to_csv(CACHE / "links.csv", index=False)
    L["year"] = L["prospectus_filed"].str[:4]
    cov = L.groupby("year").agg(found=("cik", "size"), linked=("permno", lambda x: x.notna().sum()),
                                with_trust_per_share=("trust_per_share_ipo", lambda x: x.notna().sum()))
    print(cov.to_string())
    print(L["link_method"].value_counts(dropna=False).to_string())
    return 0


# ------------------------------------------------------------------ the gate (entry-time data only)

DESIGN, CONFIRM = ("2010-01-01", "2020-12-31"), ("2021-01-01", "2025-12-31")
ACCOUNT, MAX_W, ADV_CAP = 25_000.0, 0.10, 0.25
FEE = 38.0                      # E*TRADE voluntary-action fee (primary); Fidelity $0 is the sensitivity row
HAIRCUT = 0.02                  # $/share, ASSUMPTION: liquidation expenses, excise tax, tax withdrawals
THRESH = 0.005                  # minimum net discount at entry, share of the close
PAY_LAG = 5                     # business days from the meeting (or delisting) to the redemption payment
DEADLINE_LAG = 2                # the redemption deadline: 2 business days before the meeting


def tbill() -> pd.Series:
    """Daily accrual factor of the 3-month T-bill (FRED DTB3, discount basis, actual/360)."""
    b = pd.read_csv(CACHE / "dtb3.csv")
    b["DTB3"] = pd.to_numeric(b["DTB3"], errors="coerce")
    b = b.set_index("observation_date")["DTB3"].ffill() / 100
    idx = pd.date_range("2009-01-01", "2026-12-31").strftime("%Y-%m-%d")
    r = b.reindex(idx).ffill()
    return np.cumprod(1 + r.fillna(0) / 360)            # cumulative growth per calendar day


def renamed_on(L: pd.DataFrame) -> dict:
    """First CRSP date on which a linked permno changes ticker or issuer name: the
    permno has continued as the operating company (the merger closed).

    Any change counts. same_issuer() is too loose here: it matches "New Frontier
    Corp" to "New Frontier Health Corp", the company it merged with."""
    a = pd.read_parquet(CACHE / "crsp_secinfo.parquet", columns=["permno", "secinfostartdt", "issuernm", "ticker"])
    a["s"] = a["secinfostartdt"].astype(str)
    a = a[~a["issuernm"].astype(str).str.contains("Last Known", na=False)]
    squash = lambda x: re.sub(r"[^A-Z0-9]", "", str(x).upper())
    out = {}
    for p, g in a[a["permno"].isin(L["permno"].dropna().astype(int))].sort_values("s").groupby("permno"):
        n0, t0 = squash(g["issuernm"].iloc[0]), g["ticker"].iloc[0]
        ch = g[(g["issuernm"].map(squash) != n0) | (g["ticker"] != t0)]
        if len(ch):
            out[p] = ch["s"].iloc[0]
    return out


def events(L: pd.DataFrame, F: pd.DataFrame) -> dict:
    """Redemption opportunities of each linked SPAC.

    Votes are 8-K item 5.07 event dates after the IPO, up to the first item
    2.01 (completed acquisition) 8-K. NOT ENTRY-TIME: the date comes from the
    post-vote 8-K, standing in for the meeting date the proxy announced
    two to four weeks before. A SPAC with no completed acquisition ends at its
    last CRSP day (liquidation or end of data)."""
    out = {}
    F = F[F["form"].str.startswith("8-K")]
    for r in L.dropna(subset=["permno"]).itertuples():
        f = F[(F["cik"] == r.cik) & (F["filed"] > r.prospectus_filed)]
        done = f[f["items"].str.contains("2.01", na=False)]["report"].min()
        votes = sorted(set(f[f["items"].str.contains("5.07", na=False)]["report"].dropna()))
        votes = [v for v in votes if v > r.prospectus_filed and (not isinstance(done, str) or v <= done)]
        out[int(r.permno)] = {"cik": r.cik, "ipo": r.prospectus_filed, "votes": votes,
                              "closed": done if isinstance(done, str) else None}
    return out


def phase_end(e: dict, renamed: str | None, last: str, cal: np.ndarray) -> tuple[str | None, bool]:
    """(the date the SPAC phase ended, whether it ended in a merger).

    The earliest of: the completed-acquisition 8-K; the CRSP rename; the final
    vote, when the permno stops trading within 15 trading days of it (a merger
    into a new holding company, whose 8-K is filed under another CIK). A SPAC
    that simply stops trading is taken to liquidate on its last CRSP day.
    NOT ENTRY-TIME: this only sets where the SPAC-days end."""
    ends = [x for x in (e["closed"], renamed) if x]
    if e["votes"]:
        v = e["votes"][-1]
        i, j = np.searchsorted(cal, v), np.searchsorted(cal, last)
        if j - i <= 15 and v <= last:
            ends.append(v)
    return (min(ends), True) if ends else (None, False)


def trust_estimates(L, T, growth) -> dict:
    """E_t inputs per permno: (available-from date, per-share value, basis date), in order.

    Usable from the day AFTER the filing date. Per share, in order of preference:
      - the XBRL redemption price per share (TemporaryEquityRedemptionPricePerShare);
      - trust / redeemable shares, for balance dates from 2021-06-30 on, when all
        public shares are carried as temporary equity (before the SEC's April 2021
        statement only part of them were, which overstates the ratio);
      - else the IPO trust per share, accrued at the T-bill rate (spac_trust.tbill)."""
    out = {}
    for r in L.dropna(subset=["permno"]).itertuples():
        if pd.isna(r.trust_per_share_ipo):
            continue                       # never assume $10: some SPACs sold $6 units (UBPS 2011)
        tpu = float(r.trust_per_share_ipo)
        rows = [(r.prospectus_filed, tpu, r.prospectus_filed)]
        for f in T[T["cik"] == r.cik].itertuples():
            v = None
            p = f.TemporaryEquityRedemptionPricePerShare
            if pd.notna(p) and 9.5 <= p <= 12.5:
                v = float(p)
            elif f.end >= "2021-06-30" and pd.notna(f.trust) and pd.notna(f.TemporaryEquitySharesOutstanding) \
                    and f.TemporaryEquitySharesOutstanding > 0:
                q = f.trust / f.TemporaryEquitySharesOutstanding
                model = tpu * growth[f.end] / growth[r.prospectus_filed]
                if model - 0.15 <= q <= model + 1.0:
                    v = float(q)
            if v is not None:
                rows.append((et._add_days(f.filed, 1), v, f.end))
        out[int(r.permno)] = sorted(rows)
    return out


def panel(fee=FEE, haircut=HAIRCUT) -> pd.DataFrame:
    """SPAC-days with the entry-time trust estimate E_t and the net discount."""
    growth = tbill()
    L = pd.read_csv(CACHE / "links.csv", dtype=str)
    T = pd.read_csv(CACHE / "trust_facts.csv", dtype={"cik": str})
    F = pd.read_csv(CACHE / "filings.csv", dtype=str)
    ev, est, ren = events(L, F), trust_estimates(L, T, growth), renamed_on(L)
    px = pd.read_parquet(CACHE / "crsp_daily.parquet")
    px = px[px["permno"].isin(list(ev)) & (px["dlycaldt"] <= CRSP_LAST)].copy()
    for c in ("dlyprc", "dlybid", "dlyask", "dlyprcvol"):
        px[c] = pd.to_numeric(px[c], errors="coerce").astype(float)
    px["close"] = px["dlyprc"].abs().where(px["dlyprc"].abs() > 0)
    mid = (px["dlybid"] + px["dlyask"]) / 2
    px["hs"] = ((px["dlyask"] - px["dlybid"]) / mid / 2).where((px["dlyask"] > px["dlybid"]) & (px["dlybid"] > 0))
    px = px.sort_values(["permno", "dlycaldt"])
    px["adv20"] = px.groupby("permno")["dlyprcvol"].transform(lambda x: x.shift(1).rolling(20, min_periods=10).median())
    cal = np.array(sorted(px["dlycaldt"].unique()))
    bday = lambda d, n: cal[min(max(np.searchsorted(cal, d) + n, 0), len(cal) - 1)]
    out = []
    for p, g in px.groupby("permno"):
        if p not in est:
            continue
        e = ev[p]
        last = g["dlycaldt"].max()
        end, merged = phase_end(e, ren.get(p), last, cal)
        votes = [v for v in e["votes"] if end is None or v <= end]
        opps = [(bday(v, -DEADLINE_LAG), bday(v, PAY_LAG)) for v in votes]
        if not merged:                                       # liquidated, or open at the end of the data
            opps.append((last, bday(last, PAY_LAG)))
        if not opps:
            continue
        g = g[g["dlycaldt"] < opps[-1][0]].copy()            # nothing after the final redemption deadline
        if end:
            g = g[g["dlycaldt"] < end]
        if g.empty:
            continue
        dl = np.array([o[0] for o in opps])
        k = np.searchsorted(dl, g["dlycaldt"].to_numpy(), side="right")
        g["deadline"] = dl[k]
        g["pay_day"] = np.array([o[1] for o in opps])[k]
        es = est[p]
        av = [x[0] for x in es]
        j = np.searchsorted(av, g["dlycaldt"].to_numpy(), side="right") - 1
        base = np.array([es[i][1] for i in j])
        basis = np.array([es[i][2] for i in j])
        g["E_t"] = base * growth[g["dlycaldt"]].to_numpy() / growth[basis].to_numpy()
        g["E_basis"] = np.where(j == 0, "ipo_accrual", "xbrl")
        g["cik"], g["ipo"], g["phase_end"], g["merged"] = e["cik"], e["ipo"], end, merged
        out.append(g)
    d = pd.concat(out, ignore_index=True)
    d["hs"] = d["hs"].fillna(d.groupby("dlycaldt")["hs"].transform("median")).fillna(0.0025)
    d["pos_usd"] = np.minimum(ACCOUNT * MAX_W, ADV_CAP * d["adv20"].fillna(0))
    d["shares"] = np.floor(d["pos_usd"] / d["close"])
    d["raw_disc"] = (d["E_t"] - d["close"]) / d["close"]
    d["net_disc"] = (d["E_t"] - d["close"] * (1 + d["hs"]) - fee / d["shares"].where(d["shares"] > 0)
                     - haircut) / d["close"]
    # A redeemable share more than 10% below its trust value is, in this data, a
    # share whose redemption deadline has already passed (an adjourned meeting, a
    # vote date read from the post-vote 8-K) or a stale quote. Excluded, and counted.
    d["suspect"] = d["raw_disc"] > 0.10
    d["traded"] = d["dlyprcvol"].fillna(0) > 0
    return d


def simulate(d: pd.DataFrame, lo: str, hi: str, fee=FEE, haircut=HAIRCUT, thresh=THRESH,
             mark: str = "redeem") -> dict:
    """The declared portfolio on [lo, hi], every position redeemed at E (accrued)
    on its pay day (no deal pop). Decide after the close of day t, buy at the
    close of day t+1. Returns NAV, cash and position counts per day.

    mark="redeem" values open positions at what redemption would pay (the
    gate: no deal pop, so a market price above trust must not leak into NAV);
    mark="close" marks them at the CRSP close (last one known)."""
    growth = tbill()
    rf = pd.read_parquet(ROOT / "data" / "pead" / "ff_daily.parquet").assign(date=lambda x: x["date"].astype(str))
    rf = rf.set_index("date")["rf"]
    d = d[(d["dlycaldt"] >= lo) & (d["dlycaldt"] <= hi)]
    days = sorted(d["dlycaldt"].unique())
    nxt = {a: b for a, b in zip(days[:-1], days[1:])}
    by_day = {k: g for k, g in d.groupby("dlycaldt")}
    px_next = d.set_index(["permno", "dlycaldt"])[["close", "hs", "E_t", "deadline", "pay_day", "traded"]]
    cash, held, rows, trades = ACCOUNT, {}, [], []
    pending = []
    for t in days:
        # 1. redemptions paid today
        for p in [p for p, h in held.items() if h["pay_day"] <= t]:
            h = held.pop(p)
            proceeds = h["shares"] * (h["E_entry"] * growth[h["pay_day"]] / growth[h["entry_day"]] - haircut) - fee
            cash += proceeds
            trades[h["i"]].update(exit_day=h["pay_day"], proceeds=proceeds)
        # 2. yesterday's decisions fill at today's close
        for p, nd in pending:
            if p in held or (p, t) not in px_next.index:
                continue
            q = px_next.loc[(p, t)]
            if t >= q["deadline"] or not q["traded"]:
                continue
            want = ACCOUNT * MAX_W
            cap = by_day[t].set_index("permno").loc[p, "pos_usd"]
            usd = min(want, cap)
            if usd > cash:
                continue                     # fully invested: skip, never buy a fee-sized remnant
            sh = np.floor(usd / (q["close"] * (1 + q["hs"])))
            if sh <= 0 or sh * q["close"] * (1 + q["hs"]) > cash:
                continue
            cost = sh * q["close"] * (1 + q["hs"])
            cash -= cost
            held[p] = {"shares": sh, "E_entry": q["E_t"], "entry_day": t, "pay_day": q["pay_day"], "i": len(trades)}
            trades.append({"permno": p, "entry_day": t, "cost": cost, "net_disc": nd, "capacity_limited": cap < want,
                           "pay_day": q["pay_day"]})
        # 3. NAV at today's close; cash earns T-bills (FF daily rf)
        g = by_day[t].set_index("permno")
        value = 0.0
        for p, h in held.items():
            red = h["shares"] * (h["E_entry"] * growth[t] / growth[h["entry_day"]] - haircut) - fee
            if mark == "redeem":
                value += red
            else:
                c = g.loc[p, "close"] if p in g.index else np.nan
                h["last_close"] = c if c == c else h.get("last_close", h["E_entry"])
                value += h["shares"] * h["last_close"]
        mark_ = value
        rows.append({"d": t, "cash": cash, "nav": cash + mark_, "n_held": len(held),
                     "invested": 1 - cash / max(cash + mark_, 1e-9)})
        cash *= 1 + float(rf.get(t, 0.0))
        # 4. decide after the close: eligible names by larger net discount
        el = g[(g["net_disc"] >= thresh) & (g["shares"] > 0) & ~g["suspect"]].sort_values("net_disc", ascending=False)
        pending = [(p, nd) for p, nd in el["net_disc"].items() if p not in held] if t in nxt else []
    nav = pd.DataFrame(rows).set_index("d")
    return {"nav": nav, "trades": pd.DataFrame(trades)}


def gate() -> int:
    """Pre-declaration gate, check (a): economic size from entry-time data only."""
    md = da.guard("spac_trust")
    rf = pd.read_parquet(ROOT / "data" / "pead" / "ff_daily.parquet").assign(date=lambda x: x["date"].astype(str))
    rf = rf.set_index("date")["rf"]
    base = panel()
    for name, (lo, hi) in (("design", DESIGN), ("confirm", CONFIRM)):
        x = base[(base["dlycaldt"] >= lo) & (base["dlycaldt"] <= hi)]
        print(f"{name} {lo}..{hi}: {x['permno'].nunique()} SPACs, {len(x):,} SPAC-days; below E_t "
              f"{(x['raw_disc'] > 0).sum():,} days ({x.loc[x['raw_disc'] > 0, 'permno'].nunique()} SPACs); "
              f"net discount >= {THRESH:.1%}: {(x['net_disc'] >= THRESH).sum():,} days "
              f"({x.loc[x['net_disc'] >= THRESH, 'permno'].nunique()} SPACs)")
    out = []
    for fee in (FEE, 0.0):
        for hc in (HAIRCUT, 0.0, 0.05):
            for th in (THRESH, 0.0, 0.01):
                if (fee, hc, th) != (FEE, HAIRCUT, THRESH) and sum([fee != FEE, hc != HAIRCUT, th != THRESH]) > 1:
                    continue                                  # one change at a time from the primary row
                d = base if (fee, hc) == (FEE, HAIRCUT) else panel(fee, hc)
                s = simulate(d, *DESIGN, fee=fee, haircut=hc, thresh=th)
                nav, tr = s["nav"], s["trades"]
                yr = nav["nav"].groupby(nav.index.str[:4])
                r = rf.reindex(nav.index).fillna(0.0)
                exc = {}
                for y, g in yr:
                    gross = g.iloc[-1] / g.iloc[0] if len(g) > 1 else 1.0
                    bill = float(np.prod(1 + r[g.index[1:]]))
                    exc[y] = (gross - bill) * ACCOUNT
                row = {"fee": fee, "haircut": hc, "threshold": th,
                       "excess_usd_per_yr": np.mean(list(exc.values())),
                       "days_fully_invested": float((nav["invested"] >= 1 - MAX_W).mean()),
                       "entries": len(tr),
                       "capacity_limited": float(tr["capacity_limited"].mean()) if len(tr) else np.nan,
                       "median_net_disc_entry": float(tr["net_disc"].median()) if len(tr) else np.nan,
                       **{f"y{y}": v for y, v in exc.items()}}
                out.append(row)
                print({k: (round(v, 4) if isinstance(v, float) else v) for k, v in row.items()}, flush=True)
    rep = pd.DataFrame(out)
    rep.to_csv(ROOT / "reports" / "spac_gate_design.csv", index=False)
    x = base[(base["dlycaldt"] >= DESIGN[0]) & (base["dlycaldt"] <= DESIGN[1])]
    print(f"median net discount over design SPAC-days: {x['net_disc'].median():.4f}; "
          f"over days below E_t: {x.loc[x['raw_disc'] > 0, 'net_disc'].median():.4f}")
    print("E_t basis on design days:", x["E_basis"].value_counts(normalize=True).round(3).to_dict())
    print("wrote reports/spac_gate_design.csv")
    return 0


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "gate"
    sys.exit({"crsp": crsp, "link": link, "gate": gate}[cmd]())
