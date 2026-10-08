# Reproduce, run, reset and roll back PAPER

Use Python 3.12+ and Node 22+. This audit used Windows Python 3.14.8 and Node 24.21.0. Runtime JSON, private archives, recordings, virtualenv and generated build files are ignored by Git. Never commit account registries or credentials.

```powershell
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements.txt
npm ci
.venv/Scripts/python.exe scripts/run_python_checks.py
npm run lint
npm run check:strategy
npm run build
.venv/Scripts/python.exe scripts/check_quote_contract.py
.venv/Scripts/python.exe scripts/check_pages_backend.py --output .runtime/pages-backend-check.json
```

Both Python suites use temporary state/audit/cache directories through the runner. Test imports do not load or overwrite the working account. No live execution path is enabled; `NEO_ENGINE_MODE=LIVE` fails at startup.

## Local PAPER dashboard and free HTTPS tunnel

The maintained Pages frontend can use the local PAPER services without Contabo. The local runtime is isolated under the ignored `.runtime/accounts` directory. It starts a $1,000 main PAPER account, nine separate $500 learning books, 34 independent strategy Lab books (29 TEST books at $500, Fast Scalper at $100, and the four `PROMOTED_PAPER` cohort books EARLY, MOMENTUM, PRECISION and ULTRA_PRECISION at $250 each), one shared market recorder, and a per-user gateway on loopback ports 8878/8879. It does not modify remote/server accounts.

```powershell
.\scripts\start_local_paper.ps1 -Action Start
.\scripts\start_local_paper.ps1 -Action Status
```

To repair only missing tape or Lab processes while verified main and gateway processes continue running, use `-Action StartMissing`. Stop all owned services gracefully and flush their state with `-Action Stop`; the command does not force-kill a service that is still stopping.

For local use, start the dashboard from the checkout with `npm ci` dependencies
installed. Its launcher binds only loopback and explicitly selects the local
gateway, so it does not fall back to the public VPS:

```powershell
.\scripts\start_local_dashboard.ps1 -Action Start
.\scripts\start_local_dashboard.ps1 -Action Status
.\scripts\start_local_dashboard.ps1 -Action Stop
```

Open `http://127.0.0.1:5173` and sign in with the existing Supabase account.
The launcher runs in the background and requires the gateway at port 8879.
It checks process ownership before stopping its own dashboard and refuses to
replace an unrelated process on port 5173.

Expose only the authenticated loopback gateway with the background launcher:

```powershell
.\scripts\start_local_tunnel.ps1 -Action Start
.\scripts\start_local_tunnel.ps1 -Action Status
```

Copy the `https://….trycloudflare.com` hostname into the GitHub repository variable `NEO_API_URL`, then rerun the Pages deployment workflow. The checked-in workflow reads that variable at build time. The launcher records its own PID, creation time and origin under ignored `.runtime/tunnel`; repeated Start reuses the running tunnel and Stop checks ownership. It does not modify other Cloudflared services. A Quick Tunnel is temporary and has no uptime guarantee; the computer, local PAPER processes, network, and background `cloudflared` process must remain available. If the tunnel restarts with a different hostname, update `NEO_API_URL` and rerun Pages deployment. See Cloudflare's [Quick Tunnel documentation](https://developers.cloudflare.com/tunnel/get-started/quick-tunnels/).

The browser gateway uses exact Pages-origin CORS and requires the normal Supabase bearer token for private state. Its `/state` is also authenticated; do not expose the shared main monitor to make a diagnostic probe pass. `/user/health` and the separate loopback main monitor's `/state` do not prove that the logged-in private dashboard works. Use the authenticated site to validate the account after deployment. PAPER actions only; no LIVE executor is exposed by this local service.

For this layout, provide the private loopback diagnostic separately:

```powershell
.venv/Scripts/python.exe scripts/check_pages_backend.py --backend https://YOUR-TUNNEL.trycloudflare.com --shared-backend http://127.0.0.1:8878 --output .runtime/pages-backend-check.json
```

The read-only Pages/backend check deliberately returns a nonzero exit code if the public gateway's CORS origin or shared backend schema is still old. It does not log in, bypass private account authentication, reset accounts, or certify financial results. See [DEPLOYMENT_STATUS.md](DEPLOYMENT_STATUS.md) for the latest measured deployment boundary.

