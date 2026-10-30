#!/usr/bin/env python3
"""Does the liquidity premium scale with market stress? (trial 31)

    python3 python/intraday_vix.py

THE PRIOR COMES FROM A PAPER, NOT FROM THIS DATA

Nagel, "Evaporating Liquidity" (Review of Financial Studies 25(7), 2012), finds
that short-term reversal returns are what liquidity providers earn, and that the
expected return is strongly time-varying and predictable with VIX, because
liquidity supply withdraws when markets are stressed. A NY Fed staff report
("Decomposing Short-Term Return Reversal") finds the liquidity-provision channel
matters most for RECENT LOSERS, which is exactly the down-bar asymmetry measured
here: high-volume down bars revert, high-volume up bars do not.

So if our +4.94bp gross effect is that premium, it has to GROW with stress.

FALSIFIABLE PREDICTION, declared before running

The treatment-minus-control edge rises monotonically from the low to the high
VIX tercile. If it does not, Nagel's mechanism does not apply here, the
liquidity-provision interpretation of our result is wrong, and I should stop
telling that story about it.

VIX IS LAGGED ONE SESSION. The prior close, never the same day's. Same-day VIX is
contemporaneous with the very price action being measured, so using it would be
conditioning on the outcome. Terciles rather than deciles, to keep the search
small enough that a monotone pattern means something.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

import data_access as da
import config
from spread_estimator import liquidity_half_spread_bps

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data" / "intraday_1h.parquet"
VIXP = ROOT / "data" / "vix.parquet"
SESSION_BARS = 7
VOL_MULT = 2.0


def get_vix() -> pd.DataFrame:
    if VIXP.exists():
        return da.parquet("intraday_vix", "vix")
    import yfinance as yf
    v = yf.download("^VIX", period="1100d", interval="1d",
                    progress=False, auto_adjust=False)
    if isinstance(v.columns, pd.MultiIndex):
        v.columns = v.columns.get_level_values(0)
    out = pd.DataFrame({"session": v.index.strftime("%Y-%m-%d"),
                        "vix": v["Close"].to_numpy()}).dropna()
    # Lag one session: what you could have known before the open.
    out["vix_prior"] = out["vix"].shift(1)
    out = out.dropna()
    out.to_parquet(VIXP, index=False)
    return out


def boot_p(x, reps=2000, seed=config.RANDOM_SEED):
    x = x[np.isfinite(x)]
    if len(x) < 20 or x.std(ddof=1) == 0:
        return float("nan")
    rng = np.random.default_rng(seed)
    cen = x - x.mean()
    b = np.array([cen[rng.integers(0, len(x), len(x))].mean() for _ in range(reps)])
    return float((np.abs(b) >= abs(x.mean())).mean())


def main() -> int:
    vix = get_vix()
    print(f"  VIX: {len(vix):,} sessions, {vix['session'].min()} to "
          f"{vix['session'].max()}, range {vix['vix'].min():.1f}-{vix['vix'].max():.1f}")

    # Through data_access, which stops at the lockbox seal (added 2026-10-26).
    d = da.parquet("intraday_vix", "intraday_1h")
    d = d[d["bars_in_session"] == SESSION_BARS].copy()
    d = d.sort_values(["symbol", "ts"]).reset_index(drop=True)
    g = d.groupby(["symbol", "session"])
    d["fin"] = g["close"].transform("last")
    # CAUSAL volume baseline: expanding mean of the bars BEFORE this one in
    # the same session. Using the session mean (as this did originally) lets a
    # decision at 11:30 depend on volume at 15:30, and that one line was the
    # largest error in the project. tests/test_no_lookahead_intraday.py now
    # catches it by swapping the future and checking the decision is unchanged.
    d["vm"] = (g["volume"].transform(
        lambda x: x.shift(1).expanding().mean()))
    d["dollar"] = g["volume"].transform("sum") * d["fin"]
    d["cost"] = 2 * liquidity_half_spread_bps(d["fin"], d["dollar"]) / 1e4
    d["gross"] = d["fin"] / d["close"] - 1.0
    d["vr"] = d["volume"] / d["vm"].replace(0, np.nan)
    d["down"] = d["close"] < d["open"]
    d = d[(d["bar"] >= 1) & (d["bar"] <= SESSION_BARS - 2)]
    d = d.dropna(subset=["gross", "vr", "cost"])
    d = d.merge(vix[["session", "vix_prior"]], on="session", how="inner")
    print(f"  {len(d):,} bars matched to a prior-session VIX\n")

    # Terciles on the PRIOR close, computed over sessions not bars, so a
    # high-activity day cannot drag the boundaries.
    sv = d[["session", "vix_prior"]].drop_duplicates().sort_values("session")
    sv["bucket"] = pd.qcut(sv["vix_prior"], 3, labels=["low", "mid", "high"])
    d = d.merge(sv[["session", "bucket"]], on="session", how="left")

    t = d[(d["vr"] > VOL_MULT) & d["down"]]
    a = d[(d["vr"] > VOL_MULT) & ~d["down"]]

    print("=" * 100)
    print("VIX-CONDITIONED LIQUIDITY PREMIUM  —  prediction: edge rises with stress")
    print("=" * 100)
    print(f"{'VIX bucket':<12}{'VIX range':>14}{'sess':>6}{'treat bars':>11}"
          f"{'treat':>10}{'ctrlA':>10}{'EDGE':>10}{'t':>7}{'net':>10}")
    print("-" * 100)
    rows = []
    for b in ["low", "mid", "high"]:
        tt = t[t["bucket"] == b]
        aa = a[a["bucket"] == b]
        vv = sv[sv["bucket"] == b]["vix_prior"]
        if len(tt) < 100:
            continue
        ts = tt.groupby("session")["gross"].mean()
        as_ = aa.groupby("session")["gross"].mean()
        j = pd.concat([ts.rename("t"), as_.rename("c")], axis=1).dropna()
        dd = (j["t"] - j["c"]).to_numpy()
        tstat = (dd.mean() / (dd.std(ddof=1) / np.sqrt(len(dd)))
                 if len(dd) > 2 and dd.std(ddof=1) > 0 else np.nan)
        edge = tt["gross"].mean() - aa["gross"].mean()
        net = tt["gross"].mean() - tt["cost"].mean()
        flag = "  <-- NET POSITIVE" if net > 0 else ""
        print(f"{b:<12}{f'{vv.min():.1f}-{vv.max():.1f}':>14}{len(vv):>6}"
              f"{len(tt):>11,}{tt['gross'].mean():>+10.4%}{aa['gross'].mean():>+10.4%}"
              f"{edge:>+10.4%}{tstat:>7.2f}{net:>+10.4%}{flag}")
        rows.append({"bucket": b, "vix_lo": float(vv.min()), "vix_hi": float(vv.max()),
                     "sessions": len(vv), "bars": len(tt),
                     "treat": tt["gross"].mean(), "ctrlA": aa["gross"].mean(),
                     "edge": edge, "t": tstat, "net": net,
                     "p": boot_p(dd)})
    r = pd.DataFrame(rows)
    r.to_csv(ROOT / "reports" / "intraday_vix.csv", index=False)

    if len(r) == 3:
        e = r["edge"].to_numpy()
        mono = e[0] < e[1] < e[2]
        print(f"\n  edge by bucket: {e[0]:+.4%} -> {e[1]:+.4%} -> {e[2]:+.4%}")
        print(f"  MONOTONE RISE PREDICTED BY NAGEL: "
              f"{'YES, the prediction holds' if mono else 'NO, the prediction FAILS'}")
        if not mono:
            print("  -> the liquidity-provision story for this effect is not")
            print("     supported by its own stress dependence, and I should stop")
            print("     telling it as the explanation.")
        print(f"\n  high minus low tercile: {e[2] - e[0]:+.4%}")
        hi = r[r.bucket == "high"].iloc[0]
        print(f"  high-VIX net after 12.5bp-ish costs: {hi['net']:+.4%} "
              f"({'clears costs' if hi['net'] > 0 else 'still does not clear costs'})")
    print(f"\nwrote reports/intraday_vix.csv")
    return 0


if __name__ == "__main__":
    sys.exit(main())
