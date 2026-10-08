# Lab forward tests (LAB_FORWARD_TESTS_V1)

PAPER only. These four Strategy Lab books forward-test two hypotheses that the
2026-10-08 edge study pre-registered. Each hypothesis book runs next to a random
control book. **Neither hypothesis showed positive expectancy in the research.**
The books measure whether the hypothesis beats random entries in the same universe
on new data. They are not strategies, they make no profit claim, and they are never
promoted automatically.

- Code: `backend/lab_forward_tests.py`, wired into `backend/strategy_lab.py`.
- Research source: `research/edge_study_2026_10_08/synthesis/strategies.py`
  (pre-registration sha256 `786e01fd…0189`, `synthesis/prereg.sha256`) and
  `synthesis_specs.txt` (sections LAB_A_SURGE_EST_GUARD, LAB_B_DIP_MKTDIP_GUARD and
  PROMOTION GATE).
- Tests: `tests/test_lab_forward_tests.py`. The fixtures in
  `tests/fixtures/lab_forward_obs_20261008.json` are real scan-log observations
  around research signal points, with abbreviated addresses.

## The four books

Each book is a $500 TEST book with one position at a time and a fixed $200 entry.
The size is never reduced to pass a gate. A book needs at least $200.10 (the entry plus
a network-fee reserve) and no open position to enter. That leaves about $300 of loss
headroom, which may run out before the kill rule's 50 closes; see
[Cash state](#cash-state-lab_forward_cash_state_v1).

| Book | Role | Universe (PumpSwap, SOL quote) | Signal | Exits (net) |
|---|---|---|---|---|
| `LAB_A_SURGE_EST_GUARD` | hypothesis | liquidity ≥ $50k, fee tier ≤ 95 bps | fresh buy surge: `txns.m5.buys` ≥ 30, `txns.h1.buys` > 0, m5 buys ≥ 3 × h1 buys / 12, and the same test false at the pair's previous observation | −5% / +10% / 60 min |
| `RND_LAB_A` | control of A | same as A | hashed coin, p = 0.0005 per observation, salt `synA` | same as A |
| `LAB_B_DIP_MKTDIP_GUARD` | hypothesis | liquidity ≥ $50k | price ≤ 90% of its price 15 min earlier, liquidity ≥ 85% of its level 15 min earlier, `priceChange.h24` > −50%, and market regime med15 < −0.2% | −15% / +20% / 60 min |
| `RND_LAB_B` | control of B | same as B | hashed coin, p = 0.0007 per observation, salt `synB`; no dip or regime filter | same as B |

All four books also have:

- a 300 s cooldown on a pool after each close of that pool (the research cooldown).
  The Lab's own per-token re-entry pause also applies; whichever is longer wins;
- `entry_policy_version` = `LAB_FORWARD_TESTS_V1` on every position and close;
- `lab_config_hash` = sha256 of the book's canonical parameter JSON
  (`lab_forward_tests.book_parameters`) on every position and close.

The frozen hashes, with the Lab's default cost model:

| Book | `lab_config_hash` |
|---|---|
| `LAB_A_SURGE_EST_GUARD` | `a183e57ca5f7a3e9002d7d5dd7e8a92c533396b0551647540e3a1a417c5516e0` |
| `RND_LAB_A` | `abe2cc2caf5a4515ec3a7e0a2c095ea98e48023dd3b596b4c2d54ad7cbb6ed74` |
| `LAB_B_DIP_MKTDIP_GUARD` | `b49f09dac0b802fdbfc4c9608c2533ea0064a2ed957d8d23f45676852832ea31` |
| `RND_LAB_B` | `7637d5b3206a4fe9de2ba393c1102fd8c2ea5263951abdac287fa84b723343a1` |

The canonical parameters include the resolved Lab cost-model knobs
(`NEO_LAB_GENERIC_DEX_FEE_BPS`, `NEO_LAB_BASE_SLIPPAGE_BPS`, `NEO_LAB_LATENCY_BUFFER_BPS`,
`NEO_LAB_NETWORK_FEE_SOL`, `NEO_LAB_MAX_PRICE_IMPACT_PCT`; defaults 30, 10, 10, 0.0001
and 20), read through the Lab's frozen cost-model copy `lab_paired_costs`. Admission,
booking, the model-net exit triggers and net50 all depend on them. Setting any of them
therefore produces new hashes, and so a new test. As a second check, the Lab compares its
own resolved values with the hashed ones before every forward-test entry. On any
difference it refuses the entry (`lab_forward_cost_model_mismatch`, with
`cost_model_mismatched_fields`).

A parameter change produces a new hash, which makes it a new test. Closes under an
old hash never count toward the new test's kill rule or gate. The tests pin these
values, and so does `strategy-lock.json` (`lab_forward_tests`).

### When an observation counts

- **Observation.** A distinct `updatedAt` of a pool in the feed the Lab polls.
  This matches how the research kept one point per engine batch. A refresh that
  serves the same observation again is not a new observation.
- **LAB_A "previous observation".** The pool's last distinct observation before
  the current one, in this Lab process's memory. A pool's first observation after
  a restart therefore never signals.
- **Random controls.** The draw is deterministic:
  `blake2b-64("salt|pairAddress|int(updatedAt)") / 2**64 < p`. This is the
  research `hashed_coin` verbatim. The same observation always draws the same
  result, so a repeated refresh cannot add draws.
- **LAB_B references.** The 15-minute references are the pool's last observation
  at or before `updatedAt − 900 s`. As in the research `P.ago`, there is no
  staleness limit. A pool not observed in the last 61 min (the pair-history
  retention) has no reference.

### LAB_B market regime (med15)

The regime is computed for each whole minute T (a multiple of 60,000 ms) from the
feed the Lab sees:

1. Take every PumpSwap pool quoted in SOL whose last observation at or before T is
   at most 3 min old and had liquidity ≥ $20k.
2. Compute each pool's % change against its last observation at or before
   T − 15 min. That comparison point must be at most 10 min stale.
3. If at least 8 pools qualify, med15 is the median of their changes. Otherwise it
   is unavailable and LAB_B does not signal.

A decision observed at time t uses the regime of floor(t / 60 s). The regime for T
is computed on the first refresh at or after T, from the observations received by
then, and is cached. Every LAB_B diagnostics row publishes the current minute's
regime (`entry_diagnostics.lab_forward.regime`).

The test `RegimeTests.test_research_regime_is_reproduced_from_the_feed` replays the
83 pools around one research LAB_B signal. It reproduces the research grid's
med15 of −1.2722% over the same 33 pools.

`research/edge_study_2026_10_08/lab_forward_parity.py` runs the same comparison at
scale. It replays 21 hours of scan-log points (780,718) through this module and
compares each one with the frozen research functions, rug screens excluded on both
sides. The results:

- the regime matched in all 1,191 minutes;
- RND_LAB_A and RND_LAB_B drew identically;
- LAB_A matched 130 of 132 signal points;
- LAB_B matched 3,286 of 3,290 signal points.

The 6 points that only the research found needed an observation older than the
61-minute pair history, which the Lab has already forgotten.

### What is reused, not duplicated

- **STRUCTURAL_RUG_GUARD_V1.** Applied by `DEFENSIVE_ENTRY_LAYER_V1` before every
  entry and again at commit. It covers the research's `rug_guard_v1` plus its
  interim screen, with the 2% fake-cap threshold and the corrected ticker-reuse
  rule. Its young-pool rule (≥ 720 min) subsumes LAB_A's age ≥ 60 min.
- **PairHistory.** The defensive layer's pair history supplies prices, contiguous
  segments and feed gaps. `ForwardFeedMemory` adds only what PairHistory does not
  store: the LAB_A surge state of each pool's last two observations and per-pool
  liquidity samples. The memory is bounded and in memory only, like PairHistory.
- **POOL_LOSS_MEMORY_V1.** Applied from each book's own history (2 consecutive
  losses on a pool → 6 h pause).
