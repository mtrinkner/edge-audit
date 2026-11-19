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


def test_the_replication_shrink_is_chosen_by_regime():
    assert g.shrink_for("own_design") == 0.26 and g.shrink_for("replication") == 0.58
    assert g.prior_t_from_design(10.0, 1, "replication") == pytest.approx(4.2)
    assert g.prior_t_from_design(10.0, 1, "own_design") == pytest.approx(7.4)
    with pytest.raises(ValueError):
        g.shrink_for("whatever_fits")


def test_projected_dollars_arithmetic():
    # reverse split v2: $3.66 an event x (36/136 eligible x 127.3 recent round-ups a year)
    rate = 36 / 136 * (115 + 148 + 119) / 3
    assert g.project(3.66, rate) == pytest.approx(123.3, abs=0.1)
    assert g.project(18.0, 50, cap_usd=540) == 540                # capacity caps the projection
    w = g.g_worth(14.6, projected=g.project(3.66, rate), role="component", overlap=0.0)
    assert w["g_worth"] and w["decided_by"] == "projection" and "no ceiling" in w["reason"]
    k = g.g_worth(7.12, projected=60.0, ceiling=1000.0)            # standalone: the ceiling decides first
    assert not k["g_worth"] and k["decided_by"] == "ceiling"
    assert not g.g_worth(7.12, projected=60.0, ceiling=100.3, role="component")["g_worth"]   # projection decides


def test_design_effect_n_eff():
    # a single event per month: n_eff = n, whatever rho
    assert g.n_eff(84, 1.0, 0.0) == 84 and g.n_eff(84, 1.0, 0.7) == 84
    # rho = 1: n_eff = n / m, the cluster count
    assert g.n_eff(120, 3.0, 1.0) == pytest.approx(40.0)
    # rho = 0: events only, the upper bound
    assert g.n_eff(120, 3.0, 0.0) == 120
    assert g.n_eff(120, 3.0, 0.05) == pytest.approx(120 / 1.1)
    with pytest.raises(ValueError):
        g.n_eff(10, 2.0, 1.5)
    # m in the confirmation: expected events per occupied month
    assert g.m_confirm(1, 84) == pytest.approx(1.0)
    assert g.m_confirm(83, 84) == pytest.approx(83 / g.expected_clusters(83, 84))


def test_design_path_uses_the_design_effect_with_bounds():
    assert g.expected_clusters(1, 84) == pytest.approx(1.0)
    r = g.g_real("event", design_t=5.82, k=2, n_design=36, n_confirm=83, design_clusters=30, confirm_months=84,
                 rho=0.2578)
    nd = g.n_eff(36, 36 / 30, 0.2578)
    nc = g.n_eff(83, g.m_confirm(83, 84), 0.2578)
    assert r["scaled_t"] == pytest.approx(r["prior_t"] * math.sqrt(nc / nd))
    assert r["scaled_t_clusters"] <= r["scaled_t"] <= r["scaled_t_events"]      # rho = 1 <= rho <= rho = 0
    # with no design estimate of rho, the primary 0.05 applies
    assert g.g_real("event", design_t=5.82, k=2, n_design=36, n_confirm=83, design_clusters=30,
                    confirm_months=84)["rho"] == g.RHO_DEFAULT
    assert g.g_real("portfolio", design_t=4.0, n_design=100, n_confirm=100)["scaled_t_clusters"] is None


def test_icc_recovers_within_month_correlation():
    assert g.icc([1, 1, 5, 5, 9, 9], ["a", "a", "b", "b", "c", "c"]) == pytest.approx(1.0)
    assert g.icc([1, 9, 1, 9, 1, 9], ["a", "a", "b", "b", "c", "c"]) == 0.0           # clipped at 0
    assert g.icc([1, 2, 3], ["a", "b", "c"]) != g.icc([1, 2, 3], ["a", "b", "c"])     # NaN: no month has two


def test_trial_52_is_a_false_pass_under_the_stated_rule():
    """Design t 10.67 on 2001-2019, inside Jiang-Kelly-Xiu's 1993-2019 sample:
    replication regime, 58%. P(pass) 0.37 clears 0.25, and the 2020-2026
    confirmation failed (gross t 1.80): a false pass. At the own-design 26% it
    would have been about 0.95."""
    rep = g.g_real("portfolio", design_t=10.67, n_design=228, n_confirm=80, regime="replication")
    own = g.g_real("portfolio", design_t=10.67, n_design=228, n_confirm=80, regime="own_design")
    assert rep["p_pass"] == pytest.approx(0.365, abs=0.01) and rep["g_real"]
    assert own["p_pass"] == pytest.approx(0.954, abs=0.01)
    cal, summary = g.calibrate()
    row = cal.set_index("trial").loc[52]
    assert row["outcome"] == "false pass" and row["shrink_regime"] == "replication"
    assert summary["false_kills"] == 0 and summary["cases"] >= 7


def test_cited_event_path_takes_58_percent_and_the_design_effect():
    assert g.shrink_for("cited") == 0.58
    r = g.g_real_cited_event(0.02, 0.08, n_confirm=100, confirm_months=84)
    assert r["prior_t"] == pytest.approx(0.25 * 0.42)
    ne = g.n_eff(100, g.m_confirm(100, 84), g.RHO_DEFAULT)
    assert r["scaled_t"] == pytest.approx(0.105 * math.sqrt(ne))
    assert r["scaled_t"] <= r["scaled_t_events"] == pytest.approx(0.105 * 10)
    assert g.g_real_cited_event(0.02, 0.08, 100, 84, rho=0.20)["scaled_t"] < r["scaled_t"]
    assert r["statistic"] == g.STATISTIC["event"]
    with pytest.raises(ValueError):
        g.g_real_cited_event(0.02, 0.0, 100, 84)


def test_retail_short_side_needs_borrow_adjusted_returns():
    s = g.g_real_cited_event(0.09, 0.8, 800, 84, side="short")
    assert s["tradeable_retail"] is False and s["p_pass"] is None and s["g_real"] is False
    b = g.g_real_cited_event(0.09, 0.8, 800, 84, side="short", borrow_adjusted=True)
    assert b["tradeable_retail"] and b["p_pass"] is not None
    assert g.tradeable_retail("long") and not g.tradeable_retail("short")
