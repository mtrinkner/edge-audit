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


# ---- rule v2 (trial 60): more sources, and cancellation only by a later filing

def test_v2_reads_a_press_release_filed_as_an_unusual_exhibit():
    """8-K 0000872248-16-000152, item 7.01: the press release is exhibit
    "EX-9.01", which v1 never read. v2 also takes the date written after
    "split-adjusted basis" over the 5:00 pm legal effective time before it."""
    t = read("exhibit_9_01_press_release_2016.txt")
    p = rs.parse_filing_v2(t, "2016-05-18")
    assert p["ratio"] == 4 and not p["ratio_is_range"] and p["effective"] == "2016-05-19"
    assert rs.effective_date(t) == "2016-05-18"                    # v1's reading, kept for v1


def test_v2_reads_a_certificate_of_amendment_exhibit():
    """8-K 0001213900-18-010718, EX-3.1: ratio, effective time (4:30 pm, so the
    next trading day) and the round-up are all in the certificate."""
    p = rs.parse_filing_v2(read("certificate_exhibit_3_1_2018.txt"), "2018-08-10")
    assert p["ratio"] == 20 and p["effective"] == "2018-08-13" and p["frac_class"] == "ROUNDUP_HOLDER"


def test_a_range_or_up_to_ratio_does_not_count():
    """8-K 0001104659-18-005137 (not less than 1-for-20 and not greater than
    1-for-40) and 8-K 0001144204-17-065489 (up to 1-for-10)."""
    for f in ("range_only_8k_2018.txt", "up_to_ratio_8k_2017.txt"):
        assert rs.ratio(read(f)) == (None, True)


def test_v2_takes_a_fixed_ratio_from_a_definitive_proxy_or_prospectus_not_a_preliminary_one():
    rows = pd.DataFrame([{"status": "ok", "accepted": "2016-05-02", "acc": "a", "form": f, "frac_class": "ROUNDUP_HOLDER",
                          "ratio": 10, "ratio_is_range": False, "effective": "2016-06-01", "cancel": False}
                         for f in ("PRE 14A", "DEF 14C", "424B3", "S-1/A", "10-Q")])
    rows["acc"] = list("abcde")
    got = {f["form"]: f["ratio"] for f in rs.timeline(rows, "v2")}
    assert got == {"PRE 14A": None, "DEF 14C": 10, "424B3": 10, "S-1/A": 10, "10-Q": None}
    assert all(f["ratio"] is None for f in rs.timeline(rows, "v1"))         # v1: 8-Ks only


def test_a_split_already_done_announces_nothing():
    """A prospectus recounting an earlier split states a past date."""
    t = ("On September 7, 2016, we effected a 1-for-3 reverse stock split of our common stock, "
         "and the reverse stock split became effective on September 7, 2016.")
    assert rs.parse_filing_v2(t, "2017-12-04")["effective"] is None


def test_v2_cancellation_needs_a_decision_not_a_reservation():
    """Reservations from 8-K/proxy text (accessions 0000799698-17-000026,
    0000872248-16-000134, 0000950123-10-081332, 0000950123-11-027493); v1
    flagged them as cancellations."""
    for s in read("reservations_not_cancellations.txt").splitlines():
        assert not rs.cancelled_v2(s), s
    assert rs.cancelled(read("reservations_not_cancellations.txt"))          # the v1 false positive
    for s in ("On June 3, 2016, the Board of Directors determined not to proceed with the reverse stock split.",
              "The Company announced that the reverse stock split has been postponed indefinitely.",
              "The Company has abandoned the reverse stock split approved at the annual meeting."):
        assert rs.cancelled_v2(s), s


def test_v2_only_a_later_filing_can_cancel_the_announcement():
    ann = _filing("2016-05-02", ratio=10, effective="2016-05-20", cls="ROUNDUP_HOLDER", cancel=True, rule="v2")
    k = rs.known_at([ann], "2016-05-10")
    assert k["announced"] == "2016-05-03" and not k["cancelled"]
    later = _filing("2016-05-09", cancel=True, rule="v2", acc="y")
    assert rs.known_at([ann, later], "2016-05-10")["cancelled"]
    early = _filing("2016-04-01", cancel=True, rule="v2", acc="z")          # an older plan, called off
    assert not rs.known_at([early, ann], "2016-05-10")["cancelled"]
    days = [rs.bday("2016-05-03", i) for i in range(12)]
    assert rs.entry_day([ann], days)[0] is not None                        # v1 would never enter
    assert rs.entry_day([{**ann, "rule": "v1"}], days)[0] is None


def test_v2_announcement_from_a_definitive_information_statement():
    """DEF 14C 0001193125-10-274627 (accepted 2010-12-06): a fixed 1-for-100
    ratio and an effective date three weeks later, in one filing."""
    p = rs.parse_filing_v2(read("information_statement_def14c_2010.txt"), "2010-12-06", "DEF 14C")
    assert p["ratio"] == 100 and p["effective"] == "2010-12-28"


