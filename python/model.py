#!/usr/bin/env python3
"""Phase 4 — walk-forward modeling with honest evaluation.

    python3 python/model.py
    python3 python/model.py --models logistic gbm --no-write

Reports every model against two baselines, because a classifier compared only to
zero is a classifier whose result cannot be interpreted:

  always_long    predict "up" every time. This is the base rate, and on a market
                 that drifted upward it is already right about 54% of the time.
  momentum_rule  a plain trading rule (positive 21-day momentum and price above
                 the 200-day average). The model has to beat the rule, not just
                 the coin, or the machine learning added nothing.

FEATURE SELECTION NOTE. Only stationary, scale-free features are used. Raw price
levels, moving-average levels, and share volume are deliberately excluded: a
model trained on AAPL at $25 in 2015 and asked about AAPL at $250 in 2025 is
being asked to extrapolate outside everything it saw, and tree models cannot.
Every feature here is a ratio, a return, a rank, or a flag.

The metric that decides the question is not accuracy. It is the mean net forward
return of the rows the model ranks highest, because that is what a trader would
actually capture. A model can be more accurate and less profitable at the same
time by being right about small moves and wrong about large ones.
"""

from __future__ import annotations

import argparse
import json
import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

import config
import db
import folds as foldmod

warnings.filterwarnings("ignore", category=UserWarning)

# Stationary features only. See the note in the docstring.
FEATURES = [
    "ret_1d", "gap_pct", "range_pct",
    "dist_sma_20", "dist_sma_50", "dist_sma_200",
    "rel_volume", "dist_52w_high", "dist_52w_low", "atr_pct",
    "mom_5d", "mom_21d", "mom_63d", "mom_126d",
    "vol_20_annual", "regime_bull", "above_200",
    "mkt_ret_1d", "mkt_vol_20", "mkt_dist_sma_200", "mkt_regime_bull",
    "mom_21d_xs_rank", "rel_volume_xs_rank", "vol_20_xs_rank",
]
TARGET = "label_win_5d"
RET = "fwd_ret_5d_net"
TOP_DECILE = 0.10


def load_panel() -> pd.DataFrame:
    cols = ", ".join(["symbol", "dt", "day_index", TARGET, RET, "fwd_ret_5d"] + FEATURES)
    with db.connect() as con:
        df = pd.read_sql(f"SELECT {cols} FROM v_model_rows ORDER BY dt, symbol", con)
    return df


