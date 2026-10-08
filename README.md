# Edge Audit

Backtests trading strategies, then audits whether the edge it found is real.

Most backtesting projects are built to find an edge. This one is built to check
the one you found, and it is willing to tell you no. It did tell me no.

**Python · SQL · R · Excel**

---

## The short version

I wanted to know if a short-term stock signal could actually make money. So I
pulled seven years of daily prices for 67 large caps, built 41 features, trained
models on past years and tested them on later ones, and traded the result on
paper with realistic costs.

It lost to buying the index, badly:

| | Strategy | Just buying SPY |
|---|---|---|
| Return over 7 years | **+15.3%** | **+204.4%** |
| Sharpe | 0.23 | 0.91 |
| Max drawdown | 17.1% | 33.7% |

That is the whole finding. Everything below is how I know it is not just bad luck
or a bug.

## Four reasons it was never going to work

**Costs took most of it.** The signal did find something: $6,078 of gross profit
on a $10k account. Slippage, spread and commission took $4,482 of that. And no,
a bigger account does not fix it. At $100k costs are still 63% of gross, at $1M
still 61%, because slippage is proportional. Only the fixed commission scales
away.

**My sample was five times smaller than it looked.** The model's best features
are market-wide, so its picks all pile onto the same few days. In the worst test
year, 1,342 picks landed on 52 dates, 41% of them on ten. Counting each row as a
separate trade made the Sharpe look like 1.59. Counting one return per day, which
is what actually happens to your account, it is 0.36.

**It fails the "how many things did you try" test.** Against a null of zero, the
model is significant at p = 0.009. But stocks drifted up over this period, so
zero is the wrong thing to beat. Against buy-and-hold, and correcting for the
fact that I tested 49 variants, p = 0.149. Nothing survives.

**The whole thing is balanced on a knife edge.** I rebuilt the project after
re-downloading the same data and net profit doubled. The data had moved by one
part in a million, vendor rounding on adjusted closes, with raw prices identical.
The model and backtest are both verified deterministic. A real edge does not flip
when the sixth decimal place changes.

![Reality check](reports/figures/02_reality_check.png)

## The part I am actually proud of

Anyone can run a backtest. The hard part is not fooling yourself, and that is
where most of this code went.

**A test that proves there is no lookahead.** `tests/test_no_lookahead.py` chops
the database off at an old date, rebuilds every feature from scratch, and checks
they come out byte-identical to the full-history version. Anything secretly
peeking at the future fails. It caught a real bug immediately: SQLite's `AVG`
was skipping a null and calling a 13-day average a 14-day ATR.

**A count of every strategy I ever tried.** Testing ideas until one looks good
guarantees one will look good. With 348 independent observations, the luckiest of
100 strategies with *zero* real edge posts a Sharpe around 0.96, which beats the
index. So `python/registry.py` logs every strategy before I run it, hypothesis
written down first, and abandoned attempts stay in the count forever. Then
`python/deflated_sharpe.py` tells me what a new candidate has to clear.

```
 trials   expected best from pure luck
      1                          0.000
     10                          0.599
     50                          0.866
    100                          0.963      <- SPY was 0.91
    500                          1.162
```

My own best sweep scored 0.830 against a bar of 0.863 at 49 trials. It fails its
own test, which is the point.

**Data I am not allowed to look at.** `python/lockbox.py` seals a date range the
search physically cannot read. Opening it is a one-way door, recorded with the
strategy it was opened for. Worth being honest here: every bar from 2014 to 2026
is already burned, because the backtest searched all of it. So the lockbox is
forward-dated and fills up as time passes. Slower, but it is the only version
that is actually clean.

**A forward test instead of a rerun.** Re-running the backtest monthly would just
be asking the same question over and over until it answered the way I wanted. So
the model is frozen with a SHA-256, and each month scores it on bars that did not
exist when it was frozen. Predictions get written down *before* outcomes exist,
into an append-only hash-chained ledger. `tests/test_ledger_integrity.py` proves
that editing a loss into a win, deleting a bad month, or reordering entries all
get caught.

