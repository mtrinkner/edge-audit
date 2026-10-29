"""SPAC trust-value pieces (spac_edgar.py, spac_trust.py), on synthetic fixtures.

The traps these cover:
  - an XBRL trust balance is usable from the day AFTER it was filed, never
    from its balance-sheet date;
  - a filing's own balance (its latest "end"), not a prior-period comparative;
  - after a merger the SPAC's permno continues as the operating company, so the
    SPAC phase ends at the first ticker or name change, however similar the new
    name ("New Frontier Corp" -> "New Frontier Health Corp");
  - nothing is entered after the final redemption deadline;
  - a SPAC without a parsed trust value is skipped, never given $10.
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "python"))

import spac_edgar as se
import spac_trust as st


def _fact(end, val, filed, accn, form="10-Q"):
    return {"end": end, "val": val, "filed": filed, "accn": accn, "form": form}


def test_trust_facts_use_each_filing_own_balance():
    cf = {"facts": {"us-gaap": {
        "AssetsHeldInTrustNoncurrent": {"units": {"USD": [
            _fact("2021-12-31", 230_000_000, "2022-03-01", "a1", "10-K"),
            _fact("2021-12-31", 230_000_000, "2022-05-10", "a2"),       # comparative in the next 10-Q
            _fact("2022-03-31", 230_100_000, "2022-05-10", "a2")]}},
        "TemporaryEquitySharesOutstanding": {"units": {"shares": [
            _fact("2022-03-31", 23_000_000, "2022-05-10", "a2")]}},
        "TemporaryEquityRedemptionPricePerShare": {"units": {"USD/shares": [
            _fact("2022-03-31", 10.004, "2022-05-10", "a2")]}}}}}
    rows = {r["accn"]: r for r in se.trust_facts(cf)}
    assert rows["a2"]["end"] == "2022-03-31" and rows["a2"]["trust"] == 230_100_000
    assert rows["a2"]["TemporaryEquityRedemptionPricePerShare"] == 10.004
    assert rows["a1"]["trust"] == 230_000_000


def test_xbrl_value_is_usable_only_after_its_filing_date():
    L = pd.DataFrame({"cik": ["1"], "permno": [7], "trust_per_share_ipo": ["10.00"],
                      "prospectus_filed": ["2021-01-05"]})
    T = pd.DataFrame({"cik": ["1"], "filed": ["2022-05-10"], "end": ["2022-03-31"], "trust": [2.3e8],
                      "TemporaryEquitySharesOutstanding": [2.3e7],
                      "TemporaryEquityRedemptionPricePerShare": [10.05]})
    est = st.trust_estimates(L, T, st.tbill())[7]
    assert est[0] == ("2021-01-05", 10.0, "2021-01-05")
    assert est[1][0] == "2022-05-11" and est[1][2] == "2022-03-31"   # available the day after filing


def test_spac_without_a_parsed_trust_value_is_skipped():
    L = pd.DataFrame({"cik": ["1"], "permno": [7], "trust_per_share_ipo": [np.nan],
                      "prospectus_filed": ["2011-05-11"]})
    assert st.trust_estimates(L, pd.DataFrame(columns=["cik"]), st.tbill()) == {}


def test_phase_ends_at_any_rename_even_a_similar_name(tmp_path, monkeypatch):
    sec = pd.DataFrame({"permno": [1, 1, 1], "secinfostartdt": ["2018-07-27", "2018-09-28", "2019-12-19"],
                        "issuernm": ["NEW FRONTIER CORP", "NEW FRONTIER CORP", "NEW FRONTIER HEALTH CORP"],
                        "ticker": ["NFC", "NFC", "NFH"]})
    sec.to_parquet(tmp_path / "crsp_secinfo.parquet")
    monkeypatch.setattr(st, "CACHE", tmp_path)
    L = pd.DataFrame({"permno": [1], "name_at_filing": ["New Frontier Corp"]})
    assert st.renamed_on(L) == {1: "2019-12-19"}


def test_merger_into_new_holdco_ends_at_the_final_vote():
    cal = np.array(pd.bdate_range("2019-01-01", "2019-12-31").strftime("%Y-%m-%d"))
    e = {"closed": None, "votes": ["2019-06-03", "2019-11-04"]}
    # permno stops trading 5 days after the last vote: the merger closed under another CIK
    assert st.phase_end(e, None, "2019-11-11", cal) == ("2019-11-04", True)
    # trading goes on for months after the last vote: an extension, then liquidation
    assert st.phase_end(e, None, "2019-12-31", cal) == (None, False)


def test_prospectus_terms_patterns():
    text = ('Of the proceeds we receive, $230,000,000 ($10.00 per unit) will be deposited into a '
            'U.S.-based trust account. If we do not complete our initial business combination within 24 '
            'months from the closing of this offering, we will redeem 100% of the public shares. We have '
            'been approved to list our units on Nasdaq under the symbol "ABCDU". Each unit consists of one '
            'Class A ordinary share and one-half of one redeemable warrant.')
    text = text.replace("U.S.", "US")                                # as parse_terms normalizes it
    m = se._TRUST_PU.search(text) or se._TRUST_PU2.search(text)     # parse_terms' two-step match
    assert m and float(next(g for g in m.groups() if g)) == 10.00
    assert se._months(se._MONTHS.search(text).group(1)) == 24
    assert se._UNIT_SYM.search(text).group(1) in ("ABCD", "ABCDU")
