# Equity Signal Research Pipeline

An end-to-end pipeline that tests whether a short-horizon equity trading rule has
a real edge, and reports the answer honestly. **Excel → Python → SQL → R.**

The deliverable is not a profitable strategy. The deliverable is a system that can
tell the difference between an edge and a coincidence, and that says so either way.

```
Excel workbook        trade journal and manual market data, typed by a human
      │  openpyxl / xlsxwriter        validated against a data contract
      ▼
SQLite warehouse      200,571 bars · 67 symbols · 3,017 trading days
      │  SQL window functions         41 point-in-time features
      ▼
Python                walk-forward modeling, cost-aware backtest
      │  DBI / RSQLite
      ▼
R                     bootstrap significance, multiple-comparison correction
      │  ggplot2 / writexl
      ▼
Excel dashboard       results written back for a non-technical reader
```

## Current state

| Stage | Status | Artifact |
|---|---|---|
| 1. Excel data contract | **done** | `python/make_workbook.py`, `python/validate_excel.py` |
| 2. Warehouse and ETL | **done** | `sql/01_schema.sql`, `python/ingest_bars.py` |
| 3. SQL feature layer | **done** | `sql/02_features.sql`, `sql/03_materialize.sql`, `sql/04_labels.sql` |
| 4. Modeling and backtest | in progress | `python/model.py`, `python/backtest.py` |
| 5. R validation | in progress | `R/validate.R` |
| 6. Reporting | in progress | `R/figures.R`, `python/export_excel.py` |

Rebuild everything from source:

```bash
python3 build.py --ingest
```

Sixteen seconds without the download. Every number in the project traces back to
that one command.

## What the data looks like

| Measure | Value |
|---|---|
| Daily bars loaded | 200,571 |
| Symbols | 67 (55 stocks, 12 sector ETFs) |
| Trading days | 3,017 (2014-01-02 to 2025-12-30) |
| Point-in-time features | 41 |
| Usable modeling rows | 154,660 |
| Base rate of the target | 53.6% |

That 53.6% is the number any model has to beat. US equities drifted upward over
this sample, so a coin that always says "up" is already right most of the time.
Reporting 55% accuracy without that baseline next to it would be meaningless.

## Three decisions that define the project

### 1. The conclusion is allowed to be negative

Most trading projects present a beautiful equity curve. Interviewers discount
them, correctly, because the curve is usually the product of testing many ideas
and publishing the one that survived. This project treats that selection effect
as the thing to measure rather than the thing to hide: the R layer corrects for
how many variants were tested, and a finding of "no edge after costs" is a valid
and reportable result.

### 2. Lookahead bias is tested, not asserted

Features look backward. Labels look forward. The two live in separate SQL files
so the direction of every column is auditable by reading one file.

The claim is verified by `tests/test_no_lookahead.py`, which rebuilds the entire
feature pipeline from a database truncated at a past date and asserts that every
feature value on that date is bit-identical to the full-history build. A feature
that peeks at the future cannot survive that test. Labels at the cutoff are
asserted to be *absent*, because they legitimately require data that no longer
exists.

```
[1] TRUNCATION TEST — rebuild with no data after 2023-06-30
  removed 42,009 bars after the cutoff, rebuilding pipeline
  [PASS] truncation — 23 features identical across 55 symbols
  [PASS] labels absent without future data
[2] INDEPENDENT RECOMPUTE — pandas vs SQL for AAPL
  [PASS] 6 features match pandas to 1e-6 relative
[3] LABEL DIRECTION — MSFT
  [PASS] fwd_ret_5d equals t+5 return — max diff 0.00e+00
[4] LABEL/FEATURE SEPARATION
  [PASS] no forward-looking columns in feature tables
7/7 checks passed
```

The independent-recompute check earned its place immediately: it caught that
SQLite's `AVG` was skipping the first NULL true-range value and reporting a
13-observation average as a 14-day ATR. One row, invisible by inspection, found
by implementing the feature twice and comparing.

### 3. Costs are in the label, not bolted on afterward

The binary target is defined on forward return **net of a modeled round trip**
(14 basis points: slippage plus half-spread, both parameters in `python/config.py`).
A model trained on gross returns learns that a 3 basis point edge is worth
trading, which is the most common reason a promising backtest loses money in
production.

## Repository layout

```
build.py                 one command, whole pipeline
python/
  config.py              every assumption in one auditable place
  ingest_bars.py         idempotent ETL with quality checks persisted as rows
  db.py                  connection handling, foreign keys on
  make_workbook.py       generates the Excel workbook from the schema
  validate_excel.py      the data contract; nothing loads until it passes
  load_journal.py        Excel to warehouse
sql/
  01_schema.sql          DDL with CHECK constraints and provenance tables
  02_features.sql        backward-looking features (views define the logic)
  03_materialize.sql     views to indexed tables (60s query to 15ms)
  04_labels.sql          forward-looking targets, kept deliberately separate
tests/
  test_no_lookahead.py   the audit the whole project rests on
R/                       statistical validation and figures
journal/                 the paper-trading assistant that seeded this project
docs/                    methodology and limitations
```

## Engineering notes worth asking about

**Idempotent loads.** Re-running an ingest UPSERTs on `(symbol, dt)` and leaves
row counts unchanged. Verified by running the same load twice and asserting 183
rows both times. A pipeline that duplicates on retry cannot be trusted to retry.

**Quality issues are rows, not log lines.** `data_quality_issues` recorded 9
findings on the full load. All 9 were investigated and all 9 were genuine market
events, not data errors: AMD's +52% day in April 2016 was real earnings, NFLX's
-35% in April 2022 was real, and XLRE's zero-volume days are from its launch
month in October 2015. A quality check you cannot query is a check you will
repeat by hand forever.

**Views for logic, tables for queries.** The feature layer is defined as SQL
views, which keeps one authoritative definition per feature. Reading the panel
through a stack of those views made a single `COUNT(*)` take 60 seconds, because
the window chain was re-evaluated per reference. Materializing to an indexed
table dropped it to 15 milliseconds while leaving the views as the source of
truth, so the table can always be rebuilt and never becomes a second definition.

**The calendar is derived, not assumed.** `trading_days` is built from observed
bars and carries a dense ordinal index. Horizons are counted in trading sessions
through that index. Offsetting by calendar days lands on weekends and holidays
and silently changes the holding period.

## Limitations

Stated plainly in [docs/LIMITATIONS.md](docs/LIMITATIONS.md). The short version:
the universe is current large caps, so it carries survivorship bias; the data is
daily, so no intraday claim is supportable; and the cost model is an estimate,
not a fill log.

## The paper-trading assistant

`journal/` holds the checklist-driven paper-trading assistant this project grew
out of. It is the forward-testing arm: trades logged there enter the same
warehouse through the Excel contract, so live decisions and historical research
are measured with identical code.
