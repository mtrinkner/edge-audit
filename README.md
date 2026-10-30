# Can You Find a Trading Edge With Free Data?

I tested **856 trading strategies** on US stock market data: classic technical
analysis, day-trading setups, published market anomalies, and two published AI
models that read stock charts. Each test was written down before it ran, charged
realistic trading costs, and checked against what pure luck would produce.

**Python · SQL · R · Excel · PyTorch**

## See it live

- **[Edge Audit Live](https://claude.ai/artifact/3Jq6Ykv74Sf34pXKBSC1Qd)**: the
  whole project as an interactive dashboard: how it was tested, every result,
  the bugs caught, and the full log of all 856 tests.
- **[Signal Charts](https://claude.ai/artifact/NfTTDBQTivhiKaU6f5xJxW)**: price
  charts for the 100 most-traded US stocks with every tested signal marked
  where it fired, next to that signal's tested record.

---

## The answer

**No. And the more interesting part is why.**

| What I tested | Tests | Result |
|---|---|---|
| 41 technical signals on daily data (golden cross, RSI, MACD...) | 41 | None beat just holding the stock; most trailed it slightly |
| 12 day-trading setups on hourly data (breakouts, VWAP, gaps...) | 12 | No signal at all, even with zero trading costs |
| The same setups with smarter exits, combined, or on cheaper stocks | 377 | Nothing |
| Published anomalies (intraday momentum, index deletions) | 2 | Traded away since they were published |
| The overnight premium (buy at the close, sell at the open) | 1 | **Real and tradeable**, but no better than holding |
| Two published chart-reading AI models, rebuilt exactly (one on CRSP) | 8 | Matched the papers, then lost money on years they had never seen |

## The one thing that's real

Most of the stock market's return happens **overnight**, between the close and
the next morning's open. Buying the 50 biggest stocks at the close and selling at
the open, through the exchange auctions so you don't pay the spread:

| | Return per year | Volatility | Sharpe |
|---|---|---|---|
| Overnight only, after costs | +12.1% | 13.5% | 0.91 |
| Just holding the same stocks | +18.6% | 20.4% | 0.94 |

It made money in 11 of 13 years, with t = 3.25. But it's the same risk as owning
the market half the time, so you get less return for less risk in about equal
proportion. It's a payment for bearing risk, not an edge.

## The fake edge I almost believed

One day-trading rule reached a **Sharpe of 1.52** on ETFs. It held up on a
second set of stocks, and it beat the luck bar. Then I tested it on 24 ETFs it
had never seen, and it fell to 1.19.

Looking for the reason, I found it: the backtest split each day's money evenly
across all of that day's trades. That only works if you know at 10:30 how many
trades will happen by 3:30, and that number predicted the outcome. On days the
market kept falling, more trades fired and they all lost; the split quietly
shrank those days. Fixed so it only uses information available at the time:

**Sharpe −1.27.**

## How I kept myself honest

If you test enough strategies, one will look good by pure chance. Here's the
best Sharpe ratio that luck alone produces when every strategy has **zero** real edge:

```
 strategies tested    best result from luck alone
                 1    0.00
                10    0.60
               100    0.96    <- the S&P 500 itself was 0.91
               840    1.22    <- where this project is
```

So:

1. **Every test is registered before it runs**, with the hypothesis and the
   pass/fail bar. Failed tests stay in the count forever. The registry is
   hash-chained, so entries can't be quietly deleted or edited.
2. **Corrections are added, never edited in.** When a past result turns out to
   be wrong, a note is attached to it and the original stays.
3. **Every strategy is compared with something.** Usually that's holding the
   same stock over the same time, so a strategy can't take credit for the market
   going up.
4. **Automated tests for peeking at the future.** They swap future data for
   random data and check that no signal changes.
5. **Ties go against me.** If an hourly bar hit both the stop and the target,
   the trade counts as stopped out.

## Mistakes I caught in my own work

Almost every bug made the results look **better**, not worse. A disappointing
number gets accepted and moved past; a good one should get checked, and these
are what checking found.

| What broke | What it did |
|---|---|
| A day-trading rule used the day's full volume, including later hours | Created 97% of its "edge" (+12.6bp → −0.7bp) |
| Each day's budget was split across that day's trades | Turned a losing strategy into a Sharpe of 1.52 |
| t-statistics tested a different average than the one printed | Showed t = 5.11 where the right answer was t = 0.26 |
| The spread estimator, on hourly bars | Returned noise: SPY at 1.64bp when its real spread is 0.17bp |
| A data download stopped at 2021 | A Sharpe of 1.66 that was 0.24 on the full data |
| A bankrupt company's old penny-stock prices stitched to its new shares | A fake +22,500% week that made a chart-reading AI look like it had an edge, and made 41 signals look 5× worse than they were |
| The registry scored daily data as if it were weekly | Printed "clears the bar" for a result that failed |
| A minimum-trade filter applied per stock | Silently deleted the golden cross from the study |
| `NaN` is truthy in Python | Untested signals printed as passing |

## Run it

```bash
python3 build.py --from ingest            # daily pipeline, pinned data
python3 python/ta_exits.py                # 41 technical signals, real stops and targets
python3 python/intraday_study.py          # 12 day-trading setups
python3 python/overnight_auction.py       # the overnight premium
python3 python/audit_root_causes.py       # why 840 tests found nothing
python3 python/registry.py status         # every test, and the bar now
```

## Layout

```
python/
  registry.py         every test ever run, hash-chained so it can't shrink
  evaluation.py       one way to score a strategy, built to avoid the bugs above
  deflated_sharpe.py  what a result has to beat, given how many tests were run
  signals.py          the 41 daily technical signals
  ta_exits.py         tests them with stops and targets
  intraday_*.py       the day-trading setups and their follow-ups
  overnight_auction.py, intraday_momentum.py, index_deletions.py
                      the published effects
docs/
  AUDIT_2026-10-14.md why nothing worked, measured; start here
  LIMITATIONS.md      what this data can't tell you
tests/                lookahead checks, ledger integrity, causal sizing
reports/              the result tables behind every number above
```

## Honest limits

- **Free data only.** Daily prices since 2014 and hourly prices for about two
  years. No quote-level data, so trading costs are estimated, not measured.
- **The stock list is today's companies.** Firms that went bankrupt or were
  bought out are mostly missing, which flatters every result.
- **"No edge here" isn't "no edge anywhere."** Professional firms trade on data
  and speed this project doesn't have.

More detail in [docs/AUDIT_2026-10-14.md](docs/AUDIT_2026-10-14.md) and
[docs/LIMITATIONS.md](docs/LIMITATIONS.md).

## Not investment advice

A research exercise. It connects to no broker and places no orders.
