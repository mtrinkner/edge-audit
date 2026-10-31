# Reverse-split round-up arbitrage (candidate trial 60)

**Status: Phase 1 (data and validation) done. The gate has not been run. No
payoff has been computed for any event, in either period.** This document
declares the gate's rules and pre-commits its audit before the first payoff
is seen.

## The trade

A listed company announces a 1-for-N reverse split, and its filing says
fractional shares will be **rounded up** to a whole share instead of paid in
cash. One share bought before the split becomes one post-split share, worth
about N old shares. It works one share per account, so it can't be scaled:
the same capacity-capped, contract-backed shape as the odd-lot tenders
(trial 58).

The main risks:
- **Brokers may not round up for each customer.** A round-up "at the
  participant level" is applied to the DTC participant's (the broker's)
  aggregate position, so a customer can get cash instead.
- **Prices fall** around reverse splits.
- **Terms change**, or the split is cancelled.

History can't measure what a broker does. It enters as the labeled
assumption p, with sensitivity rows.

## Data (`python/reverse_split.py`)

**Events.** Every CRSP reverse split (`crsp.stkdistributions`, distribution
type FRS with a share factor in (−1, 0)) of an ordinary common share
(sharetype NS, EQTY, COM) on NYSE, Nasdaq or NYSE American, with an ex date
in 2010-2025. That gives 2,693 events, pulled in one WRDS connection along
with security history, CCM links to Compustat CIKs, and daily quotes from 130
calendar days before to 45 after each event.

| Year | 2010 | 2011 | 2012 | 2013 | 2014 | 2015 | 2016 | 2017 | 2018 | 2019 | 2020 | 2021 | 2022 | 2023 | 2024 | 2025 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Events | 105 | 73 | 89 | 76 | 49 | 92 | 155 | 121 | 104 | 158 | 136 | 55 | 198 | 410 | 422 | 450 |

- The ratio is N = 1 / (1 + share factor).
- 96% of events have a CIK on the ex date.
- OTC stocks aren't in CRSP and aren't covered.

**Filings.** For each event with a CIK:
- every 8-K carrying item 5.03, 3.01 or 8.01, with its EX-99 press releases;
- every DEF 14A, PRE 14A, DEF 14C and PRE 14C;
- all filed from 120 days before the ex date to 10 days after.

That is 10,583 filings, from SEC EDGAR under the project's User-Agent and
cache. Each is usable only from the trading day after its acceptance date.

**Parsed from the text only:**
- **Ratio:** only an 8-K states the ratio the board chose. Design-period 8-K
  ratios match CRSP 95% of the time; proxy ratios match 56%, because
  proxies propose ranges and lists.
- **Effective date:** the first split-adjusted trading day. A time after the
  close moves it to the next trading day.
- **Fractional-share class**, with the sentence it came from:
  - ROUNDUP_HOLDER: rounded up for each holder;
  - ROUNDUP_PARTICIPANT: rounded up at the participant level, or for holders
    of record only;
  - CASH, DOWN, or UNKNOWN.
- **Announcement:** the first filing stating both an 8-K ratio and an
  effective date.
- **Cancellations and changed terms.**

### Hand validation (design period only; no payoffs inspected)

Independent reviewers read the filing text for each event, without the
parser's output or CRSP dates. Each extracted the ratio, effective date,
fractional class and sentence, announcement filing and any changes.

**Round 1** (60 events) and **round 2** (30 fresh events) found parser
errors. Each was fixed as a rule applied to all filings:
- "paid in cash", "settled in cash", "paid by check" and aggregated-sale
  wording weren't read as cash;
- "fractional common shares" wasn't matched;
- sentences about options, warrants, spin-offs and mergers were read as the
  split's terms;
- "rounded to the nearest whole share" was read as rounded up or down. It is
  now UNKNOWN;
- round-ups worded for "holders of record" weren't flagged. They are
  participant-level by the declared definition;
- long sentences were cut before their "...and paid cash" clause;
- one filing mixing classes made the event UNKNOWN. Now a class stated at
  least twice as often as all others combined wins.

**Round 3** (30 fresh events, parser frozen, md5 recorded before the draw)
was not used for any fix:

| Round | Events | Class, raw | Class, adjudicated | Ratio | Effective date | Announcement ±1 business day |
|---|---|---|---|---|---|---|
| 1 (fixes) | 60 | 58% | 92% | 88% | 62% | 77% |
| 2 (fixes) | 30 | 53% | 100% | 87% | 80% | 80% |
| **3 (untouched)** | **30** | **77%** | **97%** | **87%** | **77%** | **87%** |

