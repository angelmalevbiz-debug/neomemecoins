# Current PAPER deployment status

The maintained repository is [angelmalevbiz-debug/neomemecoins](https://github.com/angelmalevbiz-debug/neomemecoins). The public app is [GitHub Pages](https://angelmalevbiz-debug.github.io/neomemecoins/).

## Verified public site baseline

The frontend baseline at source commit `d983b1de48958d0a9dabc4ae37d210d6c175c83e` was published by [Pages run #17](https://github.com/angelmalevbiz-debug/neomemecoins/actions/runs/37376396987), which succeeded. The preceding verified-source restore [run #11](https://github.com/angelmalevbiz-debug/neomemecoins/actions/runs/37376320288) also succeeded. The served JavaScript bundle contains the current `NEO_API_URL` Quick Tunnel host. The local Lab runtime fix in this document changes backend source and does not add a frontend feature.

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
