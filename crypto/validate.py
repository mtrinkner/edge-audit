#!/usr/bin/env python3
"""How much of strategy 21's Sharpe survives an honest out-of-sample test?

    python3 crypto/validate.py

Everything here was fixed in the registry before it ran (the strategy 21
validation precommitment and the strategy 24 declaration).

THE HOLDOUT. Strategy 21 was conceived by looking at 16 survivor coins. The
point-in-time universe has 93 other coins that played no part in forming the
idea. They are the out-of-sample set. Time cannot be held out (there is no
future data), but coins can.

THE LIQUIDATION MODEL. A funding series cannot see the trade's real risk. Here
the short perp runs at leverage L and is topped back up to target margin at
each daily close. If the day's high clears the prior close by 1/L - 3%
(maintenance), the short is liquidated: lose a penalty on notional for fees,
slippage and the gap, then pay to re-enter. The daily top-up is a trade too.

THEN: probabilistic Sharpe and block-bootstrap intervals on the holdout, a Monte
Carlo of venue failure, walk-forward lookback selection, and the eight variants
of strategy 24 (vol sizing, leverage, a stablecoin-supply regime gate).
"""

from __future__ import annotations

import math
import sys

import numpy as np
import pandas as pd

from funding_carry import COST_PER_LEG, DATA, HERE
from pit_rerun import daily
from deflated_sharpe import deflated_sharpe_ratio, expected_max_sharpe
import registry

SURVIVORS = ["AVAX", "BTC", "DOGE", "ENA", "ETH", "HYPE", "MET", "NEAR", "ONDO",
             "PUMP", "STRK", "SUI", "TAO", "VVV", "XRP", "ZEC"]
MAINT = 0.03
HALF_TRIP = 2 * COST_PER_LEG
RNG = np.random.default_rng(20261009)
CRYPTO_FAMILY = (20, 21, 22, 24)    # the strategies whose variants share this data


# ---------------------------------------------------------------- the engine

def panel() -> pd.DataFrame:
    d = daily()
    g = d.groupby("coin")
    d["prev_close"] = g["close"].shift(1)
    d["rally"] = d["high"] / d["prev_close"] - 1
    d["vol30"] = g["ret"].transform(lambda r: r.shift(1).rolling(30, min_periods=20).std())
    for lb in (7, 14, 30, 60):
        d[f"trail{lb}"] = g["rate"].transform(
            lambda s, lb=lb: s.shift(1).rolling(lb).sum()) * 365 / lb
    sc = pd.read_parquet(DATA / "stablecoins.parquet").set_index("date")["supply"]
    growth = (sc / sc.shift(30) - 1).shift(1)          # known yesterday
    d["sc_growth"] = growth.reindex(d["day"].dt.tz_localize(None).dt.normalize(),
                                    method="ffill").values
    d["week"] = d["day"].dt.tz_localize(None).dt.to_period("W")
    return d


def book(d: pd.DataFrame, coins: list[str] | None = None, lookback: int = 30,
         lev: float = 3.0, sizing: str = "equal", gate: bool = False,
         liq_penalty: float | None = None, lookback_by_week: pd.Series | None = None
         ) -> pd.Series:
    """Daily excess over T-bills per dollar of capital, equal capital per member
    coin (or inverse-vol), switched sleeves sitting in T-bills."""
    x = d if coins is None else d[d["coin"].isin(coins)]
    x = x.copy()
    margin = 1 / lev
    cap = 1 + margin

    if lookback_by_week is not None:
        lb = x["week"].map(lookback_by_week).fillna(30).astype(int)
        trail = pd.Series(np.nan, index=x.index)
        for v in lb.unique():
            m = lb == v
            trail[m] = x.loc[m, f"trail{v}"]
    else:
        trail = x[f"trail{lookback}"]
    want = (trail / cap > x["tbill"]).astype(float).where(trail.notna(), 0.0)
    first = x["week"] != x.groupby("coin")["week"].shift(1)
    want = want.where(first).groupby(x["coin"]).ffill().fillna(0.0)
    if gate:
        want = want * (x["sc_growth"] > 0).astype(float)
    pos = want * x["member"]

    if sizing == "invvol":
        iv = (1 / x["vol30"]).where(x["member"] > 0)
        w = iv / iv.groupby(x["day"]).transform("mean")
        w = w.fillna(1.0).clip(upper=3.0)
    else:
        w = pd.Series(1.0, index=x.index)
    expo = pos * w

    pnl = x["rate"] + x["basis_pnl"].fillna(0.0)                 # per $ notional
    pnl = pnl - 2 * COST_PER_LEG * x["ret"].abs().fillna(0.0)    # daily margin top-up
    if liq_penalty is not None:
        liq = (x["rally"] >= margin - MAINT).astype(float)
        pnl = pnl - liq * (liq_penalty + 2 * HALF_TRIP)
    prev = expo.groupby(x["coin"]).shift(1).fillna(0.0)
    last = x.groupby("coin")["day"].transform("max") == x["day"]
    trades = (expo - prev).abs() + np.where(last, expo, 0.0)
    ex = expo * (pnl / cap - x["tbill"] / 365) - trades * HALF_TRIP / cap

    m = x["member"] > 0
    return ex[m].groupby(x.loc[m, "day"]).mean()


