"""The pre-declaration gate since 2026-11-18 (python/gate.py): G-REAL and
G-WORTH. docs/METHODOLOGY.md, "Pre-declaration gate (since 2026-11-18)"."""

import math
import sys
from pathlib import Path

import pytest
from scipy import stats

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "python"))

import gate as g


def test_k_correction_subtracts_nothing_at_k1_and_the_expected_max_at_k20():
    assert g.expected_max_normal(1) == 0.0
    assert g.expected_max_normal(2) == pytest.approx(1 / math.sqrt(math.pi), abs=1e-9)   # closed form
    assert g.expected_max_normal(20) == pytest.approx(1.8675, abs=1e-3)
    # K = 1: only the 26% out-of-sample shrink (McLean-Pontiff 2016)
    assert g.prior_t_from_design(4.0, 1) == pytest.approx(4.0 * 0.74)
    # K = 20: subtract E[max of 20 standard normals] first, then shrink
    assert g.prior_t_from_design(4.0, 20) == pytest.approx((4.0 - 1.8675) * 0.74, abs=1e-3)
    assert g.prior_t_from_design(4.0, 20) < g.prior_t_from_design(4.0, 1)


def test_the_statistic_follows_the_strategy_type():
    assert g.statistic("event") == "event-level t, clustered by ex-date month"
    assert g.statistic("portfolio") == "calendar-time t of monthly returns"
    with pytest.raises(ValueError):
        g.statistic("monthly_sharpe_for_an_event_trade")
    with pytest.raises(ValueError):                       # a cited Sharpe is a portfolio statistic
        g.g_real("event", cited_sharpe=0.5, years=10)


def test_p_pass_formula():
    assert g.p_pass(3.0) == pytest.approx(0.5)
    assert g.p_pass(2.0) == pytest.approx(1 - stats.norm.cdf(1.0))
    assert g.scale(2.0, n_confirm=40, n_design=10) == pytest.approx(4.0)
    # a cited Sharpe 0.57 over 13 years reproduces the old gate's merger-arb figure
    assert g.p_pass(g.prior_t_from_sharpe(0.57, 13)) == pytest.approx(0.172, abs=1e-3)
    assert g.g_real("portfolio", cited_sharpe=0.24, years=13)["g_real"] is False


def test_trial_60_passes_g_real_at_its_2026_11_02_design_numbers():
    """The v1 gate killed it on a monthly Sharpe (P = 0.042). On the event
    statistic its trial declared, the design numbers pass easily."""
    r = g.g_real("event", design_t=5.105, k=1, n_design=14, n_confirm=14 * 2.31)
    assert r["g_real"] and r["p_pass"] > 0.9
    w = g.g_worth(7.12, ceiling=11.90)
    assert not w["g_worth"] and "ceiling" in w["reason"]          # ceiling first
    assert not g.may_declare(r, w)
    assert g.may_declare(r, w, research_grade_in_writing=True)     # what the owner did


def test_g_worth_standalone_and_component():
    assert g.g_worth(1600, ceiling=5000)["g_worth"]
    assert not g.g_worth(1600, ceiling=1400)["g_worth"]            # ceiling under the bar kills
    assert not g.g_worth(1400, ceiling=5000)["g_worth"]
    c = g.g_worth(120, role="component", overlap=0.3)
    assert c["g_worth"] and "30%" in c["reason"]
    assert not g.g_worth(90, role="component")["g_worth"]


def test_the_rescreen_reproduces_the_recorded_table():
    out = g.rescreen().set_index("idea")
    v1 = out.loc[out.index.str.startswith("Reverse-split round-up v1")].iloc[0]
    assert v1["g_real"] and not v1["g_worth_standalone"]
    assert not out.loc[out.index.str.startswith("SPAC")].iloc[0]["g_real"]
    assert not out.loc[out.index.str.startswith("Merger arb")].iloc[0]["g_real"]
