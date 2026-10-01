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
