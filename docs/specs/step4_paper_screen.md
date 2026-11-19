# Step 4: the paper screen (protocol, 2026-11-18)

Three candidates are screened through G-REAL and G-WORTH together, then
ranked. Reverse split v2 is already in the queue as a book component (the
re-screen in docs/METHODOLOGY.md). This protocol is committed before any
candidate's number is computed. New ideas join the queue after these three;
they don't jump ahead.

## What a paper screen uses, and what it doesn't

- **Used:**
  - the published estimate, read from the paper itself, with its sample
    years, which set the shrink regime;
  - opportunity counts from EDGAR filing metadata (form, date, filer),
    which hold no prices;
  - fee schedules and the $25k account.
- **Not used:** any price or return for these events. No CRSP pull is
  joined to an event date. The screen decides whether a trial is worth
  declaring, so the trial's data stays unseen.
- **Disclosed:** every count or figure already seen, with its date (spec
  checklist item 7).

## The inputs for each candidate

1. **Mechanism:** what pays, when, and the condition at entry (checklist
   item 1).
2. **Kind:** event trade or portfolio, which fixes the statistic (G-REAL).
3. **Prior:** the paper's effect and its sample years.
   - **Shrink (revised 2026-11-19):** a cited estimate is the paper's
     in-sample result and always takes the 58% post-publication decline
     (McLean and Pontiff 2016). The first version of this protocol chose
     26% or 58% by design overlap. That rule now applies only to our own
     design estimates.
   - For a portfolio, the cited Sharpe goes through the existing path.
   - For an event trade, `g_real_cited_event` takes the cited mean and SD
     per event: prior effect = mean/SD × (1 − 0.58), scaled t = effect ×
     √n_eff.
   - **n_eff (revised 2026-11-19):** the design effect
     n ÷ (1 + (m − 1) × ρ), with m the expected events per occupied month
     and ρ = 0.05 (primary) or 0.20, since there is no design data. The
     events-only t is the upper bound. The first version used the smaller
     of the events and the expected clusters, which treated every month as
     one event.
   - **Retail short side (added 2026-11-19):** with no borrow data, only a
     long-side effect counts, unless the paper reports borrow-adjusted
     returns. A short-only candidate is "not tradeable retail" and gets no
     P(pass).
   - **No published estimate (revised 2026-11-19):** the candidate gets a
     design study on 2010-2018 only (prices allowed in design), on
     pit/sim/report, and then goes through G-REAL by the design-t path. It
     becomes a trial only if declared.
4. **Rate:**
   - eligible events a year from EDGAR counts, 2019 to 2026Q3;
   - the confirmation n: the events a year times the confirmation's years.
5. **Dollars:**
   - the cited net return per event, times the position the $25k account
     can take;
   - times the rate, capped by capital, which gives the projection;
   - the gross return before costs gives the ceiling.
6. **Seen data:** for each input, what has been seen and when.

## Ranking

1. G-REAL must pass (P(pass) ≥ 0.25).
2. G-WORTH must pass, standalone ($1,500) or as a component ($100).
3. Order by P(pass), then by lower capital overlap with the book.
   - The top one goes to step 5 as a pre-registered trial.
   - If none pass, each failure is recorded with its binding reason. The
     queue goes on with the next ideas.

## The candidates and the data each needs

| Candidate | Mechanism | Count source | Prior to read |
|---|---|---|---|
| Closed-end fund tender offers | buy a listed CEF at a discount after it announces an issuer tender near NAV; tender; prorated shares exit at market | SC TO-I (cached in data/edgar/index) restricted to **exchange-listed** CEFs | papers on CEF tender offers and discount arbitrage |
| Small cash merger arb | buy the target of a cash tender offer below a size cutoff; the spread closes at completion | SC TO-T (cached in data/edgar/index_tot), originals only, by target size | evidence that spreads or returns are larger for small targets |
| Micro-cap distress events | the drift after 8-K Item 3.01 (delisting notice) or NT 10-K | NT 10-K: full form.idx; 8-K 3.01: submissions `items` | the post-event drift papers; mostly short side, so borrow cost and availability are binding |

Going-concern opinions are dropped (owner, 2026-11-18): there is no free
structured source, and a text search for "substantial doubt" is too noisy
for a count.

Data approved for the screen (owner, 2026-11-18):
- **WRDS:** one connection, identifiers only: CRSP share code 14 for listed
  CEFs, and target market caps. No prices.
- **SEC:** the full quarterly form.idx for 2010-2026Q3, about 2.5 GB, for
  NT 10-K counts; submissions `items` for 8-K 3.01. Same User-Agent and
  cache.

## The metadata seen so far (2026-11-18, while writing this protocol)

