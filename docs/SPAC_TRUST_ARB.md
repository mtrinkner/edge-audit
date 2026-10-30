# SPAC trust-value arbitrage (proposed trial 60)

**Result: killed at the pre-declaration gate on 2026-10-29: twice, then
again after a symmetric data audit (run 4, the last design-period run).
Nothing registered; no trial spent.**

1. **First gate, as a dollar study** (the original section below): $186 a
   year over T-bills against a $1,500 bar.
2. **Second gate, as a research-grade statistical test** (the next
   section): the dollar bar was lowered to $250 a year, and the declared
   rule clears it at $406. It fails pass probability. The design Sharpe of
   1.21, halved to a prior of 0.60, gives P(pass) = 0.050 over five
   confirmation years, against the 0.25 required. That needed a design
   Sharpe of 2.08.
3. **Symmetric data audit and run 4** (the section after the second gate):
   the gains were audited as the drops had been. 22 of 78 sampled trades had
   been bought after the last chance to redeem. Fixed by rule, run 4 gives
    a year, t 2.73, Sharpe 0.93, P(pass) 0.025. Still killed.

# Second gate: a research-grade test (2026-10-29)

The goal was a pre-registered statistical test of a contract-backed trade.
Dollars are reported but are not the pass criterion. The bar was set by the
project owner: $250 a year on $25k, and P(pass) ≥ 0.25 with the prior taken
as half the design-period Sharpe.

## Data built for it (`python/spac_proxy.py`, `python/spac_trial.py`)

The first gate took vote dates from the 8-K filed after each vote, which
isn't entry-time. This study reads what a holder could have known.

**Filings read.** 7,736 SPAC-phase filings of the 463 design-era SPACs, from
EDGAR:
- definitive proxies (DEF 14A, DEFM14A, DEFR14A, 424B3 merger prospectuses);
- DEFA14A supplements;
- 8-Ks with items 5.07, 5.03, 8.01, 7.01, 2.01 or 3.01.

**Every value is usable from the day after its filing date.**

- **Meetings** (from proxies), each with:
  - the meeting date;
  - the kind: business combination, extension or annual;
  - whether holders may redeem. Extension and business-combination
    meetings: yes. Annual meetings: no;
  - the redemption deadline (stated, else 2 business days before the
    meeting);
  - the stated per-share amount.
  - Postponements and adjournments come from DEFA14A filings and 8-Ks and
    apply from their own filing date.
- **Charter deadline.** The prospectus deadline, moved by each extension in
  an 8-K: approved at a vote, or sponsor-funded. A DEFA14A states a
  proposal, so it never moves the deadline.
- **NRO_t.** The next redemption opportunity known on day t:
  - the earliest pending meeting with a redemption right whose proxy is
    filed;
  - else the charter deadline in force.
  - A filed proxy for a meeting past the charter deadline counts, because
    the deadline must have moved.
- **amount_t.** The proxy-stated amount once a proxy is filed, else E_t
  (from spac_trust.py) accrued to the payment date.
- **Outcomes, used only to settle positions.** The meeting date actually
  held (the nearest item 5.07 8-K); the amount paid (from the post-meeting
  8-Ks); liquidation amounts and dates.
  - **Fallbacks.** With no parsed paid amount, the stated amount minus the
    $0.02 haircut is used, and counted. A paid amount more than $1 from the
    amount known at entry is a misparse and is discarded.
  - **Payment dates** are an assumption: the meeting plus 5 business days.
    8-Ks rarely state them.
- **Liquidation payment lag, measured.** 52 design liquidations with a
  parsed date. Against the charter deadline in force, payment came a median
  1 business day *before* it (interquartile range −13.5 to +2). The
  declared deadline + 10 business days is late, which lowers entry yields:
  a conservative direction.

**Automated consistency checks** (`reports/spac_events_check_design.csv`):
- 352 SPACs and 588 meetings, 538 of them with a redemption right;
- stated amount found for 97%;
- paid amount found for 49%; where both exist, 81% agree within $0.10;
- paid more than $0.10 below stated: 4.6%;
- deadline before meeting, payment after meeting, and charter deadlines in
  order: 100%;
- 77 liquidations: 77% have a liquidation 8-K, 66% an amount, 68% a date.

