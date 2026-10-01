#!/usr/bin/env python3
"""The correctness test the whole project rests on.

    python3 tests/test_no_lookahead.py

If features leak the future, every metric downstream is fiction and no amount of
careful backtesting rescues it. Four independent checks:

  1. TRUNCATION (the decisive one). Rebuild the entire feature pipeline from a
     database containing bars only up to date T. If a feature computed at T is
     identical whether or not the data after T exists, that feature cannot be
     reading the future. Any mismatch is a leak, located by column.

  2. INDEPENDENT RECOMPUTE. Recalculate a sample of features in pandas, using a
     different implementation, and compare. Catches window-frame mistakes that
     the truncation test alone would miss because they are wrong consistently.

  3. LABEL DIRECTION. Verify fwd_ret_5d at t really is the return to t+5, and
     that labels are NULL at the end of each symbol's history rather than
     silently filled.

  4. LABEL/FEATURE SEPARATION. Confirm no forward-looking column leaked into the
     feature tables.
"""

from __future__ import annotations

import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "python"))
import config  # noqa: E402

TOL = 1e-9
FEATURE_COLS = [
    "sma_20", "sma_50", "sma_200", "atr_14", "vol_20_annual",
    "dist_sma_20", "dist_sma_50", "dist_sma_200", "rel_volume",
    "dist_52w_high", "dist_52w_low", "atr_pct", "mom_5d", "mom_21d",
    "mom_63d", "mom_126d", "regime_bull", "above_200", "ret_1d",
    "gap_pct", "range_pct", "prior_high_20", "prior_low_20",
]
FORWARD_WORDS = ("fwd_", "label_", "next_", "future")

results: list[tuple[str, bool, str]] = []


def record(name: str, passed: bool, detail: str = "") -> None:
    results.append((name, passed, detail))
    mark = "PASS" if passed else "FAIL"
    print(f"  [{mark}] {name}" + (f" — {detail}" if detail else ""))


def apply_pipeline(db_path: Path) -> None:
    con = sqlite3.connect(db_path)
    for f in ("02_features.sql", "03_materialize.sql", "04_labels.sql"):
        con.executescript((config.SQL_DIR / f).read_text())
    con.commit()
    con.close()


# ---------------------------------------------------------------- test 1
def test_truncation(cutoff: str = "2023-06-30") -> None:
    print(f"\n[1] TRUNCATION TEST — rebuild with no data after {cutoff}")
    with tempfile.TemporaryDirectory() as td:
        trunc = Path(td) / "truncated.db"
        shutil.copy(config.DB_PATH, trunc)
        con = sqlite3.connect(trunc)
        removed = con.execute("DELETE FROM bars WHERE dt > ?", (cutoff,)).rowcount
        con.execute("DELETE FROM trading_days")
        con.execute(
            """INSERT INTO trading_days (dt, day_index, year, month, dow)
               SELECT dt, ROW_NUMBER() OVER (ORDER BY dt) - 1,
                      CAST(strftime('%Y',dt) AS INT), CAST(strftime('%m',dt) AS INT),
                      CAST(strftime('%w',dt) AS INT)
               FROM (SELECT DISTINCT dt FROM bars) ORDER BY dt"""
        )
        con.commit()
        con.close()
        print(f"  removed {removed:,} bars after the cutoff, rebuilding pipeline")
        apply_pipeline(trunc)

        cols = ", ".join(FEATURE_COLS)
        q = f"SELECT symbol, dt, {cols} FROM feature_panel WHERE dt = ? ORDER BY symbol"
        full = pd.read_sql(q, sqlite3.connect(config.DB_PATH), params=(cutoff,))
        part = pd.read_sql(q, sqlite3.connect(trunc), params=(cutoff,))

        if full.empty or part.empty:
            record("truncation", False, f"no rows at {cutoff} in one of the builds")
            return
        if len(full) != len(part):
            record("truncation", False, f"row counts differ: {len(full)} vs {len(part)}")
            return

        merged = full.merge(part, on=["symbol", "dt"], suffixes=("_full", "_trunc"))
        leaks = []
        for c in FEATURE_COLS:
            a = merged[f"{c}_full"].to_numpy(dtype=float)
            b = merged[f"{c}_trunc"].to_numpy(dtype=float)
            both_nan = np.isnan(a) & np.isnan(b)
            diff = np.where(both_nan, 0.0, np.abs(a - b))
            diff = np.nan_to_num(diff, nan=np.inf)
            worst = float(np.nanmax(diff)) if len(diff) else 0.0
            if worst > TOL:
                leaks.append(f"{c} (max diff {worst:.3e})")
        if leaks:
            record("truncation", False, "LEAKING: " + ", ".join(leaks))
        else:
            record("truncation", True,
                   f"{len(FEATURE_COLS)} features identical across {len(merged)} symbols")

        # The labels at the cutoff MUST differ: they legitimately need future data,
        # so in the truncated build they have to be missing. If they are present,
        # the label logic is fabricating values.
        lab_full = pd.read_sql(
            "SELECT COUNT(fwd_ret_5d) n FROM labels WHERE dt = ?",
            sqlite3.connect(config.DB_PATH), params=(cutoff,))["n"][0]
        lab_part = pd.read_sql(
            "SELECT COUNT(fwd_ret_5d) n FROM labels WHERE dt = ?",
            sqlite3.connect(trunc), params=(cutoff,))["n"][0]
        record("labels absent without future data",
               lab_full > 0 and lab_part == 0,
               f"full build has {lab_full} labels at cutoff, truncated has {lab_part}")