# --------------------------------------------------------------------- metrics
def evaluate(y: np.ndarray, prob: np.ndarray, ret: np.ndarray,
             dates: np.ndarray, horizon: int = config.LABEL_HORIZON_DAYS) -> dict:
    """Statistical and economic metrics, with the clustering correction.

    THE CORRECTION THIS FUNCTION EXISTS FOR. A first version of this file scored
    the top decile by treating every selected row as an independent trade. That
    produced an annualized Sharpe of 1.59 and seven positive folds out of seven.
    It was wrong, for two reasons that compound:

      1. CROSS-SECTIONAL CLUSTERING. The strongest features are market-level and
         identical across symbols on a given date, so the model's high-confidence
         rows arrive in bursts. In the worst fold, 1,342 selected rows fell on
         only 52 distinct dates, with 41% of them on just ten dates. Fifty-five
         simultaneous positions in one market regime is one bet, not fifty-five.

      2. TEMPORAL OVERLAP. The label spans 5 trading days, so rows one day apart
         share four of five days of outcome. Consecutive observations are not
         independent draws.

    So the economic metrics here are computed on a PORTFOLIO RETURN SERIES: on
    each date, equal-weight whatever was selected to get one return for that
    date, then evaluate that series. The Sharpe is reported on non-overlapping
    dates only, sampled every `horizon` sessions, and the effective sample size
    is reported next to it so nobody reads the row count as the sample size.
    """
    from sklearn.metrics import roc_auc_score, brier_score_loss

    out: dict = {"n": int(len(y))}
    out["base_rate"] = float(y.mean())
    pred = (prob >= 0.5).astype(int)
    out["accuracy"] = float((pred == y).mean())
    majority = 1 if y.mean() >= 0.5 else 0
    out["accuracy_majority"] = float((majority == y).mean())
    out["accuracy_lift"] = out["accuracy"] - out["accuracy_majority"]
    try:
        out["auc"] = float(roc_auc_score(y, prob))
    except ValueError:
        out["auc"] = None
    out["brier"] = float(brier_score_loss(y, np.clip(prob, 0, 1)))

    k = max(1, int(len(prob) * TOP_DECILE))
    idx = np.argsort(-prob)[:k]
    sel = pd.DataFrame({"dt": dates[idx], "ret": ret[idx], "y": y[idx]})

    out["top_decile_n"] = int(k)
    out["top_decile_precision"] = float(sel["y"].mean())
    out["all_mean_net_ret"] = float(ret.mean())
    # Kept for comparison, and labeled as the naive figure it is.
    out["top_decile_mean_net_ret_naive"] = float(sel["ret"].mean())

    # ---- portfolio series: one observation per date, equal weight within date
    per_date = sel.groupby("dt")["ret"].agg(["mean", "size"]).sort_index()
    out["top_decile_dates"] = int(len(per_date))
    out["dates_available"] = int(len(np.unique(dates)))
    out["pct_dates_traded"] = float(len(per_date) / max(1, out["dates_available"]))
    out["median_picks_per_date"] = float(per_date["size"].median())
    out["max_picks_per_date"] = int(per_date["size"].max())
    # How concentrated the bet is: share of selections on the ten busiest dates.
    out["conc_top10_dates"] = float(
        per_date["size"].nlargest(10).sum() / max(1, per_date["size"].sum()))

    out["top_decile_mean_net_ret"] = float(per_date["mean"].mean())
    out["pct_dates_positive"] = float((per_date["mean"] > 0).mean())
    out["top_decile_edge_bps"] = float(
        (per_date["mean"].mean() - ret.mean()) * 10_000)

    # ---- effective sample size and an honest Sharpe
    out["n_effective"] = float(len(per_date) / horizon)
    nonoverlap = per_date["mean"].to_numpy()[::horizon]
    out["n_nonoverlapping"] = int(len(nonoverlap))
    if len(nonoverlap) > 2 and nonoverlap.std(ddof=1) > 0:
        periods_per_year = 252.0 / horizon
        out["sharpe_nonoverlap"] = float(
            nonoverlap.mean() / nonoverlap.std(ddof=1) * np.sqrt(periods_per_year))
        # Plain t statistic on the non-overlapping series. With a handful of dozen
        # observations this is the number that decides whether anything was found.
        out["t_stat"] = float(
            nonoverlap.mean() / (nonoverlap.std(ddof=1) / np.sqrt(len(nonoverlap))))
    else:
        out["sharpe_nonoverlap"] = None
        out["t_stat"] = None

    # The naive Sharpe the first version reported, kept so the gap is visible.
    sd = sel["ret"].std(ddof=1)
    out["sharpe_naive_per_row"] = (
        float(sel["ret"].mean() / sd * np.sqrt(252.0 / horizon)) if sd > 0 else None)
    return out


# --------------------------------------------------------------------- models
def fit_predict(name: str, tr: pd.DataFrame, te: pd.DataFrame, seed: int) -> np.ndarray:
    """Return predicted probabilities for the test fold.

    Imputation and scaling are fit on the TRAINING fold only. Fitting a scaler on
    the full panel leaks the test period's distribution into training, which is a
    subtle leak that survives most code review.
    """
    Xtr, Xte = tr[FEATURES].to_numpy(float), te[FEATURES].to_numpy(float)
    ytr = tr[TARGET].to_numpy(int)

    if name == "always_long":
        return np.full(len(te), 0.51)

    if name == "momentum_rule":
        # Deterministic rule, not fitted. Probability is 0.6 when the rule fires
        # and 0.4 when it does not, which gives a usable ranking for AUC while
        # keeping the decision itself binary.
        fires = (te["mom_21d"].to_numpy(float) > 0) & (te["above_200"].to_numpy(float) > 0.5)
        return np.where(fires, 0.60, 0.40)

    from sklearn.impute import SimpleImputer
    from sklearn.preprocessing import StandardScaler

    imp = SimpleImputer(strategy="median").fit(Xtr)
    Xtr, Xte = imp.transform(Xtr), imp.transform(Xte)

    if name == "logistic":
        from sklearn.linear_model import LogisticRegression

        sc = StandardScaler().fit(Xtr)
        m = LogisticRegression(max_iter=2000, C=0.1, random_state=seed)
        m.fit(sc.transform(Xtr), ytr)
        return m.predict_proba(sc.transform(Xte))[:, 1]

    if name == "gbm":
        from sklearn.ensemble import HistGradientBoostingClassifier

        m = HistGradientBoostingClassifier(
            max_iter=300, learning_rate=0.05, max_depth=4,
            min_samples_leaf=200, l2_regularization=1.0,
            early_stopping=True, validation_fraction=0.15,
            random_state=seed,
        )
        m.fit(Xtr, ytr)
        return m.predict_proba(Xte)[:, 1]

    raise ValueError(f"unknown model: {name}")


