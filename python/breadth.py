#!/usr/bin/env python3
"""The Fundamental Law, measured instead of assumed.

    python3 python/breadth.py --horizon 21
    python3 python/breadth.py --horizon 21 --rebuild

IR = IC x sqrt(breadth) gets quoted constantly and almost never measured. Every
earlier run in this project reported IC and reported Sharpe and left the term in
the middle as an article of faith. This measures all three and checks whether the
identity actually holds on this data.

WHAT IS BEING TESTED, DECLARED BEFORE RUNNING

1. BREADTH IS NOT THE NUMBER OF POSITIONS. Five hundred names that all load on
   the market is close to one bet. The honest count is the effective number of
   independent bets, taken from the eigenvalues of the return correlation matrix:

       N_eff = (sum of eigenvalues)^2 / (sum of squared eigenvalues)

   This is the participation ratio. It equals N when names are independent and
   equals 1 when they move as one. It is reported for raw returns and again for
   returns with the market and the sector mean stripped out, because the second
   is what a neutralized book actually trades. The difference between those two
   numbers is how much breadth neutralization manufactures.

2. THE UNIVERSE TRIPLES. Every previous cross-sectional run here used the
   S&P 500 only, 503 names, because the cached panel was built before midcap and
   smallcap were ingested. The database holds 1,500. This rebuilds on all of
   them. Caveat that cannot be fixed: all three constituent lists are as-of
   2026-10, so the small and mid cap additions carry SURVIVORSHIP BIAS in the
   direction that flatters the result. Names that delisted are absent.

3. RESIDUALIZATION IS THE TEST, NOT THE FIX. Each signal is regressed
   cross-sectionally, every day, on the things anyone can buy for nothing:
   market beta, size, 12-month momentum, 6-month momentum, short-term reversal,
   trailing volatility, and sector. What survives is idiosyncratic. A signal
   whose IC dies here was never alpha, it was a risk premium in a costume. The
   earnings sleeve already failed exactly this way, going from 0.34 to -0.17, so
   the price sleeve is included as a CONTROL that is expected to die. If the
   price scores survive residualization against momentum and volatility, the
   residualization is broken, not the signal.

4. PREDICTED VERSUS REALIZED. The law predicts an IR. The book produces a
   Sharpe. Reporting both side by side turns the law into a falsifiable claim
   about this data rather than a slogan, and the gap between them is the toll
   taken by costs and by imperfect construction.

Every signal measured here counts as a trial, raw and residualized separately,
and the count goes to the registry. Measuring on a tripled universe is a new
test of the same idea, not a free re-read of an old one.
"""

from __future__ import annotations

import argparse
import sys

import numpy as np
import pandas as pd

import config
import db
import lockbox
import signals as sg
from alt_signals import SIGNALS as ALT_SIGNALS, attach as attach_alt, \
    clean_insider, insider_panel
from cross_sectional import SCORES as PRICE_SCORES
from earnings_signals import as_of_panel, build_event_features
from fundamental_signals import SIGNALS as FUND_SIGNALS, build_signals, compute
from spread_estimator import liquidity_half_spread_bps

CACHE = config.ROOT / "data" / "breadth_panel.parquet"

# The free stuff. Anyone can own these with an index fund and a screener, so a
# signal only earns the word alpha if it survives having them removed.
FACTORS = ["beta", "size", "mom126", "mom252", "reversal5", "vol63"]

SLEEVES = {
    "price":        list(PRICE_SCORES),
    "earnings":     ["sue", "surprise_pct"],
    "fundamentals": FUND_SIGNALS,
    "alt":          ALT_SIGNALS,
}


