#!/usr/bin/env python3
"""
The 12 day-trading rules where trading is cheap: mega-caps and ETFs (trial 41).

    python3 python/cheap_venues.py

WHY. Trials 38 to 40 all failed the same way: the rules make 0 to 10bp before
costs and the round trip costs about 13bp. So go where the round trip is smaller.

  MEGA-CAPS  each month, the 50 symbols with the highest median daily dollar
             volume in the PREVIOUS month. Membership is known before the month
             starts. Chosen after seeing that vwap_reversion_long makes 9.6bp
             gross against a 7.4bp mega-cap spread, so contaminated by selection.
  ETFS       24 liquid equity ETFs, declared before their data was pulled, never
             seen by this project before. The cleaner test.

Same rules, same causal code, entry at the close of the signal bar, hold to the
session close. Cost is twice the previous month's EDGE half-spread, measured
within sessions. For ETFs a flat 1bp round trip is printed alongside, because
EDGE may overstate spreads below a basis point; it is not the pass bar.

CORRECTION (trial 42). The "net/day" and session-weighted columns split each
session's capital equally across ALL of that session's signals, which needs the
end-of-day count at entry time. vwap_reversion_long fires more on days the market
keeps falling, so that weighting quietly down-weights the losing days. It is not
tradeable. The per-trade columns are the causal results.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
from bidask import edge
from scipy.stats import t as tdist

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "python"))

import data_access as da
import intraday_signals as isg
from tpsl_heatmap import BARS, load

TOP_N = 50


def sessionize(d: pd.DataFrame) -> pd.DataFrame:
    d = d.copy()
    d["ts"] = pd.to_datetime(d["ts"], utc=True).dt.tz_convert("America/New_York")
    d["session"] = d["ts"].dt.date.astype(str)
    d = d.sort_values(["symbol", "ts"]).reset_index(drop=True)
    d["bar"] = d.groupby(["symbol", "session"]).cumcount()
    d["bars_in_session"] = d.groupby(["symbol", "session"])["bar"].transform("size")
    return d[d["bars_in_session"] == BARS].reset_index(drop=True)


def signals(d: pd.DataFrame) -> pd.DataFrame:
    """First firing bar of each rule per symbol-session, as intraday_study.py."""
    rows = []
    for sym, g in d.groupby("symbol", sort=False):
        prev_close = np.nan
        for sname, s in g.groupby("session", sort=True):
            s = s.reset_index(drop=True)
            for name, fn in isg.RULES.items():
                m = fn(s, prev_close) if name in isg.NEEDS_PREV_CLOSE else fn(s)
                idx = np.flatnonzero(m)
                if len(idx):
                    rows.append((name, sym, sname, int(idx[0])))
            prev_close = s["close"].iloc[-1]
    return pd.DataFrame(rows, columns=["rule", "symbol", "session", "bar"])


def edge_costs(d: pd.DataFrame) -> dict:
    """Round-trip cost per symbol-month from the PREVIOUS month's EDGE."""
    d = d.assign(month=d["session"].str[:7])
    rows = []
    for (sym, mo), g in d.groupby(["symbol", "month"]):
        per = []
        for _, s in g.groupby("session"):
            try:
                e = edge(*(s[c].to_numpy() for c in ("open", "high", "low", "close")))
            except Exception:
                continue
            if e is not None and np.isfinite(e):
                per.append(max(float(e), 0.0))
        if len(per) >= 10:
            rows.append((sym, mo, np.mean(per)))
    e = pd.DataFrame(rows, columns=["symbol", "month", "full"]).sort_values(["symbol", "month"])
    e["cost"] = e.groupby("symbol")["full"].shift(1)
    return {(s, m): c for s, m, c in zip(e["symbol"], e["month"], e["cost"]) if np.isfinite(c)}


