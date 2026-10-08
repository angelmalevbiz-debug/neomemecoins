# COST_FIRST_ESTABLISHED_PAPER_V1 — cost-first engine profile for one opt-in PAPER account

Profile `COST_FIRST_ENGINE_PROFILE_V1`, strategy id `COST_FIRST_ESTABLISHED_PAPER_V1`,
entry `COST_FIRST_ESTABLISHED_ENTRY_V1`, exit policy `cost_first` /
`COST_FIRST_NET_EXIT_V1` with `EXIT_IMPACT_EMERGENCY_V2`. PAPER only. Nothing in
this change submits swaps, resets or rewrites a ledger, switches an account, or
claims that the profile is profitable. It is an unvalidated, versioned hypothesis.

Update 2026-10-08 (`DEFENSIVE_ENTRY_LAYER_V1`, [DEFENSIVE_ENTRY_LAYER.md](DEFENSIVE_ENTRY_LAYER.md)):
profile `COST_FIRST_ENGINE_PROFILE_V2_DEFENSIVE_ENTRY`, entry `COST_FIRST_ESTABLISHED_ENTRY_V2`,
universe `COST_FIRST_UNIVERSE_V2_STRUCTURAL_RUG_GUARD` (the structural rug guard is part of
`cost_first_established.rejections`), and the heat veto and pool loss memory run before quotes.
The strategy id, exits and `EXIT_IMPACT_EMERGENCY_V2` are unchanged; the default and
`ORDER_FLOW_ADAPTIVE` hashes quoted below are the pre-layer values and have changed.
With the layer, the `effective_config_hash` values (code defaults, no `NEO_*` overrides,
pinned exactly by `tests/test_cost_first_engine_profile.py`) are:
`COST_FIRST_ESTABLISHED_PAPER_V1` `b29a6c5c772d6438dc14202f241eaa2071fbe48d56c63fbeee60cf9988b1cd12`,
`WINNER_ENSEMBLE_PAPER_V1` `04d06c9a319aeb0130d50985fefd0d87f000f134d9cf01ab8cde1c8d5e584740`,
`ORDER_FLOW_ADAPTIVE` `b7811a9683eba453ff617b6dcf8160c67bf197bb0e80268ab0497999861fe019`.
A new cost-first account's ticker registry is seeded from the Lab's and the tape's
sidecars; check that they hold entries before enabling the profile (see
[DEFENSIVE_ENTRY_LAYER.md](DEFENSIVE_ENTRY_LAYER.md#cold-start)).

## What it is

Stage 2 of the cost-first research line (`docs/STRATEGY_VALIDATION.md`,
"COST_FIRST_ESTABLISHED_V1"): the same cost-defined universe that the isolated Lab
book pair (`COST_FIRST_CONTROL`, `COST_FIRST_SCALED`) trades, run by the main
engine code for one personal account that the operator opts in. The definitions
are imported from `backend/cost_first_established.py`; the engine profile lives in
`backend/cost_first_engine_profile.py` and is selected exactly like
`ORDER_FLOW_ADAPTIVE` (`NEO_SIGNAL_STRATEGY`, account registry field
`signal_strategy`, `scripts/paper_runtime.py set-account-strategy`). Unknown values
still fail closed: the engine refuses to start and the gateway starts no engine.

Sequencing note: `docs/STRATEGY_VALIDATION.md` names the Lab pair passing its gates
as the precondition for a Stage 2 engine profile, and the judges' synthesis orders
the V2 exit test after the Lab step. This change only makes the profile selectable;
at the time of writing the Lab pair has no completed evaluation. Switching an account
to it now is the owner's explicit decision for one PAPER test account, and its
results must be reported as a separate, unvalidated engine-account sample, not as
evidence that the Lab gates passed.

The default production strategy `WINNER_ENSEMBLE_PAPER_V1` and the opt-in
`ORDER_FLOW_ADAPTIVE` profile are unchanged: their `effective_config_hash` values
(`fe08e29c…e62c` and `405669df…cdc4` with code defaults) and their full `/state`
config are byte-identical to `69be225`, and both are pinned by
`tests/test_cost_first_engine_profile.py`.

## Why (evidence; PAPER model outputs, small clustered sample, no edge claim)

Research 2026-10-08 (`analyst-exits.md`, `analyst-losses.md`, judges' synthesis step 3/5),
one 25.5 h regime, 100 engine closes on 7 mints (about 30 effective episodes):

- Cost dominates. 92 of 100 engine closes were in 90–125 bps PumpSwap tiers; the
  immediate round trip at entry (sell quote out / buy in − 1) had a median of −2.29%.
  Only pools in tiers ≤ 50 bps with ≥ $250k liquidity leave several points of gross
  headroom before a −5% net stop. Whether that universe has positive expectancy is
  unknown: the only such pool traded so far went 0W/8L on the engine (7 of them
  `EXIT_IMPACT_EMERGENCY`), and 17 Lab trades at ≤ 1.5% round trip had PF 0.43.
- `EXIT_IMPACT_EMERGENCY` V1 fires on quote noise. Its threshold
  `max(0.75, entry BUY impact + 0.5)` is compared with SELL quotes, but sell-side
  quotes ran a median +0.325 pp above buy-side quotes for the same size at the same
  instant (IQR −0.47…+0.44). 15/100 trades already met the V1 condition at entry and
  52/100 were within 0.25 pp of it. At the 29 V1 exits the market had not moved
  against the position (median +0.37%), exit/entry liquidity was 1.00, and the median
  hold was 26 s: the booked loss (median −2.42%) was the round-trip cost.
- Caveat that cuts the other way: in that same sample V1 acted as an accidental
  ~−2.5% stop and beat every hold-on counterfactual (hold to −5%/+10%/60 min:
  −6.22%, CI −9.36…−3.23, n = 29, 5 mints). V2 can realize larger losses in that
  regime. The expectation for V2 is neutral to negative; it is a causality
  correction (an impact emergency should measure impact), not a predicted gain.

## Entry (`COST_FIRST_ESTABLISHED_ENTRY_V1`)

1. Market screen = cost-first universe `COST_FIRST_UNIVERSE_V1` (imported, not
   restated): `dexId == pumpswap`, quote token SOL, PumpSwap fee tier from market cap
   in SOL ≤ 50 bps, liquidity ≥ $250,000, modeled fee + constant-product impact round
   trip ≤ 1.2% at the sized entry. Score, age and momentum are not gates. Every
   rejection is recorded in `entry_diagnostics.rejections` with its universe reason
   (`fee_tier_above_maximum`, `liquidity_below_minimum`,
   `fee_impact_roundtrip_above_maximum`, `dex_not_pumpswap`, `quote_token_not_sol`,
   `market_cap_unknown`, …) and the first examples carry the metrics (tier,
   liquidity, planned notional, modeled round trip and each limit).
2. Unchanged engine gates, in the engine's order: `signal_data_rejections`,
   same-token cooldown and quote-retry cooldown, `promoted_guard.flow_admission`
   (CONFIRMED_PUMPSWAP_WINDOW, 30 s exact pool, ≥ 3 swaps, ≥ 2 wallets, buys ≥ 1.2 ×
   sells, 12 s freshness), independent price confirmation (Jupiter tie-break), rug
   guard and `risk_admission`, known token-account rent and SOL price, executable
   buy/sell/buy quote preflight with the engine caps (expected round trip ≤ 1.5%,
   conservative ≤ 2.5%, entry impact ≤ 1.75%), and the commit-time recheck of data,
   flow, the universe itself, safety, balance, daily budget, quote age and signal age.
   No outcome-based throttle (`NONE_FIXED_HYPOTHESIS`).
3. Size (`COST_FIRST_LIQUIDITY_SCALED_V1`): `min(TRADE_NOTIONAL_USD, liquidity × 0.001)`
   (at the $250k minimum this is $250, so the engine $200 notional binds), then the
   unchanged `engine_runtime.plan_notional` daily-budget/exposure sizing and the
   default `entry_size_backoff` ladder after a pure cost rejection. The position
   records `size_rule_notional_usd` and the final `notional_usd`.
4. Engine-owned and unchanged (published as `engine-owned` in
   `config.config_ownership`): `max_positions` 8, `max_daily_loss_usd` 100,
   `max_drawdown_pct` 20, `max_total_exposure_pct` 100, full-loss risk $250,
   `same_token_cooldown_seconds` 1200, `stop_loss_pct` 5, notional cap 200, scan 3 s,
   position scan 0.5 s, cost/impact caps 1.5 / 2.5 / 1.75. Their environment
   overrides behave exactly as for the default engine (cost caps can only be lowered).
5. Each new position carries `strategy_id`, `entry_policy_version`, `entry_mode`,
   `signal_evidence = COST_FIRST_UNIVERSE_V1_ON_CONFIRMED_EXACT_POOL_FLOW_WITH_VERIFIED_EXECUTION_CHECKS`,
   `cost_first_universe` (the metrics at commit), `entry_sell_impact_pct` (impact of
   the preflight sell quote) and `exit_policy = cost_first`.

## Exit (`cost_first`, `COST_FIRST_NET_EXIT_V1`)

- Net geometry = the default fixed policy evaluated by the unchanged
  `engine_exit_policy.exit_reason`: `STOP_LOSS_NET_TARGET` at −5% net (gap losses are
  never clamped), `TAKE_PROFIT_10_NET` at +10% net, `MAX_HOLD_60`.
- `LIQUIDITY_EMERGENCY` (< 80% of entry liquidity) and `STALE_MARKET_EXIT` unchanged.
- `EXIT_IMPACT_EMERGENCY_V2` replaces V1 for these positions only:
  - threshold `max(0.75, entry_sell_impact + 0.5)`; anchor
    `ENTRY_PREFLIGHT_SELL_IMPACT` (from `entry_sell_impact_pct`, else the stored
    `preflight_sell_quote.priceImpactPct`); only if no sell impact was recorded
    `ENTRY_BUY_IMPACT_FALLBACK_V1` (V1's buy-side anchor); with neither, the 0.75 floor
    (`NO_ENTRY_IMPACT_FLOOR_ONLY`). The anchor and its source are recorded.
  - arm then confirm: the first mark at/above the threshold only arms
    (`exit_impact_emergency_v2.armed_at`, `trigger_quote`), and only when that mark
    was routed through the exact entry pool (`route_matches_entry_pool = true`). A
    mark at/above the threshold that cannot arm is counted in
    `exit_impact_emergency_v2.trigger_blocks` (with `last_trigger_block`) and takes no
    forced quote: `trigger_not_exact_pool` (an off-pool best route could never
    confirm), `trigger_in_rearm_cooldown` (within 15,000 ms of any disarm,
    `rearm_not_before`) or `trigger_confirm_budget_exhausted` (already 3 forced
    confirmation quotes for this position in the last 5 minutes,
    `confirm_attempts_at`). After ≥ 2,000 ms the
    engine takes a forced sell quote. It exits only if that quote is newly received
    (not cached), fresh, routed through the exact entry pool, received ≥ 2 s after
    arming and still at/above the threshold. Anything else disarms with a recorded
    code (`confirm_below_threshold`, `confirm_quote_cached`, `confirm_quote_stale`,
    `confirm_quote_before_delay`, `confirm_quote_not_exact_pool`,
    `confirm_quote_unavailable`, `confirm_quote_invalid`); the last 10 disarms and
    the counts stay on the position and the closed trade.
  - priority: a pending exit, `LIQUIDITY_EMERGENCY`, `STALE_MARKET_EXIT`, the stop,
    the target and the max hold all come first, on the arming mark and again on the
    confirmation quote (a confirmation quote at −5% net books `STOP_LOSS_NET_TARGET`).
    The net geometry runs on every finite, fresh confirmation quote before it can
    disarm — also a cached, off-pool, early or below-threshold one — and books the
    stop, target or max hold on it; only an unavailable, invalid or stale
    confirmation quote is not used as a mark.
  - quote budget: every confirmation is one forced exit-priority Jupiter quote from
    the shared keyless budget (one quote per about 2.1 s for all engine, Lab and
    gateway processes together; exits pre-empt entries). Before this bound a review
    probe of an off-pool route at the threshold took 8 forced quotes in 40 position
    ticks (20 s) without ever exiting. With the exact-pool arming rule, the 15 s
    re-arm cooldown and at most 3 confirmations per position per 5 minutes, V2 spends
    at most 3 forced quotes per open position per 5 minutes (24 with the 8-position
    cap, about 17% of the shared budget in the worst case).
  - evidence on the close reuses PR #15's `exit_impact_emergency` fields
    (`trigger_quote`, `confirming_quote`, `confirming_quote_meets_threshold`,
    `booked_quote = confirming`, `booked_impact_pct`, `entry_preflight`, `liquidity`,
    `threshold_pct`) plus `rule_version = EXIT_IMPACT_EMERGENCY_V2`,
    `threshold_anchor`, `anchor_source`, `anchor_impact_pct`, `armed_at`,
    `confirmed_at`, `confirm_delay_ms`; the dashboard history already renders them.
- Never retroactive: every position keeps the `exit_policy` it was opened with. A
  `fixed` or `adaptive` position held by this engine keeps V1 and its own ladder; a
  `cost_first` position keeps V2 if the account is switched back to the default.
- Code rollback: roll the deployed source back to a commit before this profile only
  while the `cost_first` account is flat. Older code does not know the `cost_first`
  exit policy and raises `unknown exit policy` from `engine_exit_policy.exit_reason`
  for every open `cost_first` position, so those positions would not be managed.
  From this change on, `engine_exit_policy` maps any unknown exit policy name to the
  fixed net geometry (−5% / +10% / 60 min) and logs the name once instead of raising,
  so a later rollback degrades to the fixed geometry (without V2) rather than
  stranding positions; the flat-account rule still applies.

## `/state` config of the profile

`config.signal_strategy`, `entry_policy_version`, `exit_policy`,
`exit_policy_version`, `exit_impact_emergency_version`, `universe_parameters`,
`size_rule`, `exit_parameters`, `strategy_profile` (profile version, gates, engine-owned
list, `profitability_proven: false`), `config_ownership`, and an
`effective_config_hash` that includes the whole profile (universe, size rule, exit
geometry, V2 parameters) and differs from the default and from `ORDER_FLOW_ADAPTIVE`.
`entry_score` is `null` (score is not a gate).

## Enabling it for one account (operator action; nothing here is automatic)

1. Deploy the reviewed source to the checkout that runs the gateway.
2. Wait until every engine is flat: `GET /state` on the main engine and on each
   personal engine port must show no open positions (exits are not managed while
   services are stopped).
3. Stop the services from the runtime checkout and require a clean exit:

   ```powershell
   .\scripts\start_local_paper.ps1 -Action Stop
   ```

   Confirm that no `python.exe` whose command line contains the runtime checkout
   path remains and that nothing listens on the main or personal engine ports.
4. While everything is stopped, record the choice in the gateway registry (touches
   only that account's `signal_strategy` field; balances, history, positions, ports
   and `trade_seq` are untouched):

   ```powershell
   .venv\Scripts\python.exe scripts\paper_runtime.py set-account-strategy --registry <runtime>\.runtime\accounts\user_accounts.json --user <account-uuid-prefix> --strategy COST_FIRST_ESTABLISHED_PAPER_V1
   ```

   `--strategy default` removes the choice. Short (< 8 characters) or ambiguous
   prefixes and unknown names change nothing.
5. Start the services (`.\scripts\start_local_paper.ps1 -Action Start`) and wait until
   every engine answers `/health` before any further Stop.

## How to verify

On the account's engine port (`/state`) and through the authenticated dashboard
(`/user/state`):

- `config.signal_strategy = COST_FIRST_ESTABLISHED_PAPER_V1`,
  `entry_policy_version = COST_FIRST_ESTABLISHED_ENTRY_V1`, `exit_policy = cost_first`,
  `exit_policy_version = COST_FIRST_NET_EXIT_V1`,
  `exit_impact_emergency_version = EXIT_IMPACT_EMERGENCY_V2`,
  `universe_parameters.max_fee_tier_bps = 50`, `min_liquidity_usd = 250000`,
  `max_fee_impact_roundtrip_pct = 1.2`, `size_rule.liquidity_size_fraction = 0.001`,
  `max_positions = 8`, `max_daily_loss_usd = 100`, `strict_max_roundtrip_cost_pct = 1.5`,
  `paper_only = true`, `effective_config_hash` changed for this account, and
  `account_scope.strategy_matches_request = true`.
- `entry_diagnostics.rejections` shows universe reasons with metrics in `examples`;
  `market_cost_feasibility.checked_market_candidates` counts universe candidates.
- Compare `trade_seq`, history length, `demo_balance_usd` and `demo_session_id` with
  the values before the stop; the main engine and every other account still report
  `WINNER_ENSEMBLE_PAPER_V1` (or their own profile) with their previous
  `effective_config_hash`.
- Offline: `.venv/Scripts/python.exe -m unittest tests.test_cost_first_engine_profile`
  with `PYTHONPATH=backend` (or the full `scripts/run_python_checks.py`).

To leave the profile: stop the services, `--strategy default`, start. Open
`cost_first` positions keep their V2 exit after the switch.

## Coverage caveat

The engine can only enter pools that the discovery feed returns and that the shared
live tape observes with COMPLETE 30 s coverage. Neither the discovery sources nor
the tape seat scheduler are changed here, so established cost-first pools compete for
the same bounded tape seats. Expect `promoted_verified_flow_unavailable` and the
universe reasons to dominate the rejection histogram; a frequency failure must be
attributed to coverage or to the universe before any follow-up, and a seat rule for
this universe would be its own versioned change.

## Acceptance criteria (fixed before the run; future observations only; no retune)

Copied from `docs/STRATEGY_VALIDATION.md` (COST_FIRST_ESTABLISHED_V1 block), with the
engine account added. "Engine account" = the one account running
`COST_FIRST_ESTABLISHED_PAPER_V1`, counted only on closes stamped
`entry_policy_version = COST_FIRST_ESTABLISHED_ENTRY_V1`, from the first close after
the switch.

| Gate | Lab books (`COST_FIRST_CONTROL`, `COST_FIRST_SCALED`) | Engine account (`COST_FIRST_ESTABLISHED_PAPER_V1`) |
| --- | --- | --- |
| Sample | >= 20 closed trades per book across >= 30 distinct `(mint, pool, hour)` clusters and >= 5 calendar days, within 7 days of the first close | same: >= 20 closes, >= 30 clusters, >= 5 calendar days, within 7 days |
| Net result | net PnL > 0 and expectancy per trade > 0 after a +50 bps per leg cost stress recomputed on the same closes | same |
| Uncertainty | cluster-bootstrap lower bound of expectancy (clustered by `(mint, pool, hour)`) > 0 | same |
| Profit factor | > 1; null (fewer than 10 losses) means inconclusive, not a pass | same |
| Drawdown | maximum drawdown <= 10% of the $500 book | maximum drawdown <= 10% of the account equity at the switch |
| Frequency | >= 1 closed trade per calendar day on average | same |
| Feasibility | — | >= 90% of closes booked on a fresh executable quote without an `UNSELLABLE` episode |
| Halt | — | operator switches the account back to default if net PnL reaches −$25 before 20 closes (manual rule; not automated in this change) |
| Failure | any gate missed = "inconclusive" (or "rejected on frequency" when the sample gate fails); the books stay TEST, nothing is retuned, no default account changes | same; the account returns to the default, nothing is retuned, no other account changes |

Engineering checks for V2 (not performance): every `EXIT_IMPACT_EMERGENCY` close of
this account carries `rule_version = EXIT_IMPACT_EMERGENCY_V2`, a trigger and a
confirming quote ≥ 2,000 ms apart, `confirming_quote.from_cache = false` and
`route_matches_entry_pool = true`; report arms, disarms by code, trigger blocks by
code and forced confirmation quotes per day (never more than 3 per position per 5
minutes). Passing any gate is not a profitability claim, and the 80% win-rate target
is not an acceptance criterion.

## Evaluation plan

Report per day and at the end: closes, wins/losses/breakeven, average net win and
loss, expectancy, PF (nullable), maximum drawdown, modeled versus quoted costs, exit
reasons (with V2 arms/disarms/confirms), clusters and calendar days, and the
rejection histogram split into universe, flow, safety, price and quote/cost reasons.
Compare with the Lab pair only as context (different stop and cost model), never as
paired trades. Nothing in this profile reaches the main account or another account
without a separate, reviewed change.
