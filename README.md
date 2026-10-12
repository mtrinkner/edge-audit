# Does Technical Analysis Work?

I tested **41 classic technical trading signals** on 12 years of US stock market
data. Golden cross, death cross, support and resistance, nine candlestick
patterns, RSI, MACD, Bollinger Bands, volume spikes, gaps.

**13.5 million simulated trades.** Here is the answer.

**Python · SQL · R · Excel**

---

## The answer

**They make money. So does just owning the stock. The signal adds nothing.**

Take the most famous one, the golden cross (50-day average crossing above the
200-day):

| | |
|---|---|
| Buy on the golden cross, use a stop and a target | **+0.54%** per trade |
| Just buy the same stock and hold it the same number of days | **+0.62%** per trade |
| **What the signal added** | **−0.08%** |

That pattern holds across almost all 41 signals:

| | |
|---|---|
| Signals that beat buy-and-hold | **4 of 41** |
| Signals that lost to buy-and-hold | **37 of 41** |
| Average amount the signal added | **−0.16%** per trade |

Every one of these signals is profitable in isolation. That is what makes them
convincing in a blog post. The profit is the stock going up, not the signal
finding anything.

## The one signal that is statistically reliable is a reliable loser

Only one of the 41 survived a proper statistical test for multiple comparisons:
`new_52w_high`, buying stocks at a 52-week high, at **p = 0.000**.

Its effect is **−0.05%**. It reliably does worse than holding.

## Why the stop is the problem

Each trade uses a stop loss 2 ATR below entry and a profit target 3 ATR above,
which is the textbook setup. ATR is a measure of how much a stock normally moves
in a day, so the stop and target scale with the stock's own volatility.

That sounds like favorable odds: risk 2 to make 3. Here is what actually happened
across all 41 signals:

```
  stopped out      48% to 58% of trades
  hit the target   41% to 47% of trades
```

**The stop fires more often than the target.** Risking 2 to make 3 only wins if
you are right more than 40% of the time, and these signals are not. The stop
cuts the winners short while holding lets them run.

## The best-looking signals were the worst

This was the most surprising result:

| Signal | Profit per trade | Just holding | Signal added |
|---|---|---|---|
| `gap_up_2pct` | **+3.30%** | +4.03% | **−0.73%** |
| `big_down_day` | **+2.69%** | +3.75% | **−1.06%** |
| `volume_dryup` | +0.89% | +2.21% | **−1.33%** |
| `donchian_break_55` | +0.15% | +0.18% | −0.03% |

The signals with the biggest profits did the most damage. Buying a 2% gap up
returns a healthy +3.30%, and it still loses 0.73% to doing nothing.

The reason: these signals fire on **volatile days**. A volatile stock moves a lot
in both directions, so the raw return looks big, and the stop gets hit before the
move finishes. The signal is selecting volatility, not direction.

## Why a high Sharpe ratio fooled me first

My first version of this study ranked signals by **Sharpe ratio**, which measures
return per unit of risk. `rsi_oversold_20` came out at **1.98**, far better than
the S&P 500's 0.91. It looked like the best thing in the project.

It was wrong for two reasons.

**A Sharpe ratio is a rate, and you cannot spend a rate.** A strategy that sits
in cash most of the time has very little volatility. Small return divided by tiny
volatility is a big ratio. It was being rewarded for *not being in the market*.
Its actual return was worse than buy-and-hold.

**The 1.98 rested on six numbers.** The signal fired 154 times, but overlapping
trades share the same market days, so there were only **6 genuinely independent
observations** behind it. The honest confidence interval was **[−2.79, +6.75]**.

That is why this project reports **excess return over buy-and-hold** as the
headline number, and prints the independent observation count next to every
result.

## How I kept myself honest

The hardest part of this kind of project is not the code. It is that if you test
enough ideas, one will look good by pure chance.

Here is how high a result pure luck gives you, if you test strategies with
**zero** real edge:

```
 strategies tested    best result luck alone produces
                 1                              0.00
                10                              0.60
               100                              0.96    <- the S&P 500 itself was 0.91
               381                              1.13    <- where this project sits
```

Test 381 worthless ideas and the luckiest one looks better than the index. So
five rules:

1. **Every strategy is written down before it runs**, with the hypothesis stated
   first. Abandoned attempts stay in the count forever.
2. **The exit rules were fixed in advance and never tuned.** 2 ATR stop, 3 ATR
   target, straight from the textbook. Searching for the best stop distance is
   where technical strategies get overfit, so I did not search.
3. **Ties go against me.** A daily bar cannot tell you whether the high or the
   low came first. When one bar touched both the stop and the target, I assumed
   the stop. Every number here is a lower bound.
4. **A test that proves there is no peeking at the future.** It chops the
   database off at an old date, rebuilds every calculation, and checks the
   answers match. It caught a real bug the first time it ran.
5. **Real trading costs**, charged at each individual stock's own estimated
   spread, on both sides of every trade.

## Mistakes this caught in my own work

Every single arithmetic error I made had pushed the results to look **better**,
never worse. That is not bad luck. A disappointing number gets accepted and moved
past; a good number gets investigated. So the errors that survive are the
flattering ones.

| What broke | What it did |
|---|---|
| Minimum-trade filter applied per stock | **Deleted the golden cross entirely.** A crossover fires ~3 times per stock in 12 years, so it failed a 50-trade test everywhere and vanished with no error |
| `NaN` is truthy in Python | Untested signals printed as passing the statistical test |
| Spread model returns `NaN` for a stock's first 20 days | One `NaN` silently killed a whole signal's p-value |
| A 14-day average computed from 13 days | inflated every signal |
| Two signals were the same formula twice | 41 names are really 39 distinct tests |

The duplicates are left in and the trial count was **not** reduced, because a
redundant look is still a look.

## Run it

```bash
python3 build.py --from ingest      # whole pipeline, pinned data, no network
python3 python/zoo.py               # 41 signals, fixed-horizon exits
python3 python/ta_exits.py          # 41 signals, real stops and targets
python3 python/registry.py status   # what have I tried, and what is the bar now
```

## Layout

```
python/
  signals.py        the 41 technical signals
  zoo.py            tests them with fixed-horizon exits
  ta_exits.py       tests them with real stops and targets, path by path
  ingest_bars.py    daily price data, idempotent, quality checks stored as rows
  registry.py       every strategy ever tried, hash-chained so it cannot shrink
  deflated_sharpe.py  what a result must beat, given how many things you tried
  lockbox.py        data the search is not allowed to look at
sql/        schema and features. Anything forward-looking lives in a separate
            file from anything backward-looking, so it cannot leak
tests/      lookahead audit, ledger integrity, search controls (21 checks)
reports/    the result tables, including ta_exits.csv behind every number above
```

## Honest limits

- **Daily data only.** Intraday patterns are untestable here, and a stop that
  looks triggered on a daily bar might not have been.
- **The stock universe is today's index members**, so companies that went
  bankrupt or were acquired are missing. This flatters results.
- **One stop/target setting**, not a sweep. A different setting would give
  different numbers. That is the point: I refused to go looking for the best one.
- **Long only.** Short signals like the death cross are tested as long entries.
- This project previously tested non-price data too, which is why the trial count
  is 381 rather than 164. That search happened and the count reflects it.

Longer versions in [docs/LIMITATIONS.md](docs/LIMITATIONS.md) and
[docs/METHODOLOGY.md](docs/METHODOLOGY.md).

## Not investment advice

A research exercise. It connects to no broker, places no orders, and its central
finding is that the strategies it studied do not beat simply holding the stock.
