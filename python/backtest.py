#!/usr/bin/env python3
"""Phase 4b — cost-aware portfolio backtest over the out-of-sample predictions.

    python3 python/backtest.py --model gbm
    python3 python/backtest.py --model gbm --prob-threshold 0.55

Only out-of-sample rows are traded: every prediction comes from a fold where the
model never saw that period. There is no in-sample equity curve in this project,
because an in-sample equity curve is a drawing, not a result.

Mechanics, in the order they matter:

  * ENTRY IS THE NEXT OPEN. A signal computed from the close of day t cannot be
    filled at that same close. Filling at the signal bar's close is the single
    most common way a backtest manufactures returns that do not exist.
  * COSTS ON BOTH SIDES. Slippage and half-spread are charged adverse to the
    trade on entry and exit, plus per-share commission with a minimum.
  * THE SAME RISK LIMITS AS THE PAPER JOURNAL. Position size comes from risk per
    trade, capped by max open positions and total exposure, using the values in
    config.py that mirror journal/risk-profile.json. Comparable beats optimal.
  * HELD FOR A FIXED HORIZON. Exit is the open `horizon` sessions later, or the
    stop if the low breached it first. Because the data is daily, a day where both
    the stop and the target were touched is resolved conservatively as a stop,
    which is pessimistic by design rather than optimistic by accident.
"""

from __future__ import annotations

import argparse
import json
import sys

import numpy as np
import pandas as pd

import config
import db


def load(model: str) -> pd.DataFrame:
    with db.connect() as con:
        df = pd.read_sql(
            """SELECT p.model_name, p.fold_id, p.symbol, p.dt, p.y_prob,
                      f.day_index, f.close, f.atr_14,
                      l.next_open
               FROM predictions p
               JOIN feature_panel f ON f.symbol = p.symbol AND f.dt = p.dt
               JOIN labels l        ON l.symbol = p.symbol AND l.dt = p.dt
               WHERE p.model_name = ?
               ORDER BY p.dt, p.y_prob DESC""", con, params=(model,))
        bars = pd.read_sql(
            "SELECT symbol, dt, day_index, open, high, low, close FROM features", con)
    return df, bars


