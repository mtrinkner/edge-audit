# Checklists

Every item gets an explicit PASS, FAIL, or UNKNOWN. UNKNOWN counts as FAIL for
gating purposes. Never fill an item from memory or from what a chart "probably"
looks like. If Mason did not paste the data, the item is UNKNOWN.

## SCAN — market scanning

| # | Item | What a PASS looks like |
|---|------|------------------------|
| 1 | Price action | Clear structure, not chop. Readable higher highs / lower lows, or a defined range with clean edges. |
| 2 | Volume | Real participation. Relative volume at or above the recent norm for that time of day. |
| 3 | Momentum | Building, not fading. Impulse legs are expanding rather than shrinking. |
| 4 | Volatility | Enough movement to matter, not chaos. ATR or recent range supports the target without wild whipsaw. |
| 5 | Market conditions | The broader market is with the setup, or at least not against it. |

Gate: fewer than 4 of 5 at PASS and the symbol does not advance out of SCAN.

## CONFIRM — signal confirmation

| # | Item | What a PASS looks like |
|---|------|------------------------|
| 1 | Trend alignment | The setup agrees with the higher timeframe trend. |
| 2 | Volume confirmation | Volume supports the direction of the move, not the pullback against it. |
| 3 | Momentum strength | Momentum backs the direction on the entry timeframe. |
| 4 | Support and resistance | Clean levels, and real room between entry and the target. |
| 5 | News and earnings risk | Nothing scheduled that can blow up the chart inside the holding period. |

Score the signal 1 to 10 and state exactly what would invalidate it. A score
below 7, or any FAIL on item 1 or item 5, sends the setup to Watchlist rather
than Plan.

## PROTECT — risk management

| # | Item | Enforced by |
|---|------|-------------|
| 1 | Maximum risk per trade | `risk.py plan` sizes the position so dollar risk never exceeds the profile cap. |
| 2 | Daily loss limit | `risk.py status` compares today's realized P/L against the limit and halts. |
| 3 | Maximum open positions | Counted from the open rows in trades.jsonl. |
| 4 | Exposure limit | Combined notional of open positions plus this one against the cap. |
| 5 | No-trade conditions | Judgment call, listed below. |

### No-trade conditions

Any one of these is an automatic Rejected, regardless of how good the setup looks:

- Earnings, an FOMC print, or another scheduled event lands inside the holding period.
- Liquidity is thin: wide spread, low relative volume, or a float that cannot absorb the size.
- The daily loss limit is already hit.
- Mason is trading to make back a loss, or is angry, rushed, or tired. He says so, or it shows.
- The data is stale, partial, or unverified.
- Position size rounds to zero shares, meaning the stop is too wide for the risk budget.

## Paper-trading launch checklist

Run this once before the first session and any time the strategy changes.

- [ ] Strategy rules written and clear
- [ ] Data source chosen and verified
- [ ] Paper account funded with fake money
- [ ] Risk limits set in risk-profile.json
- [ ] Trade log ready
- [ ] All 7 prompts saved with placeholders filled
- [ ] Confirmed: no live trading, human approval before every paper trade
- [ ] Committed to reviewing every trade honestly
