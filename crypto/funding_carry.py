#!/usr/bin/env python3
"""Strategy 20, rebuilt: what a delta-neutral funding carry actually pays.

    python3 crypto/funding_carry.py

The first pass at this only summed funding prints. That number came out at a
median 10.5% a year with a daily Sharpe of 7, and no script behind it was ever
committed. This is that script, plus the four things the funding series alone
cannot tell you:

  1. PERIOD LENGTH. Hyperliquid settled every 8 hours until 2023-06-08 and every
     hour after. Annualizing mean(rate) * 24 * 365 treats an 8-hour payment as an
     hourly one. Here funding is summed as cash and divided by elapsed time.

  2. WHERE THE CARRY COMES FROM. Hyperliquid funding is
         F = premium + clamp(interest - premium, -0.05%, +0.05%)   per 8h
     with interest fixed at 0.01% per 8h, i.e. 1.25e-5 per hour, about 11% a
     year. Whenever the perp trades within the band of the index, F is exactly
     the interest constant. That is a parameter the exchange chose, not a price
     of leverage the market set. It is still cash, but it says nothing about
     demand, and an exchange can change it.

  3. BASIS. Short perp, long spot is delta-neutral only in the limit. Every day
     the perp-spot spread marks to market. OKX daily perp and spot closes on the
     same coins measure it.

  4. CAPITAL. The spot leg is fully funded and the perp leg needs margin, so a
     dollar of carry notional ties up more than a dollar. That capital could
     have sat in T-bills. The hurdle is cash, not zero.

Venue risk, the one that actually ends these trades, is still absent. Nothing
here can price it.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
sys.path.insert(0, str(HERE.parent / "python"))

from deflated_sharpe import deflated_sharpe_ratio, expected_max_sharpe  # noqa: E402
import registry  # noqa: E402

INTEREST_PER_HOUR = 0.0001 / 8      # Hyperliquid's fixed interest leg
COST_PER_LEG = 0.0008               # taker fee plus half-spread, each of 4 legs
PERP_MARGIN = 1 / 3                 # 3x on the short perp
LIQ_RALLY = 0.30                    # a rally this size wipes a 3x short's margin
DAY_OFFSET = pd.Timedelta(hours=16)  # OKX daily candles open at 16:00 UTC


def load_funding() -> pd.DataFrame:
    f = pd.read_parquet(DATA / "hl_funding.parquet").sort_values(["coin", "ts"])
    gap = f.groupby("coin")["ts"].diff().dt.total_seconds().div(3600).round()
    # The first print of each coin has no gap; it takes the next print's.
    f["hours"] = gap.fillna(gap.groupby(f["coin"]).bfill()).fillna(1).clip(upper=8)
    f["interest"] = INTEREST_PER_HOUR * f["hours"]
    f["at_floor"] = np.isclose(f["rate"], f["interest"], atol=1e-9)
    f["market"] = f["rate"] - f["interest"]
    f["day"] = (f["ts"] - DAY_OFFSET).dt.floor("D") + DAY_OFFSET
    return f


def load_basis() -> pd.DataFrame:
    b = pd.read_parquet(DATA / "okx_basis.parquet").sort_values(["coin", "ts"])
    g = b.groupby("coin")
    # Long spot, short perp, per dollar of notional.
    b["basis_pnl"] = g["close_spot"].pct_change() - g["close_perp"].pct_change()
    b["basis"] = b["close_perp"] / b["close_spot"] - 1
    b["rally3"] = g["close_spot"].transform(lambda s: s.shift(-3) / s - 1)
    return b.rename(columns={"ts": "day"})


def annualized_by_coin(f: pd.DataFrame) -> pd.DataFrame:
    years = f.groupby("coin")["ts"].agg(lambda t: (t.max() - t.min()).days / 365.25)
    naive = f.groupby("coin")["rate"].mean() * 24 * 365
    return pd.DataFrame({
        "years": years,
        "naive": naive,
        "funding": f.groupby("coin")["rate"].sum() / years,
        "interest": f.groupby("coin")["interest"].sum() / years,
        "market": f.groupby("coin")["market"].sum() / years,
        "at_floor": f.groupby("coin")["at_floor"].mean(),
    })


def sharpe(x: pd.Series, per_year: float) -> float:
    return float(x.mean() / x.std() * np.sqrt(per_year)) if x.std() > 0 else float("nan")


def main() -> int:
    f = load_funding()
    b = load_basis()
    tb = pd.read_parquet(DATA / "tbill.parquet").set_index("date")["yld"]

    print(f"funding: {len(f):,} prints, {f.coin.nunique()} coins, "
          f"{f.ts.min().date()} to {f.ts.max().date()}")
    print(f"  8-hour prints annualized as if hourly by the first pass: "
          f"{(f.hours == 8).sum()}")
    print(f"  prints exactly at the interest constant: {f.at_floor.mean():.0%}")

    c = annualized_by_coin(f)
    print("\nper coin, annualized")
    print(c.sort_values("funding", ascending=False).to_string(
        formatters={k: (lambda v: f"{v:6.2f}") if k == "years" else (lambda v: f"{v:8.1%}")
                    for k in c.columns}))
    print(f"\n  median funding        {c.funding.median():6.1%}   (first pass: 10.5%)")
    print(f"  median interest leg   {c.interest.median():6.1%}")
    print(f"  median market leg     {c.market.median():6.1%}")
    print(f"  coins positive        {(c.funding > 0).mean():6.0%}")

    # Daily coin panel: funding cash joined to the basis marked on OKX.
    fd = f.groupby(["coin", "day"])[["rate", "interest", "market"]].sum().reset_index()
    p = fd.merge(b[["coin", "day", "basis_pnl", "basis", "rally3"]],
                 on=["coin", "day"], how="inner").dropna(subset=["basis_pnl"])
    # Entry and exit, four legs each way, charged once per coin.
    rt = 2 * 2 * COST_PER_LEG
    first = p.groupby("coin")["day"].transform("min") == p["day"]
    last = p.groupby("coin")["day"].transform("max") == p["day"]
    p["cost"] = np.where(first | last, rt / 2, 0.0)
    p["net"] = p["rate"] + p["basis_pnl"] - p["cost"]

    book = p.groupby("day")[["rate", "interest", "market", "basis_pnl", "cost", "net"]].mean()
    book["rf"] = tb.reindex(book.index.tz_localize(None).normalize(), method="ffill").values / 365
    book["on_capital"] = book["net"] / (1 + PERP_MARGIN)
    book["excess"] = book["on_capital"] - book["rf"]

    print(f"\nequal-weight book, {len(book):,} days, {p.coin.nunique()} coins, "
          f"{book.index.min().date()} to {book.index.max().date()}")
    rows = [
        ("funding only (first pass)", book["rate"]),
        ("  + basis marked daily", book["rate"] + book["basis_pnl"]),
        ("  + costs", book["net"]),
        ("  per dollar of capital", book["on_capital"]),
        ("  minus T-bills", book["excess"]),
    ]
    print(f"  {'':<28}{'ann.':>8}{'vol':>8}{'daily SR':>10}")
    for name, s in rows:
        print(f"  {name:<28}{s.mean() * 365:8.1%}{s.std() * np.sqrt(365):8.1%}"
              f"{sharpe(s, 365):10.2f}")

    yr = book.groupby(book.index.year)
    by_year = pd.DataFrame({
        "funding": yr["rate"].mean() * 365,
        "interest": yr["interest"].mean() * 365,
        "market": yr["market"].mean() * 365,
        "basis": yr["basis_pnl"].mean() * 365,
        "on_capital": yr["on_capital"].mean() * 365,
        "tbill": yr["rf"].mean() * 365,
        "excess": yr["excess"].mean() * 365,
    })
    print("\nby year, annualized")
    print(by_year.to_string(float_format=lambda v: f"{v:8.1%}"))

    m = book["excess"].groupby(book.index.strftime("%Y-%m")).sum()
    sr_m = sharpe(m, 12)
    n = registry.trials()["n_trials"]
    print(f"\nmonthly, in excess of T-bills: {len(m)} months, {(m > 0).mean():.0%} positive")
    print(f"  sharpe {sr_m:.2f}   luck bar at {n} trials {expected_max_sharpe(n, len(m)):.2f}"
          f"   deflated p {deflated_sharpe_ratio(sr_m, n, len(m)):.3f}")

    print("\nthe risk the funding series does not show")
    print(f"  worst single coin-day basis loss   {p.basis_pnl.min():7.2%}")
    print(f"  worst book day                     {book['net'].min():7.2%}")
    print(f"  coin-days with |basis| > 1%        {(p.basis.abs() > 0.01).mean():7.2%}")
    liq = p[p.rally3 > LIQ_RALLY]
    coin_years = p.groupby("coin")["day"].agg(lambda d: (d.max() - d.min()).days / 365.25).sum()
    print(f"  3-day rallies over {LIQ_RALLY:.0%} (liquidates a 3x short perp left unrebalanced)")
    print(f"    {liq.coin.nunique()} of {p.coin.nunique()} coins, "
          f"{len(liq)} coin-days, {len(liq) / coin_years:.1f} per coin-year")

    out = HERE / "reports"
    out.mkdir(exist_ok=True)
    c.to_csv(out / "carry_by_coin.csv")
    by_year.to_csv(out / "carry_by_year.csv")
    return 0


if __name__ == "__main__":
    sys.exit(main())
