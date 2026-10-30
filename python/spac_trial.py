#!/usr/bin/env python3
"""Trial 60: SPAC trust-value arbitrage, research grade. See docs/SPAC_TRUST_ARB.md.

    python3 python/spac_trial.py events            # timelines from data/spac/proxy_events*.csv
    python3 python/spac_trial.py validate          # automated consistency checks (pass rates only)
    python3 python/spac_trial.py gate              # Phase 2: ceiling, declared rule, design Sharpe, P(pass)
    python3 python/spac_trial.py run               # DESIGN 2010-2020
    python3 python/spac_trial.py run --confirm     # CONFIRMATION 2021-2025, once, on frozen code

ENTRY-TIME. Everything a decision on day t uses was filed on or before t-1
(usable the day after its filing date): proxies, postponements, extension
8-Ks, XBRL trust values, CRSP quotes through t. Outcomes (the date a meeting
was actually held, the amount actually paid, the payment date, a liquidation)
are used only to settle positions, never to choose them.
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
import spac_proxy as sp
import spac_trust as st

CACHE = st.CACHE
PERIODS = {"design": ("2010-01-01", "2020-12-31"), "confirm": ("2021-01-01", "2025-12-31")}
RECENT = ("2024-01-01", "2025-12-31")        # the confirmation's recency check

# ---- declared rule (Phase 3). Every number is cited or labeled ASSUMPTION.
ACCOUNT = 25_000.0
MAX_W = 0.10                 # at most 10% of the account per SPAC
ADV_CAP = 0.25               # at most 25% of the 20-day median dollar volume
FEE = 0.0                    # Fidelity: "Voluntary corporate reorganization $0 per transaction"
FEE_SENS = 38.0              # E*TRADE voluntary action
HAIRCUT = 0.02               # $/share ASSUMPTION: shortfall, excise tax, expenses, on estimated amounts
THRESH = 0.02                # annualized net yield over T-bills at entry
MAX_DAYS = 365               # NRO at most 365 days away
SETTLE = 2                   # ASSUMPTION: a fill must settle (T+2) before the redemption deadline
CUTOFF = 1                   # ASSUMPTION: plus 1 business day for the broker's cutoff (audit rule b)
SUSPECT = 0.10               # ask more than 10% below amount_t: a data error, excluded and counted
PAY_LAG_MEETING = 5          # ASSUMPTION: business days from the meeting to payment, when not stated
PAY_LAG_LIQ = 10             # ASSUMPTION: business days from the charter deadline to liquidation payment
DEADLINE_LAG = 2             # redemption deadline, when the proxy gives none: 2 business days before the meeting
SAME_MEETING_DAYS = 60       # two proxies whose meeting dates are this close describe one meeting
CLOSE_DAYS = 120             # ASSUMPTION: a merger vote's redemptions are paid only if the deal closes within this
EXTENSION_LIKE = ("extension", "annual")    # meetings whose redemptions are paid whatever the vote


# ------------------------------------------------------------------ calendar

def calendar() -> np.ndarray:
    ff = pd.read_parquet(ROOT / "data" / "pead" / "ff_daily.parquet")
    cal = sorted(ff["date"].astype(str))
    extra = pd.bdate_range(pd.Timestamp(cal[-1]) + pd.Timedelta(days=1), "2026-12-31").strftime("%Y-%m-%d")
    return np.array(cal + list(extra))


CAL = None


def bday(d: str, n: int) -> str:
    global CAL
    if CAL is None:
        CAL = calendar()
    i = np.searchsorted(CAL, d)
    return CAL[min(max(i + n, 0), len(CAL) - 1)]


# ------------------------------------------------------------------ timelines

def timelines(which: str | None = None) -> dict:
    """Per permno, what was known when, and what happened.

    known (each usable from the day after `filed`):
      meetings: [{id, filed, meeting, deadline, right, stated, kind}] and
                revisions [{id, filed, meeting}] from postponements
      charter:  [(filed, deadline)], starting with the prospectus deadline
    outcome:
      held[id], paid[id], pay_date[id]; liquidation (amount, date) if any."""
    W = sp.windows().set_index("permno")
    L = pd.read_csv(CACHE / "links.csv", dtype=str).dropna(subset=["permno"])
    L["permno"] = L["permno"].astype(float).astype(int)
    L = L.set_index("permno")
    names = [f"proxy_events{'_' + w if w else ''}.csv" for w in (["design", "confirm"] if which is None else [which])]
    E = pd.concat([pd.read_csv(CACHE / n, dtype={"cik": str, "items": str}) for n in names if (CACHE / n).exists()])
    E = E[E["status"] == "ok"].sort_values(["filed", "acc"])
    F8 = pd.read_csv(CACHE / "filings.csv", dtype=str)
    F8 = F8[F8["form"].isin(["8-K", "8-K/A"])]
    out = {}
    for p, w in W.iterrows():
        e = E[E["permno"] == p]
        ipo = w["ipo"]
        months = L.loc[p, "deadline_months"] if p in L.index else None
        months = int(float(months)) if isinstance(months, str) and months not in ("", "nan") else 24
        first = (pd.Timestamp(ipo) + pd.DateOffset(months=months)).strftime("%Y-%m-%d")
        charter = [(ipo, first, "prospectus")]
        meetings, revisions = [], []
        for r in e.itertuples():
            if r.kind_of_filing == "proxy":
                if not isinstance(r.meeting, str) or r.kind == "not_a_proxy" or r.meeting <= r.filed:
                    continue
                same = [m for m in meetings if abs((pd.Timestamp(m["meeting_last"]) - pd.Timestamp(r.meeting)).days)
                        <= SAME_MEETING_DAYS and m["meeting_last"] >= r.filed]
                if same:
                    m = same[-1]
                    revisions.append({"id": m["id"], "filed": r.filed, "meeting": r.meeting,
                                      "stated": r.stated_amount if r.stated_amount == r.stated_amount else None,
                                      "deadline": r.redemption_deadline if isinstance(r.redemption_deadline, str) else None})
                    m["meeting_last"] = max(m["meeting_last"], r.meeting)
                    m["right"] = m["right"] or bool(r.redemption_right)
                    continue
                meetings.append({"id": len(meetings), "filed": r.filed, "meeting": r.meeting, "meeting_last": r.meeting,
                                 "deadline": r.redemption_deadline if isinstance(r.redemption_deadline, str) else None,
                                 "right": bool(r.redemption_right), "kind": r.kind,
                                 "stated": r.stated_amount if r.stated_amount == r.stated_amount else None})
            else:
                if isinstance(getattr(r, "postponed_to", None), str):
                    pend = [m for m in meetings if m["filed"] < r.filed and m["meeting_last"] >= bday(r.filed, -3)]
                    if pend:
                        m = pend[-1]
                        revisions.append({"id": m["id"], "filed": r.filed, "meeting": r.postponed_to,
                                          "stated": None, "deadline": None})
                        m["meeting_last"] = max(m["meeting_last"], r.postponed_to)
                # an approved or sponsor-funded extension: 8-Ks only (a DEFA14A states a proposal)
                if r.form.startswith("8-K") and isinstance(getattr(r, "extended_to", None), str) \
                        and r.extended_to > charter[-1][1]:
                    charter.append((r.filed, r.extended_to, r.acc))
        # outcomes: the vote 8-K (item 5.07) nearest each meeting's last scheduled date
        votes = sorted(e.loc[e["items"].fillna("").str.contains("5.07"), "report"].dropna().unique())
        upd = e[e["kind_of_filing"] == "update"]
        held, paid, pay_date, paid_src = {}, {}, {}, {}
        for m in meetings:
            lo, hi = bday(m["meeting"], -3), (pd.Timestamp(m["meeting_last"]) + pd.Timedelta(days=45)).strftime("%Y-%m-%d")
            v = [x for x in votes if lo <= x <= hi]
            held[m["id"]] = v[0] if v else m["meeting_last"]
            h = held[m["id"]]
            after = upd[(upd["filed"] >= h) & (upd["filed"] <= (pd.Timestamp(h) + pd.Timedelta(days=30)).strftime("%Y-%m-%d"))]
            ps = after["per_share"].dropna() if "per_share" in after else pd.Series(dtype=float)
            if len(ps):
                v = float(ps.iloc[0])
                if m["stated"] and abs(v - m["stated"]) > 1.0:
                    paid_src[m["id"]] = "discarded_implausible"   # > $1 from the stated amount: a misparse
                else:
                    paid[m["id"]], paid_src[m["id"]] = v, "8-K"
            pay_date[m["id"]] = bday(h, PAY_LAG_MEETING)
        liq = None
        if not w["merged"] and w["last_crsp"] < st.CRSP_LAST:
            flag = upd["liquidation"].astype(str).eq("True") if "liquidation" in upd else pd.Series(False, index=upd.index)
            near = upd[(upd["filed"] >= (pd.Timestamp(w["end"]) - pd.Timedelta(days=120)).strftime("%Y-%m-%d")) & flag]
            amt = pd.Series(dtype=float)
            if len(near):
                la = near["liquidation_amount"] if "liquidation_amount" in near else pd.Series(np.nan, index=near.index)
                amt = la.fillna(near["per_share"]).dropna()
            dt = near["liquidation_date"].dropna() if len(near) and "liquidation_date" in near else pd.Series(dtype=str)
            liq = {"amount": float(amt.iloc[-1]) if len(amt) else None,
                   "date": dt.iloc[-1] if len(dt) else None, "found_8k": len(near) > 0}
        f8 = F8[(F8["cik"] == w["cik"]) & (F8["filed"] > ipo)]
        items = f8["items"].fillna("")
        out[p] = {"cik": w["cik"], "ipo": ipo, "end": w["end"], "merged": bool(w["merged"]), "last_crsp": w["last_crsp"],
                  "meetings": meetings, "revisions": revisions, "charter": charter,
                  "votes": sorted(f8.loc[items.str.contains("5.07", regex=False), "filed"]),
                  "terminated": sorted(f8.loc[items.str.contains("1.02", regex=False), "filed"]),
                  "held": held, "paid": paid, "paid_src": paid_src, "pay_date": pay_date, "liquidation": liq}
    return out


def known_at(tl: dict, t: str) -> dict:
    """What a holder knew after the close of day t-1 (filings dated before t):
    the pending meetings with a redemption right, each at its latest announced
    date, the charter deadline in force, and the next redemption opportunity."""
    ms = {}
    for m in tl["meetings"]:
        if m["filed"] < t:
            ms[m["id"]] = dict(m)
    for r in tl["revisions"]:
        if r["filed"] < t and r["id"] in ms:
            m = ms[r["id"]]
            m["meeting"] = r["meeting"]
            if r.get("stated"):
                m["stated"] = r["stated"]
            m["deadline"] = r.get("deadline")
    charter = [c for c in tl["charter"] if c[0] < t or c[2] == "prospectus"][-1][1]
    pend, passed = [], []
    for m in ms.values():
        dl = m["deadline"] if m["deadline"] and m["deadline"] <= m["meeting"] else bday(m["meeting"], -DEADLINE_LAG)
        if m["right"] and dl >= t:
            pend.append({**m, "deadline": dl})
        elif m["right"]:
            passed.append({**m, "deadline": dl})
    pend.sort(key=lambda m: m["deadline"])
    # Audit rule d (2026-10-29): after a meeting's redemption deadline a buyer has
    # no redemption right until the outcome is known. A merger or tender: until a
    # termination 8-K (item 1.02); an extension: until its vote 8-K or extension.
    blocked = False
    if not pend:
        for m in passed:
            if m["kind"] in EXTENSION_LIKE:
                done = any(m["deadline"] <= x < t for x in tl.get("votes", [])) or \
                    any(m["deadline"] <= c[0] < t for c in tl["charter"] if c[2] != "prospectus")
            else:
                done = any(m["filed"] < x < t for x in tl.get("terminated", []))
            blocked = blocked or not done
    # A filed proxy for a meeting after the charter deadline in force means the
    # deadline moved (an extension or automatic extension the parser missed):
    # the meeting is the next opportunity either way.
    if pend:
        m = pend[0]
        return {"kind": "meeting", "id": m["id"], "deadline": m["deadline"], "pay": bday(m["meeting"], PAY_LAG_MEETING),
                "stated": m["stated"], "charter": charter, "blocked": False}
    return {"kind": "charter", "id": None, "deadline": charter, "pay": bday(charter, PAY_LAG_LIQ),
            "stated": None, "charter": charter, "blocked": blocked}


# ------------------------------------------------------------------ the daily panel

def panel(lo: str, hi: str, which: str | None = None) -> tuple[pd.DataFrame, dict]:
    """SPAC-days on [lo, hi] with quotes, E_t, NRO_t and amount_t, all entry-time."""
    md = da.guard("spac_trial")
    hi = min(hi, md, st.CRSP_LAST)
    growth = st.tbill()
    L = pd.read_csv(CACHE / "links.csv", dtype=str)
    T = pd.read_csv(CACHE / "trust_facts.csv", dtype={"cik": str})
    est = st.trust_estimates(L, T, growth)
    tls = timelines(which)
    px = pd.read_parquet(CACHE / "crsp_daily.parquet")
    px = px[px["permno"].isin(list(tls)) & (px["dlycaldt"] <= hi)].copy()
    for c in ("dlyprc", "dlybid", "dlyask", "dlyprcvol", "dlyvol"):
        px[c] = pd.to_numeric(px[c], errors="coerce").astype(float)
    px = px.sort_values(["permno", "dlycaldt"])
    ok = valid_quote(px["dlybid"], px["dlyask"])
    px["bid"], px["ask"] = px["dlybid"].where(ok), px["dlyask"].where(ok)
    # a negative CRSP price is the bid/ask midpoint of a no-trade day: worthless when the quote is a stub
    px["close"] = px["dlyprc"].abs().where((px["dlyprc"] > 0) | ((px["dlyprc"] < 0) & ok))
    px["hs"] = ((px["ask"] - px["bid"]) / 2)
    px["adv20"] = px.groupby("permno")["dlyprcvol"].transform(lambda x: x.shift(1).rolling(20, min_periods=10).median())
    # every valid bid on the permno, whatever the security has become (audit rule c's exit)
    for p, g in px.dropna(subset=["bid"]).groupby("permno"):
        tls[p]["bids"] = g.set_index("dlycaldt")["bid"]
    px = px[px["dlycaldt"] >= lo]
    rows = []
    for p, g in px.groupby("permno"):
        tl, es = tls[p], est.get(p)
        if es is None:
            continue                                   # no parsed trust value: never assume $10
        g = g[(g["dlycaldt"] > tl["ipo"]) & (g["dlycaldt"] < tl["end"] if tl["merged"] else g["dlycaldt"] <= tl["end"])]
        if g.empty:
            continue
        # proxy-stated amounts join E_t's bases from the day after the proxy's filing date
        bases = list(es)
        for m in tl["meetings"]:
            if m["stated"] and 5 <= m["stated"] <= 12.5:
                bases.append((st.et._add_days(m["filed"], 1), float(m["stated"]), m["meeting"]))
        bases.sort()
        av = [b[0] for b in bases]
        for r in g.itertuples():
            t = r.dlycaldt
            j = np.searchsorted(av, t, side="right") - 1
            b = bases[j]
            E_t = b[1] * growth[t] / growth[b[2]] if b[2] <= t else b[1]
            k = known_at(tl, t)
            if k["deadline"] < t:
                continue                               # past the charter deadline with no extension known
            pay = k["pay"]
            amount = k["stated"] if k["stated"] else E_t * growth[pay] / growth[t]
            rows.append((p, t, r.close, r.bid, r.ask, r.hs, r.adv20, r.dlyvol, E_t, amount, bool(k["stated"]),
                         k["kind"], k["id"], k["deadline"], pay, k["charter"], k["blocked"]))
    d = pd.DataFrame(rows, columns=["permno", "d", "close", "bid", "ask", "hs", "adv20", "vol", "E_t", "amount",
                                    "amount_stated", "nro_kind", "nro_id", "deadline", "pay", "charter", "blocked"])
    d["hs"] = d["hs"].fillna(d.groupby("d")["hs"].transform("median"))
    return d, tls


STUB = 0.90                  # a quote whose bid is under 90% of its ask is a stub, not a market


def valid_quote(bid: pd.Series, ask: pd.Series) -> pd.Series:
    """CRSP closing quotes include stubs ($0.01 bid against a $10.30 ask on
    2016-11-23, permno 15362): such a day has no quote."""
    return (bid > 0) & (ask >= bid) & (bid >= STUB * ask)


def yields(d: pd.DataFrame, fee: float, haircut: float) -> pd.DataFrame:
    """Annualized net yield to NRO_t over T-bills, at day t's ask."""
    rf = rf_daily()
    d = d.copy()
    d["pos_usd"] = np.minimum(ACCOUNT * MAX_W, ADV_CAP * d["adv20"].fillna(0))
    d["shares"] = np.floor(d["pos_usd"] / d["ask"])
    yrs = (pd.to_datetime(d["pay"]) - pd.to_datetime(d["d"])).dt.days.clip(lower=1) / 365.25
    net = (d["amount"] - d["ask"] - fee / d["shares"].where(d["shares"] > 0) - haircut) / d["ask"]
    d["yld"] = net / yrs - rf.reindex(d["d"]).fillna(0).to_numpy() * 252
    d["days_to_pay"] = (pd.to_datetime(d["pay"]) - pd.to_datetime(d["d"])).dt.days
    d["suspect"] = (d["amount"] - d["ask"]) / d["ask"] > SUSPECT
    return d


