#!/usr/bin/env python3
"""Technical signals traded the way people actually trade them: with a stop.

    python3 python/ta_exits.py
    python3 python/ta_exits.py --signals golden_cross_50_200 rsi_oversold_30

WHAT WAS WRONG WITH THE OLD TEST

The 41 signals in signals.py were only ever evaluated with a FIXED-HORIZON exit:
buy when the signal fires, sell exactly h sessions later, whatever happened in
between. Nobody trades a golden cross that way. Real technical trading sets a
stop and a target, and the exit rule is arguably a larger lever on the outcome
than the entry signal is. Testing entries while ignoring exits measures half a
strategy.

THE RULES, DECLARED BEFORE THIS RAN (registry trial 27)

  entry      next session's OPEN after the signal fires, never the same close
  stop       2 x ATR(14) below entry
  target     3 x ATR(14) above entry
  timeout    60 sessions, exit at the close
  path       checked against intraday LOW and HIGH, bar by bar
  ties       if one bar touches both stop and target, the STOP is assumed first
  costs      each stock's own modelled half-spread, charged on both sides
  benchmark  buying and holding the SAME stock for the SAME realized number of
             days, which isolates the signal's timing and exit from the market

ONE SETTING, NOT A GRID. 2-and-3 ATR is a standard textbook pairing. Sweeping
the multiples would find a better number and that number would be fitted to this
sample, which is exactly how technical strategies get overfit. The whole point of
declaring the rule first is that it cannot be tuned afterwards.

WHY THE TIE-BREAK MATTERS. Daily bars cannot say whether the low or the high came
first within a session. Assuming the target hit first would flatter every result,
so the stop is assumed first. That makes every number here a lower bound rather
than a hopeful one.

PRICES ARE PUT ON A CONSISTENT BASIS. The OHLC columns are unadjusted while
adj_close carries splits and dividends. Stop and target levels have to be
compared against prices that actually traded, but returns have to be adjusted, so
every bar's open, high and low is scaled by that bar's adj_close/close ratio.
Without this a split inside a holding period reads as a 50% loss and trips every
stop in the sample.

FIXED 2026-10-26 (audit, docs/AUDIT_2026-10-26.md). Trial 27's original output
is kept as reports/ta_exits_pre_2026-10-26.csv and robustness_ta_pre_2026-10-26.csv.
  * Stops and targets filled AT the level even when the bar opened through it.
    They now fill at min(stop, open) and max(target, open), as realistic_exits.py
    does. The benchmark is unaffected, so the old excess was overstated.
  * The t-statistics treated overlapping trades as independent. They are now
    clustered by calendar blocks of 2 x TIMEOUT sessions
    (evaluation.block_clustered_mean).
"""

from __future__ import annotations

import argparse
import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

import config
import db
import lockbox
import signals as sg
from evaluation import block_clustered_mean
from spread_estimator import liquidity_half_spread_bps

ROOT = Path(__file__).resolve().parent.parent
ATR_N = 14
STOP_ATR = 2.0
TARGET_ATR = 3.0
TIMEOUT = 60
# Applied to the POOLED trade count across all symbols, never per symbol. An
# earlier version checked it inside the per-symbol loop, which silently deleted
# every rare signal: a 50/200 crossover fires roughly three times per stock in
# twelve years, so every symbol failed a 50-trade test and the whole signal
# vanished from the output without a word.
MIN_TRADES = 50


def load_bars(max_date: str) -> pd.DataFrame:
    with db.connect() as con:
        return pd.read_sql(
            "SELECT b.symbol, b.dt, b.open, b.high, b.low, b.close, b.adj_close, "
            "b.volume FROM bars b JOIN symbols s ON s.symbol=b.symbol "
            "WHERE s.kind='stock' AND b.dt <= ? ORDER BY b.symbol, b.dt",
            con, params=(max_date,))


