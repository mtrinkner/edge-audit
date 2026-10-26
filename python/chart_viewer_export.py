#!/usr/bin/env python3
"""Export per-stock chart data for the Signal Charts page.

    python3 python/chart_viewer_export.py

For the 100 stocks with the highest median daily dollar volume over the last
60 sessions before the lockbox seal, writes reports/chart_viewer/stocks/<T>.json:
about three years of split- and dividend-adjusted daily prices, the 50- and
200-day averages, every day each of the 41 tested signals fired, and the
Charting by Machines model's monthly decile (trial 56's frozen model, scored
out of sample on 2023-01 onward). Also writes index.json with the stock list
and each signal's tested result (excess return over holding the same stock,
trimmed, from reports/robustness_ta.csv).

Signals are computed on each stock's full history, so the 200-day indicators
are warmed up, then cut to the display window. A condition that holds for weeks
(price above its 200-day average) is marked only on the day it turns on.

Prices come from the project's own database (Yahoo-derived), not CRSP.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "python"))

import db
import lockbox
import signals as sg

OUT = ROOT / "reports" / "chart_viewer"
TOP_N = 100
START = "2023-10-01"
STATES = {"above_sma_200", "above_sma_50", "ma_ribbon_aligned", "macd_hist_positive"}
GROUPS = {
    "Moving averages": ["golden_cross_50_200", "golden_cross_20_100", "death_cross_50_200",
                        "above_sma_200", "above_sma_50", "cross_above_sma_50",
                        "cross_above_sma_20", "ma_ribbon_aligned", "ema_cross_12_26",
                        "pullback_to_sma50"],
    "Momentum oscillators": ["rsi_oversold_30", "rsi_oversold_20", "rsi_overbought_70",
                             "rsi_cross_50", "macd_cross_up", "macd_hist_positive",
                             "stochastic_oversold", "oversold_in_uptrend"],
    "Bands and breakouts": ["bollinger_lower_touch", "bollinger_upper_break",
                            "extreme_below_sma20", "donchian_break_20", "donchian_break_55",
                            "new_52w_high", "resistance_break_volume", "support_bounce"],
    "Candlesticks": ["bullish_engulfing", "bearish_engulfing", "hammer", "shooting_star",
                     "doji", "morning_star", "three_white_soldiers", "inside_bar",
                     "outside_bar_up"],
    "Gaps and volume": ["gap_up_2pct", "gap_down_2pct", "volume_spike_up", "volume_dryup",
                        "three_down_days", "big_down_day"],
}


def label(name: str) -> str:
    words = name.replace("_", " ")
    for a, b in (("sma", "SMA"), ("ema", "EMA"), ("rsi", "RSI"), ("macd", "MACD"),
                 ("52w", "52-week"), ("2pct", "2%")):
        words = words.replace(a, b)
    return words[0].upper() + words[1:]


def ai_deciles(symbols: set) -> dict:
    import cbm_replication as cbm
    o = pd.read_parquet(ROOT / "data" / "cbm" / "ours.parquet")
    o["MLER"] = cbm.predict(o[cbm.F].to_numpy(), "own")
    o["dec"] = o.groupby("Month")["MLER"].transform(
        lambda s: pd.qcut(s.rank(method="first"), 10, labels=False)) + 1
    o = o[o["permno"].isin(symbols)]
    out = {}
    for s, g in o.groupby("permno"):
        out[s] = [[f"{int(m) // 100}-{int(m) % 100:02d}", int(d)] for m, d in zip(g["Month"], g["dec"])]
    return out


def main() -> int:
    end = lockbox.state()["seal_from"]
    max_date = (pd.Timestamp(end) - pd.Timedelta(days=1)).date().isoformat()
    lockbox.guard(max_date, "chart_viewer_export")
    with db.connect() as con:
        d = pd.read_sql("SELECT b.symbol, b.dt, b.open, b.high, b.low, b.close, b.adj_close, "
                        "b.volume, s.name, s.sector FROM bars b JOIN symbols s USING(symbol) "
                        "WHERE s.kind='stock' AND b.dt <= ? ORDER BY b.symbol, b.dt",
                        con, params=(max_date,))
    recent = d[d["dt"] >= sorted(d["dt"].unique())[-60]]       # dates are text, sorted ISO
    dv = (recent["close"] * recent["volume"]).groupby(recent["symbol"]).median()
    top = list(dv.nlargest(TOP_N).index)
    deciles = ai_deciles(set(top))
    names = list(sg.SIGNALS)
    (OUT / "stocks").mkdir(parents=True, exist_ok=True)
    index = []
    for sym in top:
        g = d[d["symbol"] == sym].reset_index(drop=True)
        r = g["adj_close"] / g["close"]
        o, h, l, c = g["open"] * r, g["high"] * r, g["low"] * r, g["adj_close"]
        sma50 = c.rolling(50, min_periods=50).mean()
        sma200 = c.rolling(200, min_periods=200).mean()
        fired = {}
        for n in names:
            s = sg.compute(g, n).to_numpy(bool)
            if n in STATES:
                s = s & ~np.r_[False, s[:-1]]
            fired[n] = s
        keep = (g["dt"] >= START).to_numpy()
        idx = np.flatnonzero(keep)
        sig_days = {n: [int(i - idx[0]) for i in np.flatnonzero(fired[n] & keep)] for n in names}
        rnd = lambda x: [None if not np.isfinite(v) else round(float(v), 2) for v in x]
        doc = {
            "symbol": sym, "name": g["name"].iloc[0], "sector": g["sector"].iloc[0],
            "dates": g.loc[keep, "dt"].tolist(),
            "open": rnd(o[keep]), "high": rnd(h[keep]), "low": rnd(l[keep]), "close": rnd(c[keep]),
            "volume": [int(v) for v in g.loc[keep, "volume"]],
            "sma50": rnd(sma50[keep]), "sma200": rnd(sma200[keep]),
            "signals": {n: v for n, v in sig_days.items() if v},
            "ai": deciles.get(sym, []),
        }
        (OUT / "stocks" / f"{sym}.json").write_text(json.dumps(doc, separators=(",", ":")))
        index.append({"symbol": sym, "name": doc["name"], "sector": doc["sector"],
                      "last": doc["close"][-1], "first": doc["close"][0]})
    rob = pd.read_csv(ROOT / "reports" / "robustness_ta.csv").set_index("signal")
    sigs = [{"id": n, "label": label(n), "group": grp, "state": n in STATES,
             "trades": int(rob.loc[n, "trades"]),
             "excess": round(float(rob.loc[n, "trimmed"]) * 100, 3),
             "t": round(float(rob.loc[n, "t_trim"]), 2)}
            for grp, ns in GROUPS.items() for n in ns]
    assert sorted(s["id"] for s in sigs) == sorted(names)
    meta = {"stocks": index, "signals": sigs, "start": START, "end": max_date,
            "ai_note": "Charting by Machines model (trial 56), trained on 2015-2022, frozen, "
                       "scored out of sample from 2023-01. Decile 10 = most likely to outperform."}
    (OUT / "index.json").write_text(json.dumps(meta, separators=(",", ":")))
    total = sum(f.stat().st_size for f in (OUT / "stocks").glob("*.json"))
    print(f"{len(index)} stocks, {len(sigs)} signals, {total / 1e6:.1f}MB of stock files, "
          f"{START} to {max_date}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
