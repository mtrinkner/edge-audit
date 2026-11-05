# Reverse-split round-up arbitrage (candidate trial 60)

**Status: KILLED at the Phase 2 gate. Not registered as a trial.** The
declared rule earns $7 a year per account in the design period (2010-2018)
against a $500 bar, and even the perfect-foresight ceiling earns $12. The
design-period P(pass) is 0.04 against a 0.25 bar. The pre-committed audit
found no errors. The confirmation period (2019-2025) was never priced. The
rules and audit plan below were committed (c419fff) before the first payoff
was computed. The result is in [Gate result](#gate-result-design-period-final).

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

## Gate result (design period, final)

Run on code frozen at c419fff. The first run and the post-audit rerun are
identical; the rows are in `reports/rsplit_gate.csv`.

| Row | Events | $/yr | Monthly Sharpe | P(pass), 7 yr | Median $/event |
|---|---:|---:|---:|---:|---:|
| Ceiling: p = 1, cheapest entry day, perfect foresight | 26 | 11.90 | 1.13 | 0.066 | 2.05 |
| **Primary: declared rule, p = 0.8 / 0.4** | **14** | **7.12** | **0.96** | **0.042** | **3.70** |
| p = 1 | 14 | 9.64 | 0.96 | 0.042 | 4.62 |
| p = 0.5 | 14 | 4.87 | 0.95 | 0.041 | 2.33 |
| p = 0.2 | 14 | 2.01 | 0.93 | 0.039 | 0.96 |
| $5 a trade | 14 | −6.66 | −0.87 | 0.000 | −5.30 |
| Exit at the open minus half the spread | 14 | 6.33 | 0.92 | 0.038 | 4.44 |
| Exit 5 trading days later | 14 | 6.87 | 0.96 | 0.042 | 3.03 |

- **(a) KILL:** ceiling $12/yr and rule $7/yr, against $500.
- **(b) KILL:** halved Sharpe 0.48, P(pass) 0.042, against 0.25.
- **(c) Return on capital:** median +408% an event at the primary p, on
  about $0.10-$7 of capital. Every primary event made money (the worst made
  $0.81). The trade works; there are just too few events and each is a
  one-share position.
- **Break-even p** is about 0 (−0.01 for per-holder, −0.02 for participant)
  in every row. Even the cash-in-lieu leg didn't lose money on these events,
  so any p > 0 pays. The round-up probability doesn't decide the result;
  event count does.
- **A $5 commission** ($10 a round trip) wipes out the trade, since the
  median gain is $3.70.
- The 5 best of the 14 events made 65% of the profit. No exits came from
  changed terms or missed settlement.

### Why so few trades

136 design-period events have a round-up class known the day before the
split. Of those, 14 were traded:

| Outcome | Events |
|---|---:|
| No pre-split 8-K stating both the ratio and the effective date (the declared announcement rule) | 95 |
| Announced too late: 3 or fewer trading days before the split, so no settling entry day | 9 |
| Announcement flagged as cancelled by its own filing (see limitations) | 12 |
| Other with no entry day (an effective date that moved the window) | 4 |
| No valid quote on the entry day (stub) | 2 |
| **Traded** | **14** |

**Upper bound with no rule losses:** all 136 events at the primary median of
$3.70 come to $56/yr, a ninth of the bar. Fixing the cancellation false
positive (12 events, about $5/yr) or loosening the announcement rule can't
change the verdict.

### Symmetric audit result

The pre-committed sample (20 best, 20 worst, 20 random, seed 62) covers all
14 traded events, since there are fewer than 20. I checked each one against
its filings and the CRSP daily rows.

| Class | Events |
|---|---:|
| VALID | 14 |
| WRONG-CLASS, WRONG-RATIO, WRONG-DATE, ENTRY-AFTER-CUTOFF, PRICE-ERROR, NOT-A-SPLIT | 0 |

Notes from the audit, all still VALID:
- **Effective date off by one trading day** in 3 events (BankAtlantic 2011,
  OptimumBank 2016, InVivo 2018). The 8-K date was the legal effective time;
  trading on the split basis started the next day. Entry settled before both
  dates, so the threshold (more than 1 day) isn't reached.
- **Exit moved past a stub quote** in 2 events (Good Times 2010, OptimumBank
  2016), as the rule says.
- **Halcon 2012:** the preliminary 14C said cash in lieu. The definitive 14C
  and the 8-K said round up, and the class known at entry came from those.
- **McDermott 2018:** labeled participant-level because the 8-K's round-up
  sentence refers to holders of record. The CASH label on a post-split 8-K
  came from CB&I merger consideration, not the split, and wasn't used.

No class reaches 3, so no fix was allowed, and the rerun was final.

### Is the opportunity shrinking in 2024-2025?

Only counts are reported here; no confirmation-period payoff was computed.

| Year | 2016 | 2017 | 2018 | 2019 | 2020 | 2021 | 2022 | 2023 | 2024 | 2025 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Round-up events | 29 | 15 | 19 | 23 | 15 | 9 | 40 | 115 | 148 | 119 |
| With an entry day under the rule | 3 | 2 | 2 | 2 | 2 | 0 | 5 | 8 | 12 | 10 |

It's growing, not shrinking: 2023-2025 have 5 to 8 times as many round-up
events as 2010-2022. But even 12 eligible events at the design median is
about $44/yr. All 148 round-ups in 2024 at that median would be about $550,
reached only if every one were tradeable, in the busiest year on record.

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
- **The cancellation regex has false positives.** "abandon" matches the
  standard reservation that the board may abandon the split, so 12 design
  announcements were marked as cancelled by their own filing and not traded.
  Found after the gate ran. The error only drops events, never adds them, and
  the bound above shows the verdict holds without it. Not fixed: a
  post-outcome change, and the trial is killed.
