#!/usr/bin/env python3
"""Item 3: does the fundamentals sleeve work in identifiable market states?

    python3 python/regime_test.py --horizon 63

THE DISTINCTION THIS RESTS ON. Flipping a signal's sign because this decade
disagreed with the paper that proposed it is fitting: the sample picks the
answer. Asking whether a signal works in states that can be identified IN
ADVANCE is a different question, because the state is observable at decision time
and the hypothesis is testable going forward.

Both regimes here are computed from the benchmark's own history and are known at
the close of each day, so a book could actually be run this way:

  trend  SPY above or below its own 200-day average
  vol    SPY trailing 63-day realized volatility, top vs bottom tercile of the
         distribution observed UP TO THAT DAY, never the full-sample tercile,
         which would leak the future into the regime label

The honest cost: four conditional cells is four more trials, and splitting a
sample that already has about fifty independent observations leaves very few in
each cell. A strong number in a cell with twelve observations is not a finding.
Each cell reports its own count for that reason.
"""

from __future__ import annotations

import argparse
import sys

import numpy as np
import pandas as pd

import config
import db
import lockbox
from fundamental_signals import SIGNALS as FUND_SIGNALS
from multi_source import attach_fundamentals
from neutral_book import book_returns, get_panel, zscore


def regimes() -> pd.DataFrame:
    with db.connect() as con:
        spy = pd.read_sql(
            "SELECT dt, adj_close FROM bars WHERE symbol='SPY' ORDER BY dt", con)
    spy["sma200"] = spy["adj_close"].rolling(200, min_periods=200).mean()
    spy["above"] = (spy["adj_close"] > spy["sma200"]).astype(float)
    r = spy["adj_close"].pct_change()
    spy["vol63"] = r.rolling(63, min_periods=63).std() * np.sqrt(252)
    # Expanding terciles: the cutoff on any day uses only days before it.
    spy["vol_hi"] = (spy["vol63"] >
                     spy["vol63"].expanding(250).quantile(0.667).shift(1)).astype(float)
    spy["vol_lo"] = (spy["vol63"] <
                     spy["vol63"].expanding(250).quantile(0.333).shift(1)).astype(float)
    return spy[["dt", "above", "vol_hi", "vol_lo", "vol63"]]


def stats(r: pd.Series, h: int) -> dict:
    if len(r) < 3 * h:
        return {}
    s = r.to_numpy()[::h]
    if len(s) < 3 or s.std(ddof=1) == 0:
        return {}
    return {"n": int(len(s)), "ret_ann": float(r.mean() * 252 / h),
            "sharpe": float(s.mean() / s.std(ddof=1) * np.sqrt(252 / h)),
            "t": float(s.mean() / (s.std(ddof=1) / np.sqrt(len(s))))}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--horizon", type=int, default=63)
    ap.add_argument("--quantiles", type=int, default=5)
    ap.add_argument("--min-adv", type=float, default=5e6)
    args = ap.parse_args()
    h, q = args.horizon, args.quantiles

    st = lockbox.state()
    max_date = (pd.Timestamp(st["seal_from"]) - pd.Timedelta(days=1)).date().isoformat()
    lockbox.guard(max_date, "regime_test")
    panel = get_panel(max_date, [h], False)
    panel = panel[panel["adv"] >= args.min_adv]
    panel = attach_fundamentals(panel)

    parts = [zscore(panel[c], [panel["dt"], panel["sector"]])
             for c in FUND_SIGNALS if c in panel.columns]
    panel["s_fund"] = pd.concat(parts, axis=1).mean(axis=1, skipna=True)
    full = book_returns(panel, "s_fund", h, q, True)

    reg = regimes()
    rmap = {c: dict(zip(reg["dt"], reg[c])) for c in ("above", "vol_hi", "vol_lo")}
    idx = pd.Series(full.index)

    cells = {
        "ALL (unconditional)": pd.Series(True, index=full.index),
        "market above 200d": idx.map(rmap["above"]).fillna(0).astype(bool).values,
        "market below 200d": (1 - idx.map(rmap["above"]).fillna(0)).astype(bool).values,
        "high volatility": idx.map(rmap["vol_hi"]).fillna(0).astype(bool).values,
        "low volatility": idx.map(rmap["vol_lo"]).fillna(0).astype(bool).values,
    }

    print(f"\n{'=' * 74}\nFUNDAMENTALS SLEEVE BY REGIME\n{'=' * 74}")
    print(f"{'regime':<24}{'dates':>8}{'indep':>7}{'ret/yr':>10}{'Sharpe':>9}{'t':>7}")
    rows = []
    for label, mask in cells.items():
        sub = full[mask] if not isinstance(mask, pd.Series) else full[mask.values]
        m = stats(sub, h)
        if not m:
            print(f"{label:<24}{len(sub):>8}{'-':>7}{'too few':>10}")
            continue
        rows.append({"regime": label, "dates": len(sub), **m})
        print(f"{label:<24}{len(sub):>8}{m['n']:>7}{m['ret_ann']:>+9.2%}"
              f"{m['sharpe']:>9.2f}{m['t']:>7.2f}")

    res = pd.DataFrame(rows)
    res.to_csv(config.ROOT / "reports" / "regime_test.csv", index=False)
    print("\nReading this: each cell is a separate trial, and a cell with a dozen")
    print("independent observations can post any Sharpe at all. Compare the t")
    print("column, not the Sharpe column, and remember four cells were examined.")
    if not res.empty:
        b = res[res.regime != "ALL (unconditional)"]
        if not b.empty:
            best = b.loc[b.sharpe.idxmax()]
            print(f"\nbest cell: {best.regime}  Sharpe {best.sharpe:+.2f}, "
                  f"t {best.t:+.2f}, {int(best.n)} independent observations")
    return 0


if __name__ == "__main__":
    sys.exit(main())
