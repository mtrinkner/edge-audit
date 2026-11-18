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

---

# Trial 60 (from 2026-11-17): event-level confirmation, by owner decision

The owner decided on 2026-11-17 to run this as trial 60 despite the
2026-11-02 gate kill. The override and its reasons go in the declaration.
This section records the design-period work done before it.

## Phase 1: the announcement rule (design period 2010-2018 only)

### Diagnosis: where the terms first became public

40 of the 95 design round-up events v1 couldn't announce (seed 63) were
checked by hand. Every filing the company made in the 180 days before the
split was read in full (`data/rsplit/full/`, git-ignored), except ownership
forms, Form D, S-8 and correspondence.

| Where the ratio and date first appeared together | Events |
|---|---:|
| An 8-K or 8-K exhibit accepted the last trading day before the split or later | 17 |
| An 8-K exhibit early enough to trade (EX-99.1, 5 trading days ahead) | 1 |
| An 8-K with the ratio and only an assumed date (a blackout notice) | 1 |
| A fixed ratio in a proxy, information statement or 8-K, with no date until the split-day filing | 6 |
| No pre-split EDGAR filing with the final ratio | 15 |
| A definitive 14C or DEF 14A with a fixed ratio and a date | 0 |
| A prospectus with both | 0 |

The 15 announced by a press release not filed on EDGAR, or only on or after
the split day. Off-EDGAR sources weren't searched. The counts are in
`reports/rsplit_trial60_diagnosis.csv`.

**The binding constraint is timing, not the filing type.** Most companies
file the terms the day before the split, when no trade can settle in time.
A wider rule recovers a few events, not most.

### Rule v2 (`python/reverse_split.py`, rule "v2")

- **Sources:**
  - every document of every 8-K and 8-K/A, whatever its items, including
    exhibits such as a press release filed as "EX-9.01" and certificates of
    amendment;
  - DEF and PRE 14A and 14C;
  - 424B prospectuses and S-1/S-3 registration statements, with
    amendments;
  - all filed from 120 days before the ex date to 10 days after.
- **Announcement:** the first filing, usable from the trading day after its
  acceptance, that states both:
  - a single ratio: no range, list or "up to";
  - an effective or split-adjusted trading date on or after its own
    acceptance date.
- **Which forms count:** a ratio counts from 8-Ks, definitive proxies and
  information statements, and prospectuses, but not from preliminary
  proxies. In a proxy, information statement or prospectus, the ratio must
  sit within 800 characters of the future date, because prospectuses list
  earlier splits.
- **Date wording:** the date written after "split-adjusted basis" wins over
  a legal effective time written before it. "N:1" and "N-to-one" ratios and
  "the amendment will be effective on [date]" are read.
- **Class:** a fractional-share sentence counts only near a mention of the
  reverse split. It doesn't count when it's about conversions, exercises,
  debentures, purchasers, investors, advances, service fees or dividends. A
  prospectus states no class.
- **Cancellation:**
  - only a sentence stating that the split has been abandoned, withdrawn,
    cancelled, postponed indefinitely, or will not be effected;
  - any condition, reservation or covenant ("if", "may", "would", "any",
    "deemed", "unless") disqualifies it;
  - it counts only in a filing later than the announcement.
  - Changed terms in a later filing remain a change of terms, as in v1.
  - v1's regex flagged 92 of the 136 design round-up events. A first v2
    regex still flagged 35 filings, all reservations or financing
    covenants. The final rule flags none in the design period.
- **Freeze guard:** the confirmation period's v2 filings are downloaded and
  parsed only from a clean checkout of the `trial60-frozen` tag.
- **v1 is unchanged** and still reproduces the gate.

Tests: `tests/test_reverse_split.py` covers each new source on verbatim
fixtures, range and "up to" ratios that must not count, past dates, the
class context, "N:1" ratios, and the cancellation rule.

### Hand validation of v2 (independent reviewers, blind to the parser)

| Round | Use | Announcement ±1 business day | Ratio | Class |
|---|---|---:|---:|---:|
| seed 64, 30 events | failed the 90% bar; its findings fixed the rule | 77% | 77% | 73% |
| **seed 66, 30 fresh events** | **untouched: parser md5 ca19f840 recorded before the draw** | **87% raw, 90% adjudicated** | **93%** | **93%** |

**Known weakness: two of the three agreement figures depend on a scoring
choice made after the reviews came back.** Neither changes a number in the
table, and both are stated so the table isn't read as cleaner than it is.
- **Announcement met the 90% bar only after adjudication.** Raw agreement
  was 26 of 30 (87%), below the bar. One of the four misses (W19), which the
  reviewer marked borderline, was adjudicated in the parser's favour, giving
  27 of 30 (90%), exactly at the bar.
