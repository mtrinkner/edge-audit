"""Truncation audit for the event studies: PEAD, SPAC, reverse split, odd lot.

THE TEST

A decision on day D may use only what existed by D. So take the study's real
inputs, remove every filing made after D, replace every later price with a
wrong one, change the outcomes, and rebuild. Every decision dated D or earlier
must come out identical. If one changes, it was reading the future.

The rule-specific tests in test_pead.py, test_spac_trial.py and
test_reverse_split.py cover the cases someone thought of; this one does not
need to know where a leak would be.

Filings are cut on their own filing dates, not on the `usable` field the code
derives from them, so a wrong `usable` is caught too. The market calendar is
kept: it is known in advance.

A TEST THAT CANNOT FAIL IS WORSE THAN NONE (tests/test_no_lookahead_intraday.py).
Each audit also runs against a planted leak, one of them the bug that
docs/REVERSE_SPLIT_ROUNDUP.md records, and must catch it.

DESIGN PERIODS ONLY. Confirmation data stays unread here. Nothing is written
outside a temporary directory, and no WRDS connection is made.
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "python"))

import odd_lot_study as ol
import pead
import reverse_split as rs
import spac_edgar as se
import spac_proxy as sp
import spac_trial as tr
import spac_trust as st

DATA = ROOT / "data"
has = lambda *paths: all((DATA / p).exists() for p in paths)
needs_rs = pytest.mark.skipif(not has("rsplit/parsed.csv", "rsplit/event_table.csv", "rsplit/crsp_daily.parquet"),
                              reason="reverse-split caches not on this machine")
needs_ol = pytest.mark.skipif(not has("edgar/deals.csv", "edgar/crsp_daily.parquet"),
                              reason="tender caches not on this machine")
needs_spac = pytest.mark.skipif(not has("spac/proxy_events_design.csv", "spac/crsp_daily.parquet"),
                                reason="SPAC caches not on this machine")
needs_pead = pytest.mark.skipif(not has("pead/events.parquet", "pead/ibes_events.parquet"),
                                reason="PEAD caches not on this machine")

SCRAMBLE = 3.0              # future prices become three times the real ones


# ------------------------------------------------------------------ reverse split

def rs_decision(ev: dict, g: pd.DataFrame, q: pd.DataFrame, cutoff: str):
    """What the rule has done by the close of `cutoff`: the entry and its terms."""
    days = [d for d in q.index if d < ev["exdt"]]
    d, k = rs.entry_day(rs.timeline(g), days)
    if d is None or d > cutoff:
        return None
    r = rs.payoff(ev, rs.timeline(g), q) or {}
    return (d, k["cls"], k["ratio"], k["effective"], r.get("entry_day"), r.get("ratio_entry"), r.get("ask"))


def rs_blind(ev: dict, g: pd.DataFrame, q: pd.DataFrame, cutoff: str) -> tuple:
    """The same event as seen on `cutoff`: later filings gone, later quotes
    wrong, and a split that happens later at a different ratio."""
    q2 = q.copy()
    later = q2.index > cutoff
    for c in ("bid", "ask", "dlybid", "dlyask", "dlyopen", "dlyprc"):
        q2.loc[later, c] = q2.loc[later, c] * SCRAMBLE
    return ({"exdt": rs.bday(ev["exdt"], 5), "n_crsp": 999.0}, g[g["accepted"] < cutoff], q2)


def rs_mismatches(events: list) -> list:
    bad = []
    for ev, g, q in events:
        tl = rs.timeline(g)
        first = min((f["usable"] for f in tl), default=None)
        if first is None:
            continue
        for cutoff in [d for d in q.index if rs.bday(first, -1) <= d < ev["exdt"]]:
            real = rs_decision(ev, g, q, cutoff)
            blind = rs_decision(*rs_blind(ev, g, q, cutoff), cutoff)
            if str(real) != str(blind):
                bad.append((ev, cutoff, real, blind))
    return bad


def rs_postponed_event() -> list:
    """Synthetic: terms filed 2024-06-03 for a 06-12 split; the entry is 06-10, and on 06-11
    the split is postponed to 06-20."""
    days = [rs.bday("2024-06-03", i) for i in range(25)]
    q = rs.quotes(pd.DataFrame([{"dlycaldt": d, "dlybid": 1.0, "dlyask": 1.02, "dlyopen": 1.01, "dlyprc": 1.01}
                                for d in days]))
    row = dict(status="ok", form="8-K", frac_class="ROUNDUP_HOLDER", ratio=10, ratio_is_range=False, cancel=False)
    g = pd.DataFrame([{**row, "accepted": "2024-06-03", "acc": "a", "effective": "2024-06-12"},
                      {**row, "accepted": "2024-06-11", "acc": "b", "effective": "2024-06-20"}])
    return [({"exdt": "2024-06-20", "n_crsp": 10.0}, g, q)]


def test_reverse_split_rule_passes_on_a_postponement():
    assert not rs_mismatches(rs_postponed_event())


def test_reverse_split_audit_catches_the_last_qualifying_day_bug(monkeypatch):
    """The lookahead docs/REVERSE_SPLIT_ROUNDUP.md records: the entry day chosen
    with terms learned later."""
    def leaky(tl, days):
        k = rs.known_at(tl, days[-1])
        if not k["effective"]:
            return None, None
        fits = [d for d in days if rs.bday(d, rs.settlement_days(d)) < k["effective"]]
        return (fits[-1], k) if fits else (None, None)
    monkeypatch.setattr(rs, "entry_day", leaky)
    assert rs_mismatches(rs_postponed_event())


@needs_rs
def test_reverse_split_decisions_on_the_real_events():
    lo, hi = rs.SPLIT["design"]
    e = pd.read_csv(rs.CACHE / "event_table.csv")
    e = e[(e["exdt"] >= lo) & (e["exdt"] <= hi) & (e["filings"] > 0)]
    ev = rs.events().set_index(["permno", "exdt"])
    P = pd.read_csv(rs.CACHE / "parsed.csv", dtype={"cik": str, "items": str})
    by = {k: g for k, g in P.groupby(["permno", "exdt"])}
    px = pd.read_parquet(rs.CACHE / "crsp_daily.parquet")
    pg = {p: g for p, g in px.groupby("permno")}
    events = []
    for r in e.itertuples():
        g = by.get((r.permno, r.exdt))
        if g is None or r.permno not in pg:
            continue
        a, b = rs.bday(r.exdt, -100), rs.bday(r.exdt, 15)
        p = pg[r.permno]
        events.append(({"exdt": r.exdt, "n_crsp": r.n_crsp}, g, rs.quotes(p[(p["dlycaldt"] >= a) & (p["dlycaldt"] <= b)])))
    assert len(events) > 500
    bad = rs_mismatches(events)
    assert not bad, f"{len(bad)} decisions changed; first: {[(b[0]['exdt'], b[1], b[2], b[3]) for b in bad[:3]]}"


# ------------------------------------------------------------------ odd lot

def ol_entry(trade, r, px, variant):
    t = trade(r, px, variant)
    return (t["entry_day"], t["entry"]) if t else None


def ol_blind(r: dict, px: pd.DataFrame, cutoff: str) -> tuple:
    """The deal as seen on `cutoff`: later amendments gone, later closes wrong, outcome unknown."""
    r2 = dict(r)
    kept = [x for x in str(r["ext_timeline"]).split(";") if ">" in x and x.split(">")[0] < cutoff]
    r2["ext_timeline"] = ";".join(kept)
    if isinstance(r["terminated_on"], str) and r["terminated_on"] > cutoff:
        r2["terminated_on"] = np.nan
    r2.update(outcome="completed", final_usd=1.0, final_expiration=r2["expiration"], payment_date=np.nan)
    p2 = px.copy()
    p2.loc[p2["d"] > cutoff, "close"] *= SCRAMBLE
    return pd.Series(r2), p2


def ol_mismatches(deals: list, trade=ol.trade) -> list:
    bad = []
    for r, px in deals:
        row = pd.Series(r)
        for v in ("E1", "E2"):
            real = ol_entry(trade, row, px, v)
            if real is None or not isinstance(r["expiration"], str) and v == "E2":
                continue
            cutoff = real[0]
            if ol_entry(trade, *ol_blind(r, px, cutoff), v) != real:
                bad.append((r["date"], v, real))
    return bad


def ol_extended_deal() -> list:
    """Synthetic: filed 2015-03-02, expires 03-30, extended on 04-10 (after E2) to 05-15."""
    days = [str(d.date()) for d in pd.bdate_range("2015-03-02", "2015-06-30")]
    px = pd.DataFrame({"d": days, "close": np.linspace(10, 12, len(days)), "cumfac": 1.0, "vol": 1000.0})
    r = dict(date="2015-03-02", expiration="2015-03-30", ext_timeline="2015-04-10>2015-05-15", terminated_on=np.nan,
             outcome="completed", final_usd=11.0, final_expiration="2015-05-15", payment_date=np.nan)
    return [(r, px)]


def test_odd_lot_rule_passes_on_a_late_extension():
    assert not ol_mismatches(ol_extended_deal())


def test_odd_lot_audit_catches_entry_on_the_final_expiration():
    def leaky(r, px, variant):
        r2 = r.copy()
        r2["expiration"] = r["final_expiration"]
        return ol.trade(r2, px, variant)
    assert ol_mismatches(ol_extended_deal(), leaky)


@needs_ol
def test_odd_lot_decisions_on_the_real_deals():
    d = ol.deals("design")
    d["final_usd"] = d["final_price"]
    crsp = pd.read_parquet(ol.CACHE / "crsp_daily.parquet")
    crsp["d"] = crsp["dlycaldt"].dt.strftime("%Y-%m-%d")
    crsp["close"] = crsp["dlyprc"].abs().where(crsp["dlyprc"].abs() > 0)
    crsp = crsp.rename(columns={"dlyvol": "vol", "dlycumfacpr": "cumfac"})
    by = {p: g.sort_values("d").reset_index(drop=True) for p, g in crsp.groupby("permno")}
    deals = [(r._asdict(), by[int(r.permno)]) for r in d.itertuples() if int(r.permno) in by]
    assert len(deals) > 100
    bad = ol_mismatches(deals)
    assert not bad, f"{len(bad)} entries changed; first: {bad[:3]}"


# ------------------------------------------------------------------ SPAC: rebuild the panel from truncated caches

SPAC_DECISION = ["amount", "E_t", "bid", "ask", "adv20", "amount_stated", "nro_kind", "nro_id", "deadline", "pay",
                 "charter", "blocked"]
SPAC_CUTOFFS = ("2015-06-30", "2017-12-29")


def spac_truncated_cache(tmp: Path, cutoff: str) -> None:
    """Every SPAC cache as of `cutoff`: a decision on that day sees filings dated before it."""
    tmp.mkdir()
    for f in se.CACHE.iterdir():
        if f.is_file():
            (tmp / f.name).symlink_to(f)
    for name, col in (("filings.csv", "filed"), ("trust_facts.csv", "filed"), ("proxy_events_design.csv", "filed"),
                      ("links.csv", "prospectus_filed")):
        d = pd.read_csv(se.CACHE / name, dtype=str)
        (tmp / name).unlink()
        d[d[col] < cutoff].to_csv(tmp / name, index=False)
    b = pd.read_csv(se.CACHE / "dtb3.csv", dtype=str)
    (tmp / "dtb3.csv").unlink()
    b[b["observation_date"] <= cutoff].to_csv(tmp / "dtb3.csv", index=False)
    px = pd.read_parquet(se.CACHE / "crsp_daily.parquet")
    (tmp / "crsp_daily.parquet").unlink()
    px[px["dlycaldt"] <= cutoff].to_parquet(tmp / "crsp_daily.parquet", index=False)


def spac_panels(tmp: Path, cutoff: str, monkeypatch) -> tuple:
    """(panel from all the data, panel from the truncated data), decisions on days up to `cutoff`."""
    full = tr.panel("2010-01-01", cutoff, "design")[0]
    spac_truncated_cache(tmp, cutoff)
    with monkeypatch.context() as m:
        for mod in (se, sp, st, tr):
            m.setattr(mod, "CACHE", tmp)
        blind = tr.panel("2010-01-01", cutoff, "design")[0]
    return full, blind


def spac_differences(full: pd.DataFrame, blind: pd.DataFrame) -> dict:
    """Per column, how many SPAC-days differ. Days in only one panel count if the
    rule could have entered on them: `only_blind` is an eligible day the full
    panel dropped using later data, `only_full` one that later data created."""
    fee, haircut = tr.FEE, tr.HAIRCUT
    a, b = tr.yields(full, fee, haircut), tr.yields(blind, fee, haircut)
    eligible = lambda g: (g["yld"] >= tr.THRESH) & (g["days_to_pay"] <= tr.MAX_DAYS) & (g["shares"] > 0) \
        & ~g["suspect"] & (g["ask"] > 0) & ~g["blocked"].astype(bool)
    m = a.assign(el=eligible(a)).merge(b.assign(el=eligible(b)), on=["permno", "d"], how="outer",
                                       suffixes=("", "_b"), indicator=True)
    out = {"only_full": int(((m["_merge"] == "left_only") & (m["el"] == True)).sum()),
           "only_blind": int(((m["_merge"] == "right_only") & (m["el_b"] == True)).sum())}
    both = m[m["_merge"] == "both"]
    for c in SPAC_DECISION:
        x, y = both[c], both[f"{c}_b"]
        if x.dtype.kind in "fi" and y.dtype.kind in "fi":
            same = np.isclose(x.astype(float), y.astype(float), atol=1e-9, rtol=0, equal_nan=True)
        else:
            same = x.astype(str) == y.astype(str)
        out[c] = int((~same).sum())
    out["amount_on_stated_days"] = int((both["amount_stated"] & ~np.isclose(both["amount"], both["amount_b"], atol=1e-9, rtol=0)).sum())
    return out


@needs_spac
@pytest.mark.parametrize("cutoff", SPAC_CUTOFFS)
def test_spac_panel_rebuilt_from_truncated_data(tmp_path, cutoff, monkeypatch):
    full, blind = spac_panels(tmp_path / "cache", cutoff, monkeypatch)
    assert len(full) > 1000
    diff = spac_differences(full, blind)
    assert not any(diff.values()), diff


def test_spac_known_at_ignores_a_filing_until_the_next_day():
    tl = {"meetings": [{"id": 0, "filed": "2017-01-10", "meeting": "2017-02-06", "deadline": "2017-02-01", "right": True,
                        "stated": 10.23, "kind": "extension"}],
          "revisions": [], "charter": [("2015-02-06", "2017-02-11", "prospectus")], "votes": [], "terminated": []}
    assert tr.known_at(tl, "2017-01-10")["kind"] == "charter"
    assert tr.known_at(tl, "2017-01-11")["kind"] == "meeting"


# ------------------------------------------------------------------ PEAD: rebuild the event file from truncated data

PEAD_DECISION = ["day0_date", "entry", "exit", "sue", "decile", "close", "cap"]
PEAD_CUTOFF = "2008-08-15"       # mid-quarter, inside the design period


def pead_events_as_of(tmp: Path, cutoff: str, monkeypatch) -> pd.DataFrame:
    tmp.mkdir()
    for f in pead.CACHE.glob("dsf_*.parquet"):
        if f.name[4:8] <= cutoff[:4]:
            (tmp / f.name).symlink_to(f)
    for name in ("link.parquet", "ff_daily.parquet"):               # the calendar is known in advance
        (tmp / name).symlink_to(pead.CACHE / name)
    i = pd.read_parquet(pead.CACHE / "ibes_events.parquet")
    i[i["anndats"].astype(str) <= cutoff].to_parquet(tmp / "ibes_events.parquet", index=False)
    b = pd.read_parquet(pead.CACHE / "nyse_p20.parquet")
    b[b["month"] <= cutoff[:7]].to_parquet(tmp / "nyse_p20.parquet", index=False)
    with monkeypatch.context() as m:
        m.setattr(pead, "CACHE", tmp)
        m.setattr(pead.da, "guard", lambda *a, **k: cutoff)
        pead.build()
    return pd.read_parquet(tmp / "events.parquet")


def pead_differences(full: pd.DataFrame, blind: pd.DataFrame, cutoff: str) -> dict:
    keys = ["ticker", "pends"]
    a = full[full["day0_date"] <= cutoff]
    b = blind[blind["day0_date"] <= cutoff]
    m = a.merge(b, on=keys, how="outer", suffixes=("", "_b"), indicator=True)
    out = {"only_full": int((m["_merge"] == "left_only").sum()), "only_blind": int((m["_merge"] == "right_only").sum())}
    both = m[m["_merge"] == "both"]
    for c in PEAD_DECISION:
        if both[c].dtype.kind in "fi":
            same = np.isclose(both[c].astype(float), both[f"{c}_b"].astype(float), atol=1e-12, rtol=0, equal_nan=True)
        else:
            same = both[c].astype(str) == both[f"{c}_b"].astype(str)
        out[c] = int((~same).sum())
    return out


@needs_pead
def test_pead_events_rebuilt_from_truncated_data(tmp_path, monkeypatch):
    full = pd.read_parquet(pead.CACHE / "events.parquet")
    blind = pead_events_as_of(tmp_path / "cache", PEAD_CUTOFF, monkeypatch)
    assert (blind["day0_date"] <= PEAD_CUTOFF).sum() > 50_000
    diff = pead_differences(full, blind, PEAD_CUTOFF)
    assert not any(diff.values()), diff


def test_the_event_comparison_sees_a_changed_row_and_a_new_row():
    a = pd.DataFrame({"ticker": ["A", "B"], "pends": ["2008-03-31"] * 2, "day0_date": ["2008-04-20"] * 2, "entry": [1, 2],
                      "exit": [3, 4], "sue": [0.1, 0.2], "decile": [1.0, 2.0], "close": [5.0, 6.0], "cap": [7.0, 8.0]})
    assert not any(pead_differences(a, a, "2008-08-15").values())
    b = a.copy()
    b.loc[1, "decile"] = 3.0
    assert pead_differences(a, b, "2008-08-15")["decile"] == 1
    assert pead_differences(a, a.iloc[:1], "2008-08-15")["only_full"] == 1


def pead_decile_events() -> pd.DataFrame:
    """Two full quarters of synthetic surprises, the second shifted upward."""
    rng = np.random.default_rng(0)
    q1 = pd.DataFrame({"day0_date": "2020-02-15", "sue": rng.normal(0, 1, 500)})
    q2 = pd.DataFrame({"day0_date": "2020-05-15", "sue": rng.normal(2, 1, 500)})
    q2.loc[250:, "day0_date"] = "2020-06-15"
    return pd.concat([q1, q2], ignore_index=True)


def test_pead_deciles_ignore_later_events_in_the_quarter():
    ev = pead_decile_events()
    early = ev[ev["day0_date"] <= "2020-05-15"]
    assert pead.assign_deciles(ev).loc[early.index].equals(pead.assign_deciles(early))


def test_the_decile_comparison_catches_cutting_on_the_current_quarter():
    def leaky(ev):
        q = pead.quarter(ev["day0_date"])
        cuts = {k: np.quantile(g["sue"], np.linspace(0.1, 0.9, 9)) for k, g in ev.groupby(q)}
        out = pd.Series(np.nan, index=ev.index)
        for k, idx in ev.groupby(q).groups.items():
            out.loc[idx] = np.searchsorted(cuts[k], ev.loc[idx, "sue"].to_numpy(), side="right") + 1
        return out
    ev = pead_decile_events()
    early = ev[ev["day0_date"] <= "2020-05-15"]
    assert not leaky(ev).loc[early.index].equals(leaky(early))


def test_pead_leg_returns_up_to_a_day_ignore_later_prices_and_positions():
    cal = np.array([str(d.date()) for d in pd.bdate_range("2020-01-02", periods=12)])
    rows = [{"permno": p, "d": d, "open": 10.0 + p, "close": 10.5 + p, "ret": 0.01 * p, "half_spread": 0.001 * p}
            for p in (1, 2) for d in cal]
    px = pd.DataFrame(rows)
    pos = pd.DataFrame({"permno": [1, 2, 2], "entry": [1, 2, 8], "exit": [9, 10, 11]})
    cut = 5
    real = pead.leg_returns(pos, px, cal).set_index("i").loc[:cut]
    wrong = px.copy()
    wrong.loc[wrong["d"] > cal[cut], ["open", "close", "ret", "half_spread"]] *= SCRAMBLE
    blind = pead.leg_returns(pos[pos["entry"] <= cut], wrong, cal).set_index("i").loc[:cut]
    pd.testing.assert_frame_equal(real, blind)
    # A day's average must count only the positions open that day, not all of the hold's.
    wider = pead.leg_returns(pos, px, cal).set_index("i")
    assert wider.loc[1, "n"] == 1 and wider.loc[3, "n"] == 2