**Hand validation of 50 design SPACs** (`reports/spac_hand_validation.csv`).
- **Sample:** 12 liquidated, 18 with an extension vote, 20 merged, drawn at
  random (seed 60).
- **Method:** five independent reviewers extracted each field from the
  filings without seeing the parser's output. The two were then compared.

| Field (105 hand-found meetings) | Agreement |
|---|---|
| Meeting matched by the parser | 96% |
| Redemption right | 94% |
| Meeting date, within 1 business day | 89% |
| Redemption deadline, within 1 business day | 83% |
| Stated amount, within $0.02 | 69% |
| Paid amount, within $0.02 | 55% |
| **Settlement amount the sim uses, within $0.10** | **88%** (median error −$0.02) |

- **Settlement errors lean conservative.** 16% are more than $0.05 low and
  6% more than $0.05 high. The usual miss is the parser taking the round
  IPO deposit ($10.00) for the proxy's own estimate ($10.14).
- **Reviewer misreads.** Six hand "paid" values ($5.07, $51.70, ...) were
  misreads and are excluded as truth.
- **Liquidations.** 7 recorded by hand; the parser flags 6 of them but
  matches the amount within $0.02 for only 2. The rest settle at E_t minus
  the haircut.
- **Three of the 50 SPACs have no proxies or 8-Ks on EDGAR**
  (foreign-private-issuer style filers). Their meetings are invisible to
  this data.

**Fixes found by the validation, before the gate ran:**
- a warrant's $11.50 exercise price read as a paid amount;
- large DEF 14A and DEFR14A proxies fetched empty (a 5 MB cap on the index
  route);
- the "per-share price at which public shares will be redeemed ... $10.44"
  phrasing missed;
- liquidation amounts announced ahead of payment missed.

Each has a fixture test in `tests/test_spac_trial.py`.

## The rule simulated (as specified for the declaration)

**Entry and sizing:**
- **Decision.** After the close of day t, on the annualized net yield to
  NRO_t over T-bills: (amount_t − ask_t − fee/shares − $0.02) / ask_t ÷
  years to payment, minus the T-bill rate.
- **Eligible:** a yield of at least 2%, and NRO_t no more than 365 days
  away.
- **Fill:** buy at day t+1's ask. The fill must settle 2 business days
  before the redemption deadline.
- **Priority:** highest yield first.
- **Size limits:**
  - at most 10% of $25k per SPAC, and 25% of the 20-day median dollar
    volume;
  - skipped when the full position doesn't fit;
  - idle cash earns T-bills.

**Exits:**
- **Above amount:** any day the bid is at or above amount_t, sell at the
  bid.
- **Meeting deadline:** at a meeting's redemption deadline, sell if the bid
  is at or above the stated amount; otherwise redeem at the paid amount on
  the payment date.
- **Liquidation:** the paid amount on the payment date.
- **No position is ever held past a redemption deadline.** It is asserted
  every day, and tested.

**Valuation and costs:**
- NAV is marked at the bid each day.
- Primary fee $0 (Fidelity); haircut $0.02 a share (assumption).

**Data-error rules.** These are data cleaning, not trading choices:
- a quote whose bid is under 90% of its ask is a stub and counts as no
  quote;
- a day more than 10% below amount_t is excluded from entries and from
  marks.

## Gate results (design 2010-2020, `reports/spac_trial_gate.csv`)

**(a) Economic size: PASS.**

| | $/yr over T-bills on $25k | Bar |
|---|---|---|
| Ceiling: perfect foresight, unlimited capital, $0 fee | $2,991 | $250 |
| Ceiling at the $38 fee | $1,892 | |
| **Declared rule, NAV at the bid** | **$406** | $250 |

These ceilings are higher than annotation 1's $1,213. That figure used the
first gate's 8-K vote dates and a $38 fee. These use proxy-dated
opportunities, which add windows.

**(b) Pass probability: KILL.**

| | |
|---|---|
| Design months | 123 |
| Mean monthly excess over T-bills | 0.142% |
| Newey-West t (one-sided p) | 3.80 (0.00007) |
| Monthly excess Sharpe | **1.21** |
| Prior (half) | 0.60 |
| `deflated_sharpe.py --power --prior-sharpe 0.6046 --years 5` | **P(pass) = 0.050** |
| Needed | prior ≥ 1.04, so design Sharpe ≥ 2.08 |

