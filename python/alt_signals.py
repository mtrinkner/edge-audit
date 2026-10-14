"""Insider buying and short interest: signals from decisions, not from prices.

Every signal before these was a function of past prices, which is why the best
book residualized to -0.17 against beta, size and momentum. These two measure
something else: what the people running the company did with their own money,
and how much of the float the short side has committed against.

POINT-IN-TIME KEYS. Insider trades key on FILING_DATE, not transaction date: a
Form 4 is due within two business days, and the market cannot react before it
appears. Short interest keys on a settlement date plus a conservative ten-session
publication lag, because FINRA disseminates roughly eight business days after
settlement and the raw feed does not carry the publication date at all.

TRADES ARE VALUED AT MARKET PRICE, NOT THE REPORTED PRICE. The Form 4 price
field is free text and occasionally holds a total, a footnote marker, or a units
error. Taking it literally produces a single AMD "sale" of 40 million shares at
$525,600,000 each, which is $21 quadrillion and roughly two hundred times world
GDP. Half a percent of trades exceed $100m on the reported figures. Valuing every
trade at that day's actual close removes the entire class of error and is closer
to what the position was worth anyway.

ORIENTATIONS, FROM THE LITERATURE, FIXED BEFORE TESTING:
  insider purchases   positive. Lakonishok & Lee 2001, Jeng/Metrick/Zeckhauser
                      2003: purchases predict returns, sales largely do not,
                      because selling is mostly diversification and liquidity.
  short interest      negative. Boehmer, Jones & Zhang: heavily shorted names
                      underperform.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

LOOKBACK_SESSIONS = 90
SIGNALS = ["insider_buy_intensity", "insider_net_flow", "insider_buyer_breadth",
           "short_ratio", "short_change", "days_to_cover"]
ORIENTATION = {"insider_buy_intensity": +1, "insider_net_flow": +1,
               "insider_buyer_breadth": +1, "short_ratio": -1,
               "short_change": -1, "days_to_cover": -1}


def clean_insider(trades: pd.DataFrame, bars: pd.DataFrame) -> pd.DataFrame:
    """Value every trade at the market close on its transaction date."""
    px = bars[["symbol", "dt", "close"]].rename(columns={"dt": "trans_date",
                                                         "close": "mkt_price"})
    t = trades.merge(px, on=["symbol", "trans_date"], how="left")
    # Fall back to the reported price only when it is within a plausible band of
    # nothing at all; otherwise the row is dropped rather than guessed at.
    t["usd"] = t["shares"] * t["mkt_price"]
    before = len(t)
    t = t.dropna(subset=["usd"])
    t = t[(t["usd"] > 0) & (t["shares"] > 0)]
    # A filing cannot predate its own transaction.
    t = t[t["filing_date"] >= t["trans_date"]]
    return t


def insider_panel(trades: pd.DataFrame, sessions: pd.DataFrame,
                  panel_keys: pd.DataFrame) -> pd.DataFrame:
    """Trailing-window insider aggregates, as known on each session."""
    idx = dict(zip(sessions["dt"], sessions["day_index"]))
    t = trades.copy()
    t["fi"] = t["filing_date"].map(idx)
    t = t.dropna(subset=["fi"])
    t["fi"] = t["fi"].astype(int)
    t["buy_usd"] = np.where(t["trans_code"] == "P", t["usd"], 0.0)
    t["sell_usd"] = np.where(t["trans_code"] == "S", t["usd"], 0.0)
    t["is_buy"] = (t["trans_code"] == "P").astype(int)

    k = panel_keys.copy()
    k["di"] = k["dt"].map(idx)
    k = k.dropna(subset=["di"])
    k["di"] = k["di"].astype(int)

    out = []
    for sym, g in t.groupby("symbol", sort=False):
        kk = k[k["symbol"] == sym]
        if kk.empty:
            continue
        g = g.sort_values("fi")
        fi = g["fi"].to_numpy()
        # Rolling sums over the trailing window, by day index.
        cb = np.concatenate([[0.0], np.cumsum(g["buy_usd"].to_numpy())])
        cs = np.concatenate([[0.0], np.cumsum(g["sell_usd"].to_numpy())])
        cn = np.concatenate([[0.0], np.cumsum(g["is_buy"].to_numpy())])
        d = kk["di"].to_numpy()
        hi = np.searchsorted(fi, d, side="right")          # strictly <= today
        lo = np.searchsorted(fi, d - LOOKBACK_SESSIONS, side="left")
        out.append(pd.DataFrame({
            "symbol": sym, "dt": kk["dt"].to_numpy(),
            "buy_usd": cb[hi] - cb[lo],
            "sell_usd": cs[hi] - cs[lo],
            "n_buys": cn[hi] - cn[lo],
        }))
    return pd.concat(out, ignore_index=True) if out else pd.DataFrame()


def attach(panel: pd.DataFrame, ins: pd.DataFrame, si: pd.DataFrame) -> pd.DataFrame:
    p = panel.merge(ins, on=["symbol", "dt"], how="left")
    for c in ("buy_usd", "sell_usd", "n_buys"):
        p[c] = p[c].fillna(0.0)

    # Scale by dollar volume so a $1m purchase in a small name outranks the same
    # purchase in a mega-cap, which is the economically meaningful comparison.
    scale = (p["adv"] * LOOKBACK_SESSIONS).replace(0, np.nan)
    p["insider_buy_intensity"] = p["buy_usd"] / scale
    p["insider_net_flow"] = (p["buy_usd"] - p["sell_usd"]) / scale
    p["insider_buyer_breadth"] = p["n_buys"]

    if not si.empty:
        s = si.copy()
        s["_t"] = pd.to_datetime(s["tradeable_from"])
        q = p[["symbol", "dt"]].copy()
        q["_d"] = pd.to_datetime(q["dt"])
        m = pd.merge_asof(q.sort_values("_d"), s.sort_values("_t"),
                          left_on="_d", right_on="_t", by="symbol",
                          direction="backward", allow_exact_matches=True)
        m["short_ratio"] = m["short_shares"] / (m["avg_daily_vol"].replace(0, np.nan) * 21)
        m["short_change"] = (m["short_shares"] - m["prev_short"]) / \
                            m["prev_short"].replace(0, np.nan)
        p = p.merge(m[["symbol", "dt", "short_ratio", "short_change", "days_to_cover"]],
                    on=["symbol", "dt"], how="left")
    for c in SIGNALS:
        if c in p.columns:
            p[c] = ORIENTATION[c] * p[c]
    return p