- **Class depends on the comparison.**
  - My first scoring script compared the parser's class known the day
    before the split with the reviewers' class, which gives 77%.
  - Re-scored against what the reviewers were told to record (the class
    stated in filings accepted before the split day), it gives 93%.
  - The difference is the 8-Ks accepted on the last day before the split.
- **The comparison that matters for the trade** is the class known on the
  entry day: the last day whose trade settles before the split, the only day
  the class decides anything. On that basis agreement is 28 of 30 (93%).
  - The four cash notices (W11, W22, W28, W29) were accepted after the entry
    day, so neither the parser nor a reader could have known them then.
  - Sterling Financial's round-up wording came the day before the split.
    At entry, both the parser and the record show the proxy's
    participant-level wording.
  - The two misses, Tonix 2017 and One Horizon 2017, are round-up events
    the parser leaves UNKNOWN. They are not traded.
- **Announcement misses (4):**
  - two parser misses: "the NYSE has set the effective date … for
    February 27", and a ratio stated away from the word "reverse";
  - one event with an earlier, separate split in the same filing window
    (W10);
  - one the reviewer marked borderline. The filing has no split-adjusted
    date, so the parser's "none" holds on adjudication.
- **Every miss is safe**, whatever the comparison: an event left unannounced
  or UNKNOWN, a trade not taken. None is a false round-up or a false
  announcement, so the misses can cost events but can't add a bad trade.
- **Not fixed:** the seed-66 round is the measurement.
- **Overlap:** the earlier rounds' event lists weren't saved, so "fresh"
  means not drawn in this session's two samples.

### Design run, rule v2 (design work, not a gate decision)

`python3 python/rsplit_trial60.py design` writes `reports/rsplit_trial60_design.csv`.

| | v2 design |
|---|---:|
| Events traded | 36 (v1: 14) |
| Mean / median $ per event, primary p | 3.66 / 2.38 |
| Win rate | 97% |
| Event-level t, clustered by ex-date month | 5.82 |
| Top 5 events' share of profit | 39% |
| $ a year per account | 14.6 |

## Pre-committed symmetric audit of the v2 design run (fixed before any trade is inspected)

1. **Sample**, from `data/rsplit/trades_v2_design.csv`:
   - the 20 best by P&L at the primary p;
   - the 20 worst;
   - 20 more drawn at random from the rest, sorted by (ex date, permno),
     with `numpy.random.default_rng(65)`.
   - With 36 traded events, the best 20 and worst 20 cover all of them.
2. **Classes:** the seven from "Pre-committed symmetric audit" above,
   VALID through NOT-A-SPLIT, each with the filing accession or CRSP row as
   evidence.
3. **Fixes:**
   - only as rules applied to every event, never per-event exclusions;
   - only for a class appearing 3 or more times;
   - in both directions alike.
4. **Then the design run is repeated once** and recorded as final. A later
   bug is a documented correction next to the frozen result, never instead
   of it.

### Symmetric audit result (v2 design run)

The pre-committed sample covers all 36 traded events, since the 20 best and
20 worst overlap. Each was checked against its filings and CRSP daily rows.

| Class | Events |
|---|---:|
| VALID | 35 |
| WRONG-DATE (more than 1 trading day off) | 1 |
| WRONG-CLASS, WRONG-RATIO, ENTRY-AFTER-CUTOFF, PRICE-ERROR, NOT-A-SPLIT | 0 |

- **Hongli Clean Energy 2016 (WRONG-DATE):** the DEF 14C said the split
  would be effective "about October 24". Trading on the split basis began
  October 27. Entry settled before both, so the payoff isn't affected.
- **EMCORE 2012 (VALID by the threshold, with an error noted):**
  - the parser took the record date (February 15) for the effective date,
    one trading day early;
  - a later prospectus stating February 16 triggered a changed-terms exit at
    the bid, so the trade shows −$0.11 instead of the round-up;
  - this understates the result, and it occurs once.
- **Class sentences:** for PostRock, OptimumBank 2016 and Rennova the cited
  sentence alludes to the round-up rather than stating it. Each filing states
  the round-up elsewhere.
- **Latest filing wins:** for Graymark, Satcon and Alliance One an earlier
  proxy said cash in lieu or holders of record only, and the later 8-K said
  each holder is rounded up.
- **Stubs:** Good Times, Tengion and OptimumBank 2013 exits moved past stub
  quotes, as the rule says.

No class reaches 3, so no fix was allowed. The design run was repeated
once, and `reports/rsplit_trial60_design.csv` was byte-identical. That run
is final.

### Before the freeze

