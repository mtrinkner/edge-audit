# Can a Small Trader Find a Real Trading Edge?

I've tested **857 strategy variants across 60 studies** on US stock market data:
technical analysis, day-trading setups, published anomalies, two published AI
models that read stock charts, and small contract-backed trades. Each study is on
a permanent ledger, charged realistic costs, and checked against what pure luck
would produce. **One has passed**, a small reverse-split trade (trial 60), and the
search for something bigger is ongoing.

**Python · SQL · R · Excel · PyTorch · WRDS (CRSP, IBES)**

## See it live

- **[Edge Audit Live](https://claude.ai/artifact/3Jq6Ykv74Sf34pXKBSC1Qd)**: the
  whole project as an interactive dashboard: how it was tested, every result,
  the bugs caught, and the full log of all 857 tests.
- **[Signal Charts](https://claude.ai/artifact/NfTTDBQTivhiKaU6f5xJxW)**: price
  charts for the 100 most-traded US stocks with every tested signal marked
  where it fired, next to that signal's tested record.

---

## Where it stands

**One real edge, and it's small. Why the rest failed is the bigger story.**

| What I tested | Tests | Result |
|---|---|---|
| 41 technical signals on daily data (golden cross, RSI, MACD...) | 41 | None beat just holding the stock; most trailed it slightly |
| 12 day-trading setups on hourly data (breakouts, VWAP, gaps...) | 12 | No signal at all, even with zero trading costs |
| The same setups with smarter exits, combined, or on cheaper stocks | 377 | Nothing |
| Published anomalies (intraday momentum, index deletions) | 2 | Traded away since they were published |
| The overnight premium (buy at the close, sell at the open) | 1 | **Real and tradeable**, but no better than holding |
| Two published chart-reading AI models, rebuilt exactly (one on CRSP) | 8 | Matched the papers, then failed their declared tests on later years. On one, a weak signal remains before costs (Sharpe about 0.7) and about zero after |
| Earnings drift (PEAD) on IBES and CRSP, 1995–2025 | 2 | Real but decaying, from 1.11% to 0.26% a month gross. Net +0.12% a month, t = 0.70 |
| Odd-lot tender offers, 2010–2025 | 2 | Failed as declared (t = 3.06, then 0.17). Entries below the guaranteed price never lost in 32 deals, but pay about $200 a year, and that cut was found after seeing the data |
| Reverse-split round-ups, 2019–2025 | 1 | **Passed** (121 events, t = 14.1, $3.17 an event). About $55 a year per account, so real but small |

### Screened before spending a test

Since October 28, each idea must first show that a pre-registered test could
realistically confirm it. Since November 18 that gate has two parts: is it
real (chance of passing at least 0.25), and is it worth having ($1,500 a year
on its own, or $100 as part of a book of small trades).

| Idea | Size | Chance of passing | Verdict |
|---|---|---|---|
| Merger arbitrage | About $540/yr (literature estimate) | 0.016 | killed |
| SPAC trust arbitrage | $915/yr projected | 0.056 | killed |
| Reverse-split round-ups | $7/yr at first; $123/yr after the rule was rebuilt | 0.996 | rebuilt, declared as trial 60, **passed** |

Next in the queue, screened on paper before any prices are looked at:
closed-end fund tender offers, small cash merger arbitrage, and micro-cap
distress filings ([the protocol](docs/specs/step4_paper_screen.md)).

## The edge that passed: reverse-split round-ups

When a company does a reverse split, many brokers round a holder's fractional
share **up** to a whole share instead of paying cash for it. Buy a few shares
just before the split, end up with one whole post-split share, sell it.

- **Declared before the result:** the rule was frozen and tagged
  (`trial60-frozen`), registered as trial 60, then run once on 2019–2025.
- **Result:** 121 events, $3.17 an event, t = 14.06 (clustered by month). It
  held without the top 5 events, at a much lower round-up rate, and in
  2024–2025 alone.
- **Size:** about $55 a year per account, and $125 at the 2024–2025 rate. A
  $5 commission turns it negative, so it only works at zero-commission brokers.
- **Now:** a forward ledger records each new reverse split as it is announced
  ([docs/REVERSE_SPLIT_ROUNDUP.md](docs/REVERSE_SPLIT_ROUNDUP.md)).

It is real but small, so it counts as one piece of a book of small trades.
It is not the goal on its own.

## Real, but not an edge: the overnight premium

Most of the stock market's return happens **overnight**, between the close and
the next morning's open. Buying the 50 biggest stocks at the close and selling at
the open, through the exchange auctions so you don't pay the spread:

| | Return per year | Volatility | Sharpe |
|---|---|---|---|
| Overnight only, after costs | +12.4% | 13.5% | 0.92 |
| Just holding the same stocks | +19.2% | 20.4% | 0.94 |

It made money in 11 of 13 years, with t = 3.28. But it's the same risk as owning
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
               857    1.22    <- where this project is
```

So:

1. **Every test goes on a permanent ledger**, with the hypothesis and the
   pass/fail bar, declared before it runs since the workflow was built. Failed
   tests stay in the count forever. The registry is
   hash-chained, so entries can't be quietly deleted or edited.
2. **Corrections are added, never edited in.** When a past result turns out to
   be wrong, a note is attached to it and the original stays.
3. **Every strategy is compared with something.** Usually that's holding the
   same stock over the same time, so a strategy can't take credit for the market
   going up.
4. **Automated tests for peeking at the future.** They remove or replace later
   data, then check that no earlier decision changes: SQL features, intraday
   rules and event studies. Each must also catch a planted leak. Since step 3,
   every new study runs on one shared point-in-time data layer and simulator
   ([docs/INFRASTRUCTURE.md](docs/INFRASTRUCTURE.md)), and passes these checks
   automatically.
5. **A gate before every test.** It estimates the chance a test can pass, with
   a haircut for how much the idea was searched, and the dollars it could make
   ([docs/METHODOLOGY.md](docs/METHODOLOGY.md)). It's checked against every past
   trial with a known outcome.
6. **Ties go against me.** If an hourly bar hit both the stop and the target,
   the trade counts as stopped out.

## Mistakes I caught in my own work

Almost every bug made the results look **better**, not worse. A disappointing
number gets accepted and moved past; a good one should get checked, and these
are what checking found.

| What broke | What it did |
|---|---|
| A day-trading rule compared each bar with the day's full volume, including later hours | Created 97% of its "edge" (+6.5bp → +0.2bp) |
| Each day's budget was split across that day's trades | Sharpe 1.19 on unseen ETFs was −1.27 once fixed |
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
python3 python/audit_root_causes.py       # why the first 837 tests found nothing
python3 python/registry.py status         # every test, and the bar now
python3 python/pead.py run                # earnings drift (needs WRDS data)
python3 python/odd_lot_study.py           # odd-lot tender offers (needs WRDS data)
python3 python/spac_trial.py gate         # SPAC trust arbitrage (needs WRDS data)
python3 python/reverse_split.py gate      # reverse-split round-ups (needs WRDS data)
python3 python/studies/rsplit_pit.py      # trial 60 rebuilt on the shared layer (needs WRDS data)
python3 python/gate.py rescreen           # past kills under the current gate
python3 -m pytest -m "not slow"           # fast test tier (full: python3 -m pytest)
python3 python/deflated_sharpe.py --power --prior-sharpe 0.6 --years 5
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
  pead.py, odd_lot_study.py, spac_trial.py, reverse_split.py
                      earnings drift and the contract-backed trades
  rsplit_ledger.py    the reverse-split forward ledger
  gate.py             the pre-declaration gate: is it real, is it worth it
  pit.py, sim.py, report.py, harness.py
                      the shared point-in-time layer, simulator, report and
                      look-ahead harness every new study uses
  studies/            studies written on that layer
docs/
  AUDIT_2026-10-14.md why nothing worked, measured; start here
  METHODOLOGY.md      the goal, the gate, and how every trial is run
  REVERSE_SPLIT_ROUNDUP.md  the trade that passed
  INFRASTRUCTURE.md   the shared research layer
  SPEC_CHECKLIST.md   the checklist every declaration fills in
  specs/              protocols for the current step
  LIMITATIONS.md      what this data can't tell you
tests/                lookahead checks (test_no_lookahead_events.py for event
                      studies), ledger integrity, causal sizing
reports/              the result tables behind every number above
```

## Honest limits

- **Early studies used free data only.** Daily prices since 2014, hourly for about
  two years, and today's companies, so firms that failed are mostly missing. Those
  results are flattered. Studies from trial 57 on use CRSP, with delisting returns
  and closing quotes.
- **Later studies have limits too.** Short confirmation windows can't detect small
  effects, and the reverse-split sample leaves out cancelled splits.
- **"No edge here" isn't "no edge anywhere."** Professional firms trade on data
  and speed this project doesn't have.

More detail in [docs/AUDIT_2026-10-14.md](docs/AUDIT_2026-10-14.md) and
[docs/LIMITATIONS.md](docs/LIMITATIONS.md).

## Not investment advice

A research exercise. It connects to no broker and places no orders.
