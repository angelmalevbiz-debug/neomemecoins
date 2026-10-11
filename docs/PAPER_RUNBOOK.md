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

The maintained Pages frontend can use the local PAPER services without Contabo. The local runtime is isolated under the ignored `.runtime/accounts` directory. It starts a $1,000 main PAPER account, nine separate $500 learning books, 40 independent strategy Lab books (29 TEST books at $500, Fast Scalper at $100, the four `PROMOTED_PAPER` cohort books EARLY, MOMENTUM, PRECISION and ULTRA_PRECISION at $250 each, the isolated `COST_FIRST_CONTROL`/`COST_FIRST_SCALED` TEST pair at $500 each, and the four `LAB_FORWARD_TESTS_V1` TEST books at $500 each; every Lab book admits at most a modeled round trip of 0.5 × its net stop, `LAB_ACTIVE_V6`: 1.5% at the standard 3% stop, 2.5% and 2.75% for the forward-test books' 5% and 15% stops; see STRATEGY_VALIDATION.md and LAB_FORWARD_TESTS.md), one shared market recorder, and a per-user gateway on loopback ports 8878/8879. It does not modify remote/server accounts.

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

## Watchdog scheduled task

`scripts/local_paper_watchdog.ps1` keeps the four owned services (main, tape, lab, gateway) running and is itself run by the Windows scheduled task `NEO Local PAPER Watchdog`. Install or update the task from the checkout that owns the services (on this PC `C:\Users\Chavd\neomemecoins`, not the dev checkout), as the signed-in Windows user:

```powershell
.\scripts\install_local_paper_watchdog.ps1
```

The task is a limited interactive task without stored credentials. It has two triggers: at logon, and a `-Once` trigger that repeats every 5 minutes (`-RepeatMinutes`) with no end. `MultipleInstances IgnoreNew` makes a tick a no-op while the watchdog runs, and the watchdog's named mutex exits a second copy immediately, so the repeat only matters after the instance has died. Re-running the installer updates the definition and leaves a running instance alone. Remove it with `-Remove`.

Verify from the owning checkout:

```powershell
Get-ScheduledTask -TaskName 'NEO Local PAPER Watchdog' | Select-Object State
Get-ScheduledTaskInfo -TaskName 'NEO Local PAPER Watchdog' | Select-Object LastRunTime, LastTaskResult, NextRunTime
.\scripts\local_paper_watchdog.ps1 -Probe
```

`State` must be `Running`, `NextRunTime` must be set, and the probe must list every manifest service as running with main/gateway listening. The probe is read-only: it writes no log line, takes no lock and starts nothing. It also lists the per-user engines (`owner = gateway`, one row per engine worker with its listening port). Those engines are spawned, health-checked and revived by the gateway itself (`ensure_engine` and `revive_known_engines` in `backend/user_gateway.py`); they carry no `--service` argument and no manifest record, so the watchdog reports them and never stops or starts one. The loop writes a `Per-user engines` line to `.runtime/accounts/services/watchdog.log` whenever the set of engine PIDs or ports changes.

### 2026-10-07 outage of the watchdog task

Observed on this PC on 2026-10-08: the task showed `State Ready`, `LastTaskResult 0xC000013A`, `LastRunTime 2026-10-07 17:32:44`, empty `NextRunTime`, and no watchdog process. The watchdog log ended at `23:15:53 Recovery: requesting Start.` without `Watchdog stopped.`, and the running cohort (started 23:15:54–23:16:03) was parented to the dead watchdog PID. Findings:

- `0xC000013A` is `STATUS_CONTROL_C_EXIT`: the hidden PowerShell console received a close or Ctrl+C event. The `finally` block never ran, the PowerShell engine log has a start (event 400) but no stop (event 403) for that process, and the user stayed logged on (the services survived). The `NEO Local Gateway Tunnel` supervisor task shows the same result code while its `cloudflared` child kept running, so whatever closed the consoles targeted the PowerShell hosts, not their process trees. The exact sender is unknown because the `Microsoft-Windows-TaskScheduler/Operational` log is disabled on this PC, so no task history (events 110/111/330) exists. It was not a Task Scheduler timeout (`ExecutionTimeLimit PT0S`), not a logoff, and not a crash (no Application log entry).
- The task had only an at-logon trigger, so nothing fired again after the instance died. Its `RestartOnFailure` (3 × 1 min) did not re-launch it either: `LastRunTime` stayed at 17:32:44. The fix above adds the repeating trigger instead of relying on restart-on-failure.
- The same evening, cohorts that were started outside the watchdog (20:01, 22:24, 23:08) all ended within minutes without the normal `stopped with persistent state` line, each time while a tool sandbox session was starting or ending, and the watchdog restarted them (20:06, 22:40, 23:15). Start and stop the PAPER services through the watchdog or the installer, not from a tool job's console, so the services do not die with that console.

To get task history for the next incident, an administrator can enable the log once (system setting, not done by the installer):

```powershell
wevtutil set-log Microsoft-Windows-TaskScheduler/Operational /enabled:true
```

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

The main monitor and the 40-book Lab consume the same recent market snapshot, and main and the flow-gated Lab books the same live tape; the four `LAB_FORWARD_TESTS_V1` books never read flow and take no tape seat (tape policy V6). Position marks reuse that shared market snapshot; the Lab does not issue a separate price request for each open position. The main process launches its independent training worker and bounded writer. The worker has no provider client. Price/safety checks and tape must become valid before entries; UNKNOWN/DEGRADED data correctly stays WAIT. Start with an API budget appropriate to the actual authenticated provider plan.

Primary repository defaults (`backend/market_monitor.py`, mirrored in `strategy-lock.json` with `primary_daily_and_drawdown_caps_enabled: true`) are a $100 daily loss cap, a 20% drawdown cap, maximum full-loss capital $250, exposure 100%, $200 notional and eight positions; these are explicitly visible and are **not** the conservative runbook settings above. Earlier revisions of this runbook recorded both caps as disabled (`0`); that was the 2026-10-05 default and is historical. New training default risk limits remain active. Do not silently change an existing account's limits.

Opt-in per-account engine profiles are selected per account in the gateway registry while services are stopped, never by default: `ORDER_FLOW_ADAPTIVE` ([ORDER_FLOW_ADAPTIVE_OCT4_RESTORE.md](ORDER_FLOW_ADAPTIVE_OCT4_RESTORE.md)) and `COST_FIRST_ESTABLISHED_PAPER_V1` (cost-first universe with `EXIT_IMPACT_EMERGENCY_V2`; enable, verify and acceptance criteria in [COST_FIRST_ENGINE_PROFILE.md](COST_FIRST_ENGINE_PROFILE.md)).

Every engine account, every Lab book and the training probe apply `DEFENSIVE_ENTRY_LAYER_V1` before quotes ([DEFENSIVE_ENTRY_LAYER.md](DEFENSIVE_ENTRY_LAYER.md)): pools younger than 12 h, LP-pullable pools, fake-market-cap pools, reused tickers, hot or crashing pools and pools with two consecutive losses in the last 6 h in that account or book are not entered; exits are unchanged (the adaptive exit context and the training learners keep the V1 market score). The tape scheduler gives no seat to a structurally blocked pool and no new seat to a hot one (a running lease runs out); its own heat warm-up is log-only and no ledger's loss memory applies to seats. Expect a heat warm-up after each engine or Lab restart (`heat_history_warming` in `entry_diagnostics`, with `warming_windows`): no entry for 15 minutes, and none into pools at a fee tier ≥ 100 bps for 60 minutes, because the heat history is in memory only. A `defensive_entry_error` reason means one candidate's evaluation raised and was blocked (fail closed); the layer's `status()` counts these. Each service keeps a ticker sidecar next to its state file (`state.ticker_registry.json`, `strategy_lab.ticker_registry.json`, `live_tape.ticker_registry.json`) with its continuous market coverage, saved at most every 5 minutes and on every clean stop (main, Lab and tape). A corrupt sidecar (it reads but does not parse) is first copied to `<sidecar>.corrupt-<ms>` and then replaced; one that cannot be read (an I/O error such as a sharing violation) is never overwritten during that run, is read again every 60 s and merged once readable, and until then the registry adopts no sibling's coverage (`load_status` `UNREADABLE_NOT_OVERWRITTEN`, `layer.ticker_registry.sidecar_read`). Pools younger than 14 days are not entered until that service's registry has watched the market for 24 h without a gap over 60 min (`rug_ticker_registry_warming`; `layer.ticker_registry.coverage` shows `coverage_hours` and `warming`). A registry whose coverage does not vouch at start (a new personal account, a deleted file, an outage over 60 min, or a current coverage still under 24 h) merges the other services' sidecars read-only at start and adopts a sibling's current coverage (`layer.ticker_registry.seed`, `coverage.adopted_from`); a running registry does the same at once when a gap over 60 min restarts its coverage and every 5 minutes while its coverage is under 24 h (`seed.running_reseeds`), so a tape whose polls stalled for over an hour while main kept scanning recovers main's coverage at its next poll without a restart, and a registry that vouches still merges the other services' sightings, never their coverage, every 5 minutes (`TICKER_REGISTRY_SEED_V4`, `seed.sighting_merges`), so the Lab and the tape learn within about 10 minutes the relaunches main saw among the Gecko pools its 90-coin bound cuts; personal engines also read main's sidecar through `NEO_MAIN_MARKET_STATE_PATH`, which the gateway sets. Coverage stamped more than 5 minutes after the clock (a sidecar written by a test run with a fixed clock, a backward clock step) never vouches and is dropped (`TICKER_COVERAGE_CLOCK_V1`, `coverage.future_drops`). Main and every personal engine publish the layer status as `defensive_entry_layer` on `/state` after every scan, also while the account is paused; `entry_diagnostics.defensive_entry.layer` holds the same status but refreshes only while the engine evaluates entries.

**Heat-history continuity update.** The memory-only restart behavior described
above is superseded for services with a state path by
[`PAPER_OBSERVED_HEAT_CONTINUITY_V1`](PAPER_HEAT_CONTINUITY.md). They checkpoint
actual windows every 30 seconds and on clean stop. A fully validated checkpoint
with an observation clock no more than 120 seconds old resumes those windows;
missing/stale/invalid evidence and genuinely gapped pairs still warm normally.
No signal, safety gate or exit threshold is relaxed. Ticker sidecar coverage
and the flat-engine deployment requirement below are unchanged.

**First deploy of the layer** (every command from the live checkout root, the folder that holds `.runtime\accounts`, unless a step names the development repository):

Ticker coverage carries over only if main's first scan after Start records a market observation within 60 minutes of the seed's `observed_until` (the last journalled market row the seed replayed). Choose how to build the seed before you begin:

- **Seed before Stop (first deploy only; recommended).** Possible while `.runtime\accounts\state.ticker_registry.json` does not exist. The pre-release services have no ticker registry and never write that file (`git grep ticker_registry <deployed commit> -- backend scripts` in the development repository finds nothing), and the seed tool opens the journal read-only (`'rb'`, read/write sharing), so main keeps appending while it replays. The seed is written to a scratch folder outside `.runtime` and copied in after Stop. The 60-minute window then starts at the end of the replay, and the outage is only Stop, backup, seed copy, sync, lock check and Start: a few minutes.
- **Seed after Stop, in place.** Required when that sidecar already exists and for every `--replace-stale` rerun. The replay (about 4 minutes per journal day, at most about 40 minutes by default) runs inside the outage, so expect an outage of up to about 50 minutes.

**Later deploys need no seed, and after an outage over 60 minutes no seed can help.** Once the layer runs, every service keeps its own sidecar. When main's first scan after Start comes within 60 minutes of its last scan before Stop, the sidecars carry the coverage (the tool refuses a sidecar that still vouches). When the outage is longer, main's training journal holds the same gap, because nothing scans or journals while the services are stopped: a replay's coverage ends at the last scan before Stop, the tool writes a sidecar that does not vouch and exits 2, and a rerun only lengthens the outage. Every registry then warms for 24 h for pools younger than 14 days (`rug_ticker_registry_warming`); start anyway, the sightings are kept. So keep a later deploy's Stop-to-Start under 60 minutes. The in-place seed helps on a later deploy only when the sidecars themselves are lost (main's missing or corrupt and no sibling sidecar vouching) while the outage stays under 60 minutes; `--replace-stale` handles a corrupt one.

While the services are stopped nothing marks or exits an open position. Main and the personal engines must therefore hold no position before Stop (step 1); the Lab keeps trading until Stop, which is one more reason to keep the outage short. On 2026-10-08 a 53-minute outage turned a main position's last mark of −$3.39 into a −$17.68 close.

0. **Release file list and dry run** (the list in the development repository). Check out the release commit cleanly (for example `git worktree add <release checkout> <release commit>`) and list the files to sync: the changed paths under `backend\` (except `backend\tests\`), `scripts\` and `docs\` plus `strategy-lock.json`, and from `src\` only the changed files that `support_files_sha256` in the release's `strategy-lock.json` pins (today `src\lib\labStrategyView.ts`). Never sync `tests\`, `backend\tests\`, `research\` or any other `src\` file: GitHub Pages builds the frontend from the repository, and the live checkout keeps an unrelated local edit of `src\App.tsx`, which `scripts\start_local_dashboard.ps1` runs from (the old `App.tsx` compiles against the new `labStrategyView.ts`, whose export changes are additive).

   ```powershell
   $locked = (git show "<release commit>:strategy-lock.json" | Out-String | ConvertFrom-Json).support_files_sha256.PSObject.Properties.Name
   git diff --name-only --diff-filter=ACMR <deployed commit> <release commit> -- backend scripts docs src strategy-lock.json |
       Where-Object { (-not $_.StartsWith('src/') -or $locked -contains $_) -and -not $_.StartsWith('backend/tests/') } |
       Set-Content <release-files.txt>
   ```

   This release deletes no file. Then, still before step 1 and while the services run, **dry-run the sync** from the live checkout root: copy the live `backend`, `scripts`, `docs`, `src\lib` and `strategy-lock.json` into a scratch folder outside `.runtime`, overlay the release file list from the release checkout, and run the lock check and an import of the four service modules there. Both only read (the imports write no file and open no port), so nothing live changes; a missed or stale pinned file (for example `docs\LAB_FORWARD_TESTS.md`, which the lock pins) or a missing new module then shows up now, not after Stop, where every rerun lengthens the outage and eats into the seed's 60-minute window.

   ```powershell
   $python = (Resolve-Path .venv\Scripts\python.exe).Path
   $dry = Join-Path $env:TEMP ('neo-release-dry-run-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
   New-Item -ItemType Directory (Join-Path $dry 'src') | Out-Null
   foreach ($dir in 'backend', 'scripts', 'docs', 'src\lib') { Copy-Item -Recurse $dir (Join-Path $dry $dir) }
   Copy-Item strategy-lock.json $dry
   foreach ($file in Get-Content <release-files.txt>) {
       $target = Join-Path $dry $file
       New-Item -ItemType Directory -Force (Split-Path $target) | Out-Null
       Copy-Item -Force (Join-Path <release checkout> $file) $target
   }
   Push-Location $dry
   try {
       node scripts\verify-strategy-lock.mjs
       if ($LASTEXITCODE -ne 0) { throw 'dry run: lock check failed' }
       & $python -c "import sys; sys.path.insert(0, 'backend'); import market_monitor, strategy_lab, live_tape, user_gateway; print('IMPORT OK')"
       if ($LASTEXITCODE -ne 0) { throw 'dry run: import failed' }
   } finally { Pop-Location }
   ```

   It must print `STRATEGY LOCK OK` and `IMPORT OK`; otherwise fix the list (or the release) and repeat before going on. Step 6 copies the same list with the same loop.
1. **Flat engines.** For main (port 8878) and each personal engine (its `engine_port` in `.runtime\accounts\user_accounts.json`), read `GET http://127.0.0.1:<port>/state` and note `running`. Pause new entries on each running engine with `POST http://127.0.0.1:<port>/control/stop` (it pauses entries only; open positions keep their marks and exits), then wait until `/state` on every engine shows an empty `positions` list, the same check the guarded deploy script makes before it stops anything. Never stop the services while an engine holds a position.
2. **Seed before Stop only: build the seed now, into a scratch folder outside `.runtime`.** The tool is new in this release, so run the release checkout's copy with the live interpreter, from the live checkout root:

   ```powershell
   $seedDir = Join-Path $env:TEMP ('neo-ticker-seed-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
   New-Item -ItemType Directory $seedDir | Out-Null
   .venv\Scripts\python.exe <release checkout>\scripts\build_ticker_registry_seed.py --journal .runtime\accounts\training\observations.jsonl --out (Join-Path $seedDir 'state.ticker_registry.json')
   ```

   It must exit 0 (`vouches_at_end` true); note the printed `start_services_before_utc`. Keep the output outside `.runtime`: the tool's check for running services looks only at the output's folder and its parents, and a sidecar may reach `.runtime\accounts` only while the services are stopped (step 5). The replay does not use up the window here (the window starts at the last row it reads, near its end), so `--max-replay-minutes` may be raised (for example to 70) to replay the whole 15-day window. Continue with step 3 at once. If Start cannot happen at least 5 minutes before `start_services_before_utc`, discard the folder and build a new seed into a new one.
3. **Stop** the services with `.\scripts\start_local_paper.ps1 -Action Stop` and wait for `All owned PAPER services stopped and flushed.` A seed may reach `.runtime\accounts` only after Stop has completed, the first one and `--replace-stale` alike: a running main of this release keeps its registry in memory and rewrites `state.ticker_registry.json` at its next periodic save (within 5 minutes), and the Lab, the tape and personal engines seed only when their registry starts, so a seed written under running services is silently lost and every registry warms for 24 h. The tool refuses a runtime directory whose `services\processes.json` exists without `services\stop.request` (Stop writes that marker, Start removes it). The marker proves only that Stop ran: if Stop ends with `Owned services are still flushing; no forced termination performed.`, wait until `.\scripts\start_local_paper.ps1 -Action Status` shows `running` False for every service before step 4 (never kill a service to hurry this; the 60-minute window keeps running). Personal engines are not in that manifest: they leave on the same stop marker, but the gateway waits at most 30 s for each engine it started itself (logging `A personal engine is still stopping; no forced termination performed` when one outlasts that) and not at all for an engine it adopted from an earlier run, and Stop reports none of them. Before step 4, confirm that no engine port still listens; this must print nothing:

   ```powershell
   $ports = (Get-Content .runtime\accounts\user_accounts.json -Raw | ConvertFrom-Json).accounts.PSObject.Properties.Value |
       ForEach-Object { $_.engine_port } | Where-Object { $_ }
   $ports | Where-Object { Get-NetTCPConnection -State Listen -LocalPort $_ -ErrorAction SilentlyContinue }
   ```

   Repeat it while it prints a port (never kill an engine). An engine still answering on its port at Start is reused by the gateway as it is (`start_engine` returns any healthy engine on the configured port), so it would keep running the pre-release code; step 10 checks each engine's identity.
4. **Back up** the ledgers and every file the sync will overwrite into `.runtime\release-backup-<stamp>`, with a SHA-256 manifest. This is the only copy of the pre-release state: the new Lab rewrites `strategy_lab.json` with its 40 books on its first loop.

   ```powershell
   $backup = '.runtime\release-backup-' + (Get-Date -Format 'yyyyMMdd-HHmmss')
   $files = @('state.json', 'strategy_lab.json', 'strategy_lab_compact.json', 'user_accounts.json') | ForEach-Object { Join-Path '.runtime\accounts' $_ }
   $files += Get-ChildItem '.runtime\accounts\users\*\state.json' | Resolve-Path -Relative
   $files += Get-Content <release-files.txt>
   $manifest = foreach ($file in ($files | Where-Object { Test-Path $_ })) {
       $target = Join-Path $backup $file
       New-Item -ItemType Directory -Force (Split-Path $target) | Out-Null
       Copy-Item $file $target
       $hash = (Get-FileHash $file -Algorithm SHA256).Hash
       if ($hash -ne (Get-FileHash $target -Algorithm SHA256).Hash) { throw "backup mismatch: $file" }
       "$hash  $file"
   }
   $manifest | Set-Content (Join-Path $backup 'manifest.sha256')
   ```

5. **Seed before Stop only: copy the seed in.** `.runtime\accounts\state.ticker_registry.json` must still not exist; if it does, seed in place (step 8) instead:

   ```powershell
   if (Test-Path .runtime\accounts\state.ticker_registry.json) { throw 'a sidecar exists: seed in place (step 8)' }
   Copy-Item (Join-Path $seedDir 'state.ticker_registry.json') .runtime\accounts\state.ticker_registry.json
   ```

6. **Sync** every path of the release file list from the release checkout into the live checkout, with the loop the dry run used (it creates missing parent folders):

   ```powershell
   foreach ($file in Get-Content <release-files.txt>) {
       $parent = Split-Path $file
       if ($parent) { New-Item -ItemType Directory -Force $parent | Out-Null }
       Copy-Item -Force (Join-Path <release checkout> $file) $file
   }
   ```

   That list includes the new modules `backend\structural_rug_guard.py`, `backend\entry_defense.py`, `backend\heat_veto.py`, `backend\pool_loss_memory.py` and `backend\lab_forward_tests.py`, and the seed tool `scripts\build_ticker_registry_seed.py`. Do not pick files by hand: `backend\strategy_lab.py` and `backend\tape_pool_scheduler.py` (imported by `backend\live_tape.py`) import `lab_forward_tests` at the top, so without it the tape fails at import, never writes its ready file, Start's 30 s ready wait fails and its catch writes `services\stop.request`, which stops main as well while the 60-minute window keeps running.
7. **Lock check**, after the sync and before the in-place seed or Start, from the live checkout root: `node scripts\verify-strategy-lock.mjs` must print `STRATEGY LOCK OK`. It needs only Node built-ins and hashes the strategy file and every pinned support file (`support_files_sha256` in `strategy-lock.json`, which pins every runtime module of the release, `backend\lab_forward_tests.py` and `scripts\local_paper_service.py` included). A missing file fails with `ENOENT` and a stale one with `STRATEGY LOCK FAILED: <path> changed.`; both exit non-zero. Complete the sync and rerun it until it passes; never start the services on a failed check. After a passing dry run (step 0) a failure here means the sync itself missed or changed a file.
8. **Seed after Stop only: build main's sidecar** from main's training journal, in place (the tool opens the journal read-only and nothing appends to it while the services are stopped, so do not copy it: a copy of a journal of tens of GB only costs disk and minutes of the 60-minute window; the output must not exist): `.venv\Scripts\python.exe scripts\build_ticker_registry_seed.py --journal .runtime\accounts\training\observations.jsonl --out .runtime\accounts\state.ticker_registry.json`. The replay takes about 4 minutes per replayed journal day on this PC (54.6 MB/s; the journal grows about 13.9 GB a day and only a PAPER reset starts it again); it replays only the last `--window-days` (default 15: the 14-day ticker retention plus the 24 h coverage span) and at most `--max-replay-minutes` (default 40) of journal at that rate, so a journal of 2 days takes about 8-10 minutes and an older one at most about 40 minutes, keeping about the last 9 journal days of ticker memory instead of 14 (`replay.limited_by_replay_budget`, `effective_window_days`, `estimated_replay_minutes`). If the sync and the stop already took long, lower `--max-replay-minutes` so that Stop-to-Start stays under 60 minutes. The tool judges coverage at the clock read after the replay: it exits 0 only when the sidecar vouches (`vouches_at_end` true, `coverage_at_now.coverage_hours` at least 24), prints `start_services_before_utc` (the last journalled market row plus 60 minutes, UTC) and `clock.elapsed_seconds`, and exits 2 when it wrote the sidecar but the coverage at the end is lapsed or under 24 h. No rerun helps after exit 2: when the printed `coverage.coverage_hours` (at the journal's end) is under 24, the journal itself holds a gap over 60 minutes in its last 24 h (`coverage.resets`, `last_gap_minutes`); when it is 24 or more but `coverage_at_now` has lapsed, the stop and the run together outlasted the 60 minutes, and a rerun only extends the outage. Start the services anyway (step 9) and expect 24 h of warming for pools younger than 14 days. The journal holds the bounded 90-coin scan feed plus entry, probe and position rows, not the Gecko new pools cut by that bound, so the seed is a Lab/tape-grade ticker memory (the view the research used), not main's untrimmed one. If an earlier attempt left `.runtime\accounts\state.ticker_registry.json` (the services were started before the seed, a guarded deploy rolled back after the new main ran, or a first try failed) and its coverage is no longer current (`observed_until` more than 60 minutes ago), still under 24 h or stamped ahead of the wall clock, rerun the same command with `--replace-stale`, still with the services stopped; it copies the old file to `state.ticker_registry.json.replaced-<ms>`, merges its sightings, refuses to touch a sidecar whose current coverage is already 24 h or more and never replaces one it cannot read. Moving the old file into the release backup folder of step 4 with the services stopped and rerunning the tool is equally safe (a ticker sidecar is market memory, not ledger data). The Lab, tape and personal sidecars need no action because they seed from main's.
9. **Start** the services (`.\scripts\start_local_paper.ps1 -Action Start`) at least 5 minutes before the printed `start_services_before_utc` (the last journalled market row plus 60 minutes; held-position and position-mark rows never extend coverage). Before coverage carries over, Start must start main, main must load `state.json` and open port 8878, and its first discovery fetch must finish; started in the last minute, main's first scan can land after the deadline, coverage then restarts and every service warms for 24 h.
10. **Verify** on main's `GET /state` (read-only) once main has scanned and the tape has polled: `defensive_entry_layer.ticker_registry.load_status` is `LOADED` and its `coverage.warming` is false (published after every scan, also while main is paused); `strategy_lab.activity_config.defensive_entry_state.ticker_registry.coverage.warming` is false, with `seed.sources` showing `SEEDED` for `state.ticker_registry.json` or `coverage.adopted_from` set; `live_tape_status.entry_scheduling.defensive_entry.layer.ticker_registry.coverage.warming` is false. If main reports warming, check its `coverage.last_gap_minutes` first: over 60 means its first scan came more than 60 minutes after `observed_until`, so the coverage restarted; the journal now holds that gap too, a rerun yields the same short coverage and only adds a second outage, and the only outcome is 24 h of warming. Otherwise the seed did not reach main (for example `load_status` is not `LOADED`, or `coverage.covered_since` is the start time): stop the services, rerun step 8 with `--replace-stale`, start them and verify again. On the same `/state`, check the forward-test identity: `strategy_lab.activity_config.lab_forward_tests.config_hashes` must equal `lab_forward_tests.config_hashes` in the deployed `strategy-lock.json` (the four pinned hashes, also in [LAB_FORWARD_TESTS.md](LAB_FORWARD_TESTS.md)) and `strategy_lab.activity_config.lab_forward_tests.cost_model_overrides` must be empty. A `NEO_LAB_*` cost override in the Lab's environment changes the hashes and the four books then refuse every entry (`lab_forward_cost_model_mismatch`, with `cost_model_mismatched_fields`): remove the override and restart the Lab; a test under another cost model needs new, versioned book ids. Personal engines are not checked by any of this: the gateway revives them in the background after Start, ignores a failed revival and reuses any engine already answering on the account's port. So before resuming a personal engine, read its `GET http://127.0.0.1:<engine_port>/state` (read-only) and require: `config.signal_strategy` equal to the account's `signal_strategy` in `user_accounts.json` (`WINNER_ENSEMBLE_PAPER_V1` when the account names none); `config.entry_policy_version` of this release, `ORDER_FLOW_BALANCED_V5` for `ORDER_FLOW_ADAPTIVE`, `COST_FIRST_ESTABLISHED_ENTRY_V2` for `COST_FIRST_ESTABLISHED_PAPER_V1` and `WINNER_ENSEMBLE_VERIFIED_ENTRY_V5` on the default strategy (a pre-release engine reports an older one; at `e964804`, `ORDER_FLOW_BALANCED_V4`, `COST_FIRST_ESTABLISHED_ENTRY_V1` or `WINNER_ENSEMBLE_VERIFIED_ENTRY_V4`); and `defensive_entry_layer` present (a pre-release engine has none) with `ticker_registry.seed.sources` showing `SEEDED` for `state.ticker_registry.json` (main's sidecar, read through `NEO_MAIN_MARKET_STATE_PATH`) or `ticker_registry.coverage.adopted_from` set, and `ticker_registry.coverage.warming` false. An engine that does not answer was not revived: it starts when its account is next opened or at the next Start. An engine with pre-release versions outlived Stop: leave it paused, stop the services, wait until its port is free (step 3) and start them again. Then resume entries with `POST http://127.0.0.1:<port>/control/start` only on the engines that were running in step 1 and passed these checks: a paused engine stays paused across the restart (its `running` flag is saved in its state).

**Rollback.** If Start fails or step 10 shows a broken release: first check the forward-test positions (below), then stop the services, copy every code file the manifest lists (the paths of the release file list) back from `.runtime\release-backup-<stamp>`, check each against its recorded hash, run `node scripts\verify-strategy-lock.mjs` (it must pass for the restored release) and start the services. The pre-release code has no ticker registry and ignores the sidecars; files the release added stay and are not imported. The ledger copies are the pre-release state: restore one only on an owner decision, because that discards everything the services recorded since Start.

Files are clean after a rollback, ledger state is not. The released Lab rewrites `strategy_lab.json` with the four `LAB_FORWARD_TESTS_V1` books, and the pre-release Lab keeps a book it does not register (`runtime_compatibility.status` `preserved_inactive`, listed in `registry_compatibility.preserved_open_position_ids`) but never marks or exits its position. The pre-release tape pins every Lab position and gives entries only the seats the pins leave (`NEO_TAPE_MAX_PAIRS`, 4 in `start_local_paper.ps1`, minus the pins). So a forward position open at the rollback stays frozen at its last mark for the whole rollback and holds one of the 4 seats that main, the personal engines and the flow-gated Lab books share; with all four books holding one, no entry seat is left, and nothing reports it. After a later redeploy the release closes such a position at once, at its first fresh mark and far past its 60-minute max hold (`ABSOLUTE_MAX_HOLD_60` at whatever price then prevails, or `VANISHED_NO_FRESH_MARK`), and that close counts in the book's balance, its kill rule and the promotion gate although it says nothing about the hypothesis. Therefore:

- **Bounded wait, while the release still runs.** The forward books cannot be drained: the Lab has no control that pauses their entries, and the random controls enter again soon after each close (they are in the market most of the time; see Tape seats in [LAB_FORWARD_TESTS.md](LAB_FORWARD_TESTS.md)), so a moment with all four books flat may not come. Read `strategy_lab.books.<id>.position` on main's `GET /state` (read-only) for `LAB_A_SURGE_EST_GUARD`, `RND_LAB_A`, `LAB_B_DIP_MKTDIP_GUARD` and `RND_LAB_B` once a minute, and run Stop right after the first read in which all four are null. Wait at most 75 minutes from the first read: by then every position open at that read has closed (a forward position closes within its 60-minute max hold, or about 70 minutes after entry when its pool loses its mark: 10 minutes of vanish grace while the shared feed is alive), and a longer wait only trades one open position for the next. When the 75 minutes pass without such a read, run Stop then. Do not wait at all when nothing can close any position, which is a state of the Lab loop as a whole, never of one position: Start failed or main does not answer `/state` (the Lab takes its feed from main, and a loop whose feed fetch fails marks and closes nothing); or the Lab is not running or its loop keeps failing (`strategy_lab.updated_at` on `/state` no longer advances, or `strategy_lab.status` is not `online`, on two reads a minute apart). Also do not wait when the release must be stopped at once. Keep waiting in every other case. A single forward position whose own `mark_received_at` stops advancing while the Lab loop runs has lost its pool's mark, and it still closes on its own within the 75 minutes (as `VANISHED_NO_FRESH_MARK` about 70 minutes after its entry while the shared feed is alive, or earlier at a fresh mark). When main's feed itself stalls (`last_scan_at` no longer advances while main still answers), the Lab still marks its positions through the DexScreener exact-pair refresh and closes them at their exits or their 60-minute max hold; only a position whose pool vanished then stays open (the vanish rule needs a live feed), and the 75-minute bound ends the wait for it.
- **After Stop**, check the ledger itself (read-only; the services are stopped, so it is final). This prints one line (book, `trade_no`, `pairAddress`, `opened_at`) per open forward position and nothing when all four are flat:

  ```powershell
  .venv\Scripts\python.exe -c "import json; books = json.load(open('.runtime/accounts/strategy_lab.json', encoding='utf-8'))['books']; [print(i, p.get('trade_no'), p.get('pairAddress'), p.get('opened_at')) for i in ('LAB_A_SURGE_EST_GUARD', 'RND_LAB_A', 'LAB_B_DIP_MKTDIP_GUARD', 'RND_LAB_B') for p in [(books.get(i) or {}).get('position')] if p]"
  ```

- **Record what is left, then roll back.** For each printed line (the wait ran out, a book entered between the last read and Stop, or there was no wait), record the line in the rollback notes and tell the owner: each such position stays frozen and holds one tape seat until a redeploy, and its eventual late close (the same `trade_no` and `opened_at`) is for the owner to exclude from the kill-rule and gate evidence. Then roll back as above. Restoring the pre-release `strategy_lab.json` instead (owner decision only) removes the four books together with everything else the Lab recorded since Start.

**Any other Stop of a release that runs these books** (a later deploy, a restart of the services) cannot drain them either. Time it with the same bounded wait when it can be timed, run the same after-Stop check and record each printed line. Nothing marks such a position while the services are stopped; after Start the released Lab marks it again with its recorded exits and closes it at its first fresh mark if a trigger was crossed or its max hold passed during the outage (or as `VANISHED_NO_FRESH_MARK` when its pool is gone). Tell the owner which recorded `trade_no` closed after the outage: its exit was delayed by the outage, not decided by the hypothesis. Keep such a Stop short; main and the personal engines must still be flat before it (step 1).

Without this procedure every engine and Lab book enters no pool younger than 14 days for the first 24 h. Before enabling `COST_FIRST_ESTABLISHED_PAPER_V1` for an account, and for accounts already on it at the first deploy, check that the account's `defensive_entry_layer.ticker_registry.coverage.warming` is false on that engine's `/state`; it is published after every scan, also while the account is paused (the cost-first account was paused on 2026-10-08), whereas `entry_diagnostics.defensive_entry.layer` appears only once the engine evaluates entries again after `/control/start`. `cap_limited_horizon_days` below 14 means the 40,000-entry bound, not the retention, limits ticker memory. Check `entry_diagnostics.defensive_entry` (main), each book's `entry_diagnostics.defensive_entry` (Lab) and `live_tape_status.entry_scheduling.defensive_entry` (seats) for counts, reasons and examples.

The Lab's four `LAB_FORWARD_TESTS_V1` books (`LAB_A_SURGE_EST_GUARD`, `RND_LAB_A`, `LAB_B_DIP_MKTDIP_GUARD`, `RND_LAB_B`; [LAB_FORWARD_TESTS.md](LAB_FORWARD_TESTS.md)) forward-test two pre-registered research hypotheses against random controls at a fixed $200: heat is log-only for them (flags recorded on each position and close), booked P&L adds CALIB_V1 per leg, every close records `net50_usd` and the frozen `lab_config_hash`. Check `entry_diagnostics.lab_forward` (universe and signal counts, the LAB_B `regime` with `med15_pct` and `pools`) and `strategy_lifecycle` (`LAB_FORWARD_KILL_RULE_V1` evidence and, on the two hypothesis books, the `promotion_gate` for owner review; it never promotes). After a Lab restart LAB_A needs a pool's second observation and LAB_B 15 minutes of history before they can signal. A book retired by the kill rule (`lab_forward_kill_rule_retired`) only stops new entries. A hypothesis book whose balance cannot fund the next $200 entry (below $200.10, nothing open) shows `strategy_lifecycle.status = cash_exhausted` and `lab_forward_cash_exhausted`. A random control keeps entering while its hypothesis can (`LAB_FORWARD_CONTROL_CONTINUITY_V1`): its met kill rule shows `reason = control_kill_rule_deferred`, and once its balance cannot fund $200 it enters at zero capital (`capital_mode = zero_capital_control`; those closes never move the balance); it becomes `cash_exhausted` or retired only after its hypothesis stopped. A signal that waits only on the price cross-check is retried on the pool's current observation for up to 60 s after its run's first signal, a newer match never extending that window (`LAB_FORWARD_SIGNAL_CARRY_V2`, `lab_forward_price_check_pending`); `entry_diagnostics.lab_forward.signal_carry` and the gate count the signals still lost to a pending check. Forward books never request the Lab's RugCheck + Jupiter price probe (their random draws would otherwise feed arbitrary pools into the RugCheck, RPC and Jupiter budget main uses); they rely on the GeckoTerminal reference within the carry. A forward position whose exact pool has no usable mark beyond 70 min closes as `VANISHED_NO_FRESH_MARK` at its last mark minus 10%, but only while the shared feed is alive. A drained pool (reported liquidity 0) is booked at 0; a mark that omits the liquidity is a reported 0 on the shared feed and the exact-pair refresh alike (`LAB_FORWARD_MARK_LIQUIDITY_V2`, main's normalization): its price triggers the exits and the sale is booked as a drain. Setting any `NEO_LAB_*` cost knob changes the four config hashes and stops the four books' entries (`lab_forward_cost_model_mismatch`, fail closed; `cost_model_overrides` names the knobs). A new test with new funding, parameters or cost model needs new, versioned book ids; never edit or reset a ledger to restart one. The Lab rewrites and fsyncs its whole ledger every loop, and the controls keep closing for as long as their hypothesis runs: `strategy_lab.persistence` on `/state` publishes the last write's `ledger_bytes`, `write_seconds` and `write_share_of_poll` (`LAB_PERSIST_METRICS_V1`); a share approaching 1 means the 2 s loop is falling behind. Action threshold: when `write_share_of_poll` reaches 0.5 (the writes alone take half of each 2 s loop), ask the owner to decide between archiving Lab history out of the ledger and writing the full ledger only when it changes; Lab history is never trimmed, and a review measured one loop's position update plus write at 0.15 s on the 2026-10-08 ledger (1.9 MB) and 1.26 s on 10 times its history (before the switch to `json.dumps`, which cut serialization about threefold). These books take **no tape seat**: they never read flow, and tape policy `STABLE_COST_AWARE_TAPE_DISCOVERY_V6_NO_PINS_FOR_FLOW_FREE_LAB_BOOKS` leaves their positions unpinned (`live_tape_status.entry_scheduling.unpinned_flow_free_lab_positions`), so the `NEO_TAPE_MAX_PAIRS` seats stay with main, personal engines and the flow-gated Lab books.

`GET /state` includes `paper_training`: portfolio capitals/returns, open/completed trades, raw simulation versus unique observations/episodes, refusals, failure fees, queue drops/lag, last training, active version, history and control comparison. The live Pages build displays those fields.

## Recording and causal replay

The bridge coalesces only identical observation IDs in a bounded recent-ID cache, then writes accepted rows to `<account directory>/training/observations.jsonl`. It stores availability/source times, exact mint/pool, flow, safety/price provenance, main quote evidence and explicit model assumptions. It makes no per-experiment API request. The independent worker evaluates bounded batches of complete rows, atomically saves learner state with its byte-offset checkpoint, and resumes there after restart; the main bot never waits for this work. A hard stop (service restart, `taskkill /F`) during a save abandons a full-size `.training.json<8 characters>` temporary; the worker deletes its own such files older than 10 minutes at start and at most every 10 minutes after a save, and never touches `training.json`, `observations.jsonl` or any other file. Queue drops remain explicit evidence gaps and block promotion. Copy recordings after flushing/stopping the producer, or use complete newline-terminated records only.

`PAPER_OBSERVATION_PARTS_V1` bounds future journal files to 128 MiB per part. A large legacy file remains unchanged; numbered continuation files and `observations.jsonl.parts.json` preserve one logical byte cursor. Keep/archive/restore **all parts and the manifest together**. See [PAPER_OBSERVATION_PARTS.md](PAPER_OBSERVATION_PARTS.md). The live repair performs no reset, does not erase previous evidence gaps, and cannot establish profitability.

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

## Honest edge report from ledger copies (read-only)

`scripts/paper_edge_report.py` measures closed PAPER trades from **copies** of engine ledgers (`state.json` for the main and per-user accounts) and Strategy Lab ledgers (`strategy_lab.json`). It never writes to its inputs and refuses an `--output` inside an input directory. It also refuses any input or output that resolves under a live runtime directory (one named `.runtime` or holding `user_accounts.json`), and any `--archive-dir` that contains one, before opening a ledger; `--allow-runtime` overrides that deliberately and is not part of the normal procedure. Copy finished archives (or a backup) to a folder outside the runtime first, never the files an engine is still writing, because a partially written ledger is not evidence and the live runtime directory is off limits while engines run. Write the report outside the runtime as well.

```powershell
# 1. Copy finished archives to a working folder outside the runtime (RUNTIME = the runtime directory).
$work = Join-Path $env:TEMP 'neo-edge'
Copy-Item -Recurse -Path RUNTIME\accounts\archive -Destination (Join-Path $work 'ledgers\archive')
# 2. Read only the copy and write the report outside the runtime.
.venv/Scripts/python.exe scripts/paper_edge_report.py --archive-dir "main=$work\ledgers\archive" --since 2026-10-06 --output "$work\reports\edge"
.venv/Scripts/python.exe scripts/paper_edge_report.py --state main=BACKUP/state.json --state main=BACKUP/archive/reset-X/state.json --state BACKUP/users/USER_ID/state.json --lab BACKUP/strategy_lab.json --output REPORTS/edge
```

Account labels: `LABEL=PATH` (or `--archive-dir LABEL=DIR`) is explicit. Without a label the account comes from the layout only: a ledger under `users/<account-uuid>/` belongs to that user, and any other ledger falls back to `main`; no other directory name (for example a UUID-named scratch folder) is ever read as an account. The tool refuses, and asks for a label, when a ledger is under `users/` without a UUID directly below it, when a ledger's `demo_session_id` names a different user (`USER-<8 chars>-...`) than its path, or when two unlabelled ledgers fall back to `main` with different `demo_session_id` values (reset ids such as `PAPER-RESET-...` do not identify an account, so differing copies outside `users/` may be different accounts). A directory label applies only to ledgers that are not under `users/<account-uuid>/`. Paths in messages show UUID components shortened to eight characters.

The report prints Markdown and, with `--output`, writes `<output>.json` and `<output>.md`. For each engine account, strategy/policy version and exit reason, and for each Lab book, entry policy version and exit reason, it reports: unique closed trades (deduplicated by trade id across every copy; conflicting copies of one id are excluded and counted), wins/losses/breakeven on a net basis, average net win and loss, expectancy per trade in dollars and percent, a cluster-bootstrap 95% interval of expectancy over `(mint, pool, UTC hour)` clusters, the same expectancy after a +50 bps per leg cost stress, profit factor (`null` below ten losses), maximum drawdown, total modeled cost versus gross mark move with its components and the cost-only loss share, an implied gross (net minus the entry round-trip quote result), median hold, trades per hour and per day for the supplied window, the period, distinct mints and clusters. Drawdown is computed per segment and never across a reset: each engine (account, session) and each Lab book between resets (a `trade_no` restart, or the implied balance before a trade returning to the book's starting balance) is its own closed-trade equity path; the report gives the worst segment's drawdown in dollars and as a percent of that segment's peak, plus the segment count. A negative mark-based total cost (exit marks at `STALE_MARKET_EXIT` closes can lag the fill) is flagged unreliable; use the implied gross and the components there. Policy-version and exit-reason rows pool every account (or Lab book) listed in their `accounts` (`books`) field and describe that pooled set, not any one account; only per-account and per-book rows describe one ledger. Per-user account labels are shortened to eight characters. Rows below 20 closed trades, 30 clusters or five calendar days say `insufficient data`; nothing in the report is a profitability claim, and a sufficient sample is only ever labelled a descriptive sample.

### Replay identity (REPLAY_LIVE_DERIVED_WINDOWS_V1)

The primary replay is only a measurement instrument if it books the trades the live engine booked from the same recorded rows. Until this reconciliation it rejected every genuine live entry: the adapter applied its own 4 s literal to the first preflight buy quote (the live three-step preflight makes it 4.6-4.7 s old at the recorded row), a 30 s literal to the cached risk pass (the live engine accepts `promoted_entry_guard.SAFETY_MAX_AGE_MS`), read the candidate snapshot at commit where the live commit reads the scanner-refreshed feed, and let rows without quote evidence arm the 15 s quote-retry cooldown. `backend/main_replay.py` now:

- derives every freshness window from the replayed engine's constants and reports each window with its basis under `freshness_windows` (`engine_execution.MAX_AGE_MS`, `FINAL_QUOTE_MAX_AGE_MS`, `PREFLIGHT_PREVIEW_MAX_AGE_MS`, `engine_rug_guard.TTL_MS`, `promoted_entry_guard.SAFETY_MAX_AGE_MS`/`FLOW_MAX_AGE_MS`, `pair_price_integrity.TTL_MS`, `engine_entry_policy.MAX_ENTRY_QUOTE_AGE_MS`); the first preflight buy quote has no wall-clock bound at commit in the live engine, only landing freshness, chronology and the engine's own slot-drift check, so the adapter applies none either;
- evaluates the entry-stage checks of an entry-evidence row at the recorded `preflight_started_at`, the preflight consistency chain (final-quote age, `consistent_preflight`) at the recorded prepare clock `min(row available_at, final buy raw_quote _simulated_fill_at)` so a recording lag after the final quote landed is never counted as quote age, and the commit at the row's availability, exactly as the live quote sequence consumed that time (each clock's basis is reported under `freshness_windows.clock_basis`);
- gives the commit the newest exact-pool feed snapshot and flow computation recorded at or before the row (the live commit re-reads `STATE.feed` and `live_flow`); when the engine recorded a commit-stage rejection milliseconds after the evidence row (same pool, `execution.buy_verified_at`/`sell_verified_at` equal to the bundle's quote times, a commit-stage reason), that row is the same live decision and its flow is used for the commit;
- ticks positions only on recorded position-guard rows (a mark, or an `exit_quote` rejection) and only for the row's pool; scanner snapshots between marks are not failed sell quotes;
- never arms the quote-retry cooldown from a row that carries no quote attempt (`decision_summary.cooldown_arms_suppressed`);
- reports unknown valuation for a position still held past its last recorded mark (`positions_unvalued_at_end`), never a modeled value beyond the last recorded sell quote.

`tests/test_main_replay_identity_v1.py` with `tests/fixtures/replay_identity_v1.jsonl` is the versioned identity check: recorded-schema rows with the measured live timing must book the live trade (pnl within $0.01, same exit reason, same raw quantity) and WAIT with the recorded rejection reasons elsewhere. Regenerate the fixture with `python tests/test_main_replay_identity_v1.py --write` after an intentional schema change only. On the 2026-10-07 main journal slice (23,452 rows, two pools) the reconciled replay reproduces the live ledger: three closes (-6.957, -1.638 EXIT_IMPACT_EMERGENCY; -10.181 STOP_LOSS_NET_TARGET) and the open position at -3.39, with 14,161 of 14,484 recorded rejection rows reproduced; that is measurement identity, not evidence of any edge. `tests/test_main_replay_journal_20261007.py` keeps that check in the suite: `tests/fixtures/replay_main_20261007_slice.jsonl.gz` (1,339 byte-identical recorded journal lines, 26.8 MB uncompressed, pinned by SHA-256) holds the 10 entry-evidence rows, the mark rows of those episodes, exit_quote guard rows, linked commit-outcome rows and the exact-pool rows of the 10 s before each preflight, and `tests/fixtures/replay_main_20261007.expected.json` the booked live pnl values; the replay must reproduce each within $0.01. A commit-stage rejection the engine records after the quote bundle, including the Jupiter price tiebreak (`price_tiebreak_failed`), is linked to that bundle as the same live decision.

### Offline exit variants (REPLAY_EXIT_VARIANT_V1)

`--exit-variant` applies an alternative exit rule set to the same recorded episodes. Supported keys: `stop_pct`, `take_profit_pct`, `disable_exit_impact_emergency`, `max_hold_minutes`. It changes exit decisions only: entry admission, sizing, `planned_stop_net_pct` and `planned_risk_usd` keep the baseline rules, a `max_hold_minutes` override labels its fixed-policy hold exit `MAX_HOLD_<limit>` (for example `MAX_HOLD_30`; the live label stays `MAX_HOLD_60`) while every other exit label is unchanged, and the report is marked `report_kind: EXIT_VARIANT` with the rules, the baseline rules and a label. No run (baseline or variant) overwrites an existing `--output`: replacing one needs `--overwrite` and an existing report of the same `report_kind`, so a baseline is never replaced by a variant or the reverse.

```powershell
.venv/Scripts/python.exe scripts/replay_main.py --input .runtime/datasets/market.jsonl --output .runtime/main-replay/baseline.json --state-dir .runtime/main-replay/baseline-state
.venv/Scripts/python.exe scripts/replay_main.py --input .runtime/datasets/market.jsonl --output .runtime/main-replay/variant-stop3.json --state-dir .runtime/main-replay/variant-stop3-state --exit-variant "stop_pct=3" --variant-label stop3
.venv/Scripts/python.exe scripts/replay_main.py --input .runtime/datasets/market.jsonl --output .runtime/main-replay/variant-no-impact.json --state-dir .runtime/main-replay/variant-no-impact-state --exit-variant "disable_exit_impact_emergency=1,max_hold_minutes=30"
```

Limits: recorded sell quotes exist only while the live engine held the position and stop at the live exit, so a variant that holds longer than the live exit ends with an unvalued position (unknown risk, `liquidation_unavailable` for later entries); only variants that exit no later than the live exit can be valued, and non-entered candidates have no quotes at all. Three closes on one pool in 14 hours are not a sample; no variant result is a profitability claim.

## Lab high-frequency books (LAB_HIGH_FREQUENCY_V1)

The Lab process also runs three PAPER books that trade about 50 times an hour each:
`HF_RND_E95` (random control), `HF_QUIET_E95` and `HF_DIP15_E95`. The full design is in
[LAB_HIGH_FREQUENCY.md](LAB_HIGH_FREQUENCY.md). They are **expected to lose** about 3.6% net50
per trade (about $37-44 per hour per book while they trade). Each has its own $1,000, a $100
booked loss cap per UTC day and a kill rule. They are a measurement and never a promotion
candidate. They are not Lab books: they are absent from `strategy_lab.json`, the lifecycle
and the promotion migration, and they take no tape seat.

**Enable and disable.**

- `NEO_LAB_HF_ENABLED` is `1` by default. Deploy the family only from main: the owner's merge
  is the acceptance of its POOL_LOSS_MEMORY_V1 replacement (DEFENSIVE_ENTRY_LAYER.md).
- Set it to `0` and restart the Lab to stop the family. The journal and the checkpoint stay
  on disk, and the next enabled start resumes from them.
- To retire individual books for good, set `NEO_LAB_HF_RETIRE=HF_QUIET_E95,HF_DIP15_E95`
  (comma-separated ids). Open slots still run to their exits. The control retires on its own
  once both hypotheses are retired. A retirement is journaled, so it survives every restart.
- **Set every `NEO_LAB_HF_*` variable at User scope, never only in a shell.**
  `start_local_paper.ps1` passes the calling process's environment to the Lab, but the
  watchdog's scheduled task restarts the Lab (`-WatchdogRecovery`) with the task's
  environment, not the operator's shell. A value set with `$env:NEO_LAB_HF_ENABLED='0'` is
  lost at the next watchdog recovery: HF resumes trading, and a shell-only cap reverts to
  $100 (a new budget hash).

  ```powershell
  [Environment]::SetEnvironmentVariable('NEO_LAB_HF_ENABLED', '0', 'User')
  # remove it again (back to the default, enabled):
  [Environment]::SetEnvironmentVariable('NEO_LAB_HF_ENABLED', $null, 'User')
  ```

  Then open a new shell and Stop/Start. After every Start and every watchdog recovery, check
  on `GET /state` that `strategy_lab.activity_config.lab_high_frequency.running` (false when
  disabled) and `budget_hash` are what you set. If a watchdog-started Lab still shows the old
  value, the task did not see the new User environment yet: sign out and in (or restart
  Windows), then check again.

**Budget** (budget hash only; a change never starts a new evidence sample):

- `NEO_LAB_HF_START_BALANCE_USD`, default 1000. It applies to a fresh `strategy_lab_hf/`; an
  existing checkpoint keeps its balances.
- `NEO_LAB_HF_DAILY_CAP_USD`, default 100.

An invalid value keeps the default and is listed in
`strategy_lab.activity_config.lab_high_frequency.budget_env_errors`. New orders start each
UTC day d from hour (17 x d) mod 24 (`hf_session_not_open` before): Oct 9 opened at 07:00,
Oct 10 opens at 00:00, Oct 11 at 17:00, Oct 12 at 10:00. Never change the strategy rules through the environment:
the Lab's `NEO_LAB_*` cost knobs change the HF config hashes, and the books then refuse
every order (`hf_cost_model_mismatch`), as the forward-test books do.

**Files**, next to `strategy_lab.json` (override with `NEO_STRATEGY_LAB_HF_DIR`):

- `strategy_lab_hf/journal/<BOOK>/<YYYY-MM-DD>.jsonl`: an append-only journal, one row per
  state change. Row kinds:
  - `order` (`side` entry or exit);
  - `fill`;
  - `cancel`;
  - `close`;
  - `cap`, `session`, `retire`.

  The Lab flushes and fsyncs each touched file once per loop, at the end of the loop
  (`hf_end_loop`), before the checkpoint; a kill checkpoint (every 250 closes) flushes once
  more. Expect about 0.7 MB per hour of 3 x 50 trades, and with the daily cap about 3 trading
  hours per book per day.
- `strategy_lab_hf/state.json`: the checkpoint. It is written atomically every 15 s when
  anything changed and on every clean stop, always after the journal. It is skipped while a
  journaled row is not fully applied (a stop landing inside one), so that row is replayed.
  After a crash or a stop the Lab replays the journal rows above the checkpoint's `seq`
  exactly once. Then:
  - ORDERED entries are cancelled with reason `restart`;
  - open positions resume, and when a slot's first observation after the restart is at most
    10 minutes after its last mark, an exit due during the outage is ordered there
    (`late_exit_restart`);
  - after a longer gap (in practice an outage of about 10 minutes or more) the slot closes as
    `FEED_GAP` at min(pre-gap price, return price), or as `VANISHED` if the pool does not come
    back, flagged `restart_gap` with `restart_outage_ms` and `gap_ms`. That valuation is
    conservative by construction, and the closes count in the cap and the kill rule like any
    other.
- **Deploy, rollback and redeploy:** each one stops the Lab, so keep the HF outage (Stop to
  Start) under 10 minutes where you can. Record the stop and start times and the `trade_no`s of
  the rows with `reason: restart` or `restart_gap` in the deploy notes. A rollback to a
  release without HF leaves `strategy_lab_hf/` on disk untouched; the next release with HF
  resumes from it under the same outage rule.
- `strategy_lab_hf/archive/reset-*/`: a Lab reset (`strategy_lab.reset` flag) moves the
  journal and the checkpoint here. `paper_runtime.py reset-all` moves the whole
  `strategy_lab_hf/` into the reset archive (sha256 manifest with `directory:
  strategy_lab_hf`) and recreates it empty. Nothing is deleted.
- **Restore** with the Lab stopped:
  - A reset-all archive restores with the command in
    [Reset every PAPER account and restore](#reset-every-paper-account-and-restore), using
    that archive's folder. Every sha256 is checked first, the current `strategy_lab_hf/` is
    moved into a new reset archive, and the archived tree is copied back; the archive stays
    intact.
  - A Lab-reset archive (`strategy_lab_hf/archive/reset-<ms>-<id>/`, no manifest): move the
    current `journal/` and `state.json` into a new folder under `strategy_lab_hf/archive/`,
    then move the archived `journal/` and `state.json` back into `strategy_lab_hf/`.

**Read** (main's `GET /state`, read-only):

- `strategy_lab.high_frequency`: the dashboard view, also shown in the Strategies section
  under `Висока честота (HF)`;
- `strategy_lab.activity_config.lab_high_frequency`: `config_hashes`, `budget_hash`,
  `cost_model_mismatches` and `running`;
- `strategy_lab.persistence.hf`: loop milliseconds, `degraded` and `time_budget`
  (`degraded_since`, `degraded_cleared_at`, `degraded_episodes`), `refresh.at` (the last HF
  refresh), `refresh_60s` (`refreshes`, `events`, `universe`, `refreshes_with_universe` and
  `last_universe_at` over the last 60 s), `orders_last_60m`, the journal and checkpoint
  writes (`checkpoint.skipped_in_flight`), `errors` (`count`, `last`, `last_at`,
  `consecutive_loops`), `price_audit.checks_last_60s` (at most 2), the journal `epoch`,
  `mark_feed` (HF's own exact-pair feed, at most one request every 2 s) and `build`
  (`attempts`, `failures`, `last_error`, `next_attempt_at`, `built_at`);
- `strategy_lab.hf_error`: the failing HF calls of the latest loop (it disappears after the
  first clean loop; `persistence.hf.errors` keeps the count). The Lab keeps running, and the
  dashboard shows the failure above the HF books for 10 minutes. A failed HF start shows as
  `build: <error>` and is retried every minute while HF is enabled; the dashboard then says
  `HF не стартира: <error>` instead of `Няма HF данни`.

After a deploy, check that `config_hashes` equal the table in LAB_HIGH_FREQUENCY.md and
`lab_high_frequency.config_hashes` in `strategy-lock.json`, that `strategy_lab.hf_error` is
absent, `persistence.hf.errors.consecutive_loops` is 0 and `persistence.hf.build.failures`
is 0, that `persistence.hf.refresh.at` is current (a few seconds old: a stale value means HF
decisions stopped, even while `running` is true), and that `refresh_60s.universe` is above 0
with a recent `refresh_60s.last_universe_at`. Do not judge the universe by
`refresh.universe`: it is the last 2 s refresh only, and main rescans every 3 s, so it is 0
about one read in three on a healthy runtime. After the 15-minute heat warm-up, and while a
book's session is open and its cap is not tripped, expect 40-50 orders an hour per book.
`hf_degraded` (time budget) lasts at least 5 minutes once tripped.

**Rescore** from a **copy** of the folder (never the live one), outside the runtime:

```powershell
$work = Join-Path $env:TEMP 'neo-hf'
Copy-Item -Recurse -Path RUNTIME\accounts\strategy_lab_hf -Destination (Join-Path $work 'strategy_lab_hf')
.venv/Scripts/python.exe scripts/paper_edge_report.py --lab-hf "$work\strategy_lab_hf" --output "$work\reports\hf"
.venv/Scripts/python.exe scripts/evaluate_paper_lab.py --hf-dir "$work\strategy_lab_hf" --output "$work\reports\hf-metrics.json"
```

- `paper_edge_report.py --lab-hf` counts journal closes as Lab trades, deduplicated by
  (book, config hash, journal epoch, journal seq). Archived resets inside the folder are
  skipped. Its `high_frequency` section reports each hypothesis against the same-period
  control, with a pair-bootstrap CI and the gap within each fee bucket.
- `paper_edge_report.py --archive-dir` skips HF checkpoints (`strategy_lab_hf/state.json`,
  also inside reset archives): they are not engine ledgers. They are listed in the report's
  `skipped_inputs` and on stderr. Pass an archived `strategy_lab_hf` folder (or its
  `archive/reset-<ms>-<id>` folder) with another `--lab-hf` to include its closes. After a
  reset `seq` restarts at 1, but every row carries its journal `epoch` (new at each reset),
  so the archived and the current sessions read together never collide.
- `evaluate_paper_lab.py --hf-dir` reports orders, closes and cancels per hour per (book,
  config hash), with the most slots held at once (per epoch) and `journal_epochs`.
- A journal line torn by a crash (also inside a multi-byte character of a symbol) is counted
  as one malformed line and skipped; it never stops the Lab's load or these reports, so the
  journal is never edited by hand.
- Compare a day's closes with the research simulator
  (`research/hf_study_2026_10_09/hf_synthesis/syn_lib.py`) for the same window when the scan
  log is available.

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

Main and nested training archives are separate: restore each to its matching directory. The `strategy_lab_hf/` archive of the HF books is a separate archive folder too (its manifest names `directory: strategy_lab_hf` and nested paths); restoring it with `--root` set to the accounts folder brings the whole directory back. Restore also archives the current files. Hash mismatch refuses replacement. For code rollback, select the prior reviewed commit and restore its matching saved PAPER account/config archive; do not reuse future-version state blindly. Training rollback is automatic within LEARNER and recorded in version history; it never changes production code.

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

Facts that supersede the older blocks: the shared PAPER account publishes `entry_policy_version = WINNER_ENSEMBLE_VERIFIED_ENTRY_V5` (`WINNER_ENSEMBLE_VERIFIED_ENTRY_V4` before `DEFENSIVE_ENTRY_LAYER_V1`) and `exit_policy_version = HONEST_NET_EXIT_V1` (`strategy-lock.json`); `scripts/check_pages_backend.py` accepts that label, the opt-in `ORDER_FLOW_BALANCED_V5` label (`ORDER_FLOW_BALANCED_V4` before the layer), both pre-layer labels and the two earlier labels; the Lab registers 40 books (34, the `COST_FIRST_*` pair and the four `LAB_FORWARD_TESTS_V1` books, all added 2026-10-08); the latest successful Pages deployment is recorded in [DEPLOYMENT_STATUS.md](DEPLOYMENT_STATUS.md). `backend/engine_entry_policy.py` still defines the internal constant `POLICY_VERSION = 'ORDER_FLOW_VALIDATED_THRESHOLDS_V9'`; nothing publishes it (the engine reports `winner_ensemble.ENTRY_POLICY_VERSION`), and renaming it would change a locked support-file hash, so it is left unchanged and recorded here as a stale label.

### 2026-10-08 tape decoder shadow path and seat shedding (current)

Executed in a worktree on `main` at `2ee0df1` plus this change, with the repository `.venv` and `npm ci`. See [tape-decoder-shadow-and-seat-shedding.md](tape-decoder-shadow-and-seat-shedding.md) for the evidence; the tape service must be restarted by the owner for the change to take effect, and the shadow decoder path stays DEGRADED until `NEO_TAPE_VALIDATED_DECODER_VERSIONS` lists it after the 24 h comparison.

| Executed command | Result |
| --- | --- |
| `.venv/Scripts/python.exe scripts/run_python_checks.py` | PASS: `tests/` 670 tests and `backend/tests/` 148 tests (after the review follow-up: separate `shadow_events` table, shedding on zero decoded swaps, retry floor across feed gaps, body budget limited to the current selection); temporary account paths, both child exit codes 0 |
| `npm run lint` | PASS, TypeScript noEmit |
| `npm run check:strategy` | PASS: unchanged `WINNER_ENSEMBLE_PAPER_V1` strategy hash and 50 support-file hashes on the `UTF8_LF` basis |
| `npm run build` | PASS, strategy/extension guards, 32 frontend tests, Vite and extension archive; nonfatal Vite chunk-size warning |
