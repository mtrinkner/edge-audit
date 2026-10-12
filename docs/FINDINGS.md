# Findings in detail

The full account of every candidate this project tested and how each one died.
The short version is in the [README](../README.md); this is the long one, kept
because the way a result dies is the part worth reading.

---

## Testing a mechanism instead of a pattern

Every candidate so far was a *pattern*: momentum, earnings surprise, short
interest. The r/algotrading framing that reads best is that an edge should be *"a reason
someone pays you"* — a participant who is structurally forced to transact at a
bad price. So I tested one: when a stock joins the S&P 500, index funds must buy
it on the effective date regardless of price.

The headline result looked excellent and was almost entirely an artifact:

| Window around the effective date | Abnormal return | t |
|---|---|---|
| -250 to -126 sessions | **+17.53%** | 7.14 |
| -125 to -61 | +11.37% | 6.42 |
| -60 to -21 | +10.61% | 5.19 |
| -20 to -6 | +3.02% | 4.21 |
| -5 to -1 | +1.36% | 3.25 |
| **0 to +5** | **-0.79%** | **-2.09** |

A forced buyer would move the price in the days around the event. This run-up
stretches back a full year, which is not index funds — it is the selection rule.
S&P adds companies *because* they already grew 17% over the prior year.

The last row is the part a forced buyer could actually explain: a reversal once
the buying stops. Shorting each addition for ten days after the effective date,
market-adjusted and net of costs:

| | |
|---|---|
| Mean per event | **+1.20%** |
| t-statistic | **2.18** |
| Event dates | 148 over 12.7 years |
| Share positive | 55% |
| Sharpe | 0.61 |
| Luck bar at 218 trials | 1.63 |
| Deflated probability | **0.041** |

It does not decay across the sample — 2014-2017 +0.44%, 2018-2021 +1.47%,
2022-2026 +1.80% — which is unusual for a published effect and the opposite of
what the literature predicts. It still fails the bar.

Two caveats that matter more than the t-statistic. The sample is only additions
*still in the index today*, so it is survivorship-biased, though plausibly against
the short rather than for it. And 12 events a year is not a business.

### The symmetric test does not confirm it

Deletions are the mirror experiment: index funds are forced to *sell*. Using
historical constituent data (fja05680/sp500) there are 291 deletions in the
window, but only 101 have usable bars, because the rest were acquired and the
data vendor keeps nothing for delisted tickers. That exclusion is less damaging
than it sounds — an acquired company's price is pinned to the deal terms, so
there is no pressure reversal to measure either way.

The pre-event side mirrors additions exactly, which confirms the selection story
from the other direction:

| Window | Additions | Deletions |
|---|---|---|
| -250 to -126 | +17.53% | **-15.93%** |
| -125 to -61 | +11.37% | -13.04% |
| -60 to -21 | +10.61% | -9.43% |
| **0 to +5** | **-0.79% (t = -2.09)** | **+0.59% (t = 0.67)** |

Same sign as predicted, half the significance. Combining both into one
market-neutral event book — short additions, long deletions, 10-day hold — gives
0.82% per event, t = 1.65, Sharpe 0.46 against a luck bar of 1.55 and a deflated
probability of 0.025.

So the addition reversal does not survive its own symmetric test. It may be real
and underpowered at 101 deletion events, or the addition result may be the 218th
thing I tried. The honest position is that one leg clearing t = 2 while the
mirror leg does not is weak evidence, not a finding.

## The closest thing to an edge, and why it still is not one

