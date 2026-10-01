-- =============================================================================
-- 02_features.sql — point-in-time feature layer
--
-- THE CENTRAL RULE OF THIS FILE: a feature on date t may read only data from
-- dates <= t. A label may read only data from dates > t. Any view that mixes
-- those two directions leaks the future into the past and makes every downstream
-- metric meaningless.
--
-- Every window below is therefore written with an explicit frame
-- (ROWS BETWEEN n PRECEDING AND CURRENT ROW) rather than relying on the default
-- frame, because SQL's default RANGE frame includes peer rows and is not what
-- anyone means by "the last 20 days".
--
-- Lags are taken over each symbol's own ordered history. The trading_days table
-- supplies a dense day_index so the horizon arithmetic in 03_labels.sql counts
-- trading days rather than calendar days.
-- =============================================================================

DROP VIEW IF EXISTS v_bars_indexed;
CREATE VIEW v_bars_indexed AS
SELECT b.symbol,
       b.dt,
       d.day_index,
       s.sector,
       s.kind,
       b.open, b.high, b.low, b.close, b.adj_close, b.volume
FROM bars b
JOIN trading_days d ON d.dt = b.dt
JOIN symbols s      ON s.symbol = b.symbol;

-- ----------------------------------------------------------------- returns
-- Returns use adj_close. Using raw close treats every dividend as a loss.
DROP VIEW IF EXISTS v_returns;
CREATE VIEW v_returns AS
SELECT symbol,
       dt,
       day_index,
       sector,
       kind,
       open, high, low, close, adj_close, volume,
       LAG(adj_close) OVER w AS prev_adj_close,
       CASE WHEN LAG(adj_close) OVER w > 0
            THEN adj_close / LAG(adj_close) OVER w - 1.0
       END AS ret_1d,
       CASE WHEN LAG(close) OVER w > 0
            THEN open / LAG(close) OVER w - 1.0
       END AS gap_pct,
       CASE WHEN low > 0 THEN (high - low) / low END AS range_pct
FROM v_bars_indexed
WINDOW w AS (PARTITION BY symbol ORDER BY day_index);