def rf_daily() -> pd.Series:
    ff = pd.read_parquet(ROOT / "data" / "pead" / "ff_daily.parquet").assign(date=lambda x: x["date"].astype(str))
    return ff.set_index("date")["rf"]


# ------------------------------------------------------------------ the declared portfolio

def simulate(d: pd.DataFrame, tls: dict, lo: str, hi: str, fee=FEE, haircut=HAIRCUT, thresh=THRESH,
             perfect: bool = False) -> dict:
    """Daily NAV marked at the bid; see the declaration in docs/SPAC_TRUST_ARB.md.

    perfect=True is the ceiling: unlimited capital, every eligible day is
    entered (not a strategy; Phase 0 2a)."""
    growth = st.tbill()
    rf = rf_daily()
    d = yields(d[(d["d"] >= lo) & (d["d"] <= hi)], fee, haircut)
    days = sorted(d["d"].unique())
    by_day = {k: g.set_index("permno") for k, g in d.groupby("d")}
    cash, held, rows, trades, owed = ACCOUNT, {}, [], [], []
    pending = []
    counts = {"suspect_days": int(d["suspect"].sum()), "unparsed_meeting_exits": 0, "fallback_paid": 0,
              "skipped_cash": 0, "skipped_no_ask": 0, "skipped_suspect_fill": 0, "skipped_deadline_buffer": 0,
              "skipped_blocked": 0, "void_redemptions": 0}

    def close_trade(h, day, proceeds, how, paid=None):
        trades[h["i"]].update(exit_day=str(day), proceeds=proceeds, exit=how, paid=paid,
                              pnl=proceeds - trades[h["i"]]["cost"])

    for t in days:
        g = by_day[t]
        # 1. redemption and liquidation payments due today
        for o in [o for o in owed if o["pay"] <= t]:
            owed.remove(o)
            cash += o["amount"]
        # 2. yesterday's decisions fill at today's ask
        for p in pending:
            if p in held or p not in g.index:
                continue
            q = g.loc[p]
            if not (q["ask"] > 0):
                counts["skipped_no_ask"] += 1
                continue
            if q["suspect"]:                                  # audit rule a: the fill day's ask, too
                counts["skipped_suspect_fill"] += 1
                continue
            if bday(t, SETTLE + CUTOFF) > q["deadline"]:      # audit rule b
                counts["skipped_deadline_buffer"] += 1
                continue
            if q["blocked"]:                                  # audit rule d: a passed deadline, outcome unknown
                counts["skipped_blocked"] += 1
                continue
            usd = min(ACCOUNT * MAX_W, ADV_CAP * (q["adv20"] if q["adv20"] == q["adv20"] else 0))
            sh = np.floor(usd / q["ask"])
            cost = sh * q["ask"]
            if sh <= 0:
                continue
            if cost > cash and not perfect:
                counts["skipped_cash"] += 1
                continue
            cash -= cost
            held[p] = {"shares": sh, "i": len(trades), "entry": t, "last_mark": q["ask"], "E_entry": q["E_t"],
                       "void": set(), "last_bid": q["bid"] if q["bid"] > 0 else np.nan,
                       "nro": {"kind": q["nro_kind"], "id": q["nro_id"], "deadline": q["deadline"],
                               "stated": q["amount"] if q["amount_stated"] else None, "amount": q["amount"]}}
            trades.append({"permno": p, "entry_day": t, "cost": cost, "shares": sh, "ask": q["ask"],
                           "amount_entry": q["amount"], "yld": q["yld"], "nro_kind": q["nro_kind"],
                           "capacity_limited": usd < ACCOUNT * MAX_W, "stated_at_entry": q["amount_stated"]})
        # 3. exits. Each position remembers its latest known NRO, so a meeting
        # deadline on a day with no quote row is still acted on the next day.
        for p in list(held):
            h, tl = held[p], tls[p]
            q = g.loc[p] if p in g.index else None
            if q is not None:
                h["nro"] = {"kind": q["nro_kind"], "id": q["nro_id"], "deadline": q["deadline"],
                            "stated": q["amount"] if q["amount_stated"] else None, "amount": q["amount"]}
            n = h["nro"]
            bid = q["bid"] if q is not None and q["bid"] > 0 else np.nan
            if bid == bid:
                h["last_bid"] = bid
            if bid >= n["amount"]:
                proceeds = h["shares"] * bid
                cash += proceeds
                close_trade(h, t, proceeds, "sold_above_amount")
                del held[p]
                continue
            if n["kind"] == "meeting" and n["deadline"] <= t and int(n["id"]) not in h["void"]:
                mid = int(n["id"])
                kind = next(m["kind"] for m in tl["meetings"] if m["id"] == mid)
                closes = tl["merged"] and pd.Timestamp(tl["held"][mid]) - pd.Timedelta(days=5) <= pd.Timestamp(tl["end"]) \
                    <= pd.Timestamp(tl["held"][mid]) + pd.Timedelta(days=CLOSE_DAYS)
                if n["stated"] and bid >= n["stated"]:
                    proceeds = h["shares"] * bid
                    cash += proceeds
                    close_trade(h, t, proceeds, "sold_above_amount")
                elif kind not in EXTENSION_LIKE and not closes:
                    # audit rule d: a merger or tender that never closed pays no redemption;
                    # the shares come back and the position stays open
                    h["void"].add(mid)
                    counts["void_redemptions"] += 1
                    continue
                else:
                    paid = tl["paid"].get(mid)
                    if paid is not None and abs(paid - n["amount"]) > 1.0:
                        paid = None                           # > $1 from the amount known at entry: a misparse
                        counts["paid_discarded"] = counts.get("paid_discarded", 0) + 1
                    if paid is None:
                        paid = (n["stated"] or n["amount"]) - haircut
                        counts["fallback_paid"] += 1
                    proceeds = h["shares"] * paid - fee
                    pay = max(tl["pay_date"][mid], bday(t, 1))
                    if kind not in EXTENSION_LIKE:
                        pay = max(pay, bday(tl["end"], SETTLE))      # a merger's redemptions are paid at closing
                    owed.append({"pay": pay, "amount": proceeds})
                    close_trade(h, pay, proceeds, "redeemed", paid=paid)
                    trades[h["i"]]["stated_amount"] = n["stated"]
                del held[p]
                continue
            # Audit rule c: a merger the parser found no meeting for is not settled at E_t. The
            # position exits at the bid on the first trading day after the charter deadline in
            # force or the phase end, whichever is first, whatever the security has become.
            if tl["merged"]:
                x = tl["end"]
                if n["kind"] == "charter" and n["deadline"] < x:
                    x = n["deadline"]
                if t > x:
                    counts["unparsed_meeting_exits"] += 1
                    bids = tl.get("bids", pd.Series(dtype=float))
                    after = bids[bids.index > x]
                    b = float(after.iloc[0]) if len(after) and after.index[0] <= bday(x, 10) else h["last_bid"]
                    b = b if b == b else h["last_mark"]
                    proceeds = h["shares"] * b
                    cash += proceeds
                    close_trade(h, t, proceeds, "unparsed_meeting", paid=b)
                    del held[p]
                continue
            # a liquidation: the SPAC's last CRSP day has passed with the position open
            if q is None and t > tl["last_crsp"]:
                liq = tl["liquidation"] or {}
                amt = liq.get("amount")
                if amt is None:
                    amt = h["E_entry"] * growth[t] / growth[h["entry"]] - haircut
                    counts["fallback_paid"] += 1
                pay = liq.get("date") or bday(tl["charter"][-1][1], PAY_LAG_LIQ)
                pay = max(pay, bday(t, 1))
                proceeds = h["shares"] * amt
                owed.append({"pay": pay, "amount": proceeds})
                close_trade(h, pay, proceeds, "liquidated", paid=amt)
                del held[p]
        # Never held past a meeting's redemption deadline: below the amount it is redeemed,
        # at or above it sold, so "an extension passed and the position was not closed" cannot occur.
        assert not any(h["nro"]["kind"] == "meeting" and h["nro"]["deadline"] <= t and int(h["nro"]["id"]) not in h["void"]
                       for h in held.values()), \
            f"position held past a redemption deadline on {t}"
        # 4. NAV at the bid (no quote: the close less the day's median half-spread)
        value = 0.0
        for p, h in held.items():
            if p in g.index:
                q = g.loc[p]
                m = q["bid"] if q["bid"] > 0 else (q["close"] - q["hs"] if q["close"] == q["close"] else np.nan)
                # the entry rule's data-error filter, applied to marks: a redeemable share quoted more
                # than 10% under its known amount is a phase that already ended or a bad quote
                if m == m and (q["amount"] - m) / m > SUSPECT:
                    m = np.nan
                    counts["suspect_marks"] = counts.get("suspect_marks", 0) + 1
                if m == m:
                    h["last_mark"] = m
            value += h["shares"] * h["last_mark"]
        value += sum(o["amount"] for o in owed)
        rows.append({"d": t, "cash": cash, "nav": cash + value, "n_held": len(held),
                     "invested": value / max(cash + value, 1e-9)})
        cash *= 1 + float(rf.get(t, 0.0))
        # 5. decide after the close: highest annualized net yield first
        el = g[(g["yld"] >= thresh) & (g["days_to_pay"] <= MAX_DAYS) & (g["shares"] > 0) & ~g["suspect"]
               & (g["ask"] > 0) & ~g["blocked"].astype(bool)]
        pending = [p for p in el.sort_values("yld", ascending=False).index if p not in held]
    nav = pd.DataFrame(rows).set_index("d")
    tr = pd.DataFrame(trades)
    return {"nav": nav, "trades": tr, "counts": counts}