Having exhausted daily US equities, I went to a market with an explicit
mechanism: perpetual swap funding. Short perp against long spot is
delta-neutral and collects the funding payment as a contractual cash flow. Full
write-up, code and data now live in their own repo,
[crypto-funding](https://github.com/mtrinkner/crypto-funding), which carries
this registry's trial count forward.

On a point-in-time universe (each week's top 20 Hyperliquid perps by volume,
109 coins, 14 since delisted):

| | Excess over T-bills | Monthly Sharpe |
|---|---|---|
| Always-on carry | 3.0% | 0.51 |
| **Switched: hold a coin only while its trailing funding beats T-bills** | **7.6%** | **1.92** |
| Luck bar at 232 trials | | 3.17 |

**Out of sample it fails.** On 94 coins that played no part in forming the
idea, the switched carry scores 1.70 before liquidations, so the signal
generalizes. But a 3x short perp managed at the daily close is liquidated 3.1
times per coin-year, and with a 5% cost per liquidation the Sharpe is -0.24.
Cutting leverage to 2x is the only fix that worked (1.42). Volatility sizing
and an on-chain stablecoin regime gate both made it worse. The excess also
decays, from 12.1% in 2023-24 to 1.9% in 2025-26.

The median coin's carry is mostly a parameter Hyperliquid sets: an 11% interest
constant, with a market premium of about zero on top. The switched version is
the strongest risk-adjusted result outside the forward test. It's positive in
every year and broad across coins, but it still doesn't clear the bar, and its
low volatility is the wobble of a cash flow, not the risk of a trade whose real
tail is a squeeze or a venue failing.

Using funding as a crowding signal that predicts lower prices is nothing: an
IC of 0.0001 once the coins that later disappeared are put back.

Three bugs were found on the way, each of which had produced a published
number. A rate-limited ingest silently truncated 9 of 16 coins (the same
failure that killed short_v2), 8-hourly prints were annualized as hourly, and a
universe of today's survivors had manufactured a crowding signal with t = 2.15.


### Why breadth was the wrong lever

Thirteen price signals turned out to be **2.9 effective independent strategies**,
because they all transform one series. The fundamental law says
IR = IC x sqrt(breadth), so I went looking for independent data.

| Sources | Nominal | Effective | Avg correlation |
|---|---|---|---|
| Price only | 13 | 2.9 | +0.19 |
| Price + earnings | 19 | 5.0 | +0.11 |
| Earnings + fundamentals | 12 | **7.0** | **+0.04** |

Breadth more than doubled. Sharpe did not improve, because the added sleeves were
negative. **More independent bets do not help when the new bets are bad bets.**
The law has two terms and I had been treating breadth as the binding one.

### Breadth, measured instead of assumed

The section above counted breadth across *signals*. It never counted breadth
across *names*, which is the term that actually appears in the law. So I measured
it: the effective number of independent bets is the participation ratio of the
return correlation eigenvalues,

    N_eff = (sum of eigenvalues)^2 / sum of (eigenvalues squared)

which equals N when names move independently and 1 when they move as one. Because
the eigenvalues of a correlation matrix sum to N, this reduces to N^2 / sum(l^2),
so one large market eigenvalue caps the whole thing no matter how many tickers
get added. Measured on the 814 names with complete history over 3,190 sessions:

| Universe N | N_eff, raw returns | N_eff, sector-neutral | as % of N |
|---|---|---|---|
| 50 | 6.9 | 38.4 | 76.8% |
| 100 | 7.5 | 48.4 | 48.4% |
| 200 | 7.9 | 69.9 | 34.9% |
| 400 | 8.1 | 91.0 | 22.7% |
| 800 | 8.3 | **100.2** | 12.5% |

**An 800-name long-only book is 8 bets.** Raw breadth saturates almost
immediately: going from 50 names to 800 moves it from 6.9 to 8.3. Everything
beyond the first handful of names is buying the same market exposure again.

Neutralization is the lever, and it is worth about **12x** (8.3 to 100). Adding
750 names is worth about **1.2x**, and the marginal return collapses as it goes:
the jump from 400 to 800 names bought 10% more effective breadth. That is the
quantitative version of a thing that gets asserted constantly and almost never
measured. Factor neutralization is usually sold as risk control. Its larger job
is manufacturing breadth, and the cross-section is close to worthless without it.

Then the obvious question: with breadth of 100 names times 12 rebalances a year,
does anything here monetize it? No, and the reasons are specific.

**The residualization works, which is why the result can be believed.** Each
signal was regressed daily on beta, size, two momentum horizons, short-term
reversal, volatility and sector. The variance it removed sorts exactly the way it
should: 5-day reversal lost **92.6%**, low volatility 89.0%, 126-day momentum
88.7%. The price scores largely *are* the factors. The non-price signals lost
almost nothing: insider buying 1.6%, short interest change 1.6%, ROE 2.0%,
standardized earnings surprise 2.2%. A regression that failed to absorb momentum
would have invalidated the whole table, so this is the control, not a footnote.

**Nothing survives the trial count.** 64 variants in one run. Benjamini-Hochberg
at FDR 5%: **0 of 64**. The expected maximum |t| from 64 pure-noise tests is
2.42, and only one signal beat it (52-week high, residualized, t=2.94), which
then fails BH anyway. Best net Sharpe in the entire table is **0.18**. Eight of
64 are positive at all, zero clear 0.5.

**Costs are larger than the entire edge.** Median cost drag is **0.36 Sharpe
units**. The largest predicted IR anywhere in the table is 0.62, and the median
is far below the drag. At breadth 1,200 the IC needed just to pay for the trading
is 0.0104, and the best residualized IC measured is 0.0179, which clears it only
on paper: that signal's autocorrelation is 0.86, so its real independent breadth
is nearer 89 than 1,200, and at that breadth it does not clear costs. Its
realized net Sharpe is 0.00.

**The law itself held up.** Median absolute gap between predicted IR and realized
*gross* Sharpe across all 64 variants is **0.151**. IR = IC x sqrt(breadth) is a
decent predictor on this data. It just predicts a number too small to survive
costs. The constraint was never the formula, it was that costs scale with
turnover while Sharpe scales with its square root.

One thing that did not work: deflating breadth by signal persistence, using the
AR(1) variance ratio (1-rho)/(1+rho), was supposed to close the predicted-versus-
realized gap. It made the median fit slightly worse, 0.151 to 0.157. It explains
the 52-week-high row specifically and fails as a general correction, and it is
recorded here rather than dropped because it was a declared expectation.

Registered as trial 23 with 64 variants **before** it ran. Trial count 232 to
**296**, which raised the luck bar on everything above.

## Federal contract awards: the sixth data source, and a clean null

The one category left untested was data that is not derived from price at all.
Every signal that died in this project died the same way, residualising into
beta, size and momentum, and the breadth work above measured exactly how much:
5-day reversal lost 92.6% of its variance to the factor set, momentum 88.7%. The
non-price signals lost almost nothing. So the last idea worth testing was a
dated, public, non-price event with an obvious cash-flow channel.

**USAspending, transaction level, 101,986 contract actions above $10M from
2014-01 to 2026-09, $4,798bn obligated.** Declared as trial 26 with 32 variants,
including both shape predictions, before a single return was computed.

### Four traps, found before the result

**Award-level amounts are cumulative.** A 2015 query returned a Lockheed award
with a 1993 start date and a **$48bn** total, because `Award Amount` sums every
modification over an award's life. Keying an event study on that imports the
future into a 2015 decision. The transaction endpoint gives one row per action
with its own date and obligation, which is point-in-time by construction.

**Recipient names are retroactively modernised.** "RTX CORPORATION" appears on
2015 rows, for an entity that did not exist until the 2020 merger and was not
named RTX until 2023. Which company it actually was is settled by the award
numbers: the 2015 "RTX" awards are N00019 (NAVAIR) and FA8611 engine contracts,
so Pratt & Whitney, so United Technologies. The "RAYTHEON COMPANY" rows are
HQ0276 (Missile Defense) and N00024, so missiles, so the real Raytheon.

**Price series follow the acquirer, not the target.** Checked rather than
assumed: RTX closed at **$81.94** in mid-2019, which is neither UTX (~$130) nor
RTN (~$175). It is UTX scaled by the Carrier/Otis spinoff ratio, and $49.93 on
2020-04-03 is merger-completion day. LHX at $144.54 in June 2018 matches Harris
exactly. So RTX bars *are* United Technologies and LHX bars *are* Harris, which
makes those rows priceable and makes Raytheon and L3 rows unpriceable.

**The feed contains enormous source errors.** A 2015 row credits HENSEL PHELPS
CONSTRUCTION CO with **$92.5bn** across six actions, against roughly $440bn of
total federal contract obligations that year, for a private builder. Sixteen
actions above $5bn are flagged rather than deleted, because a silently dropped
row makes a sample stop matching its own description. Three of them are Lockheed
F-35 lot awards that are probably real, which is exactly why the flag is a flag.

Also checked rather than assumed: SpaceX is **not** private. It listed on
2026-06-12 as SPCX. Every one of its contract actions here predates that, and no
SPCX bars exist, so the rows are unpriceable for a different reason than the one
I would have written down.

### What the two declared tests said

| | Prediction | Result |
|---|---|---|
| **1. Pre-event window** | flat before the action date | **+0.37%**, post-event **-0.01%** |
| **1b. vs placebo** | n/a | placebo **+1.58%**, so event minus placebo is **-1.21%** |
| **2. Materiality scaling** | positive and monotone | rank corr **+0.024**, non-monotone |

The placebo is the control that makes this readable, and it is the reason the
first answer was wrong. Measured on 2015 alone the pre-event CAR was **+12.66%**
against a placebo of +1.03%, which looked like textbook anticipation: public
solicitations, run-up before award, selection rather than drift. At the full
26,643 events it collapses to +0.37% against a placebo of **+1.58%**. The
placebo is *higher* than the real dates. The 2015 result was 318 events from a
handful of defense names during a defense rally, and reporting it would have been
a confident claim about noise.

Implied book, long the top materiality quintile each month, market-adjusted,
one observation per month so nothing overlaps: **Sharpe 0.159, t = 0.57** over
152 months. Deflated probability it is real, at 340 registered trials: **0.006**.

### Limitations that are not fixable by better code

**Only 27.3% of actions are priceable, 40.8% of dollars.** Most federal contract
money goes to entities that cannot be traded: private firms, joint ventures,
FFRDC consortium LLCs, universities. Electric Boat and Bath Iron Works map to
General Dynamics and Optum to UnitedHealth, but Hensel Phelps, TriWest, General
Atomics and the national-laboratory LLCs do not map to anything.

**Survivorship runs one direction.** $172.8bn of Raytheon actions are dropped
because RTN has no price series, and the price database was built from CURRENT
index constituents. Dropping acquired companies removes exactly the firms that
became targets, which is not random with respect to performance.

**Materiality is weakest where it should be strongest.** 17 of 26 tickers have
point-in-time share counts. The missing ones are the small and mid caps, which is
precisely where award-over-market-cap would be largest and the mechanism most
visible.

Trial count 296 to **340**.

### Why small caps were the wrong lever

Reg SHO gives the cleanest causal evidence that anomalies live where arbitrage is
costly: lifting short-sale constraints cut long-short returns 94bp for small
stocks against 48bp for large. So I expanded to 1,515 names and rebuilt the cost
model first, since testing small caps on large-cap costs would have fabricated the
answer.

| Bucket | Gross Sharpe | Net Sharpe | Cost drag |
|---|---|---|---|
| **Large** | **0.71** | **0.65** | 0.41%/yr |
| Mid | 0.41 | 0.28 | 0.90%/yr |
| Small | 0.31 | 0.08 | 1.48%/yr |

Drift declines monotonically with size, and costs triple going down. Small caps
lose on both terms at once. My guess at why: PEAD needs the consensus estimate to
mean something, and small-cap consensus is three analysts rather than thirty.

### Why better portfolio construction did not help

The signal was held fixed and only the map from signal to positions changed:
quintile sort, signal weighting, risk-adjusted weighting, and factor-neutral
weighting. All three alternatives lost to the plain quintile sort.

| Construction | Names held | Gross Sharpe | Net Sharpe | Cost drag |
|---|---|---|---|---|
| **Quintile, equal weight** | 546 | **0.56** | **0.39** | 0.48%/yr |
| Signal weighted | 1,346 | 0.20 | 0.09 | 0.50%/yr |
| Risk adjusted | 1,346 | 0.16 | 0.02 | 0.46%/yr |
| Factor neutral + risk | 1,340 | -0.00 | -0.16 | 0.47%/yr |

Cost drag is flat across all four, so trading more was not the explanation. The
gross column is: spreading weight across the whole cross-section dilutes the
signal, because only the extremes carry information. And the factor-neutral book
goes to exactly zero gross, which is what sent me looking at attribution.

### Verifying the earnings data was point-in-time

The best result rests on surprise figures whose estimate timing the vendor does
not document. If those were revised after the fact, the signal is contaminated.
Four tests on 24,028 announcements:

| Test | Result |
|---|---|
| Surprise predicts the announcement-day move | +0.203, monotonic across quintiles |
| Day-0 return spread, worst to best quintile | +4.16 points |
| 60-day drift spread (this is PEAD) | +2.50 points |
| Volatility on announcement day | 3.11x a normal day |
| Median absolute surprise | 6.82%, only 9.8% within 1% |

Estimates revised toward the actual would cluster near zero and could not produce
a clean monotonic same-day reaction. One caveat stands: 78.4% of announcements
beat, against 60-70% in the literature, which I attribute to a universe of
current index members.

## Bugs this found in my own work

Left in the history on purpose, because they are the argument for building any of
this.

- **A false discovery, caught by the false-discovery detector.** My first signal-zoo
  summary reported the top result as 99.2% likely real. It had **six** independent
  observations; I had fed the project's sample size into the correction instead of
  each signal's own. The real number was 3.4%.
- **Costs that cancelled themselves.** A long/short book pays on both legs, but I
  subtracted each leg's cost and then differenced them, reporting a drag of 0.00%
  on a book trading small caps at 7.7bp.
- **A "market-neutral" book that was not.** Sector-neutral does not imply
  beta-neutral. The best book carried +0.079 of market beta, and hedging it out
  cut the Sharpe from 0.52 to 0.36. Part of what looked like alpha was the market.
- **A 100% drawdown that was not.** Compounding overlapping 63-day returns as if
  sequential multiplies the same stretch of market 63 times. Real figure: 9.9%.
- **A stale table.** The SQL feature layer still had 67 symbols after the universe
  grew to 515. The cross-sectional work reads `bars` directly so results stood, but
  I only know that because I checked.
- **An estimator that did not measure what I needed.** Corwin-Schultz gives AAPL
  23bp against a real spread near 1bp. Thrown out rather than scaled.

## Short interest: the one that got away

Short interest was the only signal whose return *grew* under factor
neutralization — 0.51 as measured, 1.66 residualized, t = 3.90. It survived every
robustness check: dropping the 2021 meme-squeeze era raised it to 1.69, starting
from mid-2022 raised it to 2.03, the best five dates were 2% of the return, and
all six years were positive.

Then I went looking for more observations and found the API returns data back to
2017-12-29, not 2021-03. My ingest had walked an offset parameter forward until
the API stopped returning rows; the API caps offsets, so it halted early and
silently. I had written that truncation into this README as a fact about FINRA's
retention policy.

Before pulling the longer history I recorded a pre-commitment in the ledger:
whatever the full sample says is the answer, no keeping the better window.

| | 2021-2026 | 2018-2026 |
|---|---|---|
| Independent observations | 22 | **34** |
| Sharpe | 1.66 | **0.24** |
| t-statistic | 3.90 | **0.69** |
| Max drawdown | 1.53% | **8.71%** |

2018 was -0.44, 2019 +0.95, 2020 -0.54. The entire result lived in the window I
happened to have. `short_v2` is abandoned; its manifest is kept and marked
REFUTED so the record shows what was frozen, what it claimed, and how it died.

**The structural lesson outlives the result.** Dropping 2021 from a sample that
*begins* in 2021 only moves the window to 2022-2026, which was the good stretch.
A robustness check performed inside a truncated sample cannot detect that the
truncation is the problem. More checking would never have found this. More data
did.