## Start a separate PAPER account

Set isolated paths and declared risk values before starting. The following creates a new $1,000 main account, with conservative explicit risk limits and independently funded $500 learning books. It changes no existing server account.

```powershell
$paperRoot = Join-Path (Get-Location) '.runtime/demo'
New-Item -ItemType Directory -Force $paperRoot | Out-Null
$env:NEO_ENGINE_MODE='PAPER'
$env:NEO_MARKET_STATE_PATH=Join-Path $paperRoot 'state.json'
$env:NEO_MARKET_AUDIT_PATH=Join-Path $paperRoot 'audit.jsonl'
$env:NEO_LIVE_TAPE_PATH=Join-Path $paperRoot 'live_tape.json'
$env:NEO_TAPE_DB_PATH=Join-Path $paperRoot 'tape.sqlite'
$env:NEO_RISK_CACHE_DIR=Join-Path $paperRoot 'risk'
$env:NEO_PRICE_CHECK_DIR=Join-Path $paperRoot 'price-check'
$env:NEO_JUPITER_LOCK_PATH=Join-Path $paperRoot 'quote.lock'
$env:NEO_JUPITER_STAMP_PATH=Join-Path $paperRoot 'quote-stamp.txt'
$env:NEO_MAX_POSITION_RISK_USD='100'
$env:NEO_MAX_TOTAL_EXPOSURE_PCT='40'
$env:NEO_MAX_DAILY_LOSS_USD='50'
$env:NEO_MAX_DRAWDOWN_PCT='10'
.venv/Scripts/python.exe backend/live_tape.py
```

The main monitor and the 34-strategy Lab consume the same live tape and recent market snapshot. Position marks reuse that shared market snapshot; the Lab does not issue a separate price request for each open position. The main process launches its independent training worker and bounded writer. The worker has no provider client. Price/safety checks and tape must become valid before entries; UNKNOWN/DEGRADED data correctly stays WAIT. Start with an API budget appropriate to the actual authenticated provider plan.

Primary repository defaults (`backend/market_monitor.py`, mirrored in `strategy-lock.json` with `primary_daily_and_drawdown_caps_enabled: true`) are a $100 daily loss cap, a 20% drawdown cap, maximum full-loss capital $250, exposure 100%, $200 notional and eight positions; these are explicitly visible and are **not** the conservative runbook settings above. Earlier revisions of this runbook recorded both caps as disabled (`0`); that was the 2026-10-05 default and is historical. New training default risk limits remain active. Do not silently change an existing account's limits.

`GET /state` includes `paper_training`: portfolio capitals/returns, open/completed trades, raw simulation versus unique observations/episodes, refusals, failure fees, queue drops/lag, last training, active version, history and control comparison. The live Pages build displays those fields.

## Recording and causal replay

The bridge coalesces only identical observation IDs in a bounded recent-ID cache, then writes accepted rows to `<account directory>/training/observations.jsonl`. It stores availability/source times, exact mint/pool, flow, safety/price provenance, main quote evidence and explicit model assumptions. It makes no per-experiment API request. The independent worker evaluates up to 100 complete rows per batch, atomically saves learner state with its byte-offset checkpoint, and resumes there after restart; the main bot never waits for this work. Queue drops remain explicit evidence gaps and block promotion. Copy recordings after flushing/stopping the producer, or use complete newline-terminated records only.

```powershell
.venv/Scripts/python.exe scripts/paper_training.py import --input .runtime/demo/training/observations.jsonl --output .runtime/datasets/market.jsonl
.venv/Scripts/python.exe scripts/paper_training.py replay --input .runtime/datasets/market.jsonl --state .runtime/replay/training.json --snapshot .runtime/replay/snapshot.json
.venv/Scripts/python.exe scripts/paper_training.py compare --state .runtime/replay/training.json
.venv/Scripts/python.exe scripts/replay_main.py --input .runtime/datasets/market.jsonl --output .runtime/main-replay/report.json --state-dir .runtime/main-replay/account
.venv/Scripts/python.exe scripts/compare_main.py --input .runtime/datasets/market.jsonl --output-dir .runtime/compare-real
```

Fresh output directories are required for primary comparisons. Replay sorts by actual availability and preserves observed timestamps; no sleep, provider calls or future features. Missing exact historical quote quantities cannot be substituted by current quotes or prices. Optional record-from-stdin command:

```powershell
Get-Content canonical-observations.jsonl | .venv/Scripts/python.exe scripts/paper_training.py record --output .runtime/recording.jsonl
```

Synthetic correctness reproduction uses `tests/fixtures/paper_gap_v9.jsonl`, not the real dataset paths. Financial evaluation needs recorded subsequent market data across enough independent days/episodes.

## Reset every PAPER account and restore

Explicit latest user authorization covers all PAPER accounts. Stop **all** main/per-user/training/Lab/Astra/paired writers before offline reset. The all-account operation enumerates the known registry/user state formats, preserves each starting balance (Fast Scalper $100, the four `PROMOTED_PAPER` cohort books $250, other Lab books normally $500), resets main/per-user $1,000 and nine training books $500 each, creates SHA256 archives and new sessions, and keeps raw market data. Unknown schemas abort before known-account changes. A multi-account reset is not one filesystem transaction; disk failure can leave a partial reset, so retain the output/archive manifests and recover from them.

Local example executed:

```powershell
.venv/Scripts/python.exe scripts/paper_runtime.py reset-all --root .runtime/accounts --offline
```

On the existing Linux server, an administrator should stop the gateway and all its child engines as well as the independent services; confirm no writers remain. Service definitions in this repository locate the primary/private/Lab/Astra accounts under `/var/lib/neo-market` and paired experiments under `/var/lib/neo-lab-paired` (verify actual deployment overrides):

```bash
python3 scripts/paper_runtime.py reset-all --root /var/lib/neo-market --paired-root /var/lib/neo-lab-paired --offline
```

Existing `/control/reset` resets only the associated primary account; authenticated `/user/reset` resets only that person's account. Neither proves every private or legacy account has been reset. The shared remote account was successfully reset during this audit; all-account remote reset remains pending administrative host access. Avoid introducing an unauthenticated bulk reset endpoint.

Restore a particular checksum archive with stopped writers:

```powershell
.venv/Scripts/python.exe scripts/paper_runtime.py restore --root .runtime/accounts --archive .runtime/accounts/archive/RESET_DIRECTORY --offline
```

Main and nested training archives are separate: restore each to its matching directory. Restore also archives the current files. Hash mismatch refuses replacement. For code rollback, select the prior reviewed commit and restore its matching saved PAPER account/config archive; do not reuse future-version state blindly. Training rollback is automatic within LEARNER and recorded in version history; it never changes production code.

## Executed result register

Each block below is dated evidence for that date's source tree; its test totals, hash counts and run numbers are not rewritten later. The newest block is the current reference.

### 2026-10-05 initial verification (historical)

Executed before migration to the maintained GitHub account:

| Executed command | Result |
| --- | --- |
| `.venv/Scripts/python.exe scripts/run_python_checks.py` | PASS: `tests/` 257 tests and `backend/tests/` 59 tests; temporary account paths, both child exit codes 0 |
| `npm ci` | PASS, 460 packages installed |
| `npm run lint` | PASS, TypeScript noEmit |
| `npm run check:strategy` | PASS, updated manifest and all 21 support hashes |
| `npm run build` | PASS, strategy/extension guards, Vite and extension archive; nonfatal Vite chunk-size warning |
| `.venv/Scripts/python.exe scripts/check_quote_contract.py` | PASS: Jupiter quote and Solana RPC; final probe matching context/confirmed slot 453648943, quote HTTP 107 ms |
| `.venv/Scripts/python.exe scripts/compare_main.py --input tests/fixtures/paper_gap_v9.jsonl --output-dir .runtime/final-three-arm-compare-lf` | PASS: three actual engine arms, two records each, one completed trade each, zero invalid records; -10 versus -40.195/-40.195 USD |
| `.venv/Scripts/python.exe scripts/paper_runtime.py reset-all --root .runtime/accounts --offline` | PASS: $1,000 primary, no history/positions; nine independent $500 books, $20 configured notional, no simulated outcomes |
| Authorized public remote `POST /control/reset`, then `GET /state` | PASS for shared primary only: HTTP 200, $1,000, zero history/positions; pre-reset public snapshot archived |
| Remote all-account reset | PENDING: Lab/Astra/private accounts require administrative host access |
| Financial edge comparison on adequate real future holdout | SKIPPED: missing sufficient recorded time-local evidence and independent subsequent dataset |
| GitHub Actions run 37350816343 | FAIL at platform startup: account locked due to billing; runner_id 0, zero steps executed. Linux regression result is unavailable, not a code-test failure. |
| Vercel PR preview | FAIL at provider build quota (`build-rate-limit`); no successful preview deployment claimed. |

