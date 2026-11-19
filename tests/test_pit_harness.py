"""The shared layer (python/pit.py, sim.py, report.py, harness.py) and the
trial 60 regression port (python/studies/rsplit_pit.py).

Fast tier: a small synthetic fixture. Slow tier (-m slow): full data, the
port reproducing trial 60's trades exactly, and the harness on the port."""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "python"))

import evaluation as ev
import harness as H
import pit as P
import report as R
import sim as S

DAYS = ["2020-01-%02d" % d for d in (2, 3, 6, 7, 8, 9, 10, 13, 14, 15, 16, 17, 21, 22, 23, 24, 27, 28, 29, 30, 31)] + \
       ["2020-02-%02d" % d for d in (3, 4, 5, 6, 7, 10, 11, 12, 13, 14, 18, 19, 20, 21, 24, 25, 26, 27, 28)]
CAL = P.Calendar(DAYS)


def prices(known_shift_days: int = 0) -> P.Dataset:
    """Three securities, a random walk with a 1% spread. known_shift_days < 0
    plants a leak: each close is declared known that many days early."""
    rng = np.random.default_rng(7)
    rows = []
    for k in (1, 2, 3):
        p = 10.0
        for d in DAYS:
            p *= np.exp(rng.normal(0, 0.03))
            rows.append({"permno": k, "dlycaldt": d, "dlyclose": p, "dlyprc": p, "dlyopen": p * 1.001,
                         "dlybid": p * 0.995, "dlyask": p * 1.005, "dlyvol": 1000})
    f = pd.DataFrame(rows)
    ka = f["dlycaldt"].map(lambda d: CAL.shift(d, known_shift_days))
    f["known_at"] = pd.to_datetime(ka + " " + P.CLOSE)
    return P.Dataset("crsp_daily", f, keys=("permno",), event_time="dlycaldt",
                     value_cols=("dlyclose", "dlyprc", "dlyopen", "dlybid", "dlyask"))


def filings() -> P.Dataset:
    f = pd.DataFrame({"permno": [1, 2, 3, 1], "accepted": ["2020-01-08", "2020-01-17", "2020-01-25", "2020-02-07"],
                      "signal": ["buy", "buy", "none", "buy"]})
    f["known_at"] = P.known_at_next_open(f["accepted"], CAL)
    return P.Dataset("filings", f, keys=("permno",), event_time="accepted", value_cols=("signal",))


def fixture(known_shift_days: int = 0) -> P.PIT:
    tb = pd.DataFrame({"date": DAYS, "rf": 0.0001})
    tb["known_at"] = P.known_at_tbill(tb["date"], CAL)
    return P.PIT([prices(known_shift_days), filings(), P.Dataset("tbill", tb, keys=("date",))], CAL)


class FilingStudy:
    """Buy one share the day a 'buy' filing becomes usable; sell five trading days later."""

    def __init__(self, pit):
        self.seen, self.open = set(), {}

    def decide(self, view, book):
        day, out = str(view.t.date()), []
        for r in view.asof("filings").itertuples():
            if r.Index in self.seen:
                continue
            self.seen.add(r.Index)
            if r.signal == "buy":
                lot = f"{r.permno}:{day}"
                out.append(S.Order(r.permno, "buy", qty=1, lot=lot, tag="in"))
                self.open[lot] = view.cal.shift(day, 5)
        for lot, when in list(self.open.items()):
            if day >= when and lot in book.lots:
                out.append(S.Order(book.lots[lot].key, "sell", lot=lot, good_until=view.cal.shift(day, 3), tag="out"))
                del self.open[lot]
        return out


class MomentumStudy:
    """Buy security 1 when its latest known close is above the one before it."""

    def __init__(self, pit):
        self.n = 0

    def decide(self, view, book):
        p = view.asof("crsp_daily", permno=1)
        if len(p) >= 2 and p["dlyclose"].iloc[-1] > p["dlyclose"].iloc[-2]:
            self.n += 1
            return [S.Order(1, "buy", qty=1, lot=f"m{self.n}", tag="in")]
        return []


class FileReadingStudy:
    def __init__(self, pit):
        pass

    def decide(self, view, book):
        pd.read_csv(ROOT / "data" / "gate_rescreen.csv")
        return []


# ------------------------------------------------------------------ known_at and asof