-- --------------------------------------------------------------- features
-- Each column answers "what could I have known at the close of day t".
DROP VIEW IF EXISTS v_features;
CREATE VIEW v_features AS
WITH base AS (
    SELECT symbol, dt, day_index, sector, kind,
           open, high, low, close, adj_close, volume,
           ret_1d, gap_pct, range_pct,
           AVG(adj_close) OVER (PARTITION BY symbol ORDER BY day_index
                                ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) AS sma_20,
           AVG(adj_close) OVER (PARTITION BY symbol ORDER BY day_index
                                ROWS BETWEEN 49 PRECEDING AND CURRENT ROW) AS sma_50,
           AVG(adj_close) OVER (PARTITION BY symbol ORDER BY day_index
                                ROWS BETWEEN 199 PRECEDING AND CURRENT ROW) AS sma_200,
           AVG(volume)    OVER (PARTITION BY symbol ORDER BY day_index
                                ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) AS vol_avg_20,
           MAX(high)      OVER (PARTITION BY symbol ORDER BY day_index
                                ROWS BETWEEN 251 PRECEDING AND CURRENT ROW) AS high_252,
           MIN(low)       OVER (PARTITION BY symbol ORDER BY day_index
                                ROWS BETWEEN 251 PRECEDING AND CURRENT ROW) AS low_252,
           MAX(high)      OVER (PARTITION BY symbol ORDER BY day_index
                                ROWS BETWEEN 20 PRECEDING AND 1 PRECEDING) AS prior_high_20,
           MIN(low)       OVER (PARTITION BY symbol ORDER BY day_index
                                ROWS BETWEEN 20 PRECEDING AND 1 PRECEDING) AS prior_low_20,
           -- Realized volatility as the standard deviation of daily returns.
           -- SQLite has no STDDEV, so it is built from the identity
           -- var = E[x^2] - E[x]^2 over the same explicit frame.
           AVG(ret_1d * ret_1d) OVER (PARTITION BY symbol ORDER BY day_index
                                ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) AS e_r2_20,
           AVG(ret_1d)          OVER (PARTITION BY symbol ORDER BY day_index
                                ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) AS e_r_20,
           COUNT(ret_1d)        OVER (PARTITION BY symbol ORDER BY day_index
                                ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) AS n_r_20,
           -- True range needs the prior close, so it is lagged explicitly.
           MAX(high - low,
               ABS(high - LAG(close) OVER (PARTITION BY symbol ORDER BY day_index)),
               ABS(low  - LAG(close) OVER (PARTITION BY symbol ORDER BY day_index))
           ) AS true_range,
           ROW_NUMBER() OVER (PARTITION BY symbol ORDER BY day_index) AS bar_num
    FROM v_returns
),
withatr AS (
    SELECT *,
           AVG(true_range) OVER atr_w   AS atr_14_raw,
           -- SQLite's AVG silently skips NULLs, so without this count the first
           -- true_range (NULL, because it needs a prior close) would be dropped
           -- and bar 14 would report a 13-observation average as a 14-day ATR.
           -- The independent-recompute test in tests/test_no_lookahead.py caught
           -- exactly this. A partially formed window is a different statistic.
           COUNT(true_range) OVER atr_w AS atr_n
    FROM base
    WINDOW atr_w AS (PARTITION BY symbol ORDER BY day_index
                     ROWS BETWEEN 13 PRECEDING AND CURRENT ROW)
)
SELECT symbol,
       dt,
       day_index,
       sector,
       kind,
       bar_num,
       open, high, low, close, adj_close, volume,
       ret_1d,
       gap_pct,
       range_pct,
       sma_20, sma_50, sma_200,
       CASE WHEN atr_n >= 14 THEN atr_14_raw END AS atr_14,
       -- Volatility annualized with 252 trading days, guarded against the
       -- tiny negative variance that floating point can produce.
       CASE WHEN n_r_20 >= 20 AND (e_r2_20 - e_r_20 * e_r_20) > 0
            THEN SQRT(e_r2_20 - e_r_20 * e_r_20) * SQRT(252.0)
       END AS vol_20_annual,
       CASE WHEN sma_20  > 0 THEN adj_close / sma_20  - 1.0 END AS dist_sma_20,
       CASE WHEN sma_50  > 0 THEN adj_close / sma_50  - 1.0 END AS dist_sma_50,
       CASE WHEN sma_200 > 0 THEN adj_close / sma_200 - 1.0 END AS dist_sma_200,
       CASE WHEN vol_avg_20 > 0 THEN volume / vol_avg_20 END AS rel_volume,
       CASE WHEN high_252 > 0 THEN adj_close / high_252 - 1.0 END AS dist_52w_high,
       CASE WHEN low_252  > 0 THEN adj_close / low_252  - 1.0 END AS dist_52w_low,
       CASE WHEN atr_n >= 14 AND atr_14_raw > 0 AND close > 0
            THEN atr_14_raw / close END AS atr_pct,
       prior_high_20,
       prior_low_20,
       -- Momentum over multiple horizons, each a pure backward look.
       CASE WHEN LAG(adj_close,  5) OVER w > 0
            THEN adj_close / LAG(adj_close,  5) OVER w - 1.0 END AS mom_5d,
       CASE WHEN LAG(adj_close, 21) OVER w > 0
            THEN adj_close / LAG(adj_close, 21) OVER w - 1.0 END AS mom_21d,
       CASE WHEN LAG(adj_close, 63) OVER w > 0
            THEN adj_close / LAG(adj_close, 63) OVER w - 1.0 END AS mom_63d,
       CASE WHEN LAG(adj_close,126) OVER w > 0
            THEN adj_close / LAG(adj_close,126) OVER w - 1.0 END AS mom_126d,
       -- Trend regime flags, computed from the same point-in-time averages.
       CASE WHEN sma_50 > sma_200 THEN 1 ELSE 0 END AS regime_bull,
       CASE WHEN adj_close > sma_200 THEN 1 ELSE 0 END AS above_200
FROM withatr
WINDOW w AS (PARTITION BY symbol ORDER BY day_index);
