#!/usr/bin/env python3
"""Charting by Machines (Murray, Xia & Xiao 2024, JFE): trials 53, 54 and 55.

    python3 python/cbm_replication.py load      # authors' CSV -> parquet (needed columns)
    python3 python/cbm_replication.py verify    # trial 53: their forecasts -> their Table 3
    python3 python/cbm_replication.py costs     # trial 54: the cost analysis the paper skips
    python3 python/cbm_replication.py replica   # trial 55 gate: retrain 1963-2014, compare to theirs
    python3 python/cbm_replication.py final     # trial 55: retrain 1963-2022, freeze
    python3 python/cbm_replication.py ours      # trial 55: our 2023-2026 inputs
    python3 python/cbm_replication.py test      # trial 55: the out-of-sample test
    python3 python/cbm_replication.py own_build # trial 56: our 2015-2022 train + 2023-2026 test panels
    python3 python/cbm_replication.py own_train # trial 56: train the paper's model on our data, freeze
    python3 python/cbm_replication.py own_test  # trial 56: the one-shot test

TRIALS 53-55 COULD NOT RUN AS DESIGNED: the authors' public stock-level file is
scrambled within month for CRSP licensing. Trial 56 is the declared weaker
substitute: the same model trained on our own (survivor-biased) data.

Everything comes from the paper (all 168 pages read) and the authors' published
code and data (Mendeley x63r376783 v2, CC BY 4.0):

  inputs     CumRet1..CumRet12: cumulative return from month t-12 through
             t-12+k-1, in percent (SampleForFits.R)
  target     RetRFNorm = qnorm(rank of RetRF within month / (N + 1))
  weight     EWPM = 1 / number of stocks that month
  network    Reshape(12,1) -> Conv1D(64, 5, valid, ReLU) -> MaxPool1D(2)
             -> LSTM(32) -> Dense(64) -> Dropout(0.2) -> ReLU -> Dense(1)
             (MLForecasts.ipynb, model 'cnn_lstm')
  training   MSE, Adam defaults (lr 1e-3), batch 32,768, up to 100 epochs,
             random 70/30 split, early stopping on validation MAE with
             patience 5, best checkpoint kept, 30 runs averaged
  portfolio  monthly deciles of the forecast, NYSE breakpoints, value
             weights from market cap at the end of month t-1; 10-1
  t-stats    Newey-West with 12 lags, as in every table of the paper

The network is rebuilt in PyTorch because TensorFlow does not support this
Python. Keras defaults are copied (Glorot-uniform kernels, orthogonal recurrent
weights, zero biases with the LSTM forget-gate bias at 1, mean-over-batch
weighted loss). The replica check in trial 55 measures how close it gets.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "python"))

RAW = ROOT / "data" / "cbm_raw" / "SampleForPaperRandomized.csv"
D = ROOT / "data" / "cbm"
PANEL = D / "panel.parquet"
F = [f"CumRet{k}" for k in range(1, 13)]
RUNS, BATCH, EPOCHS, PATIENCE = 30, 32768, 100, 5
# Rows scored per forward pass when evaluating. Only affects memory, not results:
# 262,144 ran the 8GB laptop's GPU out of memory on the 1.7M-row CRSP fit.
EVAL_CHUNK = 32768


# ------------------------------------------------------------------ helpers
def nw_t(x: pd.Series, lags: int = 12) -> float:
    """Newey-West t-statistic of the mean, as in the paper's tables."""
    x = pd.Series(x).dropna().to_numpy(float)
    n = len(x)
    e = x - x.mean()
    s = e @ e / n
    for L in range(1, lags + 1):
        s += 2 * (1 - L / (lags + 1)) * (e[L:] @ e[:-L]) / n
    return x.mean() / np.sqrt(s / n)


