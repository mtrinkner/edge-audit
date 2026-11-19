"""The Research map exports: sourced, aggregate-only, and consistent with the registry."""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "python"))

import dashboard_export as de
import registry

# The edge map aggregates git-ignored licensed caches; without them it can't be rebuilt.
LOCAL = all((ROOT / p).exists() for p in ("data/edgar/deals.csv", "data/rsplit/trades_design_gate.csv",
                                          "data/pead/ff_daily.parquet"))
needs_local = pytest.mark.skipif(not LOCAL, reason="licensed caches not on this machine")

# Row-level fields of CRSP, IBES or the deal files that must never leave the laptop.
FORBIDDEN = {"permno", "permco", "cusip", "ncusip", "cusip8", "ticker", "oftic", "gvkey", "cik",
             "dlyprc", "prc", "dlybid", "dlyask", "dlyopen", "dlyvol", "dlyret", "ret", "shrout",
             "bid", "ask", "price", "price_lo", "price_hi", "close", "open", "entry", "exit",
             "actual", "surpmean", "anndats", "fpedats", "dlycaldt", "issuernm"}


def research_map():
    out = {"families": de.family_status(), "funnel": de.funnel(), "trial_outcomes": de.trial_outcomes(),
           "decay": de.decay(), "bug_ledger": de.bug_ledger(), "candidates": de.candidates()}
    if LOCAL:
        out["edge_map"] = de.edge_map()
    return out


def test_every_research_map_row_has_a_source():
    for name, rows in research_map().items():
        assert rows, name
        for r in rows:
            assert str(r.get("source") or "").strip(), (name, r)


def test_every_family_has_a_label():
    fam = json.load(open(ROOT / "data" / "trial_families.json"))
    assert {k for k in fam if not k.startswith("_")} <= set(de.FAMILY_LABEL)


def test_no_exported_file_carries_row_level_licensed_fields():
    files = list((ROOT / "reports" / "dashboard").glob("*.json"))
    assert files
    for f in files:
        for r in json.loads(f.read_text()):
            bad = FORBIDDEN & {k.lower() for k in r}
            assert not bad, (f.name, bad)
    for name, rows in research_map().items():
        for r in rows:
            assert not FORBIDDEN & {k.lower() for k in r}, name


def test_funnel_counts_match_registry_status():
    t = registry.trials()
    f = de.funnel()
    stage = {r["stage"]: r["count"] for r in f if r["part"] == "stage"}
    assert stage["Strategies counted (N)"] == t["n_trials"]
    assert stage["Studies declared"] == t["declared"]
    assert stage["Recorded"] == t["recorded"]
    assert stage["Abandoned"] == t["abandoned"]
    fams = [r for r in f if r["part"] == "family"]
    assert sum(r["studies"] for r in fams) == t["declared"]
    assert sum(r["trials"] for r in fams) == t["n_trials"]
    assert sum(r["recorded"] for r in fams) == t["recorded"]


def test_luck_bar_is_the_registry_status_bar():
    bar = [r for r in de.trial_outcomes() if r["metric"] == "luck bar"][0]["design"]
    assert bar == round(de.expected_max_sharpe(registry.trials()["n_trials"], 348), 3)


@needs_local
def test_edge_map_dollars_match_capital_times_yield():
    # Literature rows are typed in from their source as all three numbers; they
    # are held to the same identity, so a typo in one of them fails here.
    for r in de.edge_map():
        implied = r["capital_usd"] * r["excess_pct_yr"] / 100
        assert abs(implied - r["usd_per_yr"]) <= 0.15 * abs(r["usd_per_yr"]) + 0.5, r["name"]


def test_bug_ledger_newest_first_with_numbers_where_they_exist():
    b = de.bug_ledger()
    assert [r["date"] for r in b] == sorted((r["date"] for r in b), reverse=True)
    assert all(r["after_value"] is not None for r in b)
