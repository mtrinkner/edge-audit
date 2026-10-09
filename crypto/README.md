# Crypto: perpetual funding carry

Strategies 20 to 22 in the registry. Everything in the rest of this repo tried
to predict a price. The carry doesn't. A perpetual swap never expires, so the
exchange ties it to spot with a periodic funding payment: when the perp trades
above spot, longs pay shorts. Short perp against long spot is delta-neutral and
collects that payment as a contractual cash flow.

## Headline: a universe without survivors

All three tests first ran on today's 16 Hyperliquid volume leaders, which is a
universe of survivors. The headline numbers below come from a point-in-time
universe instead: each week, the top 20 listed perps by trailing 30-day
Hyperliquid volume, using only data from before that week, delisted coins
included. That's 109 coins over 178 weeks, 14 of them since delisted (FTT,
MATIC, OM, TON, ...), and 2.4M hourly funding prints. Basis comes from
Hyperliquid's own premium field, because delisted coins have no OKX spot. The
universe rule was fixed in the registry before anything was pulled.

| | 16 survivors | **Point-in-time** |
|---|---|---|
| 20: always-on carry, excess over T-bills | 4.0%, Sharpe 0.87 | **3.0%, Sharpe 0.51** |
| 21: switched on trailing funding | 5.9%, Sharpe 1.71 | **7.6%, Sharpe 1.92** |
| 22: funding as a crowding signal, IC (t) | +0.06 (2.15) | **+0.0001 (0.00)** |

Excess is per dollar of capital, after costs. Sharpes are monthly. The luck bar
is 3.17.

| Year | 20 always on | 21 switched |
|---|---|---|
| 2023 | -8.5% | +8.4% |
| 2024 | +15.8% | +15.5% |
| 2025 | +1.4% | +3.4% |
| 2026 | -2.0% | +1.9% |

- **Survivorship was flattering the carry.** On the wider universe, always-on
  pays 3.0% over T-bills, and 2026 is negative.
- **Survivorship had manufactured the crowding result.** Strategy 22's IC was
  zero once the coins that later disappeared were back in. Funding says nothing
  about next week's return in either direction.
- **The switched carry got stronger, and the gain is broad.** 62 of 109 coins
  contribute, the top five are 36% of it, and delisted coins add 0.4 points.
  Without the top five it's still 7.5% with a Sharpe of 1.94. A wider universe
  has more coins whose funding goes negative, and the rule's job is to stop
  paying on them. That makes it the best risk-adjusted result in this repo
  outside the forward test. It still doesn't clear the bar (deflated p 0.135),
  and its Sharpe is low-variance for the same reason strategy 20's daily Sharpe
  was 7: the volatility is the wobble in a cash flow, and the risk is a tail
  (squeeze, venue failure) that no series contains.

The precommitment said the point-in-time numbers replace the survivor numbers
whichever way they went. One went up, so it's reported going up. The sections
below are the original 16-coin results, kept as the record.

## Result (16 survivors)

| | |
|---|---|
| Median coin, annualized funding | 10.0% |
| ...of which Hyperliquid's fixed interest constant | **11.0%** |
| ...of which the market premium | **-0.9%** |
| Equal-weight book, after basis and costs | 11.2% |
| Per dollar of capital (spot leg + 3x perp margin) | 8.4% |
| **In excess of T-bills** | **4.0%** |
| Monthly Sharpe of the excess | 0.87 against a luck bar of 3.07, deflated p 0.024 |

| Year | Funding | Excess over T-bills, per dollar of capital |
|---|---|---|
| 2023 | 6.2% | -1.0% |
| 2024 | **23.2%** | **+12.3%** |
| 2025 | 7.4% | +1.4% |
| 2026 | 6.0% | +0.5% |

**The median coin's carry is a number the exchange typed in.** Hyperliquid
funding is `premium + clamp(interest - premium, ±0.05%)` per 8 hours, with
interest fixed at 0.01% per 8h, which comes to about 11% a year. When the perp
trades near the index, funding equals that constant exactly, and 54% of all
353,123 hourly prints sit on it. For the median coin the market's own
contribution is slightly negative. Shorts do get the cash, but the venue sets
the rate and can change it. It tells you nothing about demand for leverage.

