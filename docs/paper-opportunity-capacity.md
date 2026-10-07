# PAPER opportunity coverage and Windows storage repair

The preceding deployment opened no MAIN or funded Lab positions during the
observed run. The quote sequence lease was not stuck: MAIN and user engines
acquired and released it. Completed preflights were refused for actual cost or
quantity drift. Raising a trade counter or removing these limits would not
establish profitable execution.

This repair addresses specific observation and scheduling failures:

- Windows readers share delete access, and the recorder replaces existing
  snapshots using `ReplaceFileW`. Real Windows tests hold readers open while
  publishing a new snapshot: old readers see the complete old observation and
  new readers see the complete new observation. Unique recovery copies preserve
  prior data on documented partial replacement failures. Persistent storage or
  ACL errors still surface as unavailable evidence.
- The shared address catalog combines bounded DEX Screener discovery with one
  DexPaprika liquid PumpSwap catalog request per minute. Each source has its own
  retry deadline and observation expiry. Addresses are refreshed and validated
  through the normal market scanner; catalog prices do not authorize entries.
  The combined catalog contains at most 60 mint addresses.
- The bounded feed retains market candidates before trimming, while preserving
  their original display order. Entry quote work tries candidates with plausible
  fee costs before unknown-cost and modeled expensive candidates. No candidate
  is deleted from the quote queue because of the fee model. Every original
  signal, safety, price, flow, cost and portfolio gate still runs.
- MAIN cost planning uses the fee-only floor already used by its tape scheduler.
  Lab retains its separate modeled slippage and latency assumptions. Actual
  MAIN route quotes remain the authority for execution costs.
- Successful foreign programs that reference a pool are classified as non-swaps
  only when every resolved outer/CPI instruction matches a complete successful
  runtime call tree with no Pump invocation, and a new confirmed RPC observation
  proves the pool's owner at or after the transaction slot. Missing or ambiguous
  evidence remains unclassified. This proves no Pump swap executed; it does not
  claim that vault balances or prices stayed unchanged. Historical terminal
  classifications remain intact. At most one bounded owner batch is added per
  recorder poll, only for otherwise eligible foreign references.
- Signature discovery uses unused capacity in the existing 48-body processing
  budget instead of always reserving half. Due retries retain capacity, all
  tracked pools progress, and oversized provider responses fail closed. A scan
  head older than the 30-second decision window starts a new causal window;
  stored history is retained and completeness must be established again.

No account, allocation, completed trade or session is reset. Live signing and
order submission remain disabled. The entry candidate rules and their V4
version are unchanged; infrastructure and queue changes are recorded in the
strategy integrity manifest.

The 80% win-rate objective remains unproven. Evaluation requires newly closed
PAPER trades, net fees and losses, and an honest observation period. Wider
coverage and corrected storage cannot guarantee a minimum trade count or
future profit.