- **HEAT_VETO_STACK_V1.** Runs **log-only** for all four books: these books test
  surge and dip hypotheses that the veto would remove. Its flags are recorded on
  every position and close (`heat_log_only_flags`, plus
  `defensive_entry.log_only_flags`). `heat_veto.LOG_ONLY_BOOK_IDS` keeps
  reserving the two hypothesis arms unchanged, so no engine's effective config
  hash changes. `strategy_lab.heat_log_only_book` adds their controls.

Price integrity with exact mint/pool identity, network-cost knowledge and the Lab
commit recheck apply as for the cost-first books. Verified tape flow and RugCheck
are not required: the research hypotheses use DexScreener data only, as do the
ordinary TEST books.

### Tape seats

These books never read tape flow: there is no flow gate at entry and no flow exit.
Their marks come from the shared feed or from the DexScreener exact-pair refresh
(`lab_position_marks`). Their positions therefore take **no** tape exit pin
(`lab_forward_tests.TAPE_PIN_REQUIRED = False`; tape policy
`STABLE_COST_AWARE_TAPE_DISCOVERY_V6_NO_PINS_FOR_FLOW_FREE_LAB_BOOKS`).

Before V6, the tape scheduler pinned every Lab position without a cap, and the live tape
runs with `NEO_TAPE_MAX_PAIRS=4`. The random controls draw many times per hour and hold
for up to 60 min, so they are in the market most of the time, and the four books alone
could have held all four seats. That would have starved main, the personal engines, the
`COST_FIRST_*` books and the promoted Lab books of verified-flow seats.

