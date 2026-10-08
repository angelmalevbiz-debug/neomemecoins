# DEFENSIVE_ENTRY_LAYER_V1: structural rug guard, heat veto and pool loss memory

`DEFENSIVE_ENTRY_LAYER_V1` (`backend/entry_defense.py`) puts three independent vetoes in
front of **every** PAPER entry path:

- `STRUCTURAL_RUG_GUARD_V1` (`backend/structural_rug_guard.py`)
- `HEAT_VETO_STACK_V1` (`backend/heat_veto.py`)
- `POOL_LOSS_MEMORY_V1` (`backend/pool_loss_memory.py`)

The vetoes run before any quote, flow promotion, RugCheck call or Jupiter price probe.

The layer is PAPER only. It removes candidates. It never admits, sizes, prices or exits
anything. Exits of open positions, cost caps, `engine_rug_guard` (`RUG_GUARD_V2`, the
RugCheck report), every ledger and every balance are unchanged. Nothing is reset or
rewritten.

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
| `rug_input_unknown` | Any input is missing, non-finite or ≤ 0: liquidity (`liquidityUsd`, else `liquidity.usd`), market cap (`marketCap`, else `fdv`), pair age (`pairCreatedAt` at `now`), mint, pool, normalized ticker, or the ticker registry itself. Evaluation stops here. |
| `rug_lp_pullable` | liquidity / market cap ≥ 1.0 |
| `rug_young_pool` | pair age < 720 min |
| `rug_fake_market_cap` | market cap ≥ $20M, liquidity / market cap < 0.02, and age < 14 days |
| `rug_ticker_reuse` | age < 14 days, and the normalized ticker (alphanumerics only, casefolded) was already seen at or before `now` on another pool with a **different** mint. A token's own second pool, with the same mint, is not a reuse. |

The ticker check is past-only. `TickerRegistry` is fed with every scan's feed, as
(mint, pool, normalized ticker, first seen, last seen). Each process keeps it as a small
JSON sidecar next to its state file:

- `state.ticker_registry.json` for each engine account
- `strategy_lab.ticker_registry.json` for the Lab
- `live_tape.ticker_registry.json` for the tape

The sidecar has these properties:

- It is written atomically (temp file, fsync, replace, Windows retry), on the first
  observation, then at most once every 5 minutes, and again on a clean shutdown (main
  engine and Lab). A crash loses at most 5 minutes of ticker memory.
- Entries unseen for 14 days are pruned. At most 20,000 entries are kept; the least
  recently seen are evicted first.
- A missing, corrupt or unsupported file starts empty and never stops a service. A
  failed save keeps the old file and is reported in `save_error`.

### HEAT_VETO_STACK_V1

A candidate is vetoed when any rule holds at decision time. Every rule that fires is
recorded.

| Reason | Rule |
| --- | --- |
| `heat_history_warming` | The pair has less than 300 s of contiguous history in this process. This is conservative: a 5-min return cannot be measured. |
| `heat_input_unknown` | The current price is missing or not positive. |
| `heat_return_5m_surge` | (a) The 5-min return is at least +3% against the price in effect 300 s earlier. |
| `heat_buy_share_5m` | (b) `txns.m5` buys / (buys + sells) ≥ 0.70 |
| `heat_volume_acceleration` | (c) `volume.m5` / (`volume.h1` / 12) ≥ 1.3 |
| `heat_extended_move` | (d) `priceChange.h6` ≥ +200, or `priceChange.h24` ≥ +150 |
| `heat_paid_profile_high_fee` | (e) The fee tier (`paper_market_feasibility.pumpswap_fee_bps`) is at least 100 bps, and the pair carried the paid-profile `latest` source (`token-profiles/latest`) at any time in the last 60 min. |
| `heat_crash_in_progress` | (f) The price is at or below 75% of its 15-min high, or the 5-min return is −20% or worse. |
| `heat_turnover_5m` | (g) `volume.m5` / `liquidityUsd` ≥ 0.095 (the 80th percentile on pre-cutoff data) |

`PairHistory` keeps rolling samples of (t, price, paid flag) per pair, fed every scan:

- Retention is 61 min, with at most 720 samples per pair and 2,048 pairs.
- A sample is stored when the price or the paid flag changes, every 30 s as a heartbeat,
  and at the start of each segment.
- An absence longer than 120 s restarts the segment, and with it the warm-up. A pair not
  re-observed within 120 s is warming again.

A missing m5 transaction count, a zero 1-hour volume or an unknown liquidity leaves rule
(b), (c) or (g) unevaluated, as in the research. An unknown liquidity already fails the
structural guard.

