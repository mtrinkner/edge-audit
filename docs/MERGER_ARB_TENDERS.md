# Cash tender-offer merger arbitrage (proposed trial 60)

**Result: killed at the pre-declaration gate, 2026-10-28. Nothing registered;
no trial spent.**

## The idea

A third-party cash tender offer (SEC Schedule TO-T) is a contract: a bidder
offers a fixed cash price for every share, usually paid within one to three
months. After the announcement the target trades a little below that price.
Buying it and tendering earns the gap if the deal closes and loses a large part
of the takeover premium if it breaks. Trial 58 showed that a contractual payoff
is real when the entry is below the guaranteed price (32 of 32 odd-lot deals
made money) but its 99-share cap held it to about $200 a year. Tender offers
remove the cap and add break risk, which rises in market crashes.

## The gate (docs/METHODOLOGY.md, "Pre-declaration gate")

Check (b), the pass probability, needs no deal data, so it was run first.

### b. Pass probability: KILL

The prior is the after-cost Sharpe of a merger-arbitrage portfolio in excess of
T-bills, cut for the post-2000 shrinkage of spreads.

- **Prior, before decay.** Mitchell and Pulvino (2001), "Characteristics of
  risk and return in risk arbitrage", *Journal of Finance* 56(6), 2135-2175,
  Table II. Their index-manager portfolio (RAIM), 4,750 cash and stock deals
  from 1963 to 1998, includes transaction costs and a 10% position cap. It
  returned 10.64% a year against 6.22% for T-bills, with 7.74% volatility:
  **Sharpe 0.57.** (Ignoring costs, their value-weighted portfolio showed 1.06.)
- **Decay.** Jetley and Ji (2010), "The shrinking merger arbitrage spread:
  reasons and implications", *Financial Analysts Journal* 66(2), 54-68. The
  median first-day spread fell from 4.10-7.94% in the years before 2001 to
  1.74-2.63% after, and merger-arbitrage fund alpha fell by about 4.8 points a
  year after 2002. About 37% of the spread is left (2.2% against 6.0% at the
  midpoints). Break losses depend on the takeover premium, not the spread, so
  volatility does not shrink with it. Scaling the 4.4%/yr excess return by
  0.37 gives **Sharpe 0.21.** McLean and Pontiff's (2016) average 58%
  post-publication decline gives 0.24.
- **Concentration.** Neither figure allows for this portfolio's 20% position
  cap (at most 5 deals at once on $25k). That is twice RAIM's 10% cap, which
  raises volatility and lowers the Sharpe further.

`python3 python/deflated_sharpe.py --power --prior-sharpe S --years 13`:

| Prior Sharpe | Source | P(t ≥ 3 in 13 years) |
|---|---|---|
| 0.57 | Mitchell-Pulvino, no decay (upper bound) | 0.172 |
| 0.24 | the same, with McLean-Pontiff decay | 0.016 |
| 0.21 | the same, scaled by the Jetley-Ji spread decline | 0.012 |
| 0.65 | the Sharpe needed to reach P = 0.25 | 0.256 |

The kill line is P(pass) < 0.25. **Even with no decay at all, the best cited
after-cost academic prior fails it.**

Hedge-fund indices report more. The HFRI Merger Arbitrage Index is often
quoted at a Sharpe of about 1.25 for 1990-2016. It was not used as the prior,
for four reasons:

- It is self-reported, with backfill and survivorship bias.
- Its monthly marks are smoothed, which understates volatility.
- Its benchmark is not stated as T-bills.
- Most of its history is the pre-2002 wide-spread era that Jetley and Ji
  measure the decline from.

### a. Economic size: not computed from deal data (preview from the literature)

The gate's dollar figure needs entry-day CRSP prices, spreads and volume for
every in-scope offer: the full parser plus two WRDS pulls. That work was not
done after the kill. A preview from the literature alone, per deal, with a
$5,000 position (20% of $25k):

    net = $5,000 × (spread − 0.1% half-spread − break rate × loss given break) − $40 fee

| Break rate / loss given break | Spread 1.74% | 2.22% | 2.63% |
|---|---|---|---|
| 7% / 25% (base) | −$46 | −$22 | −$1 |
| 5% / 15% | +$4 | +$28 | +$49 |
| 10% / 35% | −$133 | −$109 | −$88 |

- The spreads are Jetley and Ji's post-2001 median first-day range for all
  deals, not cash tender offers alone.
- The break-rate and loss priors are the ones you specified. Mitchell and
  Pulvino report a 5.0% monthly fail-to-active ratio. In their probit, a 5%
  market fall raises the failure probability by 2.25 points, and a hostile
  bid raises it by 12.8 points.
- **In the base case the expected net per deal is negative at every
  post-2001 median spread.** The $40 fee alone is 0.8% of a $5,000 position.
  That points to a second kill on check (c), the median net spread ≤ 0, but it
  is not confirmed on deal data.

### c. Benchmark fit

The proposed benchmark was T-bills, the portfolio's own cash alternative. It
fits; nothing to fix.

## What was built anyway

- `python/edgar_tenders.py` now takes the form family as a parameter
  (`index --family "SC TO-T"`), with the TO-I paths unchanged.
  - Trial 58's filing list, `deals.csv` and both odd-lot deal sets rebuild
    byte-identically.
  - Tests: `tests/test_edgar_family.py`.
- The SC TO-T family index is cached in `data/edgar/index_tot/` and
  `filings_tot.csv` (git-ignored). It covers 2001Q1-2026Q3 and keeps SC TO-T,
  SC TO-T/A, SC 14D9 and its amendments, SC 13E3 and its amendments, and
  SC TO-C.
  - 39,458 filings, deduplicated by accession number (each SC TO-T is listed
    under the bidder and the subject company).
  - 2,469 original SC TO-T offers, 1,566 of them in 2001-2012. Many are mini
    tenders for limited-partnership units and would fall outside the scope.
  - Original offers a year: 237 in 2001, about 100 in 2004-2012, 44-55 in
    2019-2025.
- Not done: the TO-T offer parser, CRSP links, hand validation and the
  merger_arb_tenders.py study. None of it is needed unless the gate is
  reopened with a better-supported prior.

## What would reopen it

A cited, post-2000, after-cost Sharpe of at least 0.65 for a portfolio of cash
tender offers built the way this one would be, in excess of T-bills. Or a
change to the gate, which is your call, not mine.