def test_known_at_rules():
    cal = P.Calendar.default()
    assert P.known_at_close(pd.Series(["2020-01-17"])).iloc[0] == pd.Timestamp("2020-01-17 16:00")
    # accepted Friday before MLK day: usable Tuesday's open
    assert P.known_at_next_open(pd.Series(["2020-01-17"]), cal).iloc[0] == pd.Timestamp("2020-01-21 09:30")
    assert P.known_at_next_open(pd.Series(["2020-01-18"]), cal).iloc[0] == pd.Timestamp("2020-01-21 09:30")
    k = P.known_at_ibes(pd.Series(["2020-01-17"] * 3), pd.Series(["10:15:00", "16:05:00", None]), cal)
    assert list(k) == [pd.Timestamp("2020-01-17 10:15"), pd.Timestamp("2020-01-21 09:30"),
                       pd.Timestamp("2020-01-21 09:30")]
    assert P.known_at_ff(pd.Series(["2020-01-15"]), cal).iloc[0] == pd.Timestamp("2020-02-28 16:00")
    assert P.known_at_tbill(pd.Series(["2020-01-15"]), cal).iloc[0] == pd.Timestamp("2020-01-02 09:30")


def test_dates_past_the_calendar_map_forward_not_back():
    cal = P.Calendar(["2020-01-02", "2020-01-03"])
    # a filing or factor after the last known day must not be dated to that day
    assert P.known_at_next_open(pd.Series(["2020-01-08"]), cal).iloc[0] == pd.Timestamp("2020-01-09 09:30")
    assert P.known_at_ff(pd.Series(["2020-01-15"]), cal).iloc[0] == pd.Timestamp("2020-02-28 16:00")
    assert cal.shift("2020-01-03", 2) == "2020-01-07"
    assert cal.range("2020-01-01", "2020-12-31") == ["2020-01-02", "2020-01-03"]

def test_asof_returns_only_known_rows_and_the_latest_revision():
    f = pd.DataFrame({"firm": [1, 1, 1, 2], "fact": ["eps", "eps", "eps", "eps"], "value": [1.0, 1.2, 1.1, 5.0],
                      "known_at": pd.to_datetime(["2020-01-02 09:30", "2020-02-03 09:30", "2020-03-02 09:30",
                                                  "2020-01-02 09:30"])})
    d = P.Dataset("xbrl", f, keys=("firm",), version_key=("firm", "fact"))
    p = P.PIT([d])
    assert p.asof("xbrl", "2020-01-31", firm=1)["value"].tolist() == [1.0]
    assert p.asof("xbrl", "2020-02-15", firm=1)["value"].tolist() == [1.2]          # revised
    assert sorted(p.asof("xbrl", "2020-06-30")["value"].tolist()) == [1.1, 5.0]          # one version per key
    assert len(p.asof("xbrl", "2019-12-31")) == 0
    with pytest.raises(ValueError):
        P.Dataset("bad", pd.DataFrame({"x": [1]}))                               # no known_at


# ------------------------------------------------------------------ the harness

def test_a_clean_study_passes_truncation_and_scramble():
    r = H.check(FilingStudy, fixture(), DAYS[0], H.default_cuts(CAL, DAYS[0], DAYS[-1]), seeds=(0, 1))
    assert r["ok"] and r["decisions"] > 0, r
    r = H.check(MomentumStudy, fixture(), DAYS[0], H.default_cuts(CAL, DAYS[0], DAYS[-1]), seeds=(0, 1))
    assert r["ok"], r


def test_a_planted_leak_is_caught():
    """Closes declared known a day early: at today's close the study sees
    tomorrow's. Truncation by known_at can't see it; the scramble can."""
    leaky = fixture(known_shift_days=-1)
    r = H.check(MomentumStudy, leaky, DAYS[0], H.default_cuts(CAL, DAYS[0], DAYS[-1]), seeds=(0, 1))
    assert not r["ok"] and not r["scramble_ok"] and r["truncation_ok"]


def test_a_study_that_reads_a_file_fails():
    with pytest.raises(PermissionError):
        H.decisions(FileReadingStudy, fixture(), DAYS[0], DAYS[3])


# ------------------------------------------------------------------ the simulator