def simulate_symbol(g: pd.DataFrame, names: list[str],
                    custom: dict | None = None) -> list[pd.DataFrame]:
    """Every firing of every signal for one symbol, run to its actual exit.

    `custom` maps a name to a function(g) -> boolean Series, so a study can put
    its own entry rule through exactly these exits (trial 46). Without it the
    behaviour is unchanged, so trial 27 still reproduces."""
    g = g.sort_values("dt").reset_index(drop=True)
    n = len(g)
    if n < ATR_N + TIMEOUT + 5:
        return []

    # Put OHLC on the same adjusted basis as adj_close (see module docstring).
    ratio = (g["adj_close"] / g["close"].replace(0, np.nan)).to_numpy()
    o = g["open"].to_numpy() * ratio
    h = g["high"].to_numpy() * ratio
    lo = g["low"].to_numpy() * ratio
    c = g["adj_close"].to_numpy()

    tr = np.maximum(h[1:] - lo[1:],
                    np.maximum(np.abs(h[1:] - c[:-1]), np.abs(lo[1:] - c[:-1])))
    atr = np.full(n, np.nan)
    atr[1:] = pd.Series(tr).rolling(ATR_N, min_periods=ATR_N).mean().to_numpy()

    adv = (g["close"] * g["volume"]).rolling(20, min_periods=20).mean()
    half = liquidity_half_spread_bps(g["close"], adv).to_numpy() / 10_000.0

    # Forward windows of highs and lows: row i holds the TIMEOUT bars that
    # follow entry at i+1, which is the first bar a position can be exposed to.
    win = TIMEOUT
    idx = np.arange(n)[:, None] + 1 + np.arange(win)[None, :]
    valid_row = idx[:, -1] < n
    idx = np.clip(idx, 0, n - 1)
    H = h[idx]
    L = lo[idx]
    O = o[idx]

    out = []
    custom = custom or {}
    sig_cache = {nm: (custom[nm](g) if nm in custom else sg.compute(g, nm)).to_numpy()
                 for nm in names}
    for nm in names:
        fired = sig_cache[nm]
        ok = (np.nan_to_num(fired, nan=0).astype(bool) & valid_row
              & np.isfinite(atr) & np.isfinite(half) & (np.arange(n) + 1 + win < n))
        rows = np.flatnonzero(ok)
        if len(rows) == 0:
            continue
        entry = o[rows + 1]
        a = atr[rows]
        stop = entry - STOP_ATR * a
        targ = entry + TARGET_ATR * a

        hit_s = L[rows] <= stop[:, None]
        hit_t = H[rows] >= targ[:, None]
        BIG = win + 1
        first_s = np.where(hit_s.any(1), hit_s.argmax(1), BIG)
        first_t = np.where(hit_t.any(1), hit_t.argmax(1), BIG)

        # Tie goes to the stop: a daily bar cannot order its own high and low.
        stopped = first_s <= first_t
        targeted = first_t < first_s
        k = np.minimum(first_s, first_t)
        timed_out = k == BIG

        # A bar that opens through a level fills at the open, not the level
        # (fixed 2026-10-26; this used to fill at the stop or target itself).
        o_hit = O[rows, np.minimum(k, win - 1)]
        exit_px = np.where(stopped & ~timed_out, np.minimum(stop, o_hit),
                   np.where(targeted & ~timed_out, np.maximum(targ, o_hit),
                            c[rows + 1 + win - 1]))
        days = np.where(timed_out, win, k + 1)

        cost = half[rows] + half[np.clip(rows + 1 + days - 1, 0, n - 1)]
        gross = exit_px / entry - 1.0
        net = gross - cost

        # Benchmark: own the same stock from the same entry for the same days.
        bench_exit = c[np.clip(rows + 1 + days - 1, 0, n - 1)]
        bench = bench_exit / entry - 1.0 - cost

        out.append(pd.DataFrame({
            "signal": nm, "symbol": g["symbol"].iloc[0],
            "dt": g["dt"].to_numpy()[rows],
            "days": days, "net": net, "bench": bench,
            "excess": net - bench,
            "outcome": np.where(timed_out, "timeout",
                        np.where(stopped, "stop", "target")),
        }))
    return out


