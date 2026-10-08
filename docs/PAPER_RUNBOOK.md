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

The main monitor and the 36-strategy Lab consume the same live tape and recent market snapshot. Position marks reuse that shared market snapshot; the Lab does not issue a separate price request for each open position. The main process launches its independent training worker and bounded writer. The worker has no provider client. Price/safety checks and tape must become valid before entries; UNKNOWN/DEGRADED data correctly stays WAIT. Start with an API budget appropriate to the actual authenticated provider plan.

Primary repository defaults (`backend/market_monitor.py`, mirrored in `strategy-lock.json` with `primary_daily_and_drawdown_caps_enabled: true`) are a $100 daily loss cap, a 20% drawdown cap, maximum full-loss capital $250, exposure 100%, $200 notional and eight positions; these are explicitly visible and are **not** the conservative runbook settings above. Earlier revisions of this runbook recorded both caps as disabled (`0`); that was the 2026-10-05 default and is historical. New training default risk limits remain active. Do not silently change an existing account's limits.

Opt-in per-account engine profiles are selected per account in the gateway registry while services are stopped, never by default: `ORDER_FLOW_ADAPTIVE` ([ORDER_FLOW_ADAPTIVE_OCT4_RESTORE.md](ORDER_FLOW_ADAPTIVE_OCT4_RESTORE.md)) and `COST_FIRST_ESTABLISHED_PAPER_V1` (cost-first universe with `EXIT_IMPACT_EMERGENCY_V2`; enable, verify and acceptance criteria in [COST_FIRST_ENGINE_PROFILE.md](COST_FIRST_ENGINE_PROFILE.md)).

Every engine account, every Lab book and the training probe apply `DEFENSIVE_ENTRY_LAYER_V1` before quotes ([DEFENSIVE_ENTRY_LAYER.md](DEFENSIVE_ENTRY_LAYER.md)): pools younger than 12 h, LP-pullable pools, fake-market-cap pools, reused tickers, hot or crashing pools and pools with two consecutive losses in the last 6 h in that account or book are not entered; exits are unchanged (the adaptive exit context and the training learners keep the V1 market score). The tape scheduler gives no seat to a structurally blocked pool and no new seat to a hot one (a running lease runs out); its own heat warm-up is log-only and no ledger's loss memory applies to seats. Expect a heat warm-up after each engine or Lab restart (`heat_history_warming` in `entry_diagnostics`, with `warming_windows`): no entry for 15 minutes, and none into pools at a fee tier ≥ 100 bps for 60 minutes, because the heat history is in memory only. A `defensive_entry_error` reason means one candidate's evaluation raised and was blocked (fail closed); the layer's `status()` counts these. Each service keeps a ticker sidecar next to its state file (`state.ticker_registry.json`, `strategy_lab.ticker_registry.json`, `live_tape.ticker_registry.json`) with its continuous market coverage, saved at most every 5 minutes and on every clean stop (main, Lab and tape). A corrupt sidecar (it reads but does not parse) is first copied to `<sidecar>.corrupt-<ms>` and then replaced; one that cannot be read (an I/O error such as a sharing violation) is never overwritten during that run, is read again every 60 s and merged once readable, and until then the registry adopts no sibling's coverage (`load_status` `UNREADABLE_NOT_OVERWRITTEN`, `layer.ticker_registry.sidecar_read`). Pools younger than 14 days are not entered until that service's registry has watched the market for 24 h without a gap over 60 min (`rug_ticker_registry_warming`; `layer.ticker_registry.coverage` shows `coverage_hours` and `warming`). A registry whose coverage does not vouch at start (a new personal account, a deleted file, an outage over 60 min, or a current coverage still under 24 h) merges the other services' sidecars read-only at start and adopts a sibling's current coverage (`layer.ticker_registry.seed`, `coverage.adopted_from`); a running registry does the same at once when a gap over 60 min restarts its coverage and every 5 minutes while its coverage is under 24 h (`seed.running_reseeds`), so a tape whose polls stalled for over an hour while main kept scanning recovers main's coverage at its next poll without a restart, and a registry that vouches still merges the other services' sightings, never their coverage, every 5 minutes (`TICKER_REGISTRY_SEED_V4`, `seed.sighting_merges`), so the Lab and the tape learn within about 10 minutes the relaunches main saw among the Gecko pools its 90-coin bound cuts; personal engines also read main's sidecar through `NEO_MAIN_MARKET_STATE_PATH`, which the gateway sets. Coverage stamped more than 5 minutes after the clock (a sidecar written by a test run with a fixed clock, a backward clock step) never vouches and is dropped (`TICKER_COVERAGE_CLOCK_V1`, `coverage.future_drops`). Main and every personal engine publish the layer status as `defensive_entry_layer` on `/state` after every scan, also while the account is paused; `entry_diagnostics.defensive_entry.layer` holds the same status but refreshes only while the engine evaluates entries.

