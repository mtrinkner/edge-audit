"""A signal must not mix dividend-adjusted and raw prices.

FOUND 2026-10-26. The vendor's adj_close is back-adjusted from today, so on any
past date adj_close / close is the product of every dividend paid AFTER that
date. KO's ratio is 0.67 in 2014 and XOM's 0.59. A feature that divides
adj_close by a raw high or low therefore contains future dividends, and the
truncation test in test_no_lookahead.py cannot see it: deleting later bars does
not re-adjust the earlier ones.

THE TEST. A signal built consistently from one price basis is unchanged when
adj_close is multiplied by a constant. One that compares across bases changes.
The three defects below were found on 2026-10-26 and fixed the same day; the
earlier outputs are kept as reports/*_pre_2026-10-26.csv. The test fails if a
new one appears.

  pullback_to_sma50 (signals.py)   raw low/close against an SMA of adj_close.
      In 2014 for heavy dividend payers the raw low almost never reaches the
      adjusted average, so the signal fires mostly late in the sample. Trial 27.
  dist_52w_high, dist_52w_low (sql/02_features.sql)   adj_close over a raw
      252-day high/low. Model features in trials 3-6 and the frozen gbm_v1,
      whose live inputs now see a ratio near 1 while it trained on 0.6-0.95.
      Their permutation importance was about zero, so the effect on results is
      small; the train/serve skew in the gbm_v1 forward test is not.
"""

import sqlite3
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "python"))

import signals as sg

# Both were fixed on 2026-10-26; the descriptions above record what they were.
KNOWN_MIXED_SIGNALS: set[str] = set()
KNOWN_MIXED_FEATURES: set[str] = set()


def _bars(n=600, seed=7):
    rng = np.random.default_rng(seed)
    close = 50 * np.exp(np.cumsum(rng.normal(0.0003, 0.015, n)))
    open_ = close * np.exp(rng.normal(0, 0.006, n))
    high = np.maximum(open_, close) * np.exp(np.abs(rng.normal(0, 0.008, n)))
    low = np.minimum(open_, close) * np.exp(-np.abs(rng.normal(0, 0.008, n)))
    dates = (np.datetime64("2015-01-01") + np.arange(n)).astype(str)
    return pd.DataFrame({"symbol": "TEST", "dt": dates, "open": open_, "high": high,
                         "low": low, "close": close, "adj_close": close * 0.97,
                         "volume": rng.integers(1_000_000, 5_000_000, n).astype(float)})


def test_signals_use_one_price_basis():
    df = _bars()
    scaled = df.assign(adj_close=df["adj_close"] * 0.6)
    mixed = {name for name in sg.SIGNALS
             if not sg.compute(df, name).equals(sg.compute(scaled, name))}
    assert mixed == KNOWN_MIXED_SIGNALS, (
        f"price-basis mixing: new {mixed - KNOWN_MIXED_SIGNALS}, "
        f"no longer mixed {KNOWN_MIXED_SIGNALS - mixed}")


def _sql_features(df):
    con = sqlite3.connect(":memory:")
    con.execute("CREATE TABLE symbols (symbol TEXT, sector TEXT, kind TEXT)")
    con.execute("INSERT INTO symbols VALUES ('TEST', 'x', 'stock')")
    con.execute("CREATE TABLE trading_days (dt TEXT, day_index INT)")
    con.executemany("INSERT INTO trading_days VALUES (?, ?)",
                    list(zip(df["dt"], range(len(df)))))
    df.to_sql("bars", con, index=False)
    con.executescript((ROOT / "sql" / "02_features.sql").read_text())
    return pd.read_sql("SELECT * FROM v_features ORDER BY day_index", con)


def test_sql_features_use_one_price_basis():
    df = _bars()
    a = _sql_features(df)
    b = _sql_features(df.assign(adj_close=df["adj_close"] * 0.6))
    skip = {"symbol", "dt", "sector", "kind", "adj_close", "sma_20", "sma_50", "sma_200"}
    mixed = set()
    for c in a.columns:
        if c in skip:
            continue
        x, y = a[c].to_numpy(float), b[c].to_numpy(float)
        if not np.allclose(x, y, equal_nan=True, rtol=1e-9, atol=1e-12):
            mixed.add(c)
    assert mixed == KNOWN_MIXED_FEATURES, (
        f"price-basis mixing: new {mixed - KNOWN_MIXED_FEATURES}, "
        f"no longer mixed {KNOWN_MIXED_FEATURES - mixed}")