def evaluate(d, trades, cost, label, flat=None) -> list[dict]:
    d = d.copy()
    last = d.groupby(["symbol", "session"])["close"].transform("last")
    d["gross"] = last / d["close"] - 1.0
    d["cost"] = [flat if flat is not None else cost.get((s, x[:7]), np.nan)
                 for s, x in zip(d["symbol"], d["session"])]
    d = d.dropna(subset=["cost"])
    d["net"] = d["gross"] - d["cost"]
    bench = d.groupby(["symbol", "bar"])["net"].mean().rename("bench")
    x = trades.join(d.set_index(["symbol", "session", "bar"])[["gross", "net", "cost"]],
                    on=["symbol", "session", "bar"]).dropna(subset=["net"])
    x = x.join(bench, on=["symbol", "bar"])
    x["excess"] = x["net"] - x["bench"]
    out = []
    for rule, g in x.groupby("rule"):
        ps = g.groupby("session")[["gross", "net", "excess"]].mean()
        n = len(ps)
        sd = ps["net"].std(ddof=1)
        t = ps["net"].mean() / (sd / np.sqrt(n)) if n > 30 and sd > 0 else np.nan
        out.append({"venue": label, "rule": rule, "trades": len(g), "sessions": n,
                    "cost_bp": g["cost"].mean() * 1e4,
                    "gross_tw_bp": g["gross"].mean() * 1e4, "net_tw_bp": g["net"].mean() * 1e4,
                    "gross_sw_bp": ps["gross"].mean() * 1e4, "net_sw_bp": ps["net"].mean() * 1e4,
                    "excess_sw_bp": ps["excess"].mean() * 1e4, "t_net": t,
                    "p_net": tdist.sf(t, n - 1) if np.isfinite(t) else np.nan})
    return out


def megacap_members(d: pd.DataFrame) -> set:
    s = d.groupby(["symbol", "session"]).agg(v=("volume", "sum"), c=("close", "last"))
    s["dv"] = s["v"] * s["c"]
    s = s.reset_index()
    s["month"] = s["session"].str[:7]
    m = s.groupby(["symbol", "month"])["dv"].median().reset_index()
    months = sorted(m["month"].unique())
    nxt = dict(zip(months[:-1], months[1:]))
    m["rank"] = m.groupby("month")["dv"].rank(ascending=False)
    top = m[m["rank"] <= TOP_N]
    # Ranked on month t, eligible to trade in month t+1.
    return {(sym, nxt[mo]) for sym, mo in zip(top["symbol"], top["month"]) if mo in nxt}


def show(r: pd.DataFrame):
    print(f"{'venue':<14}{'rule':<26}{'trades':>8}{'cost':>7}{'gross':>8}{'net/tr':>8}"
          f"{'net/day':>9}{'excess':>8}{'t':>7}")
    for _, x in r.iterrows():
        print(f"{x.venue:<14}{x.rule:<26}{x.trades:>8,}{x.cost_bp:>6.1f}b{x.gross_tw_bp:>7.1f}b"
              f"{x.net_tw_bp:>7.1f}b{x.net_sw_bp:>8.1f}b{x.excess_sw_bp:>7.1f}b{x.t_net:>7.2f}")


def main() -> int:
    print("trial 41: mega-caps and ETFs\n")
    d, cost, trades = load()
    members = megacap_members(d)
    mt = trades[[(s, x[:7]) in members for s, x in zip(trades["symbol"], trades["session"])]]
    md = d[[(s, x[:7]) in members for s, x in zip(d["symbol"], d["session"])]]
    print(f"  mega-caps: {mt['symbol'].nunique()} distinct symbols over the window, "
          f"{len(mt):,} trades")
    mega = evaluate(md, mt, cost, "mega-cap")

    # Through data_access, which stops at the lockbox seal (added 2026-10-26).
    e = sessionize(da.parquet("cheap_venues", "etf_1h_raw"))
    print(f"  ETFs: {e['symbol'].nunique()} symbols, {e['session'].nunique()} sessions")
    et = signals(e)
    ec = edge_costs(e)
    print(f"  ETF median EDGE round trip: {np.median(list(ec.values())) * 1e4:.2f}bp\n")
    etf = evaluate(e, et, ec, "ETF")
    etf_flat = evaluate(e, et, None, "ETF @1bp", flat=1e-4)

    r = pd.DataFrame(mega + etf)
    k = r.dropna(subset=["p_net"]).sort_values("p_net")
    m = len(k)
    r["bh"] = False
    r.loc[k.index, "bh"] = k["p_net"].to_numpy() <= np.arange(1, m + 1) / m * 0.05
    r["passes"] = r["bh"] & (r["net_tw_bp"] > 0) & (r["net_sw_bp"] > 0)
    out = pd.concat([r, pd.DataFrame(etf_flat)], ignore_index=True)
    out.to_csv(ROOT / "reports" / "cheap_venues.csv", index=False)

    show(r.sort_values(["venue", "net_sw_bp"], ascending=[False, False]))
    print("\nsecondary readout, NOT the pass bar: ETFs at a flat 1bp round trip")
    show(pd.DataFrame(etf_flat).sort_values("net_sw_bp", ascending=False))
    print(f"\npositive net per trade (pass-bar venues): {(r['net_tw_bp'] > 0).sum()} of {len(r)}")
    print(f"meeting the declared pass bar: {int(r['passes'].sum())} of {len(r)}")
    print("wrote reports/cheap_venues.csv")
    return 0


if __name__ == "__main__":
    sys.exit(main())