def deciles(df, sortvar, bp_mask=None):
    """Assign deciles within month using breakpoints from bp_mask rows (NYSE)."""
    out = pd.Series(np.nan, index=df.index)
    for m, g in df.groupby("Month"):
        ref = g.loc[bp_mask.loc[g.index], sortvar] if bp_mask is not None else g[sortvar]
        ref = ref.dropna()
        if len(ref) < 10:
            continue
        cuts = np.quantile(ref, np.arange(0.1, 1.0, 0.1))
        out.loc[g.index] = np.searchsorted(cuts, g[sortvar].to_numpy(), side="right")
    return out


def legs(df, sortvar, weight, bp_mask=None, cost=None):
    """Monthly value-weighted decile returns, 10-1, and per-leg turnover cost."""
    df = df.dropna(subset=[sortvar, "RetRF", weight]).copy()
    df["dec"] = deciles(df, sortvar, None if bp_mask is None else bp_mask.loc[df.index])
    df = df.dropna(subset=["dec"])
    df["w"] = df[weight] / df.groupby(["Month", "dec"])[weight].transform("sum")
    port = (df["w"] * df["RetRF"]).groupby([df["Month"], df["dec"]]).sum().unstack()
    out = pd.DataFrame({"d1": port[0], "d10": port[9]})
    out["gross"] = out["d10"] - out["d1"]
    if cost is not None:
        for d, name in ((9, "c10"), (0, "c1")):
            x = df[df["dec"] == d].pivot_table(index="Month", columns="permno", values="w",
                                              aggfunc="sum").fillna(0.0)
            c = df[df["dec"] == d].pivot_table(index="Month", columns="permno", values=cost,
                                              aggfunc="last").reindex_like(x).ffill().bfill()
            dw = x.diff().abs()
            dw.iloc[0] = x.iloc[0]
            out[name] = (dw * c).sum(axis=1) * 100            # percent, like RetRF
            out["turn" + name[1:]] = dw.sum(axis=1)
        out["net"] = out["gross"] - out["c10"] - out["c1"]
    return out, df


def line(label, s):
    s = s.dropna()
    print(f"  {label:<44}{s.mean():+7.2f}%/mo  t={nw_t(s):5.2f}  "
          f"Sharpe {s.mean() / s.std() * np.sqrt(12):5.2f}  ({len(s)} mo)")


# ------------------------------------------------------------------ load
def load() -> int:
    D.mkdir(parents=True, exist_ok=True)
    cols = ["Month", "permno", *F, "RetRF", "MktCapM", "MLER", "NYSESample",
            "MktCapGTNYSE20PctlSample", "Top500MktCapSample", "Mom", "Rev"]
    df = pd.read_csv(RAW, usecols=cols, dtype={c: "float32" for c in F + ["RetRF", "MktCapM", "MLER", "Mom", "Rev"]})
    df.to_parquet(PANEL, index=False)
    print(f"{len(df):,} stock-months, {df['Month'].min()} to {df['Month'].max()}, "
          f"{df['permno'].nunique():,} stocks")
    return 0


def panel():
    return pd.read_parquet(PANEL)


# ------------------------------------------------------------------ trial 53
def verify() -> int:
    df = panel()
    out, _ = legs(df, "MLER", "MktCapM", bp_mask=df["NYSESample"] == 1)
    out.to_csv(ROOT / "reports" / "cbm_verify_monthly.csv")
    g = out["gross"]
    print("TRIAL 53: authors' MLER, NYSE breakpoints, value-weighted (paper Table 3 / Table 5)")
    line("1963-07 to 2022-12 (paper +1.08, t 5.51)", g)
    checks = {"1963-07 to 2022-12": (g, 1.08, 0.10),
              "2005-2014 (paper -0.08)": (g[(g.index >= 200501) & (g.index <= 201412)], -0.08, 0.20),
              "2015-2022 (paper +1.20)": (g[g.index >= 201501], 1.20, 0.20)}
    ok = True
    for k, (s, target, tol) in checks.items():
        if k != "1963-07 to 2022-12":
            line(k, s)
        hit = abs(s.mean() - target) <= tol
        ok &= hit
        print(f"     {'within' if hit else 'OUTSIDE'} {tol:.2f} of {target:+.2f}")
    top = df[df["Top500MktCapSample"] == 1]
    t5, _ = legs(top, "MLER", "MktCapM")
    line("Top 500 only (paper +0.72, t 4.37)", t5["gross"])
    print(f"\n  VERDICT: {'VERIFIED' if ok else 'NOT VERIFIED'}")
    (D / "verify_ok.txt").write_text("1" if ok else "0")
    return 0


