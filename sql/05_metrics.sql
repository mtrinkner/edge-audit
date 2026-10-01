-- =============================================================================
-- 05_metrics.sql — the analysis surface the R layer reads
--
-- Everything here collapses predictions into ONE RETURN PER DATE per model. That
-- is the unit of analysis, not one return per row, because the model's selections
-- cluster: its strongest features are market-level, so high-confidence rows
-- arrive in bursts and dozens of simultaneous positions represent a single bet.
-- Treating each row as an independent trade inflated the Sharpe ratio of the
-- logistic model from 0.36 to 1.59.
--
-- The decile cut is computed per date with PERCENT_RANK, so "top decile" means
-- the best 10% of that day's opportunities, which is what a trader choosing among
-- today's candidates would actually face. Ranking across the whole pooled sample
-- instead would let a quiet day's best idea compete against a crisis day's.
-- =============================================================================

DROP VIEW IF EXISTS v_pred_ranked;
CREATE VIEW v_pred_ranked AS
SELECT p.model_name,
       p.fold_id,
       p.symbol,
       p.dt,
       p.y_prob,
       p.y_true,
       l.fwd_ret_5d_net,
       l.fwd_ret_5d,
       PERCENT_RANK() OVER (PARTITION BY p.model_name, p.dt ORDER BY p.y_prob)
           AS prob_rank_in_date,
       COUNT(*) OVER (PARTITION BY p.model_name, p.dt) AS candidates_that_date
FROM predictions p
JOIN labels l ON l.symbol = p.symbol AND l.dt = p.dt;

-- One row per model per date: the equal-weight return of that date's selections.
-- This is the series every statistical test in R/validate.R operates on.
DROP VIEW IF EXISTS v_strategy_daily;
CREATE VIEW v_strategy_daily AS
SELECT model_name,
       dt,
       COUNT(*)                   AS n_selected,
       AVG(fwd_ret_5d_net)        AS ret_net,
       AVG(fwd_ret_5d)            AS ret_gross,
       AVG(y_true)                AS hit_rate,
       AVG(y_prob)                AS mean_prob
FROM v_pred_ranked
WHERE prob_rank_in_date >= 0.90      -- top decile of that date's candidates
GROUP BY model_name, dt;

-- The passive benchmark on the same dates and the same holding horizon, so the
-- comparison is like for like. A strategy that cannot beat this has not earned
-- the transaction costs it pays.
DROP VIEW IF EXISTS v_benchmark_daily;
CREATE VIEW v_benchmark_daily AS
SELECT l.dt,
       l.fwd_ret_5d_net AS ret_net,
       l.fwd_ret_5d     AS ret_gross
FROM labels l
WHERE l.symbol = 'SPY';

-- Per-fold summary, so a result that works in some regimes and fails in others
-- is visible rather than averaged away.
DROP VIEW IF EXISTS v_fold_summary;
CREATE VIEW v_fold_summary AS
SELECT r.model_name,
       r.fold_id,
       f.test_start,
       f.test_end,
       COUNT(DISTINCT r.dt)      AS trading_dates,
       COUNT(*)                  AS selections,
       AVG(r.fwd_ret_5d_net)     AS mean_ret_net,
       AVG(r.y_true)             AS hit_rate
FROM v_pred_ranked r
JOIN wf_folds f ON f.fold_id = r.fold_id
WHERE r.prob_rank_in_date >= 0.90
GROUP BY r.model_name, r.fold_id;

-- Equity curves from the backtest, with drawdown computed in SQL so the R layer
-- and any dashboard read the same definition.
DROP VIEW IF EXISTS v_equity_drawdown;
CREATE VIEW v_equity_drawdown AS
SELECT e.bt_id,
       e.dt,
       e.equity,
       e.exposure,
       MAX(e.equity) OVER (PARTITION BY e.bt_id ORDER BY e.dt
                           ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS peak,
       (MAX(e.equity) OVER (PARTITION BY e.bt_id ORDER BY e.dt
                            ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
        - e.equity)
       / MAX(e.equity) OVER (PARTITION BY e.bt_id ORDER BY e.dt
                             ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
           AS drawdown
FROM backtest_equity e;

-- A decile cut is meaningless for a model whose score takes only one or two
-- values: PERCENT_RANK assigns every tied row the same rank, so always_long
-- (constant 0.51) selects nothing and momentum_rule (0.40/0.60) qualifies on only
-- 65 of 1,754 dates. The baselines are RULES, not rankers, so they are evaluated
-- on their own binary signal instead. Leaving them in the decile view would have
-- quietly compared a 1,754-date series against a 65-date one.
DROP VIEW IF EXISTS v_baseline_daily;
CREATE VIEW v_baseline_daily AS
SELECT p.model_name,
       p.dt,
       COUNT(*)              AS n_selected,
       AVG(l.fwd_ret_5d_net) AS ret_net,
       AVG(l.fwd_ret_5d)     AS ret_gross,
       AVG(p.y_true)         AS hit_rate
FROM predictions p
JOIN labels l ON l.symbol = p.symbol AND l.dt = p.dt
WHERE p.model_name IN ('always_long', 'momentum_rule')
  AND p.y_pred = 1
GROUP BY p.model_name, p.dt;

-- One series per strategy, baselines and rankers together, for the R layer.
DROP VIEW IF EXISTS v_all_strategies_daily;
CREATE VIEW v_all_strategies_daily AS
SELECT model_name, dt, n_selected, ret_net, ret_gross, 'top_decile' AS selection
FROM v_strategy_daily
WHERE model_name IN ('logistic', 'gbm')
UNION ALL
SELECT model_name, dt, n_selected, ret_net, ret_gross, 'rule_signal' AS selection
FROM v_baseline_daily;
