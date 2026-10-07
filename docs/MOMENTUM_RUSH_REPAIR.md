# Momentum Rush PAPER repair — 2026-10-07

This repair combines the current GitHub interface/promoted entry guards with the
local branch's data, quote, learner and net-accounting fixes. It completes the
previously unregistered Momentum Rush helper as an independently funded TEST
book. The four promoted books retain their own histories and allocations.

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