In `log_only` mode a book receives the same flags with `vetoed` false. This mode is for a
pre-registered surge or dip hypothesis arm. `heat_veto.LOG_ONLY_BOOK_IDS` reserves the
research arms `LAB_A_SURGE_EST_GUARD` and `LAB_B_DIP_MKTDIP_GUARD`, which are not
registered yet. Every registered Lab book, every engine account, the training probe and
the tape scheduler enforce the veto.

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
| Every Strategy Lab book | `strategy_lab.maybe_open`, right after the book's rule or universe match | Before the cost estimate, flow promotion, RugCheck, Jupiter price probes and modeled fills. Loss memory uses that book's own history. |
| Tape scheduler (all seat groups, including `COST_FIRST_UNIVERSE`) | `TapePoolScheduler.select`, per candidate pool | A blocked pool gets no entry or exploration seat, and its lease is released. Pins of held positions are never screened. Loss memory is the union of main's history and every Lab book's history in `/state`. |
| Training quote probe | `schedule_training_quote_probe` before the price and RugCheck calls; `run_training_quote_probe` again before `collect_exact_pool_quotes` | Loss memory uses the main account's history. |
| Engine RugCheck prewarm | `prewarm_entry_checks` | A blocked pool's price and RugCheck reports are not requested. |

Consequences, all visible in diagnostics:

- Pools younger than 12 h cannot be entered anywhere. Rules that only match young pools
  stop entering. These include main's EARLY / MOMENTUM / PRECISION / ULTRA_PRECISION /
  VERIFIED_FLOW_MOMENTUM young branches and most TEST Lab books. Their market-rule
  matches remain counted.
- After any restart, and after a pool is absent from the feed for more than 2 min, that
  pool waits 5 min (heat warm-up).

## Score companion: NEO_MARKET_SCORE_V2_LIQ_MC_BAND

`market_monitor.score_pair` now changes the liquidity/MC component:

- The +9 "Добро liquidity/MC" bonus applies only when 0.15 ≤ liquidity/MC < 0.6.
- A liquidity/MC of at least 1 scores −10 as risk "Ликвидност >= MC (LP риск)".
- From 0.6 up to (but not including) 1 the component is neutral.

The score version is published as:

- `scoreVersion` on each feed coin
- `score_version` on new positions, in `/state` config and in entry diagnostics
- part of every `effective_config_hash`

## Version strings

| Item | Before | Now |
| --- | --- | --- |
| Defensive layer | — | `DEFENSIVE_ENTRY_LAYER_V1` |
| Structural guard | — | `STRUCTURAL_RUG_GUARD_V1` (registry `TICKER_REGISTRY_V1`) |
| Heat veto | — | `HEAT_VETO_STACK_V1` (history `PAIR_HISTORY_V1`) |
| Loss memory | — | `POOL_LOSS_MEMORY_V1` |
| Main entry policy | `WINNER_ENSEMBLE_VERIFIED_ENTRY_V4` | `WINNER_ENSEMBLE_VERIFIED_ENTRY_V5`. Learning still counts V4 closes, so a rule held on V4 evidence stays held and a version bump never releases a throttle. |
| ORDER_FLOW_ADAPTIVE entry policy | `ORDER_FLOW_BALANCED_V4` | `ORDER_FLOW_BALANCED_V5`. The restored decision checks keep `DECISION_FILTER_VERSION = ORDER_FLOW_BALANCED_V4`. |
| Cost-first profile | `COST_FIRST_ENGINE_PROFILE_V1` / `COST_FIRST_ESTABLISHED_ENTRY_V1` | `COST_FIRST_ENGINE_PROFILE_V2_DEFENSIVE_ENTRY` / `COST_FIRST_ESTABLISHED_ENTRY_V2`. The strategy id `COST_FIRST_ESTABLISHED_PAPER_V1` is unchanged. |
| Cost-first universe / Lab pair | `COST_FIRST_UNIVERSE_V1` / `COST_FIRST_ESTABLISHED_V1` | `COST_FIRST_UNIVERSE_V2_STRUCTURAL_RUG_GUARD` / `COST_FIRST_ESTABLISHED_V2` |
| Lab TEST books | `LAB_ACTIVE_V6_STOP_BUDGET_COST_CAP` | `LAB_ACTIVE_V7_DEFENSIVE_ENTRY` |
| Lab funded books | `PROMOTED_MARKET_BRANCHES_EVIDENCE_COST_V4` | `PROMOTED_MARKET_BRANCHES_EVIDENCE_COST_V5` |
| Tape seats | `STABLE_COST_AWARE_TAPE_DISCOVERY_V4_COST_FIRST_PINS` | `STABLE_COST_AWARE_TAPE_DISCOVERY_V5_DEFENSIVE_ENTRY` |
| Market score | (unversioned V1) | `NEO_MARKET_SCORE_V2_LIQ_MC_BAND` |