| Stage | Trades | Effective obs | Excess vs SPY | p |
|---|---|---|---|---|
| holdout (2026 YTD) | 496 | 33.8 | +0.329% per 5 days | **0.452** |
| live (pre-registered) | 0 | — | — | — |

That holdout number looks healthy and means nothing. Its 95% interval runs from
-0.53% to +1.18%. The report prints the p-value next to the mean every time so
the number can never be read on its own.

## The signal zoo

Then I tested the classics. Forty-one published technical signals — golden cross,
RSI, MACD, Bollinger bands, Donchian breakouts, support and resistance,
candlestick patterns, gaps, volume spikes — at three holding periods each. 123
trials.

**Zero of 122 cleared their bar. Zero survived multiple-comparison correction.**

| signal | hold | indep. obs | Sharpe | its bar | verdict |
|---|---|---|---|---|---|
| rsi_oversold_20 | 21d | **6** | 1.98 | 7.89 | fails |
| rsi_oversold_30 | 21d | 41 | 1.66 | 3.02 | fails |
| above_sma_200 | 63d | 47 | 1.46 | 2.82 | fails |
| cross_above_sma_50 | 63d | 35 | 1.25 | 3.27 | fails |

15 of 122 looked significant at p < 0.05. Pure noise predicts 6. After
Benjamini-Hochberg, none survive.

Look at the top row, because it is the best argument for building any of this.
A 1.98 Sharpe computed from **six** independent observations. My first version of
this report fed the project's sample size into the correction instead of each
signal's own, and announced that result was 99.2% likely to be real. It is 3.4%.
The tool built to catch false discoveries caught mine.

## Chasing a 1.5 Sharpe with three data sources

The price signals were the problem. Thirteen of them turned out to be 2.9
effective independent strategies, because they all transform one series. So I
went looking for data generated by a different process.

| Source | What it is | Facts |
|---|---|---|
| Price | daily OHLCV | 1.58M bars, 515 symbols |
| Earnings | announcement surprise vs consensus | 42,687 events |
| SEC XBRL | as-filed fundamentals, real filing dates | 453,279 facts |

**Breadth responded exactly as the theory says it should:**

| Sources | Nominal | Effective | Avg corr |
|---|---|---|---|
| Price only | 13 | 2.9 | +0.19 |
| Price + earnings | 19 | 5.0 | +0.11 |
| Earnings + fundamentals | 12 | **7.0** | **+0.04** |

**Sharpe did not:**

| Book | Return/yr | Sharpe | t |
|---|---|---|---|
| Price only | -3.17% | -0.11 | -0.38 |
| **Earnings only** | +2.21% | **+0.52** | 1.84 |
| Fundamentals only | -2.81% | -0.60 | -2.11 |
| Earnings + fundamentals | -0.18% | +0.12 | 0.41 |

More independent bets do not help when the new bets are bad bets. IR = IC x
sqrt(breadth) has two terms and I had been treating breadth as the binding one.

### The SEC layer is the best data in the project

EDGAR gives every reported figure stamped with the date it was filed. That
matters more than it sounds: companies restate, and Microsoft refiled one
quarter's net income five times over two years. The primary key includes `filed`
so a 2015 decision sees the 2015 number, not the 2017 correction.

### Verifying the earnings data was point-in-time

The whole 0.52 rests on surprise figures whose estimate timing the vendor does
not document. Four tests on 24,028 announcements:

| Test | Result |
|---|---|
| Surprise predicts the announcement-day move | +0.203, monotonic across quintiles |
| Day-0 return spread, worst to best quintile | +4.16 points |
| 60-day drift spread (this is PEAD) | +2.50 points |
| Volatility on announcement day | 3.11x a normal day |
| Median absolute surprise | 6.82%, only 9.8% within +/-1% |

Estimates revised toward the actual would cluster surprises near zero and could
not produce a clean monotonic same-day reaction. One caveat stands: 78.4% of
announcements beat, against 60-70% in the literature, which I attribute to a
universe of current index members.

### Three attempts to improve it, all failed

Restricting to the documented drift window, dropping the weak components, and
adding the announcement-day reaction each made the sleeve worse (0.52 to 0.28).
Conditioning fundamentals on market trend or volatility produced a negative
Sharpe in all four regime cells. Both were declared before they were run and
both are recorded as failures rather than quietly dropped.

