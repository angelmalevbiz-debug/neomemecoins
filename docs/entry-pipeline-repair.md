# PAPER entry pipeline repair — 7 October 2026

The active main account and four funded Lab books had zero entries. A 50-second
sample found market-filter, incomplete exact-pool flow, buy-pressure, and retry
refusals. These were real refusals, not executed trades hidden by the UI.

An audit of 34 SOL/PumpSwap feed coins found no funded modeled roundtrip below
the 1.5% admission cap. Their fees frequently exceed that cap even before
impact and network costs. Reducing size cannot overcome fixed percentage fees.
The cap, freshness, exact-pool safety, independent price, and daily risk checks
remain enforced. This repair does not claim a profitable strategy or 80% wins.

Future funded entries use `PROMOTED_SHARED_CANDIDATES_EVIDENCE_COST_V2` and the
same exported `lab_activity.RULES` as their market discovery. Evidence admission
remains `PROMOTED_EVIDENCE_COST_V1`. Earlier ledgers and exits keep their original
versions. Cost feasibility is a labeled planning estimate and grants no entry.

Main entries use `WINNER_ENSEMBLE_VERIFIED_ENTRY_V4`. Existing rules remain;
`COST_EFFICIENT_FLOW` adds established markets with score >=76, liquidity
>=$200,000, age >=24h, liquidity/cap >=1.5%, 5m move -1% to +15%, 1h move -5%
to +40%, market buy/sell >=1.1, and 1h volume/liquidity 0.03 to 3. These are
prospective candidate filters, not a backtest. Fresh confirmed exact-pool flow
must still contain >=3 swaps, >=2 wallets, and >=1.2 buy/sell USD. Quotes must
still satisfy the 1.5% expected and 2.5% conservative roundtrip ceilings.

A shared bounded DEX Screener PumpSwap address catalog also examines pools
without current paid promotion. It caches addresses only, never entry prices,
and refreshes at most once per minute across local accounts. Supported PumpSwap
entry pools are retained even when the mint has a larger unsupported pool.
Held positions continue to mark their exact original pools.

Tape discovery deduplicates held main/Lab pools before its entry budget, keeps
them pinned regardless of modeled cost, and gives entry cohorts 60 seconds to
complete coverage. Cost estimates prioritize observations; bounded exploration
remains because actual on-chain fees can differ from estimates. Ambiguous swaps
still degrade coverage. Their diagnostics now name the actual actor flag.

Main three-leg quotes reserve a bounded 15-second cross-process lease, with
existing-position exits taking precedence. Background probes cannot consume the
sequence's HTTP slots. Quota and quote age/slot/drift checks are unchanged;
remaining failures identify their exact stage and reason in the dashboard.

Cached Gecko observations retain their receipt timestamps. Empty/failed calls
are backed off, and rate limits pause retries for three minutes. Cached older
prices cannot acquire a new timestamp merely by being rescanned.

Windows lifecycle coordination uses one named operation mutex for manual
changes and the watchdog's complete health-check/recovery decision. A partially
written startup manifest cannot trigger a competing stop. Manual startup keeps
its pause until every owned worker is verified; automatic recovery failures
remain retryable. The learner launches its actual PID rather than the Windows
virtual-environment launcher PID, and shutdown waits for its journal lock.

Shared quote/reference JSON writes retry transient Windows replacement locks
within a bounded interval, preserve the previous complete file, and raise
persistent failures. Recorder logs identify only the failed filename and Windows
error number. Failed writes do not refresh evidence timestamps.

Validation uses isolated account/cache/audit paths, including real Windows
process contention tests. Offline successful-entry fixtures establish that
valid entries remain reachable; they are not reported as live PAPER results.
Runtime deployment snapshots balances, histories, sessions, and held positions
before a graceful restart, and public validation checks the same backend ledger.