def summarize(t: pd.DataFrame) -> dict:
    """Per-trade stats; the t-statistic and p-value cluster trades by calendar
    blocks at least as long as the longest hold (evaluation.block_clustered_mean).

    Until 2026-10-26 this sampled every median-hold-th entry date and bootstrapped
    that series. Trades hold up to TIMEOUT sessions, so entries a median hold apart
    still overlapped and the t-statistics were too large."""
    per_date = t.groupby("dt")[["net", "bench", "excess"]].mean().sort_index()
    med_hold = int(max(1, round(t["days"].median())))
    res = {
        "signal": t["signal"].iloc[0], "trades": len(t),
        "dates": len(per_date), "median_hold_days": med_hold,
        "mean_net": float(t["net"].mean()),
        "mean_bench": float(t["bench"].mean()),
        "excess": float(t["excess"].mean()),
        "hit_rate": float((t["net"] > 0).mean()),
        "pct_stopped": float((t["outcome"] == "stop").mean()),
        "pct_target": float((t["outcome"] == "target").mean()),
        "pct_timeout": float((t["outcome"] == "timeout").mean()),
        "p": None, "excess_t": None, "blocks": None,
    }
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")        # block count is reported instead
        b = block_clustered_mean(t["excess"], t["dt"], TIMEOUT)
    if b["clusters"] >= 10 and b["se"] > 0:
        res["excess_t"] = float(b["t"])
        res["p"] = float(b["p_two"])
        res["blocks"] = int(b["clusters"])
    return res


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--signals", nargs="*", default=None)
    args = ap.parse_args()

    st = lockbox.state()
    max_date = (pd.Timestamp(st["seal_from"]) - pd.Timedelta(days=1)).date().isoformat() \
        if st["sealed"] else config.END_DATE.isoformat()
    lockbox.guard(max_date, "ta_exits")
    names = args.signals or list(sg.SIGNALS)
    print(f"data through {max_date}")
    print(f"{len(names)} signals, "
          f"{STOP_ATR}x ATR stop / {TARGET_ATR}x ATR target / {TIMEOUT}d timeout\n")

    bars = load_bars(max_date)
    print(f"  {len(bars):,} bars, {bars['symbol'].nunique()} symbols")

    trades = []
    syms = list(bars.groupby("symbol", sort=False))
    for i, (sym, g) in enumerate(syms, 1):
        trades += simulate_symbol(g, names)
        if i % 300 == 0:
            print(f"  {i}/{len(syms)} symbols, {sum(len(x) for x in trades):,} trades")
    if not trades:
        print("no trades")
        return 1
    T = pd.concat(trades, ignore_index=True)
    print(f"  {len(T):,} simulated trades total\n")

    rows = [summarize(g) for _, g in T.groupby("signal", sort=False)
            if len(g) >= MIN_TRADES]
    res = pd.DataFrame(rows).sort_values("excess", ascending=False)

    # Benjamini-Hochberg across every signal measured in this run.
    k = res.dropna(subset=["p"]).sort_values("p").copy()
    m = len(k)
    k["bh_crit"] = (np.arange(1, m + 1) / m) * 0.05
    k["passes_bh"] = k["p"] <= k["bh_crit"]
    res = res.merge(k[["signal", "bh_crit", "passes_bh"]], on="signal", how="left")
    # NaN means "never tested", and NaN is truthy, so an untested signal printed
    # as a BH survivor until this was made explicit.
    res["passes_bh"] = res["passes_bh"].fillna(False).astype(bool)
    res["tested"] = res["p"].notna()
    (ROOT / "reports").mkdir(exist_ok=True)
    res.to_csv(ROOT / "reports" / "ta_exits.csv", index=False)

    f = lambda v, s="{:.2f}": s.format(v) if pd.notna(v) else "     -"
    print("=" * 108)
    print("TECHNICAL SIGNALS WITH A REAL EXIT  —  excess over holding the same "
          "stock the same number of days")
    print("=" * 108)
    print(f"{'signal':<25}{'trades':>8}{'blocks':>7}{'hold':>6}{'stop%':>7}"
          f"{'tgt%':>6}{'hit%':>6}{'net':>8}{'bench':>8}{'excess':>9}{'t':>7}{'p':>7}{'BH':>5}")
    for _, r in res.iterrows():
        print(f"{r['signal']:<25}{int(r['trades']):>8}{f(r['blocks'],'{:.0f}'):>7}"
              f"{int(r['median_hold_days']):>6}{f(r['pct_stopped'],'{:.0%}'):>7}"
              f"{f(r['pct_target'],'{:.0%}'):>6}{f(r['hit_rate'],'{:.0%}'):>6}"
              f"{f(r['mean_net'],'{:+.2%}'):>8}{f(r['mean_bench'],'{:+.2%}'):>8}"
              f"{f(r['excess'],'{:+.2%}'):>9}{f(r['excess_t']):>7}"
              f"{f(r['p'],'{:.3f}'):>7}"
              f"{('YES' if bool(r['passes_bh']) else ('no' if r['tested'] else '  -')):>5}")

    nsurv = int(res["passes_bh"].fillna(False).sum())
    print(f"\nsurvived Benjamini-Hochberg at FDR 5%: {nsurv} of {m}")
    print(f"signals with positive excess: {int((res['excess'] > 0).sum())} of {len(res)}")
    print(f"mean excess across all signals: {res['excess'].mean():+.3%}")
    print(f"\nwrote reports/ta_exits.csv")
    return 0


if __name__ == "__main__":
    sys.exit(main())
