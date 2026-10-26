"""Inference tools added in the 2026-10-26 audit.

The central one: entry-date clustering understates the standard error of
multi-day trades, because trades entered on neighbouring days share most of
their holding period. This builds that situation from pure noise and checks
that block clustering widens the error and entry-date clustering does not.
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "python"))

import deflated_sharpe as ds
from evaluation import (block_clustered_mean, clustered_mean, factor_alpha,
                        newey_west_t, rank_ic)


def _overlapping_trades(hold: int, n_stocks=40, n_days=800, seed=3):
    """Every stock is bought every day and held `hold` days. Zero true edge."""
    rng = np.random.default_rng(seed)
    r = rng.normal(0, 0.02, (n_days + hold, n_stocks))
    c = np.vstack([np.zeros((1, n_stocks)), np.cumsum(r, axis=0)])
    fwd = c[hold:hold + n_days] - c[:n_days]
    # Built with numpy: DataFrame.stack and pd.bdate_range both segfault on this
    # machine's pandas 3 / Python 3.14 build. Weekends are left in; it is noise.
    dates = (np.datetime64("2015-01-01") + np.arange(n_days)).astype(str)
    return pd.DataFrame({"dt": np.repeat(dates, n_stocks),
                         "symbol": np.tile(np.arange(n_stocks), n_days),
                         "ret": fwd.ravel()})


def test_entry_date_clustering_understates_overlapping_holds():
    t = _overlapping_trades(hold=20)
    by_date = clustered_mean(t["ret"], t["dt"])
    by_block = block_clustered_mean(t["ret"], t["dt"], hold_days=20)
    # Twenty-fold overlap should widen the SE by roughly sqrt(20)-ish.
    assert by_block["se"] > 2.5 * by_date["se"], (by_block["se"], by_date["se"])


def test_block_clustering_is_neutral_without_overlap():
    t = _overlapping_trades(hold=1)
    by_date = clustered_mean(t["ret"], t["dt"])
    by_block = block_clustered_mean(t["ret"], t["dt"], hold_days=1, block_multiple=1)
    assert 0.6 < by_block["se"] / by_date["se"] < 1.6


def test_newey_west_matches_plain_t_without_lags():
    x = pd.Series(np.random.default_rng(0).normal(0.001, 0.01, 500))
    nw = newey_west_t(x, lags=0)
    plain = x.mean() / (x.std(ddof=0) / np.sqrt(len(x)))
    assert abs(nw["t"] - plain) < 1e-9


def test_newey_west_widens_for_overlapping_returns():
    r = np.random.default_rng(1).normal(0, 0.01, 2000)
    h = 20
    overl = pd.Series(np.convolve(r, np.ones(h), "valid"))
    assert newey_west_t(overl, lags=h - 1)["se"] > 3 * newey_west_t(overl, lags=0)["se"]


def test_factor_alpha_recovers_known_alpha_and_beta():
    rng = np.random.default_rng(2)
    n = 3000
    f = pd.DataFrame({"mkt": rng.normal(0.0004, 0.01, n), "smb": rng.normal(0, 0.005, n)})
    y = 0.0002 + 1.3 * f["mkt"] - 0.4 * f["smb"] + rng.normal(0, 0.002, n)
    out = factor_alpha(y, f, lags=5)
    assert abs(out["coef"]["mkt"] - 1.3) < 0.02
    assert abs(out["coef"]["smb"] + 0.4) < 0.03
    assert abs(out["coef"]["alpha"] - 0.0002) < 0.0001
    assert out["alpha_t"] > 3


def test_rank_ic_sees_a_planted_signal():
    rng = np.random.default_rng(4)
    rows = []
    for d in range(60):
        s = rng.normal(size=200)
        rows.append(pd.DataFrame({"d": d, "s": s, "r": 0.1 * s + rng.normal(size=200)}))
    df = pd.concat(rows)
    ic = rank_ic(df["s"], df["r"], df["d"])
    assert len(ic) == 60 and 0.05 < ic.mean() < 0.15


def test_power_functions_agree():
    # A Sharpe confirmed after exactly its minimum track record has PSR = 95%.
    yrs = ds.min_track_record_years(1.0, 0.0, 0.95)
    psr = ds.probabilistic_sharpe_ratio(1.0, 0.0, int(round(yrs * 252)), 252)
    assert abs(psr - 0.95) < 0.01
    assert ds.probabilistic_sharpe_ratio(0.8, 0.8, 1000, 252) == 0.5
    # Annualized SE is about 1/sqrt(years) near zero, whatever the frequency.
    assert abs(ds.sharpe_se(0.0, 10, periods_per_year=252) - 1 / np.sqrt(10)) < 0.01
    assert abs(ds.sharpe_se(0.0, 10, periods_per_year=12) - 1 / np.sqrt(10)) < 0.02
    assert 1.4 < ds.min_detectable_sharpe(3) < 1.5
