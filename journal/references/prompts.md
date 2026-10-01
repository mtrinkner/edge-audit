# The seven prompts

Source prompts from the workflow doc, kept verbatim. In the skill they run as
internal stage instructions, but they are here so Mason can copy any one of
them into a fresh chat and get the same behavior standalone.

**Prompt 1 – Scanning the Market**
"Act as my trading scanner. Here is the market data I gathered for [symbol(s)] on [timeframe]: [paste price action, volume, momentum, volatility, and overall market condition notes]. Based only on this data, list which setups look worth a closer look and why."

**Prompt 2 – Filtering Weak Setups**
"Here are the candidate setups: [paste list]. Using my strategy rules ([your strategy rules]), filter out the weak ones. For each rejected setup, give one clear reason."

**Prompt 3 – Confirming a Signal**
"Confirm this setup for [symbol] on [timeframe]. Data: [trend, volume, momentum, support/resistance levels, any news/earnings]. Score the signal 1–10 on strength and tell me exactly what would INVALIDATE it."

**Prompt 4 – Building a Trade Plan**
"Build a structured trade plan for [symbol], [direction], [timeframe]. My account risk settings: [max risk % per trade, account size]. Include: entry, stop loss, take-profit target, risk-to-reward ratio, position size, and the exact rule that invalidates the trade."

**Prompt 5 – Reviewing Risk**
"Review the risk on this trade plan: [paste plan]. My limits: max risk per trade [X%], daily loss limit [X], max open positions [X], exposure limit [X]. Tell me if this trade breaks any limit."

**Prompt 6 – Backtesting a Strategy**
"Help me structure a backtest of these rules: [entry rules], [exit rules], over [historical period]. I will provide the historical data — do NOT invent results. Give me the exact table of metrics to fill in."

**Prompt 7 – Creating a Final Trade Memo**
"Turn this into a clean trade memo using my template: [paste confirmed plan + signal score + risk review]. Fill every field. End with a Final Status (Approved / Watchlist / Rejected)."
