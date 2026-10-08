# DEFENSIVE_ENTRY_LAYER_V1: structural rug guard, heat veto and pool loss memory

`DEFENSIVE_ENTRY_LAYER_V1` (`backend/entry_defense.py`) puts three independent vetoes in
front of **every** PAPER entry path:

- `STRUCTURAL_RUG_GUARD_V1` (`backend/structural_rug_guard.py`)
- `HEAT_VETO_STACK_V1` (`backend/heat_veto.py`)
- `POOL_LOSS_MEMORY_V1` (`backend/pool_loss_memory.py`)

The vetoes run before any quote, flow promotion, RugCheck call or Jupiter price probe.
The tape scheduler is the one exception to "all three": its seats serve every ledger, so
it gives no seat to a structurally blocked pool and no new seat to a hot one, but applies
no ledger's loss memory and does not wait for its own heat warm-up (see
[Tape seats](#tape-seats-structural-guard-and-heat-for-new-seats)).

The layer is PAPER only. It removes candidates. It never admits, sizes, prices or exits
anything. Exits of open positions, cost caps, `engine_rug_guard` (`RUG_GUARD_V2`, the
RugCheck report), every ledger and every balance are unchanged. Nothing is reset or
rewritten. The companion score change is an entry change only: the ORDER_FLOW_ADAPTIVE
exit context and the isolated training learners (`PAPER_TRAINING_V1`, including the
`GOLD_ADAPTIVE` book's exits) keep reading the V1 score (see
[Exit context](#exit-context-the-v1-score-stays-the-exit-basis)).

**No profitability claim.** The research behind this change found no strategy with
positive expectancy after costs. These rules measurably cut losses. They do not create
profit.

## Evidence

The research ran on 22.8 h of the engine's own scan log (`obs.sqlite3`: 3.12M rows,
2,042 pairs), with train, holdout and a pre-registered 1.92 h forward window. All numbers
are PAPER.

### Drains

- 157 terminal drains happened among 1,549 PumpSwap SOL pools.
- All 19 drains in pools that ever held at least $250k liquidity came from two
  structural families:
  - **Fake market cap:** 10 drains. The same tickers (USDP, IOF, UDR, DOTF, GOIF, WSOS,
    VSOF, SARP, DAWS, ...) were relaunched as new mints with "market caps" of
    $20M–$1.3B and liquidity/market cap below 2%, at 0.3–4 days old.
  - **LP-pullable:** 9 drains. Liquidity/market cap was at least 1 (1.2–1.9 observed).
    Liquidity went to 0 at an unchanged price, and tickers were recycled (TWEETCRAFT x3,
    SharkTank x4, BORDR, ...).
- Every other drain hit a young pool, with a median age of 12.8 min. Pools under 15 min
  old drained 87% of the time within 2 h.

### Cost-first universe

- 10 of the 64 pools that entered the cost-first universe drained within the 22.8 h.
- 50 of the 61 pools that ever passed the V1 universe were rug-family flagged. The fee
  tier is computed from market cap, so a fake market cap earns the cheapest tier.
- The cost-first account opened 3 rug-family positions within 80 s of going live:
  - WOSE `GhBPuDpt`: $237M, liquidity/market cap 0.58%, 420 min old
  - GOIF `D2pVedgH`: $194M, 0.65%, 375 min
  - SARP `HiPe6mDS`: $83M, 0.99%, 709 min
- `RUG_GUARD_V2` never ran on these families (`checked_at` 0). It passed SharkTank
  `65GHmCby`, which later collapsed.
- Under the old unbounded +9 "good liquidity/MC" bonus, the scorer gave LP-pullable pools
  86.7 on average and SETUP on 96.9% of their rows.

### Re-buying a falling pool

21 of the 23 historical closes were one token (swordinu) re-bought every 30–40 min while
it fell 76%. Three accounts ran the same rule. The 20-minute same-token cooldown has no
memory of losses: it recorded 8,774 cooldown rejections on that pool, and a re-buy
followed each loss.

### Structural guard results (holdout)

- Drain-event recall was 0.897 at a point false-positive rate of 0.148 (liquidity at
  least $20k).
- Drain hazard per position-hour fell from 4.39% to 1.31%. In the cost-first universe it
  fell from 3.48% to 0.99%. Pools with 100–125 bps fees and at least $250k liquidity
  went from 54%/h to fully blocked.
- Random entries still lose: −8.72% per trade without the guard and −5.12% with it
  (calibrated, stressed net50).

### Heat veto results (pre-registered forward test, 1.92 h)

- Full stack: vetoed samples returned −8.73% and kept samples −3.66% at 30 min. The
  difference is −5.07 pp, pair-CI95 [−11.0, −0.95] (confirmed).
- The momentum part alone: −5.73 pp, CI [−12.1, −1.4] (confirmed).
- Attention, crash and turnover parts had the right sign, but their CIs span 0.
- Kept samples still lost −3.1% to −3.7%.

### Caveats

- The 2% fake-market-cap threshold is holdout-informed and not validated. The research
  froze 1% on train, and 2% also catches DAWS-like pools. Fixtures DAWS `BTSpnpim` and
  GOIF `8ZMkMgWM` pass at 1% and block at 2%.
- No fake-cap pool drained in the short forward window. Cost-first trades the guard
  removed did better there (−1.1% vs −2.7%), because these pools drift up on wash buys
  until they are drained. The guard's value is the drain tail, about −100% per event,
  which a 2-hour window cannot show.

## Rules

### STRUCTURAL_RUG_GUARD_V1

This is a pure check that fails closed. It is called as
`check(coin, now, registry) -> {'version', 'blocked', 'reasons', 'liq_mcap', 'age_min', 'mcap', ...}`.

Reasons are evaluated in this order, and every reason that applies is recorded:

| Reason | Rule |
| --- | --- |
| `rug_input_unknown` | Any input is missing, non-finite or ≤ 0: liquidity (`liquidityUsd`, else `liquidity.usd`), market cap (`marketCap`, else `fdv`), pair age (`pairCreatedAt` at `now`), mint, pool, or the ticker registry itself; or the pool is younger than 14 days and its normalized ticker is missing. Only the two ticker rules read the ticker, and they apply only under 14 days, so an established pool whose symbol normalizes to nothing (an emoji, `$`) or to the placeholder is judged by the other rules without one. The feed's placeholder for a missing symbol (`TOKEN`, written by `make_coin` and the Gecko early pools) is a missing ticker, and it is never registered. Evaluation stops here. |
| `rug_lp_pullable` | liquidity / market cap ≥ 1.0 |
| `rug_young_pool` | pair age < 720 min |
| `rug_fake_market_cap` | market cap ≥ $20M, liquidity / market cap < 0.02, and age < 14 days |
| `rug_ticker_reuse` | age < 14 days, and the normalized ticker (alphanumerics only, casefolded) was already seen at or before `now` on another pool with a **different** mint. A token's own second pool, with the same mint, is not a reuse. |
| `rug_ticker_registry_warming` | age < 14 days, no reuse found, and the registry has observed the market continuously for less than 24 h at `now` (see [Coverage](#coverage-rug_ticker_registry_warming)). |

The ticker check is past-only. `TickerRegistry` is fed with every scan's feed, as
(mint, pool, normalized ticker, first seen, last seen). Each process keeps it as a small
JSON sidecar next to its state file (`TICKER_REGISTRY_V2_COVERAGE`):

- `state.ticker_registry.json` for each engine account
- `strategy_lab.ticker_registry.json` for the Lab
- `live_tape.ticker_registry.json` for the tape

The sidecar has these properties:

- It is written atomically (temp file, fsync, replace, Windows retry), on the first
  observation, then at most once every 5 minutes, and again on a clean shutdown (main
  engine, Lab and tape). A crash loses at most 5 minutes of ticker memory.
- A hard stop (TerminateProcess, `taskkill /F`) during a save skips its cleanup and
  leaves the temporary (`.<sidecar>.<8 characters>.tmp`, up to several MB) beside the
  sidecar. The registry deletes its own such files once they are at least 10 minutes old,
  when it starts and at most every 10 minutes after that, before a save
  (`TICKER_REGISTRY_TEMP_SWEEP_V1`, `status().stale_temps.removed`). Only that exact name
  pattern next to its own sidecar is a candidate; no other file is touched.
- Entries unseen for 14 days are pruned. At most 40,000 entries are kept; the least
  recently seen are evicted first. The research scan log saw 2,042 pairs in 22.8 h
  (about 2,150 a day), so 14 days need about 30,000 entries. The earlier 20,000 cap
  would have filled in about 9 days and forgotten a sibling mint after about 8.4 days.
  If the cap still evicts (a busier feed), `status()` publishes `cap_evictions`,
  `cap_evicted_at` and `cap_limited_horizon_days`, the age of the newest sighting the
  cap evicted at that time; this is the effective memory horizon, shorter than 14 days.
  `oldest_last_seen_days` is published too.
- A missing, corrupt or unsupported file starts empty and never stops a service. A
  failed save keeps the old file and is reported in `save_error`. A `TICKER_REGISTRY_V1`
  sidecar (written before coverage existed) still loads its sightings; its coverage
  starts again at the next observation.
- Read failures (`TICKER_REGISTRY_SIDECAR_READ_V1`). A sidecar that cannot be read is not
  corrupt: an I/O error (a Windows sharing violation from a backup or antivirus scan, a
  permission error) is retried 3 times, and if it persists the registry starts empty but
  never overwrites the file during that run (`load_status`
  `UNREADABLE_NOT_OVERWRITTEN`; saves report `OWN_SIDECAR_KEPT_UNREAD`). It reads the
  file again at most every 60 s and, once it reads, merges its sightings and its
  coverage (continuous with this run's when the restart gap was at most 60 min) and
  resumes saving (`LOADED_AFTER_REREAD`). Until then it merges sibling sightings but
  adopts no sibling's coverage, because its own memory is unknown: it stays warming
  (fail closed) instead of vouching with a sibling's coverage for memory it lost.
  Before this rule an I/O error counted as corruption, the intact sidecar was replaced at
  the first save without a copy, and a sibling's coverage vouched for it. Content that
  reads but does not parse is corrupt: it is copied to `<sidecar>.corrupt-<ms>` (no
  service reads that name) before the registry starts empty and replaces it
  (`CORRUPT_STARTED_EMPTY`, `sidecar_read.corrupt_backup`); if the copy fails it is kept
  like an unreadable one (`CORRUPT_NOT_OVERWRITTEN`).

#### Coverage (rug_ticker_registry_warming)

"No other mint seen" only means something once the registry has watched the market. An
empty registry cannot tell a relaunch from a first launch. Some family pools sit just
above the 2% fake-cap line, where ticker reuse is the only rule that blocks them: the
research rows USDF `EpugLBw1` (2.00%, $20.2M, 97 h old) and DOTF `5GaCcKLd` (2.01%,
$20.3M, 135 h old). Before this rule they passed the guard and the cost-first universe
with an empty registry, which is every registry on the first deploy, after a long outage
or after the cap evicted sightings. The rule now fails closed instead:

- The sidecar keeps `covered_since`, the start of the current continuous observation,
  and `observed_until`, the newest market observation. Both survive restarts.
- An observation is a scan with at least one discovered market row
  (`DISCOVERED_MARKET_ROWS_V2`). Its time is the newest `updatedAt` of those rows (at
  most 5 s after the scan clock), so a stale published feed or an empty scan never
  extends coverage. A row whose `sources` name only a held position (`open-position`,
  `open-position-pinned-pair`: `scan_once` adds a held coin under these when discovery
  did not return it) is registered as a sighting but observes no market. When the
  discovery lists fail and only the pairs endpoint answers, main's feed holds just the
  open positions (plus Gecko new pools, if Gecko answers, which are discovered rows);
  before this rule such a feed kept coverage current for as long as a position was open,
  while relaunch siblings went unseen. `coverage.held_only_scans` counts those scans.
  The Lab and the tape read main's published feed with the same `sources`, and personal
  engines run the same scan, so every registry applies the rule.
- A gap longer than 60 min restarts coverage (`coverage.resets`, `last_gap_minutes`). In
  the research log the median other-mint sibling of a reused ticker was visible for
  about 1 min, and 82% of the reuse candidates (liquidity ≥ $20k, 561 cases) had every
  earlier sibling visible for less than 60 min, so a long gap can hide a whole relaunch.
  Routine restarts (minutes) keep coverage.
- When the 40,000-entry cap evicts, `covered_since` moves to the newest evicted
  sighting: memory is complete only after it.
- A pool younger than 14 days with no reuse found is blocked as
  `rug_ticker_registry_warming` until coverage at `now` is at least 24 h (the research
  log covered 22.8 h). Coverage is 0 when the registry's last observation is more than
  60 min before `now`. Pools of 14 days or more are never judged by tickers, so they
  never wait.
- Coverage stamped in the future never vouches (`TICKER_COVERAGE_CLOCK_V1`). An
  `observed_until` more than 5 min after the reference clock was never observed: a
  sidecar written by a test run with a fixed clock (the suites use January 2027), a
  sibling host with a skewed clock, or a backward wall-clock step. Such coverage is not
  current (coverage 0), it is dropped when the own sidecar is loaded or re-read, it is
  never adopted from a sibling at a seed (`seed.sources` then records
  `SEEDED_COVERAGE_AHEAD_IGNORED`; the sightings are still merged), and a span in memory
  is dropped at the next scan, which starts coverage again and seeds at once
  (`coverage.future_drops`, `last_future_ahead_minutes`). Before this rule a future
  stamp kept the coverage current for as long as the clock stayed behind it, and the
  registry ignored every observation up to the stamp, so no gap was ever recorded: in a
  review simulation the Lab, seeded from main after loading such a sidecar, reported
  35 h of coverage and no reset after a 5 h outage and allowed a 3-day-old pool that an
  honest sidecar blocked as `rug_ticker_registry_warming`; it also saved the stamp again,
  and any sibling seeding from it after its own outage would have vouched at once. The
  tolerance is 5 min, not the 5 s feed skew, because the engine's scan and entry threads
  and the offline replay (entry-stage checks at the recorded preflight start) legitimately
  look seconds to a minute behind the newest observation; a smaller backward step delays
  gap detection by at most 5 min.

`check()` publishes `registry_coverage_h`; `status()` publishes `coverage` with
`covered_since`, `observed_until`, `coverage_hours`, `warming`, `resets`,
`last_gap_minutes`, `adopted_from`, `basis`, `held_only_scans`, `clock_version`,
`future_drops` and `last_future_ahead_minutes`.

The cost: on the first deploy, every engine and Lab book enters no pool younger than
14 days for 24 h unless the registries are seeded (below), and again after any outage
longer than 60 min.

#### Seeding (TICKER_REGISTRY_SEED_V4)

A ticker registry is market memory, not account memory: every service sees the same
market. A registry whose own coverage does not vouch after loading its sidecar (new,
empty, legacy, last observation more than 60 min ago, or current but shorter than 24 h,
as after services that ran briefly before the first-deploy seed) therefore merges the
sidecars of the other services read-only. Since `TICKER_REGISTRY_SEED_V3` a running
registry seeds the same way at its next observation when a gap over 60 min restarts its
coverage, and at most every 5 min while its coverage is under 24 h
(`seed.running_reseeds`, `seed.last_seed_at`). Since `TICKER_REGISTRY_SEED_V4` a
registry whose coverage vouches still merges the siblings' sightings, never their
coverage, at most every 5 min (`seed.sighting_merges`, `sighting_merge_new_pools`,
`sighting_merge_sources` `MERGED`/`MISSING`/`UNREADABLE`/`CORRUPT`,
`last_sighting_merge_at`; a seed counts as a merge). Main observes the untrimmed feed,
including the Gecko new pools that `bounded_feed` cuts at `MAX_FEED` = 90, and the Lab and
the tape see only main's trimmed published feed. Under V3 the Lab and the tape adopted
main's coverage at start, vouched, and from then on never picked up a sighting main made
later: for up to 14 days a relaunch whose sibling appeared only among the cut Gecko pools
(a 4-day-old DOTF pool at liquidity/MC 2.01%) was refused by main as `rug_ticker_reuse`
while a COST_FIRST Lab book could enter it and the tape seated it. Main saves its sidecar
at most every 5 min, so main's sightings now reach the Lab and the tape within about
10 min. Sightings only add memory, so the merge can only block more. Under
`TICKER_REGISTRY_SEED_V2` it seeded
only when it was built: a tape process that kept running but completed no poll for over
60 min (main's `/state` timing out while main kept scanning) restarted its coverage and
withheld the seat of every pool under 14 days for 24 h, so main, the personal engines and
the Lab could not enter them either (they need a COMPLETE tape window) although their own
registries vouched; only a tape restart cleared it. The seeds are named by their
state-file environment variables (`NEO_MAIN_MARKET_STATE_PATH`, `NEO_MARKET_STATE_PATH`,
`NEO_STRATEGY_LAB_PATH`, `NEO_LIVE_TAPE_PATH`):

- The main engine seeds from the Lab's and the tape's sidecars.
- A personal engine (its `NEO_MARKET_STATE_PATH` is its own account) seeds from main's
  sidecar, which the gateway passes as `NEO_MAIN_MARKET_STATE_PATH`, and from the Lab's
  and the tape's. Main's registry is the only one fed the untrimmed feed with the Gecko
  new pools, where relaunches show up first; the Lab and the tape see main's trimmed
  published feed and take main's sightings through the merges above.
- The Lab seeds from main's and the tape's; the tape from main's and the Lab's.
- It adopts the earliest `covered_since` of a sibling whose own coverage is current (last
  observation at most 60 min ago), because the merged sightings cover that span. When its
  own short coverage is current too, both spans end within 60 min of now, so their union
  is continuous and the earlier start is kept.
- A seed only adds sightings, so it can only block more. Seed files are read with delete
  sharing and never written. A missing, unreadable or corrupt seed is skipped
  (`seed.sources` in `status()` records `SEEDED`, `MISSING`, `UNREADABLE` or `CORRUPT`
  per file).
- While the registry's own sidecar is kept unread (`UNREADABLE_NOT_OVERWRITTEN`, see the
  sidecar properties above) a seed merges sightings but adopts no coverage.

**First deploy.** No sidecar exists yet. `scripts/build_ticker_registry_seed.py` builds
main's sidecar offline from the main training journal, read in place
(`.runtime/accounts/training/observations.jsonl` in the live checkout; the tool opens it
read-only and nothing appends to it while the services are stopped, so it is not
copied). The journal records the bounded scan feed
(`MAX_FEED` = 90 coins after `entry_quote_priority.bounded_feed`; unchanged repeat polls
within 3 s are coalesced) plus entry, probe and position rows, each with its mint, pool,
symbol and time; the research scan log was built from it. Gecko new pools cut by the
bound are not journalled, although main's live registry sees them in the untrimmed
feed, so the seed is a Lab/tape-grade memory: the same trimmed view the Lab, the tape and
the research had. For up to 14 days after the deploy, a relaunch whose earlier sibling
appeared only among the trimmed Gecko pools before the deploy can pass
`rug_ticker_reuse`; the research reuse figures came from the same trimmed log. Sightings
main makes after the deploy reach the Lab and the tape through the V4 merges.

The tool replays the journal through the same registry code (normalization,
placeholders, pruning, cap, 60-min gap rule). Every valid row registers its sighting;
coverage comes only from market rows, so held-position rows (`open-position` sources)
and position-mark rows (`exit_quote` or a `mark` quote) never extend it. It writes a
`TICKER_REGISTRY_V2_COVERAGE` sidecar to a path that must not exist, and never writes the
journal. From the live checkout root, after `.\scripts\start_local_paper.ps1 -Action Stop`
has completed:

```powershell
.venv\Scripts\python.exe scripts\build_ticker_registry_seed.py --journal .runtime\accounts\training\observations.jsonl --out .runtime\accounts\state.ticker_registry.json
```

Run time (`SEED_REPLAY_WINDOW_V1`). The replay parses every row it reads: about 4 min per
journal day on the reference PC (54.6 MB/s on 4.5 KB rows; the journal grows about
13.9 GB a day and only a PAPER reset starts it again). The tool therefore does not replay
from the first byte: it seeks (a binary search on `observed_at` by byte offset; rows are
appended in observation order, with 1 h of slack) to the first row of the last
`--window-days` before the newest row (default 15: the 14-day retention plus the 24 h
coverage span) and replays from there (`replay.start_offset`, `skipped_bytes`);
`--full-journal` replays everything. Sightings older than the window would be pruned by
the 14-day retention anyway. The window is a time span, and 15 journal days replay for
about an hour, so the start is also capped by bytes: the replay starts no earlier than
`--max-replay-minutes` (default 40) of journal at the reference 54.6 MB/s before its end
(about 131 GB, about 9 journal days; `replay.limited_by_replay_budget`,
`effective_window_days`, `estimated_replay_minutes`). The cap and a shorter
`--window-days` (at least 2) trade older ticker memory for time; the replayed span must
still give 24 h of coverage. Coverage is judged at the clock read after the
replay and the write, not before it: the tool prints `coverage_at_now` at that clock,
`clock.elapsed_seconds`, `start_services_before_utc` (`observed_until` + 60 min, ISO UTC)
and `vouches_at_end`, and exits 2 when the sidecar was written but its coverage at the
end is lapsed or under 24 h (it exits 0 only when it vouches, 1 when nothing was written).
Before this, `coverage_at_now` used the clock read before a replay of the whole journal:
once the journal was about 13 days old the replay alone outlasted the 60-minute window,
and the tool printed warming false and exited 0 while every registry would warm for 24 h.

Run it in place on the live journal after the services stopped (do not copy it: a copy
of a journal of tens of GB only costs disk and minutes of the 60-minute window), check
that it exits 0 (`vouches_at_end` true), then start the services at least 5 minutes
before the printed `start_services_before_utc`; the Lab, the tape and personal engines
then seed from main's sidecar. See [PAPER_RUNBOOK.md](PAPER_RUNBOOK.md) for the full
procedure, including when a rerun cannot help.

On the first deploy only, while no `state.ticker_registry.json` exists, the replay can
run before Stop instead, so that it is not part of the outage: the pre-release services
have no ticker registry and never write that file, and the tool opens the journal
read-only (`'rb'`, read/write sharing), so main keeps appending. The release checkout's
copy of the tool writes the sidecar to a scratch folder outside `.runtime`, and the
operator copies it into `.runtime\accounts` after Stop; the 60-minute window then starts
at the end of the replay, and the outage is only stop, backup, copy, sync, lock check and
start. Main and the personal engines must hold no position before Stop either way
(PAPER_RUNBOOK.md, first deploy).

The services must be stopped whenever a seed is written into the runtime folder, the
first one and `--replace-stale` alike. A running main keeps its registry in memory and rewrites
`state.ticker_registry.json` at its next periodic save (within 5 min), and the Lab, the
tape and the personal engines read a seed only when their registry starts or restarts its
coverage. A seed written under running services is therefore overwritten: in a review
simulation the sidecar showed 30.0 h of coverage right after the tool and 0.44 h with one
entry 6.7 min later, and a Lab restarted afterwards adopted the 0.44 h and kept warming.
The tool refuses a runtime directory (the output's folder or a parent) whose
`services/processes.json` exists without `services/stop.request`; Stop writes that marker
and Start removes it, so the refusal clears exactly when Stop has run. The marker proves
that Stop ran, not that every process exited: when Stop reports that services are still
flushing, wait until `-Action Status` shows none running before running the tool. After
Start, check on main's `/state` that main's `defensive_entry_layer.ticker_registry`
(published by every scan, also while main is paused) shows `load_status` `LOADED` and
`coverage.warming` false, that the Lab's
`strategy_lab.activity_config.defensive_entry_state.ticker_registry` shows
`coverage.warming` false (with `seed.sources` `SEEDED` or `coverage.adopted_from` set), and
that the tape's `live_tape_status.entry_scheduling.defensive_entry.layer.ticker_registry`
shows `coverage.warming` false. The tool prints these checks as `verify_after_start`.

An earlier attempt can leave main's sidecar behind: the services were started before the
seed (main writes its sidecar on its first scan), a guarded deploy rolled back after the
new main ran, or a first try failed. Such a sidecar does not vouch yet, and the Lab and
tape sidecars are no better, so every engine and Lab book would block pools under
14 days for 24 h. `--replace-stale` (services stopped) replaces an existing sidecar only
when it is corrupt (it reads but does not parse), carries no coverage, its coverage is
stamped more than 5 min after the wall clock (`TICKER_COVERAGE_CLOCK_V1`: never observed,
`coverage_ahead_minutes`), or its coverage at the wall clock is not current (last
observation more than 60 min ago) or shorter than 24 h. A rerun only helps when the seed
was overwritten or never written: when the services started more than 60 min after
`observed_until`, or the journal itself has a gap over 60 min in its last 24 h, the
rebuilt sidecar has the same short coverage, the tool exits 2 again, and a rerun only adds
a second outage; expect 24 h of warming instead. A sidecar with current coverage of 24 h or
more is never replaced, and neither is
one that cannot be read (an I/O error: no copy can be kept, and something may still hold
it open). The old file is first copied to `<out>.replaced-<ms>` (no service reads that
name) and its readable sightings are merged into the new sidecar. Moving the old file
aside by hand (services stopped) is equally safe: it is market memory, not ledger data.
The Lab, tape and personal sidecars need no action because they seed from main's.

### HEAT_VETO_STACK_V1

A candidate is vetoed when any rule holds at decision time. Every rule that fires is
recorded.

| Reason | Rule |
| --- | --- |
| `heat_history_warming` | A rule's window is not covered by this process's history, so the rule cannot be evaluated (conservative). `metrics.warming_windows` names the window: `return_5m` (less than 300 s of contiguous history, or no price at or before now − 300 s inside it), `crash_15m` (the pair was first observed by this process less than 900 s ago and rule (f) does not already fire on the shorter window), `paid_profile_60m` (fee tier ≥ 100 bps, the pair is not already known paid, and this process has observed the feed for less than 3600 s). See [Restarts and window coverage](#restarts-and-window-coverage). |
| `heat_input_unknown` | The current price is missing or not positive. |
| `heat_return_5m_surge` | (a) The 5-min return is at least +3% against the price in effect 300 s earlier. |
| `heat_buy_share_5m` | (b) `txns.m5` buys / (buys + sells) ≥ 0.70 |
| `heat_volume_acceleration` | (c) `volume.m5` / (`volume.h1` / 12) ≥ 1.3 |
| `heat_extended_move` | (d) `priceChange.h6` ≥ +200, or `priceChange.h24` ≥ +150 |
| `heat_paid_profile_high_fee` | (e) The fee tier (`paper_market_feasibility.pumpswap_fee_bps`) is at least 100 bps, and the pair carried the paid-profile `latest` source (`token-profiles/latest`) at any time in the last 60 min. |
| `heat_crash_in_progress` | (f) The price is at or below 75% of its 15-min high, or the 5-min return is −20% or worse. The 15-min high reads every retained sample of the last 15 min across feed gaps, like the research window; the 5-min return needs the current contiguous segment. |
| `heat_turnover_5m` | (g) `volume.m5` / `liquidityUsd` ≥ 0.095 (the 80th percentile on pre-cutoff data) |

`PairHistory` keeps rolling samples of (t, price, paid flag) per pair, fed every scan:

- Retention is 61 min, with at most 720 samples per pair and 2,048 pairs.
- A sample is stored when the price or the paid flag changes, every 30 s as a heartbeat,
  and at the start of each segment.
- An absence longer than 120 s restarts the segment, and with it the warm-up. A pair not
  re-observed within 120 s is warming again. Each absence is kept as a gap (at most 64
  per pair), so the 15-min crash high still sees the prices from before it. The opening
  price, the last sample at or before the window start, counts only when its segment was
  still observed after the window start.

A missing or malformed m5 transaction count (`txns` or `txns.m5` not a dict), a zero
1-hour volume or an unknown liquidity leaves rule (b), (c) or (g) unevaluated, as in the
research. An unknown liquidity already fails the structural guard.

#### Restarts and window coverage

The research windows read a continuous scan log. `PairHistory` lives in memory only, so
after an engine, Lab or tape restart it holds only the samples since the restart. With a
5-minute warm-up alone, rule (f) took its 15-min high and rule (e) looked for a paid
profile over the post-restart samples only. Example: a 110 bps pool carried `latest`
from 40 to 20 min before the decision and fell from 1.0 to 0.6 nine minutes before it.
With continuous history it is vetoed (`heat_paid_profile_high_fee`,
`heat_crash_in_progress`); with only the 6 minutes since a restart it was allowed. Each
window now has its own coverage requirement, and an uncovered window is
`heat_history_warming` (fail closed):

| Window | Covered when |
| --- | --- |
| (a) 5-min return | ≥ 300 s of contiguous history (compared unrounded; `history_span_s` is rounded for display only) and a price at or before now − 300 s inside the segment |
| (f) 15-min high | the pair was first observed by this process ≥ 900 s ago (the high itself still spans feed gaps), unless (f) already fires on the shorter window |
| (e) 60-min paid-profile lookback | only for a fee tier ≥ 100 bps and a pair not already known paid: this process has observed the feed for ≥ 3600 s |

Coverage counts on the process clock, so an old `updatedAt` never claims a window the
process did not observe. The cost:

- After any restart, an engine or the Lab enters no pool for 15 min.
- After any restart, it enters no pool at a fee tier ≥ 100 bps for 60 min unless the pool
  is seen with a paid profile, which vetoes it anyway.
- A pool first seen by a running process waits 15 min; a pool absent from the feed for
  more than 120 s waits 5 min (a new contiguous segment).
- The tape seats do not wait: the tape's own warm-up is log-only there.

The engines' and the Lab's diagnostics label `heat_history_warming` as "the pool history
does not cover the check windows yet (5/15 min, 60 min at ≥ 100 bps; after a restart)";
`metrics.warming_windows` in each example names the uncovered window.

Persisting `last_paid_at` and a compact 15-min high per pair would shorten the restart
wait; it is not implemented.

In `log_only` mode a book receives the same flags with `vetoed` false. This mode is for a
pre-registered surge or dip hypothesis arm. `heat_veto.LOG_ONLY_BOOK_IDS` reserves the
research arms `LAB_A_SURGE_EST_GUARD` and `LAB_B_DIP_MKTDIP_GUARD`, registered as Lab
books by `LAB_FORWARD_TESTS_V1` (`backend/lab_forward_tests.py`), which also runs their
random controls `RND_LAB_A` and `RND_LAB_B` log-only (`strategy_lab.heat_log_only_book`);
the reserved set itself, and so every engine's effective config hash, is unchanged. Every
other Lab book, every engine account and the training probe enforce the veto. The tape scheduler withholds new seats on every heat rule except its
own warm-up (see below).

### Errors fail closed

`DefensiveEntryLayer.evaluate` and `observe` never raise. If deciding one candidate raises
unexpectedly, that candidate is blocked with `defensive_entry_error` (the exception type
is recorded, never the data), the error is counted in the layer `status()`, and the scan,
the Lab refresh or the tape poll continues with the other candidates. A failed
observation leaves the pair history short, so the heat veto keeps warming.

### POOL_LOSS_MEMORY_V1

After 2 consecutive losing closes (net `pnl_usd` < 0) on the same (mint, pool) in one
account or one Lab book, new entries into that pool are blocked for 6 h after the last
loss, with reason `pool_loss_cooldown`.

The memory is derived read-only from the ledger's existing closed history on every
decision. There is no schema change and no history rewrite.

Streak rules:

- A close with `pnl_usd` ≥ 0 ends the streak.
- A close whose result is unknown neither counts nor breaks the streak.

## Where it runs

| Entry path | Place | Notes |
| --- | --- | --- |
| Main engine, default `WINNER_ENSEMBLE_PAPER_V1` | `market_monitor.Monitor._maybe_open_checked`, right after the market screen | Before the quote-retry state, `promoted_entry_guard.flow_admission`, `price_integrity.check`, `engine_rug_guard.check` and every quote. It is checked again on the commit-time observation, under the entry lock. |
| `ORDER_FLOW_ADAPTIVE` | Same place, after `oct4.market_rejections` | Before the October 4 signal checks, flow and quotes. |
| `COST_FIRST_ESTABLISHED_PAPER_V1` | Structural guard inside `cost_first_established.rejections` (universe V2), with the engine's registry; heat and loss memory as for the default | The universe is also rechecked at commit. |
| Both `COST_FIRST` Lab books | Structural guard inside the same `cost_first_established.rejections`, with the Lab's registry | Plus the layer, as for every Lab book. |
| Every Strategy Lab book | `strategy_lab.maybe_open`, right after the book's rule or universe match | Before the cost estimate, flow promotion, RugCheck, Jupiter price probes and modeled fills. Loss memory uses that book's own history. It is checked again at commit, on the commit clock and the book's history at that moment (`commit_recheck_blocked`). The Lab has no observation newer than its refresh, so this catches a stale pair history, a moved 5-min reference or a new loss; a pool that turns hot after the refresh is refused at the next refresh. |
| Tape scheduler (all seat groups, including `COST_FIRST_UNIVERSE`) | `TapePoolScheduler.select`, per candidate pool | A pool the structural guard blocks gets no entry or exploration seat; a hot pool gets no new seat (a running lease runs out). The tape's own heat warm-up is log-only and no loss memory applies (see below). Pins of held positions are never screened. |
| Training quote probe | `schedule_training_quote_probe` before the price and RugCheck calls; `run_training_quote_probe` again before `collect_exact_pool_quotes` | Loss memory uses the main account's history. |
| Engine RugCheck prewarm | `prewarm_entry_checks` (`PREWARM_V2_DEFENSIVE_POPULATION`) | Warms only pools the layer can allow: pair age ≥ 720 min at now, the active strategy's market screen, liquidity ≥ $4k and an allowed layer decision; at most 4 per scan, by 5-min activity then score. The old population (`ageMinutes` ≤ 360) was entirely blocked by the young-pool rule, so an allowed pool's first RugCheck happened at entry and returned `pending` (`risk_check_pending`, one scan lost). |

### Tape seats: structural guard and heat for new seats

A tape seat is not an entry. It gives a pool the exact-pool flow coverage that every engine
needs before it may enter (`promoted_entry_guard.flow_admission` requires a COMPLETE
window, which only a seated pool has), and one seat serves every ledger. Spec item 4 asks
that no seat be spent on a pool that every entry path blocks:

- **The structural guard is durable**: an LP-pullable, young, fake-cap, reused-ticker or
  registry-warming pool stays blocked for every engine, so it gets no seat. The tape's
  registry-warming state follows the other services: when a gap over 60 min restarts the
  tape's coverage (its polls stalled while main kept scanning) it seeds from main's and
  the Lab's sidecars at its next poll and adopts a current sibling's coverage
  (`TICKER_REGISTRY_SEED_V3`, kept in V4), so it withholds seats only while no service
  vouches. No tape restart is needed; `live_tape_status.entry_scheduling.defensive_entry.layer.ticker_registry.seed.running_reseeds`
  counts these seeds. While it vouches it merges main's sightings every 5 min
  (`TICKER_REGISTRY_SEED_V4`), so a relaunch main saw among the cut Gecko pools gets no
  seat either.
- **Heat withholds a new seat.** A hot or crashing pool (rules (a)–(g), or an unknown
  price, judged with the scheduler's own pair history) gets no new entry or exploration
  seat, since no engine would enter it now. A pool that already holds a running lease
  keeps it until the 60 s lease expires: a buy share or turnover near its threshold
  flickers, and dropping a lease on one hot 2 s poll would re-queue the pool and reset
  its coverage. At expiry it competes again under the same screen.
  (`heat_withheld_new_seats`, `heat_running_leases_kept`)
- **The tape's own warm-up is log-only.** `heat_history_warming` describes the
  scheduler's own history after a tape restart, not the pool, and a seat is how pool data
  is gathered. Withholding seats then would leave the engines, whose own histories may be
  warm, without a COMPLETE window. Each engine enforces its own warm-up at decision and
  commit.
- **Loss memory is per ledger.** Two losses in one Lab book (or in main) say nothing
  about another account. A withheld seat would leave every engine, including accounts
  with no losses there, without a COMPLETE window for up to 6 h. Each entry path applies
  its own ledger's memory before quoting, so the seat adds nothing. This deviation from
  spec item 4 was ACCEPTED 2026-10-08 by the operator under the owner's delegation: one
  tape seat serves every ledger, so a per-ledger loss cooldown at seats would starve
  accounts without losses of a COMPLETE window; loss memory is enforced per ledger at
  entry, never at shared seats.

Consequences, all visible in diagnostics:

- Pools younger than 12 h cannot be entered anywhere. Rules that only match young pools
  stop entering. These include main's EARLY / MOMENTUM / PRECISION / ULTRA_PRECISION /
  VERIFIED_FLOW_MOMENTUM young branches and most TEST Lab books. Their market-rule
  matches remain counted.
- After any restart, every pool waits 15 min and pools at a fee tier ≥ 100 bps wait
  60 min (heat window coverage). A pool absent from the feed for more than 2 min waits
  5 min; a pool first seen by a running process waits 15 min.
- Pools younger than 14 days wait until the service's ticker registry has watched the
  market for 24 h (first deploy without a seed, an outage longer than 60 min of every
  service, a cap eviction, coverage stamped ahead of the clock); a registry whose sibling
  vouches adopts that coverage at start, after its own gap, or within 5 min while warming.
  Pools of 14 days or more are not affected.

## Score companion: NEO_MARKET_SCORE_V2_LIQ_MC_BAND

`market_monitor.score_pair` now changes the liquidity/MC component:

- The +9 "Добро liquidity/MC" bonus applies only when 0.15 ≤ liquidity/MC < 0.6.
- A liquidity/MC of at least 1 scores −10 as risk "Ликвидност >= MC (LP риск)".
- From 0.6 up to (but not including) 1 the component is neutral.

The score version is published as:

- `scoreVersion` on each feed coin
- `score_version` on new positions, in `/state` config and in entry diagnostics
- part of every `effective_config_hash`

### Exit context: the V1 score stays the exit basis

The ORDER_FLOW_ADAPTIVE exit path calls `market_context(coin, position)` on every tick.
Its conviction includes a `neo_score` term (+4 at ≥ 95, −4 below 85), and conviction
drives `CONVICTION_EXIT` (< 35), `CONVICTION_PROFIT_LOCK` (< 50), `ADAPTIVE_MAX_HOLD`
(< 72) and the hold mode (max hold, target, trail). Feeding it the V2 score would have
changed exits under the unchanged `GOLD_ADAPTIVE_NET_CANDIDATE_V1` (for example a pool at
liquidity/MC 1.2 scored 100 under V1 and 82 under V2, and the same flow moved conviction
from STRONG to NORMAL).

So the score change is an entry change only:

- `score_pair_models` computes both scores from one observation; `make_coin` publishes
  the V1 score as `scoreV1`. Every other score term is shared.
- `market_context` with a held position reads `scoreV1`
  (`EXIT_CONTEXT_SCORE_VERSION = NEO_MARKET_SCORE_V1`); an observation recorded before
  V2 carries only `score`, which V1 computed.
- An entry context publishes `exit_basis_conviction` (same flow, V1 score). A new
  adaptive position's `adaptive_hold`, which the blind-flow exit fallback keeps, comes
  from it. Entry gates still use the V2 conviction (`entry_conviction`,
  `entry_hold_mode`).
- `exit_context_score_version` is recorded in `/state` config, in the config ownership
  map, on new positions and in every `effective_config_hash`.

Exit decisions are therefore identical to the pre-change code for positions opened before
and after this change; `tests/test_defensive_entry_layer.py` checks this for pools at
liquidity/MC 0.7 and 1.1.

#### The training learners stay on V1 too

The isolated `PAPER_TRAINING_V1` learners (`paper_training.py`) read two things from the
engine's observations:

- The recorded `context`: the `GOLD_ADAPTIVE` book (`exit_policy: adaptive`) exits through
  `engine_exit_policy.exit_reason` on its conviction and hold mode (`CONVICTION_EXIT`
  below 35, `CONVICTION_PROFIT_LOCK` below 50, `ADAPTIVE_MAX_HOLD` below 72). The engine
  recorded `market_context(coin, {})`, which is an entry context and would have carried
  the V2 conviction. Example: a pool at liquidity/MC 0.7 with weak flow scores V1 91 and
  V2 82, so its conviction is 38 on V1 and 34 on V2, and a `GOLD_ADAPTIVE` position at
  net −1% would have closed with `CONVICTION_EXIT` where the pre-change code held it.
- `coin.score` for every book's `min_score` and for the engine's training-probe candidate
  signal (`training_candidate_signal`).

Both stay on `NEO_MARKET_SCORE_V1`. Every `training_bridge.observe` call records
`Monitor.training_context(...)`: an entry context gets the V1-basis conviction
(`exit_basis_conviction`) and its hold mode, with `conviction_score_version`
`NEO_MARKET_SCORE_V1` and the engine's own `entry_conviction` for reference; a held
position's context already is V1-based and is only labelled. The learners' score is
`paper_training.learner_score` (`coin.scoreV1`, else `score` for rows recorded before V2;
`LEARNER_SCORE_VERSION`, published in the learner snapshot and in `/state` config as
`training_score_version`). The learners' decision on any given row is therefore the
pre-change one, and `PAPER_TRAINING_V1` keeps its version: bumping it would refuse the
saved training state (an explicit reset), which this change must not do.

The rows they see are not the pre-change population, though. `scan_once` sorts the
discovered feed by the V2 `score` and `bounded_feed` keeps the 90 coins with the market
candidates first (`market_candidate`: for the default strategy, the ensemble's
`score >= threshold`, also V2); the journal records only that bounded feed, and the Lab
and the tape read the same feed. A pool at liquidity/MC 0.6–1 loses 9 points and one at 1
or above 19 (the +9 bonus is gone and the LP risk costs 10), so such pools drop out of
the 90 more often from the deploy on. The learner sample is therefore not one regime
across the deploy. Its boundary is the first journal row stamped `coin.scoreVersion`
(`market_monitor.make_coin` writes it on every observation since the layer;
`main_replay` publishes it as `entry_defense.rows_recorded_under_layer.earliest_at`):
compare or validate learner books on rows before it or rows after it, never pooled, and
count the validation days (at least 5) from the boundary for a post-deploy sample. Ranking
the bounded feed's remainder by `scoreV1` would bring the population closer, but not back
(the candidate set itself follows the V2 entry screen); it is not implemented.

## Version strings

| Item | Before | Now |
| --- | --- | --- |
| Defensive layer | — | `DEFENSIVE_ENTRY_LAYER_V1` |
| Structural guard | — | `STRUCTURAL_RUG_GUARD_V1` (registry `TICKER_REGISTRY_V2_COVERAGE`; `TICKER_REGISTRY_V1` sidecars still load) |
| Heat veto | — | `HEAT_VETO_STACK_V1` (history `PAIR_HISTORY_V1`) |
| Loss memory | — | `POOL_LOSS_MEMORY_V1` |
| Main entry policy | `WINNER_ENSEMBLE_VERIFIED_ENTRY_V4` | `WINNER_ENSEMBLE_VERIFIED_ENTRY_V5`. Learning still counts V4 closes, so a rule held on V4 evidence stays held and a version bump never releases a throttle. |
| ORDER_FLOW_ADAPTIVE entry policy | `ORDER_FLOW_BALANCED_V4` | `ORDER_FLOW_BALANCED_V5`. The restored decision checks keep `DECISION_FILTER_VERSION = ORDER_FLOW_BALANCED_V4` because their rules and thresholds are unchanged, but their inputs are not: the `score` check and the entry conviction (the `neo_score` term) read the V2 entry score `NEO_MARKET_SCORE_V2_LIQ_MC_BAND` (published as `entry_score_version` and `decision_inputs_note` in the profile's `config_snapshot`). V2 is never above V1 (a pool at liquidity/MC 0.6–1 loses 9 points), so a pool can now fail `score` or `conviction` where V4 passed it; this only removes entries. Exit context and hold modes stay on V1. |
| Cost-first profile | `COST_FIRST_ENGINE_PROFILE_V1` / `COST_FIRST_ESTABLISHED_ENTRY_V1` | `COST_FIRST_ENGINE_PROFILE_V2_DEFENSIVE_ENTRY` / `COST_FIRST_ESTABLISHED_ENTRY_V2`. The strategy id `COST_FIRST_ESTABLISHED_PAPER_V1` is unchanged. |
| Cost-first universe / Lab pair | `COST_FIRST_UNIVERSE_V1` / `COST_FIRST_ESTABLISHED_V1` | `COST_FIRST_UNIVERSE_V2_STRUCTURAL_RUG_GUARD` / `COST_FIRST_ESTABLISHED_V2` |
| Lab TEST books | `LAB_ACTIVE_V6_STOP_BUDGET_COST_CAP` | `LAB_ACTIVE_V7_DEFENSIVE_ENTRY` |
| Lab funded books | `PROMOTED_MARKET_BRANCHES_EVIDENCE_COST_V4` | `PROMOTED_MARKET_BRANCHES_EVIDENCE_COST_V5` |
| Tape seats | `STABLE_COST_AWARE_TAPE_DISCOVERY_V4_COST_FIRST_PINS` | `STABLE_COST_AWARE_TAPE_DISCOVERY_V5_DEFENSIVE_ENTRY` (since superseded by V6 from `LAB_FORWARD_TESTS_V1`, which keeps every V5 seat rule and only stops pinning positions of Lab books that never read flow) |
| Market score | (unversioned V1) | `NEO_MARKET_SCORE_V2_LIQ_MC_BAND` |
| Lab retirement review | `LAB_STRATEGY_LIFECYCLE_V1` | `LAB_STRATEGY_LIFECYCLE_V2_CARRIED_EVIDENCE` |
| Engine RugCheck prewarm | (unversioned: `ageMinutes` ≤ 360) | `PREWARM_V2_DEFENSIVE_POPULATION` |
| Ticker registry seeding | — | `TICKER_REGISTRY_SEED_V4` (seeds a registry whose coverage is not current or under 24 h at start, and a running one after a coverage gap or every 5 min while under 24 h; a vouching registry merges its siblings' sightings, never their coverage, every 5 min; adopts a sibling's current coverage, main's sidecar for personal engines, offline first-deploy builder with `--replace-stale`, refused while the services run). `TICKER_REGISTRY_SEED_V2` (start only) and `TICKER_REGISTRY_SEED_V3` (no merge while vouching) were never released. |
| Ticker coverage clock | — | `TICKER_COVERAGE_CLOCK_V1` (coverage stamped more than 5 min after the reference clock is never current and is dropped) |
| First-deploy seed replay | (whole journal, clock read before the replay) | `SEED_REPLAY_WINDOW_V1` (last `--window-days`, default 15, capped at `--max-replay-minutes`, default 40, of journal at the reference rate; coverage judged at the clock after the replay; exit 2 when written but not vouching; `start_services_before_utc`) |
| Engine layer status on `/state` | (only `entry_diagnostics.defensive_entry.layer`, refreshed by entry evaluations) | also `defensive_entry_layer`, refreshed by every scan, paused or running (in memory only, never in the ledger) |
| Ticker sidecar read failures | — | `TICKER_REGISTRY_SIDECAR_READ_V1` (an unreadable own sidecar is kept and re-read, never overwritten during the run; a corrupt one is copied to `<sidecar>.corrupt-<ms>` before it is replaced) |
| Ticker registry coverage basis | `ANY_FEED_ROW_V1` (any non-empty feed; never released) | `DISCOVERED_MARKET_ROWS_V2` (held-position rows never mark coverage) |
| Training learners' score basis | (implicit V1) | `NEO_MARKET_SCORE_V1` (`paper_training.LEARNER_SCORE_VERSION`; `PAPER_TRAINING_V1` unchanged) |

`STRUCTURAL_RUG_GUARD_V1` and the other layer names are unreleased (PR #23 is not
merged). Changes made during its review keep the names; the definitions are published in
`config()` and the effective config hashes below change with every one of them.

The `effective_config_hash` of all three engine strategies changes. It now includes
`entry_defense.config()` (with the heat window coverage and the registry bounds) and the
score version. The pre-layer default and adaptive hashes pinned at `69be225` no longer
match. The new values, with code defaults and no `NEO_*` overrides, are pinned by exact
value in `tests/test_cost_first_engine_profile.py`:

| Strategy | `effective_config_hash` |
| --- | --- |
| `WINNER_ENSEMBLE_PAPER_V1` (default) | `580e3d25ebb845ff2ddd3e1e83928584bf75ca2208669f1be29adeb466b7b0b7` |
| `ORDER_FLOW_ADAPTIVE` | `fcf5bd55bf7e5e8b99c1cc9f863236eb679536c31f42bca04db83e473f55d7ba` |
| `COST_FIRST_ESTABLISHED_PAPER_V1` | `4ff33f9d0111846ff0d25e18f70574566a059a0832f7232bcbd1c7e887c97e10` |

The Lab's retirement review (`LAB_STRATEGY_LIFECYCLE_V2_CARRIED_EVIDENCE`):

- V6 and `COST_FIRST_ESTABLISHED_V1` closes stay in the ledgers, unchanged.
- Books already retired stay retired. A retirement never expires.
- Books not yet retired keep their V6 (or `COST_FIRST_ESTABLISHED_V1`) closes as
  evidence next to the new V7 (or V2) closes. V7 and V2 only remove entries from their
  predecessors, so the bump never delays a retirement. This is the same rule as the
  ensemble's loss throttle, which still counts V4 closes. Under V1 of the review, a book
  with 11 losing V6 closes and 1 losing V7 close counted 1 close and stayed open; now it
  counts 12 and retires. Older regimes still do not count (V5 closes predate the V6 cost
  cap). The evidence publishes `accepted_activity_versions` and
  `closed_trades_by_activity_version`.

## Diagnostics

### Main engine

`entry_diagnostics.defensive_entry` contains:

- versions
- `checked` and `blocked`
- `rejections` and `primary_rejections`
- up to 5 examples, with liquidity/market cap, age, market cap, ticker reuse, registry
  coverage (`registry_coverage_h`), heat metrics and the loss-memory deadline
- `pool_loss_cooldown_pools`
- `commit_recheck_blocked`
- `layer`: the registry and history status, including sidecar load and save state
  (`load_status`, `sidecar_read`), the registry `coverage` (`covered_since`,
  `coverage_hours`, `warming`, `resets`, `future_drops`), the `seed` sources,
  `running_reseeds` and `sighting_merges`, `cap_evictions`, `cap_limited_horizon_days`,
  `oldest_last_seen_days` and the history's `observing_since`

`entry_diagnostics` is refreshed only when entries are evaluated: `maybe_open` returns at
once while the account is paused (`/control/stop`), and `entry_diagnostics` is not
restored from `state.json` (it restarts as `starting`). The same layer status is therefore
also published at the top level of `/state` as `defensive_entry_layer`, refreshed by every
scan whether the engine is running or paused (in memory only; never written to the
ledger). A paused account such as the cost-first account shows its ticker coverage there.

Each rejection example's heat metrics include `warming_windows`, `pair_coverage_s` and
`process_coverage_s`. `/state` config publishes `prewarm_version`.

The reasons are also counted in `rejections` with Bulgarian labels
(`engine_entry_policy.LABELS`). New positions carry `defensive_entry` (the decision they
passed, with versions), `score_version` and `exit_context_score_version`.

### Lab books

Each book's `entry_diagnostics` carries `defensive_rejected` and `defensive_entry`. It
keeps 2 examples per book. The dashboard projection keeps counts only, including
`commit_recheck_blocked`.

When every matched candidate of a book was removed by the layer, `blocked_reason` names
the most frequent highest-priority reason. When the commit recheck refuses the chosen
candidate, `blocked_reason` is its first reason and `commit_recheck_rejected` is 1. New
Lab positions carry `defensive_entry`, the decision on the commit clock.

### Tape scheduler

`live_tape_status.entry_scheduling.defensive_entry` contains:

- counts and up to 6 examples
- `blocked_pools_in_feed` (structurally blocked pools and hot pools without a lease)
- `seat_rule: NO_SEAT_FOR_A_STRUCTURALLY_BLOCKED_POOL_NO_NEW_SEAT_FOR_A_HOT_POOL`
- `heat_veto_mode: NEW_SEATS_WITHHELD_RUNNING_LEASES_KEPT_WARMING_LOG_ONLY`,
  `heat_withheld_new_seats`, `heat_running_leases_kept` and `log_only_flags` (the tape's
  own `heat_history_warming`, and heat flags of pools that keep a running lease)
- `pool_loss_memory_scope: NOT_APPLIED_AT_SEATS_EACH_LEDGER_AT_ITS_OWN_ENTRY`
- `layer`

### Lab dashboard

`src/lib/labStrategyView.ts` names every defensive reason code (and the `defensive_entry`
fallback) in Bulgarian, with the same meaning as `engine_entry_policy.LABELS`, so a Lab
book blocked by the layer never shows a raw code.

### What is not recorded

Diagnostics are per scan or per refresh: counts plus a few examples, overwritten on the
next scan. No per-candidate record of blocked pools is persisted (no journal rows, nothing
in `audit.jsonl`). Matching structural reason codes against later drains needs the
forward log listed under follow-ups.

## Replay

`backend/main_replay.py` cannot reconstruct the layer's whole-feed pair history and ticker
registry from a journal slice. `MainReplay(entry_defense='recorded_policy')`, the default,
replays the recorded decision path of a journal recorded before the layer existed without
the layer and labels the report `REPLAY_RECORDED_POLICY_PREDATES_DEFENSIVE_ENTRY_LAYER_V1`.

Once the layer is deployed, the journal holds observations the engine decided under it:
skipping the layer there would admit entries the live engine refused (`pool_loss_cooldown`,
`rug_*`, `heat_*`) and the label would be false. Engines running the layer stamp every
observation with `coin.scoreVersion` (`market_monitor.make_coin`; no earlier row has the
field), so `recorded_policy` counts those rows before replaying and refuses the journal
(`RecordedPolicyRefused`, `scripts/replay_main.py` exits with the message) when any is
present (`RECORDED_POLICY_REFUSES_ROWS_RECORDED_UNDER_THE_LAYER_V1`). Replay only the rows
recorded before the deploy, or choose a labelled counterfactual:

- `entry_defense='apply'` (`--entry-defense apply`) runs the layer on the replayed rows
  only (`COUNTERFACTUAL_LAYER_ON_REPLAYED_ROWS_ONLY`);
- `entry_defense='without_layer'` (`--entry-defense without_layer`) skips the layer on any
  journal (`COUNTERFACTUAL_WITHOUT_DEFENSIVE_ENTRY_LAYER_V1`).

Neither is an identity replay. Every report publishes `entry_defense.rows_recorded_under_layer`
(count and earliest time). An archived engine without the layer (the frozen arm of
`compare_main.py`) is never refused.

## Tests and fixtures

`tests/fixtures/defensive_entry_obs_20261008.json` holds real observations from the
research scan log, extracted read-only, with mint and pool abbreviated to 8 characters:

- **Fake market cap:** USDP, IOF, WOSE, GOIF x2, SARP x2, DAWS. All are blocked.
- **Just above the 2% line:** USDF `EpugLBw1` and DOTF `5GaCcKLd`. Only ticker reuse
  blocks them; with an empty registry they are now blocked as
  `rug_ticker_registry_warming` (the cold-start case), and as `rug_ticker_reuse` once
  their sibling mints are known.
- **LP-pullable:** SharkTank `CRGN2uGj` and knightcat `7puXhhDF`. Both are blocked.
- **Young pool:** OGTRUMP `9rSmRH9w`, 6 min old, which drained later. It is blocked.
- **Established:** ANSEM, CATE, TROLL, neet. All pass the structural guard.

The file also holds the ticker universe of those tickers, 16 minutes of the real TROLL
series for heat warm-up, and one paid-profile observation.

`tests/test_defensive_entry_layer.py` covers:

- every rule, its boundaries and the reason order; a missing ticker blocks only a pool
  under 14 days (an established pool named `TOKEN`, an emoji, `$` or nothing passes, with
  the 14-day boundary)
- registry persistence: restart, atomic replace, corrupt file, a legacy V1 sidecar,
  pruning, bounds, failed replace, the engine and tape sidecars surviving a restart, the
  tape's clean-stop flush, the 40,000-entry bound and its published horizon, and seeding
  from sibling sidecars (read-only, coverage adopted only from a current sibling, a short
  current sidecar left before the seed still adopting it, a vouching one not seeded,
  missing or corrupt seeds skipped, main's sidecar for personal engines); an unreadable
  own sidecar (a sharing violation) never overwritten, adopting no sibling coverage and
  merged once readable, a transient read error retried, a corrupt one copied aside before
  it is replaced (and kept when the copy fails); a running registry, through the tape
  scheduler, seeding again after a 70-minute gap and a warming one every 5 minutes; the
  engine and tape sidecar tests stamped by the registry's own wall clock, so they pass
  after the fixed test clock (2027-01-15) is 14 days in the past (checked with the wall
  clock moved to 2027-02-06 and 2030-06-01)
- registry coverage: the USDF/DOTF relaunches blocked on an empty or 23.98 h registry
  and judged after 24 h, a 61-minute gap restarting coverage, a lapsed coverage at
  decision time, a stale published feed or an empty scan never extending it, a feed of
  held positions only never extending it (30 h of one held coin, through the layer, and
  through `scan_once` with the discovery lists down), coverage
  surviving a restart, a cap eviction moving its start, the engine and the Lab's
  `COST_FIRST` books blocking a pool under 14 days before any quote on a fresh registry;
  the missing-symbol placeholder `TOKEN`; the offline seed builder on a synthetic journal
  (covered sidecar, journal untouched, existing output refused, a journal gap,
  held-position and mark rows never extending coverage, `--replace-stale` recovering a
  sidecar left by an earlier attempt with a backup and merged sightings, refusing one
  that already vouches or cannot be read, and both runs refused while a runtime's
  `services/processes.json` has no `services/stop.request`; coverage judged at the clock
  after the replay, with exit 2 when a replay outlasts the 60-minute window or the journal
  gives less than 24 h; `start_services_before_utc`; the replay seeking to the 15-day
  window and `--full-journal`; a sidecar stamped ahead of the wall clock replaceable)
- the coverage clock: an own sidecar stamped in January 2027 never vouching, seeding from
  main instead and recording a later 5 h outage as a gap (`rug_ticker_registry_warming`),
  with the future stamp never saved again; coverage ahead of the decision clock not
  current and dropped at the next scan after a backward clock step; a sibling stamped in
  the future merged but never adopted
- `TICKER_REGISTRY_SEED_V4`: a vouching Lab registry taking main's later DOTF sighting at
  its next 5-minute merge (`rug_ticker_reuse`), never main's coverage
- a paused engine publishing `defensive_entry_layer` with its ticker coverage on every
  scan, never in the ledger file
- replay: `recorded_policy` refusing a journal with rows recorded under the layer,
  `without_layer` and `apply` labelled as counterfactuals; the ORDER_FLOW_ADAPTIVE profile
  publishing that its V4 checks read the V2 entry score
- the training learners' V1 basis: the recorded `GOLD_ADAPTIVE` context and its exit at
  liquidity/MC 0.7 and 1.1, the learners' `min_score` and probe signal, the probe's
  recorded context
- account isolation: the suite's ledger paths are temporary, and a shell
  `NEO_MARKET_STATE_PATH` pointing at a stand-in ledger survives an engine test run in a
  fresh process (the setdefault-based module rewrote it); every gate test module that
  isolates the layer, run alone in its own process with `NEO_MARKET_STATE_PATH`,
  `NEO_MAIN_MARKET_STATE_PATH`, `NEO_STRATEGY_LAB_PATH` and `NEO_LIVE_TAPE_PATH` pointing
  at a sentinel directory, leaves it empty
- each heat rule, warm-up (compared unrounded at 299.96 s, and fail closed without a
  reference price), the 15-min and 60-min window coverage after a restart, gaps,
  staleness, a crash during a feed gap, malformed inputs and `log_only`; a raising
  evaluation fails closed
- loss-memory streaks, including the swordinu re-buy pattern
- the score band, and unchanged ORDER_FLOW_ADAPTIVE exit context and hold mode at
  liquidity/MC 0.7 and 1.1
- an integration test per entry path, proving that the guard, veto and loss memory are
  consulted before any quote, flow promotion, RugCheck or price probe; the commit-time
  recheck of the engine (default and COST_FIRST: a surge or two losses appearing while
  quotes are prepared) and of the Lab (a loss booked during provider work); the RugCheck
  prewarm population, ranking and cap
- tape seats: structural blocks withheld, hot pools get no new seat while a running
  lease is kept until it expires, the tape's own warm-up and another ledger's losses
  never withhold a seat, up to 6 examples
- the Lab dashboard labels every defensive reason code
- the strategy lock and this document record owner decision (1) (loss memory per ledger
  at entry, not at shared seats) as ACCEPTED, with no pending sign-off left

`backend/tests/test_lab_strategy_lifecycle.py` covers the carried V6 and
`COST_FIRST_ESTABLISHED_V1` evidence; `tests/test_cost_first_established.py` covers the
physical-only V1 universe helper.

Existing gate tests isolate the layer with a module-level patch (`TEST_GATE_ISOLATION`).
The cost-first universe's structural guard is not patched in those tests. The same
setUpModule also replaces `strategy_lab.DEFENSE` and `live_tape._POOL_SCHEDULER` with
path-less layers, because `strategy_lab.maybe_open` and `live_tape.feed_snapshot` still
observe through them: run alone (with `PYTHONPATH=backend`, without
`scripts/run_python_checks.py`, which points every path at a temp dir) six modules
otherwise wrote `strategy_lab.ticker_registry.json` (fixed test clock, January 2027, and
fixture sightings) or `live_tape.ticker_registry.json` next to the shell's
`NEO_STRATEGY_LAB_PATH` / `NEO_LIVE_TAPE_PATH` (or the `/var/lib/neo-market` defaults),
which a Lab started later would have loaded (see the coverage clock above).

## Limits and follow-ups

- `lab_paired_runner.py` and `astra6_brain.py` are experiment services that run only on
  the VPS. The local launcher does not start them. They are not wired in this change.
- The isolated training learner books (`paper_training.py`, never promoted
  automatically) are not gated here. They simulate from the observations they receive,
  on the V1 score basis. The route-quote probe that feeds them executable evidence is
  gated.
- The scheduler applies no loss memory at its seats (ACCEPTED 2026-10-08 by the operator
  under the owner's delegation: one tape seat serves every ledger, so loss memory is
  enforced per ledger at entry, never at shared seats) and does not wait for its own heat
  warm-up; each engine, personal engines included, applies its own loss memory and heat
  at entry.
- The first-deploy seed is built from the bounded, journalled feed, so it lacks the
  Gecko new pools main's live registry sees beyond `MAX_FEED`; journalling the identities
  of trimmed-out coins would give it main's parity, and is not implemented. After the
  deploy the Lab and the tape take main's untrimmed-feed sightings through the 5-minute
  merges (`TICKER_REGISTRY_SEED_V4`), lagging main by up to about 10 min.
- The PAPER_TRAINING_V1 learner sample changes population at the deploy (the V2-ranked
  bounded feed; see the training learners section): its boundary is the first journal row
  with `coin.scoreVersion`.
- Heat history is per process and starts empty after a restart, which costs an engine or
  the Lab 15 min without entries, and 60 min for pools at a fee tier ≥ 100 bps (the tape
  seats do not wait). Only the ticker registry persists, with its coverage.
- The archived research forensics (`research/edge_study_2026_10_08/forensics/`) reproduce
  the V1 universe through `cost_first_engine_profile.physical_universe_rejections`; the
  live V2 universe fails closed without a ticker registry and cannot reproduce them.
- These research suggestions are not implemented:
  - the LAB_A and LAB_B arms (their ids are reserved as log-only)
  - the optional "exit on first paid-profile appearance" trigger
  - retiring `EXIT_IMPACT_EMERGENCY_V1` for main and ORDER_FLOW_ADAPTIVE
- Not implemented yet: a bounded forward log of blocked candidates (abbreviated mint and
  pool, reasons, liquidity/MC, age, market cap) to match structural reason codes against
  later drains (liquidity to 0, or price −80% within 10 min). Keep one for several days
  before trusting the precision numbers. Judge
  every book on the `STRATEGY_VALIDATION.md` acceptance basis. The layer is never itself
  a reason to promote anything.