`reports/rsplit_hand_validation.csv` has the full table.

**How to read the columns:**
- **Raw:** the parser's class from filings accepted before the split day,
  against the reviewer's.
- **Adjudicated:** I re-read the full filing wherever the two disagreed. The
  reviewers saw excerpts of up to 6,000 characters (rounds 1-2) or 12,000
  (round 3) per filing, and several said the sentence was cut. Where the
  parser's sentence is in the full text and states the treatment, the
  parser is counted right.
- **The one round-3 error (G11)** is tax boilerplate about "cash in lieu"
  outvoting the round-up sentences. That makes the event UNKNOWN, so it is
  dropped, not traded wrongly. It is reported, not fixed, because round 3
  was the measurement.
- **The reviewers' own ratios and dates** match CRSP 96-100% and 96-98% of
  the time.

**Class agreement is at least 90% on the untouched round (97% adjudicated),
so the parser stands for Phase 2.**

**Ratios and dates.** The misses lean safe:
- an effective date a day early moves the entry earlier, and the trade still
  settles before the real split;
- a ratio found only in a proxy leaves the event unannounced and untraded;
- an effective date later than the real one is checked against the actual
  CRSP split date, and the trade gets no round-up.

**Coverage.** Only 22% of design events with filings have an 8-K stating
both the ratio and the date, accepted before the split. The rest announced
on the split day or later, or only by proxy, and can't be traded.

**CRSP factor vs filing ratio.**
- Where an 8-K ratio was found, it matches the CRSP factor for 95% of
  design events and 92% of confirmation events.
- The 50 mismatches (11 design, 39 confirmation) are listed in
  `data/rsplit/factor_mismatches.csv`. It holds CRSP rows, so it stays
  git-ignored; `reports/rsplit_events_check.csv` has the counts.
- The payoff uses the filing ratio known at entry for the decision, and
  CRSP's factor for the cash-in-lieu branch.

**Events per year by fractional class** (`reports/rsplit_class_counts.csv`).
The class is the one known from filings usable before the split day.
Counts only; no prices.

| Year | Round-up, holder | Round-up, participant | Cash | Down | Unknown |
|---|---|---|---|---|---|
| 2010 | 9 | 2 | 38 | 1 | 55 |
| 2011 | 8 | 1 | 28 | 0 | 36 |
| 2012 | 18 | 1 | 23 | 0 | 47 |
| 2013 | 10 | 2 | 35 | 2 | 27 |
| 2014 | 4 | 0 | 14 | 0 | 31 |
| 2015 | 16 | 2 | 26 | 0 | 48 |
| 2016 | 25 | 4 | 44 | 2 | 80 |
| 2017 | 12 | 3 | 30 | 0 | 76 |
| 2018 | 16 | 3 | 25 | 0 | 60 |
| 2019 | 19 | 4 | 41 | 1 | 93 |
| 2020 | 14 | 1 | 44 | 1 | 76 |
| 2021 | 9 | 0 | 7 | 1 | 38 |
| 2022 | 37 | 3 | 71 | 1 | 86 |
| 2023 | 105 | 10 | 120 | 2 | 173 |
| 2024 | 131 | 17 | 115 | 1 | 158 |
| 2025 | 109 | 10 | 115 | 5 | 211 |

Design 2010-2018: 118 per-holder and 18 participant-level round-ups.
Confirmation 2019-2025: 424 and 45. Round-ups grew from 2023: 105, 131 and
109 per-holder events a year in 2023-2025, against 9-25 a year in the design
period. Only events with a usable pre-split announcement can be traded.

### Tests (`tests/test_reverse_split.py`)

- One verbatim fixture per class, including participant-level wording;
- "rounded down and paid cash" is CASH;
- a range is not a final ratio;
- the acceptance-date availability rule;
- the entry day;
- the round-up and cash payoffs, and the break-even p;
- a postponement after entry sells at the bid;
- a settlement on the split day gets no round-up.

The postponement test caught a lookahead before any payoff was computed:
the entry rule had picked "the last qualifying day" using terms learned
later. It now decides one day at a time. This change came after round 3's
md5 was recorded. It touches only the entry timing, not the text parsers
round 3 measured.

## Declared gate rules (fixed before any payoff)