- **Freeze guard, changed once:** it was changed from "HEAD is the tag" to
  "python/ matches the tag, committed and uncommitted", so the declaration
  commit can follow the tag. The parser and the rules are unchanged since
  the seed-66 validation.
- **Freeze guard on cached outputs.** The code guard alone wouldn't stop a
  confirmation run from reading a parse cached by earlier code.
  - Every confirmation-period output that `reverse_split.py` or
    `rsplit_trial60.py` writes (the filing list, the parsed filings, the
    event table, the trades) now gets a sidecar `<file>.manifest.json`. It
    records the trial60-frozen commit, the md5 of `python/*.py`, the rule
    ("v2"), the period, and when the file was written.
  - Every confirmation-period read checks the manifest. A missing manifest,
    another commit, or a `python/` tree that has changed since the write is
    refused with "confirmation inputs not produced by trial60-frozen".
  - Four tests cover this: no manifest, another commit, an md5 mismatch, and
    a matching manifest, which passes.
  - Before the tag, the 2019-2025 rows of the v1 caches were moved to
    `data/rsplit/pre_freeze/`, kept, not deleted. That is 7,565 of the
    10,724 rows in `parsed.csv`, 1,829 of the 2,693 in `event_table.csv`,
    and 39 of the 50 in `factor_mismatches.csv`. The design rows stay in
    place, so v1 reproduces. Raw downloaded filings stay where they are.
- **A known conservative bias: a restated date exits the trade.** A later
  prospectus that restates the effective date one day differently counts as
  changed terms, and the position is sold at the bid. EMCORE 2012 shows −$0.11
  instead of the round-up for this reason.
  - It occurred once in the design run, below the 3-occurrence bar, so no
    fix was allowed.
  - Expect it in the confirmation run too. The confirmation summary reports
    the number of changed-terms exits (`changed_terms_exits` in
    `reports/rsplit_trial60_confirm_summary.csv`).
- **The confirmation period was not untouched:**
  - on 2026-11-02 the v1 parse read the 2019-2025 filings;
  - this document published per-year class counts, and v1 entry-day counts
    totalling 39 for 2019-2025, in "Is the opportunity shrinking";
  - no 2019-2025 price or payoff has ever been computed.
- **MDE inputs, from EDGAR filing metadata only.** 2019-2025 has 2.31× the
  design events with a CIK, 2.36× the pre-split 8-Ks, and 3.59× the
  pre-split 8-Ks with item 5.03. On the smallest scale the expected number
  traded is about 83.
- **MDE:**
  - with the design SD of $3.50 an event, iid, the mean that gives t = 3 is
    $1.15, or $1.48 with 80% power;
  - clustering by ex-date month widens both;
  - the design mean was $3.66.

## Phase 4: confirmation result (2019-2025, run once on 2026-11-18)

Declared as registry trial 60 (`rsplit_roundup`, family structural, 1
variant) before any 2019-2025 price was read. Run once at tag trial60-frozen
(commit 0b9adfc, `python/` md5 5c0f7469…). Every confirmation input carried a
manifest naming that commit. Recorded exactly as it came out.

**Verdict: PASS.** All five declared criteria hold.

| Check | Bar | Result |
|---|---|---|
| (a) mean, clustered t | mean > 0, t ≥ 3 | $3.17 an event, t 14.06 (46 month clusters) |
| (b) without the 5 largest | mean > 0 | $2.87 |
| (c) p = 0.2, both classes | mean > 0 | $0.86 |
| (d) 2024-2025 | mean > 0 | $2.83 (88 events) |
| (e) events traded | ≥ 30 | 121 |

Reported, not criteria (`reports/rsplit_trial60_confirm.csv` and
`_summary.csv`):

| | |
|---|---:|
| Win rate / median / 10th percentile / worst | 97.5% / $2.83 / $0.70 / −$0.08 |
| Median return on capital an event | 8.6× |
| p = 1 | $4.24 an event |
| $5 commission | **−$5.64 an event** |
| Exit at the open / 5 days later | $3.27 / $3.05 |
| Holder class (106) / participant class (15) | $3.35 / $1.84 |
| $ a year per account | 54.7 |
| Monthly Sharpe (84 months) / deflated Sharpe at N = 857 | 2.59 / 0.999 |
| Changed-terms exits (the known conservative bias) | 7 |

By year, events traded: 2019 5, 2020 5, 2021 1, 2022 6, 2023 16, 2024 45,
2025 43. Every year's mean is positive.

The pipeline counts before pricing were:
- 1,829 events;
- 514 with a round-up class known the day before the split;
- 281 announced under v2;
- 0 flagged cancelled.

Those counts ran above the metadata-based expectation. The MDE assumed about
83 traded events and 121 traded. The code was frozen, so nothing was changed
for it.

