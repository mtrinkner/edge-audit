# Methodology

## The goal

The search has two targets, in order. A study is judged against the first
before anyone asks about the second.

**Tier 1, REAL.** A pre-registered trial passes its declared test with
t ≥ 3. The test is declared before any confirmation-period outcome is seen.
The trial is counted in the registry, and its Sharpe is deflated by the
number of trials, as described in "Multiple comparisons" and "Scoring rules"
below.
- Achieved once: trial 60, reverse-split round-ups, 2019-2025. Its clustered
  event-level t was 14.06 (registry entries for trial 60;
  docs/REVERSE_SPLIT_ROUNDUP.md).

**Tier 2, MEANINGFUL.** At least $1,500 a year on a $25,000 account, after
all costs. It can come from one edge or from a declared book of edges, and
must be confirmed out of sample.
- A book is declared as a book: its components, how capital is split between
  them, and the combined test, all fixed before its confirmation run.
- A book can't be assembled after the fact from components that passed
  separately.
- Not yet achieved. Trial 60 makes about $55 a year over 2019-2025 at one
  share per event, or about $125 a year at the 2024-2025 event rate.

**Constraints**
- **t ≥ 3 stays.** It is the bar for every confirmation, standalone or
  book.
- **"Standalone" is relaxed: books are allowed.** A small edge that is real
  (Tier 1) can count toward Tier 2 as a component of a declared book.
- **"Public data" is relaxed over time.** Getting better data (licensed
  databases, vendor feeds) is a parallel track. A trial on new data still
  follows every rule here, and the data's terms decide what can be
  committed.

## Point-in-time discipline

A feature on date *t* may read data from dates ≤ *t*. A label may read data from
dates > *t*. Features live in `sql/02_features.sql`, labels in `sql/04_labels.sql`,
and the separation is structural so it can be audited by reading one file.

Every window carries an explicit `ROWS BETWEEN n PRECEDING AND CURRENT ROW`
frame. SQL's default frame is `RANGE`, which includes peer rows and is not what
anyone means by "the last 20 days".

Windows that cannot be fully formed yield NULL rather than a partial average.
A 14-day ATR computed from 13 observations is a different statistic wearing the
same name.

## Validation protocol

**Walk-forward, not k-fold.** Random folds place future observations in the
training set and inflate every metric. Folds here are contiguous: four years
train, one year test, stepped forward one year at a time.

**Purge and embargo.** The label looks 5 days forward, so training rows within 5
days of the test window overlap it and are removed (purge). A further 5-day
embargo after the test window guards against serial correlation bleeding across
the boundary. Both are set in `python/config.py`.

**Baselines first.** Every model is reported against the 53.6% base rate and
against a simple rule, not against zero. A classifier that beats nothing is not
a result.

## Multiple comparisons

Testing many variants and reporting the best one guarantees a good-looking
result whether or not an edge exists. The R layer therefore records how many
variants were tested and applies a correction for it, and the count of tested
variants is reported alongside any performance figure.

## Statistical significance of returns

Return series are serially correlated and fat-tailed, so a plain t-test
overstates significance. The R layer uses a stationary bootstrap, which resamples
blocks and preserves short-range dependence, to build the null distribution.

## Cost treatment

Costs are applied inside the label definition rather than subtracted from results
afterward, so the model never learns to value an edge smaller than its own
execution cost.

## Reproducibility

`python3 build.py --ingest` rebuilds everything from source. Views define feature
logic; materialized tables are derived artifacts that can always be regenerated.
Random seeds are fixed in `python/config.py`.

**Two test tiers** (`pytest.ini`), in the pinned environment:
- **Fast, on every change:** `pytest -m "not slow"`.
- **Full, before every declaration and every confirmation run:** `pytest`.
  The `slow` marker covers the look-ahead audits that rebuild or rescan
  cached data: the SQL truncation rebuild, the intraday future-swap audit and
  the event-study truncation audits.

---

# Methods added during the work

## Factor attribution, applied to every candidate

A raw Sharpe stopped being interesting once the earnings result was traced to its
factor exposures. Every candidate is now regressed cross-sectionally, each day,
on market beta, size and 126-day momentum, and judged on the residual.

