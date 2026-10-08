#!/usr/bin/env python3
"""Do insider buying or short interest survive factor neutralization?

    python3 python/alt_book.py --horizon 63

THE ONLY QUESTION THAT MATTERS HERE. Every signal in this project so far has
residualized to nothing. The earnings book looked like 0.34 and became -0.17 once
beta, size and momentum were removed, because the "surprise" was partly measuring
price momentum under another name. A raw Sharpe is no longer interesting; a
residual Sharpe is.

So every signal is reported twice: as measured, and after a daily cross-sectional
regression on beta, size and momentum. A signal whose return disappears under
that regression is a factor tilt in a costume, however good the headline looks.

SAMPLE NOTE. Insider filings run 2014-2026. Short interest only reaches back to
2021 because that is all FINRA's API retains, which leaves roughly twenty
independent observations at a 63-day horizon. That is too few to conclude
anything from, and the short-interest rows are reported with that caveat rather
than quietly pooled with the longer series.
"""

from __future__ import annotations

import argparse
import sys

import numpy as np
import pandas as pd

import config
import db
import lockbox
import portfolio as P
from alt_signals import SIGNALS, attach, clean_insider, insider_panel


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--horizon", type=int, default=63)
    ap.add_argument("--quantiles", type=int, default=5)
    ap.add_argument("--min-adv", type=float, default=5e6)
    args = ap.parse_args()
    h, q = args.horizon, args.quantiles

    st = lockbox.state()
    max_date = (pd.Timestamp(st["seal_from"]) - pd.Timedelta(days=1)).date().isoformat()
    lockbox.guard(max_date, "alt_book")

    panel = P.build_panel(max_date, h, args.min_adv)
    with db.connect() as con:
        trades = pd.read_sql(
            "SELECT symbol, filing_date, trans_date, trans_code, shares, price "
            "FROM insider_trades WHERE trans_code IN ('P','S')", con)
        bars = pd.read_sql("SELECT symbol, dt, close FROM bars", con)
        sess = pd.read_sql("SELECT dt, day_index FROM trading_days ORDER BY dt", con)
        si = pd.read_sql("SELECT symbol, tradeable_from, short_shares, prev_short, "
                         "avg_daily_vol, days_to_cover FROM short_interest", con)

    n0 = len(trades)
    trades = clean_insider(trades, bars)
    print(f"  insider trades: {n0:,} -> {len(trades):,} after market-price valuation")
    print(f"  short interest: {len(si):,} observations from {si['tradeable_from'].min()}")

    ins = insider_panel(trades, sess, panel[["symbol", "dt"]])
    panel = attach(panel, ins, si)
    cov = panel[SIGNALS].notna().mean()
    print("  coverage:", "  ".join(f"{k.split('_')[0][:6]}={v:.0%}" for k, v in cov.items()))

    rows = []
    for sig in SIGNALS:
        d = panel.dropna(subset=[sig]).copy()
        if len(d) < 50_000:
            continue
        d["s"] = P.zs(d[sig], [d["dt"], d["sector"]])
        d = d.dropna(subset=["s"])
        raw = P.stat(P.run(d, "s", "quintile", h, q), h)
        d["s_res"] = P.residualize(d, "s", ["beta", "size", "mom126"])
        dd = d.dropna(subset=["s_res"])
        res = P.stat(P.run(dd, "s_res", "quintile", h, q), h) if len(dd) > 50_000 else {}
        if raw:
            rows.append({"signal": sig, "n_obs": raw["n"],
                         "raw_sharpe": raw["sharpe"], "raw_t": raw["t"],
                         "res_sharpe": res.get("sharpe"), "res_t": res.get("t"),
                         "first_dt": d["dt"].min()})

    out = pd.DataFrame(rows)
    out.to_csv(config.ROOT / "reports" / "alt_book.csv", index=False)
    f = lambda v, s="{:.2f}": s.format(v) if (v is not None and pd.notna(v)) else "    -"
    print(f"\n{'=' * 84}")
    print("INSIDER AND SHORT-INTEREST SIGNALS — as measured, and factor-neutralized")
    print("=" * 84)
    print(f"{'signal':<24}{'from':>12}{'AS-IS Sh':>10}{'t':>7}"
          f"{'RESIDUAL Sh':>13}{'t':>7}{'survives?':>11}")
    for _, r in out.sort_values("res_sharpe", ascending=False, na_position="last").iterrows():
        surv = ("YES" if (pd.notna(r.res_sharpe) and r.res_sharpe > 0.2
                          and pd.notna(r.res_t) and abs(r.res_t) > 1.5) else "no")
        print(f"{r.signal:<24}{r.first_dt:>12}{f(r.raw_sharpe):>10}{f(r.raw_t):>7}"
              f"{f(r.res_sharpe):>13}{f(r.res_t):>7}{surv:>11}")

    print("\nFor comparison, the previous best book (earnings surprise, large caps):")
    print(f"{'earnings sue+surprise':<24}{'2014-01':>12}{'0.34':>10}{'1.21':>7}"
          f"{'-0.17':>13}{'-0.60':>7}{'no':>11}")
    print("\nA signal that keeps its Sharpe through the residual column is measuring")
    print("something beta, size and momentum do not already explain. Nothing in this")
    print("project has managed that yet.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
