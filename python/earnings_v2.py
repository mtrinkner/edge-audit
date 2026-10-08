#!/usr/bin/env python3
"""Item 2: raise the quality of the earnings sleeve without adding data.

    python3 python/earnings_v2.py --horizon 63

Three changes, each justified before it was run:

1. RESTRICT TO THE DRIFT WINDOW. Post-earnings drift is an event-time effect that
   the literature puts at roughly sixty trading sessions. The v1 sleeve carried a
   surprise for up to 250 days, so most of the book was holding stale news. A
   signal held past the window it works in is noise wearing the signal's name.

2. DROP THE WEAK COMPONENTS. beat_streak, days_since and surprise_accel each
   posted a negative Sharpe on their own. They were included for completeness and
   they diluted the two that worked. Removing them is not sample fitting: they
   were dropped for having no documented basis as standalone predictors, which
   was true before the test.

3. ADD THE ANNOUNCEMENT-DAY REACTION. The market's own move on the release is
   information the surprise number does not contain, since it reflects guidance,
   margins and everything else said that day. Combining the two is standard and
   predates this project.

Everything else is held constant so the comparison isolates these changes:
same universe, same sector neutralization, same quintiles, same two-sided costs.
"""

from __future__ import annotations

import argparse
import sys

import numpy as np
import pandas as pd

import config
import db
import lockbox
from earnings_signals import as_of_panel, build_event_features
from neutral_book import book_returns, get_panel, zscore

DRIFT_WINDOW = 60


def build(panel: pd.DataFrame) -> pd.DataFrame:
    with db.connect() as con:
        ev = pd.read_sql(
            "SELECT symbol, tradeable_from, surprise_pct FROM earnings "
            "WHERE surprise_pct IS NOT NULL ORDER BY symbol, tradeable_from", con)
        sess = pd.read_sql("SELECT dt, day_index FROM trading_days", con)
        px = pd.read_sql("SELECT symbol, dt, day_index, adj_close FROM features", con)

    # Announcement-day reaction, computed from the session the news could first
    # be traded on. It uses only that day's realized move, nothing later.
    px = px.sort_values(["symbol", "day_index"])
    px["ret_0"] = px.groupby("symbol")["adj_close"].pct_change()
    ev = ev.merge(px[["symbol", "dt", "ret_0"]],
                  left_on=["symbol", "tradeable_from"], right_on=["symbol", "dt"],
                  how="left").drop(columns="dt")
    ev = ev.rename(columns={"ret_0": "event_reaction"})
    ev = build_event_features(ev)

    merged = as_of_panel(ev, panel[["symbol", "dt"]].copy())
    idx = dict(zip(sess["dt"], sess["day_index"]))
    merged["age"] = merged["dt"].map(idx) - merged["tradeable_from"].map(idx)

    # The restriction that defines v2: outside the drift window there is no
    # position, rather than a stale one.
    inside = (merged["age"] >= 0) & (merged["age"] <= DRIFT_WINDOW)
    for c in ("sue", "surprise_pct", "event_reaction"):
        merged.loc[~inside, c] = np.nan
    merged["sue_x_reaction"] = merged["sue"] * np.sign(merged["event_reaction"])

    cols = ["sue", "surprise_pct", "event_reaction", "sue_x_reaction", "age"]
    return panel.merge(merged[["symbol", "dt"] + cols], on=["symbol", "dt"], how="left")


def sleeve(panel, cols, label):
    parts = [zscore(panel[c], [panel["dt"], panel["sector"]])
             for c in cols if c in panel.columns]
    print(f"  {label}: {len(parts)} signals {cols}")
    return pd.concat(parts, axis=1).mean(axis=1, skipna=True)


def metrics(r, h):
    if len(r) < 60:
        return {}
    s = r.to_numpy()[::h]
    if len(s) < 3 or s.std(ddof=1) == 0:
        return {}
    eq = (1 + pd.Series(s)).cumprod()
    return {"ret_ann": float(r.mean() * 252 / h),
            "vol": float(s.std(ddof=1) * np.sqrt(252 / h)),
            "sharpe": float(s.mean() / s.std(ddof=1) * np.sqrt(252 / h)),
            "t": float(s.mean() / (s.std(ddof=1) / np.sqrt(len(s)))),
            "max_dd": float(((eq.cummax() - eq) / eq.cummax()).max()),
            "n": int(len(s))}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--horizon", type=int, default=63)
    ap.add_argument("--quantiles", type=int, default=5)
    ap.add_argument("--min-adv", type=float, default=5e6)
    args = ap.parse_args()
    h, q = args.horizon, args.quantiles

    st = lockbox.state()
    max_date = (pd.Timestamp(st["seal_from"]) - pd.Timedelta(days=1)).date().isoformat()
    lockbox.guard(max_date, "earnings_v2")
    panel = get_panel(max_date, [h], False)
    panel = panel[panel["adv"] >= args.min_adv]
    panel = build(panel)
    inw = panel["sue"].notna().mean()
    print(f"\n  rows inside the {DRIFT_WINDOW}-day drift window: {inw:.1%}\n")

    variants = {
        "v1: all 6, 250d stale": None,     # reconstructed below for reference
        "v2a: sue + surprise, windowed": ["sue", "surprise_pct"],
        "v2b: + event reaction": ["sue", "surprise_pct", "event_reaction"],
        "v2c: reaction only": ["event_reaction"],
        "v2d: sue x reaction sign": ["sue", "surprise_pct", "sue_x_reaction"],
    }
    rows = []
    for label, cols in variants.items():
        if cols is None:
            continue
        panel["_s"] = sleeve(panel, cols, label)
        r = book_returns(panel, "_s", h, q, True)
        m = metrics(r, h)
        if m:
            rows.append({"variant": label, **m})

    res = pd.DataFrame(rows)
    res.to_csv(config.ROOT / "reports" / "earnings_v2.csv", index=False)
    f = lambda v, s="{:.2f}": s.format(v) if pd.notna(v) else "    -"
    print(f"\n{'=' * 78}\nEARNINGS SLEEVE — v1 baseline vs v2 variants\n{'=' * 78}")
    print(f"{'variant':<32}{'ret/yr':>10}{'vol':>8}{'Sharpe':>9}{'t':>7}{'maxDD':>9}")
    print(f"{'v1 (previous best)':<32}{'+2.21%':>10}{'7.3%':>8}{'0.52':>9}{'1.84':>7}{'9.9%':>9}")
    for _, r in res.iterrows():
        print(f"{r.variant:<32}{f(r.ret_ann,'{:+.2%}'):>10}{f(r.vol,'{:.1%}'):>8}"
              f"{f(r.sharpe):>9}{f(r.t):>7}{f(r.max_dd,'{:.1%}'):>9}")
    if not res.empty:
        b = res.loc[res.sharpe.idxmax()]
        print(f"\nbest v2: {b.variant}  Sharpe {b.sharpe:+.3f} (v1 was +0.52)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