The earnings score correlates +0.104 with 126-day momentum, because companies
that beat expectations have usually already risen. Its book went from 0.34 to
-0.17 under neutralization. A signal that cannot survive that regression is a
repackaged factor tilt, whatever its headline number.

## Mechanism before pattern

The most useful reframe in the project came from a practitioner forum rather than
a paper: an edge should be *"a reason someone pays you"*, meaning a participant
structurally forced to transact at a bad price.

This is not decoration. A mechanism makes a prediction about the SHAPE of an
effect, and that prediction is testable. Forced buying must concentrate in time
near the forced event. When the S&P addition run-up was examined on that basis it
stretched back a full year (+17.5% from -250 to -126 sessions), which no forced
buyer can produce. It was the index committee's selection rule. A pattern-first
approach would have reported +4.65% with t = 5.26 and called it an edge.

## Pre-commitment before extending a sample

Extending a sample for an existing hypothesis adds evidence and is not a new
trial. But choosing the window after seeing both results is snooping.

So before pulling the longer short-interest history, a pre-commitment was written
to the hash-chained ledger: whatever the full sample says is the answer, with no
keeping the window that looks better. It then said 0.24 against 1.66. Without the
commitment on record beforehand, the pull-back-and-report-the-better-window
temptation would have been very hard to resist, and the resulting sentence would
have been technically true and substantively a lie.

## Separating a risk premium from an edge

A risk premium is compensation for bearing a risk and is paid to whoever bears
it. An edge is a mispricing and is paid to whoever identifies it.

The overnight equity premium is the first kind: 62% of the total return and 38%
of the variance accrue overnight, but the breakeven half-spread for harvesting it
by trading is 2.01bp against a large-cap cost of 2.1bp. The market charges
almost exactly what it pays. You collect it by holding, which buy-and-hold
investors already do.

Crypto funding carry is also the first kind, and visibly being competed away:
23.5% annualized in 2024, 6.3% in 2025, 1.7% in 2026.

Trial 44 tested this directly. Executed through the opening and closing
auctions, the overnight premium is capturable after all: +4.88bp a night, t=3.25,
Sharpe 0.914 over 2014–2026. But holding the same stocks scored 0.938. Being
able to collect it doesn't make it an edge, and the pass bar now requires a
strategy that is long the market part of the time to beat simply holding.

## Scoring rules since the 2026-10-14 audit

Every new study goes through `python/evaluation.py`, which exists because each
of these went wrong at least once:

- **Size every trade before it happens.** Use a fixed notional per trade, or a
  rule that only uses signals fired so far. Splitting a day's capital across all
  of that day's signals needs the end-of-day count, and it produced a Sharpe of
  1.52 from a strategy whose causal Sharpe was −1.27.
  `tests/test_causal_weights.py` checks any sizing rule by deleting future
  trades and confirming past weights don't move.
- **Test the quantity you report.** Use the per-trade mean, with standard errors
  clustered by session, because a market day is one shock, not thousands.
- **Annualize by the series' own frequency.** The registry now records it, after
  a daily series was scored as weekly and printed "clears the bar".
- **Report what a test could have seen.** Give the minimum detectable effect next
  to every null.
- **Report the break-even cost, not just the net.** No spread on these names was
  actually measured, so a verdict shouldn't depend on one. One cent per share is
  the only hard lower bound.
- **Name the information family when declaring a test.** 76% of trials went to
  price patterns before anyone counted.

## Pre-declaration gate, 2026-10-28 to 2026-11-18 (superseded)

*Replaced by "Pre-declaration gate (since 2026-11-18)" below. It is kept
because four kills were decided under it. Check (c), benchmark fit, carries
over unchanged.*


Trials 10, 12, 14, 34, 58 and 59 were each declared without asking first
whether a pass was likely or worth having. Trial 58's edge was real and worth
$200 a year; trial 59 needed a Sharpe of 0.83 from an anomaly whose cited
post-publication Sharpe was well under that. From trial 60 on, every
declaration must pass three checks, and the numbers go into the declaration.