# ------------------------------------------------------------------ trial 54
def costs() -> int:
    if (D / "verify_ok.txt").read_text() != "1":
        print("REFUSING: trial 53 did not verify.")
        return 1
    df = panel()
    df["cost"] = np.where(df["MktCapGTNYSE20PctlSample"] == 1, 0.0010, 0.0020)
    out, _ = legs(df, "MLER", "MktCapM", bp_mask=df["NYSESample"] == 1, cost="cost")
    out.to_csv(ROOT / "reports" / "cbm_costs_monthly.csv")
    rec, full = out[out.index >= 201501], out
    print("TRIAL 54: authors' portfolios, 10bp/20bp per unit traded")
    for lab, s in (("2015-2022", rec), ("1963-2022", full)):
        line(f"gross {lab}", s["gross"])
        line(f"net   {lab}", s["net"])
    pa = rec["net"].mean() > 0 and nw_t(rec["net"]) >= 2
    pb = full["net"].mean() > 0 and nw_t(full["net"]) >= 2
    print(f"\n  (a) 2015-2022 net t >= 2: {'PASS' if pa else 'FAIL'}")
    print(f"  (b) 1963-2022 net t >= 2: {'PASS' if pb else 'FAIL'}")
    print(f"  VERDICT: {'PASS' if pa and pb else 'FAIL'}\n")
    print("descriptive only:")
    print(f"  monthly turnover per leg (one-way, mean): long {out['turn10'].mean():.0%}, "
          f"short {out['turn1'].mean():.0%}")
    for lab, s in (("2015-2022", rec), ("1963-2022", full)):
        per_unit = (s["turn10"] + s["turn1"]).mean()
        print(f"  break-even cost {lab}: {s['gross'].mean() / per_unit * 100:.1f}bp per unit traded")
    for a, b in ((196307, 197412), (197501, 198412), (198501, 199412), (199501, 200412),
                 (200501, 201412), (201501, 202212)):
        s = out[(out.index >= a) & (out.index <= b)]
        print(f"  {a // 100}-{b // 100}: gross {s['gross'].mean():+.2f}  net {s['net'].mean():+.2f} "
              f"(t {nw_t(s['net']):+.2f})")
    top = df[df["Top500MktCapSample"] == 1]
    t5, _ = legs(top, "MLER", "MktCapM", cost="cost")
    line("Top 500, net 1963-2022", t5["net"])
    line("Top 500, net 2015-2022", t5.loc[t5.index >= 201501, "net"])
    return 0


# ------------------------------------------------------------------ the network
def net():
    import torch.nn as nn

    class CNNLSTM(nn.Module):
        def __init__(self):
            super().__init__()
            self.conv = nn.Conv1d(1, 64, 5)              # valid padding, stride 1
            self.pool = nn.MaxPool1d(2)
            self.lstm = nn.LSTM(64, 32, batch_first=True)
            self.fc1 = nn.Linear(32, 64)
            self.drop = nn.Dropout(0.2)
            self.fc2 = nn.Linear(64, 1)
            # Keras defaults
            for m in (self.conv, self.fc1, self.fc2):
                nn.init.xavier_uniform_(m.weight)
                nn.init.zeros_(m.bias)
            nn.init.xavier_uniform_(self.lstm.weight_ih_l0)
            nn.init.orthogonal_(self.lstm.weight_hh_l0)
            nn.init.zeros_(self.lstm.bias_ih_l0)
            nn.init.zeros_(self.lstm.bias_hh_l0)
            with __import__("torch").no_grad():
                self.lstm.bias_ih_l0[32:64] = 1.0        # unit_forget_bias=True

        def forward(self, x):                             # x: (n, 12)
            h = self.pool(self.conv(x.unsqueeze(1)).relu())   # (n, 64, 4)
            _, (hn, _) = self.lstm(h.transpose(1, 2))
            return self.fc2(self.drop(self.fc1(hn[-1])).relu()).squeeze(1)
    return CNNLSTM()


