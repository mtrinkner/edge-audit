#!/usr/bin/env python3
"""Exact replication of Jiang, Kelly & Xiu (2023, Journal of Finance), I20/R20 (trial 52).

    python3 python/jkx_replication.py pack      # authors' 1993-2000 images -> packed training set
    python3 python/jkx_replication.py train     # 5 CNNs, exactly as the paper
    python3 python/jkx_replication.py verify    # authors' 2001-2019 images: do we reproduce the paper?
    python3 python/jkx_replication.py render    # our month-end images, 2020-01 to 2026-08
    python3 python/jkx_replication.py test      # the one-shot out-of-sample test (refuses if verify failed)

Every setting below comes from the paper (all 57 pages read) or from the authors'
published data (dachxiu.chicagobooth.edu, img_data.zip). Page numbers refer to
J. Finance 78(6). The public data is MONTHLY 20-day images, so the strategy
replicated is I20/R20 (Table II: equal-weight H-L Sharpe 2.16, 2001-2019).

  data       authors' CRSP images 1993-2000 for training                p.3206
  label      y = 1 if the next 20-day return Ret_20d > 0, else 0       p.3205-3206
  split      random 70% training / 30% validation                      p.3204-3205
  init       Xavier                                                    p.3205
  optimiser  Adam, learning rate 1e-5, batch 128                       p.3205
  layers     batch norm between convolution and activation             p.3205
  dropout    50% on the fully connected layer only                     p.3205
  stopping   halt when validation loss fails to improve for 2 epochs   p.3205
  inputs     normalised by training-pixel mean and sd                  p.3205 fn.8
  ensemble   5 independent runs, forecasts averaged                    p.3206
  network    5x3 filters, 2x1 max-pool; first layer vertical stride 3,
             dilation 2 (20-day images); 64/128/256 filters; leaky
             ReLU slope 0.01; FC 46,080; softmax; 708,866 parameters   p.3203, 3244-3247
  portfolio  monthly deciles on predicted up-probability; H-L = decile
             10 minus decile 1; equal- and value-weight                p.3206
  costs      10bp per unit traded above the 80th size percentile,
             20bp otherwise (Frazzini, Israel & Moskowitz)             p.3214

OUR IMAGES are drawn to match the authors' files: price-only (no dividend
adjustment; matching their pixels doubled exact matches from 25% to 52%, and
99.96% of all pixels agree on 288 overlapping stock-months), price rows 0-50,
an empty row 51, volume rows 52-63, a 20-day moving average through the
middle pixel of each day joined by straight lines, interior missing days left
blank (p.3199-3200). Where vendors differ (CRSP vs Yahoo) a point can land one
pixel away; that is the residual 0.04%.

Nothing on or after the 2026-10-07 lockbox seal is read.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "python"))

SRC = ROOT / "data" / "jkx_raw" / "monthly_20d"
OUT = ROOT / "data" / "jkx"
TRAIN_YEARS = range(1993, 2001)
VERIFY_YEARS = range(2001, 2020)
H, W, DAYS, PH, VH = 64, 60, 20, 51, 12
RUNS, BATCH, LR, PATIENCE, MAX_EPOCHS = 5, 128, 1e-5, 2, 30
PAPER_EW_SHARPE = 2.16          # Table II, I20/R20, equal-weight H-L, 2001-2019


def year_files(y):
    return (SRC / f"20d_month_has_vb_[20]_ma_{y}_images.dat",
            SRC / f"20d_month_has_vb_[20]_ma_{y}_labels_w_delay.feather")


def load_year(y):
    fi, fl = year_files(y)
    X = np.memmap(fi, dtype=np.uint8, mode="r").reshape(-1, H, W)
    L = pd.read_feather(fl)
    return X, L


# ---------------------------------------------------------------- pack / train
def pack() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    xs, ys = [], []
    for y in TRAIN_YEARS:
        X, L = load_year(y)
        ok = L["Ret_20d"].notna().to_numpy()
        xs.append(np.packbits((X[ok] > 0).reshape(int(ok.sum()), -1), axis=1))
        ys.append((L.loc[ok, "Ret_20d"].to_numpy() > 0).astype(np.int64))
        print(f"  {y}: {int(ok.sum()):,} labelled images", flush=True)
    Xp, y = np.concatenate(xs), np.concatenate(ys)
    np.save(OUT / "train_X.npy", Xp)
    np.save(OUT / "train_y.npy", y)
    print(f"{len(y):,} training-period images, {y.mean():.1%} labelled up")
    return 0


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
    assert sum(p.numel() for p in net.parameters()) == 708866
    return net


def to_tensor(packed, mu, sd, dev):
    import torch
    img = np.unpackbits(packed, axis=1)[:, :H * W].reshape(-1, 1, H, W).astype(np.float32) * 255
    return torch.from_numpy((img - mu) / sd).to(dev)


def train() -> int:
    import torch
    dev = "mps" if torch.backends.mps.is_available() else "cpu"
    Xp, y = np.load(OUT / "train_X.npy"), np.load(OUT / "train_y.npy")
    sample = np.unpackbits(Xp[:: max(1, len(Xp) // 20000)], axis=1)[:, :H * W] * 255.0
    mu, sd = float(sample.mean()), float(sample.std())
    print(f"device {dev}; {len(y):,} images; pixel mean {mu:.2f} sd {sd:.2f}", flush=True)
    lossf = torch.nn.CrossEntropyLoss()
    for run in range(RUNS):
        rng = np.random.default_rng(run)
        perm = rng.permutation(len(y))
        cut = int(len(y) * 0.7)
        tr, va = perm[:cut], perm[cut:]
        torch.manual_seed(run)
        net = model().to(dev)
        opt = torch.optim.Adam(net.parameters(), lr=LR)
        best, bad, path = np.inf, 0, OUT / f"run{run}.pt"
        for ep in range(MAX_EPOCHS):
            t0 = time.time()
            net.train()
            order = rng.permutation(tr)
            for k in range(0, len(order), BATCH):
                j = np.sort(order[k:k + BATCH])
                opt.zero_grad()
                loss = lossf(net(to_tensor(Xp[j], mu, sd, dev)),
                             torch.from_numpy(y[j]).to(dev))
                loss.backward()
                opt.step()
            net.eval()
            tot = 0.0
            with torch.no_grad():
                for k in range(0, len(va), 1024):
                    j = np.sort(va[k:k + 1024])
                    tot += lossf(net(to_tensor(Xp[j], mu, sd, dev)),
                                 torch.from_numpy(y[j]).to(dev)).item() * len(j)
            vl = tot / len(va)
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


def ensemble_predict(images_uint8_or_packed, packed: bool) -> np.ndarray:
    import torch
    dev = "mps" if torch.backends.mps.is_available() else "cpu"
    nets = []
    for run in range(RUNS):
        ck = torch.load(OUT / f"run{run}.pt", map_location=dev)
        n = model().to(dev)
        n.load_state_dict(ck["state"])
        n.eval()
        nets.append((n, ck["mu"], ck["sd"]))
    src = images_uint8_or_packed
    out = np.zeros(len(src))
    with torch.no_grad():
        for k in range(0, len(src), 1024):
            chunk = src[k:k + 1024]
            pk = chunk if packed else np.packbits((np.asarray(chunk) > 0).reshape(len(chunk), -1), axis=1)
            p = 0
            for n, mu, sd in nets:
                p = p + torch.softmax(n(to_tensor(pk, mu, sd, dev)), 1)[:, 1].cpu().numpy()
            out[k:k + len(chunk)] = p / RUNS
    return out


# ---------------------------------------------------------------- portfolios
def decile_book(df, ret="ret", weight=None, cost=None):
    """Monthly H-L, deciles formed within each month. Returns gross and net series."""
    df = df.dropna(subset=["p", ret]).copy()
    df["dec"] = df.groupby("date")["p"].transform(
        lambda s: pd.qcut(s.rank(method="first"), 10, labels=False))
    months = sorted(df["date"].unique())
    res, prev = [], {0: {}, 9: {}}
    for m in months:
        g = df[df["date"] == m]
        row = {"date": m}
        for d, sign in ((9, 1), (0, -1)):
            x = g[g["dec"] == d]
            w = (x[weight] / x[weight].sum()) if weight else pd.Series(1 / len(x), index=x.index)
            row[d] = float((w * x[ret]).sum())
            if cost is not None:
                cur = dict(zip(x["id"], w))
                names = set(cur) | set(prev[d])
                c = sum(abs(cur.get(i, 0) - prev[d].get(i, 0)) * cost.get((m, i), cost.get(i, 0.002))
                        for i in names) if prev[d] else sum(cur[i] * cost.get((m, i), 0.002) for i in cur)
                row[f"c{d}"] = c
                prev[d] = cur
        res.append(row)
    r = pd.DataFrame(res).set_index("date")
    r["gross"] = r[9] - r[0]
    if cost is not None:
        # Borrow on the short decile, a month at a time (added 2026-10-26).
        import config
        r["borrow"] = config.COSTS.borrow_bps_annual / 1e4 / 12
        r["net"] = r["gross"] - r["c9"] - r["c0"] - r["borrow"]
    return r


def stats(s):
    s = s.dropna()
    return s.mean(), s.mean() / (s.std() / np.sqrt(len(s))), s.mean() / s.std() * np.sqrt(12), len(s)


def verify() -> int:
    parts = []
    for y in VERIFY_YEARS:
        X, L = load_year(y)
        p = ensemble_predict(X, packed=False)
        parts.append(pd.DataFrame({"date": L["Date"].astype(str).str[:10], "id": L["StockID"],
                                   "mcap": L["MarketCap"], "ret": L["Ret_20d"], "p": p}))
        print(f"  scored {y}: {len(L):,} images", flush=True)
    v = pd.concat(parts, ignore_index=True)
    v.to_parquet(OUT / "verify_predictions.parquet", index=False)
    ew = decile_book(v)
    vw = decile_book(v, weight="mcap")
    m, t, sh, n = stats(ew["gross"])
    mv, tv, shv, _ = stats(vw["gross"])
    print(f"\nVERIFICATION on the authors' 2001-2019 images ({n} months)")
    print(f"  equal-weight H-L: {m * 100:+.2f}%/month, t={t:.2f}, Sharpe {sh:.2f}   (paper: 2.16)")
    print(f"  value-weight H-L: {mv * 100:+.2f}%/month, t={tv:.2f}, Sharpe {shv:.2f}")
    ok = sh >= 0.5 * PAPER_EW_SHARPE
    json.dump({"ew_sharpe": sh, "vw_sharpe": shv, "months": n, "passed": bool(ok)},
              open(OUT / "verify.json", "w"), indent=2)
    print(f"  implementation {'VERIFIED' if ok else 'NOT VERIFIED'} "
          f"(rule: at least half the paper's Sharpe; step 2 refuses to run otherwise)")
    return 0


# ---------------------------------------------------------------- our images
def draw(o, h, l, c, ma, v):
    img = np.zeros((H, W), np.uint8)
    lo = np.nanmin(np.r_[l, ma])
    hi = np.nanmax(np.r_[h, ma])
    if not np.isfinite(lo) or hi <= lo:
        return None
    y = lambda p: (PH - 1) - np.rint((p - lo) / (hi - lo) * (PH - 1)).astype(int)
    for d in range(DAYS):
        if not np.isfinite([o[d], h[d], l[d], c[d]]).all():
            continue                         # missing day: column left blank (p.3199-3200)
        x = 3 * d
        img[y(o[d]), x] = 1
        img[y(h[d]):y(l[d]) + 1, x + 1] = 1
        img[y(c[d]), x + 2] = 1
    for d in range(DAYS - 1):
        if np.isfinite(ma[d]) and np.isfinite(ma[d + 1]):
            y0, y1 = y(ma[d]), y(ma[d + 1])
            for k, x in enumerate(range(3 * d + 1, 3 * d + 5)):
                img[int(round(y0 + (y1 - y0) * k / 3)), x] = 1
    vmax = np.nanmax(v)
    if vmax > 0:
        for d in range(DAYS):
            if np.isfinite(v[d]) and v[d] > 0:
                hv = int(np.rint(v[d] / vmax * VH))
                if hv > 0:
                    img[H - hv:, 3 * d + 1] = 1
    return img


def render() -> int:
    import db
    import lockbox
    end = lockbox.state()["seal_from"]
    max_date = (pd.Timestamp(end) - pd.Timedelta(days=1)).date().isoformat()
    lockbox.guard(max_date, "jkx_replication render")
    with db.connect() as con:
        d = pd.read_sql("SELECT b.symbol, b.dt, b.open, b.high, b.low, b.close, b.adj_close, "
                        "b.volume FROM bars b JOIN symbols s USING(symbol) WHERE s.kind='stock' "
                        "AND b.dt <= ?", con, params=(max_date,))
    cal = np.array(sorted(d["dt"].unique()))
    month = pd.Index(cal).str[:7]
    me = [i for i in range(len(cal) - 1) if month[i] != month[i + 1]]   # last session of each month
    me = [i for i in me if cal[i] >= "2020-01-01" and i + 21 < len(cal)]
    pos = {x: i for i, x in enumerate(cal)}
    packs, meta = [], []
    for n, (sym, g) in enumerate(d.groupby("symbol", sort=True)):
        ii = g["dt"].map(pos).to_numpy()
        A = {k: np.full(len(cal), np.nan) for k in ("o", "h", "l", "c", "v", "tr", "aopen")}
        A["o"][ii], A["h"][ii], A["l"][ii] = g["open"], g["high"], g["low"]
        A["c"][ii], A["v"][ii], A["tr"][ii] = g["close"], g["volume"], g["adj_close"]
        A["aopen"][ii] = g["open"] * g["adj_close"] / g["close"]
        ma = pd.Series(A["c"]).rolling(DAYS, min_periods=1).mean().to_numpy()
        dv = pd.Series(A["c"] * A["v"]).rolling(20, min_periods=10).mean().to_numpy()
        for i in me:
            s = slice(i - DAYS + 1, i + 1)
            # IPO or delisting inside the window excludes the image (p.3199)
            if np.isnan(A["c"][i - DAYS + 1]) or np.isnan(A["c"][i]):
                continue
            img = draw(A["o"][s], A["h"][s], A["l"][s], A["c"][s], ma[s], A["v"][s])
            if img is None:
                continue
            ret = A["tr"][i + 20] / A["tr"][i] - 1                    # Ret_20d, total return
            oo = A["aopen"][i + 21] / A["aopen"][i + 1] - 1          # next-open variant
            packs.append(np.packbits(img.reshape(-1)))
            meta.append((sym, cal[i], ret, oo, dv[i]))
        if n % 300 == 0:
            print(f"  {n} stocks, {len(packs):,} images", flush=True)
    np.save(OUT / "ours_X.npy", np.stack(packs))
    pd.DataFrame(meta, columns=["id", "date", "ret", "ret_oo", "dollar_vol"]).to_parquet(
        OUT / "ours_meta.parquet", index=False)
    print(f"{len(packs):,} images, {len(me)} month-ends ({cal[me[0]]} to {cal[me[-1]]})")
    return 0


def test() -> int:
    from evaluation import clustered_mean
    vj = OUT / "verify.json"
    if not vj.exists() or not json.load(open(vj))["passed"]:
        print("REFUSING: the implementation has not reproduced the paper on its own data.")
        return 1
    X = np.load(OUT / "ours_X.npy")
    m = pd.read_parquet(OUT / "ours_meta.parquet")
    m["p"] = ensemble_predict(X, packed=True)
    m.to_parquet(OUT / "ours_predictions.parquet", index=False)
    # size buckets for the paper's cost rule, by dollar volume within each month
    m["big"] = m.groupby("date")["dollar_vol"].transform(lambda s: s >= s.quantile(0.8))
    cost = {(d, i): (0.001 if b else 0.002) for d, i, b in zip(m["date"], m["id"], m["big"])}
    ew = decile_book(m, cost=cost)
    g, n_ = ew["gross"], ew["net"]
    mg, tg, sg, n = stats(g)
    mn, tn, sn, _ = stats(n_)
    print(f"\nTRIAL 52 OUT-OF-SAMPLE TEST, {n} months {ew.index.min()} to {ew.index.max()}, "
          f"{m.groupby('date').size().median():.0f} stocks a month")
    print(f"  gross H-L {mg * 100:+.2f}%/month  t={tg:.2f}  Sharpe {sg:.2f}   "
          f"{'PASS' if tg >= 2 else 'FAIL'}")
    print(f"  net H-L   {mn * 100:+.2f}%/month  t={tn:.2f}  Sharpe {sn:.2f}   "
          f"{'PASS' if tn >= 2 else 'FAIL'}")
    print(f"  VERDICT: {'PASS' if tg >= 2 and tn >= 2 else 'FAIL'}\n")
    print("descriptive only:")
    c = clustered_mean(g, pd.Series(g.index))
    print(f"  concentration: top-5 months carry {c['top5_share']:.0%}; trimmed mean "
          f"{c['trimmed_mean'] * 100:+.2f}%   alarm={c['alarm']}")
    dvw = decile_book(m, weight="dollar_vol")
    print("  dollar-volume-weighted gross H-L: %+.2f%%/month t=%.2f Sharpe %.2f" % (
        stats(dvw["gross"])[0] * 100, stats(dvw["gross"])[1], stats(dvw["gross"])[2]))
    top = m[m.groupby("date")["dollar_vol"].rank(ascending=False) <= 500]
    t5 = decile_book(top)
    print("  500 largest by dollar volume (paper Table IV analogue): %+.2f%%/month t=%.2f Sharpe %.2f" % (
        stats(t5["gross"])[0] * 100, stats(t5["gross"])[1], stats(t5["gross"])[2]))
    oo = decile_book(m, ret="ret_oo")
    print("  next-open execution: %+.2f%%/month t=%.2f" % (stats(oo["gross"])[0] * 100, stats(oo["gross"])[1]))
    dec = m.dropna(subset=["ret"]).assign(dec=lambda x: x.groupby("date")["p"].transform(
        lambda s: pd.qcut(s.rank(method="first"), 10, labels=False)))
    print("  mean monthly return by decile (%): " + " ".join(
        f"{v * 100:+.2f}" for v in dec.groupby(["date", "dec"])["ret"].mean().groupby("dec").mean()))
    ew.to_csv(ROOT / "reports" / "jkx_replication_monthly.csv")
    print("\nwrote reports/jkx_replication_monthly.csv")
    return 0


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "test"
    sys.exit({"pack": pack, "train": train, "verify": verify,
              "render": render, "test": test}[cmd]())
