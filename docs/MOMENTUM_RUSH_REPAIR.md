# Momentum Rush PAPER repair — 2026-10-07

This repair combines the current GitHub interface/promoted entry guards with the
local branch's data, quote, learner and net-accounting fixes. It completes the
previously unregistered Momentum Rush helper as an independently funded TEST
book. The four promoted books retain their own histories and allocations.

The concurrent GitHub integration's faster Rush exits are retained: a 3% net
stop, 7% net target, 20-minute maximum hold, liquidity collapse below 65% of the
entry level, a fresh exact-pool flow reversal, and a 2.5 percentage point net
trail after a 4% net peak. Other books retain their existing exit framework.
Missing or stale marks do not create simulated fills. A stop threshold does
not limit the actual loss in a gap.

## Confirmed problems

- The helper and activity policy existed in GitHub, but the runner had no
  Momentum Rush registration or execution wiring.
- The Windows services run from `C:\Users\Chavd\neomemecoins`, a different
  checkout from the development workspace. The running tape still submitted
  multiple transaction bodies in one RPC batch. The provider returned HTTP 400
  and required at most one `getTransaction` request.
- Pinned market records survived a closed position. On re-entry, tuple-key
  priority could select that older record ahead of a fresh same-pool snapshot,
  producing `STALE_MARKET_EXIT` only seconds after entry. The repaired exit path
  selects the newest actual same-mint/same-pool observation and rejects future
  timestamps. Truly stale or missing observations still trigger protection.
- The main dashboard totals used the four promoted books while its history
  showed another account's trades. Both now use the same selected cohort.
- The Pages build ignored `NEO_API_URL`, and the backend readiness probe rejected
  the supported winner ensemble because it expected only an older policy ID.

## New experiment

Momentum Rush Brain seeks a broader early-momentum universe, scores acceleration
from distinct causal snapshots, remembers only its own already closed net trades,
and limits low-cap allocation. Its observations and memory are bounded. Fresh
identity, safety, price verification and modeled execution costs remain admission
requirements. The experiment cannot enter the promoted winner ensemble or send
live transactions. The win-rate target is 80%, explicitly unvalidated.

## Measure results without changing history

Use the **full** ledger, not the dashboard's shortened history:

```powershell
.venv/Scripts/python.exe scripts/evaluate_paper_lab.py --input .runtime/accounts/strategy_lab.json --output .runtime/lab-evaluation.json --target 80
```

The report measures each book separately: completed wins/losses/breakevens, net
PnL, profit factor, net expectancy, observed hourly/24h normalized throughput,
closed-ledger drawdown and a descriptive 95% Wilson interval. Missing, malformed,
future, duplicated, prior-session or truncated records are flagged. Open positions
and partial exits do not become additional completed wins. The report never
resets the account, pools independent books, or promotes a strategy.

At least 100 closed trades over five completed UTC days are required before the
report labels the target observed in its sample. That label also requires positive
net PnL and a profit factor above one (or positive PnL with no losses). It is a
descriptive threshold, not independent financial validation. The interval assumes
independent outcomes; token/market correlations can violate that assumption.
Closed-ledger drawdown excludes intratrade/open losses. More daily trades and a
high win rate can still lose money after costs.

## Deployment boundaries

The public Pages build uses the VPS API at
`https://neo-meme-api.169-58-211-177.sslip.io`. The public VPS and the Windows
services have different PAPER histories. A successful local restart or Pages
build does not establish that the VPS Python services received this repair.

Read-only probes found the public VPS reachable with fresh state and correct
gateway CORS. Its Desktop Commander administrative connector was offline, so
backend installation there requires that connection to recover or another
authorized administrative session. Preserve its 35 existing books, including
Momentum Swarm and Astra; do not overwrite its state with the Windows ledger.

No empirical 80% success rate or positive trading edge has been established.
Synthetic regression success verifies behavior and accounting, not returns.

## Executed verification

The integrated revision passed 342 root Python tests, 114 backend Python tests,
16 frontend regression tests, TypeScript, the strategy hash guard, extension
syntax checks and the production build. The build emitted a nonfatal bundle-size
warning. The retained one-shot GitHub migration now exits successfully without
rewriting an already integrated engine; incompatible partial migrations refuse
without changing source.

The Windows update used a checksummed archive of nine existing account/state
files after all owned writers stopped gracefully. Subsequent read-only checks
confirmed all archived primary trades and all 33 existing Lab ledgers were
retained; Momentum Rush was added as the 34th book in TEST. Main, tape, Lab and
gateway returned running status. The tape began decoding actual events (204 in
one measured projection); coverage was still degraded, with provider pagination
and some unsupported transactions unresolved. This is partial data recovery,
not proof of complete flow coverage or a successful new strategy outcome.

## Local follow-up

Git publication recovered using the existing owner account explicitly. PR #1
contains the integrated source at `794ff55` and its GitHub regression jobs passed.
After the user selected local work only, subsequent changes remained local.

The local dashboard launcher serves `http://127.0.0.1:5173` and selects the local
gateway at port 8879. Normal sign-in remains required. Unregistered custom Lab
books retain their full ledgers and positions, expose `preserved_inactive`, and
receive no entries or invented exit policy. Restoring their actual policy is
required before resuming them.

The old tape configuration could retrieve only one signature per pool per poll
from 12 busy pools and could not catch up with their observed flow. The local
configuration now monitors two pools with a bounded 48-body budget, four concurrent
requests, and at most one historical retry per cycle. Current bodies rank by
on-chain time. Pools outside this capacity retain unavailable flow; guards still
require actual complete fresh evidence. Historical rows remain durable.

The final source passed 346 root and 119 backend Python tests, 16 frontend
regressions, TypeScript, strategy hashes, extension checks and production build.
The dashboard launcher was exercised through start, repeated start, stop and
restart. Local CORS returned the exact localhost origin; private state returned
the expected unauthenticated 401. No private authenticated dashboard result or
financial success rate is inferred from these checks.

Transient failures refreshing the shared SOL/USD quote now retry after five
seconds instead of waiting another 45 seconds and allowing the 60-second
reference to expire. Successful refreshes and authentication failures retain
the usual 45-second interval; shared quota, exit priority and provider cooldown
still apply. Old observations are never repriced using a future reference.

The local source was installed with all four PAPER services running and zero
stderr output after a checksummed account archive. All 34 existing Lab ledgers
and the primary's 31 archived completed trades survived the update. The reduced
pool set cleared pagination and had cursors within 2–3 seconds and no current
transaction backlog. Complete flow coverage still fluctuates with real unknown
actors/instructions and missing verified FX evidence. This remains a data-quality
constraint, not evidence of an 80% trading edge. Rush had no completed trades in
the measured follow-up.