A pool that main or a personal engine also holds stays pinned by that holder.
`live_tape_status.entry_scheduling.unpinned_flow_free_lab_positions` counts the
positions left unpinned. Every other Lab book's positions are pinned as before.

## Costs, booking and net50

Admission uses the Lab's one rule, `lab_activity.admission_cost_cap_pct`:
0.5 × the net stop, bounded by the unchanged 2.75% model ceiling. Each book
applies it to its own pre-registered stop:

- LAB_A and RND_LAB_A: 2.5%.
- LAB_B and RND_LAB_B: 2.75%.

The modeled round trip at $200 must fit the cap, or the signal is counted under
`cost_rejected` with `modeled_roundtrip_cost_limit`. The size is never reduced to
make it fit.

**The cap filters the research populations.** At $200, about 72% of the
research's LAB_A trades and 23% of its LAB_B trades fit (in-sample, 22.8 h).
LAB_B's research trades sat mostly in 95–115 bps pools, with a median modeled
round trip of 3.18%. Both controls face the same cap, so each comparison stays
like for like. But the forward population is cheaper than the research one, and
LAB_B will trade far less often than the research's 0.5–3.5 per hour.

Booking adds CALIB_V1 (research `calib/calibration.py`, basis `engine`,
conservative) per leg to the Lab's shared spot model
(`strategy_lab.calibrated_entry_execution` / `calibrated_exit_execution`, a price
penalty added to impact + slippage + latency):

- fee tier ≤ 50 bps: +22 bps;
- fee tier 52.5–125 bps: +10 bps (the conservative floor);
- liquidity < $50k or a trade > 0.3% of liquidity: +25 bps;
- the engine's fixed rent and network costs: +5.5 bps per leg at $200.

The book holds the calibrated quantity, and every mark and the close sell with the
same extra. The balance therefore moves by the calibrated result. Positions record
`calib_bps_per_leg`, `cost_calibration` (its components) and `model_quantity`.

**Exit triggers** use the uncalibrated model net of the model quantity
(`model_pnl_pct`), because the research's exits decided on the model net. The
order is stop, then take-profit, then max hold. The booked `pnl_pct` is shown
next to it.

Each close records:

- `pnl_usd`: booked, including CALIB_V1 and the drain-aware exit below.
- `model_pnl_usd`: the shared model without calibration.
- `calibration_cost_usd`: CALIB_V1's share of the difference.
- `drain_valuation_cost_usd`: the drain-aware exit's share of the difference (0 unless
  the sale exceeds 12.5% of the pool's reported liquidity).
- `close_kind`: `marked`, `drained` or `vanished`, with `exit_liquidity_usd` and
  `close_policy_version`.
- `net50_usd` / `net50_pct`: the booked net minus 50 bps per leg, minus another
  200 bps on stop exits and 100 bps on trailing exits. This uses the research
  `calibrate_trade` form: `(1 + booked) × (1 − 50 bps) × (1 − (50 bps + extra)) − 1`.