def targets(df):
    from scipy.stats import norm
    r = df.groupby("Month")["RetRF"].rank(method="average")
    n = df.groupby("Month")["RetRF"].transform("size")
    return norm.ppf(r / (n + 1)).astype("float32"), (1.0 / n).astype("float32")


def train_ensemble(df, tag):
    import torch
    dev = "mps" if torch.backends.mps.is_available() else "cpu"
    df = df.dropna(subset=F + ["RetRF"])
    y, w = targets(df)
    X = df[F].to_numpy("float32")
    print(f"training {tag}: {len(df):,} rows, months {df['Month'].min()}-{df['Month'].max()}, "
          f"device {dev}", flush=True)
    # The full data stays in ordinary memory; only one batch at a time goes to the
    # GPU. Holding all of it on the GPU ran the 8GB laptop out of GPU memory on the
    # 2.2M-row fit, which shares that memory with every other app.
    Xt, yt, wt = (torch.from_numpy(np.ascontiguousarray(a)) for a in (X, np.asarray(y), np.asarray(w)))
    for run in range(RUNS):
        path = D / f"{tag}_run{run}.pt"
        if path.exists():
            continue
        # A run is saved under a temporary name and renamed only when it finishes,
        # so an interrupted run is never mistaken for a finished one on resume.
        # If the GPU is still out of memory after a few tries, that one run trains
        # on the CPU instead: about 5x slower, same arithmetic, always finishes.
        for attempt in range(4):
            use = dev if attempt < 3 else "cpu"
            try:
                _one_run(run, path, Xt, yt, wt, len(df), use)
                break
            except RuntimeError as e:
                if "out of memory" not in str(e):
                    raise
                print(f"  run {run}: GPU out of memory (attempt {attempt + 1})"
                      + (", retrying in 60s" if attempt < 2 else ", switching this run to the CPU"),
                      flush=True)
                if dev == "mps":
                    torch.mps.empty_cache()
                if attempt < 2:
                    time.sleep(60)
        if dev == "mps":
            torch.mps.empty_cache()


def _one_run(run, path, Xt, yt, wt, n, dev):
    import torch
    tmp = path.with_suffix(".partial")
    g = np.random.default_rng(1000 + run)
    perm = g.permutation(n)
    cut = int(len(perm) * 0.7)
    tr, va = torch.from_numpy(perm[:cut]), torch.from_numpy(perm[cut:])
    torch.manual_seed(run)
    m = net().to(dev)
    opt = torch.optim.Adam(m.parameters(), lr=1e-3, betas=(0.9, 0.999), eps=1e-7)
    best, bad, t0 = np.inf, 0, time.time()
    for ep in range(EPOCHS):
        m.train()
        order = tr[torch.randperm(len(tr))]
        for k in range(0, len(order), BATCH):
            j = order[k:k + BATCH]
            xb, yb, wb = Xt[j].to(dev), yt[j].to(dev), wt[j].to(dev)
            opt.zero_grad()
            loss = (wb * (m(xb) - yb) ** 2).mean()   # Keras sum-over-batch-size
            loss.backward()
            opt.step()
        m.eval()
        with torch.no_grad():
            mae = 0.0
            for k in range(0, len(va), EVAL_CHUNK):
                j = va[k:k + EVAL_CHUNK]
                mae += (m(Xt[j].to(dev)) - yt[j].to(dev)).abs().sum().item()
            mae /= len(va)
        if mae < best - 1e-7:
            best, bad = mae, 0
            torch.save(m.state_dict(), tmp)
        else:
            bad += 1
            if bad >= PATIENCE:
                break
    tmp.rename(path)
    print(f"  run {run}: {ep + 1} epochs, best validation MAE {best:.5f} "
          f"({time.time() - t0:.0f}s, {dev})", flush=True)