**Periods:** design 2010-2018; confirmation 2019-2025 (7 years). Events per
year in each class are reported for both periods: counts only.

**One event, one share,** traded only when the class known at entry is a
round-up:
- **Entry.** Buy 1 share at the ask at the close of the last trading day
  whose trade settles before the effective date known that day (T+3 before
  2017-09-05, T+2 to 2024-05-28, T+1 after; `odd_lot_study.settlement_days`).
  - Not before the first trading day after the announcement is usable.
  - No such day: skipped.
  - No valid quote that day (bid < 90% of ask counts as none): no entry.
- **Rounded up (probability p).** Sell the post-split share at the bid at
  the close of the effective date (the CRSP ex date), or at the next valid
  bid.
- **Not rounded up (1 − p).** Cash for 1/N share: the effective date's bid
  ÷ N.
- **Cancelled or changed terms** after entry (ratio, date, or a CASH/DOWN
  class): sell the 1 share at the bid on the first trading day the filing is
  usable.
- **Settlement on or after the actual split date:** no round-up (p = 0 for
  that event).
- **Costs:** $0 commission; $5 a trade as a sensitivity row.

**Broker round-up probability p** (ASSUMPTION; no published source):
- **primary:** 0.8 for ROUNDUP_HOLDER, 0.4 for ROUNDUP_PARTICIPANT;
- **sensitivity:** 1, 0.5 and 0.2 for both;
- the break-even p is reported for each class.

**Exit sensitivity rows:**
- the effective date's open minus half the spread;
- the bid 5 trading days later.

**(a) Economic size, ceiling first:**
- **Ceiling:** every round-up event at p = 1, with perfect foresight on the
  cheapest valid entry day, design period, in $/yr per account.
- **Then:** the declared rule at the primary p.
- **Bar:** $500 a year per account, at the primary p, design period.
- **KILL** if the ceiling or the rule is under $500.

**(b) Pass probability.** No published prior exists:
- take the declared rule's design-period monthly P&L Sharpe (zero months
  included) and halve it;
- `deflated_sharpe.py --power --prior-sharpe S --years 7`;
- **KILL** if P(pass) < 0.25, i.e. a design Sharpe under 1.76.

**(c) Benchmark:** zero. Capital is a few dollars per event, held for days.
The return on capital per event is reported.

## Pre-committed symmetric audit (before any payoff is seen)

The SPAC study (docs/SPAC_TRUST_ARB.md, run 4) found its gains inflated by
trades that couldn't have earned them, because only the losses had been
audited. So this audit is fixed now.

1. **Sample:** from the first gate run's design-period traded events:
   - the 20 best by P&L at the primary p;
   - the 20 worst;
   - 20 more drawn at random from the rest, sorted by (ex date, permno),
     using `numpy.random.default_rng(62)`.
2. **Classes** (every one that applies, with the filing accession or CRSP
   row as evidence):
   - VALID;
   - WRONG-CLASS: the filings don't support a round-up for this holder;
   - WRONG-RATIO: N differs from the filings or CRSP;
   - WRONG-DATE: the effective date is off by more than 1 trading day;
   - ENTRY-AFTER-CUTOFF: the trade wouldn't have settled before the record
     or effective date;
   - PRICE-ERROR: a stub or misprinted quote at entry or exit;
   - NOT-A-SPLIT: the CRSP event isn't a reverse split of this security
     (a merger exchange, a conversion, a different security).
3. **Fixes:**
   - only as rules applied to all events, never per-event exclusions;
   - only for a class appearing 3 or more times in the sample;
   - in both directions alike: a qualifying fix that raises returns goes in
     too.
4. **Then the gate is rerun once.** That run is final for the design
   period. A later bug fix is a documented correction, not a new gate
   decision.

On a kill: written up here, nothing registered. On a pass: reported to the
owner before any declaration.

## Limitations (so far)

- **OTC stocks** aren't in CRSP, and many reverse splits happen there.
- **The broker round-up rate p** is unobservable in history.
- **Record dates aren't parsed.** Settlement is checked against the
  effective date, and a record date before it would be missed.
- **Only completed splits are in the event list.** A split announced and
  then abandoned isn't a CRSP event, so its losses are missing. Changes and
  postponements of completed splits are modeled.
- **CRSP factor mismatches:** 50, listed in the git-ignored data/rsplit/.
- **The validation reviewers worked from excerpts.** The full filing was
  read only for the disagreements.