The design-period result itself is strong, and this rule kills it anyway.
That is by design: a design Sharpe is inflated by the search that produced
the rule, so a prior of half of it is used. Five confirmation years can't
reliably detect a true Sharpe of 0.60 at t ≥ 3. The confirmation would
probably fail even if the edge were real.

**(c) Benchmark: T-bills.** The excess series nets the FF daily rf from
NAV, and the trust's own interest is inside the SPAC's price. Market beta
is 0.01. Down-market beta, over the 10 months when the market fell more
than 4%, is −0.03.

**The rest of the design run (reported, not judged):**
- **Excess by year ($):**

  | 2010 | 2011 | 2012 | 2013 | 2014 | 2015 | 2016 | 2017 | 2018 | 2019 | 2020 |
  |---|---|---|---|---|---|---|---|---|---|---|
  | 0 | 1 | 39 | 6 | 137 | 237 | 724 | 134 | 763 | 746 | 1,675 |

  2020 is 38% of the total. Without it, the mean is $279 a year.
- **Losses and drawdown:** the worst month is −$150 (2014-08); no year
  lost money; maximum drawdown on the bid-marked NAV is 3.1%.
- **Capital use:**
  - 180 entries, 32% average invested;
  - 53% capacity-limited, and 297 entries skipped for cash;
  - a median of 81 days locked;
  - the top 5 SPACs earned 25% of profit.
- **Exits:**
  - 98 sold at or above amount_t, which is the deal pop and early
    convergence;
  - 59 redeemed;
  - 17 settled at a merger meeting the parser didn't find (counted);
  - 6 liquidated.
- **Floor forecast error** (paid minus amount_t at entry): median −$0.02;
  10th percentile −$0.10. Paid amounts more than $0.10 below stated: 1.9%.

## The Sharpe moved with each data fix

The gate ran three times on the design period, and each rerun followed a
data error found by inspecting the largest NAV moves:

| Run | Fix | Design Sharpe | P(pass) |
|---|---|---|---|
| 1 | — | 0.37 | 0.005 |
| 2 | Stub quotes ($0.01 bid vs a $10.30 ask, permno 15362) are no quote | 0.59 | 0.010 |
| 3 | The >10% suspect filter applied to marks (two positions marked near $6.50 after their SPAC phase had in fact ended); misparsed paid amounts ($5.66, $8.00) discarded | 1.21 | 0.050 |

Every fix moved the Sharpe up, and each was chosen after seeing a result.
That is a degree of freedom, and it is disclosed here. Each targeted a
verified error, not a parameter. Further cleaning could raise the Sharpe
again. Reaching 2.08 would need monthly volatility to fall by 40% from a
series whose worst month is already −$150. I don't think the remaining
errors are that large, but that is a judgment, not a measurement.

## The confirmation period was not touched

Nothing from 2021-2025 was parsed, simulated or printed in this study.
- The only earlier look is the four entry-time counts of 2026-10-29
  (annotation 2).
- A confirmation-era filing download was started for the automated checks.
  It was stopped at the kill: about 1,000 of 11,900 filings were fetched,
  and none was parsed.

## Limitations

- **Payment dates** are assumed: meeting + 5 business days, and charter
  deadline + 10 for liquidations.
- **17 exits** settled at a merger meeting the parser missed, at E_t
  accrued minus the haircut. That is plausibly optimistic.
- **The charter deadline at entry** comes from the prospectus's first
  "within N months" and misses automatic extensions. An early deadline
  overstates the entry yield.
- **SPACs without EDGAR proxies** (foreign private issuers) have no meetings
  in this data.
- **The bid** is CRSP's closing bid. A real exit may get less, and a real
  entry may pay more than the closing ask.

# Symmetric data audit and run 4 (pre-committed 2026-10-29)

Runs 1-3 each followed a data fix found by looking at the largest NAV
*drops*, and each raised the Sharpe. Nobody looked at the gains. A
read-only review of run 3's trades raised four concerns: short holds
bought 13-16% under trust days before a meeting, the 17 `unparsed_meeting`
exits settled at E_t, the suspect filter running on the decision day but
not the fill day, and the share of profit from entries above a 5%
discount. This section fixes the audit's sample, classes and decision
rules **before any trade is inspected**. It was committed before the
audit began.

## Sample (fixed before inspection)

Drawn from `data/spac/trades_design_gate.csv` (run 3, 180 trades):

