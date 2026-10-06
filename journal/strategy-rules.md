# Strategy rules — systematic forward test

**This is a SYSTEMATIC strategy, not a discretionary one. There are no judgment
calls to make. If you find yourself deciding something, the rules are incomplete
and the fix is to write the missing rule down, not to decide in the moment.**

Version: v1 | Frozen: 2026-10-06 | Account: $100,000 paper

## Why this file looks nothing like a day-trading plan

You were asked nine questions about how you trade. Three of the answers were
"whatever is best for a quant project", one was "find signals through
backtesting", and one was "feel". Those answers are not a discretionary strategy,
and writing one for you would have meant inventing preferences you do not hold.

What they describe is a systematic research program, so that is what this is. The
model decides; you execute and record. The one answer that could not survive is
"exit by feel", because a strategy with a discretionary exit cannot be evaluated:
every disappointing result becomes a question about whether you exited well
rather than whether the signal works.

## Universe

Large-cap US equities, the 55 names in `data/universe.csv`. This matches your
answer of big tech and big names, and it already excludes penny stocks, meme
stocks and crypto by construction.

No single-stock selection happens by hand. The model ranks every name in the
universe each day and the rules below take the top of that ranking.

## The rules, frozen

Pinned in `models/gbm_v1.json` and enforced by `python/forward_run.py`:

| Rule | Value |
|---|---|
| Signal | Frozen GBM, SHA-256 registered, trained through 2025-12-31 |
| Entry trigger | Predicted probability at or above 0.55 |
| Positions per day | Top 3 by probability |
| Entry price | Next session's open, never the close that generated the signal |
| Holding period | 5 trading days |
| Stop | 1.5 x ATR(14), and never tighter than 2% of entry price |
| Direction | Long only |
| Max open positions | 3 |
| Max exposure | 50% of account |
| Risk per trade | 1% ($1,000) |
| Daily loss limit | 2% ($2,000) |

The 2% stop floor is arithmetic, not preference: $1,000 of risk inside a $50,000
exposure cap cannot be spread over a stop narrower than 2% of price. It does not
change with account size, because both numbers scale together.

## What you actually do each month

```bash
python3 python/forward_run.py
```

It fetches new bars, scores the frozen model, writes predictions down before
outcomes exist, scores whatever has matured, and prints the record. You are not
asked to approve individual trades, because approving them would reintroduce the
discretion this design removes.

## The rules about the rules

1. **The model does not get retrained.** Not after a bad month, not after a good
   one. Retraining requires a new version and the forward record starts at zero.
2. **No parameter gets adjusted mid-test.** Changing the threshold after seeing
   results is how a forward test becomes a backtest wearing a disguise.
3. **No entry is ever deleted from the ledger.** It is hash-chained, and
   `tests/test_ledger_integrity.py` proves edits, deletions and reorderings are
   detectable.
4. **A mean return is never read without its p-value.** The report prints them
   together for this reason.

## What you already know before starting

From the backtest in this repository, and it should temper expectations:

- Transaction costs consumed 74% of gross profit, and that barely improved at
  $100k (63%) or even $1M (61%). Costs are proportional, so capital does not
  rescue them.
- The same signal was statistically indistinguishable from luck over seven years
  (Reality Check p = 0.149).
- 2026 year-to-date, as an out-of-sample holdout: +0.329% excess per 5-day
  period, p = 0.452, 95% CI spanning -0.53% to +1.18%. Promising-looking mean,
  no evidence behind it.
- The benchmark is not zero. It is the index, which returned 17% a year over the
  test period.

The expected outcome of this forward test is that it confirms the backtest and
finds nothing. That is a perfectly good result and the reason the ledger is built
to make it impossible to quietly walk away from.

## Open questions you have not answered

Left deliberately blank rather than guessed:

- Whether you want a second frozen variant running alongside v1 as a comparison
- Whether to extend the universe beyond the current 55 names
- What would make you stop the test, decided in advance rather than mid-drawdown