**All of the excess return is 2024.** That was a year of real levered-long
demand, with a market leg of +12.2%. The other three years pay roughly what
T-bills pay, with venue risk on top.

## Switching it on and off (strategy 21, 16 survivors)

If all the excess is 2024, could a rule that only looks backward have known it
was in 2024? Funding is persistent, so I declared one before running it. For
each coin, hold the carry only while its trailing funding, scaled to capital,
beats the T-bill yield. Otherwise hold T-bills. Every switch pays the cost of
trading out of or into both legs. There were four variants, and the primary was
named in advance.

| | Excess over T-bills | Monthly Sharpe | Worst month | Time on |
|---|---|---|---|---|
| Always on (strategy 20) | 4.0% | 0.87 | -2.4% | 100% |
| **30-day lookback, weekly (primary)** | **5.9%** | **1.71** | -0.2% | 64% |
| 30-day, monthly | 5.4% | 1.60 | -0.3% | 63% |
| 7-day, weekly | 5.7% | 1.54 | -0.3% | 66% |
| 7-day, monthly | 5.5% | 1.55 | -0.3% | 65% |

The luck bar at 228 trials is 3.08. The primary's deflated p is 0.11.

It isn't lookahead. Letting the rule peek one week ahead gives 6.5%. Delaying
it 7, 14 and 30 days gives 5.3%, 4.8% and 4.2%, a smooth decay toward
always-on, which is what a real but modest persistence effect looks like.

**But the prediction was wrong about where the gain comes from.**

| Year | Always on | Switched |
|---|---|---|
| 2023 | -1.0% | **+8.9%** |
| 2024 | 12.3% | 11.6% |
| 2025 | 1.4% | 1.9% |
| 2026 | 0.5% | 1.1% |

The prediction was that the rule would sit out 2025 and 2026. It can't. The 11%
interest constant, scaled to capital, is 8.25% on its own, which clears T-bills,
so the rule stays on while the negative market leg bleeds the position down to
about 1%. The whole improvement is in 2023, and most of that is two coins: SUI
and AVAX went through long stretches of negative funding that cost the
always-on book 8.0% and 4.2%, and the switch sat them out. That's a handful of
episodes in a book of 5 to 7 coins, not a regime signal.

The lesson to keep: the switch is sensible risk hygiene (don't pay funding you
expected to receive), but it doesn't turn the carry into an edge.

## Funding as a crowding signal (strategy 22, 16 survivors)

The crypto-research story is that high funding means a crowded, fragile long
side, so high-funding coins should underperform. That predicts price, which is
a different claim from collecting funding. A book that shorts high-funding perps
also collects their funding, so the P&L is split. The claim stands or falls on
the price part.

| Weekly book | Price | Funding | Costs | Net | IC (t) |
|---|---|---|---|---|---|
| **Cross-sectional, 7-day (primary)** | **-88.0%** | +15.9% | -6.8% | -78.9% | +0.06 (2.15) |
| Cross-sectional, 30-day | -77.0% | +15.9% | -2.9% | -64.0% | +0.07 (2.03) |
| Time-series, 7-day | -21.6% | +9.4% | -1.9% | -14.1% | +0.06 (2.22) |
| Time-series, 30-day | -27.0% | +8.2% | -1.0% | -19.8% | +0.07 (2.17) |

Annualized, per $1 long and $1 short. The hypothesis predicted a negative IC.

**The sign is wrong.** High funding was followed by *higher* returns, and all
four variants lose. I'm not flipping it into "buy high funding":

- **77% of the loss comes from 10 weeks**, mostly the December 2023 to January
  2024 alt rally, when crowded coins kept rising. The worst week cost 49% of the
  book. That's the squeeze risk of shorting the crowd, which is exactly the side
  this bet took.
- **It fades as the survivorship bias is removed.** The universe is today's
  volume leaders, and many of them pumped while crowded. Dropping coins listed
  after mid-2024 gives t = 1.99. The 6 oldest coins alone give IC +0.045,
  t = 1.18, not significant.

