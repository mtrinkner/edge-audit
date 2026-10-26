#!/usr/bin/env python3
"""Trial 58: odd-lot tender offer arbitrage, scored as declared.

    python3 python/odd_lot_study.py              # DESIGN: offers filed 2010-2018
    python3 python/odd_lot_study.py --confirm    # CONFIRMATION 2019-2025, once, frozen code

For every common-stock issuer tender offer (SC TO-I) with an odd-lot priority
clause and CRSP prices, buy 99 shares and tender them. Odd lots are bought in
full, so the profit is (final purchase price - entry close) x 99 - fee. A
terminated offer is sold at the close of the first trading day after the
termination announcement (no fee).

ENTRY RULES (the two declared variants)
  E1  close of the first trading day after the SC TO-I filing.
  E2  close of the last feasible day: the expiration in force that day minus the
      settlement cycle (T+3 before 2017-09-05, T+2 until 2024-05-28, T+1 after)
      minus 2 business days of broker cutoff. "In force" means the original
      expiration, moved only by extensions filed before the entry day; an
      extension announced later leaves the position already open. An offer
      terminated on or before the E2 day is never entered.

Business days are the stock's CRSP trading days. A close is |dlyprc| (CRSP
reports the bid/ask midpoint, negated, on days without a trade).

INPUTS: data/edgar/deals.csv and deal_links.csv (python/edgar_tenders.py), the
CRSP daily rows in data/edgar/crsp_daily.parquet, and the hand checks in
data/odd_lot_overrides.csv. Offers priced in Canadian dollars are converted at
the FRED DEXCAUS rate on the final expiration.

OUTPUTS: per-deal rows to data/edgar/odd_lot_<period>_deals.csv (CRSP-derived,
git-ignored) and aggregates only to reports/odd_lot_<period>.csv.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "python"))

import data_access as da
from edgar_tenders import CACHE, fetch, in_scope
from evaluation import clustered_mean

SHARES = 99
FEES = (0.0, 20.0, 40.0)                 # $40 is primary; the others are sensitivity checks
PRIMARY_FEE = 40.0
CUTOFF_DAYS = 2                          # broker cutoff, business days
PERIODS = {"design": ("2010-01-01", "2018-12-31"), "confirm": ("2019-01-01", "2025-12-31")}


def settlement_days(trade_date: str) -> int:
    return 3 if trade_date < "2017-09-05" else 2 if trade_date < "2024-05-28" else 1


def deals(period: str) -> pd.DataFrame:
    """Priced common-stock offers with odd-lot priority filed in the period, hand checks applied."""
    lo, hi = PERIODS[period]
    d = in_scope(pd.read_csv(CACHE / "deals.csv", dtype={"cik": str}))
    d = d.merge(pd.read_csv(CACHE / "deal_links.csv"), on="path", suffixes=("_edgar", ""))
    d = d[d["crsp_priced"] & d["odd_lot_priority"].astype(bool) & ~d["odd_lot_only"].astype(bool)
          & d["date"].between(lo, hi)].copy()
    d["currency"] = "USD"
    ov = pd.read_csv(ROOT / "data" / "odd_lot_overrides.csv", dtype=str)
    for _, o in ov.iterrows():
        i = d.index[d["path"] == o["path"]]
        for col in ("outcome", "final_price", "final_expiration", "terminated_on", "currency"):
            if len(i) and isinstance(o[col], str) and o[col]:
                d.loc[i, col] = float(o[col]) if col == "final_price" else o[col]
        if len(i) and o["outcome"] == "completed":
            d.loc[i, "terminated_on"] = np.nan
    return d.reset_index(drop=True)


def cad_per_usd() -> pd.Series:
    """FRED DEXCAUS (Canadian dollars per US dollar), cached."""
    path = CACHE / "fx_dexcaus.csv"
    if not path.exists():
        path.write_bytes(fetch("https://fred.stlouisfed.org/graph/fredgraph.csv?id=DEXCAUS"))
    fx = pd.read_csv(path)
    fx.columns = ["date", "rate"]
    fx["rate"] = pd.to_numeric(fx["rate"], errors="coerce")
    return fx.dropna().set_index("date")["rate"]


def trade(r, px: pd.DataFrame, variant: str) -> dict | None:
    """One deal under one entry rule. None means the rule never enters it."""
    days = px["d"].tolist()
    close = px["close"].to_numpy()
    after = [i for i, x in enumerate(days) if x > r.date]
    if not after:
        return None
    e1 = after[0]
    if variant == "E1":
        i = e1
    else:
        if not isinstance(r.expiration, str):
            return None                            # no expiration on the cover: no E2 day
        timeline = [x.split(">") for x in str(r.ext_timeline).split(";") if ">" in x]
        exp = r.expiration
        for _ in range(20):
            k = max(j for j, x in enumerate(days) if x <= exp) if days[0] <= exp else 0
            i = max(k - settlement_days(days[k]) - CUTOFF_DAYS, e1)
            i = max(k - settlement_days(days[i]) - CUTOFF_DAYS, e1)   # the cycle depends on the trade date
            known = max([r.expiration] + [ne for fd, ne in timeline if fd < days[i]])
            if known <= exp:
                break
            exp = known
        if isinstance(r.terminated_on, str) and r.terminated_on <= days[i]:
            return None                            # terminated before the E2 day: never entered
    while i < len(days) and not np.isfinite(close[i]):
        i += 1                                     # no valid close: the next day that has one
    if i >= len(days):
        return None
    entry_day, entry = days[i], close[i]
    out = {"variant": variant, "entry_day": entry_day, "entry": entry}
    if r.outcome == "terminated":
        j = next((j for j, x in enumerate(days) if x > r.terminated_on and np.isfinite(close[j])), None)
        if j is None or j < i:
            return None
        out.update(exit=close[j], exit_day=days[j], payment_day=days[j], fee_applies=False)
    elif r.outcome == "completed" and np.isfinite(r.final_usd):
        k = max(j for j, x in enumerate(days) if x <= r.final_expiration)
        pay = r.payment_date if isinstance(r.payment_date, str) else days[min(k + settlement_days(days[k]), len(days) - 1)]
        out.update(exit=r.final_usd, exit_day=r.final_expiration, payment_day=pay, fee_applies=True)
        # A split between entry and the tender changes the share count. CRSP's cumulative factor
        # falls across a 2-for-1 split (2 -> 1), so the entry in tender-day units is entry x f1 / f0,
        # and the trade is scored as 99 tender-day shares.
        f0 = px["cumfac"].iloc[i]
        f1 = px["cumfac"].iloc[k]
        if np.isfinite(f0) and np.isfinite(f1) and f0 > 0 and abs(f1 / f0 - 1) > 1e-9:
            out["entry"] = entry * f1 / f0
            out["split_adjusted"] = True
    else:
        return None
    out["gross"] = (out["exit"] - out["entry"]) * SHARES
    out["capital"] = out["entry"] * SHARES
    out["days_locked"] = (pd.Timestamp(out["payment_day"]) - pd.Timestamp(entry_day)).days
    pre = px.iloc[max(0, e1 - 21):e1]
    out["dollar_volume_20d"] = float((pre["close"] * pre["vol"]).median()) if len(pre) else np.nan
    return out


def run(period: str) -> int:
    md = da.guard("odd_lot_study")
    d = deals(period)
    if (d["currency"] == "CAD").any():
        fx = cad_per_usd()
        rate = d["final_expiration"].map(lambda x: fx[fx.index <= x].iloc[-1] if isinstance(x, str) else np.nan)
        d["final_usd"] = np.where(d["currency"] == "CAD", d["final_price"] / rate, d["final_price"])
    else:
        d["final_usd"] = d["final_price"]
    crsp = pd.read_parquet(CACHE / "crsp_daily.parquet")
    crsp["d"] = crsp["dlycaldt"].dt.strftime("%Y-%m-%d")
    crsp = crsp[crsp["d"] <= md]
    crsp["close"] = crsp["dlyprc"].abs().where(crsp["dlyprc"].abs() > 0)
    crsp = crsp.rename(columns={"dlyvol": "vol", "dlycumfacpr": "cumfac"})
    by = {p: g.sort_values("d") for p, g in crsp.groupby("permno")}
    rows = []
    for r in d.itertuples():
        px = by.get(int(r.permno))
        if px is None:
            continue
        for v in ("E1", "E2"):
            t = trade(r, px.reset_index(drop=True), v)
            if t:
                below = r.price_lo if r.offer_type == "dutch" else r.price   # the low end, or the fixed price
                rows.append({"path": r.path, "company": r.company, "filed": r.date, "year": int(r.date[:4]),
                             "offer_type": r.offer_type, "outcome": r.outcome, "currency": r.currency,
                             "final_usd": r.final_usd,
                             "below_low_end": (t["entry"] < below) if below == below else np.nan, **t})
    t = pd.DataFrame(rows)
    t["entry_month"] = t["entry_day"].str[:7]
    for fee in FEES:
        t[f"profit_{int(fee)}"] = t["gross"] - np.where(t["fee_applies"], fee, 0.0)
    t.to_csv(CACHE / f"odd_lot_{period}_deals.csv", index=False)

    years = int(PERIODS[period][1][:4]) - int(PERIODS[period][0][:4]) + 1
    out = []
    for v, g in t.groupby("variant"):
        for fee in FEES:
            col = f"profit_{int(fee)}"
            cm = clustered_mean(g[col], g["entry_month"])
            term = g[g["outcome"] == "terminated"][col]
            gains = g[g[col] > 0][col].sum()
            ry = (g[col].sum() / (g["capital"] * g["days_locked"].clip(lower=1) / 365).sum())
            top = g.sort_values(col, ascending=False)
            share = top[col].clip(lower=0).cumsum() / max(top[col].clip(lower=0).sum(), 1e-9)
            carriers = top[share.shift(fill_value=0) < 0.8]
            out.append({"variant": v, "fee": fee, "deals": len(g), "deals_per_year": len(g) / years,
                        "mean": cm["mean"], "median": g[col].median(), "t": cm["t"], "p_one": cm["p_one"],
                        "clusters": cm["clusters"], "p10": g[col].quantile(.1), "p90": g[col].quantile(.9),
                        "share_positive": (g[col] > 0).mean(), "terminated": (g["outcome"] == "terminated").sum(),
                        "terminated_loss": term[term < 0].sum(), "gains": gains,
                        "dollars_per_year": g[col].sum() / years, "mean_capital": g["capital"].mean(),
                        "median_days_locked": g["days_locked"].median(), "return_per_capital_year": ry,
                        "share_below_low_end": g["below_low_end"].mean(),
                        "carriers_80pct": len(carriers),
                        "carriers_median_dollar_volume": carriers["dollar_volume_20d"].median(),
                        "all_median_dollar_volume": g["dollar_volume_20d"].median(),
                        "top5_share": cm["top5_share"], "trimmed_mean": cm["trimmed_mean"], "alarm": cm["alarm"]})
    s = pd.DataFrame(out)
    s.to_csv(ROOT / "reports" / f"odd_lot_{period}.csv", index=False)
    pd.set_option("display.width", 220)
    print(f"{period.upper()} {PERIODS[period]}: {d.shape[0]} offers with odd-lot priority and CRSP prices")
    print(s[["variant", "fee", "deals", "mean", "median", "t", "p_one", "clusters", "share_positive",
             "terminated", "terminated_loss", "gains", "dollars_per_year", "return_per_capital_year",
             "median_days_locked", "share_below_low_end"]].round(3).to_string(index=False))
    for v, g in t.groupby("variant"):
        print(f"\n{v}: worst five at ${PRIMARY_FEE:.0f}")
        print(g.nsmallest(5, "profit_40")[["company", "filed", "outcome", "entry_day", "entry", "final_usd",
                                           "exit", "profit_40"]].round(2).to_string(index=False))
    print("\ndeals per year (E1):", t[t["variant"] == "E1"].groupby("year").size().to_dict())
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--confirm", action="store_true", help="run the confirmation period, once")
    sys.exit(run("confirm" if ap.parse_args().confirm else "design"))
