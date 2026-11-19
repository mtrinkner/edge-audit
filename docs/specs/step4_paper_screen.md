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
3. **Prior:** the paper's effect, its sample years, and its regime.
   - The regime is replication (58%) if the trial's design period would
     overlap the paper's sample, otherwise own_design (26%).
   - For a portfolio, the cited Sharpe goes through the existing path.
   - For an event trade, a new path takes the cited mean and SD per event:
     prior t = mean/SD × (1 − shrink) × √n, with n the smaller of the
     projected events and the event-equivalent of the expected month
     clusters. It is added to `python/gate.py` with tests, and committed
     before any candidate is run.
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