1. **Top 20:** the 20 largest `pnl`.
2. **Bottom 20:** the 20 smallest `pnl`.
3. **Random 20:** from the remaining 140, sorted by (`entry_day`,
   `permno`), drawn without replacement by
   `numpy.random.default_rng(61).choice(140, 20, replace=False)`.
4. **Unparsed:** every trade with `exit == "unparsed_meeting"`.
5. **Short holds:** every trade with `exit_day − entry_day` of 14
   calendar days or less.

A trade in several groups is classified once and reported in each group
it belongs to. Totals count it once.

## Classes

Each trade is checked against its EDGAR filings and CRSP rows, and gets
every class that applies, with the evidence (filing accession or CRSP
permno and date) recorded:

- **VALID:** none of the below.
- **PHANTOM-DEADLINE:** the redemption deadline actually in force (from
  the filings, including ones the parser missed) was on or before the fill
  date plus 2 business days of settlement. No redemption right was bought.
- **PHANTOM-PHASE:** the SPAC phase had ended at entry or before the
  modeled exit: merged, renamed, or a different security on the permno.
- **PRICE-ERROR:** a stub or misprinted bid or ask at entry, exit or a
  mark.
- **AMOUNT-ERROR:** the settlement amount is more than $0.10 from what
  the filings say was paid.
- **TIMING-ERROR:** the payment or meeting date is more than 5 business
  days from the filings.

## Decision rules (fixed before inspection)

Every fix is a rule applied to all trades, never a per-trade override or
exclusion.

a. **Suspect check on the fill day.** The >10% check also runs on the
   fill day's ask, against the amount known that day. A fill that fails
   it is not entered.
b. **Deadline buffer.** No fill unless the redemption deadline in force
   is at least 3 business days after the fill date (2 for settlement, 1
   for the broker's cutoff), using only information filed by the decision
   day.
c. **No E_t settlement for a missed meeting.** A position whose meeting
   the parser didn't find exits at the bid on the first trading day after
   the charter deadline in force or the SPAC phase end, whichever comes
   first. The bid comes from CRSP on that permno, whatever the security
   has become. If CRSP has no valid quote then, the position exits at its
   last valid bid, on that day.
d. **Other errors.** Any other error class that appears in 3 or more
   sampled trades gets a rule fix with a test. Fewer than 3 is reported,
   not fixed.
e. **Both directions.** A bug that pushes returns up and one that pushes
   them down are fixed alike. If a qualifying fix would raise returns, it
   goes in too.

## Run 4 is the last gate run on the design period

Run 4 applies rules a-c and any fix qualifying under d, each with a test,
and reruns `python/spac_trial.py gate` with the gate rules unchanged:

- a $250/yr bar;
- a prior of half the design Sharpe;
- P(pass) ≥ 0.25 over 5 years.

Whatever run 4 shows is recorded as the gate decision. A later bug fix
goes in as a documented correction, not a new gate decision. If run 4
fails, nothing is registered. If it passes, the owner decides on a
declaration. Nothing from 2021-2025 is parsed, simulated or printed.

**Disclosed before the audit:** to check the trade file's columns, I
printed its header and first two rows (permnos 12350 and 12792, both
long-hold redemptions earning $24 and $8). Nothing else was inspected.

## Audit results (2026-10-29, after the commit above)

The sample is 78 of run 3's 180 trades: top 20, bottom 20, random 20, all
17 `unparsed_meeting` exits and all 33 holds of 14 days or less. Groups
overlap. Each trade was checked against its EDGAR filings and CRSP rows.
Scratch evidence (accession numbers, CRSP dates) is in the session notes;
the aggregates are in `reports/spac_audit_design.csv`.

**Trades by class and group** (a trade can carry two classes):

| Class | Top 20 | Bottom 20 | Random 20 | Unparsed (17) | Short holds (33) | All 78 | PnL, all 78 |
|---|---|---|---|---|---|---|---|
| VALID | 14 | 12 | 15 | 2 | 13 | 49 | $2,337 |
| PHANTOM-DEADLINE | 6 | 3 | 5 | 13 | 18 | 22 | $1,602 |
| PHANTOM-PHASE | 1 | 0 | 0 | 1 | 1 | 2 | $128 |
| TIMING-ERROR | 0 | 3 | 0 | 1 | 1 | 4 | −$38 |
| PRICE-ERROR | 0 | 1 | 0 | 0 | 0 | 1 | −$19 |
| AMOUNT-ERROR | 0 | 1 | 0 | 0 | 0 | 1 | −$12 |
| *Rule c would mis-settle* | 0 | 0 | 1 | 3 | 1 | 3 | $111 |
| All sampled | 20 | 20 | 20 | 17 | 33 | 78 | $3,871 |

### What each class was

- **PHANTOM-DEADLINE, 22 trades: one bug.** Once a filed meeting's
  redemption deadline passed, `known_at` dropped the meeting and fell back
  to the charter deadline, months away. The rule then saw a SPAC under
  trust with a distant exit and bought it the next day, after the last
  chance to redeem. Examples:
  - permno 14994: deadline 2016-06-30 (DEFM14A 0001213900-16-014411), bought
    2016-07-01 at $9.00;
  - permno 16704: deadline 2018-02-15 (424B3 0001193125-18-031449), bought
    2018-02-16 at $8.97;
  - permno 16673: deadline 2018-11-02 (DEFM14A 0001193125-18-303530), bought
    2018-11-06.

  In all three the merger closed days later, and the shares became the
  combined company's stock, at $4.65, $7.95 and $9.90 on the first day
  after.

  13 of the 22 were then settled at E_t as `unparsed_meeting`, worth $1,108
  that no holder could have earned. The other 9 exited by a sale at the bid,
  or through a liquidation after the deal failed (permno 14324). Those exits
  are market outcomes, but no redemption right was bought.
- **TIMING-ERROR, 4 trades.**
  - Three were redemptions paid on mergers that never closed. Cambridge
    Capital terminated its deal on 2015-05-07 (8-K 0001193125-15-175752,
    item 1.02). Easterly terminated on 2016-12-31 (8-K 0001144204-17-000038).
    Shares tendered for redemption came back; run 3 paid them.
  - One was Aquasition's tender offer: $10.30, expired 2014-07-29 (SC TO-I/A
    0001144204-14-046505). Run 3 settled it at E_t on 2014-11-03.
