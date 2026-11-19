# Shared infrastructure: pit.py, sim.py, report.py, harness.py

From step 4 on, every new study uses these modules and passes the harness. All
13 defects in the bug ledger came from study-specific code: a known_at
computed by hand, a quote rule copied with a change, a T-bill path read from
the future. One layer, tested once, removes that class of error.

## The point-in-time layer (`python/pit.py`)

Every row of every dataset carries `known_at`, the first moment a trader could
use it. The only way to read data is

```python
pit.asof(name, t, latest=False, **filters)   # rows with known_at <= t
view = pit.view(t); view.asof(name, ...)    # the same, with t fixed
```

A `Dataset` is a frame plus:
- `keys`: the columns `asof` filters on quickly;
- `event_time`: when the row's event happened (a price's date, a filing's
  acceptance), used by the scramble check;
- `value_cols`: what the scramble permutes;
- `version_key`: set it and revisions are kept, each with its own known_at.
  `asof` returns, per key, the latest version known at t.
- `lookahead`: a reason, for a study's declared conditioning set that is
  knowingly not point-in-time. The harness reports it and skips it.

### known_at rules

Times are exchange-local. OPEN is 09:30 and CLOSE is 16:00. Past the
calendar's last day, lookups continue on weekdays, so a later date maps
forward rather than back to the last known day.

| Dataset | known_at | Function |
|---|---|---|
| CRSP daily | the close of its date | `known_at_close` |
| EDGAR filings | the open of the trading day after the acceptance date; a filing accepted on a non-trading day is usable from the next trading day's open (reverse_split.timeline's "usable") | `known_at_next_open` |
| XBRL facts | the open of the trading day after the filing date | `known_at_next_open` |
| IBES announcements | before 16:00 on a trading day: at that time. At or after 16:00, no time (00:00), or a non-trading day: the next trading day's open | `known_at_ibes` |
| Fama-French factors | the library publishes the daily file once a month, after the month ends; conservatively, the close of the last trading day of the following month | `known_at_ff` |
| T-bills (FF rf) | the one-month bill's return for month m is set by its yield when bought at the start of m: the open of the month's first trading day | `known_at_tbill` |

Factor regressions in `report.py` are evaluated after the fact, so the FF
publication lag doesn't constrain them. It constrains a *decision* that uses
factors.

## The simulator (`python/sim.py`)

A study is an object with `decide(view, book) -> list[Order]`:
- `view` is the data known at the close of the day;
- `book` holds its open lots (`book.lots`), closed lots and pending orders.

`decide` runs sealed: a file read inside it raises `PermissionError`.

Each trading day D:
1. **Open.**
   - Corporate actions with ex date D are applied: shares divide by the
     factor, and the fractional policy (`roundup`, `cash`, `down`) decides a
     fraction.
   - A lot whose settlement is on or after the ex date is not of record, and
     keeps its fraction.
   - Orders flagged `cancel_on_action` for that security are cancelled.
   - `open_mhs` orders are tried.
2. **Close.** Pending close orders are tried. Then `decide` runs, and its
   `fill="close"` orders are tried at D's close.
3. **End of day.** Expired orders are recorded as unfilled with a reason, and
   NAV is marked.

The fill rules:
- **Prices:** buys at the ask, sells at the bid, at the close, or `open_mhs`:
  the open minus half the closing spread.
- **Stub rule:** a quote is valid only if bid > 0, ask ≥ bid and
  bid ≥ 0.9 × ask. With no valid quote an order waits until `good_until`.
- **Settlement:** T+3 before 2017-09-05, T+2 until 2024-05-28, T+1 after.
- **Fees** by broker schedule (`BROKERS`).
- **Caps** on position count and position dollars.
- **Cash** earns the T-bill rate known at t (dataset `tbill`).
- **NAV** is marked at the bid, or the last valid bid.

`Sim(pit, prices=..., actions=..., broker=..., fractional=..., capital=...)
.run(study, start, end)` returns `trades` (one row per lot), `nav` and
`unfilled`.

