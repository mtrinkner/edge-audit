"""Forward signal ledger (python/rsplit_ledger.py): the calendar, the signal
the frozen rule gives on a synthetic filing history, and append-only writes."""

import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "python"))

import reverse_split as rs
import rsplit_ledger as rl


@pytest.fixture(autouse=True)
def forward_calendar():
    old = rs.CAL
    rl.use_forward_calendar()
    yield
    rs.CAL = old


def filing(acc, accepted, form="8-K", cls=None, ratio=None, effective=None, cancel=False):
    return {"status": "ok", "acc": acc, "accepted": accepted, "form": form, "frac_class": cls,
            "ratio": ratio, "ratio_is_range": False, "effective": effective, "cancel": cancel}


def test_forward_calendar_skips_weekends_and_nyse_holidays():
    cal = set(rs.CAL)
    assert "2026-11-25" in cal and "2026-11-27" in cal
    assert "2026-11-26" not in cal                      # Thanksgiving
    assert "2026-11-28" not in cal and "2026-11-29" not in cal
    assert "2026-12-25" not in cal and "2027-01-01" not in cal
    assert rs.bday("2026-11-25", 1) == "2026-11-27"


def test_an_announced_round_up_split_gets_its_planned_entry_day():
    p = pd.DataFrame([filing("a1", "2026-11-10", "DEF 14C", cls="ROUNDUP_HOLDER"),
                      filing("a2", "2026-11-16", ratio=20, effective="2026-12-01")])
    s = rl.signal(p, "2026-11-18")
    assert s["announcement_acc"] == "a2" and s["ratio"] == 20 and s["cls"] == "ROUNDUP_HOLDER"
    # T+1: the last trade that settles before Dec 1 is Nov 27 (Nov 30 would settle on Dec 1)
    assert s["planned_entry_day"] == "2026-11-27" and s["status"] == "upcoming"
    assert rl.signal(p, "2026-11-27")["status"] == "entry_today"


def test_no_announcement_without_a_ratio_and_a_future_date():
    p = pd.DataFrame([filing("a1", "2026-11-10", "DEF 14A", cls="ROUNDUP_HOLDER", ratio=10)])
    assert rl.signal(p, "2026-11-18") is None


def test_a_cash_in_lieu_split_is_logged_but_not_a_trade():
    p = pd.DataFrame([filing("a1", "2026-11-16", cls="CASH", ratio=10, effective="2026-12-01")])
    s = rl.signal(p, "2026-11-18")
    assert s["status"] == "not_roundup" and s["planned_entry_day"] is not None


def test_a_later_cancellation_is_logged():
    p = pd.DataFrame([filing("a1", "2026-11-10", cls="ROUNDUP_HOLDER", ratio=10, effective="2026-12-01"),
                      filing("a2", "2026-11-16", cancel=True)])
    s = rl.signal(p, "2026-11-18")
    assert s["cancelled"] and s["status"] == "cancelled" and s["planned_entry_day"] is None


def test_the_ledger_appends_only_changes(tmp_path):
    path = tmp_path / "ledger.csv"
    row = {"run_date": "2026-11-18", "cik": "1", "company": "X", "tickers": "X", "exchange": "Nasdaq",
           "announcement_acc": "a2", "announcement_usable": "2026-11-17", "ratio": 20,
           "effective_known": "2026-12-01", "cls": "ROUNDUP_HOLDER", "class_acc": "a1",
           "planned_entry_day": "2026-11-27", "cancelled": False, "status": "upcoming", "rule": "v2"}
    assert rl.append([row], path) == 1
    assert rl.append([{**row, "run_date": "2026-11-19"}], path) == 0            # nothing changed
    assert rl.append([{**row, "run_date": "2026-11-20", "effective_known": "2026-12-02",
                       "planned_entry_day": "2026-11-30"}], path) == 1        # postponed
    led = pd.read_csv(path, dtype=str)
    assert list(led["run_date"]) == ["2026-11-18", "2026-11-20"]             # the first row is kept as written
    assert list(led["ratio"]) == ["20", "20"]