- **PHANTOM-PHASE, 2 trades.**
  - DD3 (permno 18193) merged with Betterware on 2020-03-13 under a new
    permno; run 3 treated it as a liquidation and paid trust (+$127).
  - AR Capital's rename to Axar on 2016-10-11 was read as a merger.
- **PRICE-ERROR, 1 trade.** Permno 14878 filled at a closing ask of $10.53
  while it traded at $9.86 (2015-12-31).
- **AMOUNT-ERROR, 1 trade.** GTY redeemed at about $10.29 (8-K
  0001144204-19-009918). The parser took a $10.00 promissory-note figure
  from the same 8-K.
- **Rule c would mis-settle 3 valid trades.** Each had a real redemption the
  parser couldn't see:
  - two tender-offer SPACs: Aquasition, and TKK Symphony at $10.31, expired
    2020-02-13 (SC TO-I/A 0001213900-20-004186);
  - one merger proxy filed under the combined company's CIK: Modern Media
    Acquisition Corp. S.A., 424B3 0001193125-19-222306, deadline
    2019-08-26.

  Run 3's E_t settlement was close to right for these three. Rule c as
  written would have sold each at the post-merger bid.

**The random 20, as a read on the bulk.** 5 of the 20 are PHANTOM-DEADLINE,
carrying $187 of the group's $450 (42%). Only two of those were settled at
E_t ($46, 10%); the other three exited at market prices. That suggests a
10-40% overstatement on an ordinary trade, depending on whether a sale is
counted.

**Short holds and unparsed exits were mostly phantom.** 18 of the 33 short
holds and 13 of the 17 unparsed exits are PHANTOM-DEADLINE. The reviewer's
two examples (permnos 14994 and 16704) are both in that class.

**Entries above a 5% discount: mostly valid.** 27 trades, 37% of run 3's
profit. The long holds among them (bought at $9.67-9.92 in 2017-19, sold or
redeemed about a year later) check out: valid quotes, sales at the bid. The
short ones are the phantom-deadline trades above.

### Rule fixes applied in run 4

