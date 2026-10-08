# Methodology

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
