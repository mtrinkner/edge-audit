-- =============================================================================
-- 03_materialize.sql — turn the feature views into indexed tables
--
-- Why this file exists: v_features is a stack of window functions over 200k
-- rows. Reading it through two more views made a single COUNT(*) take 60
-- seconds, because SQLite re-evaluated the whole window chain for every
-- reference. Views are the right place for the LOGIC (they document intent and
-- cannot drift from it); tables are the right place for the QUERY SURFACE.
--
-- So the panel is computed exactly once here, written to a real table, and
-- indexed. The views remain the single definition of each feature, which means
-- the materialized table can always be rebuilt and never becomes a separate
-- source of truth.
-- =============================================================================

DROP TABLE IF EXISTS features;
CREATE TABLE features AS SELECT * FROM v_features;

CREATE UNIQUE INDEX idx_features_pk      ON features(symbol, dt);
CREATE INDEX        idx_features_dt      ON features(dt);
CREATE INDEX        idx_features_dayidx  ON features(symbol, day_index);

-- Benchmark state per date, now a cheap read off the indexed table.
DROP VIEW IF EXISTS v_market;
CREATE VIEW v_market AS
SELECT dt,
       day_index,
       ret_1d        AS mkt_ret_1d,
       vol_20_annual AS mkt_vol_20,
       dist_sma_200  AS mkt_dist_sma_200,
       regime_bull   AS mkt_regime_bull
FROM features
WHERE symbol = 'SPY';

-- The modeling panel: stocks only, with market context attached and
-- cross-sectional ranks computed per date. Ranks compare symbols to each other
-- on the SAME day, so every input is known at the close of t. That is a legal
-- same-time comparison, not a peek forward.
DROP TABLE IF EXISTS feature_panel;
CREATE TABLE feature_panel AS
SELECT f.*,
       m.mkt_ret_1d,
       m.mkt_vol_20,
       m.mkt_dist_sma_200,
       m.mkt_regime_bull,
       PERCENT_RANK() OVER (PARTITION BY f.dt ORDER BY f.mom_21d)    AS mom_21d_xs_rank,
       PERCENT_RANK() OVER (PARTITION BY f.dt ORDER BY f.rel_volume) AS rel_volume_xs_rank,
       PERCENT_RANK() OVER (PARTITION BY f.dt ORDER BY f.vol_20_annual) AS vol_20_xs_rank
FROM features f
LEFT JOIN v_market m ON m.dt = f.dt
WHERE f.kind = 'stock';

CREATE UNIQUE INDEX idx_panel_pk     ON feature_panel(symbol, dt);
CREATE INDEX        idx_panel_dt     ON feature_panel(dt);
CREATE INDEX        idx_panel_dayidx ON feature_panel(symbol, day_index);