Reversing the trade now would be a hypothesis chosen after seeing the answer,
tested on a universe built from winners.

## What the funding series cannot show

Measured on OKX daily perp and spot closes for the same 16 coins:

- **Basis at the daily close is tight.** Worst coin-day basis loss is -0.63%,
  and the worst book day is -0.32%. Basis blowouts, if they happen, happen
  intraday, which daily data cannot see.
- **Squeezes are routine.** A 30% three-day rally, enough to wipe the margin of
  an unrebalanced 3x short perp, happened in 13 of 16 coins, about 3.3 times per
  coin-year. The trade survives only if someone is moving collateral across legs
  around the clock.
- **Venue failure** is the risk that has actually ended this trade, and no
  series prices it.

## How the first pass got it wrong

The first version, committed in 820721d, said median 10.5%, daily Sharpe 7.11,
monthly 1.56, and 1.7% in 2026. It had three defects:

1. **No analysis script.** The numbers could not be reproduced. `funding_carry.py`
   is that script.
2. **Truncated data.** `post()` returned `[]` after three failed requests, and the
   pagination loop read that as the end of history. Rate limiting cut 9 of 16
   coins off at exact multiples of 500 rows (AVAX after 44 days, ZEC after 21).
   That is the same failure as the FINRA offset cap that killed short_v2. The
   ingest now raises instead, and warns when a series ends early. The re-pull
   has 353,123 prints, up from 233,170. The published 2026 figure of 1.7% was an
   artifact of this. The real figure is 6.0%: the carry decayed back to the
   interest constant, not to zero.
3. **Period length.** Hyperliquid settled every 8 hours until 2023-06-08.
   `mean(rate) * 24 * 365` treats those 410 prints as hourly. Funding is now
   summed as cash and divided by elapsed time.

A precommitment was written to the registry before any of this was computed:
the reproducible numbers replace the published ones, whichever way they go.

## Running it

```bash
python3 crypto/ingest_hl_funding.py --same-coins   # Hyperliquid hourly funding
python3 crypto/ingest_okx.py --basis               # OKX perp + spot closes, same coins
python3 crypto/ingest_tbill.py                     # 3-month T-bill (FRED DTB3)
python3 crypto/funding_carry.py                    # the analysis
python3 crypto/carry_switch.py                     # strategy 21, switched on trailing funding
python3 crypto/funding_crowding.py                 # strategy 22, funding as a crowding signal
python3 crypto/ingest_hl_universe.py               # point-in-time universe, ~1 hour, resumable
python3 crypto/pit_rerun.py                        # 20, 21, 22 on that universe (the headline)
```

The data the numbers come from is committed in `data/`.

| File | What it is |
|---|---|
| `hl_funding.parquet` | Hyperliquid funding, 16 coins, 2023-05 to 2026-10 |
| `okx_basis.parquet` | OKX daily perp and spot closes for those coins |
| `tbill.parquet` | FRED DTB3 |
| `hl_candles.parquet` | Hyperliquid daily perp candles, all 234 coins in meta, delisted included |
| `hl_universe.parquet` | weekly top 20 by trailing 30-day volume, point in time |
| `hl_funding_pit.parquet` | hourly funding for the 109 coins that ever made the top 20 |
| `okx_funding/perp/spot.parquet` | the original OKX pull: 100 days of funding, top 40 by coin count (see below) |

## Limits

- **Survivorship**, for the 16-coin sections. The headline fixes it as far as
  Hyperliquid's API allows. Coins removed from its metadata entirely, if any,
  are still missing, and so is anything that never listed there.
- **Cross-venue.** Funding comes from Hyperliquid and basis from OKX. A real
  position would hold both legs on venues it can move collateral between.
- **Costs.** Costs are a flat 8bp per leg, charged on entry and exit. Rebalancing
  to avoid liquidation would cost more.
- **The original OKX universe was ranked by 24h volume in coins, not dollars**,
  so it is mostly sub-penny meme coins with no BTC or ETH. That ranking is fixed
  in `ingest_okx.py`, but the pinned files were not re-pulled because nothing
  here depends on them.
