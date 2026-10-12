# Edge Audit

**A backtester that tries to prove its own results wrong.**

I tested 340 trading strategies against 12 years of US stock market data to find
one that beats simply buying the S&P 500. **None of them did.** This repository
is the evidence, including every time I fooled myself and caught it.

Most backtesting projects are built to find a winning strategy. This one is built
to check a winner you think you already have, and it is willing to say no.

**Python · SQL · R · Excel**

---

## The result

| | Best it ever looked | What it actually is |
|---|---|---|
| Short interest | 1.66 Sharpe | **0.24** |
| Earnings drift | 0.34 Sharpe | **-0.17** |
| Contract awards | looked real on one year | **refuted** |
| **Just buying SPY** | | **0.91** |

Sharpe ratio measures return per unit of risk. Higher is better. The index
returned 0.91 over this period, and nothing I built beat it once the mistakes
were removed.

## Why "nothing worked" is the finding, not a failure

If you test enough strategies, one will look good by pure chance. This is the
single most important idea in the project, and it is why the result is a real
answer rather than a shrug.

Here is how high a Sharpe ratio pure luck hands you, if you test strategies that
have **zero** real edge:

```
 strategies tested    best Sharpe you get from luck alone
                 1                                  0.00
                10                                  0.60
               100                                  0.96     <- SPY itself was 0.91
               340                                  1.12     <- where this project sits
```

Read the last line. Test 340 worthless ideas and the luckiest one posts a 1.12,
which looks better than the index. So a backtest number means nothing on its own.
What matters is whether it beats what luck would have given you at the number of
things you tried.

That is why every strategy here is **written down before it runs**, hypothesis
first, and why abandoned attempts stay in the count forever. A 45-variant
parameter sweep counts as 45 attempts, not 1.

## The three that got closest, and what killed each

**Short interest looked like a 1.66 Sharpe.** It passed every robustness check I
could build. Then I found a bug in my own data download: it had silently stopped
early and I was testing on half the available history. On the full sample it is
**0.24**.

> The lesson worth keeping: a robustness check run inside a truncated sample
> cannot detect that the truncation is the problem. More checking would never
> have found this. Only more data did.

**Earnings drift looked like the real thing.** It was the only strategy to clear
the usual statistical bar. Then I checked whether it was actually new information
or just a repackaged version of things anyone can buy for free. Stripping out
market exposure, company size and momentum took it from +0.34 to **-0.17**. It
was momentum in a costume, because companies that beat expectations have usually
already gone up.

**Federal contract awards looked promising for one year.** Companies winning big
government contracts seemed to rise beforehand, which fit a clean story. So I ran
a control: the same companies, but on random dates instead of award dates. On the
full sample of 26,643 awards, **the random dates performed better than the real
ones.** The one-year result was noise from a handful of defense stocks during a
defense rally.

The full account of every candidate is in **[docs/FINDINGS.md](docs/FINDINGS.md)**.

## The data

Six sources, chosen because each is produced by a different process. Price
history, analyst forecast error, audited financial statements, insider filings,
short positioning and federal spending do not share a common failure mode.

| Source | What it is | Size |
|---|---|---|
| Daily bars | prices for the S&P 500, 400 and 600 | 4.4M rows, 1,512 stocks |
| Earnings | announcement surprise vs consensus | 112,850 events |
| SEC filings | as-filed fundamentals, real filing dates | 453,279 facts |
| Insider trades | SEC Form 4 purchases and sales | 523,953 trades |
| Short interest | FINRA bi-monthly positioning | 287,490 observations |
| Federal contracts | USAspending awards over $10M | 101,986 actions, $4.8tn |

Every source is **point-in-time**: each row is tagged with the date that
information became public, not the date it describes. Without this a backtest
quietly trades on facts that had not been published yet.

## How it checks itself

1. **A test that proves there is no peeking at the future.** It chops the
   database off at an old date, rebuilds every calculation from scratch, and
   checks the answers come out identical. Anything secretly reading ahead fails.
   It caught a real bug on its first run.
2. **A count of every strategy ever tried**, hash-chained so the number cannot be
   quietly reduced later.
3. **Data the search is not allowed to read.** Sealed, and opening it is a
   one-way door that gets recorded.
4. **A forward test, not a re-run.** The model is frozen with a fingerprint and
   scored on data that did not exist when it was frozen. Predictions are written
   down before the outcomes exist.
