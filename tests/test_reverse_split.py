"""Reverse-split round-up parsers and timing (python/reverse_split.py).

Fixtures in tests/fixtures/rsplit/ are verbatim excerpts of public SEC
filings (accession numbers in the test names' docstrings). The traps:
  - "at the participant level" is not a round-up for every holder;
  - rounded down and then paid cash in lieu is CASH, not DOWN;
  - a range of ratios in a proxy is not a final ratio;
  - a filing is usable only from the trading day after its acceptance date.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "python"))

import reverse_split as rs

FIX = ROOT / "tests" / "fixtures" / "rsplit"
read = lambda name: (FIX / name).read_text()


def test_round_up_for_each_holder():
    """8-K 0001104659-15-076718."""
    c, s = rs.fractional_class(read("holder_8k_2015.txt"))
    assert c == "ROUNDUP_HOLDER" and "rounded up" in s


def test_round_up_at_the_participant_level():
    """DEF 14A 0001493152-25-009597: brokerage holders are rounded up at DTC
    participant level, so a customer may get cash."""
    c, s = rs.fractional_class(read("participant_def14a_2025.txt"))
    assert c == "ROUNDUP_PARTICIPANT" and "participant level" in s


def test_cash_in_lieu():
    """8-K 0001001907-17-000064."""
    assert rs.fractional_class(read("cash_8k_2017.txt"))[0] == "CASH"


def test_rounded_down_then_paid_cash_is_cash():
    """8-K 0001140361-23-050802."""
    assert rs.fractional_class(read("down_then_cash_8k_2023.txt"))[0] == "CASH"


def test_rounded_down_without_cash_is_down():
    assert rs.sentence_class("Any fractional shares resulting from the Reverse Stock Split will be rounded down "
                             "to the nearest whole share.") == "DOWN"


def test_no_fractional_sentence_is_unknown():
    assert rs.fractional_class(read("announcement_8k_2022.txt"))[0] == "UNKNOWN"


def test_announcement_ratio_and_effective_date():
    """8-K 0001213900-22-061546 (Air Industries): 1-for-10, split-adjusted
    trading from 2022-10-18."""
    t = read("announcement_8k_2022.txt")
    assert rs.ratio(t) == (10, False)
    assert rs.effective_date(t) == "2022-10-18"


def test_a_range_is_not_a_final_ratio():
    """DEF 14A 0001437749-12-010301."""
    assert rs.ratio(read("range_def14a_2012.txt"))[1] is True           # flagged: the timeline ignores it


def _filing(accepted, **kw):
    return {"usable": rs.bday(accepted, 1) if rs.bday(accepted, 0) == accepted else rs.bday(accepted, 0),
            "accepted": accepted, "acc": "x", "form": "8-K", "cls": "UNKNOWN", "ratio": None, "effective": None,
            "cancel": False, **kw}


def test_a_filing_is_usable_from_the_trading_day_after_acceptance():
    tl = [_filing("2022-10-04", ratio=10, effective="2022-10-18", cls="ROUNDUP_HOLDER")]   # a Tuesday
    assert rs.known_at(tl, "2022-10-04")["announced"] is None
    k = rs.known_at(tl, "2022-10-05")
    assert k["announced"] == "2022-10-05" and k["cls"] == "ROUNDUP_HOLDER"
    fri = [_filing("2022-10-07", ratio=10, effective="2022-10-18")]                        # accepted Friday
    assert rs.known_at(fri, "2022-10-07")["announced"] is None and rs.known_at(fri, "2022-10-10")["announced"]


def test_entry_is_the_last_day_that_settles_before_the_split():
    """T+1 from 2024-05-28: a trade on day d settles d+1, which must be before
    the effective date."""
    tl = [_filing("2024-06-03", ratio=10, effective="2024-06-12", cls="ROUNDUP_HOLDER")]
    days = [rs.bday("2024-06-03", i) for i in range(8)]
    d, k = rs.entry_day(tl, days)
    assert d == "2024-06-10" and rs.bday(d, rs.settlement_days(d)) < "2024-06-12"


# ---- payoff of one event (synthetic quotes; no real prices)

import pandas as pd


def _quotes(days, pre=(1.00, 1.02), post=(9.80, 10.00), eff=None):
    rows = []
    for d in days:
        b, a = post if eff and d >= eff else pre
        rows.append({"dlycaldt": d, "dlybid": b, "dlyask": a, "dlyopen": (a + b) / 2, "dlyprc": (a + b) / 2})
    return rs.quotes(pd.DataFrame(rows))


def test_round_up_payoff_and_the_cash_branch():
    """1-for-10: buy 1 share at $1.02; rounded up it is 1 share at a $9.80 bid;
    not rounded up it is cash for 1/10 of that bid."""
    days = [rs.bday("2024-06-03", i) for i in range(12)]
    eff = "2024-06-12"
    tl = [_filing("2024-06-03", ratio=10, effective=eff, cls="ROUNDUP_HOLDER")]
    r = rs.payoff({"exdt": eff, "n_crsp": 10.0}, tl, _quotes(days, eff=eff))
    assert r["entry_day"] == "2024-06-10" and r["ask"] == 1.02 and r["rounded"] == 9.80
    assert abs(rs.value(r, 1.0) - 8.78) < 1e-9
    assert abs(rs.value(r, 0.0) - (0.98 - 1.02)) < 1e-9
    assert abs(rs.break_even_p(r) - 0.04 / 8.82) < 1e-9


def test_cash_class_is_not_traded():
    days = [rs.bday("2024-06-03", i) for i in range(12)]
    tl = [_filing("2024-06-03", ratio=10, effective="2024-06-12", cls="CASH")]
    assert rs.payoff({"exdt": "2024-06-12", "n_crsp": 10.0}, tl, _quotes(days, eff="2024-06-12")) is None


def test_a_postponement_after_entry_sells_at_the_bid():
    days = [rs.bday("2024-06-03", i) for i in range(15)]
    tl = [_filing("2024-06-03", ratio=10, effective="2024-06-12", cls="ROUNDUP_HOLDER"),
          _filing("2024-06-11", ratio=10, effective="2024-06-20", cls="ROUNDUP_HOLDER")]   # usable 06-12
    r = rs.payoff({"exdt": "2024-06-20", "n_crsp": 10.0}, tl, _quotes(days, eff="2024-06-20"))
    assert r["exit"] == "changed" and r["exit_day"] == "2024-06-12" and rs.value(r, 0.8) == 1.00 - 1.02


def test_settling_on_the_split_day_gets_no_round_up():
    """The filing said 06-14, so the entry is 06-12 (T+1 settles 06-13). The
    split came on 06-13, the settlement day: no round-up, cash for 1/N."""
    days = [rs.bday("2024-06-03", i) for i in range(12)]
    tl = [_filing("2024-06-03", ratio=10, effective="2024-06-14", cls="ROUNDUP_HOLDER")]
    r = rs.payoff({"exdt": "2024-06-13", "n_crsp": 10.0}, tl, _quotes(days, eff="2024-06-13"))
    assert r["entry_day"] == "2024-06-12"
    assert r["settle_miss"] and abs(rs.value(r, 1.0) - (0.98 - 1.02)) < 1e-9