### Drained and vanished pools (LAB_FORWARD_CLOSE_POLICY_V1)

The shared spot model caps exit impact at 20% and treats a liquidity of 0 as $1. On
its own it would book an LP pull (liquidity to 0 at an unchanged price) at about −21%.
It would also never close a position whose exact pool stopped giving a usable mark: the
1-slot book would stay blocked, and the loss would never reach the kill rule or the
gate. The research harness values a drained pool at 0 (`harness_final` F6). It closes a
pool that leaves the feed at its last price minus 10% (`VANISH_HAIRCUT_PCT`, F3). For
these four books only:

- **Drain-aware booked exit.** Every booked mark and close uses an impact of
  `max(shared capped impact, x / (1 + x))`, where `x = 2 × sale value / reported
  liquidity` (research `rug/realistic_exit.py`, a constant-product exit). This equals
  the shared model while `x ≤ 0.25`, so ordinary trades are unchanged. It rises toward
  100% as liquidity drains, and a reported liquidity of 0 sells for nothing.
  - The reported liquidity is the DexScreener pair object's own `liquidity.usd` when
    the mark carries that object, else the feed's `liquidityUsd`. In the scan feed, a
    PumpSwap liquidity of exactly 0 was always terminal.
  - Unknown liquidity keeps the shared impact.
  - The exit **triggers** are unchanged: the uncalibrated model net still decides, and
    a drained pool trips the stop.
- **Vanished pool.** A position closes as `VANISHED_NO_FRESH_MARK` at its last usable
  mark minus 10% when all of these hold:
  - it is held at least max hold + 10 min (70 min);
  - its last usable exact-pool mark is at least 10 min old;
  - this Lab process has looked for a mark without success for at least 60 s, so after
    a restart the exact-pair refresh is retried first;
  - the shared feed still carries a priced observation at most 60 s old, so a feed
    outage is not taken for a vanished pool.

  The value goes through the drain-aware exit at the last mark's liquidity, and the
  exit-reason extra in net50 is 0. Each position keeps `last_mark` (price, native price,
  liquidity, market cap and quote) for this purpose.
- **Counted, not hidden.** Drained and vanished closes are ordinary closes of the frozen
  config, so they are in the kill-rule sample. The evidence counts them
  (`vanished_closes`, `drained_closes`). An open position past its max hold that still
  has no mark is reported as `open_unpriced_past_max_hold`, and the gate counts it as a
  pending vanished close.

The research's feed-gap rule (a pool absent for more than 10 min that returns is closed
at the lower of the pre-gap and return prices) is not replicated. Here a returning pool
resumes, and its exits are decided on the returning mark.

## Kill rule (LAB_FORWARD_KILL_RULE_V1)

The kill rule applies to each of the four books. It counts only closes stamped with
the book's current `lab_config_hash` and `LAB_FORWARD_TESTS_V1`.

After at least 50 such closes, the book is retired when both of these hold:

- mean net50 $/trade < 0;
- the upper bound of the 95% CI of mean net50 $/trade < 0.

The CI is a pair (mint, pool) bootstrap with 2,000 resamples and a fixed seed of
20261008. With fewer than 3 distinct pools, a per-trade normal approximation is
used instead.

Retirement:

- stops new entries only. It never touches balance, history or the exits of an open
  position;
- persists across restarts and is never undone automatically.

The marker is `strategy_lifecycle` with version `LAB_FORWARD_KILL_RULE_V1` and
`reason: pre_registered_kill_rule`. These four books are judged only by this rule.
The shared 12-close lifecycle heuristic would retire a control (expected about
−5%/trade) before the comparison it exists for.

The rule applies to the controls too, as the task specified. However, a $500 book at a
fixed $200 entry will more likely run out of cash first (next section) than reach 50
closes. In that case the kill rule never evaluates.

The research protocol's further "drawdown > 30%" kill clause is not automated. The
book's booked max drawdown is published with the gate for review.

## Cash state (LAB_FORWARD_CASH_STATE_V1)

