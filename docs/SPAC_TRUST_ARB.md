# SPAC trust-value arbitrage (proposed trial 60)

**Result: killed at the pre-declaration gate, 2026-10-29. Nothing registered;
no trial spent.**

On a $25,000 account, the declared portfolio would have earned about **$186 a
year over T-bills** in the design period, 2010-2020. The gate needs $1,500.
The most generous single change, a $0 redemption fee, gives $326. The trade
works: positions are entered at a median 1.5% below trust value and redeemed
at it. But the discount is earned once per holding period of a year or so, on
capital that is invested about half the time.

## The idea

A SPAC holds its IPO cash in a trust invested in T-bills. Every public
shareholder can redeem at the pro-rata trust value at each business-combination
vote, at each extension vote, and at liquidation. A share bought below that
value has a contractual floor. Its return over T-bills is the discount, plus
the option to sell above trust after a deal announcement, minus costs.

## Data

### Universe

- **Search.** EDGAR full-text search for IPO prospectuses (424B4, 424B1)
  mentioning a trust account and a business combination, filed 2010-2025.
- **Filter.** Kept only filers whose SIC on the submission's own header is
  6770 (blank checks). The SIC at filing time is used because a merged SPAC's
  current SIC is the operating company's.
- **Result.** 1,366 SPACs, one IPO each. That matches published annual counts
  closely (2019: 56, 2020: 245, 2021: 594). Code: `python/spac_edgar.py`.

### Coverage

`reports/spac_coverage.csv`:

| IPO years | Found in EDGAR | Linked to CRSP | Trust per share parsed | XBRL trust history |
|---|---|---|---|---|
| 2010-2014 | 50 | 32 | 43 | 27 |
| 2015-2020 | 413 | 377 | 396 | 369 |
| 2021-2025 | 903 | 774 | 884 | 863 |
| Total | 1,366 | 1,183 | 1,323 | 1,259 |

### Linking to CRSP

- **Primary link.** The prospectus's common symbol, on a CRSP ordinary common
  share listed within 400 days, whose CRSP issuer name is the SPAC's
  (`edgar_tenders.same_issuer`). Tickers are recycled, so the symbol alone
  never decides.
- **Fallback.** CIK → Compustat → CCM, with the same name check. 1,172 SPACs
  link by ticker and 11 by CCM.
- **Units and warrants.** CRSP carries no SPAC units, warrants or rights.
  Checked: no equity security of these issuers is named as one. So the common
  share is the only permno.
- **The 183 unlinked.** Mostly 2010-2012 OTC listings, 2025 IPOs still
  trading as units at the end of CRSP, and tickers parsed wrongly: a sponsor's
  earlier SPAC named in the prospectus, or a unit symbol that isn't the common
  ticker plus "U". Fixing the last group needs a second WRDS pull. That wasn't
  worth it: the design period is already 88% covered (409 of 463), and the result is
  far from the line.

### Prices

CRSP `dsf_v2`, through 2025-12-31 (one WRDS connection):

- the close, or the quote midpoint on zero-volume days;
- the bid and ask;
- the dollar volume.

### Trust value per share known on day t (E_t)

Every value is usable from the day **after** its filing date, never from its
balance-sheet date. In order of preference:

1. The XBRL redemption price per share (`TemporaryEquityRedemptionPricePerShare`).
2. Trust balance (`AssetsHeldInTrust*`) divided by
   `TemporaryEquitySharesOutstanding`, for balance dates from 2021-06-30 on.
   Before the SEC's April 2021 statement, SPACs carried only part of their
   public shares as temporary equity, so the ratio overstates the per-share
   value.
3. Otherwise, the IPO trust per share from the prospectus.

Each is accrued at the 3-month T-bill rate (FRED DTB3) from its basis date. In
the design period, 76% of SPAC-days use the IPO accrual and 24% use XBRL.

Checked against the XBRL redemption price where both exist, the accrual model's
error is a median +$0.007 a share with a median absolute miss of $0.03. The
10th and 90th percentiles are −$0.03 and +$0.21 (424 filings, 104 SPACs, design
period). It errs high, because trusts pay tax out of their interest. A
too-high E_t creates discounts that aren't there, so it biases this gate
toward passing.

### Redemption opportunities

- **Votes.** 8-K item 5.07 dates (shareholder votes), from the SEC
  submissions data. 4,299 vote 8-Ks across 1,057 SPACs.