def build_panel(max_date: str, horizons: list[int]) -> pd.DataFrame:
    with db.connect() as con:
        bars = pd.read_sql(
            "SELECT b.symbol, b.dt, b.open, b.high, b.low, b.close, b.adj_close, "
            "b.volume, s.sector FROM bars b JOIN symbols s ON s.symbol=b.symbol "
            "WHERE s.kind='stock' AND b.dt <= ? ORDER BY b.symbol, b.dt",
            con, params=(max_date,))
        spy = pd.read_sql(
            "SELECT dt, adj_close FROM bars WHERE symbol='SPY' ORDER BY dt", con)
    print(f"  {len(bars):,} bars, {bars['symbol'].nunique()} symbols")

    spy["mkt"] = spy["adj_close"].pct_change()
    mkt = dict(zip(spy["dt"], spy["mkt"]))

    out = []
    for sym, g in bars.groupby("symbol", sort=False):
        g = g.sort_values("dt").reset_index(drop=True)
        if len(g) < 300:
            continue
        r = g["adj_close"].pct_change()
        m = g["dt"].map(mkt)
        cov = r.rolling(252, min_periods=120).cov(m)
        var = m.rolling(252, min_periods=120).var()
        row = {
            "symbol": g["symbol"], "dt": g["dt"], "sector": g["sector"],
            "ret": r,
            "adv": (g["close"] * g["volume"]).rolling(20, min_periods=20).mean(),
            "vol63": r.rolling(63, min_periods=40).std(),
            "beta": cov / var.replace(0, np.nan),
            "mom126": g["adj_close"] / g["adj_close"].shift(126) - 1.0,
            "mom252": g["adj_close"] / g["adj_close"].shift(252) - 1.0,
            "reversal5": -(g["adj_close"] / g["adj_close"].shift(5) - 1.0),
            "close": g["close"],
        }
        for h in horizons:
            row[f"fwd_{h}"] = g["adj_close"].shift(-h) / g["adj_close"] - 1.0
        for name, (fn, n) in PRICE_SCORES.items():
            row[name] = fn(g, n)
        out.append(pd.DataFrame(row))
    p = pd.concat(out, ignore_index=True)
    p["size"] = np.log(p["adv"].clip(lower=1e5))
    p["half_bps"] = liquidity_half_spread_bps(p["close"], p["adv"])
    return p


def attach_sources(panel: pd.DataFrame) -> pd.DataFrame:
    """Earnings, as-filed fundamentals, insider and short interest."""
    with db.connect() as con:
        ev = pd.read_sql(
            "SELECT symbol, tradeable_from, surprise_pct FROM earnings "
            "WHERE surprise_pct IS NOT NULL ORDER BY symbol, tradeable_from", con)
        facts = pd.read_sql(
            "SELECT symbol, concept, period_start, period_end, filed, val, form "
            "FROM fundamentals", con)
        trades = pd.read_sql(
            "SELECT symbol, filing_date, trans_date, trans_code, shares, price "
            "FROM insider_trades WHERE trans_code IN ('P','S')", con)
        px = pd.read_sql("SELECT symbol, dt, close FROM bars", con)
        sess = pd.read_sql("SELECT dt, day_index FROM trading_days ORDER BY dt", con)
        si = pd.read_sql("SELECT symbol, tradeable_from, short_shares, prev_short, "
                         "avg_daily_vol, days_to_cover FROM short_interest", con)

    merged = as_of_panel(build_event_features(ev), panel[["symbol", "dt"]].copy())
    panel = panel.merge(merged[["symbol", "dt", "sue", "surprise_pct"]],
                        on=["symbol", "dt"], how="left")
    print(f"  earnings attached, sue coverage {panel['sue'].notna().mean():.0%}")

    panel = compute(build_signals(facts), panel)
    print("  fundamentals coverage:", "  ".join(
        f"{k[:9]}={v:.0%}" for k, v in panel[FUND_SIGNALS].notna().mean().items()))

    trades = clean_insider(trades, px)
    ins = insider_panel(trades, sess, panel[["symbol", "dt"]])
    panel = attach_alt(panel, ins, si)
    print("  alt coverage:", "  ".join(
        f"{k.split('_')[0][:6]}={v:.0%}"
        for k, v in panel[ALT_SIGNALS].notna().mean().items()))
    return panel