# ------------------------------------------------------------------ reports

def monthly(nav: pd.DataFrame) -> pd.DataFrame:
    """Monthly return, T-bill (FF rf compounded) and excess, plus the market (FF Mkt-RF)."""
    ff = pd.read_parquet(ROOT / "data" / "pead" / "ff_daily.parquet").assign(date=lambda x: x["date"].astype(str))
    ff = ff.set_index("date").reindex(nav.index).fillna(0.0)
    m = pd.DataFrame({"nav": nav["nav"], "rf": ff["rf"], "mktrf": ff["mktrf"]})
    m["month"] = m.index.str[:7]
    m["r"] = m["nav"].pct_change().fillna(m["nav"].iloc[0] / ACCOUNT - 1)
    g = m.groupby("month")
    out = pd.DataFrame({"r": g["r"].apply(lambda x: (1 + x).prod() - 1),
                        "rf": g["rf"].apply(lambda x: (1 + x.shift(1).fillna(0)).prod() - 1),
                        "mktrf": g["mktrf"].apply(lambda x: (1 + x).prod() - 1)})
    out["excess"] = out["r"] - out["rf"]
    return out


def summarize(sim: dict, label: str, n_trials: int | None = None) -> dict:
    from deflated_sharpe import deflated_sharpe_ratio
    from evaluation import newey_west_t
    from scipy.stats import norm
    nav, tr = sim["nav"], sim["trades"]
    m = monthly(nav)
    nw = newey_west_t(m["excess"])
    sd = m["excess"].std()
    sharpe = m["excess"].mean() / sd * np.sqrt(12) if sd > 0 else np.nan
    yr = m.groupby(m.index.str[:4]).apply(lambda x: ((1 + x["r"]).prod() - (1 + x["rf"]).prod()) * ACCOUNT)
    dd = (nav["nav"] / nav["nav"].cummax() - 1).min()
    X = np.c_[np.ones(len(m)), m["mktrf"]]
    beta = np.linalg.lstsq(X, m["excess"], rcond=None)[0][1] if len(m) > 2 else np.nan
    down = m[(m["mktrf"] + m["rf"]) < -0.04]
    dbeta = (np.linalg.lstsq(np.c_[np.ones(len(down)), down["mktrf"]], down["excess"], rcond=None)[0][1]
             if len(down) > 2 else np.nan)
    rec = m.loc[m.index >= RECENT[0][:7], "excess"]
    row = {"run": label, "months": nw["n"], "mean_monthly_excess": nw["mean"], "t_nw": nw["t"],
           "p_one_sided": float(1 - norm.cdf(nw["t"])) if nw["t"] == nw["t"] else np.nan,
           "sharpe": sharpe, "excess_usd_per_yr": float(yr.mean()),
           "worst_year_usd": float(yr.min()), "worst_year": yr.idxmin() if len(yr) else None,
           "worst_month_usd": float(m["excess"].min() * nav["nav"].mean()), "worst_month": m["excess"].idxmin(),
           "max_drawdown": float(dd), "beta": float(beta), "down_months": len(down), "down_beta": float(dbeta),
           "recent_mean_monthly": float(rec.mean()) if len(rec) else np.nan,
           "avg_invested": float(nav["invested"].mean()), "entries": len(tr)}
    if n_trials:
        row["deflated_sharpe"] = deflated_sharpe_ratio(sharpe, n_trials, nw["n"], periods_per_year=12)
    if len(tr):
        done = tr.dropna(subset=["exit"])
        row.update({f"exit_{k}": int(v) for k, v in done["exit"].value_counts().items()})
        red = done[done["exit"].isin(["redeemed", "liquidated"])]
        if len(red):
            err = red["paid"] - red["amount_entry"]
            row["paid_minus_amount_entry_median"] = float(err.median())
            row["paid_minus_amount_entry_p10"] = float(err.quantile(0.1))
            st_ = red.dropna(subset=["stated_amount"]) if "stated_amount" in red else red.iloc[:0]
            row["paid_below_stated_10c_share"] = float(((st_["paid"] < st_["stated_amount"] - 0.10)).mean()) if len(st_) else np.nan
        row["days_locked_median"] = float((pd.to_datetime(done["exit_day"]) - pd.to_datetime(done["entry_day"])).dt.days.median())
        row["capacity_limited"] = float(tr["capacity_limited"].mean())
        pnl = done.groupby("permno")["pnl"].sum()
        row["top5_profit_share"] = float(pnl.nlargest(5).sum() / pnl[pnl > 0].sum()) if (pnl > 0).any() else np.nan
    row.update({f"n_{k}": v for k, v in sim["counts"].items()})
    row.update({f"y{k}": float(v) for k, v in yr.items()})
    return row


