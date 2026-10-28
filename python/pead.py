#!/usr/bin/env python3
"""Post-earnings announcement drift on IBES + CRSP (WRDS). Not yet declared.

    python3 python/pead.py check             # verify the WRDS tables and columns this uses
    python3 python/pead.py pull              # IBES, links, breakpoints, factors, CRSP daily -> data/pead/
    python3 python/pead.py build             # events: day 0, universe, SUE, deciles
    python3 python/pead.py run               # DESIGN: announcements 1995-2012
    python3 python/pead.py run --confirm     # CONFIRMATION 2013-2025, once, frozen code

THE TRADE
  Event: a quarterly EPS announcement in IBES (actu_epsus). Day 0 is the
  announcement date, or the next trading day if it came at or after 16:00 or
  carries no time stamp (IBES writes an unknown time as 00:00:00).
  Surprise: SUE = (actual EPS - median estimate) / price on day -1. The estimate
  is the last IBES summary (statsumu_epsus, one quarter ahead) dated before the
  announcement and no more than 90 days old. Both are IBES unadjusted figures,
  on the same share basis as CRSP's raw price; an event with a split between
  the estimate and day -1 is dropped.
  Universe on day -1: CRSP ordinary common share (sharetype NS, securitysubtype
  COM) on NYSE, AMEX or Nasdaq, |price| >= $5, market value at or above the NYSE
  20th percentile of the previous month-end.
  Deciles: cut on the previous calendar quarter's SUE distribution (day 0 in
  that quarter, same universe), so no event is ranked against later events.
  Position: buy (short) at the open of day +2, hold to the close of day +61:
  60 trading days. Each leg is an equal-weighted average of its open positions.
  Costs: half the quoted spread (CRSP closing bid/ask) on the entry day and the
  exit day; 50bp a year to borrow the short leg.
  V1: long the top decile, short the bottom. V2: long the top decile minus the
  market (Fama-French Mkt-RF + RF).
  Test: monthly net return, Newey-West t (evaluation.newey_west_t).

Everything is cached under data/pead/ (git-ignored: CRSP and IBES are licensed
and never committed). Only aggregates go to reports/.
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

CACHE = ROOT / "data" / "pead"
FIRST = "1994-07-01"                      # a half year before the design starts, for the first cutpoints
PERIODS = {"design": ("1995-01-01", "2012-12-31"), "confirm": ("2013-01-01", "2025-12-31")}
RECENT = ("2020-01-01", "2025-12-31")     # the confirmation's decay check
ENTRY_LAG, HOLD = 2, 60                   # enter at the open of day +2, exit at the close of day +61
MIN_PRICE = 5.0
EST_MAX_AGE = 90                          # calendar days
BORROW = 0.005                            # a year, on the short leg
SPREAD_FALLBACK = 0.0025                  # half-spread when a stock has no quote that day and no peers do

# The WRDS tables and the columns this module reads. `check` verifies them.
TABLES = {
    ("ibes", "actu_epsus"): ["ticker", "pends", "pdicity", "anndats", "anntims", "value", "measure",
                             "usfirm", "curr_act"],
    ("ibes", "statsumu_epsus"): ["ticker", "statpers", "fpedats", "fiscalp", "fpi", "measure", "medest",
                                 "numest", "usfirm", "curcode"],
    ("wrdsapps_link_crsp_ibes", "ibcrsphist"): ["ticker", "permno", "sdate", "edate", "score"],
    ("crsp", "dsf_v2"): ["permno", "dlycaldt", "dlyprc", "dlyopen", "dlyret", "dlybid", "dlyask", "dlycap",
                         "dlycumfacpr", "primaryexch", "sharetype", "securitysubtype"],
    ("crsp", "msf_v2"): ["permno", "mthcaldt", "mthcap", "primaryexch", "sharetype", "securitysubtype"],
    ("ff", "factors_daily"): ["date", "mktrf", "rf"],
}


def connect():
    import wrds
    from crsp_pull import username
    return wrds.Connection(wrds_username=username())


# ------------------------------------------------------------------ check

def check() -> int:
    """Every table and column this module uses exists, with rows in the period."""
    db = connect()
    bad = []
    for (schema, table), cols in TABLES.items():
        have = set(db.raw_sql("SELECT column_name FROM information_schema.columns "
                              f"WHERE table_schema='{schema}' AND table_name='{table}'")["column_name"])
        missing = [c for c in cols if c not in have]
        print(f"{schema}.{table}: {'ok' if not missing else 'MISSING ' + ', '.join(missing)}")
        bad += missing
    if not bad:
        print(db.raw_sql("SELECT extract(year FROM anndats) AS year, count(*) AS n, "
                         "avg((anntims IS NOT NULL)::int) AS timed FROM ibes.actu_epsus "
                         "WHERE pdicity='QTR' AND measure='EPS' AND usfirm=1 AND anndats >= '1994-01-01' "
                         "GROUP BY 1 ORDER BY 1").to_string(index=False))
        print(db.raw_sql("SELECT fpi, count(*) FROM ibes.statsumu_epsus WHERE fiscalp='QTR' AND measure='EPS' "
                         "AND statpers BETWEEN '2010-01-01' AND '2010-12-31' GROUP BY 1 ORDER BY 1").to_string(index=False))
    db.close()
    return 1 if bad else 0


# ------------------------------------------------------------------ pull

def pull() -> int:
    md = da.guard("pead")
    CACHE.mkdir(parents=True, exist_ok=True)
    db = connect()
    # Each announcement with the last one-quarter-ahead consensus dated before it.
    ev = db.raw_sql(f"""
        SELECT DISTINCT ON (a.ticker, a.pends)
               a.ticker, a.pends, a.anndats, a.anntims, a.value AS actual,
               s.statpers, s.medest, s.numest
        FROM ibes.actu_epsus a
        JOIN ibes.statsumu_epsus s
          ON s.ticker = a.ticker AND s.fpedats = a.pends AND s.fiscalp = 'QTR' AND s.measure = 'EPS'
         AND s.fpi = '6' AND s.statpers < a.anndats AND s.statpers >= a.anndats - {EST_MAX_AGE}
         AND s.curcode = a.curr_act
        WHERE a.pdicity = 'QTR' AND a.measure = 'EPS' AND a.usfirm = 1
          AND a.anndats BETWEEN '{FIRST}' AND '{md}' AND a.value IS NOT NULL AND s.medest IS NOT NULL
        ORDER BY a.ticker, a.pends, s.statpers DESC""")
    ev.to_parquet(CACHE / "ibes_events.parquet", index=False)
    print(f"{len(ev):,} announcements with a consensus", flush=True)
    link = db.raw_sql("SELECT ticker, permno, sdate, edate, score FROM wrdsapps_link_crsp_ibes.ibcrsphist "
                      "WHERE permno IS NOT NULL AND score <= 2")
    link.to_parquet(CACHE / "link.parquet", index=False)
    bp = db.raw_sql(f"""
        SELECT to_char(mthcaldt, 'YYYY-MM') AS month,
               percentile_cont(0.2) WITHIN GROUP (ORDER BY mthcap) AS nyse_p20
        FROM crsp.msf_v2
        WHERE primaryexch = 'N' AND sharetype = 'NS' AND securitysubtype = 'COM' AND mthcap > 0
          AND mthcaldt BETWEEN '{FIRST}'::date - 60 AND '{md}'
        GROUP BY 1 ORDER BY 1""")
    bp.to_parquet(CACHE / "nyse_p20.parquet", index=False)
    ff = db.raw_sql(f"SELECT date, mktrf, rf FROM ff.factors_daily WHERE date BETWEEN '{FIRST}' AND '{md}'")
    ff["date"] = ff["date"].astype(str)
    ff.to_parquet(CACHE / "ff_daily.parquet", index=False)
    # CRSP daily, a year at a time, for the stocks with an announcement that year or the
    # quarter before (a position opened in December runs into March).
    ev["anndats"] = ev["anndats"].astype(str)
    link["sdate"], link["edate"] = link["sdate"].astype(str), link["edate"].astype(str)
    m = ev.merge(link, on="ticker")
    m = m[(m["sdate"] <= m["anndats"]) & (m["edate"] >= m["anndats"])]
    cols = ", ".join(TABLES[("crsp", "dsf_v2")])
    for y in range(int(FIRST[:4]), int(md[:4]) + 1):
        out = CACHE / f"dsf_{y}.parquet"
        if out.exists():
            continue
        p = m[(m["anndats"] >= f"{y - 1}-09-01") & (m["anndats"] <= f"{y}-12-31")]["permno"].unique()
        if not len(p):
            continue
        d = db.raw_sql(f"SELECT {cols} FROM crsp.dsf_v2 WHERE permno IN ({','.join(str(int(x)) for x in p)}) "
                       f"AND dlycaldt BETWEEN '{y}-01-01' AND '{min(f'{y}-12-31', md)}'")
        d["dlycaldt"] = d["dlycaldt"].astype(str)
        d.to_parquet(out, index=False)
        print(f"  {y}: {len(p):,} stocks, {len(d):,} rows", flush=True)
    db.close()
    return 0


# ------------------------------------------------------------------ build (pure functions, tested)

def day_zero(anndats: pd.Series, anntims: pd.Series, calendar: np.ndarray) -> np.ndarray:
    """Index into the trading calendar of each announcement's day 0.

    The first trading day on or after the announcement date; the first trading
    day after it if the announcement came at or after 16:00 or has no time."""
    t = anntims.astype(str).str[:5]
    # IBES stamps an unknown time as 00:00:00; treat it like a missing one.
    after_close = anntims.isna() | (t >= "16:00") | (t == "00:00")
    i = np.searchsorted(calendar, anndats.to_numpy(str), side="left")
    on_day = (i < len(calendar)) & (calendar[np.minimum(i, len(calendar) - 1)] == anndats.to_numpy(str))
    return np.where(after_close.to_numpy() & on_day, i + 1, i)


def quarter(d: pd.Series) -> pd.Series:
    s = d.astype(str)
    return s.str[:4] + "Q" + ((s.str[5:7].astype(int) - 1) // 3 + 1).astype(str)


def prev_quarter(q: str) -> str:
    y, n = int(q[:4]), int(q[5])
    return f"{y - 1}Q4" if n == 1 else f"{y}Q{n - 1}"


def assign_deciles(ev: pd.DataFrame) -> pd.Series:
    """Decile 1-10 of each event's SUE, cut on the previous calendar quarter's events."""
    q = quarter(ev["day0_date"])
    cuts = {k: np.quantile(g["sue"], np.linspace(0.1, 0.9, 9)) for k, g in ev.groupby(q) if len(g) >= 50}
    out = pd.Series(np.nan, index=ev.index)
    for k, idx in ev.groupby(q).groups.items():
        c = cuts.get(prev_quarter(k))
        if c is not None:
            out.loc[idx] = np.searchsorted(c, ev.loc[idx, "sue"].to_numpy(), side="right") + 1
    return out


def leg_returns(pos: pd.DataFrame, px: pd.DataFrame, calendar: np.ndarray) -> pd.DataFrame:
    """Daily equal-weighted return of a set of positions, gross and net of spread.

    pos: permno, entry (calendar index of the entry day), exit (index of the exit day).
    px: permno, d (ISO date), open, close, ret, half_spread.
    The entry day earns close / open - 1; later days earn CRSP's dlyret (delisting
    returns included); the half-spread is charged on the entry day and the exit day.
    A position whose stock stops trading is closed on its last row."""
    px = px.assign(i=np.searchsorted(calendar, px["d"].to_numpy(str)))
    by = {p: g.set_index("i").sort_index() for p, g in px.groupby("permno")}
    rows = []
    for p, g in pos.groupby("permno"):
        s = by.get(p)
        if s is None:
            continue
        for e, x in zip(g["entry"], g["exit"]):
            w = s.loc[(s.index >= e) & (s.index <= x)]
            if w.empty or w.index[0] != e:
                continue                                  # no row on the entry day: never entered
            r = w["ret"].to_numpy(float).copy()
            r[0] = w["close"].iloc[0] / w["open"].iloc[0] - 1
            c = np.zeros(len(w))
            c[0] += w["half_spread"].iloc[0]
            c[-1] += w["half_spread"].iloc[-1]
            rows.append(pd.DataFrame({"i": w.index, "gross": r, "net": r - c}))
    if not rows:
        return pd.DataFrame(columns=["i", "gross", "net", "n"])
    a = pd.concat(rows)
    out = a.groupby("i").agg(gross=("gross", "mean"), net=("net", "mean"), n=("gross", "size")).reset_index()
    return out


def build() -> int:
    md = da.guard("pead")
    ev = pd.read_parquet(CACHE / "ibes_events.parquet")
    link = pd.read_parquet(CACHE / "link.parquet")
    for c in ("anndats", "statpers", "pends"):
        ev[c] = ev[c].astype(str)
    for c in ("sdate", "edate"):
        link[c] = link[c].astype(str)
    ev = ev[ev["anndats"] <= md]
    ev = ev.merge(link, on="ticker")
    ev = ev[(ev["sdate"] <= ev["anndats"]) & (ev["edate"] >= ev["anndats"])]
    ev = ev.sort_values("score").drop_duplicates(["ticker", "pends"])
    cal = np.array(sorted(pd.read_parquet(CACHE / "ff_daily.parquet")["date"].astype(str)))
    ev["day0"] = day_zero(ev["anndats"], ev["anntims"], cal)
    ev = ev[ev["day0"] + ENTRY_LAG + HOLD - 1 < len(cal)]
    ev["day0_date"] = cal[ev["day0"]]
    ev["dm1"] = cal[ev["day0"] - 1]
    ev["entry"], ev["exit"] = ev["day0"] + ENTRY_LAG, ev["day0"] + ENTRY_LAG + HOLD - 1
    px = load_prices(md)
    # Day -1: price, size, listing, and the share basis at day -1 and at the estimate date.
    pre = px.rename(columns={"d": "dm1"})[["permno", "dm1", "close", "cap", "cumfac", "primaryexch", "sharetype",
                                         "securitysubtype"]]
    ev = ev.merge(pre, on=["permno", "dm1"], how="inner")
    est = px.rename(columns={"d": "statpers", "cumfac": "cumfac_est"})[["permno", "statpers", "cumfac_est"]]
    ev = ev.merge(est, on=["permno", "statpers"], how="left")
    bp = pd.read_parquet(CACHE / "nyse_p20.parquet")
    bp["month"] = bp["month"].map(lambda m: f"{int(m[:4]) + (m[5:] == '12')}-{int(m[5:]) % 12 + 1:02d}")
    ev["month"] = ev["dm1"].str[:7]                            # previous month-end breakpoint applies
    ev = ev.merge(bp, on="month", how="left")
    keep = ((ev["sharetype"] == "NS") & (ev["securitysubtype"] == "COM") & ev["primaryexch"].isin(["N", "A", "Q"])
            & (ev["close"] >= MIN_PRICE) & (ev["cap"] >= ev["nyse_p20"])
            & (ev["cumfac_est"].isna() | (abs(ev["cumfac_est"] / ev["cumfac"] - 1) < 1e-9)))
    n0 = len(ev)
    ev = ev[keep].copy()
    ev["sue"] = (ev["actual"] - ev["medest"]) / ev["close"]
    ev["decile"] = assign_deciles(ev)
    ev.to_parquet(CACHE / "events.parquet", index=False)
    print(f"{n0:,} linked announcements with a day -1 price; {len(ev):,} in the universe; "
          f"{ev['decile'].notna().sum():,} ranked")
    print(ev.groupby(ev["day0_date"].str[:4]).size().to_string())
    return 0


def load_prices(md: str) -> pd.DataFrame:
    import glob
    px = pd.concat([pd.read_parquet(f) for f in sorted(glob.glob(str(CACHE / "dsf_*.parquet")))])
    px = px[px["dlycaldt"] <= md]
    px = px.rename(columns={"dlycaldt": "d", "dlyopen": "open", "dlyret": "ret", "dlycap": "cap",
                            "dlycumfacpr": "cumfac"})
    px["close"] = px["dlyprc"].abs().where(px["dlyprc"].abs() > 0)
    mid = (px["dlybid"] + px["dlyask"]) / 2
    hs = ((px["dlyask"] - px["dlybid"]) / mid / 2).where((px["dlyask"] > px["dlybid"]) & (px["dlybid"] > 0))
    px["half_spread"] = hs.fillna(hs.groupby(px["d"]).transform("median")).fillna(SPREAD_FALLBACK)
    return px


# ------------------------------------------------------------------ run

def run(period: str) -> int:
    from evaluation import newey_west_t
    md = da.guard("pead")
    lo, hi = PERIODS[period]
    ev = pd.read_parquet(CACHE / "events.parquet")
    ev = ev[ev["day0_date"].between(lo, hi) & ev["decile"].notna()]
    cal = np.array(sorted(pd.read_parquet(CACHE / "ff_daily.parquet")["date"].astype(str)))
    ev = ev[ev["exit"] < len(cal)]
    px = load_prices(md)
    px = px[px["permno"].isin(ev["permno"].unique())]
    top = leg_returns(ev[ev["decile"] == 10], px, cal).set_index("i")
    bot = leg_returns(ev[ev["decile"] == 1], px, cal).set_index("i")
    ff = pd.read_parquet(CACHE / "ff_daily.parquet").assign(date=lambda x: x["date"].astype(str)).set_index("date")
    idx = sorted(set(top.index) | set(bot.index))
    dly = pd.DataFrame(index=idx)
    dly["d"] = cal[dly.index]
    dly = dly[dly["d"].between(lo, hi)]
    for leg, t in (("long", top), ("short", bot)):
        for k in ("gross", "net"):
            dly[f"{leg}_{k}"] = t[k].reindex(dly.index).fillna(0.0)
        dly[f"{leg}_n"] = t["n"].reindex(dly.index).fillna(0)
    mkt = (ff["mktrf"] + ff["rf"]).reindex(dly["d"]).to_numpy()
    dly["v1_gross"] = dly["long_gross"] - dly["short_gross"]
    dly["v1_net"] = dly["long_net"] - dly["short_net"] - BORROW / 252 * (dly["short_n"] > 0)
    dly["v2_gross"] = dly["long_gross"] - mkt
    dly["v2_net"] = dly["long_net"] - mkt
    dly["month"] = dly["d"].str[:7]
    mon = dly.groupby("month")[["v1_gross", "v1_net", "v2_gross", "v2_net"]].apply(lambda x: (1 + x).prod() - 1)
    out = []
    for col in mon.columns:
        nw = newey_west_t(mon[col])
        rec = mon[col][mon.index >= RECENT[0][:7]]
        out.append({"series": col, "months": nw["n"], "mean_monthly": nw["mean"], "t_nw": nw["t"],
                    "annualised": (1 + nw["mean"]) ** 12 - 1, "sharpe": mon[col].mean() / mon[col].std() * np.sqrt(12),
                    "recent_mean_monthly": rec.mean() if len(rec) else np.nan})
    s = pd.DataFrame(out)
    s["events_long"], s["events_short"] = (ev["decile"] == 10).sum(), (ev["decile"] == 1).sum()
    s["avg_positions_long"], s["avg_positions_short"] = dly["long_n"].mean(), dly["short_n"].mean()
    s.to_csv(ROOT / "reports" / f"pead_{period}.csv", index=False)
    print(f"{period.upper()} {PERIODS[period]}: {len(ev):,} ranked announcements")
    print(s.round(5).to_string(index=False))
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["check", "pull", "build", "run"])
    ap.add_argument("--confirm", action="store_true")
    a = ap.parse_args()
    sys.exit({"check": check, "pull": pull, "build": build}.get(a.cmd, lambda: run("confirm" if a.confirm else "design"))())
