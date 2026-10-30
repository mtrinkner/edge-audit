"""Trial 60's redemption-event parsers (spac_proxy.py) and timing rules (spac_trial.py).

Fixtures in tests/fixtures/spac/ are short excerpts of real SEC filings
(public): Barington/Hilco's extension proxy and vote 8-K (2017), CM Seven
Star's liquidation 8-K (2018), a meeting postponement 8-K (2016), Axar's
ordinary annual meeting proxy (2016), and the tender offers of Aquasition
(2014) and TKK Symphony (2020).

The traps:
  - an annual meeting with no redemption right is not an exit;
  - a warrant's $11.50 exercise price and an extension's post-contribution
    estimate are not the redemption amount;
  - a proxy, a postponement and an extension are usable only from the day
    AFTER their filing date;
  - a position is never held past a meeting's redemption deadline.
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "python"))

import spac_proxy as sp
import spac_trial as tr

FIX = ROOT / "tests" / "fixtures" / "spac"
read = lambda name: (FIX / name).read_text()


def test_extension_proxy():
    p = sp.parse_proxy(read("bhac_def14a_extension.txt"), "DEF 14A")
    assert p["meeting"] == "2017-02-06" and p["kind"] == "extension" and p["redemption_right"]
    assert p["redemption_deadline"] == "2017-02-01"
    assert p["stated_amount"] == 10.23            # not 10.38 (after a contribution) nor 11.50 (warrant)


def test_annual_meeting_has_no_redemption_right():
    p = sp.parse_proxy(read("axar_def14a_annual.txt"), "DEF 14A")
    assert p["meeting"] == "2016-12-14" and p["kind"] == "annual" and not p["redemption_right"]


def test_extension_result_8k():
    u = sp.parse_8k(read("bhac_8k_extension_result.txt"), "2017-02-15")
    assert u["extended_to"] == "2017-08-11"
    assert u["per_share"] == 10.23                # what redeeming holders were paid


def test_liquidation_8k():
    u = sp.parse_8k(read("cm_seven_8k_liquidation.txt"), "2018-05-31")
    assert u["liquidation"] and u["per_share"] == 10.48
    assert u["liquidation_date"] == "2018-06-06"  # the remaining shares' redemption, not May 31


def test_postponement_8k():
    u = sp.parse_8k(read("postponement_8k.txt"), "2016-07-11")
    assert u["postponed_to"] == "2016-07-19"


def _timeline():
    return {"ipo": "2015-02-11", "charter": [("2015-02-11", "2017-02-11", "prospectus"),
                                              ("2017-02-15", "2017-08-11", "acc-ext")],
            "meetings": [{"id": 0, "filed": "2017-01-26", "meeting": "2017-02-06", "meeting_last": "2017-02-06",
                          "deadline": "2017-02-01", "right": True, "kind": "extension", "stated": 10.23}],
            "revisions": [{"id": 0, "filed": "2017-01-31", "meeting": "2017-02-10", "stated": None, "deadline": None}]}


def test_proxy_is_usable_only_after_its_filing_date():
    tl = _timeline()
    assert tr.known_at(tl, "2017-01-26")["kind"] == "charter"          # filed that day: not yet known
    k = tr.known_at(tl, "2017-01-27")
    assert k["kind"] == "meeting" and k["deadline"] == "2017-02-01" and k["stated"] == 10.23


def test_postponement_applies_from_its_filing_date():
    tl = _timeline()
    assert tr.known_at(tl, "2017-01-31")["deadline"] == "2017-02-01"   # the 8-K is dated the 31st
    k = tr.known_at(tl, "2017-02-01")
    assert k["deadline"] == tr.bday("2017-02-10", -tr.DEADLINE_LAG)     # moved with the meeting


def test_extension_is_usable_only_after_its_8k():
    tl = _timeline()
    tl["meetings"], tl["revisions"] = [], []
    assert tr.known_at(tl, "2017-02-10")["deadline"] == "2017-02-11"
    assert tr.known_at(tl, "2017-02-15")["deadline"] == "2017-02-11"    # 8-K filed the 15th
    assert tr.known_at(tl, "2017-02-16")["deadline"] == "2017-08-11"


def test_position_is_redeemed_at_the_deadline_never_carried_past_it():
    """A meeting with a redemption right and the bid below the stated amount:
    the position is redeemed at the paid amount, so an extension that passes
    cannot leave it open (simulate() also asserts this every day)."""
    days = list(tr.bday("2017-01-03", i) for i in range(30))
    rows = []
    for d in days:
        meeting = d <= "2017-02-01"
        rows.append({"permno": 1, "d": d, "close": 10.0, "bid": 9.98, "ask": 10.0, "hs": 0.01, "adv20": 1e6,
                     "vol": 1000, "E_t": 10.23, "amount": 10.23 if meeting else 10.30, "amount_stated": meeting,
                     "nro_kind": "meeting" if meeting else "charter", "nro_id": 0.0 if meeting else np.nan,
                     "deadline": "2017-02-01" if meeting else "2017-08-11",
                     "pay": tr.bday("2017-02-06", 5) if meeting else tr.bday("2017-08-11", 10), "charter": "2017-08-11",
                     "blocked": False})
    d = pd.DataFrame(rows)
    tls = {1: {**_timeline(), "end": "2017-12-31", "merged": False, "last_crsp": "2017-12-31",
               "paid": {0: 10.23}, "pay_date": {0: tr.bday("2017-02-06", 5)}, "liquidation": None}}
    s = tr.simulate(d, tls, days[0], days[-1], thresh=0.0)
    t = s["trades"]
    assert len(t) >= 1 and t.iloc[0]["exit"] == "redeemed" and t.iloc[0]["paid"] == 10.23
    assert t.iloc[0]["entry_day"] < "2017-02-01"


def test_liquidation_amount_stated_ahead_of_payment():
    u = sp.parse_8k(read("liquidation_amount_8k.txt"), "2019-12-04")
    assert u["liquidation"] and u["liquidation_amount"] == 10.37


def test_stated_amount_is_the_proxy_estimate_not_the_ipo_deposit():
    p = sp.parse_proxy(read("per_share_price_proxy.txt"), "DEF 14A")
    assert p["stated_amount"] == 10.44


def test_stub_quote_is_no_quote():
    bid, ask = pd.Series([0.01, 10.05, 9.98]), pd.Series([10.30, 10.65, 10.00])
    assert tr.valid_quote(bid, ask).tolist() == [False, True, True]   # 10.05/10.65 = 94%: a wide but real quote


# ---- audit rules (docs/SPAC_TRUST_ARB.md, "Symmetric data audit and run 4")

def test_tender_offer_is_a_redemption_opportunity():
    p = sp.parse_tender(read("aquasition_sc_to_i.txt"))
    assert p["kind"] == "tender" and p["redemption_right"]
    assert p["meeting"] == p["redemption_deadline"] == "2014-07-01" and p["stated_amount"] == 10.30


def test_tender_extension_gives_the_new_expiry_not_the_release_date():
    p = sp.parse_tender(read("tkk_sc_to_i_extension.txt"))
    assert p["meeting"] == "2020-01-08" and p["stated_amount"] == 10.28


def test_tender_final_result_gives_the_price_paid():
    p = sp.parse_tender(read("tkk_sc_to_i_final.txt"))
    assert p["stated_amount"] == 10.31 and p["meeting"] == "2020-02-13"


def test_coregistrant_proxy_is_matched_on_the_spac_name():
    assert sp.name_stem("Modern Media Acquisition Corp.") == "Modern Media Acquisition"
    assert sp.name_stem("Diamond Eagle Acquisition Corp \\ DE") == "Diamond Eagle Acquisition"
    assert sp.names_spac("NOTICE OF SPECIAL MEETING OF STOCKHOLDERS OF PACE HOLDINGS CORP. TO BE HELD", "Pace Holdings Corp.")
    assert not sp.names_spac("our office space in Fort Worth; a special meeting", "Pace Holdings Corp.")


def _merger_timeline(**kw):
    tl = {"ipo": "2017-03-30", "charter": [("2017-03-30", "2019-03-30", "prospectus")],
          "meetings": [{"id": 0, "filed": "2018-10-22", "meeting": "2018-11-06", "meeting_last": "2018-11-06",
                        "deadline": "2018-11-02", "right": True, "kind": "business_combination", "stated": 10.09}],
          "revisions": [], "votes": [], "terminated": []}
    tl.update(kw)
    return tl


def test_no_entry_after_a_passed_deadline_until_the_outcome_is_known():
    """Permno 16673 was bought on 2018-11-06, after its merger proxy's
    2018-11-02 deadline, and settled at trust (run 3): no right was bought."""
    tl = _merger_timeline()
    assert not tr.known_at(tl, "2018-11-02")["blocked"]                 # still pending: a meeting NRO
    k = tr.known_at(tl, "2018-11-05")
    assert k["kind"] == "charter" and k["blocked"]
    tl = _merger_timeline(terminated=["2018-12-03"])                    # a termination 8-K (item 1.02)
    assert tr.known_at(tl, "2018-12-03")["blocked"] and not tr.known_at(tl, "2018-12-04")["blocked"]
    ext = _merger_timeline(votes=["2018-11-07"])
    ext["meetings"][0]["kind"] = "extension"
    assert tr.known_at(ext, "2018-11-07")["blocked"] and not tr.known_at(ext, "2018-11-08")["blocked"]


def _sim(rows_fn, tl_extra, n=30, start="2018-10-01", **kw):
    days = [tr.bday(start, i) for i in range(n)]
    d = pd.DataFrame([{"permno": 1, "d": t, "close": 10.0, "bid": 9.98, "ask": 10.0, "hs": 0.01, "adv20": 1e6,
                       "vol": 1000, "E_t": 10.20, "amount": 10.20, "amount_stated": False, "nro_kind": "charter",
                       "nro_id": np.nan, "deadline": "2019-03-30", "pay": tr.bday("2019-03-30", 10),
                       "charter": "2019-03-30", "blocked": False, **rows_fn(i, t)} for i, t in enumerate(days)])
    if "keep" in d:                    # days the SPAC has no row; another (untradable) SPAC keeps the calendar
        d.loc[~d["keep"], ["permno", "ask", "bid", "adv20"]] = [2, np.nan, np.nan, 0.0]
        d = d.drop(columns="keep")
    tl = {**_merger_timeline(), "end": "2019-12-31", "merged": False, "last_crsp": "2019-12-31", "held": {0: "2018-11-06"},
          "paid": {}, "pay_date": {0: tr.bday("2018-11-06", 5)}, "liquidation": None, **tl_extra}
    return tr.simulate(d, {1: tl, 2: tl}, days[0], days[-1], thresh=0.0, **kw), days


def test_fill_day_ask_is_checked_for_the_suspect_filter():
    s, days = _sim(lambda i, t: {"ask": 8.90 if i == 1 else 10.0, "bid": 8.85 if i == 1 else 9.98}, {}, n=3)
    assert len(s["trades"]) == 0 or s["trades"].iloc[0]["entry_day"] != days[1]
    assert s["counts"]["skipped_suspect_fill"] >= 1


def test_fill_needs_three_business_days_before_the_deadline():
    for gap, entered in ((2, False), (3, True)):
        dl = lambda t, g=gap: tr.bday("2018-10-02", g)
        s, days = _sim(lambda i, t, dl=dl: {"deadline": dl(t), "pay": tr.bday(dl(t), 10)}, {}, n=2)
        assert (len(s["trades"]) > 0) == entered


def test_missed_meeting_exits_at_the_bid_after_the_phase_end():
    """A merger the parser found no meeting for: the bid on the first day
    after the phase end, here the combined company's $7.00, not E_t."""
    end = tr.bday("2018-10-01", 10)
    bids = pd.Series({tr.bday("2018-10-01", i): 9.98 for i in range(10)} | {tr.bday(end, 1): 7.00})
    s, days = _sim(lambda i, t: {"keep": t < end}, {"merged": True, "end": end, "bids": bids, "meetings": []}, n=15)
    t = s["trades"].iloc[0]
    assert t["exit"] == "unparsed_meeting" and t["paid"] == 7.00 and t["pnl"] < 0


def test_redemption_is_void_when_the_merger_never_closes():
    """Easterly's merger vote was adjourned, then the deal terminated on
    2016-12-31: shares tendered for redemption came back (run 3 paid them)."""
    def rows(i, t):
        pend = t <= "2018-11-02"
        return {"nro_kind": "meeting" if pend else "charter", "nro_id": 0.0 if pend else np.nan,
                "deadline": "2018-11-02" if pend else "2019-03-30", "amount": 10.09 if pend else 10.20,
                "amount_stated": pend, "pay": tr.bday("2018-11-06", 5) if pend else tr.bday("2019-03-30", 10)}
    s, days = _sim(rows, {"merged": True, "end": "2019-06-30"}, n=40)
    t = s["trades"].iloc[0]
    assert t.get("exit") != "redeemed" and s["counts"]["void_redemptions"] == 1