# ---------------------------------------------------------------- statistics

def monthly(b: pd.Series) -> pd.Series:
    return b.groupby(b.index.strftime("%Y-%m")).sum()


def sharpe_m(b: pd.Series) -> float:
    m = monthly(b)
    return float(m.mean() / m.std() * math.sqrt(12))


def psr(b: pd.Series, target_annual: float) -> float:
    """Probabilistic Sharpe (Bailey & Lopez de Prado): P(true SR > target),
    on monthly returns, allowing for skew and fat tails."""
    m = monthly(b).values
    n = len(m)
    sr = m.mean() / m.std(ddof=1)
    g3 = pd.Series(m).skew()
    g4 = pd.Series(m).kurt() + 3
    tgt = target_annual / math.sqrt(12)
    z = (sr - tgt) * math.sqrt(n - 1) / math.sqrt(1 - g3 * sr + (g4 - 1) / 4 * sr ** 2)
    return 0.5 * (1 + math.erf(z / math.sqrt(2)))


def stationary_bootstrap(b: pd.Series, n_paths: int = 5000, mean_block: int = 30):
    """Politis-Romano resampling of the daily series, preserving dependence."""
    v = b.values
    n = len(v)
    p = 1 / mean_block
    out = np.empty((n_paths, n))
    for k in range(n_paths):
        idx = np.empty(n, dtype=int)
        i = RNG.integers(n)
        for t in range(n):
            if t and RNG.random() >= p:
                i = (i + 1) % n
            else:
                i = RNG.integers(n)
            idx[t] = i
        out[k] = v[idx]
    return out


def path_sharpes(paths: np.ndarray) -> np.ndarray:
    n = paths.shape[1] // 30 * 30
    mo = paths[:, :n].reshape(paths.shape[0], -1, 30).sum(axis=2)
    return mo.mean(axis=1) / mo.std(axis=1, ddof=1) * math.sqrt(12)


def venue_failure(paths: np.ndarray, hazard: float, loss: float):
    """Add Poisson venue failures to bootstrapped paths: each costs `loss` of capital."""
    p = paths.copy()
    hits = RNG.random(p.shape) < hazard / 365
    p[hits] -= loss
    sr = path_sharpes(p)
    eq = np.cumsum(p, axis=1)
    dd = (np.maximum.accumulate(eq, axis=1) - eq).max(axis=1)
    return sr, dd


# ---------------------------------------------------------------- walk-forward

def walk_forward(d: pd.DataFrame, coins: list[str]) -> pd.Series:
    """Each quarter, pick the lookback with the best trailing 12-month Sharpe
    (computed only on data before the quarter), and trade it that quarter."""
    curves = {lb: book(d, coins, lookback=lb) for lb in (7, 14, 30, 60)}
    days = curves[30].index
    quarters = pd.PeriodIndex(days.tz_localize(None), freq="Q").unique()
    weeks = d["week"].unique()
    choice = {}
    for q in quarters[4:]:
        start = q.start_time.tz_localize("UTC")
        lo = start - pd.Timedelta(days=365)
        scores = {}
        for lb, c in curves.items():
            past = c[(c.index >= lo) & (c.index < start)]
            mo = monthly(past)
            scores[lb] = mo.mean() / mo.std() if mo.std() > 0 else -np.inf
        best = max(scores, key=scores.get)
        for wk in weeks:
            if wk.start_time.to_period("Q") == q:
                choice[wk] = best
    lbw = pd.Series(choice)
    oos = book(d, coins, lookback_by_week=lbw)
    first_oos = quarters[4].start_time.tz_localize("UTC")
    return oos[oos.index >= first_oos], lbw


# ---------------------------------------------------------------- report

def line(name: str, b: pd.Series, n_trials: int, n_family: int) -> str:
    sr = sharpe_m(b)
    n = len(monthly(b))
    return (f"  {name:<44}{b.mean() * 365:7.1%}{sr:7.2f}"
            f"{psr(b, 0):7.2f}{psr(b, 1.5):7.2f}"
            f"{deflated_sharpe_ratio(sr, n_family, n):8.3f}"
            f"{deflated_sharpe_ratio(sr, n_trials, n):8.3f}")


HEAD = (f"  {'':<44}{'excess':>7}{'SR':>7}{'PSR>0':>7}{'PSR>1.5':>8}"
        f"{'DSR fam':>8}{'DSR all':>8}")