The `effective_config_hash` of all three engine strategies changes. It now includes
`entry_defense.config()` and the score version. The pre-layer default and adaptive hashes
pinned at `69be225` no longer match, and the tests assert that they differ.

The Lab's retirement review counts closes per entry-policy version, as before:

- V6 and `COST_FIRST_ESTABLISHED_V1` closes are kept in the ledgers.
- Books already retired stay retired. A retirement never expires.
- Books not yet retired collect new evidence under V7, as they did at the V5 → V6 change.

## Diagnostics

### Main engine

`entry_diagnostics.defensive_entry` contains:

- versions
- `checked` and `blocked`
- `rejections` and `primary_rejections`
- up to 5 examples, with liquidity/market cap, age, market cap, ticker reuse, heat
  metrics and the loss-memory deadline
- `pool_loss_cooldown_pools`
- `commit_recheck_blocked`
- `layer`: the registry and history status, including sidecar load and save state

The reasons are also counted in `rejections` with Bulgarian labels
(`engine_entry_policy.LABELS`). New positions carry `defensive_entry` (the decision they
passed, with versions) and `score_version`.

### Lab books

Each book's `entry_diagnostics` carries `defensive_rejected` and `defensive_entry`. It
keeps 2 examples per book. The dashboard projection keeps counts only.

When every matched candidate of a book was removed by the layer, `blocked_reason` names
the most frequent highest-priority reason. New Lab positions carry `defensive_entry`.

### Tape scheduler

`live_tape_status.entry_scheduling.defensive_entry` contains:

- counts and up to 6 examples
- `blocked_pools_in_feed`
- `pool_loss_memory_scope`
- `layer`

## Replay

`backend/main_replay.py` cannot reconstruct the layer's whole-feed pair history and ticker
registry from a journal slice, and today's journals were recorded before the layer
existed. `MainReplay(entry_defense='recorded_policy')`, the default, therefore replays the
recorded decision path without the layer and labels the report
`REPLAY_RECORDED_POLICY_PREDATES_DEFENSIVE_ENTRY_LAYER_V1`.

`entry_defense='apply'`, or `scripts/replay_main.py --entry-defense apply`, runs the layer
on the replayed rows only. That run is a labelled counterfactual, never an identity
replay.

## Tests and fixtures

`tests/fixtures/defensive_entry_obs_20261008.json` holds real observations from the
research scan log, extracted read-only, with mint and pool abbreviated to 8 characters:

- **Fake market cap:** USDP, IOF, WOSE, GOIF x2, SARP x2, DAWS. All are blocked.
- **LP-pullable:** SharkTank `CRGN2uGj` and knightcat `7puXhhDF`. Both are blocked.
- **Young pool:** OGTRUMP `9rSmRH9w`, 6 min old, which drained later. It is blocked.
- **Established:** ANSEM, CATE, TROLL, neet. All pass the structural guard.

The file also holds the ticker universe of those tickers, 16 minutes of the real TROLL
series for heat warm-up, and one paid-profile observation.

`tests/test_defensive_entry_layer.py` covers:

- every rule, its boundaries and the reason order
- registry persistence: restart, atomic replace, corrupt file, pruning, bounds, failed
  replace, and the engine sidecar surviving a restart
- each heat rule, warm-up, gaps, staleness and `log_only`
- loss-memory streaks, including the swordinu re-buy pattern
- the score band
- an integration test per entry path, proving that the guard, veto and loss memory are
  consulted before any quote, flow promotion, RugCheck or price probe

Existing gate tests isolate the layer with a module-level patch (`TEST_GATE_ISOLATION`).
The cost-first universe's structural guard is not patched in those tests.

## Limits and follow-ups

- `lab_paired_runner.py` and `astra6_brain.py` are experiment services that run only on
  the VPS. The local launcher does not start them. They are not wired in this change.
- The isolated training learner books (`paper_training.py`, never promoted
  automatically) are not gated here. They simulate from the observations they receive.
  The route-quote probe that feeds them executable evidence is gated.
- The scheduler sees main's and the Lab's closed history in `/state`, not personal
  engines' histories. Each personal engine applies its own loss memory at entry.
- Heat history is per process and starts empty after a restart, which costs a 5-min
  warm-up. Only the ticker registry persists.
- These research suggestions are not implemented:
  - the LAB_A and LAB_B arms (their ids are reserved as log-only)
  - the optional "exit on first paid-profile appearance" trigger
  - retiring `EXIT_IMPACT_EMERGENCY_V1` for main and ORDER_FLOW_ADAPTIVE
- Keep a forward log of structural reason codes against later drains (liquidity to 0, or
  price −80% within 10 min) for several days before trusting the precision numbers. Judge
  every book on the `STRATEGY_VALIDATION.md` acceptance basis. The layer is never itself
  a reason to promote anything.
