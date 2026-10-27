# Odd-lot tender offer arbitrage (trial 58)

**Result: no edge.** Declared 2026-10-26, run once on 2019-2025 with frozen
code (commit 8ac13fc), recorded FAIL.

## The idea

When a company buys back its own stock through a tender offer (an SEC Schedule
TO-I), it often promises to buy every share from holders of fewer than 100
shares, with no proration: the "odd-lot priority" clause. If the stock trades
below the eventual purchase price, buying 99 shares and tendering them earns
the gap. The trade is too small for institutions, which is why it might
survive.

## Method

**Deals.** Every SC TO-I and SC TO-I/A in the EDGAR quarterly form indexes,
2010 Q1 to 2026 Q3 (19,675 filings), with the 8-Ks filed while each offer was
open; many companies announce the results only in an 8-K.
`python/edgar_tenders.py` reads each Offer to Purchase and its amendments for:

- the security;
- fixed price or Dutch-auction range;
- shares or dollars sought;
- the odd-lot clause and its threshold;
- the expiration and every extension, with the date each became public;
- the outcome, final price, proration and payment date.

It keeps cash offers for a common or ordinary share: 504 offers. Notes,
preferred, warrants, option exchanges, NAV-priced funds and odd-lot-only offers
are dropped.

**Prices.** CRSP daily (`crsp.dsf_v2`) via WRDS. Each offer is linked by the
CUSIP on its Schedule TO cover, provided the CRSP issuer name on that date is
the filer's; otherwise by the Compustat-CCM link, then by ticker. 378 offers
have CRSP prices. The 127 without are listed in `reports/odd_lot_unmatched.csv`;
they are mostly non-traded REITs and BDCs, SPACs and OTC stocks.

**Validation.** Each field was checked by hand on three random samples of 30
offers. The first sample was used to tune the parser. Accuracy on the other two
(60 offers, each scored before the fixes it prompted):

| Field | Accuracy |
|---|---|
| Security, commencement, original expiration, outcome | 100% |
| Offer type and price, odd-lot priority | 98% |
| Final price, final expiration, extensions, threshold | 97% |
| Amount sought | 95% |
| Proration (not used in the profit) | 92% |
| Payment date (not used in the profit) | 93% |

Nine deals were checked by hand: every termination and every completed deal
with no final price. The five corrections are in `data/odd_lot_overrides.csv`.

**The trade, as declared.** Buy 99 shares at the close.

- **E1:** the first trading day after the filing.
- **E2:** the last feasible day. That is the expiration in force that day
  (only extensions already announced), minus the settlement cycle (T+3, then
  T+2 from 2017-09-05, then T+1 from 2024-05-28), minus 2 business days of
  broker cutoff.

Profit is (final price − entry) × 99 − $40 for a completed offer. A terminated
offer is sold at the close of the first trading day after the termination
announcement. Fees of $0 and $20 are sensitivity checks. The t-statistic is
clustered by entry month (`evaluation.clustered_mean`). Canadian-dollar offers
(Imperial Oil) are converted at FRED DEXCAUS.

**The bar.** Reject if any of these hold in the confirmation period:

- the mean profit at $40 is ≤ 0;
- t < 3;
- there are fewer than 10 tradeable deals a year on average;
- terminated-deal losses exceed half the gains.

## Results

At the $40 fee:

| | Design 2010-2018 | Confirmation 2019-2025 |
|---|---|---|
| Deals with odd-lot priority and CRSP prices | 169 (19 a year) | 82 (12 a year; 6 in 2024, 1 in 2025) |
| E1 mean / median per deal | +$48.42 / +$2.57 | +$16.73 / +$9.50 |
| E1 t | 3.06 | **0.17** |
| E2 mean per deal, t | +$1.75, t = 0.09 | −$80.21, t = −0.72 |
| E1 dollars per year per account | $909 | $196 |
| Terminations | none | 2 (MGM, JMP, March 2020): −$1,658 |

The confirmation column is the corrected run (see the Oppenheimer link error
below). The frozen run gave E1 +$51.57, t = 0.50, and E2 −$44.58, t = −0.38.
It is rejected either way.

**Why it fails.**

- **A few expensive stocks carry the mean.** White Mountains trades at
  $400-$1,400 a share, so a 2% gap on 99 shares is worth more than a whole
  year of ordinary deals. Without its five design-period offers, E1's design
  t falls to 2.29.
- **The median deal earns about the fee.** The stock usually trades close to
  the tender price.
- **One bad deal can wipe out a year.** Coca-Cola Consolidated 2024 traded
  above its $925 tender price and lost $5,601.
- **The profit sits in liquid stocks, not illiquid ones.** The deals carrying
  80% of the design profit trade a median $17M a day (all deals: $3.6M), and
  deals under $5M a day lose money.
- **The supply is shrinking.** Listed issuers with odd-lot priority went from
  about 20 offers a year to 1 in 2025.

## Limits and errors found

- **The parser can be wrong.** About 3% of key fields are wrong at the
  measured accuracy. Proration and payment date are less reliable but don't
  enter the profit.
- **Prices are closing prices.** There is no bid-ask spread or commission
  beyond the $40 tender fee, which flatters the result.
- **Coverage is CRSP's.** OTC stocks and non-traded REITs are out, and CRSP
  ends 2025-12-31.
- **Corrections made after the design results were seen.** Each was verified
  against the filing:
  - linking by cover CUSIP, because CIK → gvkey had picked merger successors
    and FNF instead of the FNFV tracking stock;
  - a split adjustment that ran backwards (Herbalife);
  - Casey's 2010, where the parser took Couche-Tard's $45 bid instead of the
    $38 tender price.
- **Oppenheimer link error, found after the confirmation run.** Oppenheimer's
  2023 Schedule TO gives a CUSIP that CRSP assigns to its SPAC, OPY
  Acquisition Corp I. The frozen run therefore entered at $10.22 instead of
  $39.08. Links now also require the CRSP issuer name to match (`same_issuer`);
  that check also caught American HomePatient, which was linked to Watson
  Pharmaceuticals and was not in the study. Corrected outputs are in
  `reports/odd_lot_*_corrected_2026-10-26.csv`.

## Reproduce

```
python3 python/edgar_tenders.py index      # SC TO-I filings (SEC, cached in data/edgar/)
python3 python/edgar_tenders.py link       # CIK -> CRSP (WRDS)
python3 python/edgar_tenders.py download   # the filings
python3 python/edgar_tenders.py parse
python3 python/edgar_tenders.py eightk     # results 8-Ks, then parse again
python3 python/edgar_tenders.py prices     # CRSP daily rows (WRDS, git-ignored)
python3 python/odd_lot_study.py            # design
python3 python/odd_lot_study.py --confirm  # confirmation
```

EDGAR requests stay under 8 a second, with a contact User-Agent. CRSP data is
licensed and stays in the git-ignored `data/edgar/`.