A book enters only while its balance is at least $200.10: the fixed $200 plus a
$0.10 network-fee reserve (0.0001 SOL up to $1,000/SOL). When it has no open position
and its balance is below that, it can never trade again, because a balance only moves
on a close.

**This is the expected path for the random controls.** The research's own net50
results for the controls are −$10.3 and −$12.3 per $200 trade (`RND_LAB_A`, train and
holdout) and −$11.0 and −$9.0 (`RND_LAB_B`) (`synthesis/insample_results.json`). At
those rates the $300 of headroom lasts about 25–35 closes. Any average loss above $6
per trade spends it before the kill rule's 50 closes.

Such a book is not shown as active. Its `strategy_lifecycle` has:

- `status: cash_exhausted`, `reason: balance_below_fixed_notional`;
- `entry_enabled: false` and `kill_rule_evaluable` (false under 50 closes);
- `cash` with `balance_usd`, `min_entry_balance_usd`, `exhausted_at` (its last close)
  and `last_entry_at`.

The Lab's entry diagnostics name `lab_forward_cash_exhausted`. The dashboard shows
"БЕЗ КАПИТАЛ". The state never touches balance or history. A kill-rule retirement
takes precedence and persists.

Two consequences, both deliberate:

- **The kill rule may never evaluate** for a book that runs out of cash first. The
  state says so instead of showing an active book that cannot trade.
- **The gate's control comparison is limited** to the period in which the control
  could still enter (see `control_coverage` below). A control that ran out of cash on
  day 1 cannot support a 3-day comparison, so the gate fails.

Funding the books for the pre-registered horizon is the owner's decision. The task fixed
$500 and $200. One option is a start balance that covers about 150 trades at the
controls' research expectancy, roughly $2,000. That change would produce new config
hashes, so it would be a new test.


## Promotion gate (owner review only, never automatic)

`strategy_lifecycle.promotion_gate` on LAB_A and LAB_B evaluates the research
PROMOTION GATE on trades entered after the Lab start (net50 basis). It never
promotes anything: `automatic_promotion` is always false, and the books stay TEST
whatever it says.

| Criterion | Threshold |
|---|---|
| Sample | ≥ 150 closed trades over ≥ 25 pairs and ≥ 3 days, covering every UTC hour |
| Expectancy | mean net50 > 0, and the pair-bootstrap CI95 lower bound of mean $/trade > 0 |
| Control coverage | the control could still enter (not retired, not out of cash) for ≥ 90% of the hypothesis's evaluated trades and of its time window (`control_coverage`) |
| Control | inside that same period, the hypothesis beats the random control by more than the control's CI half-width |
| Concentration | top pair share ≤ 0.20, still positive with the best pair removed, no single day > 50% of the P&L |
| Drawdown | a 3-slot $1000 book's max drawdown ≤ 20% (not evaluated in the Lab: it needs an offline replay; the 1-slot book's own drawdown is published) |
| Rug guard | zero entries on pools the structural guard flagged |
| Pricing | vanished, drained or unpriced closes, plus an open position past max hold without a mark, < 10% of trades |

**Same period.** The comparison window runs from the hypothesis's first evaluated entry
to the earlier of two times: its last evaluated close, and the moment the control could
no longer enter (`retired_at`, or the control's last close if it ran out of cash). Both
books are compared on trades opened inside that window. `promotion_gate.control_window`
publishes the window, the control's end reason and the trade and time shares.
`book_cash_state` shows whether the hypothesis book itself can still trade.

Passing every evaluable criterion only makes a book eligible for the owner's review
and an offline 3-slot replay. Promotion to an engine account would be a separate,
versioned change.

## Restarts

Nothing in `ForwardFeedMemory` is persisted. After a restart:

- LAB_A needs a pool's second observation;
- LAB_B needs 15 minutes of history (and the regime needs 8 qualifying pools);
- the controls can draw immediately.

The structural guard's ticker-registry warm-up still applies to pools under 14 days
old (`rug_ticker_registry_warming`). The heat warm-up is log-only here.

The vanished-pool clock is in memory too. After a restart, a held pool that has no
mark is looked for for 60 s before it can close as vanished. Its persisted
`mark_received_at` and `last_mark` still date the last usable mark.