- **End of the SPAC phase.** The earliest of:
  - the completed-acquisition 8-K (item 2.01);
  - the first CRSP ticker or name change, which is when the permno has
    continued as the operating company;
  - the final vote, when the permno stops trading within 15 trading days of
    it (a merger into a new holding company).
- **Liquidations.** A SPAC that just stops trading is taken to liquidate on
  its last CRSP day.
- **Deadlines.** The redemption deadline is 2 business days before the
  meeting. Payment is 5 business days after it.

### Traps handled (`tests/test_spac.py`)

- **Rename after a merger.** The permno continues as the operating company, so
  the SPAC phase ends at the first ticker or name change. `same_issuer` is too
  loose for this: it matched New Frontier Corp to New Frontier Health Corp, and
  the first gate run counted a year of operating-company prices around $8.77
  as SPAC days.
- **Final deadline.** Nothing is entered after the final redemption deadline.
- **Unparsed trust values.** A SPAC without a parsed trust value is skipped,
  never assigned $10. Universal Business Payment Solutions sold $6 units in
  2011.
- **Filing-date availability.** XBRL values are usable only after their
  filing date.
- **Comparative balances.** Each filing's own balance-sheet date is used, not a
  prior-period comparative it repeats.
- **Suspect days.** A day more than 10% below E_t is excluded and counted
  (291 days, 63 SPACs). In this data it means a redemption deadline that had
  already passed (an adjourned meeting, or a vote date read from the post-vote
  8-K) or a stale quote.

**Not built.** The proxy-stated redemption amount, extension terms and
contributions, and paid amounts; the hand validation of 50 SPACs; and
outcome cross-checks. None is needed for a kill that does not depend on
outcomes.

## The gate

### a. Economic size: KILL

The portfolio as specified for Phase 3:

- **Account and sizing.** $25k, at most 10% per SPAC, and at most 25% of the
  20-day median dollar volume.
- **Entry.** Decide after the close of day t on a net discount of at least
  0.5%. Buy at the close of t+1, which must have traded.
- **Order.** First come first served; ties go to the larger discount. Entries
  are skipped when the full position doesn't fit.
- **Exit.** Redeemed at E (accrued) on the next opportunity: no deal pop, and
  positions are valued at what redemption would pay.
- **Idle cash.** Earns T-bills (Fama-French daily rf).

Costs:

- half the quoted spread;
- the redemption fee: E\*TRADE's $38 voluntary-action fee is primary;
  Fidelity's $0 voluntary-reorganization fee is a sensitivity row;
- a trust-shortfall haircut of $0.02 a share (an **assumption**), with
  sensitivities at $0 and $0.05.

Design 2010-2020 (`reports/spac_gate_design.csv`):

| Fee | Haircut | Threshold | Excess $/yr over T-bills | Entries | Days fully invested | Capacity-limited entries | Median net discount at entry |
|---|---|---|---|---|---|---|---|
| **$38** | **$0.02** | **0.5%** | **$186** | 75 | 35% | 31% | 1.47% |
| $38 | $0.02 | 0% | $100 | 90 | 39% | 48% | 0.84% |
| $38 | $0.02 | 1% | $227 | 66 | 35% | 26% | 1.80% |
| $38 | $0 | 0.5% | $195 | 79 | 36% | 33% | 1.39% |
| $38 | $0.05 | 0.5% | $152 | 72 | 35% | 28% | 1.31% |
| $0 | $0.02 | 0.5% | $326 | 210 | 34% | 81% | 2.14% |

**Primary row by year:**

| Year | 2010 | 2011 | 2012 | 2013 | 2014 | 2015 | 2016 | 2017 | 2018 | 2019 | 2020 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Excess $ | 0 | −6 | −22 | 0 | 437 | 179 | 148 | 568 | 374 | 64 | 306 |

**Median net discount.**
- Over all design SPAC-days: −2.1%.
- Over the days below E_t: −1.0%.
- At entry: +1.5%, which is positive by construction because of the
  threshold.

So the typical SPAC-day is not a trade at all after costs.

**Why so little.**
- A position earns its discount once. Most entries are a year or more from
  their redemption date, so about 1.5% is earned per year of capital locked.
- 10 slots of $2,500, about half of them filled, at roughly 1.5% a year, is
  $200 to $400.
- The $38 fee is 1.5% of a $2,500 position, so it removes most candidates.
  At $0, entries triple but the per-SPAC volume cap binds on 81% of them.

**Bias of the estimate.** Optimistic overall:
- **Optimistic:** vote dates come from the post-vote 8-Ks; annual meetings
  with no redemption right count as exits; E_t errs high.