A first frontend build correctly failed the old strategy hash guard before manifest update; it was an expected authorized-change guard failure. The final guard passed without deleting verification. The synthetic comparison dataset SHA256 is `5c1609a5768aff2bf4c06e7a9cdd5df529be67e5bd18407915c6d6f8095db363`. Raw archives and private runtime data remain local and excluded from Git.

The original repair was reviewed in PR #2 of the legacy repository. The maintained source was subsequently published to `angelmalevbiz-debug/neomemecoins` at `9634414e2ea109afd83b5f78e0277a6bc530f2d0`; its Pages run `37359080106` and PAPER regression run `37359080111` succeeded. The served HTML, JavaScript and CSS were checked against that run's exact artifact. The migrated regression result was 259 root tests plus 59 backend tests. These successful checks supersede the old account-lock/quota failures above for the maintained Pages site.

Production backend installation has not happened because no administrative host session is available. At 2026-10-05 19:07 UTC its gateway still allowed only the legacy Pages origin; the shared backend still reported `ORDER_FLOW_GOLD_SIGNAL_VERIFIED_V7` and had no `paper_training` object. Updating a web build cannot restart or update its Python services. The shared primary reset does not satisfy the requested all-account reset.

### 2026-10-08 documentation re-verification

Executed in a clean worktree on `main` at `54fec89` plus this documentation change, with the repository `.venv` and `npm ci`:

| Executed command | Result |
| --- | --- |
| `.venv/Scripts/python.exe scripts/run_python_checks.py` | PASS: `tests/` 574 tests and `backend/tests/` 148 tests; temporary account paths, both child exit codes 0 |
| `npm run lint` | PASS, TypeScript noEmit |
| `npm run check:strategy` | PASS: `WINNER_ENSEMBLE_PAPER_V1` strategy hash and all 48 support-file hashes on the `UTF8_LF` basis |
| `npm run build` | PASS, strategy/extension guards, Vite and extension archive; nonfatal Vite chunk-size warning |

Facts that supersede the older blocks: the shared PAPER account publishes `entry_policy_version = WINNER_ENSEMBLE_VERIFIED_ENTRY_V4` and `exit_policy_version = HONEST_NET_EXIT_V1` (`strategy-lock.json`); `scripts/check_pages_backend.py` accepts that label, the opt-in `ORDER_FLOW_BALANCED_V4` label and the two earlier labels; the Lab registers 34 books; the latest successful Pages deployment is recorded in [DEPLOYMENT_STATUS.md](DEPLOYMENT_STATUS.md). `backend/engine_entry_policy.py` still defines the internal constant `POLICY_VERSION = 'ORDER_FLOW_VALIDATED_THRESHOLDS_V9'`; nothing publishes it (the engine reports `winner_ensemble.ENTRY_POLICY_VERSION`), and renaming it would change a locked support-file hash, so it is left unchanged and recorded here as a stale label.

### 2026-10-08 tape decoder shadow path and seat shedding (current)

Executed in a worktree on `main` at `2ee0df1` plus this change, with the repository `.venv` and `npm ci`. See [tape-decoder-shadow-and-seat-shedding.md](tape-decoder-shadow-and-seat-shedding.md) for the evidence; the tape service must be restarted by the owner for the change to take effect, and the shadow decoder path stays DEGRADED until `NEO_TAPE_VALIDATED_DECODER_VERSIONS` lists it after the 24 h comparison.

| Executed command | Result |
| --- | --- |
| `.venv/Scripts/python.exe scripts/run_python_checks.py` | PASS: `tests/` 661 tests and `backend/tests/` 148 tests; temporary account paths, both child exit codes 0 |
| `npm run lint` | PASS, TypeScript noEmit |
| `npm run check:strategy` | PASS: unchanged `WINNER_ENSEMBLE_PAPER_V1` strategy hash and 50 support-file hashes on the `UTF8_LF` basis |
| `npm run build` | PASS, strategy/extension guards, 32 frontend tests, Vite and extension archive; nonfatal Vite chunk-size warning |