def predict(X, tag):
    import torch
    dev = "mps" if torch.backends.mps.is_available() else "cpu"
    # Inputs stay in ordinary memory and go to the GPU one chunk at a time.
    Xt = torch.from_numpy(np.ascontiguousarray(np.asarray(X, "float32")))
    out = np.zeros(len(X))
    for run in range(RUNS):
        m = net().to(dev)
        m.load_state_dict(torch.load(D / f"{tag}_run{run}.pt", map_location=dev))
        m.eval()
        with torch.no_grad():
            out += np.concatenate([m(Xt[k:k + EVAL_CHUNK].to(dev)).cpu().numpy()
                                   for k in range(0, len(X), EVAL_CHUNK)]) / RUNS
        if dev == "mps":
            torch.mps.empty_cache()
    return out


# ------------------------------------------------------------------ trial 55
def replica() -> int:
    df = panel()
    train_ensemble(df[df["Month"] <= 201412], "replica")
    t = df[df["Month"] >= 201501].dropna(subset=F + ["RetRF", "MLER"]).copy()
    t["MyMLER"] = predict(t[F].to_numpy(), "replica")
    rc = t.groupby("Month").apply(lambda g: g["MyMLER"].corr(g["MLER"], method="spearman")).mean()
    mine, _ = legs(t, "MyMLER", "MktCapM", bp_mask=t["NYSESample"] == 1)
    theirs, _ = legs(t, "MLER", "MktCapM", bp_mask=t["NYSESample"] == 1)
    print("\nREPLICA CHECK, 2015-2022 (our retrain vs the authors' published forecasts)")
    print(f"  mean monthly rank correlation with their MLER: {rc:.3f}   (gate >= 0.70)")
    line("theirs", theirs["gross"])
    line("ours (replica)", mine["gross"])
    gap = abs(mine["gross"].mean() - theirs["gross"].mean())
    ok = rc >= 0.70 and gap <= 0.40
    print(f"  10-1 gap {gap:.2f}%/mo (gate <= 0.40)\n  REPLICA {'ACCEPTED' if ok else 'REJECTED'}")
    (D / "replica_ok.txt").write_text("1" if ok else "0")
    return 0


def final() -> int:
    if (D / "replica_ok.txt").read_text() != "1":
        print("REFUSING: the replica check did not pass.")
        return 1
    train_ensemble(panel(), "final")
    return 0


def ours(lo: int = 202301, hi: int = 202609, out_name: str = "ours.parquet") -> int:
    import db
    import lockbox
    end = lockbox.state()["seal_from"]
    max_date = (pd.Timestamp(end) - pd.Timedelta(days=1)).date().isoformat()
    lockbox.guard(max_date, "cbm_replication ours")
    with db.connect() as con:
        d = pd.read_sql("SELECT b.symbol, b.dt, b.close, b.adj_close, b.volume FROM bars b "
                        "JOIN symbols s USING(symbol) WHERE s.kind='stock' AND b.dt <= ?",
                        con, params=(max_date,))
    d["Month"] = d["dt"].str[:4].astype(int) * 100 + d["dt"].str[5:7].astype(int)
    d["dv"] = d["close"] * d["volume"]
    me = d.sort_values("dt").groupby(["symbol", "Month"]).agg(px=("adj_close", "last"),
                                                             dv=("dv", "median"),
                                                             last=("dt", "last"))
    last_session = d.groupby("Month")["dt"].max()
    me = me[me["last"].values == last_session.reindex(me.index.get_level_values(1)).values]
    px = me["px"].unstack(0).sort_index()
    dv = me["dv"].unstack(0).sort_index()
    ret = px.pct_change() * 100
    rows = []
    for i, m in enumerate(px.index):
        if m < lo or m > hi or i < 13:
            continue
        r = ret.iloc[i - 12:i]                       # months t-12 .. t-1
        cum = ((1 + r / 100).cumprod() - 1) * 100
        ok = cum.notna().all() & ret.iloc[i].notna() & dv.iloc[i - 1].notna()
        for s in cum.columns[ok]:
            rows.append([m, s, *cum[s].to_numpy(), ret.iloc[i][s], dv.iloc[i - 1][s]])
    o = pd.DataFrame(rows, columns=["Month", "permno", *F, "RetRF", "MktCapM"])
    D.mkdir(parents=True, exist_ok=True)
    o.to_parquet(D / out_name, index=False)
    print(f"{len(o):,} stock-months, {o['Month'].nunique()} months "
          f"({o['Month'].min()}-{o['Month'].max()}), {o['permno'].nunique()} stocks")
    return 0


