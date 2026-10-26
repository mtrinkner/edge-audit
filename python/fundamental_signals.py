"""Point-in-time fundamental signals from as-filed SEC data.

THE CONSTRUCTION RULE. For a decision on date t, a concept's value is the most
recent figure whose `filed` date is on or before t. Where a company restated,
that resolves to the number that was public at t, not the correction published
later. Microsoft refiled one quarter's net income five times over two years; a
naive join would have put the 2017 figure into a 2015 decision.

TRAILING TWELVE MONTHS, AS KNOWN. Flow concepts (income, cash flow, gross profit)
are summed over the four most recent quarters whose filings existed at t. Stock
concepts (assets, equity, shares) take the single latest filed value. A 10-K
covers a full year, so annual filings are used directly rather than added to
quarters, which would double count.

SIX SIGNALS, ORIENTATION FIXED FROM THE LITERATURE BEFORE LOOKING AT RESULTS:

  earnings_yield       TTM earnings / market cap          + (Basu 1977)
  book_to_price        equity / market cap                + (Fama & French 1992)
  roe                  TTM earnings / equity              + (quality)
  accruals             (TTM earnings - TTM cash flow)/assets  - (Sloan 1996)
  asset_growth         year-over-year change in assets    - (Cooper/Gulen/Schill 2008)
  gross_profitability  TTM gross profit / assets          + (Novy-Marx 2013)

The two negatives matter: high accruals and fast asset growth both PREDICT WEAK
returns, so the score is negated. Choosing those signs from this sample instead
of from the papers would be fitting the answer.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

FLOW = {"NetIncomeLoss", "NetCashProvidedByUsedInOperatingActivities", "GrossProfit"}
STOCK = {"Assets", "StockholdersEquity", "CommonStockSharesOutstanding",
         "EntityCommonStockSharesOutstanding",
         "WeightedAverageNumberOfDilutedSharesOutstanding"}

SIGNALS = ["earnings_yield", "book_to_price", "roe", "accruals",
           "asset_growth", "gross_profitability"]
# Orientation taken from the literature, not from this sample.
ORIENTATION = {"earnings_yield": +1, "book_to_price": +1, "roe": +1,
               "accruals": -1, "asset_growth": -1, "gross_profitability": +1}


def latest_as_of(facts: pd.DataFrame, concept: str) -> pd.DataFrame:
    """One row per (symbol, filed) carrying the TTM or latest value known then.

    Walks each symbol's filings in order, maintaining the best-known value for
    every period_end, and emits a snapshot after each filing date. That snapshot
    is by construction what a researcher could have computed on that date.
    """
    f = facts[facts["concept"] == concept]
    if f.empty:
        return pd.DataFrame(columns=["symbol", "filed", "value"])
    is_flow = concept in FLOW
    out = []
    for sym, g in f.sort_values(["symbol", "filed", "period_end"]).groupby("symbol", sort=False):
        known: dict[str, tuple[float, float]] = {}   # period_end -> (val, days)
        for filed, chunk in g.groupby("filed", sort=True):
            for r in chunk.itertuples():
                days = np.nan
                if r.period_start:
                    days = (pd.Timestamp(r.period_end) - pd.Timestamp(r.period_start)).days
                known[r.period_end] = (r.val, days)
            if not known:
                continue
            ends = sorted(known)
            if is_flow:
                annual = [e for e in ends if 330 <= (known[e][1] or 0) <= 400]
                quarters = [e for e in ends if 60 <= (known[e][1] or 0) <= 120]
                if annual and (not quarters or annual[-1] >= quarters[-1]):
                    val = known[annual[-1]][0]
                elif len(quarters) >= 4:
                    val = sum(known[e][0] for e in quarters[-4:])
                else:
                    continue
            else:
                val = known[ends[-1]][0]
            out.append((sym, filed, val))
    return pd.DataFrame(out, columns=["symbol", "filed", "value"])


def build_signals(facts: pd.DataFrame) -> pd.DataFrame:
    """Wide frame of (symbol, filed) -> concept values known at that filing."""
    frames = []
    for c in sorted(set(facts["concept"])):
        d = latest_as_of(facts, c).rename(columns={"value": c})
        if not d.empty:
            frames.append(d.set_index(["symbol", "filed"]))
    if not frames:
        return pd.DataFrame()
    wide = pd.concat(frames, axis=1).reset_index().sort_values(["symbol", "filed"])
    # Forward fill within each symbol: a concept keeps its latest known value
    # until a later filing updates it. Done per symbol so one company's numbers
    # can never bleed into another's.
    value_cols = [c for c in wide.columns if c not in ("symbol", "filed")]
    wide[value_cols] = wide.groupby("symbol")[value_cols].ffill()
    return wide


def compute(wide: pd.DataFrame, price_panel: pd.DataFrame) -> pd.DataFrame:
    """Attach fundamentals to the daily panel as of each date, then form ratios."""
    w = wide.copy()
    w["_f"] = pd.to_datetime(w["filed"])
    p = price_panel.copy()
    p["_d"] = pd.to_datetime(p["dt"])
    merged = pd.merge_asof(
        p.sort_values("_d"), w.sort_values("_f"),
        left_on="_d", right_on="_f", by="symbol",
        direction="backward", allow_exact_matches=True,
    )

    shares = merged.get("CommonStockSharesOutstanding")
    alt = merged.get("EntityCommonStockSharesOutstanding")
    if shares is None:
        shares = alt
    elif alt is not None:
        shares = shares.fillna(alt)
    dil = merged.get("WeightedAverageNumberOfDilutedSharesOutstanding")
    if dil is not None:
        shares = shares.fillna(dil) if shares is not None else dil

    mcap = shares * merged["close"] if shares is not None else np.nan
    mcap = pd.Series(mcap, index=merged.index).replace([0, np.inf, -np.inf], np.nan)

    ni = merged.get("NetIncomeLoss")
    eq = merged.get("StockholdersEquity")
    at = merged.get("Assets")
    ocf = merged.get("NetCashProvidedByUsedInOperatingActivities")
    gp = merged.get("GrossProfit")

    safe = lambda x: x.replace([0, np.inf, -np.inf], np.nan) if x is not None else None
    merged["earnings_yield"] = ni / mcap if ni is not None else np.nan
    merged["book_to_price"] = eq / mcap if eq is not None else np.nan
    merged["roe"] = ni / safe(eq) if (ni is not None and eq is not None) else np.nan
    merged["accruals"] = ((ni - ocf) / safe(at)
                          if all(x is not None for x in (ni, ocf, at)) else np.nan)
    merged["gross_profitability"] = (gp / safe(at)
                                     if (gp is not None and at is not None) else np.nan)
    # Asset growth needs the value a year ago, taken from this same as-of series
    # so it too is point-in-time.
    merged = merged.sort_values(["symbol", "dt"])
    if at is not None:
        prev = merged.groupby("symbol")["Assets"].shift(252)
        merged["asset_growth"] = merged["Assets"] / prev.replace(0, np.nan) - 1.0
    else:
        merged["asset_growth"] = np.nan

    for s, sign in ORIENTATION.items():
        if s in merged.columns:
            merged[s] = sign * merged[s]
    return merged
