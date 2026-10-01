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