def permutation_importance(tr: pd.DataFrame, te: pd.DataFrame, seed: int) -> pd.DataFrame:
    """Permutation importance on the GBM, measured in AUC lost.

    Permutation rather than the built-in split counts: split counts reward
    high-cardinality features regardless of whether they help, which flatters
    noisy continuous columns.
    """
    from sklearn.ensemble import HistGradientBoostingClassifier
    from sklearn.impute import SimpleImputer
    from sklearn.metrics import roc_auc_score

    rng = np.random.default_rng(seed)
    imp = SimpleImputer(strategy="median").fit(tr[FEATURES].to_numpy(float))
    Xtr = imp.transform(tr[FEATURES].to_numpy(float))
    Xte = imp.transform(te[FEATURES].to_numpy(float))
    ytr, yte = tr[TARGET].to_numpy(int), te[TARGET].to_numpy(int)

    m = HistGradientBoostingClassifier(
        max_iter=300, learning_rate=0.05, max_depth=4, min_samples_leaf=200,
        l2_regularization=1.0, early_stopping=True, validation_fraction=0.15,
        random_state=seed).fit(Xtr, ytr)
    base = roc_auc_score(yte, m.predict_proba(Xte)[:, 1])

    rows = []
    for j, f in enumerate(FEATURES):
        drops = []
        for _ in range(3):
            Xp = Xte.copy()
            Xp[:, j] = rng.permutation(Xp[:, j])
            drops.append(base - roc_auc_score(yte, m.predict_proba(Xp)[:, 1]))
        rows.append({"feature": f, "auc_drop": float(np.mean(drops)),
                     "auc_drop_sd": float(np.std(drops))})
    return pd.DataFrame(rows).sort_values("auc_drop", ascending=False).reset_index(drop=True)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--models", nargs="*",
                    default=["always_long", "momentum_rule", "logistic", "gbm"])
    ap.add_argument("--no-write", action="store_true", help="skip writing to the warehouse")
    ap.add_argument("--importance", action="store_true", default=True)
    args = ap.parse_args()

    print("loading modeling panel")
    df = load_panel()
    print(f"  {len(df):,} rows, {df['symbol'].nunique()} symbols, "
          f"{df['dt'].min()} to {df['dt'].max()}")
    print(f"  {len(FEATURES)} stationary features, target base rate "
          f"{df[TARGET].mean():.4f}")

    fs = foldmod.build_folds(df["dt"])
    problems = foldmod.assert_no_overlap(df, fs, config.LABEL_HORIZON_DAYS)
    if problems:
        print("\nFOLD CONSTRUCTION FAILED:", file=sys.stderr)
        for p in problems:
            print(f"  {p}", file=sys.stderr)
        return 1
    print(f"  {len(fs)} walk-forward folds, purge {config.PURGE_DAYS}d, "
          f"embargo {config.EMBARGO_DAYS}d, no overlap violations")

    all_rows: list[dict] = []
    pred_rows: list[tuple] = []

    for f in fs:
        tr, te = foldmod.split(df, f)
        if tr.empty or te.empty:
            continue
        print(f"\nfold {f.fold_id}  train {f.train_start}..{f.train_end} "
              f"({len(tr):,})  test {f.test_start}..{f.test_end} ({len(te):,})")
        yte = te[TARGET].to_numpy(int)
        ret = te[RET].to_numpy(float)
        for name in args.models:
            prob = fit_predict(name, tr, te, config.RANDOM_SEED + f.fold_id)
            m = evaluate(yte, prob, ret, te["dt"].to_numpy())
            m.update({"model": name, "fold_id": f.fold_id,
                      "test_start": f.test_start, "test_end": f.test_end})
            all_rows.append(m)
            auc = f"{m['auc']:.4f}" if m["auc"] is not None else "  n/a"
            t = f"{m['t_stat']:+.2f}" if m["t_stat"] is not None else " n/a"
            print(f"  {name:<14} auc {auc}  per-date net "
                  f"{m['top_decile_mean_net_ret']*100:+.3f}%  "
                  f"dates {m['top_decile_dates']:>3}  n_eff {m['n_effective']:>5.1f}  "
                  f"t {t}  conc10 {m['conc_top10_dates']:.0%}")
            if not args.no_write:
                pred_rows.extend(
                    (name, f.fold_id, s, d, float(yv), float(p),
                     int(p >= 0.5))
                    for s, d, yv, p in zip(te["symbol"], te["dt"], yte, prob)
                )

    res = pd.DataFrame(all_rows)
    if res.empty:
        print("no folds produced results", file=sys.stderr)
        return 1

    print("\n" + "=" * 78)
    print("POOLED ACROSS FOLDS (mean, with per-fold spread)")
    print("=" * 78)
    agg = res.groupby("model").agg(
        folds=("fold_id", "count"),
        auc=("auc", "mean"),
        auc_sd=("auc", "std"),
        acc=("accuracy", "mean"),
        lift=("accuracy_lift", "mean"),
        top_ret=("top_decile_mean_net_ret", "mean"),
        top_ret_sd=("top_decile_mean_net_ret", "std"),
        top_ret_naive=("top_decile_mean_net_ret_naive", "mean"),
        edge_bps=("top_decile_edge_bps", "mean"),
        sharpe=("sharpe_nonoverlap", "mean"),
        sharpe_naive=("sharpe_naive_per_row", "mean"),
        n_eff=("n_effective", "sum"),
        n_rows=("top_decile_n", "sum"),
        t_stat=("t_stat", "mean"),
        pct_dates_pos=("pct_dates_positive", "mean"),
        conc10=("conc_top10_dates", "mean"),
        folds_positive=("top_decile_mean_net_ret", lambda s: int((s > 0).sum())),
    ).reset_index()
    order = {"always_long": 0, "momentum_rule": 1, "logistic": 2, "gbm": 3}
    agg = agg.sort_values("model", key=lambda s: s.map(order)).reset_index(drop=True)

    print(f"{'model':<14} {'auc':>7} {'±sd':>6} {'per-date net':>13} "
          f"{'edge bps':>9} {'sharpe':>7} {'t':>6} {'% dates +':>10} {'folds +':>8}")
    for _, r in agg.iterrows():
        auc = f"{r.auc:.4f}" if pd.notna(r.auc) else "    n/a"
        sd = f"{r.auc_sd:.4f}" if pd.notna(r.auc_sd) else "   n/a"
        sh = f"{r.sharpe:.2f}" if pd.notna(r.sharpe) else "  n/a"
        t = f"{r.t_stat:+.2f}" if pd.notna(r.t_stat) else " n/a"
        print(f"{r.model:<14} {auc:>7} {sd:>6} {r.top_ret*100:>12.3f}% "
              f"{r.edge_bps:>+9.1f} {sh:>7} {t:>6} {r.pct_dates_pos:>9.1%} "
              f"{int(r.folds_positive)}/{int(r.folds)}")

    print("\nSAMPLE SIZE AND CLUSTERING — why the row count is not the sample size")
    print(f"{'model':<14} {'rows picked':>12} {'eff. obs':>9} {'naive sharpe':>13} "
          f"{'honest sharpe':>14} {'conc. top-10 dates':>19}")
    for _, r in agg.iterrows():
        shn = f"{r.sharpe_naive:.2f}" if pd.notna(r.sharpe_naive) else " n/a"
        sh = f"{r.sharpe:.2f}" if pd.notna(r.sharpe) else " n/a"
        print(f"{r.model:<14} {int(r.n_rows):>12,} {r.n_eff:>9.0f} {shn:>13} "
              f"{sh:>14} {r.conc10:>18.0%}")

    config.FIGURES.parent.mkdir(parents=True, exist_ok=True)
    res.to_csv(config.ROOT / "reports" / "fold_metrics.csv", index=False)
    agg.to_csv(config.ROOT / "reports" / "model_summary.csv", index=False)
    print(f"\nwrote reports/fold_metrics.csv and reports/model_summary.csv")

    if args.importance:
        print("\ncomputing permutation importance on the last fold")
        tr, te = foldmod.split(df, fs[-1])
        imp = permutation_importance(tr, te, config.RANDOM_SEED)
        imp.to_csv(config.ROOT / "reports" / "feature_importance.csv", index=False)
        print(f"{'feature':<22} {'auc drop':>10}")
        for _, r in imp.head(10).iterrows():
            print(f"  {r.feature:<20} {r.auc_drop:>+10.5f}")
        print("  wrote reports/feature_importance.csv")

    if not args.no_write:
        with db.transaction() as con:
            con.execute("DELETE FROM predictions")
            con.execute("DELETE FROM wf_folds")
            con.executemany(
                """INSERT INTO wf_folds (fold_id, train_start, train_end,
                   test_start, test_end, purge_days, embargo_days)
                   VALUES (?,?,?,?,?,?,?)""", [f.as_row() for f in fs])
            con.executemany(
                """INSERT INTO predictions
                   (model_name, fold_id, symbol, dt, y_true, y_prob, y_pred)
                   VALUES (?,?,?,?,?,?,?)""", pred_rows)
        print(f"\nwrote {len(pred_rows):,} predictions and {len(fs)} folds to the warehouse")

    return 0


if __name__ == "__main__":
    sys.exit(main())