def get_panel(max_date: str, horizons: list[int], rebuild: bool) -> pd.DataFrame:
    if CACHE.exists() and not rebuild:
        p = pd.read_parquet(CACHE)
        if all(f"fwd_{h}" in p.columns for h in horizons):
            print(f"  cached panel: {len(p):,} rows, {p['symbol'].nunique()} symbols")
            return p
    print("  building panel (several minutes)")
    p = build_panel(max_date, horizons)
    p = attach_sources(p)
    p.to_parquet(CACHE, index=False)
    print(f"  wrote {CACHE.name}")
    return p


def zs(s: pd.Series, keys) -> pd.Series:
    g = s.groupby(keys)
    return (s - g.transform("mean")) / g.transform("std").replace(0, np.nan)


def zs(s: pd.Series, keys) -> pd.Series:
    g = s.groupby(keys)
    return ((s - g.transform("mean")) / g.transform("std").replace(0, np.nan)
            ).astype("float32")


class Resid:
    """Daily cross-sectional residualizer that holds one design matrix.

    An earlier version residualized into a full 32-column frame and concatenated
    it onto a 4.4M row panel. That peaked past the 8GB on this machine and the
    process was killed partway through the analysis, after the panel build had
    already succeeded. So the design matrix is built once, the per-date row
    indices are cached once, and each signal is residualized into a single
    float32 vector that is scored and then dropped.
    """

    def __init__(self, d: pd.DataFrame, factors: list[str]):
        sec = pd.get_dummies(d["sector"], prefix="s", drop_first=True, dtype="float32")
        self.X = np.column_stack([
            np.ones(len(d), dtype=np.float32),
            d[factors].to_numpy(dtype=np.float32),
            sec.to_numpy(dtype=np.float32),
        ])
        self.base_ok = np.isfinite(self.X).all(axis=1)
        self.groups = [idx for idx in d.groupby("dt", sort=True).indices.values()]
        self.n = len(d)
        del sec

    def of(self, y: np.ndarray) -> np.ndarray:
        out = np.full(self.n, np.nan, dtype=np.float32)
        for idx in self.groups:
            ok = idx[self.base_ok[idx] & np.isfinite(y[idx])]
            if len(ok) < 40:
                continue
            A = self.X[ok]
            keep = A.std(axis=0) > 0
            keep[0] = True
            A = A[:, keep]
            yy = y[ok]
            b, *_ = np.linalg.lstsq(A.astype(np.float64), yy.astype(np.float64),
                                    rcond=None)
            out[ok] = yy - (A.astype(np.float64) @ b).astype(np.float32)
        return out


def rank_ic(score: np.ndarray, fwd: np.ndarray, groups: list[np.ndarray],
            min_names: int = 50) -> np.ndarray:
    """Spearman rank IC per date. Ranks, not levels, so one outlier cannot
    decide the day."""
    out = []
    for idx in groups:
        ok = idx[np.isfinite(score[idx]) & np.isfinite(fwd[idx])]
        if len(ok) < min_names:
            out.append(np.nan)
            continue
        a = pd.Series(score[ok]).rank().to_numpy()
        b = pd.Series(fwd[ok]).rank().to_numpy()
        sa, sb = a.std(), b.std()
        out.append(np.nan if sa == 0 or sb == 0
                   else float(np.corrcoef(a, b)[0, 1]))
    return np.array(out, dtype=float)


def book_returns(score: np.ndarray, fwd: np.ndarray, vol: np.ndarray,
                 cost: np.ndarray, sec_codes: np.ndarray,
                 groups: list[np.ndarray]) -> tuple[np.ndarray, np.ndarray]:
    """Sector-neutral, dollar-neutral, risk-weighted book. Gross and net.

    Weight is the sector-demeaned score divided by trailing volatility, then each
    sector is scaled to equal gross so no sector can dominate, then the whole
    book is scaled to unit gross so the Sharpe is not a leverage artifact.
    """
    g_out, n_out = [], []
    for idx in groups:
        ok = idx[np.isfinite(score[idx]) & np.isfinite(fwd[idx])
                 & np.isfinite(vol[idx]) & (vol[idx] > 0)]
        if len(ok) < 50:
            g_out.append(np.nan)
            n_out.append(np.nan)
            continue
        sc, f, v, c, sx = score[ok], fwd[ok], vol[ok], cost[ok], sec_codes[ok]
        w = np.zeros(len(ok))
        for u in np.unique(sx):
            m = sx == u
            if m.sum() < 2:
                continue
            x = sc[m] - sc[m].mean()
            x = x / v[m]
            gs = np.abs(x).sum()
            if gs > 0:
                w[m] = x / gs
        tot = np.abs(w).sum()
        if tot == 0:
            g_out.append(np.nan)
            n_out.append(np.nan)
            continue
        w = w / tot
        g_out.append(float((w * f).sum()))
        n_out.append(float((w * f).sum() - (np.abs(w) * c).sum()))
    return np.array(g_out), np.array(n_out)