**a. Economic size.** Trades a year × capacity per trade × expected net edge
per trade, computed only from entry-time data (prices, spreads, volume and
deal terms known on the entry day) and from literature priors for anything
that depends on outcomes. Never from the outcomes themselves. The
declaration states the kill threshold in dollars a year and the account size
it assumes.

- **Size the ceiling first.** Perfect-foresight entries, limited only by
  capacity. If the ceiling fails, kill. If only the declared rule fails,
  redesign the rule before declaring. (Trial 60's first gate sized only the
  rule: $186 a year against a ceiling of $1,213.)
- **Required excess return = dollar bar / account.** Screen out any class
  whose typical excess yield over T-bills is below that before building
  anything. A $1,500 bar on $25k needs 6% a year over T-bills.
- **The dollar bar is set per study** and written in its declaration. A
  research-grade study may set it low, and says so.

**b. Pass probability.** With a cited prior Sharpe S, after realistic
post-publication decay and costs, and T confirmation years:

    P(pass) = 1 − Φ(3 − S·√T)

`python3 python/deflated_sharpe.py --power --prior-sharpe S --years T`
prints it. Don't spend a trial when P(pass) < 0.25. At 13 years that means
S < 0.65.

**c. Benchmark fit.** The benchmark has the same weighting and universe as
the portfolio. Trial 59's V2 compared an equal-weighted long leg of mid-caps
and up with the value-weighted market, so it measured a size spread as much
as the anomaly. An equal-weighted portfolio is compared with the
equal-weighted universe it is drawn from; a strategy that holds cash part of
the time is compared with T-bills.

If any check fails, the numbers are written into the study's doc, nothing is
registered, and the trial is not spent.

## Pre-declaration gate (since 2026-11-18): G-REAL and G-WORTH

The old gate asked one question with two numbers. It judged an event trade
on a monthly calendar-time Sharpe, so months with no events added zeros and
destroyed power. It also killed trial 60, which then passed its confirmation
at t = 14. The new gate asks two separate questions. Code: `python/gate.py`;
tests: `tests/test_gate.py`.

### G-REAL: can a pre-registered test confirm it?

1. **Use the statistic the trial will declare as primary.**
   - An event trade uses the event-level t, standard errors clustered by
     ex-date month.
   - A portfolio uses the calendar-time t of monthly returns.
2. **Prior t, from one of two sources.**
   - **A cited published estimate, after decay and costs.** For a
     portfolio, prior t = Sharpe × √(confirmation years).
   - **The design estimate, corrected for search:**
     prior t = (design t − E[max of K standard normals]) × (1 − 0.26).
     K is the number of rule variants actually evaluated on the design
     period. K = 1 subtracts nothing, and K = 20 subtracts 1.87.
   - The shrink comes from McLean and Pontiff (2016), "Does Academic
     Research Destroy Stock Return Predictability?", *Journal of Finance*
     71(1), 5-32. Returns are 26% lower out of sample and 58% lower after
     publication. It replaces "halve the design Sharpe". Which one applies is
     set by the shrink rule below.

   **The shrink rule** (fixed 2026-11-18, before the calibration table was
   computed):
   - **Regime = replication (58%)** when the design period overlaps the
     sample period of the specific paper whose signal definition the study
     uses. **Otherwise own_design (26%).**
   - Each calibration row cites that paper and its sample years, taken from
     the paper itself, not from memory.
   - The regime follows from what the design period is and is recorded
     before any P(pass) is computed. No other gate parameter (the t bar,
     the 0.25 line, the K correction, the scaling) is tuned to the
     calibration table. With about 7 cases, fitting would overfit the gate.
3. **Scale to the confirmation:**
   scaled t = prior t × √(expected confirmation n ÷ design n).
   - n is events for an event trade and months for a portfolio.
   - The expected confirmation n comes only from information known before
     pricing: filing counts and years.
4. **P(pass) = 1 − Φ(3 − scaled t).** Declare only if P(pass) ≥ 0.25.

`python3 python/gate.py real --kind event --design-t T --k K --n-design N --n-confirm M`
or `--kind portfolio --cited-sharpe S --years Y`.

### G-WORTH: is it worth having?

- **Tier 2 standalone:** at least $1,500 a year on $25k. Size the ceiling
  first (perfect foresight, limited only by capacity). A ceiling under the
  bar kills; otherwise the rule itself must clear it.
