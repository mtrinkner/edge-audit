-- =============================================================================
-- 04_labels.sql — supervised targets
--
-- Labels look FORWARD. Features look BACKWARD. Keeping them in separate files
-- is deliberate: it makes the direction of every column obvious at a glance,
-- and it means a reviewer can audit the leakage question by reading one file.
--
-- The horizon is counted in each symbol's own trading sessions via LEAD over its
-- ordered history, not in calendar days. A 5 calendar day offset lands on a
-- weekend or a holiday and silently shortens or lengthens the holding period.
--
-- fwd_ret_5d_net subtracts the modeled round-trip cost. A label defined on gross
-- returns teaches a model that a 3 basis point edge is worth trading, which is
-- the single most common way a research project produces a strategy that loses
-- money in production.
-- =============================================================================

DROP TABLE IF EXISTS labels;
CREATE TABLE labels AS
WITH fwd AS (
    SELECT symbol,
           dt,
           day_index,
           adj_close,
           LEAD(adj_close, 1)  OVER w AS fwd_close_1d,
           LEAD(adj_close, 5)  OVER w AS fwd_close_5d,
           LEAD(adj_close, 21) OVER w AS fwd_close_21d,
           LEAD(open, 1)       OVER w AS next_open,
           -- The worst drawdown experienced while holding, used to test whether
           -- a stop would have been hit before the target. MIN over the FOLLOWING
           -- rows, which is forward-looking and therefore belongs only in a label.
           MIN(low)  OVER (PARTITION BY symbol ORDER BY day_index
                           ROWS BETWEEN 1 FOLLOWING AND 5 FOLLOWING) AS fwd_min_low_5d,
           MAX(high) OVER (PARTITION BY symbol ORDER BY day_index
                           ROWS BETWEEN 1 FOLLOWING AND 5 FOLLOWING) AS fwd_max_high_5d
    FROM features
    WINDOW w AS (PARTITION BY symbol ORDER BY day_index)
)
SELECT symbol,
       dt,
       day_index,
       CASE WHEN adj_close > 0 AND fwd_close_1d  IS NOT NULL
            THEN fwd_close_1d  / adj_close - 1.0 END AS fwd_ret_1d,
       CASE WHEN adj_close > 0 AND fwd_close_5d  IS NOT NULL
            THEN fwd_close_5d  / adj_close - 1.0 END AS fwd_ret_5d,
       CASE WHEN adj_close > 0 AND fwd_close_21d IS NOT NULL
            THEN fwd_close_21d / adj_close - 1.0 END AS fwd_ret_21d,
       -- Net of a round trip at the modeled cost. 14 bps total is
       -- 2 * (5 bps slippage + 2 bps half-spread); keep in sync with config.CostModel.
       CASE WHEN adj_close > 0 AND fwd_close_5d IS NOT NULL
            THEN fwd_close_5d / adj_close - 1.0 - 0.0014 END AS fwd_ret_5d_net,
       -- The binary target. Defined on the NET return, deliberately.
       CASE WHEN adj_close > 0 AND fwd_close_5d IS NOT NULL
            THEN CASE WHEN fwd_close_5d / adj_close - 1.0 - 0.0014 > 0 THEN 1 ELSE 0 END
       END AS label_win_5d,
       fwd_min_low_5d,
       fwd_max_high_5d,
       CASE WHEN adj_close > 0 AND fwd_min_low_5d IS NOT NULL
            THEN fwd_min_low_5d / adj_close - 1.0 END AS fwd_mae_5d,
       CASE WHEN adj_close > 0 AND fwd_max_high_5d IS NOT NULL
            THEN fwd_max_high_5d / adj_close - 1.0 END AS fwd_mfe_5d,
       next_open
FROM fwd;

CREATE UNIQUE INDEX idx_labels_pk ON labels(symbol, dt);
CREATE INDEX        idx_labels_dt ON labels(dt);

-- The modeling table: features joined to labels, with the rows that cannot be
-- used removed. Dropping incomplete rows here rather than in Python keeps the
-- definition of "a usable training row" in one auditable place.
DROP VIEW IF EXISTS v_model_rows;
CREATE VIEW v_model_rows AS
SELECT p.*,
       l.fwd_ret_1d,
       l.fwd_ret_5d,
       l.fwd_ret_5d_net,
       l.fwd_ret_21d,
       l.label_win_5d,
       l.fwd_mae_5d,
       l.fwd_mfe_5d,
       l.next_open
FROM feature_panel p
JOIN labels l ON l.symbol = p.symbol AND l.dt = p.dt
WHERE p.bar_num > 200          -- needs a full 200-day average to exist
  AND p.vol_20_annual IS NOT NULL
  AND p.mom_126d      IS NOT NULL
  AND p.rel_volume    IS NOT NULL
  AND p.mkt_ret_1d    IS NOT NULL
  AND l.label_win_5d  IS NOT NULL;