def own_build() -> int:
    """Trial 56: training and test panels from our own database."""
    ours(201502, 202212, "own_train.parquet")
    return ours(202301, 202609, "ours.parquet")


def own_train() -> int:
    """Trial 56: the paper's model, trained on our 2015-2022 panel, then frozen."""
    train_ensemble(pd.read_parquet(D / "own_train.parquet"), "own")
    return 0


def test(tag: str = "final", label: str = "TRIAL 55") -> int:
    from evaluation import clustered_mean
    if tag == "final" and (D / "replica_ok.txt").read_text() != "1":
        print("REFUSING: the replica check did not pass.")
        return 1
    o = pd.read_parquet(D / "ours.parquet")
    o["MLER"] = predict(o[F].to_numpy(), tag)
    o["big"] = o.groupby("Month")["MktCapM"].transform(lambda s: s >= s.quantile(0.8))
    o["cost"] = np.where(o["big"], 0.0010, 0.0020)
    out, _ = legs(o, "MLER", "MktCapM", cost="cost")
    out.to_csv(ROOT / "reports" / "cbm_oos_monthly.csv")
    g, n = out["gross"], out["net"]
    se = g.std() / np.sqrt(len(g))
    print(f"{label} OUT OF SAMPLE: {len(g)} months {g.index.min()}-{g.index.max()}, "
          f"~{o.groupby('Month').size().median():.0f} stocks a month")
    line("gross 10-1", g)
    line("net 10-1 (10/20bp)", n)
    pg, pn = nw_t(g) >= 2, nw_t(n) >= 2
    print(f"\n  gross t >= 2: {'PASS' if pg else 'FAIL'}   net t >= 2: {'PASS' if pn else 'FAIL'}")
    print(f"  VERDICT: {'PASS' if pg and pn else 'FAIL'}\n")
    print("descriptive only:")
    print(f"  minimum detectable effect (80% power): about {2.8 * se:.2f}%/month")
    c = clustered_mean(g, pd.Series(g.index))
    print(f"  concentration: top-5 months carry {c['top5_share']:.0%}; trimmed mean "
          f"{c['trimmed_mean']:+.2f}%, alarm={c['alarm']}")
    print(f"  turnover per leg: long {out['turn10'].mean():.0%}, short {out['turn1'].mean():.0%}")
    ew, _ = legs(o.assign(one=1.0), "MLER", "one")
    line("equal-weighted gross 10-1", ew["gross"])
    line("long-only decile 10 minus all stocks (VW)", out["d10"] - o.groupby("Month").apply(
        lambda x: np.average(x["RetRF"], weights=x["MktCapM"])))
    return 0


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "verify"
    sys.exit({"load": load, "verify": verify, "costs": costs, "replica": replica,
              "final": final, "ours": ours, "test": test,
              "own_build": own_build, "own_train": own_train,
              "own_test": lambda: test("own", "TRIAL 56")}[cmd]())