def _participation_ratio(M: np.ndarray) -> float:
    """N_eff = (sum lambda)^2 / sum(lambda^2) on the correlation matrix.

    Equals N for independent columns, 1 when they move as one. Because the
    eigenvalues of a correlation matrix sum to N, this reduces to N^2/sum(l^2),
    so a single large market eigenvalue caps it hard no matter how many names
    are added. That cap is the thing worth measuring.
    """
    C = np.corrcoef(M, rowvar=False)
    lam = np.linalg.eigvalsh(C)
    lam = lam[lam > 1e-10]
    return float(lam.sum() ** 2 / (lam ** 2).sum())


def effective_breadth(d: pd.DataFrame, seed: int = config.RANDOM_SEED) -> dict:
    """Effective number of independent bets, and how it scales with universe size.

    Two fixes over the first attempt. It dropped every DATE on which any name was
    missing, which with 1,486 names of unequal history left 90 names and made the
    whole measurement meaningless. And a correlation matrix needs more rows than
    columns or its eigenvalues are rank-deficient noise that overstates
    independence, so the panel is restricted to names with COMPLETE history
    (T=3,190 observations against N<1,000, a ratio near four).

    The saturation curve is the point. If the participation ratio flattens as N
    grows, then tripling the nominal universe buys almost no breadth, and the
    Fundamental Law's square-root term cannot be bought with more tickers.
    """
    w = d[["symbol", "dt", "ret", "sector"]].dropna().copy()
    w["resid"] = w["ret"] - w.groupby(["dt", "sector"])["ret"].transform("mean")
    n_dates = w["dt"].nunique()
    full = w.groupby("symbol")["ret"].count()
    keep = full[full == n_dates].index
    w = w[w["symbol"].isin(keep)]
    R = w.pivot(index="dt", columns="symbol", values="ret").to_numpy(dtype=float)
    E = w.pivot(index="dt", columns="symbol", values="resid").to_numpy(dtype=float)
    N, T = R.shape[1], R.shape[0]

    rng = np.random.default_rng(seed)
    curve = []
    for n in [50, 100, 200, 400, 800, N]:
        if n > N:
            continue
        cols = rng.choice(N, size=n, replace=False) if n < N else np.arange(N)
        curve.append({"n": n,
                      "pr_raw": _participation_ratio(R[:, cols]),
                      "pr_resid": _participation_ratio(E[:, cols])})
    return {"n_names": N, "n_dates": T,
            "n_eff_raw": curve[-1]["pr_raw"],
            "n_eff_resid": curve[-1]["pr_resid"],
            "curve": curve}


def autocorr(score: np.ndarray, d: pd.DataFrame, h: int) -> float:
    """Correlation of a name's score with its own score h sessions later.

    Near 1 means consecutive non-overlapping rebalances are the same bet
    restated, so 252/h overstates independent decisions per year.
    """
    t = pd.DataFrame({"sym": d["symbol"].to_numpy(), "v": score})
    lag = t.groupby("sym")["v"].shift(h)
    ok = np.isfinite(t["v"]) & np.isfinite(lag)
    if ok.sum() < 1000:
        return float("nan")
    return float(np.corrcoef(t["v"][ok], lag[ok])[0, 1])


