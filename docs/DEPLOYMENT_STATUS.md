# Current PAPER deployment status

Repair and publication configuration on 2026-10-07: see
[Momentum Rush repair](MOMENTUM_RUSH_REPAIR.md) and
[PR #1](https://github.com/angelmalevbiz-debug/neomemecoins/pull/1).
The active Windows services run from `C:\Users\Chavd\neomemecoins` in PAPER mode.
The repository variable `NEO_API_URL` was configured to the PC's authenticated
gateway through a new HTTPS Quick Tunnel. The repaired Pages workflow reads that
variable; a successful deployment of this source is required for the public
site to switch from the old VPS API. The public site remains
`https://angelmalevbiz-debug.github.io/neomemecoins/`.
The VPS has not been updated, and its independent account histories are retained.

Latest successful Pages deployment, verified 2026-10-08 with `gh run list`:
workflow run [37679737562](https://github.com/angelmalevbiz-debug/neomemecoins/actions/runs/37679737562)
(run #55) built `main` at `54fec89` and completed at 2026-10-07T20:10Z after starting
at 20:08Z. Every `main` push since the 2026-10-06 baseline below has deployed
successfully, so the baseline commit `d983b1de` / run #17 cited there is historical.
The running services register 34 Lab books: 29 TEST books at $500, Fast Scalper
at $100 and the four `PROMOTED_PAPER` cohort books at $250 each.

The local dashboard is available at `http://127.0.0.1:5173`, using the local
gateway at `http://127.0.0.1:8879`. Normal Supabase sign-in is required; private
authenticated account viewing is not certified by the unauthenticated probes.
Unknown custom Lab ledgers survive source upgrades with an explicit inactive
status. Their original position policy must be restored before they can resume.
The PC backend is accessible through the tunnel: health 200, exact Pages-origin
CORS 204, and unauthenticated private state 401. The separate loopback shared
backend schema passed the read-only deployment probe. Neither probe certifies a
logged-in private account. Supabase authentication remains required on both URLs.

The tape now prioritizes current observations while limiting historical replay;
three SQLite indexes and disjoint window queries avoid repeatedly scanning the
durable ledger. On an offline 400,049-signature database, decision-window queries
improved from 49.006 to 0.112 ms, current pending selection from 44.214 to 0.117 ms,
and historical selection from 61.492 to 0.035 ms. Migration retained row
fingerprints and results. These timings measure database work, not trading
profitability. The i5-13600K has 20 logical threads and approximately 32 GiB RAM;
provider quotas, unsupported transactions and evidence quality still limit
verified flow. Shared API limits and entry protections remain active.

Quick Tunnels have temporary hostnames and no uptime guarantee. Keep the PC,
services, network and tunnel running. After a tunnel process restart, update
`NEO_API_URL` and rebuild Pages with the new hostname. This configuration is not
a permanent hosted backend. Complete flow coverage, an 80% win rate and positive
net expectancy remain unproven; Momentum Rush stays an independent TEST book.

Everything below is a dated 2026-10-06 baseline. Its endpoint, strategy count,
reset and service measurements are historical and do not describe the current
deployment. The 2026-10-07 source repair performs no reset by itself. The local
runtime ledgers were, however, reset on 2026-10-07 at 08:40 UTC, before that day's
deployments: the shared main account's current session is
`PAPER-RESET-1791362408708-ce6722b7` (started 2026-10-07T08:40:08Z), both per-user
accounts received new `PAPER-RESET` sessions at 08:40:18Z and 08:40:27Z, and the Lab
restarted at 08:40:36Z. Checksum archives of the previous ledgers were written under
the runtime's ignored `.runtime/accounts/archive` at the same time. Earlier wording
here and in pull-request descriptions that said "no reset" described the code
change, not the runtime.

The maintained repository is [angelmalevbiz-debug/neomemecoins](https://github.com/angelmalevbiz-debug/neomemecoins). The public app is [GitHub Pages](https://angelmalevbiz-debug.github.io/neomemecoins/).

## Verified public site baseline

Historical, superseded by the 2026-10-07 deployments listed above: the frontend baseline at source commit `d983b1de48958d0a9dabc4ae37d210d6c175c83e` was published by [Pages run #17](https://github.com/angelmalevbiz-debug/neomemecoins/actions/runs/37376396987), which succeeded. The preceding verified-source restore [run #11](https://github.com/angelmalevbiz-debug/neomemecoins/actions/runs/37376320288) also succeeded. The served JavaScript bundle contains the current `NEO_API_URL` Quick Tunnel host. The local Lab runtime fix in this document changes backend source and does not add a frontend feature.

Read-only checks on 2026-10-06: Pages returned HTTP 200; the local gateway's `/user/health` returned HTTP 200; `OPTIONS /user/state` returned 204 with the exact `https://angelmalevbiz-debug.github.io` origin and the `Authorization` header allowed. An unauthenticated `/user/state` returns the expected 401. The Codex browser is currently at the sign-in form, so the authenticated private dashboard has not been verified here.

## Live local PAPER runtime snapshot

The local machine is running the main monitor, shared live-tape recorder, 33-strategy Lab, and per-user gateway in PAPER mode. All state is isolated under ignored `.runtime/accounts`. The machine, these services, network connection, and `cloudflared` Quick Tunnel must remain available; Quick Tunnels can change hostname and have no uptime guarantee. No live executor or wallet signing is enabled.

At the measured `/state` snapshot on 2026-10-06, the main account scanned 68 market-feed entries; 0 passed its early order-flow signal, 0 were quoted, and 0 opened. The diagnostics report incomplete or delayed verified order flow and no qualifying early buy-flow impulse. This main account therefore had 0 open and 0 closed trades at that snapshot.

The Lab reported `online` for 33 separate books, with 3 open and 13 completed positions at the measured snapshot. Each book retains its own balance and risk; the Fast Scalper starts at $100 and the other Lab books at $500. The raw open entries passed exact-pool price cross-checks and record DEX fees, network fees, modeled price impact, slippage/latency, and `REALISTIC_COSTS_V1`. Results are measured per book; balances and returns are not summed.

The separate candidate learner remains `WAIT`: 0 learner simulations, 69 unique market episodes, 41,470 unique observations, active version `v0-control`, and no completed training attempt or approved candidate. The 33 Lab books are experimental PAPER portfolios, not evidence that a candidate model has been trained or promoted. The tape currently reports `degraded`, tracks 60 pairs, and had no recent trade events; order-flow-dependent entries stay blocked until valid events arrive. No profitability conclusion is supported by the current sample.

## Reset boundary

The new local runtime was reset before starting, with archives retained. The 33 Lab books began from fresh state. The previous remote/legacy backend's shared account was reset, but Lab/Astra/paired and private accounts there were not audited or reset because administrative host access was unavailable. That legacy backend is not the endpoint used by the current Pages build; its old history does not appear in the local runtime.

See [PAPER_RUNBOOK.md](PAPER_RUNBOOK.md) for start/stop commands, recovery, replay, reset, and verification details. The read-only probe command is:

```sh
python scripts/check_pages_backend.py --output .runtime/pages-backend-check.json
```

It checks only public gateway/schema responses; it does not authenticate, establish private-dashboard access, certify completed learning, or prove profitability.
