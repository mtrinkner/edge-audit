# Spec pre-mortem checklist

Filled in and committed **before every declaration**, as
`docs/specs/<study>_checklist.md`, with the commit named in the declaration.
Each answer cites its evidence: a filing accession, a file and line, a test,
a broker's fee page, a CRSP field. "N/A" needs a reason. A blank item blocks
the declaration.

Each item comes from a past mistake. The lesson is in brackets.

| # | Question | Answer | Evidence |
|---|---|---|---|
| 1 | **When exactly does the contract or mechanism pay, and what condition must hold at entry?** [Trial 58 bought odd-lot tenders unconditionally, even above the tender floor, where the priority paid nothing.] | | |
| 2 | **Which fees and costs really apply, by source?** Commission, the broker's corporate-action or voluntary-reorganization fee, spread, borrow, and the account's minimums. [The $40 merger-arb fee did not apply to tender offers; SPAC redemptions cost $0 at one broker and $38 at another.] | | |
| 3 | **For every input: when was it public, and does the code use it only after then?** Filing acceptance time, quote time, the vendor's revision date. Name the function that enforces it and the test that checks it. [The SPAC T-bill path that actually happened was used to accrue amount_t; the 52-week features mixed adjusted and raw prices.] | | |
| 4 | **What could look like profit but isn't?** Expired rights, post-merger or post-split prices, stub quotes, misparsed amounts, delisting returns, survivorship. [SPAC trades bought after the last redemption deadline; the CHRD re-stitch.] | | |
| 5 | **The units and thresholds.** Raw or annualized, per share or per position, percent or basis points, which calendar. [Trial 27's clustering; zoo.py's weekly units; trial 60's dollars a year divided by the wrong years.] | | |
| 6 | **Does the test statistic match the strategy type (G-REAL)?** Event trades: event-level t, clustered by ex-date month. Portfolios: calendar-time monthly t. State K, the rule variants evaluated on the design period. [The 2026-11-02 gate judged an event trade on a monthly Sharpe.] | | |
| 7 | **What data from the confirmation period has already been seen?** Every parse, count, plot or published figure, with the date and where it appeared. [SPAC 2021-2025 opportunity counts; trial 60's v1 entry-day counts.] | | |
| 8 | **Who reviewed the spec, independently of whoever wrote it?** The reviewer is the separate review session or a named person, never the author: the session or person that wrote the spec can't review it. Give the reviewer, the date, and what they changed. A reviewer handed the author's conclusion doesn't count. | | |

G-REAL and G-WORTH (`python/gate.py`, docs/METHODOLOGY.md) are computed
after this checklist and quoted in the declaration.