### Where it lands

196 registered trials. The bar a new candidate must clear is now 1.036. The best
book this project ever produced is 0.52, with a deflated probability of 0.013 of
being real, against a benchmark of 0.91 and a target of 1.5.

Reaching 1.5 by combining sleeves of that quality needs 8.3 uncorrelated ones. I
have 7.0, and the two added were negative. That is the ceiling this data implies.

## Going down the cap spectrum

The literature says anomalies concentrate where arbitrage is costly. The cleanest
causal evidence is the SEC's Reg SHO pilot: lifting short-sale constraints cut
long-short anomaly returns by 94bp for small stocks against 48bp for large. So I
expanded the universe from 515 names to 1,515 (S&P 500 + 400 + 600), 4.4M bars,
112,850 earnings events.

**First I had to fix the cost model**, because testing small caps on a large-cap
cost assumption manufactures an edge out of an accounting choice. Corwin-Schultz
estimated from daily OHLC turned out unusable as a level: it gives AAPL 23bp
against a real quoted spread near 1bp, because it conflates volatility with
spread. Replaced with a model built on quantities our data measures precisely, a
tick-size floor and an inverse-square-root-of-volume term, anchored so large caps
keep the 2bp assumption used everywhere else:

| Bucket | Median price | Median $vol/day | Half-spread |
|---|---|---|---|
| S&P 500 | $98 | $212M | 2.1 bp |
| S&P 400 | $59 | $44M | 4.4 bp |
| S&P 600 | $35 | $15M | 7.6 bp |

**The hypothesis was refuted. PEAD gets weaker down-cap, not stronger:**

| Bucket | Names | Gross Sharpe | Net Sharpe | t | Cost drag |
|---|---|---|---|---|---|
| **Large** | 500 | **0.71** | **0.65** | 2.29 | 0.41%/yr |
| Mid | 399 | 0.41 | 0.28 | 1.00 | 0.90%/yr |
| Small | 593 | 0.31 | 0.08 | 0.28 | 1.48%/yr |

Gross drift declines monotonically with size, and costs then roughly triple going
down, so net collapses from 0.65 to 0.08. Small caps lose on both terms at once.

A cost bug of mine nearly hid this. The first version subtracted each leg's cost
and then differenced the legs, which cancelled the costs and reported a drag of
0.00% on a book trading small caps at 7.7bp. A long/short book pays on both legs,
so the two terms add.

## Running it

```bash
python3 build.py --from ingest     # whole pipeline, pinned data, no network
python3 python/forward_run.py      # the monthly forward test
python3 python/registry.py status  # what have I tried, and what is the bar now
python3 python/lockbox.py status   # what am I not allowed to look at
```

Every number in this README comes from the first command. The exact bars are
committed in `data/raw/bars_snapshot.csv.gz`, because the data vendor quietly
revises adjusted closes and a fresh download will not reproduce them. Use
`--ingest` when you want to check whether the finding survives new data.

## What is where

```
python/
  ingest_bars.py      downloads and checks bars, idempotent, quality issues stored as rows
  make_workbook.py    builds the Excel journal; validate_excel.py refuses bad rows
  model.py            baselines, logistic, gbm, with the clustering fix
  folds.py            walk-forward splits with a gap so no label leaks into the test year
  backtest.py         next-open fills, costs both sides, gross - costs = net asserted
  registry.py         every strategy ever tried, hash-chained
  deflated_sharpe.py  what a candidate has to beat, given how many you tried
  lockbox.py          data the search is not allowed to read
  freeze_model.py     pin a model with a hash; forward_run.py scores it monthly
sql/                  schema, features, labels, analysis views
R/                    stationary bootstrap, Reality Check, the five figures
tests/                lookahead audit, ledger integrity, search controls
```

## Honest limits

In `docs/LIMITATIONS.md`, but the short version: the universe is today's large
caps so it has survivorship bias, the data is daily so nothing intraday is
testable, and the cost model is an estimate rather than real fills.

## Not investment advice

This is a research exercise. It connects to no broker, places no orders, and its
main finding is that the strategy it studied does not work.