5. **Trading costs charged at each stock's own spread**, because applying
   large-company costs to small companies invents profit out of an assumption.

## Mistakes it caught in my own work

Nine arithmetic errors, and every single one had made the results look **better**,
never worse. That is not coincidence: a bad number gets moved past, a good number
gets investigated, so the errors that survive are the flattering ones.

| | |
|---|---|
| A 14-day average computed from 13 days | silently inflated every signal |
| Trading costs counted twice | and in another place, cancelled to zero |
| Overlapping returns compounded as if sequential | produced a 100% drawdown |
| 499 blank spreadsheet rows read as real trades | |
| A "market-neutral" book carrying real market exposure | hedging it cut 0.52 to 0.36 |

Detail in [docs/FINDINGS.md](docs/FINDINGS.md).

## What is running now

`index_v1` is frozen and recording monthly: short each new addition to the S&P
500 for ten sessions, hedged with the index. It is the only candidate whose
explanation made a testable prediction about the *shape* of the effect, and
testing that prediction is what exposed the flaw in the original version.

It **fails its own luck bar** (0.61 against 1.12) and is frozen anyway, because
the objection against it is sample size, and that is the one objection that time
answers. At roughly 12 events a year it needs years before it says anything.

`v1`, the machine learning price model, keeps running as a **control** rather
than a candidate. Its signal is negative once market exposure is stripped out, so
if both it and `index_v1` drift upward together, that is evidence the market did
it rather than either strategy.

Two strategies were **retired as refuted**: `short_v2` after the data bug, and
the earnings model after the factor check.

Nothing here connects to a broker, places an order, or holds credentials.
Execution stays a human decision.

## Run it

```bash
python3 build.py --from ingest      # whole pipeline, pinned data, no network
python3 python/forward_run.py       # the monthly pre-registered forward test
python3 python/registry.py status   # what have I tried, and what is the bar now
python3 python/lockbox.py status    # what am I not allowed to look at
```

The exact prices behind the published numbers are committed in
`data/raw/bars_snapshot.csv.gz`, because data vendors quietly revise historical
prices and a fresh download will not reproduce them.

## Layout

```
python/     ingest, signals, portfolio construction, and the audit tools
            registry.py          every strategy ever tried, hash-chained
            deflated_sharpe.py   what a result must beat, given how many you tried
            lockbox.py           data the search is not allowed to read
            breadth.py           how many independent bets a portfolio really has
sql/        schema, features, labels. Features and labels in separate files so
            nothing that looks forward can leak into something that looks back
R/          bootstrap tests and figures
tests/      lookahead audit, ledger integrity, search controls (21 checks)
docs/       FINDINGS.md, METHODOLOGY.md, LIMITATIONS.md
```

## Honest limits

- The stock universe is **today's** index members, so companies that went
  bankrupt or were acquired are missing. This flatters results.
- The data is daily, so nothing intraday is testable.
- The cost model is an approximation of something that properly needs
  trade-by-trade data.
- Earnings estimates come from a vendor that does not document when they were set.

Longer version in [docs/LIMITATIONS.md](docs/LIMITATIONS.md).

## Not investment advice

A research exercise. It connects to no broker, places no orders, and its central
finding is that the strategies it studied do not beat the index.

## Résumé summary

> **Edge Audit** — Python, SQL, R, Excel
> Built a research pipeline that backtests trading strategies and then audits
> whether the edge is real. Ingested six independent point-in-time data sources
> (4.4M daily bars across 1,500 equities, 102K federal contract actions, 113K
> earnings announcements, 453K as-filed SEC XBRL facts keyed on filing date, 524K
> insider transactions, 287K short-interest observations). Engineered features in
> SQL window functions and proved the pipeline leak-free with a truncation-rebuild
> audit. **Tested 340 strategies, logged before each run, and found none that
> survives: the best was a factor tilt in disguise (+0.34 to -0.17 after
> neutralizing beta, size and momentum) and the second-best fell from a 1.66
> Sharpe to 0.24 once a pagination bug in my own ingest was fixed and the sample
> doubled.**

One-bullet version:

> - Built a backtesting pipeline that audits its own results across six data
>   sources: 340 strategies tested and logged before each run so a lucky one can't
>   pass as real; the best looked like a 1.66 Sharpe until fixing a bug in my own
>   data pull doubled the sample and took it to 0.24