# ---------------------------------------------------------------- test 2
def test_independent_recompute(symbol: str = "AAPL") -> None:
    print(f"\n[2] INDEPENDENT RECOMPUTE — pandas vs SQL for {symbol}")
    con = sqlite3.connect(config.DB_PATH)
    bars = pd.read_sql(
        "SELECT dt, open, high, low, close, adj_close, volume FROM bars "
        "WHERE symbol=? ORDER BY dt", con, params=(symbol,))
    sqlf = pd.read_sql(
        "SELECT dt, sma_20, vol_20_annual, mom_21d, rel_volume, atr_14, "
        "dist_52w_high FROM features WHERE symbol=? ORDER BY dt",
        con, params=(symbol,))
    con.close()

    b = bars.copy()
    b["ret"] = b["adj_close"].pct_change()
    exp = pd.DataFrame({"dt": b["dt"]})
    exp["sma_20"] = b["adj_close"].rolling(20).mean()
    # ddof=0 matches the population variance identity used in SQL.
    exp["vol_20_annual"] = b["ret"].rolling(20).std(ddof=0) * np.sqrt(252.0)
    exp["mom_21d"] = b["adj_close"] / b["adj_close"].shift(21) - 1.0
    exp["rel_volume"] = b["volume"] / b["volume"].rolling(20).mean()
    prev_close = b["close"].shift(1)
    tr = pd.concat([
        b["high"] - b["low"],
        (b["high"] - prev_close).abs(),
        (b["low"] - prev_close).abs(),
    ], axis=1).max(axis=1)
    exp["atr_14"] = tr.rolling(14).mean()
    exp["dist_52w_high"] = b["adj_close"] / b["high"].rolling(252).max() - 1.0

    m = sqlf.merge(exp, on="dt", suffixes=("_sql", "_pd"))
    bad = []
    for c in ["sma_20", "vol_20_annual", "mom_21d", "rel_volume", "atr_14", "dist_52w_high"]:
        a = m[f"{c}_sql"].to_numpy(dtype=float)
        e = m[f"{c}_pd"].to_numpy(dtype=float)
        mask = ~(np.isnan(a) | np.isnan(e))
        if mask.sum() == 0:
            bad.append(f"{c} (no comparable rows)")
            continue
        rel = np.abs(a[mask] - e[mask]) / np.maximum(np.abs(e[mask]), 1e-12)
        if np.nanmax(rel) > 1e-6:
            bad.append(f"{c} (max rel diff {np.nanmax(rel):.2e})")
    record("independent recompute", not bad,
           ", ".join(bad) if bad else "6 features match pandas to 1e-6 relative")


# ---------------------------------------------------------------- test 3
def test_label_direction(symbol: str = "MSFT") -> None:
    print(f"\n[3] LABEL DIRECTION — {symbol}")
    con = sqlite3.connect(config.DB_PATH)
    df = pd.read_sql(
        """SELECT f.dt, f.adj_close, l.fwd_ret_5d, l.fwd_ret_1d
           FROM features f JOIN labels l ON l.symbol=f.symbol AND l.dt=f.dt
           WHERE f.symbol=? ORDER BY f.dt""", con, params=(symbol,))
    tail = pd.read_sql(
        """SELECT COUNT(*) n FROM labels
           WHERE symbol=? AND fwd_ret_5d IS NOT NULL
             AND dt > (SELECT MAX(dt) FROM labels WHERE symbol=?
                       AND fwd_ret_5d IS NOT NULL)""",
        con, params=(symbol, symbol))["n"][0]
    con.close()

    expect_5 = df["adj_close"].shift(-5) / df["adj_close"] - 1.0
    expect_1 = df["adj_close"].shift(-1) / df["adj_close"] - 1.0
    for col, exp, h in (("fwd_ret_5d", expect_5, 5), ("fwd_ret_1d", expect_1, 1)):
        a = df[col].to_numpy(dtype=float)
        e = exp.to_numpy(dtype=float)
        mask = ~(np.isnan(a) | np.isnan(e))
        worst = float(np.nanmax(np.abs(a[mask] - e[mask]))) if mask.sum() else np.inf
        record(f"{col} equals t+{h} return", worst < 1e-12, f"max diff {worst:.2e}")

    last_n = int(df["fwd_ret_5d"].tail(5).isna().sum())
    record("last 5 bars have no 5-day label", last_n == 5,
           f"{last_n} of the final 5 rows are NULL as required")


# ---------------------------------------------------------------- test 4
def test_no_forward_columns() -> None:
    print("\n[4] LABEL/FEATURE SEPARATION")
    con = sqlite3.connect(config.DB_PATH)
    offenders = {}
    for tbl in ("features", "feature_panel"):
        cols = [r[1] for r in con.execute(f"PRAGMA table_info({tbl})")]
        hits = [c for c in cols if any(w in c.lower() for w in FORWARD_WORDS)]
        if hits:
            offenders[tbl] = hits
    con.close()
    record("no forward-looking columns in feature tables", not offenders, str(offenders))


def main() -> int:
    print("=" * 72)
    print("LOOKAHEAD AUDIT")
    print("=" * 72)
    test_truncation()
    test_independent_recompute()
    test_label_direction()
    test_no_forward_columns()

    failed = [r for r in results if not r[1]]
    print("\n" + "=" * 72)
    print(f"{len(results) - len(failed)}/{len(results)} checks passed")
    if failed:
        print("\nFAILURES:")
        for name, _, detail in failed:
            print(f"  {name}: {detail}")
        print("\nThe pipeline leaks. Do not trust any downstream metric.")
        return 1
    print("No lookahead detected. Downstream metrics are computed on honest inputs.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