- **SC TO-I originals:** 2010-2026, 300-525 a year from fund-like filer
  names, 101-177 distinct filers a year.
  - Most are non-traded interval and tender-offer funds that repurchase
    every quarter. They aren't tradeable.
  - The listed-CEF subset isn't known yet.
- **SC TO-T originals:** 88-116 a year in 2019-2025. That figure counts
  the bidder's filings, not distinct deals.
- **Prices:** none seen.

## First run (2026-11-18, under the original rules; superseded by the rerun below)

Inputs: `data/screen_step4.csv`, each row with its source and arithmetic.
Output: `reports/screen_step4.csv` (`python3 python/screen_step4.py screen`).
No price or return for any of these events was read.

| Candidate | Row | Prior (mean / SD per event) | Regime | Scaled t (events / clusters) | P(pass) | G-REAL | Projection $/yr | Ceiling $/yr | Binding |
|---|---|---|---|---:|---:|---|---:|---:|---|
| CEF tender offers | **primary**: Kadapakkam et al. 2000-2015 | 0.50% / 5.0% | replication | 0.46 / 0.34 | 0.004 | fail | 2,963 | 10,606 | G-REAL |
| CEF tender offers | Gray 1999-2003, after his costs | 0.84% / 5.0% | own design | 1.36 / 0.99 | 0.022 | fail | 4,977 | 10,606 | G-REAL |
| CEF tender offers | Gray, gross (ceiling) | 1.79% / 5.0% | own design | 2.89 / 2.12 | 0.188 | fail | 10,606 | 10,606 | G-REAL |
| NT 10-K short | **primary**: Bartov-Konchitchki, net of assumed costs | 5.41% / 82.9% | own design | 1.40 / 0.44 | 0.005 | fail | 2,700 | – | G-REAL |
| NT 10-K short | gross (ceiling) | 9.41% / 82.9% | own design | 2.43 / 0.77 | 0.013 | fail | 4,700 | – | G-REAL |
| Small cash merger arb | all-deal Sharpe 0.24; size evidence points lower | – | cited | 0.87 | 0.016 | fail | 540 | – | G-REAL |
| 8-K Item 3.01 short | no published drift estimate | – | – | – | – | not screenable | – | – | no prior |

- **Every candidate fails G-REAL.** All the dollar figures that could be
  computed would clear the bars, as a component (CEF, NT 10-K, merger arb)
  and standalone (CEF, NT 10-K). Size isn't the problem: none has a
  published effect strong enough for a test to confirm.
- **The primary rows were chosen by a rule, not by result.** For CEF, the
  most recent and largest sample, Kadapakkam, Zhang & Yildirim (2021), is
  primary. Gray (2005) is a 22-offer sample from 1999-2003, and his
  figures are shown as sensitivity rows. Even Gray's gross figure fails
  under the cluster scaling. On events alone it would give t 2.89 and
  P(pass) 0.46, but it is gross and 20 years old, and the later paper
  found 0.5%.
- **The NT 10-K cost assumption** (2.5% spread and commission, 1.5% borrow)
  is this screen's own and was stated before computing. The gross ceiling
  fails too, so the verdict doesn't depend on it.
  - The SD is implied from the paper's p-value, at 83% per event. The
    signal is very noisy.
  - Bartov and Konchitchki's firms have a median market value of $183M,
    so they aren't micro-caps.
- **Small cash merger arb:** no paper gives post-2000 returns for small cash
  tender offers.
  - The one return-by-size result, Baker and Savasoglu (2002), goes the
    other way: returns rise with target size.
  - Jetley and Ji (2010) find wider spreads for small targets, but
    explain them by trading and information costs.
  - A small-deal tilt has no prior better than the all-deal 0.24.
- **8-K Item 3.01:** no published drift estimate exists, so it can't be
  screened on paper. It would need its own design study, which is a trial
  and so belongs to step 5.
- **Not used:** the one approved WRDS connection. Target market caps and
  NT 10-K filer sizes would change n, but each verdict fails under the
  cluster scaling, which caps n at the confirmation's months. No Duo push
  was sent.

### Seen data (checklist item 7)

- **Listed-CEF NAV tender counts, 2010-2026:** from deals.csv (the trial 58
  parse), printed 2026-11-18. Their prices exist on disk from trial 58's
  CRSP pull and were not read.
- **SC TO-T offers with a target, by year, and NT 10-K and 8-K filing
  counts, 2010-2026:** printed 2026-11-18.
- **Papers read:**
  - Bartov & Konchitchki (2017), full text;
  - Gray (2005), full text;
  - Baker & Savasoglu (2002), Mitchell & Pulvino (2001), Jetley & Ji (2010)
    and Offenberg & Pirinsky (2013 WP), full text;
  - Kadapakkam, Zhang & Yildirim (2021) and Guragai (2022), abstracts and
    preview only.