## The report (`python/report.py`)

- **`primary_t(kind, ...)`:** the statistic G-REAL uses. An event trade gets
  the event-level t, clustered by month. A portfolio gets the calendar-time
  monthly t, Newey-West.
- **`event_table(pnl, event_date, period, extra=...)`:** a row for all events,
  one without the top 5, one per year, and any extra rows. Each row has
  events, mean, median, win rate, p10, worst, SD, top-5 share, clustered t,
  `years_covered` and `usd_per_yr = total / years_covered`.
- **`nav_report(nav, trades, rf_daily, factors)`:** monthly excess return,
  Newey-West t, Sharpe, maximum drawdown, turnover and capacity used. With
  factors it adds alpha, beta and down-market beta. FF5+UMD daily factors
  (1963-07-01 to 2026-09-30) are in `data/ff/ff5_umd_daily.parquet`, built by
  `python/ff_factors.py` from the Ken French library CSVs; the source files'
  md5s are in `data/ff/ff5_umd_daily.json`. Load with `pit.ff_factors`.

## The harness (`python/harness.py`)

`harness.check(make_study, pit, start, cuts)` runs the study through the
simulator three ways at each cut:
1. on the full data;
2. on data truncated at the cut's close (every row known later dropped);
3. on data whose rows with an event after the cut have their values
   scrambled.

Decisions up to the cut must be identical. Truncation catches a study that
reads beyond its view. The scramble catches a `known_at` set too early,
which truncation can't see. Use `default_cuts` for several cuts, since a
leak only shows on days the study acts.

What it can't see: data a study holds outside the view, such as a frame
loaded at import time. Studies hold none, and review checks for it.

Tests (`tests/test_pit_harness.py`):
- **Fast tier:** known_at rules, revisions, the harness passing a clean study
  and catching a planted leak (closes declared known a day early) and a file
  read, plus fills, the stub rule, settlement of record at a split, round-up,
  T-bill accrual, fees, caps and the report.
- **Slow tier:** the trial 60 port reproducing both periods exactly, the
  harness on the port, and the frozen parse functions reproducing the frozen
  parse.

## Writing a study

```python
class MyStudy:
    def __init__(self, pit): ...
    def decide(self, view, book):
        rows = view.asof("rsplit_filings", permno=p)        # only what is known now
        return [sim.Order(p, "buy", qty=1, lot="id", fill="close")]

pit = P.PIT([P.crsp_daily(path), ...], P.Calendar.default())
res = sim.Sim(pit, actions="crsp_splits").run(MyStudy(pit), start, end)
harness.check(MyStudy, pit, start, harness.default_cuts(pit.cal, start, end))
report.event_table(pnl, dates, period)
```

## The reference port: trial 60 (`python/studies/rsplit_pit.py`)

Reverse-split round-ups rebuilt on this layer. The frozen parse, timeline,
`known_at` and entry rule are imported unchanged. The trades come from three
simulator runs: exit at the close, at the open minus half the spread, and 5
trading days later.

It reproduces trial 60's trades **exactly** (0 differences):
- design: 40 rows, 36 traded, mean $3.6594, t 5.82;
- confirmation: 125 rows, 121 traded, mean $3.1657, clustered t 14.06.

- **Inputs:** the frozen parse output and event table. For the confirmation,
  each is read only if its manifest names the trial60-frozen commit and the
  md5 of `python/` *as it is at the tag*.
- **Declared look-ahead:** the event universe is CRSP's list of splits that
  happened, with their actual ex dates. Trial 60 chose its filings, entry
  window and exit timing from it, so cancelled splits are absent. The
  dataset `rsplit_universe` is registered with `lookahead` set, and the
  harness tests every other dataset.
- **Rerunning trial 60 itself:** `python/` no longer matches the tag, so the
  confirmation guard refuses. To rerun trial 60, check out `trial60-frozen`.
  The port runs on today's code.
