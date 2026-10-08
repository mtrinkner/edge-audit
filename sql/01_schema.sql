-- =============================================================================
-- 01_schema.sql — warehouse DDL
--
-- Design notes a reviewer should be able to verify by reading this file:
--   * Raw ingested data is never mutated. Corrections land in adjustments, so
--     the pipeline can be replayed from source and get the same answer.
--   * Every row knows which ingest run produced it, so a bad load is traceable
--     and reversible instead of silently poisoning the sample.
--   * Natural keys (symbol, dt) are the primary keys on fact tables. Re-running
--     a load is an UPSERT, not a duplicate, which is what makes the pipeline
--     idempotent.
--   * Data quality problems are recorded as rows, not printed to a console and
--     lost. A quality issue you cannot query is a quality issue you will repeat.
-- =============================================================================

PRAGMA foreign_keys = ON;

-- ------------------------------------------------------------------ dimensions
CREATE TABLE IF NOT EXISTS symbols (
    symbol            TEXT PRIMARY KEY,
    name              TEXT NOT NULL,
    sector            TEXT,
    kind              TEXT NOT NULL CHECK (kind IN ('stock', 'etf')),
    inclusion_reason  TEXT NOT NULL,
    added_at          TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Trading calendar derived from observed bars rather than assumed. Holidays and
-- half days differ by year and hardcoding them is a reliable source of off-by-one
-- errors in forward-return calculations.
CREATE TABLE IF NOT EXISTS trading_days (
    dt          TEXT PRIMARY KEY,
    day_index   INTEGER NOT NULL UNIQUE,   -- dense 0..N ordinal, the join key for lags
    year        INTEGER NOT NULL,
    month       INTEGER NOT NULL,
    dow         INTEGER NOT NULL
);

-- ------------------------------------------------------------------ provenance
CREATE TABLE IF NOT EXISTS ingest_runs (
    run_id            INTEGER PRIMARY KEY AUTOINCREMENT,
    source            TEXT NOT NULL,
    interval          TEXT NOT NULL,
    requested_start   TEXT NOT NULL,
    requested_end     TEXT NOT NULL,
    symbols_requested INTEGER NOT NULL,
    symbols_loaded    INTEGER,
    rows_loaded       INTEGER,
    status            TEXT NOT NULL CHECK (status IN ('running', 'ok', 'partial', 'failed')),
    started_at        TEXT NOT NULL DEFAULT (datetime('now')),
    finished_at       TEXT,
    notes             TEXT
);

CREATE TABLE IF NOT EXISTS data_quality_issues (
    issue_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id      INTEGER REFERENCES ingest_runs(run_id),
    symbol      TEXT,
    dt          TEXT,
    severity    TEXT NOT NULL CHECK (severity IN ('info', 'warn', 'error')),
    issue       TEXT NOT NULL,
    detail      TEXT,
    detected_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_dq_symbol ON data_quality_issues(symbol, severity);

-- ----------------------------------------------------------------- price facts
CREATE TABLE IF NOT EXISTS bars (
    symbol      TEXT NOT NULL REFERENCES symbols(symbol),
    dt          TEXT NOT NULL,
    open        REAL NOT NULL CHECK (open  > 0),
    high        REAL NOT NULL CHECK (high  > 0),
    low         REAL NOT NULL CHECK (low   > 0),
    close       REAL NOT NULL CHECK (close > 0),
    adj_close   REAL NOT NULL CHECK (adj_close > 0),
    volume      INTEGER NOT NULL CHECK (volume >= 0),
    run_id      INTEGER NOT NULL REFERENCES ingest_runs(run_id),
    PRIMARY KEY (symbol, dt),
    -- A bar that violates OHLC ordering is corrupt. Reject at write time rather
    -- than discovering it as a nonsensical backtest result six steps later.
    CHECK (high >= low),
    CHECK (high >= open AND high >= close),
    CHECK (low  <= open AND low  <= close)
);

CREATE INDEX IF NOT EXISTS idx_bars_dt ON bars(dt);

-- ------------------------------------------------- manual entries from Excel
-- The Excel workbook is the human interface. These tables are its landing zone,
-- loaded only after the validator passes. source_row is kept so an error can be
-- traced back to the exact spreadsheet row that caused it.
CREATE TABLE IF NOT EXISTS journal_trades (
    trade_id        INTEGER PRIMARY KEY,
    symbol          TEXT NOT NULL,
    direction       TEXT NOT NULL CHECK (direction IN ('long', 'short')),
    setup           TEXT,
    opened_at       TEXT NOT NULL,
    entry           REAL NOT NULL CHECK (entry > 0),
    stop            REAL NOT NULL CHECK (stop  > 0),
    target          REAL CHECK (target > 0),
    shares          INTEGER NOT NULL CHECK (shares > 0),
    closed_at       TEXT,
    exit            REAL CHECK (exit > 0),
    signal_score    INTEGER CHECK (signal_score BETWEEN 1 AND 10),
    followed_plan   INTEGER CHECK (followed_plan IN (0, 1)),
    notes           TEXT,
    source_row      INTEGER NOT NULL,
    loaded_at       TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS journal_observations (
    obs_id      INTEGER PRIMARY KEY,
    symbol      TEXT NOT NULL,
    dt          TEXT NOT NULL,
    field       TEXT NOT NULL,
    value       REAL,
    text_value  TEXT,
    source      TEXT NOT NULL,
    source_row  INTEGER NOT NULL,
    UNIQUE (symbol, dt, field)
);

-- ------------------------------------------------------------ model artifacts
CREATE TABLE IF NOT EXISTS wf_folds (
    fold_id      INTEGER PRIMARY KEY,
    train_start  TEXT NOT NULL,
    train_end    TEXT NOT NULL,
    test_start   TEXT NOT NULL,
    test_end     TEXT NOT NULL,
    purge_days   INTEGER NOT NULL,
    embargo_days INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS predictions (
    model_name  TEXT NOT NULL,
    fold_id     INTEGER NOT NULL REFERENCES wf_folds(fold_id),
    symbol      TEXT NOT NULL,
    dt          TEXT NOT NULL,
    y_true      REAL,
    y_prob      REAL,
    y_pred      INTEGER,
    PRIMARY KEY (model_name, fold_id, symbol, dt)
);

CREATE TABLE IF NOT EXISTS backtest_runs (
    bt_id         INTEGER PRIMARY KEY AUTOINCREMENT,
    strategy      TEXT NOT NULL,
    params_json   TEXT NOT NULL,
    cost_json     TEXT NOT NULL,
    start_dt      TEXT NOT NULL,
    end_dt        TEXT NOT NULL,
    is_oos        INTEGER NOT NULL CHECK (is_oos IN (0, 1)),
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS backtest_trades (
    bt_id        INTEGER NOT NULL REFERENCES backtest_runs(bt_id),
    trade_seq    INTEGER NOT NULL,
    symbol       TEXT NOT NULL,
    direction    TEXT NOT NULL CHECK (direction IN ('long', 'short')),
    entry_dt     TEXT NOT NULL,
    entry_px     REAL NOT NULL,
    exit_dt      TEXT,
    exit_px      REAL,
    shares       INTEGER NOT NULL,
    gross_pnl    REAL,
    costs        REAL,
    net_pnl      REAL,
    r_multiple   REAL,
    exit_reason  TEXT,
    PRIMARY KEY (bt_id, trade_seq)
);

CREATE TABLE IF NOT EXISTS backtest_equity (
    bt_id    INTEGER NOT NULL REFERENCES backtest_runs(bt_id),
    dt       TEXT NOT NULL,
    equity   REAL NOT NULL,
    cash     REAL NOT NULL,
    exposure REAL NOT NULL,
    PRIMARY KEY (bt_id, dt)
);

-- ---------------------------------------------------- earnings events
-- A second, genuinely independent data source. Price-derived signals are all
-- transforms of one series, which is why 13 of them collapsed to 2.9 effective
-- independent strategies. Earnings surprise is measured by a different process
-- entirely (analyst forecasts versus reported results), so it has a real chance
-- of being uncorrelated with momentum.
--
-- POINT-IN-TIME DISCIPLINE. announced_at is a timestamp, not a date, because
-- whether a release landed before or after the close decides the first session
-- it could have been traded on. tradeable_from stores that resolved date, so no
-- downstream query has to re-derive it and get it wrong.
CREATE TABLE IF NOT EXISTS earnings (
    symbol          TEXT NOT NULL REFERENCES symbols(symbol),
    announced_at    TEXT NOT NULL,          -- full timestamp with offset
    period_end      TEXT,
    eps_estimate    REAL,
    eps_actual      REAL,
    surprise_pct    REAL,
    tradeable_from  TEXT NOT NULL,          -- first session this could be acted on
    run_id          INTEGER REFERENCES ingest_runs(run_id),
    PRIMARY KEY (symbol, announced_at)
);

CREATE INDEX IF NOT EXISTS idx_earnings_tradeable ON earnings(tradeable_from);
CREATE INDEX IF NOT EXISTS idx_earnings_symbol ON earnings(symbol, tradeable_from);
