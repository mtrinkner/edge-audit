#!/usr/bin/env python3
"""Train the chart-image CNN and test it out of sample (trial 50).

    python3 python/cnn_charts.py train      # 5 models on 2014-2019, ~hours on a laptop
    python3 python/cnn_charts.py evaluate   # frozen models on 2020-2026, one shot
    python3 python/cnn_charts.py train --split time      # trial 51
    python3 python/cnn_charts.py evaluate --split time   # trial 51

Architecture and training follow Jiang, Kelly & Xiu (2023, Journal of Finance),
section II and the appendix, for 20-day images:
  conv 5x3, 64 filters, stride (3,1), dilation (2,1)  -> BN -> LeakyReLU -> maxpool 2x1
  conv 5x3, 128 filters                               -> BN -> LeakyReLU -> maxpool 2x1
  conv 5x3, 256 filters                               -> BN -> LeakyReLU -> maxpool 2x1
  flatten (256 x 3 x 60 = 46,080) -> dropout 0.5 -> fully connected -> softmax
Xavier initialisation, Adam at 1e-5, batch 128, cross-entropy, early stopping
after 2 epochs without validation improvement, 5 independent runs averaged.

Training uses images dated before 2019-12-16 only, so no training label (5 days
forward) reaches into the 2020 test period. The test period is scored once.

TRIAL 51 (--split time). Trial 50 splits training and validation charts at
random, as the paper does, which puts the same weeks in both. A diagnostic run
before the test showed validation loss then rewards recognising WHICH WEEK a
chart is from, not which stock beats the others that week. With --split time,
the model trains on charts before 2018-12-24 and validates on 2019, weeks it
never trained on. Everything else is identical. The default (--split random)
is unchanged, so trial 50 runs exactly as declared.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "python"))

from evaluation import mde

D = ROOT / "data" / "cnn"
TRAIN_END = "2019-12-16"
TEST_START = "2020-01-01"
RUNS = 5
BATCH = 128
LR = 1e-5
PATIENCE = 2
MAX_EPOCHS = 30
COST = 5e-4                     # per unit of weight traded, each side


def model():
    import torch.nn as nn
    def block(cin, cout, **kw):
        return [nn.Conv2d(cin, cout, (5, 3), **kw), nn.BatchNorm2d(cout),
                nn.LeakyReLU(0.01), nn.MaxPool2d((2, 1))]
    net = nn.Sequential(
        *block(1, 64, stride=(3, 1), dilation=(2, 1), padding=(12, 1)),
        *block(64, 128, padding=(2, 1)),
        *block(128, 256, padding=(2, 1)),
        nn.Flatten(), nn.Dropout(0.5), nn.Linear(46080, 2))
    for m in net.modules():
        if isinstance(m, (nn.Conv2d, nn.Linear)):
            nn.init.xavier_uniform_(m.weight)
    return net


def batches(X, idx, y, mu, sd, dev, shuffle, rng=None):
    import torch
    if shuffle:
        idx = rng.permutation(idx)
    for k in range(0, len(idx), BATCH):
        j = np.sort(idx[k:k + BATCH])
        img = np.unpackbits(X[j], axis=1).reshape(-1, 1, 64, 60).astype(np.float32) * 255
        xb = torch.from_numpy((img - mu) / sd).to(dev)
        yb = None if y is None else torch.from_numpy(y[j]).long().to(dev)
        yield j, xb, yb


def tag(split: str) -> str:
    return "cnn_run" if split == "random" else "cnn_time_run"


def train(split: str = "random") -> int:
    import torch
    dev = "mps" if torch.backends.mps.is_available() else "cpu"
    X = np.load(D / "images_i20.npy")
    m = pd.read_parquet(D / "meta_i20.parquet")
    tr_all = np.flatnonzero((m["dt"] < TRAIN_END).to_numpy() & m["r5_cc"].notna().to_numpy())
    y = (m["r5_cc"].to_numpy() > 0).astype(np.int64)
    sample = np.unpackbits(X[tr_all[:: max(1, len(tr_all) // 20000)]], axis=1) * 255.0
    mu, sd = float(sample.mean()), float(sample.std())
    print(f"device {dev}; {len(tr_all):,} training-period images; pixel mean {mu:.2f} sd {sd:.2f}")
    for run in range(RUNS):
        rng = np.random.default_rng(run)
        if split == "random":
            perm = rng.permutation(tr_all)
            cut = int(len(perm) * 0.7)
            tr, va = perm[:cut], perm[cut:]
        else:
            dts = m["dt"].to_numpy()[tr_all]
            tr = tr_all[dts < "2018-12-24"]
            va = tr_all[dts >= "2019-01-01"]
        torch.manual_seed(run)
        net = model().to(dev)
        opt = torch.optim.Adam(net.parameters(), lr=LR)
        lossf = torch.nn.CrossEntropyLoss()
        best, bad, path = np.inf, 0, D / f"{tag(split)}{run}.pt"
        for ep in range(MAX_EPOCHS):
            t0 = time.time()
            net.train()
            for _, xb, yb in batches(X, tr, y, mu, sd, dev, True, rng):
                opt.zero_grad()
                loss = lossf(net(xb), yb)
                loss.backward()
                opt.step()
            net.eval()
            tot, n = 0.0, 0
            with torch.no_grad():
                for j, xb, yb in batches(X, va, y, mu, sd, dev, False):
                    tot += lossf(net(xb), yb).item() * len(j)
                    n += len(j)
            vl = tot / n
            print(f"  run {run} epoch {ep}: validation loss {vl:.5f} ({time.time() - t0:.0f}s)",
                  flush=True)
            if vl < best - 1e-6:
                best, bad = vl, 0
                torch.save({"state": net.state_dict(), "mu": mu, "sd": sd}, path)
            else:
                bad += 1
                if bad >= PATIENCE:
                    break
        print(f"  run {run} done, best validation loss {best:.5f}", flush=True)
    return 0


def predict(split: str = "random") -> pd.DataFrame:
    import torch
    dev = "mps" if torch.backends.mps.is_available() else "cpu"
    X = np.load(D / "images_i20.npy")
    m = pd.read_parquet(D / "meta_i20.parquet")
    te = np.flatnonzero((m["dt"] >= TEST_START).to_numpy())
    p = np.zeros(len(te))
    for run in range(RUNS):
        ck = torch.load(D / f"{tag(split)}{run}.pt", map_location=dev)
        net = model().to(dev)
        net.load_state_dict(ck["state"])
        net.eval()
        out = np.zeros(len(te))
        pos = {g: k for k, g in enumerate(te)}
        with torch.no_grad():
            for j, xb, _ in batches(X, te, None, ck["mu"], ck["sd"], dev, False):
                pr = torch.softmax(net(xb), 1)[:, 1].cpu().numpy()
                out[[pos[g] for g in j]] = pr
        p += out / RUNS
    t = m.iloc[te].copy()
    t["p_up"] = p
    t.to_parquet(D / pred_file(split), index=False)
    return t


def pred_file(split: str) -> str:
    return "pred_test.parquet" if split == "random" else "pred_test_time.parquet"


def evaluate(split: str = "random") -> int:
    f = D / pred_file(split)
    t = predict(split) if not f.exists() else pd.read_parquet(f)
    t = t.dropna(subset=["r5_oo"])
    if split == "time":
        # Declared before trial 51 was scored: trim each week's returns at its
        # 0.5% / 99.5% tails. Trial 50's test was distorted by one vendor
        # bankruptcy re-stitch (CHRD, +22,525% in a week). Raw kept for display.
        for col in ("r5_oo", "r5_cc"):
            t[col + "_raw"] = t[col]
            lo = t.groupby("dt")[col].transform(lambda s: s.quantile(0.005))
            hi = t.groupby("dt")[col].transform(lambda s: s.quantile(0.995))
            t[col] = t[col].clip(lo, hi)
    t["dec"] = t.groupby("dt")["p_up"].transform(
        lambda s: pd.qcut(s.rank(method="first"), 10, labels=False))
    weeks = sorted(t["dt"].unique())

    def leg(dec, col):
        x = t[t["dec"] == dec]
        r = x.groupby("dt")[col].mean().reindex(weeks)
        w = {dt: set(g["symbol"]) for dt, g in x.groupby("dt")}
        turn = []
        prev = set()
        for dt in weeks:
            cur = w.get(dt, set())
            n = max(len(cur), 1)
            # equal weights: names entering + leaving, each a full 1/n weight traded
            changed = len(cur ^ prev) / n if prev else 1.0
            turn.append(changed)
            prev = cur
        return r, pd.Series(turn, index=weeks)

    hi, th = leg(9, "r5_oo")
    lo, tl = leg(0, "r5_oo")
    hl_gross = hi - lo
    hl_net = hl_gross - COST * (th + tl)
    out = "cnn_charts_weekly.csv" if split == "random" else "cnn_charts_time_weekly.csv"
    n = hl_gross.notna().sum()
    tt = lambda s: s.mean() / (s.std() / np.sqrt(s.notna().sum()))
    sh = lambda s: s.mean() / s.std() * np.sqrt(52)
    print(f"trial {50 if split == 'random' else 51} test: {len(weeks)} weeks {weeks[0]} to {weeks[-1]}, "
          f"{t.groupby('dt').size().median():.0f} stocks a week\n")
    acc = ((t["p_up"] > 0.5) == (t["r5_cc"] > 0)).mean()
    print(f"  accuracy of the up/down call: {acc:.1%} (always 'up' would score "
          f"{(t['r5_cc'] > 0).mean():.1%})")
    print("\nPRE-DECLARED TEST (enter next open, 5 sessions, H-L deciles, equal weight)")
    print(f"  gross H-L {hl_gross.mean() * 1e4:+.1f}bp/week  t={tt(hl_gross):.2f}  "
          f"Sharpe {sh(hl_gross):.2f}   {'PASS' if tt(hl_gross) >= 2 else 'FAIL'} (replication)")
    print(f"  net H-L   {hl_net.mean() * 1e4:+.1f}bp/week  t={tt(hl_net):.2f}  "
          f"Sharpe {sh(hl_net):.2f}   {'PASS' if tt(hl_net) >= 2 else 'FAIL'} (tradeable)")
    print(f"  weekly turnover per leg: {th.mean():.0%} long, {tl.mean():.0%} short")
    if split == "time":
        raw = (t[t["dec"] == 9].groupby("dt")["r5_oo_raw"].mean()
               - t[t["dec"] == 0].groupby("dt")["r5_oo_raw"].mean()).reindex(weeks)
        print(f"  (untrimmed, for reference: gross H-L {raw.mean() * 1e4:+.1f}bp/week, t={tt(raw):.2f})")
    ok = tt(hl_gross) >= 2 and tt(hl_net) >= 2
    print(f"\n  VERDICT: {'PASS' if ok else 'FAIL'}\n")

    print("descriptive only:")
    se = hl_gross.std() / np.sqrt(n)
    per_unit = (th + tl).mean()
    print(f"  break-even cost: {hl_gross.mean() / per_unit * 1e4:+.1f}bp per unit traded "
          f"(declared 5bp); MDE {mde(se) * 1e4:.1f}bp/week")
    cc = leg(9, "r5_cc")[0] - leg(0, "r5_cc")[0]
    print(f"  paper's close-to-close timing: H-L {cc.mean() * 1e4:+.1f}bp/week, t={tt(cc):.2f}, "
          f"Sharpe {sh(cc):.2f}")
    uni = t.groupby("dt")["r5_oo"].mean().reindex(weeks)
    lx = hi - COST * th - uni
    print(f"  long-only top decile minus universe, net: {lx.mean() * 1e4:+.1f}bp/week, t={tt(lx):.2f}")
    dec = t.groupby(["dt", "dec"])["r5_oo"].mean().groupby("dec").mean() * 1e4
    print("  mean weekly return by decile (bp): " + " ".join(f"{v:+.0f}" for v in dec))
    for y, g in hl_gross.groupby(pd.Index(weeks).str[:4]):
        print(f"  {y}: gross H-L {g.mean() * 1e4:+.1f}bp/week")
    pd.DataFrame({"hl_gross": hl_gross, "hl_net": hl_net, "turn_long": th,
                  "turn_short": tl}).to_csv(ROOT / "reports" / out)
    print(f"\nwrote reports/{out}")
    return 0


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "evaluate"
    split = sys.argv[sys.argv.index("--split") + 1] if "--split" in sys.argv else "random"
    assert split in ("random", "time")
    sys.exit(train(split) if cmd == "train" else evaluate(split))
