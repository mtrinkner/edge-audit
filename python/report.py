"""The standard evaluation. See docs/INFRASTRUCTURE.md.

The primary t follows the study's kind, as G-REAL does (python/gate.py):
- an event trade: the event-level mean, standard errors clustered by month;
- a portfolio: the calendar-time mean of monthly returns, Newey-West t.

event_table() gives, for a payoff per event: events, mean, median, win
rate, 10th percentile, worst, SD, top-5 share, t, clusters, and $/yr over
the years each row covers. It does this for every row: all events, without
the top 5, and each year.

nav_report() gives, from a simulator NAV (sim.Sim): monthly excess returns,
Newey-West t, Sharpe, maximum drawdown, turnover, and capacity used. With
factors, it adds FF5+UMD alpha, the market beta and the down-market beta.
"""

from __future__ import annotations

import warnings

import numpy as np
import pandas as pd

import evaluation as ev

DROP_TOP = 5
KINDS = ("event", "portfolio")


def years_between(lo: str, hi: str) -> float:
    """Whole calendar years from lo to hi (2019-01-01..2025-12-31 is 7)."""
    return int(hi[:4]) - int(lo[:4]) + 1


def event_stats(x: pd.Series, month: pd.Series, capital: pd.Series | None = None) -> dict:
    """Shape and clustered t of one row's payoffs."""
    x = pd.Series(x, dtype=float)
    n = int(x.notna().sum())
    out = {"events": n, "mean": float(x.mean()) if n else np.nan, "median": float(x.median()) if n else np.nan,
           "win_rate": float((x > 0).mean()) if n else np.nan,
           "p10": float(x.quantile(0.1)) if n else np.nan, "worst": float(x.min()) if n else np.nan,
           "sd": float(x.std(ddof=1)) if n > 1 else np.nan,
           "top5_share": float(x.nlargest(DROP_TOP).sum() / x[x > 0].sum()) if (x > 0).any() else np.nan}
    if capital is not None and n:
        out["return_on_capital_median"] = float((x / capital.loc[x.index]).median())
    if n >= 2:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            c = ev.clustered_mean(x, month.loc[x.index])
        out.update(t=float(c["t"]), p_one=float(c["p_one"]), clusters=int(c["clusters"]))
    else:
        out.update(t=np.nan, p_one=np.nan, clusters=n)
    return out


def event_table(pnl: pd.Series, event_date: pd.Series, period: tuple[str, str],
                capital: pd.Series | None = None, extra: dict | None = None) -> pd.DataFrame:
    """Rows: all, drop_top_5, each year, and any `extra` {label: boolean mask};
    every row's $/yr divides by the years it covers."""
    lo, hi = period
    d = pd.Series(event_date).astype(str)
    month = d.str[:7]
    rows = []

    def row(label: str, mask: pd.Series, years: float) -> None:
        x = pnl[mask]
        s = event_stats(x, month, capital)
        s["years_covered"] = years
        s["usd_per_yr"] = float(x.sum() / years) if len(x) else 0.0
        rows.append({"row": label, **s})

    allm = pd.Series(True, index=pnl.index)
    row("all", allm, years_between(lo, hi))
    keep = pnl.drop(pnl.nlargest(DROP_TOP).index).index
    row(f"drop_top_{DROP_TOP}", pnl.index.isin(keep), years_between(lo, hi))
    for label, (mask, years) in (extra or {}).items():
        row(label, mask, years)
    for y in sorted(d.str[:4].unique()):
        row(f"year_{y}", d.str[:4] == y, 1)
    return pd.DataFrame(rows)


def primary_t(kind: str, pnl: pd.Series | None = None, event_date: pd.Series | None = None,
              monthly: pd.Series | None = None) -> dict:
    """The statistic the trial declares, by kind."""
    if kind == "event":
        c = ev.clustered_mean(pnl, pd.Series(event_date).astype(str).str[:7])
        return {"statistic": "event-level t, clustered by month", "t": float(c["t"]), "clusters": int(c["clusters"])}
    if kind == "portfolio":
        c = ev.newey_west_t(monthly)
        return {"statistic": "calendar-time monthly t, Newey-West", "t": float(c["t"]), "months": int(c["n"])}
    raise ValueError(f"kind is one of {KINDS}")


def nav_report(nav: pd.DataFrame, trades: pd.DataFrame | None = None, rf_daily: pd.Series | None = None,
               factors: pd.DataFrame | None = None) -> dict:
    """From a simulator NAV (columns day, nav, market_value). rf_daily: daily
    T-bill returns by day. factors: daily FF5+UMD (Mkt-RF first) by day."""
    n = nav.set_index("day")["nav"]
    r = n.pct_change().dropna()
    rf = rf_daily.reindex(r.index).fillna(0.0) if rf_daily is not None else 0.0 * r
    m = (1 + r).groupby(r.index.str[:7]).prod() - 1
    mrf = (1 + rf).groupby(rf.index.str[:7]).prod() - 1
    ex = m - mrf
    nw = ev.newey_west_t(ex)
    dd = float((n / n.cummax() - 1).min())
    out = {"months": int(len(ex)), "mean_monthly_excess": float(ex.mean()), "t_nw": nw["t"],
           "sharpe": float(ex.mean() / ex.std(ddof=1) * np.sqrt(12)) if ex.std(ddof=1) > 0 else np.nan,
           "max_drawdown": dd,
           "capacity_used": float((nav["market_value"] / nav["nav"]).mean())}
    if trades is not None and len(trades):
        notional = (trades["qty"] * trades["entry_px"]).sum() + (trades["qty"] * trades["exit_px"]).fillna(0).sum()
        out["turnover_per_year"] = float(notional / n.mean() / (len(n) / 252))
    if factors is not None:
        dex = r - rf
        fa = ev.factor_alpha(dex, factors, periods_per_year=252)
        mkt = factors.columns[0]
        down = factors[mkt].reindex(dex.index) < 0
        db = ev.factor_alpha(dex[down], factors.loc[down[down].index, [mkt]], periods_per_year=252)
        out.update(alpha_annual=fa["alpha_annual"], alpha_t=fa["alpha_t"], beta=fa["coef"][mkt],
                   down_beta=db["coef"][mkt], factor_r2=fa["r2"])
    return out