### Correction, 2026-11-18: dollars a year by row (report format only)

`rsplit_trial60.score()` divided every row's dollar total by the whole
period's length (7 years for the confirmation, 9 for the design), so the year
rows and the 2024-2025 row understated their own dollars a year.
`python/rsplit_report.py` now adds `years_covered` (1 for a year row, 2 for
2024-2025, the period's length otherwise) to both report CSVs. It recomputes
`usd_per_yr = events × mean / years_covered` from each row's own aggregates.

- **Unchanged:** no payoff, price or frozen function was touched; every
  other cell of the two CSVs is byte-identical; the pass checks don't use
  `usd_per_yr`. The verdict and every number in the table above stand.
- **Period rows** move by about a cent, since 7 years replaces 2,556
  days ÷ 365.25. The primary row goes from $54.74 to $54.72.
- **Rows that change meaningfully:**

| Row | As run ($/yr) | Corrected ($/yr) |
|---|---:|---:|
| 2024-2025 (88 events) | 35.6 | **124.6** |
| 2025 (43 events) | 20.1 | 140.8 |
| 2024 (45 events) | 15.5 | 108.3 |
| 2023 (16 events) | 9.0 | 62.6 |

The whole-period figure, about $55 a year, averages over 2019-2022, which
had 1 to 6 events a year. At the 2024-2025 event rate the same one-share
rule made about $125 a year per account.

### What the pass does and does not establish

- **Established:** on 2019-2025, after the rule was frozen and declared, a
  share bought before a round-up split and held through it made money on
  97.5% of events at the assumed p. It is still positive if brokers pass the
  round-up through only 20% of the time.
- **Not established: p itself.** Whether a retail broker actually delivers
  the round-up is assumed (0.8 / 0.4), not observed. No public source
  measures it. Check (c) bounds the damage but doesn't measure it.
- **Cost-dependent.** At a $5 commission every event loses. The trade exists
  only at a $0-commission broker.
- **Small in dollars.** One share an account, about $55 a year at 2019-2025's
  event rate. It is a real edge and not an income.
- **Concentrated in time.** 88 of the 121 events are in 2024-2025, the years
  this trade became widely discussed. The 2024-2025 mean is still positive.
  Whether issuers respond by choosing cash in lieu is for the forward
  ledger to show.

## Phase 5: forward plan (nothing built)

The one unknown the backtest can't answer is p. The forward test measures it
and, separately, checks that the frozen rule's live signals match what
happens.

1. **Signal ledger (paper, automated):**
   - Run the frozen v2 parser weekly on new EDGAR filings.
   - Write each event it would trade to a ledger: the announcement
     accession, ratio, effective date, class and the entry day it chose.
   - Do this before the split, with no price data.
   - After the split, add the CRSP or vendor outcome. No change to the rule
     is allowed. A bug becomes a documented correction.
2. **Broker outcome (manual, done by the owner, not by the code):**
   - For a pre-registered set of upcoming events, the owner may choose to
     hold 1 share at a $0-commission broker through the split.
   - Log whether the broker delivered a whole share, cash, or nothing.
   - This is the only way to observe p. The log records the broker name and
     each outcome.
3. **Pre-registered read-out:**
   - After 20 broker outcomes, report the observed delivery rate with a
     binomial interval.
   - The trade remains positive in expectation if the rate's lower bound is
     above the p at which the confirmation mean reaches zero. That p is
     below 0.2, from check (c).
   *Built 2026-11-18: `python/rsplit_ledger.py`, tests in
   `tests/test_rsplit_ledger.py`.*
   - It finds companies through EDGAR full-text search on "reverse stock
     split", "reverse split" and "share consolidation", in the forms v2
     reads.
   - It keeps NYSE, Nasdaq and NYSE American listings, using EDGAR's exchange
     field.
   - It reads each company's last 120 days of v2 filings with the frozen
     functions, imported and unchanged.
   - It appends a row to `data/rsplit/forward/ledger.csv` (git-ignored) only
     when a split is new or what is known about it changes.
   - The calendar extends the frozen one with NYSE trading days through 2027.
   - No prices are read. `summary` prints counts only.
   - Run it every trading day: the entry day is only the last day that
     settles before the split, and a short window can fall between weekly
     runs.
   - Adding this file changed the `python/` md5, so the confirmation's cached
     inputs now refuse to be re-read. That is the guard working: the
     confirmation ran once, at the tag, and the tag still reproduces it.
4. **Stop rules:**
   - Stop if issuers' cash-in-lieu share among new splits rises enough that
     fewer than 1 qualifying event a month appears for 6 months.
   - Stop if the observed delivery rate's upper bound falls under the
     break-even p.