def ceiling(d: pd.DataFrame, lo: str, hi: str, fee: float, haircut: float) -> dict:
    """Perfect foresight, unlimited capital: each SPAC's best net-yield day per
    redemption window x its capped position, earning the discount net of costs
    (amount_t - ask - fee - haircut), $/yr."""
    y = yields(d[(d["d"] >= lo) & (d["d"] <= hi)], fee, haircut)
    y = y[(y["ask"] > 0) & (y["shares"] > 0) & ~y["suspect"]]
    y["usd"] = (y["amount"] - y["ask"] - haircut) * y["shares"] - fee
    best = y.loc[y.groupby(["permno", "deadline"])["usd"].idxmax()]
    best = best[best["usd"] > 0]
    years = (pd.Timestamp(hi) - pd.Timestamp(lo)).days / 365.25
    return {"fee": fee, "windows": len(best), "spacs": best["permno"].nunique(),
            "ceiling_usd_per_yr": best["usd"].sum() / years,
            "median_ann_yield_best_day": float(best["yld"].median()) if len(best) else np.nan,
            "median_ann_yield_all_positive_days": float(y.loc[y["yld"] > 0, "yld"].median())}


def gate() -> int:
    """Phase 2 on the design period: (a) the ceiling and the declared rule at
    $250/yr; (b) the declared rule's monthly excess Sharpe, NAV at the bid,
    halved, into P(pass) for 5 confirmation years."""
    from deflated_sharpe import pass_probability
    lo, hi = PERIODS["design"]
    d, tls = panel(lo, hi)
    rows = [ceiling(d, lo, hi, FEE, HAIRCUT), ceiling(d, lo, hi, FEE_SENS, HAIRCUT)]
    for r in rows:
        print({k: (round(v, 4) if isinstance(v, float) else v) for k, v in r.items()})
    sim = simulate(d, tls, lo, hi)
    s = summarize(sim, "design_gate")
    prior = s["sharpe"] / 2
    s.update(prior_sharpe=prior, p_pass=pass_probability(prior, 5.0) if prior == prior else np.nan,
             ceiling_usd_per_yr=rows[0]["ceiling_usd_per_yr"], ceiling_usd_per_yr_fee38=rows[1]["ceiling_usd_per_yr"])
    for k, v in s.items():
        print(f"  {k:<36}{v}")
    a = rows[0]["ceiling_usd_per_yr"] >= 250 and s["excess_usd_per_yr"] >= 250
    b = s["p_pass"] >= 0.25
    print(f"\n(a) economic size: {'PASS' if a else 'KILL'}  (b) P(pass) = {s['p_pass']:.3f}: {'PASS' if b else 'KILL'}")
    pd.DataFrame([s]).to_csv(ROOT / "reports" / "spac_trial_gate.csv", index=False)
    sim["trades"].to_csv(CACHE / "trades_design_gate.csv", index=False)
    return 0