| Rule | Basis | Test (`tests/test_spac_trial.py`) |
|---|---|---|
| a. Suspect check on the fill day's ask | pre-committed | `test_fill_day_ask_is_checked_for_the_suspect_filter` |
| b. Deadline at least fill + 3 business days | pre-committed | `test_fill_needs_three_business_days_before_the_deadline` |
| c. A merger without a parsed meeting exits at the bid on the first trading day after the charter deadline or phase end | pre-committed | `test_missed_meeting_exits_at_the_bid_after_the_phase_end` |
| d1. No entry after a meeting's deadline until its outcome is known: a termination 8-K (item 1.02) for a merger or tender, the vote 8-K or an extension for an extension meeting | PHANTOM-DEADLINE, 22 | `test_no_entry_after_a_passed_deadline_until_the_outcome_is_known` |
| d2. A merger or tender redemption is void unless the deal closes within 120 days of the meeting (assumption), and is paid at closing | TIMING-ERROR, 4 (3 of them this cause) | `test_redemption_is_void_when_the_merger_never_closes` |
| d3/e. Tender offers (SC TO-I and amendments) are redemption opportunities: expiry = deadline, offer price = amount. Merger proxies filed under a co-registrant's CIK are found by EDGAR full-text search on the SPAC's name, for every merged SPAC, and kept only if the document opens as a meeting notice naming the SPAC in full | Rule c would mis-settle, 3. These fixes raise returns relative to rule c, and go in under rule e | `test_tender_offer_*` (3), `test_coregistrant_proxy_is_matched_on_the_spac_name` |

**Not fixed** (fewer than 3 in the sample, reported only):
- PHANTOM-PHASE (2);
- PRICE-ERROR (1);
- AMOUNT-ERROR (1);
- the remaining TIMING-ERROR cause, a tender settled late (1). d3 now
  parses tenders, so this one is handled anyway.

The new parsers found:
- 151 tender filings for 16 SPACs;
- 32 co-registrant proxies with a meeting, for 26 SPACs. Many duplicate the
  SPAC's own proxy and merge into the same meeting.

### Is the paid-amount parser good enough to settle a 1-2% edge?

Not on its own. Hand validation found paid amounts within $0.02 of the
filings 55% of the time. Settlement amounts were within $0.10 88% of the
time, with a median error of −$0.02, and errors lean low. A 1-2% edge is
$0.10-0.20 a share, so a $0.10 miss is half the edge. Two things limit the
damage:
- In run 4, redemptions carry only $454 of $4,308 PnL. Most profit is sales
  at the bid, where the paid amount only sets the sell trigger.
- The errors push returns down.

**Proposed fix (not implemented; GTY is 1 case):**
- prefer an 8-K sentence of the form "redeemed at a per share price of
  approximately $X";
- tighten the plausibility check from $1.00 to $0.15 of the stated amount,
  falling back to stated minus the haircut.

## Run 4: the last gate run on the design period

| | Run 1 | Run 2 | Run 3 | **Run 4** |
|---|---|---|---|---|
| Excess $/yr on $25k | — | — | $406 | **$277** |
| NW t | — | — | 3.80 | **2.73** |
| Monthly Sharpe | 0.37 | 0.59 | 1.21 | **0.93** |
| P(pass), prior = Sharpe/2, 5 yrs | 0.005 | 0.010 | 0.050 | **0.025** |
| Entries | — | — | 180 | 161 |
| PnL: sold at the bid | — | — | $3,624 (98) | $3,436 (90) |
| PnL: redeemed | — | — | $857 (59) | $454 (49) |
| PnL: no parsed meeting | — | — | $1,220 (17) | $353 (19) |
| PnL: liquidated | — | — | $249 (6) | $65 (3) |
| PnL from discounts above 5% | — | — | $2,190 (37%) | $1,318 (31%) |
| PnL from holds of 14 days or less | — | — | $1,482 | $192 |
| 2020's share of profit | — | — | 38% | **52%** |
| Excess $/yr, 2010-2019 | — | — | $279 | **$145** |

Ceiling (perfect foresight, $0 fee): $3,056 a year. Worst month −$147
(2014-08); maximum drawdown 4.8%; market beta 0.02. Run 4's skips:
- 22 entries blocked by d1;
- 19 that failed the deadline buffer;
- 8 suspect fills;
- 8 redemptions voided.

The 19 run-4 exits with no parsed meeting are mostly SPACs that kept
trading past the prospectus deadline, where an automatic or unparsed
extension was missed. They exit at the SPAC's own bid, near trust.

