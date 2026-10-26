#!/usr/bin/env python3
"""Draw 20-day chart images for every stock, every week (trial 50).

    python3 python/cnn_images.py

Follows Jiang, Kelly & Xiu (2023, Journal of Finance), section I:
  - 64 x 60 pixels: 20 days x 3 pixels (open tick, high-low bar, close tick)
  - black background, white marks, no colour
  - a 20-day moving average line through the middle pixel of each day
  - volume bars in the bottom fifth
  - each image scaled so its own highest and lowest price touch the top and
    bottom, which puts every stock on the same visual scale

Prices are split- and dividend-adjusted. One image per stock every 5 sessions,
on a common calendar, so every week's images can be ranked against each other.
Images are bit-packed (480 bytes each) to fit in memory on a laptop.
Nothing on or after the 2026-10-07 lockbox seal is read.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "python"))

import db
import lockbox

OUT = ROOT / "data" / "cnn"
DAYS, H, W = 20, 64, 60
PH = 51          # price area: rows 0-50
VH = 12          # volume area: rows 52-63, row 51 left empty as a gap
STEP = 5


def draw(o, h, l, c, ma, v) -> np.ndarray:
    img = np.zeros((H, W), np.uint8)
    lo = min(l.min(), np.nanmin(ma))
    hi = max(h.max(), np.nanmax(ma))
    if not np.isfinite(lo) or hi <= lo:
        return None
    y = lambda p: (PH - 1) - np.rint((p - lo) / (hi - lo) * (PH - 1)).astype(int)
    yo, yh, yl, yc, ym = y(o), y(h), y(l), y(c), y(ma)
    for d in range(DAYS):
        x = 3 * d
        img[yo[d], x] = 1
        img[yh[d]:yl[d] + 1, x + 1] = 1
        img[yc[d], x + 2] = 1
    # moving average: a dot per day, joined by straight lines
    for d in range(DAYS - 1):
        x0, x1 = 3 * d + 1, 3 * d + 4
        for k, x in enumerate(range(x0, x1 + 1)):
            img[int(round(ym[d] + (ym[d + 1] - ym[d]) * k / 3)), x] = 1
    vmax = v.max()
    if vmax > 0:
        hv = np.rint(v / vmax * VH).astype(int)
        for d in range(DAYS):
            if hv[d] > 0:
                img[H - hv[d]:, 3 * d + 1] = 1
    return img


def main() -> int:
    end = lockbox.state()["seal_from"]
    max_date = (pd.Timestamp(end) - pd.Timedelta(days=1)).date().isoformat()
    lockbox.guard(max_date, "cnn_images")
    with db.connect() as con:
        d = pd.read_sql("SELECT b.symbol, b.dt, b.open, b.high, b.low, b.close, "
                        "b.adj_close, b.volume FROM bars b JOIN symbols s USING(symbol) "
                        "WHERE s.kind='stock' AND b.dt <= ?", con, params=(max_date,))
    cal = np.array(sorted(d["dt"].unique()))
    pos = {dt: i for i, dt in enumerate(cal)}
    img_idx = np.arange(DAYS + 19, len(cal), STEP)            # common weekly calendar
    print(f"{d['symbol'].nunique()} stocks, {len(cal)} sessions, {len(img_idx)} image dates")

    OUT.mkdir(parents=True, exist_ok=True)
    packs, meta = [], []
    for n, (sym, g) in enumerate(d.groupby("symbol", sort=True)):
        g = g.sort_values("dt")
        full = np.full(len(cal), np.nan)
        cols = {}
        r = (g["adj_close"] / g["close"]).to_numpy()
        ii = g["dt"].map(pos).to_numpy()
        for k, src in (("o", g["open"].to_numpy() * r), ("h", g["high"].to_numpy() * r),
                       ("l", g["low"].to_numpy() * r), ("c", g["adj_close"].to_numpy()),
                       ("v", g["volume"].to_numpy(float))):
            a = full.copy()
            a[ii] = src
            cols[k] = a
        ma = pd.Series(cols["c"]).rolling(DAYS, min_periods=DAYS).mean().to_numpy()
        for i in img_idx:
            s = slice(i - DAYS + 1, i + 1)
            block = [cols[k][s] for k in "ohlcv"] + [ma[s]]
            if any(np.isnan(b).any() for b in block):
                continue
            img = draw(*block[:4], block[5], block[4])
            if img is None:
                continue
            c = cols["c"]
            r5 = c[i + 5] / c[i] - 1 if i + 5 < len(cal) else np.nan
            oo = (cols["o"][i + 6] / cols["o"][i + 1] - 1) if i + 6 < len(cal) else np.nan
            packs.append(np.packbits(img))
            meta.append((sym, cal[i], r5, oo))
        if n % 250 == 0:
            print(f"  {n} stocks, {len(packs):,} images")
    X = np.stack(packs)
    np.save(OUT / "images_i20.npy", X)
    m = pd.DataFrame(meta, columns=["symbol", "dt", "r5_cc", "r5_oo"])
    m.to_parquet(OUT / "meta_i20.parquet", index=False)
    print(f"\n{len(X):,} images, {X.nbytes / 1e6:.0f}MB packed -> {OUT}")
    print(f"  up-label share (close-to-close, 5 days): {(m['r5_cc'] > 0).mean():.1%}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
