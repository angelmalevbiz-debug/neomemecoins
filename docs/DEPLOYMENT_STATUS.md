# Measured Pages/backend status

The maintained repository is [angelmalevbiz-debug/neomemecoins](https://github.com/angelmalevbiz-debug/neomemecoins). The browser address is [GitHub Pages](https://angelmalevbiz-debug.github.io/neomemecoins/). The legacy repository remains a historical baseline.

## Published baseline and current local replacement

At source commit `9634414e2ea109afd83b5f78e0277a6bc530f2d0`, Pages deployment [37359080106](https://github.com/angelmalevbiz-debug/neomemecoins/actions/runs/37359080106) and regression [37359080111](https://github.com/angelmalevbiz-debug/neomemecoins/actions/runs/37359080111) passed. Served HTML, JS and CSS matched the exact Pages CI artifact. That publication did not deploy the Python services.

On 2026-10-05 at 19:28 UTC, a new local PAPER runtime behind the free Quick Tunnel passed read-only probes. The authenticated gateway preflight returned 204 with the exact Pages origin and allowed the `authorization` header; `/user/health` returned 200 and the unauthenticated private-state request returned the expected 401. The loopback shared monitor returned policy `ORDER_FLOW_VALIDATED_THRESHOLDS_V9`, `paper_only=true`, and the `paper_training` object. This probe confirmed backend readiness before the subsequent Pages publication; verify the final publication separately against its Actions run. The GitHub `NEO_API_URL` repository variable is set to the current tunnel hostname.

The live PAPER learner currently reports 1,600 unique observations across 65 market episodes, zero simulations, zero open/completed trades, and active version `v0-control`. All candidate books have separate $500 balances. The current market feed fails required safety/flow/route evidence checks, so signals are recorded as rejected observations and the engine correctly remains in WAIT. These observations are not trades and do not establish profitable edge. An earlier short Windows read/replace contention has been repaired and its status check now retries before declaring the worker degraded.

Read-only backend measurements on 2026-10-05 at 19:07 UTC:

| Probe from the new Pages origin | HTTP result | Actual allowed origin |
| --- | --- | --- |
| `OPTIONS /user/state` with authorization preflight | 204 | `https://angelmalev9-creator.github.io` |
| `GET /user/health` | 200 | `https://angelmalev9-creator.github.io` |
| Unauthenticated `GET /user/state` | 401, correctly protected | `https://angelmalev9-creator.github.io` |
| Shared diagnostic `GET /state` | 200 | `*` |

The requested origin is `https://angelmalevbiz-debug.github.io`. The old private gateway's mismatched response explains the reported browser `Failed to fetch` after successful Supabase login. A 204 preflight alone does not establish browser access. The source gateway includes an exact allowlist for both Pages origins and local development; its authenticated success, unauthorized and upstream-error responses are covered by HTTP integration tests. The local replacement's expected unauthenticated 401 has been confirmed, but an authenticated Pages browser session has not been checked in this runtime.

The shared remote state still reports `ORDER_FLOW_GOLD_SIGNAL_VERIFIED_V7`, `paper_only=true`, and no `paper_training`. Therefore the repaired learning engine is not proven running on that server. A correct frontend message cannot repair this backend deployment.

## PAPER reset boundary

The shared primary account on the legacy server was reset to $1,000 with empty history and positions. Local reset archives and independent training accounts were verified. The new local per-user registry is fresh and begins accounts at $1,000; its nine training books begin independently at $500. Remote legacy Lab, Astra and paired experiments still contain prior results; private per-user accounts have not been audited or reset through administrative host access. The all-account request remains incomplete on that legacy server. Preserve every account's independent capital; never aggregate legacy simulation counts as independent market evidence.

## Remaining host operation

The free local path avoids the legacy server and is documented in [PAPER_RUNBOOK.md](PAPER_RUNBOOK.md). If the legacy server is brought back into scope, use an authorized admin session to install reviewed source into its actual checkout, inspect effective service environment/path overrides, stop every primary/private/training/Lab/Astra/paired writer, then execute the documented offline `reset-all`. Retain checksum archives, verify empty accounts/new sessions, and restart the original service configuration in PAPER mode. Do not reset files while writers remain active. The repository's example locations must be checked against the running host.

Verify the private gateway CORS origin after restarting it and its reverse proxy, and verify the revised shared policy plus `paper_training`. Finally use a real authenticated browser session on Pages to verify the private dashboard. No unauthenticated fallback, public token relay, LIVE execution, signing or wallet funding is part of this repair.

```sh
python scripts/check_pages_backend.py --output .runtime/pages-backend-check.json
```

This command performs only public read-only diagnostic requests. Exit 0 establishes the listed gateway/schema checks; it does not establish private dashboard access, all-account reset, live learning progress, or profitable market edge. Those need separate evidence. Runtime and account evidence stays ignored locally.