def sharpe(r: np.ndarray, h: int) -> tuple[float, float, int]:
    """Annualized Sharpe on NON-OVERLAPPING periods only.

    Consecutive dates share h-1 days of the same forward return. Treating them
    as independent is how this project once reported a 100% drawdown.
    """
    s = r[::h]
    s = s[np.isfinite(s)]
    if len(s) < 3 or s.std(ddof=1) == 0:
        return float("nan"), float("nan"), len(s)
    return (float(s.mean() / s.std(ddof=1) * np.sqrt(252 / h)),
            float(s.mean() / (s.std(ddof=1) / np.sqrt(len(s)))),
            len(s))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--horizon", type=int, default=21)
    ap.add_argument("--min-adv", type=float, default=5e6)
    ap.add_argument("--rebuild", action="store_true")
    args = ap.parse_args()
    h = args.horizon

    st = lockbox.state()
    max_date = (pd.Timestamp(st["seal_from"]) - pd.Timedelta(days=1)).date().isoformat() \
        if st["sealed"] else config.END_DATE.isoformat()
    lockbox.guard(max_date, "breadth")
    print(f"data through {max_date}, horizon {h}d\n")

    sig_cols = [c for v in SLEEVES.values() for c in v]
    # vol63 is both a risk weight and a factor, so dedupe while keeping order:
    # a duplicated name makes panel[c] return a frame instead of a column.
    need = list(dict.fromkeys(
        ["symbol", "dt", "sector", "ret", "adv", "vol63", "half_bps", f"fwd_{h}"]
        + FACTORS + sig_cols))

    if args.rebuild or not CACHE.exists():
        panel = get_panel(max_date, [h], args.rebuild)
    else:
        import pyarrow.compute as pc
        import pyarrow.parquet as pq
        have = set(pq.ParquetFile(CACHE).schema_arrow.names)
        t = pq.read_table(CACHE, columns=[c for c in need if c in have])
        t = t.filter(pc.greater_equal(t["adv"], args.min_adv))
        panel = t.to_pandas()
        del t
        print(f"  cached panel: {len(panel):,} rows, "
              f"{panel['symbol'].nunique()} symbols")

    panel = panel[panel["adv"] >= args.min_adv]
    assert not panel.columns.duplicated().any(), "duplicate columns in panel"
    f64 = [c for c in panel.columns if panel[c].dtype == "float64"]
    panel[f64] = panel[f64].astype("float32")
    panel = panel.sort_values(["dt", "symbol"]).reset_index(drop=True)
    npd = panel.groupby("dt").size()
    print(f"  after ADV filter: {len(panel):,} rows, "
          f"{panel['symbol'].nunique()} symbols, "
          f"median {npd.median():.0f} names/day  (old panel: 503 symbols)")

    print("\nmeasuring effective breadth (correlation eigenvalues)")
    eb = effective_breadth(panel)
    print(f"  names with complete history        {eb['n_names']:>7}"
          f"   over {eb['n_dates']} dates")
    print(f"\n  {'universe N':>11}{'N_eff raw':>11}{'N_eff neutral':>15}"
          f"{'% of N':>9}")
    for c in eb["curve"]:
        print(f"  {c['n']:>11}{c['pr_raw']:>11.1f}{c['pr_resid']:>15.1f}"
              f"{c['pr_resid'] / c['n']:>9.1%}")
    print(f"\n  effective bets, raw returns        {eb['n_eff_raw']:>7.1f}")
    print(f"  effective bets, sector-neutral     {eb['n_eff_resid']:>7.1f}")
    breadth = eb["n_eff_resid"] * (252 / h)
    print(f"  breadth/yr = {eb['n_eff_resid']:.1f} x {252 / h:.0f} rebalances"
          f" = {breadth:.0f}")

    # Sleeve composites from signals standardized within date and sector.
    cols, labels = [], []
    for sleeve, members in SLEEVES.items():
        members = [m for m in members if m in panel.columns]
        if not members:
            continue
        zc = []
        for m in members:
            panel[f"z_{m}"] = zs(panel[m], [panel["dt"], panel["sector"]])
            zc.append(f"z_{m}")
            cols.append(f"z_{m}")
            labels.append((sleeve, m))
        panel[f"c_{sleeve}"] = panel[zc].mean(axis=1, skipna=True).astype("float32")
        cols.append(f"c_{sleeve}")
        labels.append((sleeve, f"c_{sleeve}"))
    # Equal weight BY SLEEVE, so the 13-signal price sleeve cannot outvote the
    # 2-signal earnings sleeve on count alone.
    sc = [f"c_{k}" for k in SLEEVES if f"c_{k}" in panel.columns]
    panel["c_all"] = panel[sc].mean(axis=1, skipna=True).astype("float32")
    cols.append("c_all")
    labels.append(("combined", "c_all"))

    print(f"\nresidualizing {len(cols)} signals against "
          f"{', '.join(FACTORS)} + sector")
    R = Resid(panel, FACTORS)
    groups = R.groups
    fwd = panel[f"fwd_{h}"].to_numpy(dtype=float)
    vol = panel["vol63"].to_numpy(dtype=float)
    cost = (2 * panel["half_bps"].to_numpy(dtype=float) / 10_000)
    sec_codes = pd.factorize(panel["sector"])[0]

    rows = []
    for (sleeve, name), col in zip(labels, cols):
        raw = panel[col].to_numpy(dtype=float)
        res = R.of(panel[col].to_numpy(dtype=np.float32)).astype(float)
        for tag, y in (("raw", raw), ("resid", res)):
            ic = rank_ic(y, fwd, groups)
            icv = ic[np.isfinite(ic)]
            if len(icv) < 20:
                continue
            nono = ic[::h]
            nono = nono[np.isfinite(nono)]
            ict = (float(nono.mean() / (nono.std(ddof=1) / np.sqrt(len(nono))))
                   if len(nono) > 2 and nono.std(ddof=1) > 0 else np.nan)
            g, n = book_returns(y, fwd, vol, cost, sec_codes, groups)
            sg, _, _ = sharpe(g, h)
            sn, tn, nobs = sharpe(n, h)
            rows.append({
                "sleeve": sleeve, "signal": name, "variant": tag,
                "ic": float(icv.mean()), "ic_t": ict,
                "rho_h": autocorr(y, panel, h), "breadth": breadth,
                "ir_predicted": float(icv.mean()) * np.sqrt(breadth),
                "sharpe_gross": sg, "sharpe_net": sn, "t_net": tn, "n_obs": nobs,
            })
        del raw, res

    res = pd.DataFrame(rows)
    (config.ROOT / "reports").mkdir(exist_ok=True)
    res.to_csv(config.ROOT / "reports" / "breadth.csv", index=False)

    f = lambda v, s="{:.3f}": s.format(v) if pd.notna(v) else "      -"
    print(f"\n{'=' * 100}")
    print(f"IC, BREADTH, IR  —  horizon {h}d, sector-neutral risk-weighted, "
          f"net of per-name costs")
    print("=" * 100)
    print(f"{'sleeve':<13}{'signal':<23}{'var':<6}{'IC':>8}{'IC t':>7}"
          f"{'rho':>7}{'IRpred':>8}{'Shp gr':>8}{'Shp net':>9}{'t':>7}")
    for sl in list(SLEEVES) + ["combined"]:
        sub = res[res.sleeve == sl]
        if sub.empty:
            continue
        for _, r in sub.iterrows():
            mark = "  <<" if str(r.signal).startswith("c_") else ""
            print(f"{r.sleeve:<13}{str(r.signal):<23}{r.variant:<6}{f(r['ic']):>8}"
                  f"{f(r['ic_t'],'{:.2f}'):>7}{f(r['rho_h'],'{:.2f}'):>7}"
                  f"{f(r['ir_predicted'],'{:.2f}'):>8}{f(r['sharpe_gross'],'{:.2f}'):>8}"
                  f"{f(r['sharpe_net'],'{:.2f}'):>9}{f(r['t_net'],'{:.2f}'):>7}{mark}")
        print()

    print(f"{len(res)} signal-variant measurements, declared in the registry as "
          f"trial 23 with 64 variants BEFORE this ran.")
    print("  python3 python/registry.py status")
    print("\nwrote reports/breadth.csv")
    return 0


if __name__ == "__main__":
    sys.exit(main())