- **Book component:** at least $100 a year, and the declaration reports how
  much of its capital would compete with the book's existing pieces.

`python3 python/gate.py worth --usd U --ceiling C` or
`--role component --overlap F`.

### When a trial may be declared

- G-REAL passes and G-WORTH passes, or
- G-REAL passes and the owner declares it research-grade in writing. That
  covers Tier 1 only: a research-grade pass is real but not, on its own,
  meaningful.

Before either, `docs/SPEC_CHECKLIST.md` is filled in with evidence and
committed. Benchmark fit, check (c) of the old gate, still applies.

### Re-screen of past kills under the new gate (2026-11-18)

Every past kill, recorded symmetrically. Nothing is declared from this
table. Inputs: `data/gate_rescreen.csv`, each with its source; output:
`reports/gate_rescreen.csv` (`python3 python/gate.py rescreen`).

| Idea | Old verdict | Statistic | K | Prior t | Scaled t | P(pass) | G-REAL | G-WORTH standalone | G-WORTH component | New verdict |
|---|---|---|---:|---:|---:|---:|---|---|---|---|
| Reverse split v1, 2026-11-02 design numbers | kill: $7/yr; P(pass) 0.042 | event, clustered | 1 | 3.78 | 5.74 | 0.997 | **pass** | fail (ceiling $12) | fail ($7 < $100) | research-grade only |
| Reverse split v2, trial 60 as declared (reference) | declared by owner override | event, clustered | 2 | 3.89 | 5.91 | 0.998 | **pass** | fail ($15) | fail ($15 < $100) | research-grade only |
| SPAC trust arb, run 4 | kill: P(pass) 0.025 | calendar monthly | 1 | 2.02 | 1.41 | 0.056 | fail | fail ($278) | pass ($278; 30% overlap) | kill |
| Merger arb, cash tenders | kill: P(pass) 0.172 | calendar monthly, cited | – | 0.87 | 0.87 | 0.016 | fail | fail ($540) | pass ($540; 100% overlap) | kill |

Reasons:
- **Reverse split v1 (the trial 60 regression).** The design's 14 events
  had a clustered event t of 5.11 (12 clusters).
  - K = 1, so prior t = 5.11 × 0.74 = 3.78.
  - The expected confirmation n is 14 × 2.31 (EDGAR events with a CIK), so
    the scaled t is 5.74 and P(pass) is 0.997.
  - The old gate's P(pass) of 0.042 came from the wrong statistic, a monthly
    Sharpe over 108 mostly empty months.
  - G-WORTH fails both ways. Under the new rules, the owner's research-grade
    override was the only route, and it was the route taken. Trial 60 then
    passed.
- **Reverse split v2.** K = 2, because v1 and v2 were both run on design
  trades, so 0.56 is subtracted before the shrink. It still passes G-REAL.
  No v2 ceiling was computed.
- **SPAC run 4.** The prior t is (2.73 − 0) × 0.74 = 2.02. The confirmation
  has 60 months against the design's 123, which scales it to 1.41 and
  P(pass) to 0.056. The kill stands, on G-REAL.
  - As a book component it would clear $100 a year. Its average capital use
    is 30% of the account.
  - Seen data: the 2021-2025 opportunity counts printed on 2026-10-29.
- **Merger arb.** The new rule requires the cited prior after decay.
  Mitchell and Pulvino's 0.57, with McLean and Pontiff's 58%
  post-publication decay, is 0.24, which gives t 0.87 over 13 years and
  P(pass) 0.016. Even the undecayed 0.57 gives 0.172. The kill stands.
  - As a book component its base case ($540 a year) would clear $100, but
    five $5,000 slots use the whole account, so it competes with everything.

**Queue for step 4.** No past kill becomes declarable outside the
research-grade route. The only G-REAL pass is the reverse-split round-up,
which is already trial 60. Its Tier 2 role would be as a book component,
which needs at least $100 a year. It made about $55 a year over 2019-2025,
and about $125 a year at the 2024-2025 event rate, on one share. Whether that
qualifies is a step-4 question, and it carries a seen-data disclosure:
2019-2025 has now been fully priced.

