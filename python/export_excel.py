#!/usr/bin/env python3
"""Phase 6 — write results back into Excel for a non-technical reader.

    python3 python/export_excel.py

The loop closes where it started. Data entered through a workbook, went through
SQL and Python and R, and the conclusion comes back as a workbook someone can
open without installing anything.

The first sheet states the finding in plain language before any number appears.
A dashboard that makes a reader derive the conclusion from a metrics table has
not finished the job.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

import config
import db


def read_csv_if(path: Path) -> pd.DataFrame:
    return pd.read_csv(path) if path.exists() else pd.DataFrame()


def main() -> int:
    reports = config.ROOT / "reports"
    figures = config.FIGURES
    out = config.DASHBOARD_XLSX
    config.ensure_dirs()

    with db.connect() as con:
        bt = con.execute("SELECT MAX(bt_id) AS id FROM backtest_runs").fetchone()["id"]
        if bt is None:
            print("no backtest found. Run python3 python/backtest.py first.", file=sys.stderr)
            return 1
        trades = pd.read_sql(
            "SELECT * FROM backtest_trades WHERE bt_id=? ORDER BY trade_seq",
            con, params=(bt,))
        equity = pd.read_sql(
            "SELECT dt, equity, exposure, drawdown FROM v_equity_drawdown "
            "WHERE bt_id=? ORDER BY dt", con, params=(bt,))
        spy = pd.read_sql(
            "SELECT dt, adj_close FROM bars WHERE symbol='SPY' AND dt BETWEEN ? AND ? "
            "ORDER BY dt", con, params=(equity.dt.min(), equity.dt.max()))
        dq = pd.read_sql(
            "SELECT symbol, dt, severity, issue, detail FROM data_quality_issues "
            "ORDER BY issue, dt", con)
        folds = pd.read_sql("SELECT * FROM wf_folds ORDER BY fold_id", con)
        coverage = pd.read_sql(
            "SELECT symbol, COUNT(*) bars, MIN(dt) first_bar, MAX(dt) last_bar "
            "FROM bars GROUP BY symbol ORDER BY symbol", con)

    summary = read_csv_if(reports / "model_summary.csv")
    validation = read_csv_if(reports / "validation.csv")
    rc = read_csv_if(reports / "reality_check.csv")
    importance = read_csv_if(reports / "feature_importance.csv")
    fold_metrics = read_csv_if(reports / "fold_metrics.csv")
    sens = read_csv_if(reports / "sensitivity.csv")

    # Headline numbers, computed here rather than copied, so the dashboard cannot
    # drift from the warehouse.
    strat_total = equity.equity.iloc[-1] / equity.equity.iloc[0] - 1
    spy_total = spy.adj_close.iloc[-1] / spy.adj_close.iloc[0] - 1
    years = (pd.to_datetime(equity.dt.iloc[-1]) - pd.to_datetime(equity.dt.iloc[0])).days / 365.25
    gross, costs, net = trades.gross_pnl.sum(), trades.costs.sum(), trades.net_pnl.sum()
    p_rc = float(rc.reality_check_p.iloc[0]) if not rc.empty else float("nan")

    with pd.ExcelWriter(out, engine="xlsxwriter") as xl:
        wb = xl.book
        f_h1 = wb.add_format({"bold": True, "font_size": 16, "font_color": "#1F3864"})
        f_h2 = wb.add_format({"bold": True, "font_size": 12, "font_color": "#1F3864",
                              "bottom": 2, "border_color": "#1F3864"})
        f_body = wb.add_format({"text_wrap": True, "valign": "top", "font_size": 11})
        f_lab = wb.add_format({"bold": True, "bg_color": "#F2F2F2", "border": 1})
        f_num = wb.add_format({"num_format": "#,##0.00", "border": 1})
        f_pct = wb.add_format({"num_format": "0.00%", "border": 1})
        f_cur = wb.add_format({"num_format": "$#,##0", "border": 1})
        f_bad = wb.add_format({"num_format": "0.00%", "border": 1, "bg_color": "#FFC7CE",
                               "font_color": "#9C0006", "bold": True})
        f_good = wb.add_format({"num_format": "0.00%", "border": 1, "bg_color": "#C6EFCE",
                                "font_color": "#006100", "bold": True})
        f_head = wb.add_format({"bold": True, "bg_color": "#1F3864", "font_color": "white",
                                "border": 1, "text_wrap": True, "valign": "vcenter"})

        # ------------------------------------------------------------ Findings
        ws = wb.add_worksheet("Findings")
        xl.sheets["Findings"] = ws
        ws.set_column("A:A", 34); ws.set_column("B:B", 18); ws.set_column("C:C", 62)
        ws.hide_gridlines(2)
        ws.write("A1", "Equity Signal Research Pipeline", f_h1)
        ws.write("A2", "Does a short-horizon equity signal have a real edge after costs?", f_body)

        ws.write("A4", "THE ANSWER", f_h2)
        ws.merge_range("A5:C9",
            "No. A gradient-boosted model produced a small gross edge out of sample, but "
            "transaction costs consumed roughly three quarters of it, and what remained is "
            "not statistically distinguishable from the passive baseline once data snooping "
            "is accounted for.\n\n"
            f"The best of three strategies beat an always-long baseline by {validation.excess.max()*100:.3f}% "
            f"per 5-day period. White's Reality Check puts that at p = {p_rc:.3f}, well inside "
            "the range chance produces when several ideas are tested on one history.",
            f_body)

        ws.write("A11", "HEADLINE NUMBERS", f_h2)
        rows = [
            ("Strategy total return", strat_total, "pct_bad"),
            ("SPY buy and hold, same window", spy_total, "pct_good"),
            ("Strategy annualized", (1 + strat_total) ** (1 / years) - 1, "pct"),
            ("Benchmark annualized", (1 + spy_total) ** (1 / years) - 1, "pct"),
            ("Gross P/L before costs", gross, "cur"),
            ("Transaction costs paid", -costs, "cur"),
            ("Net P/L", net, "cur"),
            ("Costs as share of gross", costs / gross if gross else 0, "pct_bad"),
            ("Max drawdown", equity.drawdown.max(), "pct"),
            ("Round trips", len(trades), "num"),
            ("Reality Check p-value", p_rc, "num"),
        ]
        if not sens.empty:
            rows += [
                ("Parameter variants tested", len(sens), "num"),
                ("Best variant return", sens.return_pct.max() / 100, "pct"),
                ("Worst variant return", sens.return_pct.min() / 100, "pct"),
                ("Variants beating SPY", int((sens.return_pct > spy_total * 100).sum()), "num"),
            ]
        r = 11
        for label, val, kind in rows:
            fmt = {"pct": f_pct, "pct_bad": f_bad, "pct_good": f_good,
                   "cur": f_cur, "num": f_num}[kind]
            ws.write(r, 0, label, f_lab); ws.write_number(r, 1, float(val), fmt)
            r += 1

        ws.write(r + 1, 0, "HOW TO READ THIS", f_h2)
        ws.merge_range(r + 2, 0, r + 6, 2,
            "Every prediction is out of sample: the model was trained on earlier years and "
            "tested on later ones, with a gap so no training label resolved inside a test "
            "window. Costs are modeled on both sides of every trade. The statistical test "
            "resamples in blocks, because overlapping 5-day returns are not independent, and "
            "it corrects for the number of strategies tested. Limitations are listed in "
            "docs/LIMITATIONS.md; the universe carries survivorship bias and the data is "
            "daily, so no intraday claim is supported.", f_body)

        # ---------------------------------------------------------- Charts
        ws2 = wb.add_worksheet("Charts")
        xl.sheets["Charts"] = ws2
        ws2.hide_gridlines(2)
        ws2.set_column("A:A", 2)
        row = 1
        for png, note in [
            ("01_equity_vs_benchmark.png", "The strategy underperformed a passive benchmark."),
            ("02_reality_check.png", "The best result sits inside the distribution of chance."),
            ("04_cost_waterfall.png", "Costs consumed most of the gross edge."),
            ("03_per_fold_excess.png", "The edge does not persist across regimes."),
            ("05_feature_importance.png", "The model is timing the market, not picking stocks."),
        ]:
            p = figures / png
            if p.exists():
                ws2.write(row, 1, note, f_h2)
                ws2.insert_image(row + 1, 1, str(p), {"x_scale": 0.62, "y_scale": 0.62})
                row += 26
        if row == 1:
            ws2.write(1, 1, "Run Rscript R/figures.R to generate charts.", f_body)

        # ------------------------------------------------------ data sheets
        def sheet(df: pd.DataFrame, name: str, widths: dict | None = None) -> None:
            if df.empty:
                return
            df.to_excel(xl, sheet_name=name, index=False)
            w = xl.sheets[name]
            for c, col in enumerate(df.columns):
                w.write(0, c, col, f_head)
                w.set_column(c, c, (widths or {}).get(col, max(11, min(26, len(str(col)) + 6))))
            w.freeze_panes(1, 0)
            w.autofilter(0, 0, len(df), len(df.columns) - 1)

        sheet(sens, "Parameter sensitivity")
        sheet(summary, "Model comparison")
        sheet(validation, "Validation")
        sheet(fold_metrics, "Fold metrics")
        sheet(importance, "Feature importance")
        sheet(folds, "Walk-forward folds")
        sheet(trades, "Backtest trades")
        sheet(coverage, "Data coverage")
        sheet(dq, "Data quality", {"detail": 56})

        # Equity sheet with a native Excel chart, so the curve is live in the
        # workbook rather than only a picture.
        eq = equity.copy()
        eq.to_excel(xl, sheet_name="Equity curve", index=False)
        we = xl.sheets["Equity curve"]
        for c, col in enumerate(eq.columns):
            we.write(0, c, col, f_head)
        we.set_column("A:A", 12); we.set_column("B:D", 14)
        chart = wb.add_chart({"type": "line"})
        chart.add_series({
            "name": "Strategy equity",
            "categories": ["Equity curve", 1, 0, len(eq), 0],
            "values": ["Equity curve", 1, 1, len(eq), 1],
            "line": {"color": "#1F3864", "width": 1.5},
        })
        chart.set_title({"name": "Strategy equity, out of sample"})
        chart.set_y_axis({"name": "account equity ($)"})
        chart.set_x_axis({"name": "date", "num_font": {"rotation": -45}})
        chart.set_legend({"position": "bottom"})
        chart.set_size({"width": 900, "height": 420})
        we.insert_chart("F2", chart)

    print(f"wrote {out}")
    print(f"  Findings, Charts, and {len([x for x in [summary, validation, trades] if not x.empty])}+ data sheets")
    print(f"  headline: strategy {strat_total:+.1%} vs SPY {spy_total:+.1%}, "
          f"Reality Check p = {p_rc:.3f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
