"""Trial 60's redemption-event parsers (spac_proxy.py) and timing rules (spac_trial.py).

Fixtures in tests/fixtures/spac/ are short excerpts of real SEC filings
(public): Barington/Hilco's extension proxy and vote 8-K (2017), CM Seven
Star's liquidation 8-K (2018), a meeting postponement 8-K (2016) and Axar's
ordinary annual meeting proxy (2016).

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
                     "pay": tr.bday("2017-02-06", 5) if meeting else tr.bday("2017-08-11", 10), "charter": "2017-08-11"})
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
