#!/usr/bin/env python3
"""Execution cost model with separated components and four scenarios.

    python3 python/cost_model.py
    python3 python/cost_model.py --gross-bps 12 --holding-days 5 --liquidity mid

WHY THIS REPLACES A SINGLE NUMBER

Until now every result in this project was netted against one quantity called
"cost". That is four or five different things added together, and they behave
differently: a spread is paid per round trip, impact scales with size, borrow
accrues with time, and fees are fixed. Collapsing them hid the fact that the one
driving every conclusion was an ESTIMATE, never an observation.

THE COMPONENTS, kept separate because they are not interchangeable

  effective_spread  what you actually pay to cross. MEASURED here with EDGE
                    (Ardia/Guidotti/Kroencke, JFE 2024) from this project's own
                    bars. An estimate, not an observed quote.
  quoted_spread     what the book shows. NOT AVAILABLE. No quote data exists in
                    this project, and the distinction matters because effective
                    is usually narrower than quoted for marketable orders.
  realized_spread   effective spread minus post-trade price move, i.e. what the
                    liquidity provider keeps. NOT AVAILABLE for the same reason.
  impact            the price moves against you as you trade. Square-root law,
                    impact_bps = Y * vol * sqrt(size/ADV), from Almgren et al.
                    This is an ASSUMPTION from the literature, not a measurement:
                    no execution data exists here to fit Y.
  fees              explicit commission. A parameter, not a model.
  borrow            cost of holding a short, accrues with calendar time.
  adverse_selection for passive orders only. MEASURED in trial 30: a resting bid
                    fills 86.9% of the time and the fills underperform the
                    non-fills by 0.65%, because a bid fills when price keeps
                    falling.

THE FOUR SCENARIOS, grounded in measurement where measurement was possible

Spread multipliers come from the EDGE panel conditioned on VIX, not from taste:

    VIX quintile 0 (12.7-14.3)   5.03bp half-spread   1.00x
    VIX quintile 4 (20.0-31.9)   6.64bp               1.32x
    worst 2% of months           8.73bp               1.73x
    95th pct of all sym-months  13.26bp               2.31x of median

  A OPTIMISTIC  0.65x the measured spread. Justified ONLY by EDGE's known upward
                bias at small spreads: it reports 3.35bp for stocks trading
                $627M/day where the true half-spread is nearer 0.3-0.5bp. This is
                a correction for a known estimator bias, and it is the one
                scenario that could be too kind.
  B BASE        1.00x measured. The default for any reported result.
  C ADVERSE     1.32x, the measured calm-to-stressed quintile ratio.
  D STRESS      1.73x, the measured worst-2%-of-months level, plus impact at
                three times the base participation rate, because stressed markets
                are thinner as well as wider.

A result that survives only under scenario A is FRAGILE and is labelled so.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "python"))

import data_access as da
import config

SPREADS = ROOT / "data" / "edge_spreads.parquet"
SESSIONS_PER_YEAR = 252

# Measured from the EDGE panel conditioned on prior-session VIX. See docstring.
SCENARIOS = {
    "A optimistic": dict(spread_mult=0.65, impact_mult=1.0,
                         note="corrects EDGE's known upward bias at tiny spreads"),
    "B base":       dict(spread_mult=1.00, impact_mult=1.0,
                         note="measured spread, as reported"),
    "C adverse":    dict(spread_mult=1.32, impact_mult=1.5,
                         note="measured calm-to-stressed VIX quintile ratio"),
    "D stress":     dict(spread_mult=1.73, impact_mult=3.0,
                         note="measured worst 2% of months; thinner as well as wider"),
}

# Almgren square-root law coefficient. ASSUMPTION from the literature; this
# project has no execution data to fit it, and that is stated rather than hidden.
IMPACT_Y = 1.0


@dataclass
class Trade:
    gross_bps: float          # expected gross return per round trip
    holding_days: float
    liquidity: str            # micro | small | mid | large | mega
    size_usd: float = 10_000
    daily_vol: float = 0.02   # stock's daily return vol, for the impact term
    fee_bps_per_side: float = 0.5
    short: bool = False


def spread_table() -> pd.Series:
    """Median round-trip spread per liquidity bucket, bucketed by DOLLAR VOLUME.

    Two bugs fixed here, both recorded. The buckets were first ranked descending
    while still labelled "mega" first, so the widest spreads were called the most
    liquid and the table reported a 22.42bp round trip for mega-caps against a
    measured 7.4bp. Then, with the direction fixed, bucketing by SPREAD still
    disagreed with cost_first_screen.py in the tails, because that module buckets
    by dollar volume. Liquidity means volume, so this does the same and the two
    modules now agree.
    """
    # Through data_access, which stops at the lockbox seal (added 2026-10-26).
    s = da.parquet("cost_model", "edge_spreads").dropna(subset=["edge_full"])
    b = da.parquet("cost_model", "intraday_1h")
    b = b[b["bars_in_session"] == 7].copy()
    b["month"] = b["session"].str[:7]
    g = b.groupby(["symbol", "session"])
    dol = (g["volume"].sum() * g["close"].last()).reset_index(name="dollar")
    dol["month"] = dol["session"].str[:7]
    md = dol.groupby(["symbol", "month"], as_index=False)["dollar"].median()
    j = s.merge(md, on=["symbol", "month"], how="inner")
    j["q"] = pd.qcut(j["dollar"].rank(method="first"), 5,
                     labels=["micro", "small", "mid", "large", "mega"])
    return j.groupby("q", observed=True)["edge_full"].median()


def adv_for(liquidity: str) -> float:
    """Representative daily dollar volume, from the measured panel buckets."""
    return {"micro": 8e6, "small": 23e6, "mid": 52e6,
            "large": 119e6, "mega": 363e6}[liquidity]


def decompose(t: Trade, scen: str, spreads: pd.Series) -> dict:
    s = SCENARIOS[scen]
    eff = float(spreads.get(t.liquidity, spreads.median())) * s["spread_mult"]
    eff_bps = eff * 1e4                                    # round trip
    adv = adv_for(t.liquidity)
    participation = t.size_usd / max(adv, 1.0)
    # Square-root impact is paid on the way in AND on the way out (fixed
    # 2026-10-26; it was charged once per round trip).
    impact_bps = (2 * IMPACT_Y * t.daily_vol * np.sqrt(participation)
                  * 1e4 * s["impact_mult"])
    fees_bps = 2 * t.fee_bps_per_side
    borrow_bps = (config.COSTS.borrow_bps_annual * t.holding_days / 252.0
                  if t.short else 0.0)
    total = eff_bps + impact_bps + fees_bps + borrow_bps
    return {"scenario": scen, "effective_spread": eff_bps, "impact": impact_bps,
            "fees": fees_bps, "borrow": borrow_bps, "total_cost": total,
            "net_bps": t.gross_bps - total,
            "net_annual_pct": (t.gross_bps - total) / 1e4
                              * (SESSIONS_PER_YEAR / max(t.holding_days, 1e-9)) * 100}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--gross-bps", type=float, default=12.0)
    ap.add_argument("--holding-days", type=float, default=5.0)
    ap.add_argument("--liquidity", default="mid",
                    choices=["micro", "small", "mid", "large", "mega"])
    ap.add_argument("--size-usd", type=float, default=10_000)
    ap.add_argument("--short", action="store_true")
    args = ap.parse_args()

    spreads = spread_table()
    t = Trade(gross_bps=args.gross_bps, holding_days=args.holding_days,
              liquidity=args.liquidity, size_usd=args.size_usd, short=args.short)

    print(f"CANDIDATE: {t.gross_bps:.1f}bp gross, {t.holding_days:.0f}-day hold, "
          f"{t.liquidity} liquidity, ${t.size_usd:,.0f} per position"
          f"{', SHORT' if t.short else ''}\n")
    print("=" * 96)
    print(f"{'scenario':<16}{'spread':>9}{'impact':>9}{'fees':>8}{'borrow':>9}"
          f"{'TOTAL':>9}{'NET':>9}{'net/yr':>10}{'verdict':>12}")
    print("-" * 96)
    rows = []
    for name in SCENARIOS:
        d = decompose(t, name, spreads)
        v = "profitable" if d["net_bps"] > 0 else "loses"
        print(f"{name:<16}{d['effective_spread']:>8.2f}b{d['impact']:>8.2f}b"
              f"{d['fees']:>7.1f}b{d['borrow']:>8.2f}b{d['total_cost']:>8.2f}b"
              f"{d['net_bps']:>+8.2f}b{d['net_annual_pct']:>+9.1f}%{v:>12}")
        rows.append(d)
    r = pd.DataFrame(rows)

    base = r[r.scenario == "B base"].iloc[0]
    print(f"\nBREAK-EVEN: this candidate needs {base['total_cost']:.2f}bp gross "
          f"under the base scenario; it has {t.gross_bps:.1f}bp")
    margin = t.gross_bps / base["total_cost"] if base["total_cost"] > 0 else np.inf
    print(f"  cost coverage ratio: {margin:.2f}x "
          f"({'above' if margin > 1 else 'BELOW'} break-even)")

    npos = int((r["net_bps"] > 0).sum())
    if npos == 0:
        cls = "DEAD — loses under every scenario including the optimistic one"
    elif npos == 1:
        cls = "FRAGILE — survives only the optimistic scenario"
    elif npos < len(SCENARIOS):
        cls = "CONDITIONAL — survives base but not stress"
    else:
        cls = "ROBUST — survives all four, including measured stress"
    print(f"\nCLASSIFICATION: {cls}")
    print(f"\nNOT MODELLED, because the data does not exist here: quoted spread,")
    print(f"realized spread, queue position, fill probability for passive orders.")
    print(f"Any passive-execution claim is UNVERIFIED, not profitable.")
    (ROOT / "reports").mkdir(exist_ok=True)
    r.to_csv(ROOT / "reports" / "cost_scenarios.csv", index=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