def validate(which: str = "design") -> int:
    """Automated consistency checks on the event data: pass rates only, never
    returns. The only checks run on the confirmation period."""
    tls = timelines(which)
    lo, hi = PERIODS[which]
    keep = (lambda ipo: ipo <= hi) if which == "design" else (lambda ipo: ipo >= lo)
    tls = {p: tl for p, tl in tls.items() if keep(tl["ipo"])}
    rows = []
    for p, tl in tls.items():
        ch = [c[1] for c in tl["charter"]]
        for m in tl["meetings"]:
            mid = m["id"]
            paid = tl["paid"].get(mid)
            rows.append({"permno": p, "kind": m["kind"], "right": m["right"], "has_stated": m["stated"] is not None,
                         "has_paid": paid is not None, "paid_within_10c": (abs(paid - m["stated"]) <= 0.10)
                         if paid is not None and m["stated"] else np.nan,
                         "paid_10c_below_stated": (paid < m["stated"] - 0.10) if paid is not None and m["stated"] else np.nan,
                         "deadline_before_meeting": (m["deadline"] <= m["meeting"]) if m["deadline"] else np.nan,
                         "payment_after_meeting": tl["pay_date"][mid] >= tl["held"][mid],
                         "charter_order": all(a < b for a, b in zip(ch, ch[1:]))})
    v = pd.DataFrame(rows)
    liq = [tl["liquidation"] for tl in tls.values() if tl["liquidation"]]
    out = {"period": which, "spacs": len({r["permno"] for r in rows}), "meetings": len(v),
           "meetings_with_right": int(v["right"].sum()) if len(v) else 0}
    r = v[v["right"]] if len(v) else v
    for c in ("has_stated", "has_paid", "paid_within_10c", "paid_10c_below_stated", "deadline_before_meeting",
              "payment_after_meeting", "charter_order"):
        out[c] = float(pd.to_numeric(r[c], errors="coerce").mean()) if len(r) else np.nan
    out["liquidations"] = len(liq)
    out["liquidation_with_8k"] = float(np.mean([x["found_8k"] for x in liq])) if liq else np.nan
    out["liquidation_with_amount"] = float(np.mean([x["amount"] is not None for x in liq])) if liq else np.nan
    out["liquidation_with_date"] = float(np.mean([x["date"] is not None for x in liq])) if liq else np.nan
    for k, x in out.items():
        print(f"  {k:<28}{x}")
    pd.DataFrame([out]).to_csv(ROOT / "reports" / f"spac_events_check_{which}.csv", index=False)
    return 0