- **Conservative:** no deal pop, which the spec requires; extension
  contributions are only seen at the next 10-Q.

None of these is large next to a gap of eight times.

**Confirmation period (2021-2025), entry-time data only, as the spec asks:**
- 980 SPACs, 376,896 SPAC-days;
- 296,093 days below E_t (936 SPACs);
- 61,957 days with a net discount of at least 0.5% (671 SPACs).

Opportunities were far more plentiful there. That can't rescue the trial:
the gate is judged on the design period, and the confirmation period has not
been simulated.

### b. Pass probability: no cited prior exists

- **Gahng, Ritter and Zhang (2023, *RFS* 36(9), 3463-3501), Table 3.** IPO
  investors who buy a unit at the offer price and follow an optimal redemption
  strategy earned 23.9% a year, equal-weighted, over 2010-2020 (458 SPACs).
  Liquidated SPACs earned 2.0% a year; the worst SPAC 0.51% a year.
  - These are unit returns, warrants included, bought at the IPO.
  - There is no Sharpe ratio and no secondary-market purchase below trust.
- **Klausner, Ohlrogge and Ruan (2022, *Yale J. on Regulation* 39(1),
  228-303).** As cited by Gahng, Ritter and Zhang: 11.6% a year for IPO
  investors in 47 mergers of 2019-2020. I did not verify this against the
  paper itself. Same limitations.

No source covers buying the common below trust in the secondary market. The
most comparable figure is the liquidated-SPAC floor return of 2.0% a year.
That is a total return in a period when T-bills averaged about 0.6%, and it
has no stated volatility. So no cited Sharpe exists to put into
`deflated_sharpe.py --power`, and check (b) was not computed. Five years at
t ≥ 3 needs S ≥ 1.04.

With redemption-value marking, the strategy's own Sharpe would be very high
and meaningless: there is no price risk until exit. The question was always
the dollars, and check (a) answers it.

### c. Benchmark fit

The benchmark is T-bills.
- The trust's own interest is in E_t's accrual, so the excess series is net
  of it.
- Cash earns the T-bill rate, so T-bill income is never counted as excess.
- The difference between the 3-month (DTB3, trust) and 1-month (FF rf, cash)
  rates is a few dollars a year on this account.

## Limitations

- **CRSP coverage.**
  - OTC SPACs (most of 2010-2012) and SPACs still trading as units are absent.
  - CRSP ends 2025-12-31.
  - 183 SPACs are unlinked.
- **Filing date vs acceptance time.** XBRL values are used from the day after
  their filing date. That is conservative for a filing accepted before the
  close.
- **Excise tax.** The 1% excise tax on buybacks from 2023 on (Inflation
  Reduction Act) can be charged against the trust, and liquidation expenses
  can be too. The $0.02 haircut stands in for both, as an assumption.
- **Broker redemption mechanics.**
  - Redemption requires instructing the broker before the deadline, often a
    day or more earlier than the SPAC's own.
  - Fees vary from $0 (Fidelity) to $38 (E\*TRADE) and $50 (some smaller
    brokers).
- **OTC SPACs excluded.**
- **E_t.** It ignores proxy-stated amounts and extension contributions until
  the next 10-Q.

## Reproduce

```
python3 python/spac_edgar.py universe   # EDGAR full-text search + prospectus headers (SEC, cached in data/spac/)
python3 python/spac_edgar.py terms      # trust per share, deadline, symbol
python3 python/spac_edgar.py facts      # XBRL companyfacts
python3 python/spac_edgar.py filings    # submissions: 8-K items 5.07 / 2.01
python3 python/spac_trust.py crsp       # ONE WRDS connection (one Duo push)
python3 python/spac_trust.py link
python3 python/spac_trust.py gate       # reports/spac_gate_design.csv
```

Also needs `data/spac/dtb3.csv` (FRED DTB3) and `data/pead/ff_daily.parquet`
(Fama-French daily rf, from the trial 59 pull).

## Sources

- Gahng, Ritter and Zhang (2023), working-paper version:
  https://site.warrington.ufl.edu/ritter/files/SPACs.pdf
- Fidelity commissions and fees ("Voluntary corporate reorganization $0 per
  transaction"): https://www.fidelity.com/trading/commissions-margin-rates
- E\*TRADE pricing ($38 for voluntary actions such as tender offers):
  https://us.etrade.com/what-we-offer/pricing-and-rates. Taken from search
  results; the page timed out when fetched directly.
