# Limitations

Written before the results, so the caveats cannot be tuned to flatter them.

## Survivorship bias in the universe

`data/universe.csv` is a fixed list of large caps and sector ETFs selected from
companies that exist and are liquid **today**. Firms that were large in 2014 and
then failed, were acquired, or fell out of the index are absent. The sample is
therefore biased toward companies that did well.

Why it was accepted: a point-in-time index-constituent history is paid data. The
honest response is to name the bias, keep it out of any headline claim, and note
that it inflates long-side results specifically.

What it means for interpretation: a long-only momentum result from this universe
should be assumed optimistic. Relative and cross-sectional results, where every
symbol carries the same bias, are less affected.

## Daily data only

Bars are daily OHLCV. That supports claims about multi-day holding periods and
nothing shorter. The label horizon is 5 trading days for exactly this reason.

Consequences:
- No intraday entry, exit, or stop can be evaluated. A stop that would have been
  hit at 10:15am is invisible.
- Order of events inside a day is unknown. `fwd_mae_5d` and `fwd_mfe_5d` record
  the worst and best excursion over the holding period, but not which came first,
  so "the target was hit before the stop" is not answerable from this data.
- The original day-trading framing in `journal/` is therefore **not** testable
  here. That is a real gap between the two halves of the project, not an
  oversight.

## The cost model is an estimate

`python/config.py` models 5 bps slippage, a 2 bps half-spread, and per-share
commission. These are plausible retail figures for liquid large caps, not
measurements from a fill log. Thinner names and larger sizes would pay more.

Mitigation: costs are a parameter, and sensitivity to them is reported rather
than assumed. Any result that only survives at zero cost is reported as not
surviving.

## Prices are adjusted, with the usual caveats

Returns use `adj_close`, which folds in dividends and splits. Adjusted series are
revised retroactively by the vendor, so a backtest run today sees a slightly
different history than one run in 2020. Raw `close` is stored alongside so
position sizing uses prices that actually existed.

## A single data vendor

All bars come from one free source. No second vendor cross-check, so a systematic
vendor error would pass through undetected. The quality checks catch internal
inconsistency, not vendor-wide bias.

## Sample size and regime coverage

3,017 trading days spanning 2014 to 2025 includes the 2020 crash and the 2022
bear, which is better than a single-regime sample. It is still one history of one
market. Rules tuned on it are tuned on that history.

## What this project does not claim

- That any strategy here will make money.
- That a backtest predicts future returns.
- That daily-bar results transfer to intraday trading.
- That statistical significance on 2014-2025 data implies significance going
  forward.

## The result is chaotically sensitive to its inputs (measured)

This was discovered by accident and is the most important caveat in the project.

The pipeline was rebuilt from scratch, re-downloading the same symbols over the
same date range. Net P/L changed from $1,596 to $3,191, roughly double, with no
code change.

The inputs had barely moved. Comparing the two downloads row by row across all
200,571 bars:

| Column | Rows differing | Max relative difference |
|---|---|---|
| `close` | 0 | 2.2e-16 (floating point only) |
| `volume` | 0 | 0 |
| `adj_close` | 140,745 (70%) | **1.06e-6** |

Raw prices and volumes were bit-identical. Only adjusted closes moved, by at most
one part in a million, which is vendor rounding from a recomputed adjustment
factor. Three rows changed by more than 1e-6.

A one-part-in-a-million input perturbation doubling the outcome is not a bug in
the pipeline. The model and the backtest were both verified deterministic:
re-running either on an unchanged database reproduces identical predictions and
identical P/L. The sensitivity is a property of the strategy.

The mechanism is threshold crossing. The backtest holds at most three concurrent
positions and enters when predicted probability clears a cutoff. A microscopic
shift in probability reorders which symbols clear it on a given day, a different
trade is taken, capital is committed differently, and the paths diverge and never
reconverge.

**What this means for interpretation.** Any single headline number from this
backtest is one draw from a wide distribution, not a measurement. That is why
`python/sensitivity.py` reports the spread across parameter variants rather than a
point estimate, and why the conclusion rests on the statistical tests in
`R/validate.R` rather than on the equity curve.

It is also independent evidence for the main finding. A strategy with a real,
robust edge does not reverse its outcome when the sixth decimal place of its
inputs changes.

---

# Limitations found the hard way

Everything above was written before the work. These were discovered during it,
and several only came to light because something else went wrong first.

## A silent truncation that read as a fact about the world

The short-interest ingest walked an `offset` parameter forward until the API
stopped returning rows. The API caps offsets, so it halted at 2021-03 and I wrote
"FINRA only retains short interest back to 2021" into the README as though it
were a property of FINRA. The archive goes back to 2017-12-29.

That bug produced the project's most promising result. On the truncated sample
the strategy showed a Sharpe of 1.66 with t = 3.90, and it survived every
robustness check I could construct. On the full sample it is 0.24 with t = 0.69.

**The lesson is structural and worth more than the result.** Dropping 2021 from a
sample that *begins* in 2021 only moves the window to 2022-2026, which was the
good stretch. A robustness check performed inside a truncated sample cannot
detect that the truncation is the problem. No amount of further checking would
have found this. Only more data did.

## Measured variance is not always risk

The crypto funding carry shows a daily Sharpe of 7.11. That number is arithmetic
and meaningless: the variance being measured is the wobble in a contractual cash
flow, not the risk of the position. The actual risks are basis blowout,
liquidation of the short leg in a squeeze, and the venue failing while holding
the collateral. None appear in a funding series.

Any strategy whose risk is a rare total loss will show a magnificent Sharpe until
it does not. A Sharpe above about 3 on a retail-accessible strategy should be
treated as evidence that the wrong variance is being measured.

## Survivorship, in two different directions

Index **additions** are sampled only from names still in the index today, so the
sample is biased toward additions that worked out. Index **deletions** are worse:
only 101 of 291 have usable bars, because the rest were acquired and the data
vendor keeps nothing for delisted tickers. Zero of ten delisted test cases
returned any history.

The deletion exclusion is less damaging than it looks, since an acquired
company's price is pinned to deal terms and has no pressure reversal to measure.
The addition bias plausibly runs *against* the short leg rather than for it. Both
are stated rather than corrected, because they cannot be corrected with free data.

## Vendor data that cannot be verified

The earnings surprise figures come with no documentation of when the consensus
estimate was set. Four tests support that they are genuinely pre-announcement
(the surprise predicts the announcement-day move monotonically at +0.203, the
day-0 move is 3.11x a normal day, and surprises do not cluster near zero). None
of that is proof. A point-in-time estimate source would settle it and costs money.

The 78.4% beat rate against 60-70% in the literature remains unexplained beyond
"survivorship in a current-constituent universe".

## The cost model is a model

Corwin-Schultz estimated from daily OHLC was tried and rejected: it gives AAPL
23bp against a real quoted spread near 1bp, because it conflates volatility with
spread. The replacement uses a tick floor plus an inverse-square-root-of-volume
term, anchored so large caps match a 2bp assumption. It is a reasonable shape
fitted to two well-measured quantities, not a measurement. Everything down-cap
depends on it.

## What 226 trials actually established

Not that no edge exists. That no edge was found in daily-frequency, liquid,
retail-accessible markets using free data and this toolkit, and that the two
things which looked like edges were a factor tilt and a sample artifact.