def run(period: str) -> int:
    """The declared rule on one period: primary row and the sensitivity rows."""
    import json
    lo, hi = PERIODS[period]
    n_trials = None
    try:
        import registry
        n_trials = registry.trials()["n_trials"]
    except Exception:
        pass
    d, tls = panel(lo, hi, None)
    out = []
    for label, kw in (("primary", {}), ("fee_38", {"fee": FEE_SENS}), ("haircut_0.05", {"haircut": 0.05}),
                      ("yield_1pct", {"thresh": 0.01}), ("yield_4pct", {"thresh": 0.04})):
        sim = simulate(d, tls, lo, hi, **kw)
        s = summarize(sim, f"{period}_{label}", n_trials if label == "primary" else None)
        out.append(s)
        if label == "primary":
            sim["trades"].to_csv(CACHE / f"trades_{period}.csv", index=False)
            sim["nav"].to_csv(CACHE / f"nav_{period}.csv")
            print(json.dumps({k: (round(v, 5) if isinstance(v, float) else v) for k, v in s.items()}, indent=1,
                             default=str))
        else:
            print(f"{label}: mean {s['mean_monthly_excess']:.5f} t {s['t_nw']:.2f} ${s['excess_usd_per_yr']:.0f}/yr")
    rep = pd.DataFrame(out)
    rep.to_csv(ROOT / "reports" / f"spac_trial_{period}.csv", index=False)
    if period == "confirm":
        p = rep.iloc[0]
        ok = p["mean_monthly_excess"] > 0 and p["t_nw"] >= 3 and rep.iloc[1]["mean_monthly_excess"] > 0 \
            and p["recent_mean_monthly"] > 0
        print(f"\nCONFIRMATION: {'PASS' if ok else 'FAIL'}")
    return 0


def main(a) -> int:
    if a.cmd == "gate":
        return gate()
    if a.cmd == "validate":
        return validate("confirm" if a.confirm else "design")
    if a.cmd == "run":
        return run("confirm" if a.confirm else "design")
    raise SystemExit(f"unknown command {a.cmd}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", nargs="?", default="run")
    ap.add_argument("--confirm", action="store_true")
    a = ap.parse_args()
    sys.exit(main(a))