def test_v2_announcement_from_a_prospectus_and_a_prospectus_states_no_class():
    """424B3 0001437749-16-026589 (accepted 2016-03-02) carries the 8-K text:
    1-for-20, trading split-adjusted from March 7."""
    p = rs.parse_filing_v2(read("prospectus_424b3_2016.txt"), "2016-03-02", "424B3")
    assert p["ratio"] == 20 and p["effective"] == "2016-03-07" and p["frac_class"] == "UNKNOWN"


def test_v2_a_prospectus_listing_earlier_splits_announces_nothing():
    t = ("We effected a 1-for-25 reverse stock split on March 11, 2016 and a 1-for-4 reverse stock split on "
         "August 15, 2016. " + "x " * 600 + "The offering is expected to close on June 30, 2017, on which date "
         "the shares will become effective for trading.")
    assert rs.parse_filing_v2(t, "2017-04-03", "424B5")["ratio"] is None


def test_v2_class_ignores_fractional_wording_about_other_instruments():
    """8-K 0001144204-10-025989: an equity line agreement's "Fractional shares
    shall be rounded to the next higher whole number" is not the split's class."""
    t = ("The number of shares of Common Stock that the Investor shall purchase pursuant to each Advance shall be "
         "determined by dividing the amount of the Advance by the Purchase Price. No fractional shares shall be "
         "issued. Fractional shares shall be rounded to the next higher whole number of shares.")
    assert rs.fractional_class_v2(t)[0] == "UNKNOWN"
    assert rs.fractional_class(t)[0] == "ROUNDUP_HOLDER"                      # v1 read it as the split's


def test_v2_reads_n_to_one_ratios():
    assert rs.parse_filing_v2("The Company will effect a reverse stock split at a ratio of 16:1 which will take "
                              "effect at the start of trading on May 13, 2010.", "2010-05-12")["ratio"] == 16
    assert rs.parse_filing_v2("The reverse stock split, on a six-to-one basis, was approved.", "2015-04-23")["ratio"] == 6


# --- freeze guard: confirmation-period outputs carry a manifest --------------------------------

import json as _json
import pytest


def _frozen(monkeypatch, commit="a" * 40, md5="b" * 32):
    monkeypatch.setattr(rs, "frozen_commit", lambda: commit)
    monkeypatch.setattr(rs, "python_tree_md5", lambda: md5)


def test_a_confirmation_read_without_a_manifest_refuses(tmp_path):
    f = tmp_path / "parsed_v2_confirm.csv"
    pd.DataFrame({"x": [1]}).to_csv(f, index=False)
    with pytest.raises(SystemExit, match="confirmation inputs not produced by trial60-frozen"):
        rs.read_output(f, "confirm")


def test_a_manifest_from_another_commit_refuses(tmp_path, monkeypatch):
    f = tmp_path / "event_table_v2_confirm.csv"
    _frozen(monkeypatch, commit="c" * 40)
    rs.write_output(pd.DataFrame({"x": [1]}), f, "confirm")
    _frozen(monkeypatch, commit="a" * 40)                                   # the tag names another commit
    with pytest.raises(SystemExit, match="confirmation inputs not produced by trial60-frozen"):
        rs.read_output(f, "confirm")


def test_a_python_tree_mismatch_refuses(tmp_path, monkeypatch):
    f = tmp_path / "trades_v2_confirm.csv"
    _frozen(monkeypatch)
    rs.write_output(pd.DataFrame({"x": [1]}), f, "confirm")
    _frozen(monkeypatch, md5="d" * 32)                                      # python/ edited after the write
    with pytest.raises(SystemExit, match="confirmation inputs not produced by trial60-frozen"):
        rs.read_output(f, "confirm")


def test_a_matching_manifest_passes(tmp_path, monkeypatch):
    f = tmp_path / "parsed_v2_confirm.csv"
    _frozen(monkeypatch)
    rs.write_output(pd.DataFrame({"x": [1, 2]}), f, "confirm")
    m = _json.loads((tmp_path / "parsed_v2_confirm.csv.manifest.json").read_text())
    assert m["commit"] == "a" * 40 and m["python_md5"] == "b" * 32 and m["rule"] == "v2" and m["period"] == "confirm"
    assert list(rs.read_output(f, "confirm")["x"]) == [1, 2]


def test_design_outputs_need_no_manifest(tmp_path):
    f = tmp_path / "parsed_v2_design.csv"
    rs.write_output(pd.DataFrame({"x": [1]}), f, "design")
    assert not (tmp_path / "parsed_v2_design.csv.manifest.json").exists()
    assert len(rs.read_output(f, "design")) == 1