def test_fills_stub_rule_settlement_and_split():
    rows = []
    for d, bid, ask in [("2020-01-02", 1.00, 1.02), ("2020-01-03", 1.00, 1.20), ("2020-01-06", 9.8, 10.0),
                        ("2020-01-07", 9.9, 10.1)]:
        rows.append({"permno": 9, "dlycaldt": d, "dlyclose": ask, "dlyprc": ask, "dlyopen": bid, "dlybid": bid,
                     "dlyask": ask, "dlyvol": 1})
    f = pd.DataFrame(rows)
    f["known_at"] = P.known_at_close(f["dlycaldt"])
    acts = pd.DataFrame({"permno": [9], "exdt": ["2020-01-06"], "factor": [10.0]})
    acts["known_at"] = pd.to_datetime(acts["exdt"] + " " + P.OPEN)
    cal = P.Calendar(["2020-01-02", "2020-01-03", "2020-01-06", "2020-01-07"])
    pit = P.PIT([P.Dataset("crsp_daily", f, keys=("permno",)), P.Dataset("acts", acts, keys=("permno",))], cal)

    class Buy:
        def __init__(self, day):
            self.day = day

        def decide(self, view, book):
            d = str(view.t.date())
            if d == self.day:
                return [S.Order(9, "buy", qty=1, lot="x")]
            if d == "2020-01-06" and "x" in book.lots:
                return [S.Order(9, "sell", lot="x", good_until="2020-01-07")]
            return []

    # T+2 in 2020: a buy on 01-02 settles 01-06 = the ex date, so it is NOT of record
    r = S.Sim(pit, actions="acts", fractional="roundup").run(Buy("2020-01-02"), "2020-01-02", "2020-01-07")
    lot = r["trades"].iloc[0]
    assert lot["entry_px"] == 1.02 and not lot["of_record"] and lot["qty"] == pytest.approx(0.1)
    assert lot["exit_px"] == 9.8
    # a stub quote (bid < 90% of ask) does not fill
    r = S.Sim(pit, actions="acts").run(Buy("2020-01-03"), "2020-01-02", "2020-01-07")
    assert r["trades"].empty and r["unfilled"]["reason"].tolist() == ["no_valid_quote"]


def test_round_up_of_record_and_cash_accrues_at_the_known_tbill_rate():
    days = ["2016-01-04", "2016-01-05", "2016-01-06", "2016-01-07", "2016-01-08", "2016-01-11"]
    cal = P.Calendar(days)
    f = pd.DataFrame([{"permno": 9, "dlycaldt": d, "dlyclose": 1.0, "dlyprc": 1.0, "dlyopen": 1.0, "dlybid": 0.99,
                       "dlyask": 1.0, "dlyvol": 1} for d in days])
    f["known_at"] = P.known_at_close(f["dlycaldt"])
    acts = pd.DataFrame({"permno": [9], "exdt": ["2016-01-11"], "factor": [4.0]})
    acts["known_at"] = pd.to_datetime(acts["exdt"] + " " + P.OPEN)
    tb = pd.DataFrame({"date": days, "rf": 0.001})
    tb["known_at"] = P.known_at_tbill(tb["date"], cal)
    pit = P.PIT([P.Dataset("crsp_daily", f, keys=("permno",)), P.Dataset("acts", acts, keys=("permno",)),
                 P.Dataset("tbill", tb, keys=("date",))], cal)

    class Buy:
        def decide(self, view, book):
            return [S.Order(9, "buy", qty=1, lot="x")] if str(view.t.date()) == "2016-01-04" else []

    r = S.Sim(pit, actions="acts", fractional="roundup", capital=100.0).run(Buy(), days[0], days[-1])
    lot = r["trades"].iloc[0]
    assert lot["of_record"] and lot["qty"] == 1 and lot["qty_before_rounding"] == 0.25     # T+3: settles 01-07
    nav = r["nav"].set_index("day")
    assert nav.loc["2016-01-04", "cash"] == pytest.approx(100 * 1.001 - 1.0)
    assert nav.loc["2016-01-05", "market_value"] == pytest.approx(0.99)                     # marked at the bid


def test_fees_and_caps():
    pit = fixture()

    class Two:
        def decide(self, view, book):
            if str(view.t.date()) == DAYS[1]:
                return [S.Order(1, "buy", qty=1, lot="a"), S.Order(2, "buy", qty=1, lot="b")]
            return []

    r = S.Sim(pit, broker=S.BROKERS["flat5"], max_positions=1).run(Two(), DAYS[0], DAYS[3])
    assert len(r["trades"]) == 1 and r["trades"]["fee_in"].iloc[0] == 5.0
    assert r["unfilled"]["reason"].tolist() == ["max_positions"]


# ------------------------------------------------------------------ the report

