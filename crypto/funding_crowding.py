#!/usr/bin/env python3
"""Strategy 22: does high funding predict lower returns?

    python3 crypto/funding_crowding.py

Funding is what levered longs pay to stay levered. When it is high the long side
is crowded, paying heavily, and fragile to an unwind, so the crowding story says
high-funding coins should underperform low-funding ones over the next week.

That is a forecast of price, which is different from strategies 20 and 21. They
collected funding as a cash flow. A book that shorts high-funding perps collects
funding too, so if the two were not separated the carry already measured would
show up again here dressed as a prediction. Every number is split:

    PRICE    the perp's price move, which is the claim under test
    FUNDING  the funding the book pays or receives, which is strategy 20 again

Four variants, declared before running: lookback 7 or 30 days, and either a
dollar-neutral cross-sectional book or a time-series book where each coin goes
short itself when its funding is above its own trailing median. The primary,
named in advance, is cross-sectional with a 7-day lookback.
"""

from __future__ import annotations

import sys

import numpy as np
import pandas as pd

from funding_carry import COST_PER_LEG, HERE, load_basis, load_funding
from deflated_sharpe import deflated_sharpe_ratio, expected_max_sharpe
import registry

MIN_COINS = 4
VARIANTS = [("xs", 7, True), ("xs", 30, False), ("ts", 7, False), ("ts", 30, False)]


def weekly(lookback: int) -> pd.DataFrame:
    """One row per coin-week: the signal known before the week, then what happened."""
    f = load_funding()
    b = load_basis()
    fd = f.groupby(["coin", "day"])["rate"].sum().reset_index()
    d = b[["coin", "day", "close_perp"]].merge(fd, on=["coin", "day"], how="inner")
    d = d.sort_values(["coin", "day"]).reset_index(drop=True)
    g = d.groupby("coin")
    d["ret"] = g["close_perp"].pct_change()
    # Trailing funding through yesterday. Today's is not known at today's open.
    d["signal"] = g["rate"].transform(lambda s: s.shift(1).rolling(lookback).sum())
    d["week"] = d["day"].dt.tz_localize(None).dt.to_period("W")

    w = d.groupby(["coin", "week"]).agg(
        signal=("signal", "first"),             # as of the first day of the week
        ret=("ret", lambda r: (1 + r.fillna(0)).prod() - 1),
        funding=("rate", "sum"),
        days=("ret", "size"),
    ).reset_index()
    w = w[(w["days"] >= 5) & w["signal"].notna()]
    return w


def weights(w: pd.DataFrame, kind: str) -> pd.Series:
    if kind == "xs":
        r = w.groupby("week")["signal"].rank()
        dm = r - r.groupby(w["week"]).transform("mean")
        # Long low funding, short high. Scaled to $1 long and $1 short.
        x = -dm / dm.abs().groupby(w["week"]).transform("sum") * 2
        n = w.groupby("week")["coin"].transform("size")
        return x.where(n >= MIN_COINS, 0.0).fillna(0.0)
    # Time series: each coin against its own history, known only up to last week.
    med = w.groupby("coin")["signal"].transform(lambda s: s.expanding().median().shift(1))
    side = np.sign(med - w["signal"])            # +1 long when funding below its median
    n = w.groupby("week")["coin"].transform("size")
    return (side / n).where(med.notna(), 0.0).fillna(0.0)


def run(kind: str, lookback: int) -> tuple[pd.DataFrame, pd.Series]:
    w = weekly(lookback).sort_values(["coin", "week"]).reset_index(drop=True)
    w["w"] = weights(w, kind)
    prev = w.groupby("coin")["w"].shift(1).fillna(0.0)
    w["price"] = w["w"] * w["ret"]
    w["fund"] = -w["w"] * w["funding"]           # a long pays funding, a short receives it
    w["cost"] = (w["w"] - prev).abs() * COST_PER_LEG
    book = w.groupby("week")[["price", "fund", "cost"]].sum()
    book["net"] = book["price"] + book["fund"] - book["cost"]
    book = book[w.groupby("week")["w"].apply(lambda x: x.abs().sum() > 0)]

    ic = w[w["w"] != 0].groupby("week").apply(
        lambda x: x["signal"].rank().corr(x["ret"].rank()) if len(x) >= MIN_COINS else np.nan,
        include_groups=False).dropna()
    return book, ic


def sr(x: pd.Series) -> float:
    return float(x.mean() / x.std() * np.sqrt(52)) if x.std() > 0 else float("nan")


def main() -> int:
    n = registry.trials()["n_trials"]
    btc = weekly(7).query("coin == 'BTC'").set_index("week")["ret"]
    rows, years = [], {}
    for kind, lb, primary in VARIANTS:
        book, ic = run(kind, lb)
        name = f"{'cross-sectional' if kind == 'xs' else 'time-series'} {lb}d" + \
               ("  PRIMARY" if primary else "")
        rows.append({
            "variant": name,
            "weeks": len(book),
            "price": book["price"].mean() * 52,
            "funding": book["fund"].mean() * 52,
            "cost": -book["cost"].mean() * 52,
            "net": book["net"].mean() * 52,
            "sr_price": sr(book["price"]),
            "sr_net": sr(book["net"]),
            "ic": ic.mean(),
            "ic_t": ic.mean() / ic.std() * np.sqrt(len(ic)),
            "btc_corr": book["price"].corr(btc.reindex(book.index)),
            "bar": expected_max_sharpe(n, len(book)),
            "p_price": deflated_sharpe_ratio(sr(book["price"]), n, len(book)),
        })
        years[name] = book["price"].groupby(book.index.year).mean() * 52

    r = pd.DataFrame(rows).set_index("variant")
    pct = lambda v: f"{v:7.1%}"
    num = lambda v: f"{v:6.2f}"
    fmt = {**{c: pct for c in ["price", "funding", "cost", "net"]},
           **{c: num for c in ["sr_price", "sr_net", "ic", "ic_t", "btc_corr", "bar"]},
           "p_price": lambda v: f"{v:6.3f}", "weeks": lambda v: f"{v:5d}"}
    print(f"trials to date {n}. annualized, per $1 long and $1 short (time-series: $1 gross)\n")
    print(r.to_string(formatters=fmt))
    # A negative IC is what the hypothesis predicts: higher funding, lower return.
    print("\nIC is rank correlation of trailing funding with next week's return;")
    print("the hypothesis predicts it is negative.")
    print("\nprice component by year")
    print(pd.DataFrame(years).T.to_string(float_format=pct))

    out = HERE / "reports"
    out.mkdir(exist_ok=True)
    r.to_csv(out / "funding_crowding.csv")
    return 0


if __name__ == "__main__":
    sys.exit(main())