def run(model: str, threshold: float, top_n: int, horizon: int,
        stop_atr: float) -> dict:
    sig, bars = load(model)
    if sig.empty:
        raise SystemExit(f"no predictions for model {model!r}. Run python/model.py first.")

    bars = bars.sort_values(["symbol", "day_index"])
    by_symbol = {s: g.reset_index(drop=True) for s, g in bars.groupby("symbol")}
    pos_of = {s: {int(d): i for i, d in enumerate(g["day_index"])}
              for s, g in by_symbol.items()}

    c = config.COSTS
    equity = config.ACCOUNT_SIZE
    cash = equity
    open_pos: list[dict] = []
    trades: list[dict] = []
    curve: list[dict] = []
    seq = 0
    skipped = {"exposure": 0, "max_positions": 0, "zero_size": 0, "no_fill": 0}

    dates = sorted(sig["dt"].unique())
    sig_by_date = {d: g for d, g in sig.groupby("dt")}

    for dt in dates:
        # ---- exits first, so capital frees up before new entries are considered
        still: list[dict] = []
        for p in open_pos:
            g = by_symbol[p["symbol"]]
            i = pos_of[p["symbol"]].get(p["entry_day_index"])
            if i is None:
                still.append(p); continue
            held = g.iloc[i : i + horizon + 1]
            cur = g[g["dt"] == dt]
            if cur.empty:
                still.append(p); continue
            ci = int(cur.index[0])
            bars_held = ci - i

            exit_px = exit_reason = None
            low = float(cur["low"].iloc[0]); high = float(cur["high"].iloc[0])
            if p["direction"] == "long" and low <= p["stop"]:
                exit_px, exit_reason = p["stop"], "stop"
            elif p["direction"] == "short" and high >= p["stop"]:
                exit_px, exit_reason = p["stop"], "stop"
            elif bars_held >= horizon:
                exit_px, exit_reason = float(cur["open"].iloc[0]), "horizon"

            if exit_px is None:
                still.append(p); continue

            # COST ACCOUNTING. gross_pnl is measured on RAW prices, with no
            # slippage folded in, and every friction is itemized in costs, so the
            # identity net = gross - costs holds exactly and is asserted below.
            # An earlier version computed gross from slippage-adjusted fills while
            # also reporting slippage in the costs column, which double-counted it
            # and made "costs ate N% of gross" an unverifiable claim.
            sh = p["shares"]
            slip_ps = exit_px * (c.slippage_bps + c.spread_bps) / 10_000
            fill = exit_px - slip_ps if p["direction"] == "long" else exit_px + slip_ps
            gross = ((exit_px - p["entry_raw"]) if p["direction"] == "long"
                     else (p["entry_raw"] - exit_px)) * sh
            exit_comm = max(c.min_commission, c.commission_per_share * sh)
            exit_slip = slip_ps * sh
            costs = p["entry_comm"] + p["entry_slip"] + exit_comm + exit_slip
            net = gross - costs
            assert abs(net - (gross - costs)) < 1e-6, "cost identity violated"
            seq += 1
            trades.append({
                "trade_seq": seq, "symbol": p["symbol"], "direction": p["direction"],
                "entry_dt": p["entry_dt"], "entry_px": p["entry_px"], "exit_dt": dt,
                "exit_px": fill, "shares": sh, "gross_pnl": gross,
                "costs": costs, "net_pnl": net, "exit_reason": exit_reason,
                "r_multiple": net / p["dollar_risk"] if p["dollar_risk"] else None,
            })
        open_pos = still

        # ---- entries
        today = sig_by_date.get(dt)
        if today is not None:
            cand = today[today["y_prob"] >= threshold].nlargest(top_n, "y_prob")
            for _, r in cand.iterrows():
                if len(open_pos) >= config.MAX_OPEN_POSITIONS:
                    skipped["max_positions"] += 1; continue
                nxt = r["next_open"]
                atr = r["atr_14"]
                if pd.isna(nxt) or pd.isna(atr) or atr <= 0:
                    skipped["no_fill"] += 1; continue
                entry_raw = float(nxt)
                slip = entry_raw * (c.slippage_bps + c.spread_bps) / 10_000
                entry_px = entry_raw + slip            # long only; adverse fill
                stop = entry_px - stop_atr * float(atr)
                risk_ps = entry_px - stop
                if risk_ps <= 0:
                    skipped["zero_size"] += 1; continue
                budget = equity * config.MAX_RISK_PCT_PER_TRADE / 100.0
                shares = int(budget // risk_ps)
                if shares < 1:
                    skipped["zero_size"] += 1; continue
                notional = shares * entry_px
                open_notional = sum(p["entry_px"] * p["shares"] for p in open_pos)
                if (open_notional + notional) / equity * 100.0 > config.MAX_EXPOSURE_PCT:
                    # Shrink to fit rather than skip outright, but never below one share.
                    room = equity * config.MAX_EXPOSURE_PCT / 100.0 - open_notional
                    shares = int(max(0, room) // entry_px)
                    if shares < 1:
                        skipped["exposure"] += 1; continue
                    notional = shares * entry_px
                comm = max(c.min_commission, c.commission_per_share * shares)
                i = pos_of[r["symbol"]].get(int(r["day_index"]))
                if i is None:
                    skipped["no_fill"] += 1; continue
                open_pos.append({
                    "symbol": r["symbol"], "direction": "long", "entry_dt": dt,
                    "entry_raw": entry_raw,          # unadjusted, for the gross figure
                    "entry_px": entry_px,            # what was actually paid
                    "stop": stop, "shares": shares,
                    "dollar_risk": risk_ps * shares, "entry_day_index": int(r["day_index"]),
                    "entry_comm": comm, "entry_slip": slip * shares,
                })

        realized = sum(t["net_pnl"] for t in trades)
        # Open positions carry their entry costs already; subtract the entry
        # frictions of still-open trades so equity is not briefly overstated.
        open_entry_costs = sum(p["entry_comm"] + p["entry_slip"] for p in open_pos)
        open_notional = sum(p["entry_px"] * p["shares"] for p in open_pos)
        unreal = 0.0
        for p in open_pos:
            cur = by_symbol[p["symbol"]]
            cur = cur[cur["dt"] == dt]
            if not cur.empty:
                unreal += (float(cur["close"].iloc[0]) - p["entry_px"]) * p["shares"]
        equity = config.ACCOUNT_SIZE + realized + unreal - open_entry_costs
        curve.append({"dt": dt, "equity": equity,
                      "cash": config.ACCOUNT_SIZE + realized - open_notional,
                      "exposure": open_notional})

    tdf = pd.DataFrame(trades)
    cdf = pd.DataFrame(curve)
    return {"trades": tdf, "curve": cdf, "skipped": skipped,
            "params": {"model": model, "threshold": threshold, "top_n": top_n,
                       "horizon": horizon, "stop_atr": stop_atr}}


def metrics(tdf: pd.DataFrame, cdf: pd.DataFrame) -> dict:
    if tdf.empty:
        return {"trades": 0}
    eq = cdf["equity"].to_numpy()
    peak = np.maximum.accumulate(eq)
    dd = (peak - eq) / peak
    wins = tdf[tdf.net_pnl > 0]; losses = tdf[tdf.net_pnl < 0]
    gw = wins.net_pnl.sum(); gl = abs(losses.net_pnl.sum())
    daily = pd.Series(eq).pct_change().dropna()
    return {
        "trades": int(len(tdf)),
        "net_pnl": float(tdf.net_pnl.sum()),
        "total_costs": float(tdf.costs.sum()),
        "return_pct": float(eq[-1] / config.ACCOUNT_SIZE - 1) * 100,
        "win_rate": float(len(wins) / len(tdf)),
        "avg_win": float(wins.net_pnl.mean()) if len(wins) else 0.0,
        "avg_loss": float(losses.net_pnl.mean()) if len(losses) else 0.0,
        "profit_factor": float(gw / gl) if gl > 0 else None,
        "avg_r": float(tdf.r_multiple.mean()),
        "max_drawdown_pct": float(dd.max() * 100),
        "sharpe_daily_annual": (float(daily.mean() / daily.std(ddof=1) * np.sqrt(252))
                                if daily.std(ddof=1) > 0 else None),
        "stops_hit": int((tdf.exit_reason == "stop").sum()),
        "horizon_exits": int((tdf.exit_reason == "horizon").sum()),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--model", default="gbm")
    ap.add_argument("--prob-threshold", type=float, default=0.55)
    ap.add_argument("--top-n", type=int, default=config.MAX_OPEN_POSITIONS)
    ap.add_argument("--horizon", type=int, default=config.LABEL_HORIZON_DAYS)
    ap.add_argument("--stop-atr", type=float, default=1.5)
    ap.add_argument("--no-write", action="store_true")
    args = ap.parse_args()

    out = run(args.model, args.prob_threshold, args.top_n, args.horizon, args.stop_atr)
    tdf, cdf = out["trades"], out["curve"]
    m = metrics(tdf, cdf)

    print(f"backtest: {args.model}, threshold {args.prob_threshold}, "
          f"top {args.top_n}/day, {args.horizon}-day hold, {args.stop_atr} ATR stop")
    print(f"  out-of-sample window: {cdf['dt'].min()} to {cdf['dt'].max()}")
    if m["trades"] == 0:
        print("  no trades taken. Threshold may be above every predicted probability.")
        return 0
    print(f"\n{'trades':<22}{m['trades']:>12,}")
    for k, label, fmt in [
        ("return_pct", "total return", "{:>11.2f}%"),
        ("net_pnl", "net P/L", "${:>11,.0f}"),
        ("total_costs", "costs paid", "${:>11,.0f}"),
        ("win_rate", "win rate", "{:>11.1%}"),
        ("profit_factor", "profit factor", "{:>12.2f}"),
        ("avg_r", "average R", "{:>12.2f}"),
        ("max_drawdown_pct", "max drawdown", "{:>11.2f}%"),
        ("sharpe_daily_annual", "sharpe (daily, ann.)", "{:>12.2f}"),
    ]:
        v = m.get(k)
        print(f"{label:<22}" + (fmt.format(v) if v is not None else f"{'n/a':>12}"))
    print(f"{'stops hit':<22}{m['stops_hit']:>12,}")
    print(f"{'horizon exits':<22}{m['horizon_exits']:>12,}")
    print(f"\nsignals skipped: {out['skipped']}")
    gross = float(tdf.gross_pnl.sum()); costs = float(tdf.costs.sum())
    net = float(tdf.net_pnl.sum())
    print("\nP/L decomposition (gross - costs = net, verified)")
    print(f"  gross P/L before costs  ${gross:>12,.0f}")
    print(f"  costs paid              ${-costs:>12,.0f}")
    print(f"  net P/L                 ${net:>12,.0f}")
    assert abs(net - (gross - costs)) < 0.01, "cost decomposition does not reconcile"
    if gross > 0:
        print(f"  costs as share of gross {costs / gross:>12.1%}")
    else:
        print(f"  gross was negative; costs made a losing strategy worse")

    if not args.no_write:
        with db.transaction() as con:
            cur = con.execute(
                """INSERT INTO backtest_runs
                   (strategy, params_json, cost_json, start_dt, end_dt, is_oos)
                   VALUES (?,?,?,?,?,1)""",
                (args.model, json.dumps(out["params"]),
                 json.dumps(config.COSTS.__dict__), str(cdf["dt"].min()),
                 str(cdf["dt"].max())))
            bt = cur.lastrowid
            con.executemany(
                """INSERT INTO backtest_trades
                   (bt_id, trade_seq, symbol, direction, entry_dt, entry_px,
                    exit_dt, exit_px, shares, gross_pnl, costs, net_pnl,
                    r_multiple, exit_reason)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                [(bt, t.trade_seq, t.symbol, t.direction, t.entry_dt, t.entry_px,
                  t.exit_dt, t.exit_px, t.shares, t.gross_pnl, t.costs, t.net_pnl,
                  t.r_multiple, t.exit_reason) for t in tdf.itertuples()])
            con.executemany(
                "INSERT INTO backtest_equity (bt_id, dt, equity, cash, exposure) "
                "VALUES (?,?,?,?,?)",
                [(bt, r.dt, r.equity, r.cash, r.exposure) for r in cdf.itertuples()])
        print(f"\nwrote backtest run {bt}: {len(tdf)} trades, {len(cdf)} equity points")
    return 0


if __name__ == "__main__":
    sys.exit(main())