def test_report_years_covered_and_the_statistic_by_kind():
    pnl = pd.Series([1.0, 2.0, -0.5, 3.0, 1.5, 0.5, 2.5])
    dates = pd.Series(["2024-01-05", "2024-01-20", "2024-03-02", "2025-02-01", "2025-02-11", "2025-06-01",
                       "2019-05-01"])
    t = R.event_table(pnl, dates, ("2019-01-01", "2025-12-31"),
                      extra={"ex_2024_2025": (dates.str[:4].isin(["2024", "2025"]).values, 2)}).set_index("row")
    assert t.loc["all", "years_covered"] == 7 and t.loc["all", "usd_per_yr"] == pytest.approx(pnl.sum() / 7)
    assert t.loc["year_2024", "usd_per_yr"] == pytest.approx(2.5)
    assert t.loc["ex_2024_2025", "usd_per_yr"] == pytest.approx((pnl.sum() - 2.5) / 2)
    c = ev.clustered_mean(pnl, dates.str[:7])
    assert R.primary_t("event", pnl, dates)["t"] == pytest.approx(c["t"])
    m = pd.Series(np.random.default_rng(1).normal(0.01, 0.02, 60))
    assert R.primary_t("portfolio", monthly=m)["t"] == pytest.approx(ev.newey_west_t(m)["t"])


def test_nav_report_with_factors():
    days = pd.bdate_range("2020-01-01", periods=300).strftime("%Y-%m-%d")
    rng = np.random.default_rng(3)
    mkt = pd.Series(rng.normal(0.0004, 0.01, len(days)), index=days)
    r = 0.0001 + 0.5 * mkt + rng.normal(0, 0.002, len(days))
    nav = pd.DataFrame({"day": days, "nav": 1000 * np.cumprod(1 + r.values), "market_value": 500.0})
    out = R.nav_report(nav, rf_daily=pd.Series(0.0, index=days), factors=pd.DataFrame({"mkt": mkt}))
    assert out["beta"] == pytest.approx(0.5, abs=0.05) and out["capacity_used"] < 1 and out["max_drawdown"] <= 0


# ------------------------------------------------------------------ slow: trial 60 on the new layer

@pytest.mark.slow
@pytest.mark.parametrize("period,events,mean", [("design", 36, 3.6594), ("confirm", 121, 3.1657)])
def test_the_port_reproduces_trial_60_exactly(period, events, mean):
    from studies import rsplit_pit as RP
    port = RP.trades(period)
    frozen = pd.read_csv(RP.CACHE / f"trades_v2_{period}.csv")
    assert RP.compare(port, frozen) == []
    s = RP.summary(port, period).set_index("row").loc["all"]
    assert int(s["events"]) == events and s["mean"] == pytest.approx(mean, abs=5e-5)


@pytest.mark.slow
def test_the_harness_on_the_port():
    """Truncation and scramble on every dataset but the declared universe."""
    from studies import rsplit_pit as RP
    pit = RP.load("design")
    make = lambda p: RP.RoundUp(p.cal)
    kw = {"prices": "crsp_daily", "actions": "crsp_splits"}
    r = H.check(make, pit, "2010-01-04", ["2011-09-30", "2013-06-28"], sim_kw=kw)
    assert r["ok"], r
    assert list(r["lookahead_datasets"]) == ["rsplit_universe"]


@pytest.mark.slow
def test_the_frozen_parse_functions_reproduce_the_frozen_parse():
    """200 cached confirmation filings re-parsed with the imported functions
    match the tag's parse output row for row."""
    import reverse_split as rs
    from studies import rsplit_pit as RP
    f = RP.frozen_input(RP.CACHE / "filings_v2_confirm.csv", "confirm")
    p = RP.frozen_input(RP.CACHE / "parsed_v2_confirm.csv", "confirm")
    s = f.sample(200, random_state=60)
    for r in s.to_dict("records"):
        new = rs._parse_one_v2(r)
        old = p[(p["acc"] == r["acc"]) & (p["permno"] == r["permno"]) & (p["exdt"] == r["exdt"])].iloc[0]
        for c in ("status", "ratio", "effective", "frac_class", "cancel"):
            a, b = new.get(c), old[c]
            assert (pd.isna(a) and pd.isna(b)) or str(a) == str(b) or \
                (not pd.isna(a) and not pd.isna(b) and float(a) == float(b)), (r["acc"], c, a, b)
