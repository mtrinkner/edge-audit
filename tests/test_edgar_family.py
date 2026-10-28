"""edgar_tenders.py with the form family as a parameter (added 2026-10-28).

Trial 58 read SC TO-I filings; trial 60 would read SC TO-T. The index layer is
shared, so it must keep rebuilding trial 58's filing list exactly, and it must
keep only the forms asked for. An SC TO-T is listed once per party (bidder and
subject company) with the same accession number, which the TO-T table records.
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "python"))

import edgar_tenders as et

SAMPLE = """\
Form Type   Company Name                                                  CIK         Date Filed  File Name
---------------------------------------------------------------------------------------------------------
SC TO-I          ACME CORP                                                     111111      2005-04-01  edgar/data/111111/0000111111-05-000001.txt
SC TO-I/A        ACME CORP                                                     111111      2005-04-20  edgar/data/111111/0000111111-05-000002.txt
SC TO-T          ACXIOM CORP                                                   733269      2005-04-01  edgar/data/733269/0001193125-05-068429.txt
SC TO-T          DIGITAL IMPACT INC /DE/                                       1095105     2005-04-01  edgar/data/1095105/0001193125-05-068429.txt
SC TO-T/A        ACXIOM CORP                                                   733269      2005-04-15  edgar/data/733269/0001193125-05-070000.txt
SC 14D9          DIGITAL IMPACT INC /DE/                                       1095105     2005-04-01  edgar/data/1095105/0001193125-05-068500.txt
SC 14D9C         DIGITAL IMPACT INC /DE/                                       1095105     2005-03-25  edgar/data/1095105/0001193125-05-060000.txt
"""


def test_family_keeps_only_its_forms():
    toi = et.form_index_rows(SAMPLE, et.FAMILIES["SC TO-I"]["forms"])
    assert [r[0] for r in toi] == ["SC TO-I", "SC TO-I/A"]
    tot = et.form_index_rows(SAMPLE, et.FAMILIES["SC TO-T"]["forms"])
    assert [r[0] for r in tot] == ["SC TO-T", "SC TO-T", "SC TO-T/A", "SC 14D9"]   # not SC 14D9C
    assert tot[1][1] == "DIGITAL IMPACT INC /DE/" and tot[1][2] == "1095105"


def test_one_tot_filing_is_listed_under_both_parties():
    tot = et.form_index_rows(SAMPLE, ("SC TO-T",))
    acc = {r[4].rsplit("/", 1)[-1] for r in tot}
    assert len(tot) == 2 and len(acc) == 1


def test_trial_58_filing_list_rebuilds_identically():
    import pandas as pd
    if not (et.CACHE / "filings.csv").exists() or not (et.CACHE / "index").exists():
        pytest.skip("EDGAR cache (git-ignored) not present")
    fam = et.FAMILIES["SC TO-I"]
    rows = [r for y, q in et.quarters(fam["first"], fam["last"]) for r in et.index_rows(y, q)]
    f = pd.DataFrame(rows, columns=["form", "company", "cik", "date", "path"])
    f = f[f["date"] <= "2026-10-06"].drop_duplicates("path").sort_values(["cik", "date"])
    old = pd.read_csv(et.CACHE / "filings.csv", dtype=str)
    pd.testing.assert_frame_equal(f.reset_index(drop=True).astype(str), old.astype(str))
