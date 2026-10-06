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
| 4. Modeling and backtest | **done** | `python/model.py`, `python/folds.py`, `python/backtest.py` |
| 5. R validation | **done** | `R/validate.R`, `python/sensitivity.py` |
| 6. Reporting | **done** | `R/figures.R`, `python/export_excel.py` |

Rebuild everything from source:

```bash
python3 build.py --from ingest     # uses the pinned data snapshot, no network
```

Every number in this README comes from that command. The bars behind the
published results are committed in `data/raw/bars_snapshot.csv.gz`, because the
data vendor silently revises adjusted closes and a fresh download does not
reproduce them. `python3 build.py --ingest` re-downloads instead, which is how
you check whether the finding survives new data rather than just re-reading it.

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


## The finding

**No strategy beat a passive benchmark, and the apparent edge does not survive
statistical scrutiny.** Stated up front because that is the result, not a
disappointment to bury below a chart.

| | Strategy (GBM) | SPY buy and hold |
|---|---|---|
| Total return, 7 years out of sample | **+15.3%** | **+204.4%** |
| Annualized | 2.1% | 17.3% |
| Sharpe | 0.23 | 0.91 |
| Max drawdown | 17.1% | 33.7% |

Four things had to be true at once for this to be a real edge. None of them were.

**1. Costs ate most of the gross edge.** The model found something: $6,078 of
gross P/L on a $10,000 account over seven years. Slippage, spread and commission
took $4,482 of it, **73.7%**, leaving $1,596. Across all 45 parameter variants the
median was **69%** of gross lost to costs. An edge that exists only before
execution is not an edge.

**2. The sample is far smaller than the row count suggests.** The model's
strongest features are market-wide, so its high-confidence picks arrive in bursts:
in the worst fold, 1,342 selected rows fell on 52 distinct dates, 41% of them on
just ten. With 5-day overlapping labels, 1,740 trading dates are roughly **348
independent observations**. Scoring each row as an independent trade inflated the
logistic model's Sharpe from **0.36 to 1.59**. The honest number is the first one.

**3. It fails the data-snooping test.** Against a zero null, the GBM's mean return
is significant at p = 0.009. But equities drifted upward over the sample, so zero
is the wrong null. Against an always-long baseline the excess is +0.112% per
5-day period at p = 0.128, and White's Reality Check across all strategies tested
gives **p = 0.149**. Nothing survives Benjamini-Hochberg.

**4. The result is chaotically sensitive to its inputs.** Rebuilding after
re-downloading the same data changed net P/L from $1,596 to $3,191. The inputs had
moved by at most **1.06e-6** in relative terms (vendor rounding on adjusted
closes; raw prices and volumes were bit-identical), and both the model and the
backtest were verified deterministic. With at most three concurrent positions
chosen by a probability cutoff, a microscopic shift reorders which names clear the
threshold and the paths diverge. Across 45 parameter variants, returns ranged from
**-5.2% to 56.6%**, and **0 of 45 beat SPY**.

The best variant returned 56.6%. That number is the maximum of 45 tries and is
what a less careful write-up would report as "the result".

![Reality check](reports/figures/02_reality_check.png)

## Three decisions that define the project

### 1. The conclusion is allowed to be negative

Most trading projects present a beautiful equity curve. Interviewers discount
them, correctly, because the curve is usually the product of testing many ideas
and publishing the one that survived. This project treats that selection effect
as the thing to measure rather than the thing to hide: the R layer corrects for
how many variants were tested, and a finding of "no edge after costs" is a valid
and reportable result.

It would have been easy to report the 56.6% variant and stop. Every mechanism in
this repository exists to prevent that.

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
  folds.py               walk-forward splits with purge and embargo
  model.py               baselines and models, clustering-aware evaluation
  backtest.py            cost-aware portfolio backtest, next-open fills
  sensitivity.py         45-variant robustness sweep
  export_excel.py        results back into Excel
sql/
  01_schema.sql          DDL with CHECK constraints and provenance tables
  02_features.sql        backward-looking features (views define the logic)
  03_materialize.sql     views to indexed tables (60s query to 15ms)
  04_labels.sql          forward-looking targets, kept deliberately separate
  05_metrics.sql         one return per date per strategy, the analysis surface
R/
  validate.R             stationary bootstrap, BH, White's Reality Check
  figures.R              the five charts that carry the argument
tests/
  test_no_lookahead.py   the audit the whole project rests on
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

## Forward test

The backtest is finished and its answer is negative. Re-running it monthly would
be the same question asked twelve times a year against the same history, which is
how false findings get published. So the live arm is a **pre-registered forward
test** instead:

```bash
python3 python/freeze_model.py      # once: train, serialize, register a SHA-256
python3 python/forward_run.py       # monthly: score data the model has not seen
```

The model and every decision rule are frozen in `models/gbm_v1.json`. Each run
writes predictions to an append-only, hash-chained ledger **before** outcomes
exist, then scores whatever has matured. `tests/test_ledger_integrity.py` proves
that edits, deletions and reorderings are detectable, because an append-only
claim nobody tested is just a claim.

Evidence is reported in two buckets that never merge. Dates between the training
cutoff and the first run are `holdout`: out-of-sample, but the data already
existed, so not pre-registered. Everything after is `live`.

| Stage | Trades | Dates | Effective obs | Excess vs SPY | p |
|---|---|---|---|---|---|
| holdout (2026 YTD) | 496 | 169 | 33.8 | +0.329% per 5 days | **0.452** |
| live | 0 | — | — | — | — |

The holdout mean looks healthy and means nothing: its 95% interval runs from
-0.53% to +1.18%. At roughly 4 independent observations a month, the live arm
needs about 8 months to reach even 30. The report prints the p-value next to the
mean every time, so the number is never read alone.

## Résumé summary

> **Equity Signal Research Pipeline** — Excel, Python, SQL, R
> Built an end-to-end research pipeline testing whether a short-horizon equity
> signal has a tradeable edge. Ingested and validated 200K daily bars across 67
> symbols into a SQLite warehouse with provenance tracking and queryable data
> quality checks; engineered 41 point-in-time features using SQL window functions,
> verified leak-free by a truncation-rebuild audit; trained walk-forward models
> with purge and embargo; and validated results in R using a stationary bootstrap
> and White's Reality Check. **Found the apparent 1.59 Sharpe fell to 0.36 once
> clustered, overlapping trades were counted correctly, that transaction costs
> consumed 74% of the gross edge, and that 0 of 45 parameter variants beat a
> passive benchmark. Reported the negative result.**

## Reproducing

```bash
python3 build.py --ingest     # full rebuild including the data download
Rscript R/validate.R          # just the statistical tests
open reports/dashboard.xlsx   # the results, for a non-technical reader
```

Requires Python 3 with pandas, scikit-learn, xlsxwriter and yfinance, plus R with
tidyverse, DBI and RSQLite. On macOS, install the R packages with Anaconda off the
PATH or Anaconda's `libcurl` breaks the RSQLite build:

```bash
env PATH="/opt/homebrew/bin:/usr/bin:/bin" Rscript -e 'install.packages(c("RSQLite","writexl"))'
```
