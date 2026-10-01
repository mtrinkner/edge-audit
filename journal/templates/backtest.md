# Backtest — {{STRATEGY_NAME}}

## Rules under test

| Field | Value |
|-------|-------|
| Historical data period | {{PERIOD}} |
| Symbols / universe | {{UNIVERSE}} |
| Timeframe | {{TIMEFRAME}} |
| Entry rules | {{ENTRY_RULES}} |
| Exit rules | {{EXIT_RULES}} |
| Stop rule | {{STOP_RULE}} |
| Position sizing | {{SIZING}} |
| Costs modeled | {{COSTS}} |

## Metrics to fill in from real data

| Metric | Value |
|--------|-------|
| Number of trades tested | |
| Win rate | |
| Average win | |
| Average loss | |
| Profit factor | |
| Maximum drawdown | |
| Longest losing streak | |
| Average R per trade | |
| Net P/L | |

## Rules for filling this in

1. The table stays blank until Mason supplies historical bars or a fill list.
   Fabricated results are worse than no results.
2. Every metric traces to specific trades. Keep the trade by trade list next to
   the summary so any number can be checked.
3. State the sample size next to the win rate. Thirty trades is a hint, not evidence.
4. Note what is not modeled: slippage, partial fills, halts, borrow availability
   on shorts, and the survivorship bias in whatever symbol list was used.
5. A backtest describes the past. It is not a forecast, and the skill will not
   present it as one.

## Findings

{{FINDINGS}}

## What would break this strategy

{{FAILURE_MODES}}