**Gate decision, as declared:**
- (a) PASS: $277 ≥ $250, and the ceiling clears it.
- (b) KILL: P(pass) = 0.025 < 0.25. The design Sharpe would have to be
  2.08.

Killed. Nothing registered; no trial spent. Run 4 is final for the design
period. Any later fix is a documented correction, not a new gate decision.

**How much of run 3 was real.** About two-thirds:
- $/yr fell from $406 to $277 (68%);
- PnL fell from $5,950 to $4,308 (72%);
- the Sharpe fell from 1.21 to 0.93.

The lost part is concentrated where the reviewer pointed: short holds fell
from $1,482 to $192, and unparsed settlements from $1,220 to $353. What
remains leans harder on 2020: 52% of profit, and $145 a year without it.

## Correction to the second gate's "not touched" statement

"Nothing from 2021-2025 was parsed" was wrong. The design cohort's scope
holds every filing of a SPAC that IPO'd by 2020-12-31, through its phase end
plus 60 days. That included 3,271 filings dated 2021 or later in run 3, out
of 7,736. Run 4's new tender filings add 12 more. They were parsed as part
of the design cohort's event timelines. The audit's inspection tables printed
meeting dates and phase ends for sampled design SPACs, some falling in
2021-22. No 2021-2025 quote, return or simulation was computed or printed,
and the confirmation cohort (IPO after 2020) was never parsed.

# First gate (2026-10-29 morning)

On a $25,000 account, the declared portfolio would have earned about **$186 a
year over T-bills** in the design period, 2010-2020. The gate needs $1,500.
The most generous single change, a $0 redemption fee, gives $326. The trade
works: positions are entered at a median 1.5% below trust value and redeemed
at it. But the discount is earned once per holding period of a year or so, on
capital that is invested about half the time.

## Annotations (added 2026-10-29, after the kill)

**1. The ceiling, which the gate should have sized first.** For each SPAC's
redemption window in the design period: the single best day's net discount
× that day's capped position (min of $2,500 and 25% of the 20-day median
dollar volume), with unlimited capital and perfect foresight. Traded,
non-suspect days only; the same E_t, half-spread and $0.02 haircut as the
gate.

| Fee | Windows | SPACs | Ceiling $/yr | Median years to payment |
|---|---|---|---|---|
| $38 | 238 | 212 | **$1,213** | 0.84 |
| $0 | 354 | 260 | $2,211 | 0.71 |

- **The ceiling also fails the $1,500 bar at the $38 fee**, so the kill
  stands even for a perfect rule. At $0 the ceiling clears it and only the
  rule ($326) fails.
- **Concentrated:** 2018 and 2020 are 75% of the $38-fee ceiling ($3,195
  and $6,822 of $13,341).
- **Annualized net yield over T-bills** (net discount ÷ years to the
  payment date), $38 fee:
  - over all positive-net design days (8,294): median **1.1%**, 90th
    percentile **2.9%**;
  - on each window's best day: median 2.4%, 90th percentile 8.7%.
- **Against the bar:** the bar of $1,500 on $25k needs 6% a year over
  T-bills. The typical opportunity yields a fifth of that. Under the rule now in
  METHODOLOGY.md, this class would have been screened out before anything
  was built.

**2. A partial look at the confirmation period.** For a contract-backed
trade the discount at entry is most of the return. So these 2021-2025
counts, printed on 2026-10-29, are a partial look:
- SPACs, SPAC-days, days below E_t, and days with a net discount of at
  least 0.5% (the four numbers under "Confirmation period" below).

Nothing else from 2021-2025 was computed or printed:
- no returns or NAV;
- no exit prices, deal pops, paid amounts or outcomes;
- no volatility;
- no distribution of discounts beyond those counts.

**3. Valuation.** The gate sim valued open positions at redemption value
(`mark="redeem"`), so it carried no price risk until exit. A real trial
marks to the market bid.

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
python3 python/spac_trust.py gate       # reports/spac_gate_design.csv (first gate)
python3 python/spac_proxy.py download design && python3 python/spac_proxy.py parse design   # SEC, cached
python3 python/spac_trial.py validate   # reports/spac_events_check_design.csv
python3 python/spac_trial.py gate       # reports/spac_trial_gate.csv (second gate)
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