def main() -> int:
    d = panel()
    rows = registry.entries()
    n_trials = registry.trials()["n_trials"]
    n_family = sum(max(1, r.get("variants", 1)) for r in rows
                   if r["kind"] == "declare" and r["strategy_id"] in CRYPTO_FAMILY)
    members = sorted(d.loc[d.member > 0, "coin"].unique())
    holdout = [c for c in members if c not in SURVIVORS]
    seen = [c for c in members if c in SURVIVORS]
    print(f"{len(members)} coins in the universe: {len(seen)} seen while forming the idea, "
          f"{len(holdout)} held out")
    print(f"trials: {n_trials} in the repo, {n_family} in the crypto family "
          f"(bars {expected_max_sharpe(n_trials, 42):.2f} / "
          f"{expected_max_sharpe(n_family, 42):.2f} at 42 months)\n")

    print("STRATEGY 21 AS SPECIFIED (30d, weekly, 3x, equal weight)")
    print(HEAD)
    s21_full = book(d)
    print(line("all coins, no liquidations (the 1.92)", s21_full, n_trials, n_family))
    print(line("seen coins only", book(d, seen), n_trials, n_family))
    print(line("HOLDOUT, no liquidations", book(d, holdout), n_trials, n_family))
    for pen in (0.02, 0.05, 0.10):
        tag = "HOLDOUT, liquidations at " + f"{pen:.0%}" + (" (HEADLINE)" if pen == 0.05 else "")
        print(line(tag, book(d, holdout, liq_penalty=pen), n_trials, n_family))
    hold = book(d, holdout, liq_penalty=0.05)

    liq = d[(d.member > 0) & d.coin.isin(holdout)]
    for lev in (2, 3, 5):
        ev = (liq["rally"] >= 1 / lev - MAINT).sum()
        cy = liq.groupby("coin")["day"].size().sum() / 365
        print(f"    liquidation events at {lev}x: {ev} ({ev / cy:.2f} per coin-year held out)")

    print("\nBOOTSTRAP (stationary, mean block 30 days, 5,000 paths) on the holdout headline")
    paths = stationary_bootstrap(hold)
    srs = path_sharpes(paths)
    lo, mid, hi = np.percentile(srs, [5, 50, 95])
    print(f"  Sharpe 5th / 50th / 95th percentile: {lo:.2f} / {mid:.2f} / {hi:.2f}")
    print(f"  share of paths with Sharpe > 1.5: {(srs > 1.5).mean():.0%}, > 0: {(srs > 0).mean():.0%}")

    print("\nVENUE FAILURE MONTE CARLO (loses the perp margin, 25% of capital at 3x)")
    print(f"  {'annual hazard':<16}{'median SR':>10}{'P(SR>1.5)':>11}{'P(DD>20%)':>11}")
    for h in (0.0, 0.01, 0.03, 0.05):
        sr, dd = venue_failure(paths, h, (1 / 3) / (1 + 1 / 3))
        print(f"  {h:<16.0%}{np.median(sr):10.2f}{(sr > 1.5).mean():11.0%}{(dd > 0.20).mean():11.0%}")

    print("\nWALK-FORWARD on the holdout (quarterly lookback choice from 7/14/30/60)")
    wf, choice = walk_forward(d.assign(), holdout)
    fixed = book(d, holdout)
    fixed = fixed[fixed.index >= wf.index.min()]
    print(f"  chosen lookbacks: {choice.value_counts().sort_index().to_dict()} (weeks)")
    print(f"  walk-forward OOS Sharpe {sharpe_m(wf):.2f} vs fixed 30d over the same span "
          f"{sharpe_m(fixed):.2f} ({wf.index.min().date()} on, no liquidations)")

    print("\nSTRATEGY 24: designed on the seen coins, judged on the holdout, liquidations at 5%")
    print(f"  {'variant':<30}{'seen SR':>9}{'hold excess':>12}{'hold SR':>9}{'PSR>1.5':>9}"
          f"{'DSR fam':>9}{'DSR all':>9}")
    results = []
    for sizing in ("equal", "invvol"):
        for lev in (2.0, 3.0):
            for gate in (False, True):
                name = f"{sizing:<7} {lev:.0f}x gate {'on ' if gate else 'off'}"
                primary = sizing == "invvol" and lev == 2.0 and not gate
                s = book(d, seen, lev=lev, sizing=sizing, gate=gate, liq_penalty=0.05)
                h = book(d, holdout, lev=lev, sizing=sizing, gate=gate, liq_penalty=0.05)
                sr = sharpe_m(h)
                n = len(monthly(h))
                print(f"  {name + (' PRIMARY' if primary else ''):<30}{sharpe_m(s):9.2f}"
                      f"{h.mean() * 365:12.1%}{sr:9.2f}{psr(h, 1.5):9.2f}"
                      f"{deflated_sharpe_ratio(sr, n_family, n):9.3f}"
                      f"{deflated_sharpe_ratio(sr, n_trials, n):9.3f}")
                results.append({"variant": name, "primary": primary, "seen_sr": sharpe_m(s),
                                "holdout_excess": h.mean() * 365, "holdout_sr": sr,
                                "psr_1_5": psr(h, 1.5)})
                if primary:
                    prim = h
    yr = prim.groupby(prim.index.year).mean() * 365
    print(f"  primary by year: " + ", ".join(f"{y} {v:+.1%}" for y, v in yr.items()))

    out = HERE / "reports"
    out.mkdir(exist_ok=True)
    pd.DataFrame(results).to_csv(out / "validation_s24.csv", index=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