**First deploy of the layer** (every command from the live checkout root, the folder that holds `.runtime\accounts`):

The whole procedure, from Stop to Start, must fit in the 60-minute coverage window that begins at the last journalled market row (shortly before Stop): plan the sync and the seed run before stopping.

1. Stop the services with `.\scripts\start_local_paper.ps1 -Action Stop` and wait for `All owned PAPER services stopped and flushed.` The seed tool may only run after Stop has completed, the first run and `--replace-stale` alike: a running main keeps its registry in memory and rewrites `state.ticker_registry.json` at its next periodic save (within 5 minutes), and the Lab, the tape and personal engines seed only when their registry starts, so a seed written under running services is silently lost and every registry warms for 24 h. The tool refuses a runtime directory whose `services\processes.json` exists without `services\stop.request` (Stop writes that marker, Start removes it). The marker proves only that Stop ran: if Stop ends with `Owned services are still flushing; no forced termination performed.`, wait until `.\scripts\start_local_paper.ps1 -Action Status` shows `running` False for every service before step 4 (never kill a service to hurry this; the 60-minute window keeps running, so a long flush may leave the procedure to end with 24 h of warming).
2. Sync the release files into the live checkout now, with the services stopped and before the seed: every path that `git diff --name-only <deployed commit>..<release commit>` (run in the development repository) lists under `backend\`, `scripts\`, `src\` and `docs\`, plus `strategy-lock.json`. That includes the new modules `backend\structural_rug_guard.py`, `backend\entry_defense.py`, `backend\heat_veto.py`, `backend\pool_loss_memory.py` and `backend\lab_forward_tests.py`, and the seed tool `scripts\build_ticker_registry_seed.py`. Do not pick files by hand: `backend\strategy_lab.py` and `backend\tape_pool_scheduler.py` (imported by `backend\live_tape.py`) import `lab_forward_tests` at the top, so without it the tape fails at import, never writes its ready file, Start's 30 s ready wait fails and its catch writes `services\stop.request`, which stops main as well while the 60-minute window keeps running.
3. Check the sync before the seed, from the live checkout root: `node scripts\verify-strategy-lock.mjs` must print `STRATEGY LOCK OK`. It needs only Node built-ins and hashes the strategy file and every pinned support file (`support_files_sha256` in `strategy-lock.json`, which pins every runtime module of the release, `backend\lab_forward_tests.py` and `scripts\local_paper_service.py` included). A missing file fails with `ENOENT` and a stale one with `STRATEGY LOCK FAILED: <path> changed.`; both exit non-zero. Complete the sync and rerun it until it passes; never start the services on a failed check.
4. Build main's sidecar from main's training journal, in place (the tool opens the journal read-only and nothing appends to it while the services are stopped, so do not copy it: a copy of a journal of tens of GB only costs disk and minutes of the 60-minute window; the output must not exist): `.venv\Scripts\python.exe scripts\build_ticker_registry_seed.py --journal .runtime\accounts\training\observations.jsonl --out .runtime\accounts\state.ticker_registry.json`. The replay takes about 4 minutes per replayed journal day on this PC (54.6 MB/s; the journal grows about 13.9 GB a day and only a PAPER reset starts it again); it replays only the last `--window-days` (default 15: the 14-day ticker retention plus the 24 h coverage span) and at most `--max-replay-minutes` (default 40) of journal at that rate, so a journal of 2 days takes about 8-10 minutes and an older one at most about 40 minutes, keeping about the last 9 journal days of ticker memory instead of 14 (`replay.limited_by_replay_budget`, `effective_window_days`, `estimated_replay_minutes`). If the sync and the stop already took long, lower `--max-replay-minutes` so that Stop-to-Start stays under 60 minutes. The tool judges coverage at the clock read after the replay: it exits 0 only when the sidecar vouches (`vouches_at_end` true, `coverage_at_now.coverage_hours` at least 24), prints `start_services_before_utc` (the last journalled market row plus 60 minutes, UTC) and `clock.elapsed_seconds`, and exits 2 when it wrote the sidecar but the coverage at the end is lapsed or under 24 h. No rerun helps after exit 2: when the printed `coverage.coverage_hours` (at the journal's end) is under 24, the journal itself holds a gap over 60 minutes in its last 24 h (`coverage.resets`, `last_gap_minutes`); when it is 24 or more but `coverage_at_now` has lapsed, the stop and the run together outlasted the 60 minutes, and a rerun only extends the outage. Start the services anyway (step 5) and expect 24 h of warming for pools younger than 14 days. The journal holds the bounded 90-coin scan feed plus entry, probe and position rows, not the Gecko new pools cut by that bound, so the seed is a Lab/tape-grade ticker memory (the view the research used), not main's untrimmed one. If an earlier attempt left `.runtime\accounts\state.ticker_registry.json` (the services were started before the seed, a guarded deploy rolled back after the new main ran, or a first try failed) and its coverage is no longer current (`observed_until` more than 60 minutes ago), still under 24 h or stamped ahead of the wall clock, rerun the same command with `--replace-stale`, still with the services stopped; it copies the old file to `state.ticker_registry.json.replaced-<ms>`, merges its sightings, refuses to touch a sidecar whose current coverage is already 24 h or more and never replaces one it cannot read. Moving the old file into the release backup folder with the services stopped and rerunning the tool is equally safe (a ticker sidecar is market memory, not ledger data). The Lab, tape and personal sidecars need no action because they seed from main's.
5. Start the services (`.\scripts\start_local_paper.ps1 -Action Start`) before the printed `start_services_before_utc` (the last journalled market row plus 60 minutes; held-position and position-mark rows never extend coverage).
6. Verify on main's `GET /state` (read-only) once main has scanned and the tape has polled: `defensive_entry_layer.ticker_registry.load_status` is `LOADED` and its `coverage.warming` is false (published after every scan, also while main is paused); `strategy_lab.activity_config.defensive_entry_state.ticker_registry.coverage.warming` is false, with `seed.sources` showing `SEEDED` for `state.ticker_registry.json` or `coverage.adopted_from` set; `live_tape_status.entry_scheduling.defensive_entry.layer.ticker_registry.coverage.warming` is false. If main reports warming, check its `coverage.last_gap_minutes` first: over 60 means its first scan came more than 60 minutes after `observed_until`, so the coverage restarted; the journal now holds that gap too, a rerun yields the same short coverage and only adds a second outage, and the only outcome is 24 h of warming. Otherwise the seed did not reach main (for example `load_status` is not `LOADED`, or `coverage.covered_since` is the start time): stop the services, rerun step 4 with `--replace-stale`, start them and verify again. On the same `/state`, check the forward-test identity: `strategy_lab.activity_config.lab_forward_tests.config_hashes` must equal `lab_forward_tests.config_hashes` in the deployed `strategy-lock.json` (the four pinned hashes, also in [LAB_FORWARD_TESTS.md](LAB_FORWARD_TESTS.md)) and `strategy_lab.activity_config.lab_forward_tests.cost_model_overrides` must be empty. A `NEO_LAB_*` cost override in the Lab's environment changes the hashes and the four books then refuse every entry (`lab_forward_cost_model_mismatch`, with `cost_model_mismatched_fields`): remove the override and restart the Lab; a test under another cost model needs new, versioned book ids.

Without this procedure every engine and Lab book enters no pool younger than 14 days for the first 24 h. Before enabling `COST_FIRST_ESTABLISHED_PAPER_V1` for an account, and for accounts already on it at the first deploy, check that the account's `defensive_entry_layer.ticker_registry.coverage.warming` is false on that engine's `/state`; it is published after every scan, also while the account is paused (the cost-first account was paused on 2026-10-08), whereas `entry_diagnostics.defensive_entry.layer` appears only once the engine evaluates entries again after `/control/start`. `cap_limited_horizon_days` below 14 means the 40,000-entry bound, not the retention, limits ticker memory. Check `entry_diagnostics.defensive_entry` (main), each book's `entry_diagnostics.defensive_entry` (Lab) and `live_tape_status.entry_scheduling.defensive_entry` (seats) for counts, reasons and examples.

The Lab's four `LAB_FORWARD_TESTS_V1` books (`LAB_A_SURGE_EST_GUARD`, `RND_LAB_A`, `LAB_B_DIP_MKTDIP_GUARD`, `RND_LAB_B`; [LAB_FORWARD_TESTS.md](LAB_FORWARD_TESTS.md)) forward-test two pre-registered research hypotheses against random controls at a fixed $200: heat is log-only for them (flags recorded on each position and close), booked P&L adds CALIB_V1 per leg, every close records `net50_usd` and the frozen `lab_config_hash`. Check `entry_diagnostics.lab_forward` (universe and signal counts, the LAB_B `regime` with `med15_pct` and `pools`) and `strategy_lifecycle` (`LAB_FORWARD_KILL_RULE_V1` evidence and, on the two hypothesis books, the `promotion_gate` for owner review; it never promotes). After a Lab restart LAB_A needs a pool's second observation and LAB_B 15 minutes of history before they can signal. A book retired by the kill rule (`lab_forward_kill_rule_retired`) only stops new entries. A hypothesis book whose balance cannot fund the next $200 entry (below $200.10, nothing open) shows `strategy_lifecycle.status = cash_exhausted` and `lab_forward_cash_exhausted`. A random control keeps entering while its hypothesis can (`LAB_FORWARD_CONTROL_CONTINUITY_V1`): its met kill rule shows `reason = control_kill_rule_deferred`, and once its balance cannot fund $200 it enters at zero capital (`capital_mode = zero_capital_control`; those closes never move the balance); it becomes `cash_exhausted` or retired only after its hypothesis stopped. A signal that waits only on the price cross-check is retried for up to 60 s on the pool's current observation (`LAB_FORWARD_SIGNAL_CARRY_V1`, `lab_forward_price_check_pending`); `entry_diagnostics.lab_forward.signal_carry` and the gate count the signals still lost to a pending check. Forward books never request the Lab's RugCheck + Jupiter price probe (their random draws would otherwise feed arbitrary pools into the RugCheck, RPC and Jupiter budget main uses); they rely on the GeckoTerminal reference within the carry. A forward position whose exact pool has no usable mark beyond 70 min closes as `VANISHED_NO_FRESH_MARK` at its last mark minus 10%, but only while the shared feed is alive. A drained pool (reported liquidity 0) is booked at 0; an exact-pair refresh that reports no liquidity at all is unknown, not drained (`LAB_FORWARD_MARK_LIQUIDITY_V1`): it is no usable mark (`quote_unavailable_reason = exact_pool_liquidity_unknown`). Setting any `NEO_LAB_*` cost knob changes the four config hashes and stops the four books' entries (`lab_forward_cost_model_mismatch`, fail closed; `cost_model_overrides` names the knobs). A new test with new funding, parameters or cost model needs new, versioned book ids; never edit or reset a ledger to restart one. The Lab rewrites and fsyncs its whole ledger every loop, and the controls keep closing for as long as their hypothesis runs: `strategy_lab.persistence` on `/state` publishes the last write's `ledger_bytes`, `write_seconds` and `write_share_of_poll` (`LAB_PERSIST_METRICS_V1`); a share approaching 1 means the 2 s loop is falling behind. These books take **no tape seat**: they never read flow, and tape policy `STABLE_COST_AWARE_TAPE_DISCOVERY_V6_NO_PINS_FOR_FLOW_FREE_LAB_BOOKS` leaves their positions unpinned (`live_tape_status.entry_scheduling.unpinned_flow_free_lab_positions`), so the `NEO_TAPE_MAX_PAIRS` seats stay with main, personal engines and the flow-gated Lab books.

`GET /state` includes `paper_training`: portfolio capitals/returns, open/completed trades, raw simulation versus unique observations/episodes, refusals, failure fees, queue drops/lag, last training, active version, history and control comparison. The live Pages build displays those fields.

## Recording and causal replay

The bridge coalesces only identical observation IDs in a bounded recent-ID cache, then writes accepted rows to `<account directory>/training/observations.jsonl`. It stores availability/source times, exact mint/pool, flow, safety/price provenance, main quote evidence and explicit model assumptions. It makes no per-experiment API request. The independent worker evaluates up to 100 complete rows per batch, atomically saves learner state with its byte-offset checkpoint, and resumes there after restart; the main bot never waits for this work. A hard stop (service restart, `taskkill /F`) during a save abandons a full-size `.training.json<8 characters>` temporary; the worker deletes its own such files older than 10 minutes at start and at most every 10 minutes after a save, and never touches `training.json`, `observations.jsonl` or any other file. Queue drops remain explicit evidence gaps and block promotion. Copy recordings after flushing/stopping the producer, or use complete newline-terminated records only.

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

Facts that supersede the older blocks: the shared PAPER account publishes `entry_policy_version = WINNER_ENSEMBLE_VERIFIED_ENTRY_V5` (`WINNER_ENSEMBLE_VERIFIED_ENTRY_V4` before `DEFENSIVE_ENTRY_LAYER_V1`) and `exit_policy_version = HONEST_NET_EXIT_V1` (`strategy-lock.json`); `scripts/check_pages_backend.py` accepts that label, the opt-in `ORDER_FLOW_BALANCED_V5` label (`ORDER_FLOW_BALANCED_V4` before the layer), both pre-layer labels and the two earlier labels; the Lab registers 40 books (34, the `COST_FIRST_*` pair and the four `LAB_FORWARD_TESTS_V1` books, all added 2026-10-08); the latest successful Pages deployment is recorded in [DEPLOYMENT_STATUS.md](DEPLOYMENT_STATUS.md). `backend/engine_entry_policy.py` still defines the internal constant `POLICY_VERSION = 'ORDER_FLOW_VALIDATED_THRESHOLDS_V9'`; nothing publishes it (the engine reports `winner_ensemble.ENTRY_POLICY_VERSION`), and renaming it would change a locked support-file hash, so it is left unchanged and recorded here as a stale label.

### 2026-10-08 tape decoder shadow path and seat shedding (current)

Executed in a worktree on `main` at `2ee0df1` plus this change, with the repository `.venv` and `npm ci`. See [tape-decoder-shadow-and-seat-shedding.md](tape-decoder-shadow-and-seat-shedding.md) for the evidence; the tape service must be restarted by the owner for the change to take effect, and the shadow decoder path stays DEGRADED until `NEO_TAPE_VALIDATED_DECODER_VERSIONS` lists it after the 24 h comparison.

| Executed command | Result |
| --- | --- |
| `.venv/Scripts/python.exe scripts/run_python_checks.py` | PASS: `tests/` 670 tests and `backend/tests/` 148 tests (after the review follow-up: separate `shadow_events` table, shedding on zero decoded swaps, retry floor across feed gaps, body budget limited to the current selection); temporary account paths, both child exit codes 0 |
| `npm run lint` | PASS, TypeScript noEmit |
| `npm run check:strategy` | PASS: unchanged `WINNER_ENSEMBLE_PAPER_V1` strategy hash and 50 support-file hashes on the `UTF8_LF` basis |
| `npm run build` | PASS, strategy/extension guards, 32 frontend tests, Vite and extension archive; nonfatal Vite chunk-size warning |
