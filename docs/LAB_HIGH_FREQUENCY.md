# Lab high-frequency PAPER books (LAB_HIGH_FREQUENCY_V1)

PAPER only. These books never build, sign or send a transaction, never call Jupiter or
RugCheck, and are **expected to lose money**. They are a measurement, not a strategy, and
they are never promotion candidates.

Owner request (2026-10-09): "it has to trade more often like 50 trades per hour for each
strategy".

The edge study of 2026-10-08 (`research/edge_study_2026_10_08`) found no strategy with
positive expectancy after costs:

- A round trip costs about 1.4% in the cheapest established pools and 3-6% elsewhere.
- DexScreener prices lag the chain by about 20 s and refresh about every 30 s per pair.
- Random entries lose 4-8% per trade.

So the existing strategies, the structural rug guard and the cost caps stay exactly as they
are. Instead, a separate family of three PAPER books trades about 50 times an hour each.
Each book has its own capital, its own random-entry control, honest fills and a daily loss
cap, so it cannot wipe itself out. The study behind every rule is archived in
[research/hf_study_2026_10_09](../research/hf_study_2026_10_09/README.md).

Code: `backend/lab_high_frequency.py` (the container), wired into `backend/strategy_lab.py`.
Storage: `strategy_lab_hf/` next to the Lab ledger. The books are never in
`strategy_lab.json`, `STATE['books']`, the lifecycle review or the promotion migration.

## Books

| Book | Dashboard name | Role | Entry rule at a refresh event |
|---|---|---|---|
| `HF_RND_E95` | HF контрола · случайни входове | random control | `lab_forward_tests.hashed_coin(pairAddress, updatedAt, 0.25, 'hfA')` is true |
| `HF_QUIET_E95` | HF тихи пулове | hypothesis, control `HF_RND_E95` | `volume.m5 / liquidityUsd <= 0.002` and `abs(price / previous-observation price - 1) <= 0.006` |
| `HF_DIP15_E95` | HF дъно 15 мин | hypothesis, control `HF_RND_E95` | `price / (minimum priceUsd of the pool's observations in the trailing 900 s, current one included) - 1 <= 0.002` |

- `HF_QUIET_E95` measures pool selection, not price. Its gap over random disappears inside
  fee buckets: 66% of its holdout trades were in pools of 50 bps or less, against 43% for
  the control.
- `HF_DIP15_E95` needs the pool's HF memory to reach back to `updatedAt - 900 s`. Until it
  does, the reason is `hf_dip15_history_warming`. The test is written in the research's
  float form (`price / low - 1 <= 0.002`), so boundary cases decide as in the research.
- The heat veto is **enforced** for all three books. No hypothesis needs it off: on train,
  DIP15 lost the same with it off.
- Portfolio group: `HF_EXPERIMENT`. Each book starts with $1,000.

### Frozen identities

Each strategy config hash is the sha256 of
`json.dumps(book_parameters(id), sort_keys=True, separators=(',', ':'), ensure_ascii=True)`,
the same basis as `lab_forward_tests`. It covers:

- the family version, the role and the control;
- the universe, the refresh event and the signal (`p` and `salt` for the control);
- the book rules, the pool rule, the exit, the fills and the print guard;
- the accounting, the defensive modes, the resolved Lab cost model and the kill rule.

The budget (start balance, daily cap, session rotation and capital floor) has its own hash.
A budget change never starts a new evidence sample. A strategy-hash change starts a new
sample in the same ledger: the aggregates are kept per (book, config hash).

| Book | `config_hash` |
|---|---|
| `HF_RND_E95` | `d4d0a6c29f9dd56d161d86fd12a824546306a43a71463fba1d6d44d74d0872d6` |
| `HF_QUIET_E95` | `4195a6e1bf7cc3d31d23dac4cf7ac83fd350da7765a8837d05438a44bab9ef06` |
| `HF_DIP15_E95` | `dc60cdb56c44163e2a38dbaab778edd4a28493e021daf9c64502d8faec8fdb38` |

Default budget hash ($1,000 start, $100 daily cap, 7-hour session step, 50% floor):
`d6bf441229424a474e97d18ab2afdc17bca385868d0454a2cea4676bbfd639fa`.

The hashes are pinned in `tests/test_lab_high_frequency.py`, in `strategy-lock.json`
(`lab_high_frequency`) and in this table. They are published on main's `GET /state` as
`strategy_lab.activity_config.lab_high_frequency.config_hashes` and `budget_hash`.

## Shared rules (identical for every book)

### Universe: HF_UNIVERSE_E95_V1

A candidate must pass every one of these checks:

- It is in the Lab's main `/state` feed and passes `lab_activity.usable_feed_coin`.
- `dexId == 'pumpswap'` and the quote is SOL.
- `liquidityUsd >= 50,000`.
- `strategy_lab.pumpswap_fee_bps(coin) <= 95`.
- The modeled $25 round trip (`entry_execution` + `exit_execution`, uncalibrated) is at most
  2.75% (`lab_activity.MAX_ENTRY_COST_PCT`). It is logged on every order (`rt_model_pct`)
  and the size is never reduced. In the E95 universe this round trip had a median of 1.73%
  and a maximum of 2.47%, so the cap never binds.
- The pool is not blocked by the toggle guard (`hf_toggle_pool`, see below).

### Decision event: HF_REFRESH_EVENT_V1

- A decision is taken only at a fresh DexScreener refresh. That is a new
  (`pairAddress`, `updatedAt`) observation whose `priceUsd` differs from the same pool's
  previous observation, where that previous observation is at most 60 s older.
- A restamp at the same price is not an event, and neither is a return after more than
  60 s. Each event is decided once.
- HF keeps the last (`updatedAt`, `priceUsd`) of each pool from the main feed only, plus 15
  minutes of price runs for DIP15 and 10 minutes of large steps for the toggle guard. A pool
  not seen for 61 minutes is dropped.
- Candidates are ordered by (`updatedAt`, `pairAddress`) ascending. Each book places at most
  one new order per Lab refresh (2 s).

### Defensive layer

The three books share one `DefensiveEntryLayer.evaluate(coin, now, blocked_pools={},
heat_log_only=False)` per pool per refresh. `on_refresh` first calls `defense.observe` again,
which is idempotent.

- **STRUCTURAL_RUG_GUARD_V1: enforced.** It is checked at the decision and again on the entry
  fill observation. A block at the fill cancels the order with
  `hf_structural_block_at_fill`. The check fails closed: an error, or a registry that cannot
  vouch, also blocks.
- **HEAT_VETO_STACK_V1: enforced**, warm-up included. Expect no HF order for about 15
  minutes after a Lab restart.
- **POOL_LOSS_MEMORY_V1: shadow only.** Every order and close records `plm_v1_would_block`,
  computed by `pool_loss_memory.index` over the book's in-memory closes of the last 6 h. With
  it enforced, HF books traded 3.5-4.0 times an hour on the holdout, because about 99% of HF
  closes lose. For these books it is replaced by HF_POOL_RULE_V1 (see
  [DEFENSIVE_ENTRY_LAYER.md](DEFENSIVE_ENTRY_LAYER.md#lab-high-frequency-books-lab_high_frequency_v1)
  for the recorded acceptance).
- **Toggle guard (HF_PRINT_GUARD_V1):** a pool is ineligible for every HF book for 6 h after
  a TOGGLE. A toggle is a step of |dp| >= 15% between consecutive main-feed observations
  after which the price returns within 2% of the pre-step price within 600 s. It is known at
  the return observation, from HF's own memory. One pool with $2.8-3.5M of liquidity flipped
  between two price levels about ±44% apart, and 11 pools toggled in 43 h.

### Book rules: HF_BOOK_RULES_V1

- Each book has 3 slots and a fixed $25 notional (`FIXED_NOTIONAL_NO_BACKOFF`).
- An order needs an available balance of at least $25.10: the balance minus the
  reservations of unclosed slots. The $25.10 is reserved at order time and released at the
  close or cancel.
- One position per pool per book. A pool counts as held from the order until its exit fill.
- **HF_RATE_GOVERNOR_V1:** at most 50 orders per book in any trailing 3,600 s, by decision
  time. Orders count even if they are later cancelled.
- **HF_POOL_RULE_V1:** a 120 s per-pool cooldown after that book's exit fill (its observation
  time) or cancel. There is no loss brake: on train, a 4-loss / 30-minute brake cut the rate
  to 19 trades an hour.
- The resolved Lab cost model must equal `lab_forward_tests.PREREGISTERED_COST_MODEL`.
  Otherwise every book refuses orders with `hf_cost_model_mismatch` (fail closed), and
  `cost_model_mismatches` lists the knobs that differ.

### Exit: HF_EXIT_TIME_120_V1

The exit is ordered at the first observation of the pool with
`updatedAt >= decision_at + 120,000 ms` that is also later than the entry fill. There is no
stop and no take-profit: stops would add 200 bps to net50 and barely fire.

### Fills: HF_FILL_NEXT_REFRESH_V1 (research harness F1)

Both legs use `lab_forward_tests.new_fill_leg` / `advance_fill_leg` with `FILL_BASIS` (a 60 s
window, 30 s grace).

- The fill is the first later exact-pool observation whose price differs from the leg's
  decision print (`next_refresh`).
- With no changed print within 60 s, the fill is the first later observation (`quiet`,
  resolved at the window end).
- An entry with no later observation within 60 s is cancelled with
  `hf_entry_no_next_observation` and its slot is released.
- An exit with no observation within 60 s waits for the next observation and fills there
  (`late_next_observation`), as the harness took the next point.
- Out-of-feed held pools are marked through the existing
  `lab_position_marks.POSITION_MARK_FEED.resolve`. At most 9 HF positions can be open, within
  its limit of 16 pending refreshes.
- **VANISHED:** no new usable mark for 10 minutes while the shared feed is alive. This
  process must also have looked for a mark for 60 s, so a restart first retries the
  exact-pair refresh. The close is valued at the last mark minus 10%
  (`lab_forward_tests.vanished_coin`).
- **FEED_GAP:** a held pool that comes back after more than 10 minutes is valued at
  min(pre-gap price, return price). A return at liquidity 0 values at the return. This only
  happens when VANISHED could not fire, for example when the shared feed was down too.
- A pool at liquidity 0 is valued at 0 (drain-aware exit).
- **Restart:**
  - ORDERED slots are cancelled with reason `restart`.
  - OPEN slots resume. An overdue exit is ordered at the first observation after the restart
    (`late_exit_restart`).
  - EXIT_ORDERED legs keep resolving.

### Print guard: HF_PRINT_GUARD_V1 (valuation)

- A fill print 15% or more away from its leg's decision print is valued at the less
  favourable of the two (buy: the higher; sell: the lower), with the flag `fill_outlier`. For
  the exit leg the decision print is the trigger print.
- The exit valuation price is capped at 1.15 x the entry valuation price (`gain_capped`).
  Losses are never capped.
- On the holdout the toggle guard plus this valuation changed net50 per trade by -0.07 pp
  (control), -0.05 pp (quiet) and -0.05 pp (15-minute low), and the rate by -1% to -6%. The
  guard is conservative by construction.

### Accounting: HF_ACCOUNTING_V1

- **Booked:**
  - entry `strategy_lab.calibrated_entry_execution(fill coin, 25, calib)`;
  - exit `calibrated_exit_execution(fill coin, qty, calib, drain_aware=True)`, both at the
    print-guarded fill prices;
  - `calib` is `lab_forward_tests.calib_extra_bps_per_leg(fee tier at the entry fill,
    liquidity at the entry fill, 25)` (CALIB_V1). At $25 it includes 44.2 bps of engine
    fixed costs per leg.
- **net50** = `lab_forward_tests.net50(booked pnl, 25, reason)`, with reason `HF_TIME_120`,
  `VANISHED` or `FEED_GAP`. None of these carries a stop or trailing extra.
- **net0** = uncalibrated `entry_execution` / `exit_execution` at the same valuation prices.
- **Research-fill shadow:** every close carries a `research_fill` block in the
  `LAB_FORWARD_FILL_BASIS_V4` shape (leg statuses, fill prices, lags, net50). It re-values the
  stored entry fill observation and the exit fill observation with the booked model. Booking
  *is* at the research fill, so `minus_booked_usd` is 0, an invariant the tests check.
- **Decision-print shadow** (`decision_print_shadow {pnl_usd, net50_usd}`): the same trade
  valued at the decision print and the trigger print, without the print guard. That is what
  the other Lab books would book. On the holdout, stale prints flattered DIP15 by 0.13 pp per
  trade and the other books by 0.0-0.02 pp.
- **HF_PRICE_AUDIT_V1** (log only, never blocks):
  - `pair_price_integrity.cached(coin)` is a read-only lookup plus `validate`, with no fetch.
  - At decisions HF calls `check()`, which schedules a GeckoTerminal fetch. It does so at most
    twice in any rolling 60 s across all HF books, and only when no reference is cached.
  - Each close records `{status, deviation_pct, reference_age_ms}` for both fills when a
    reference exists.
  - HF never calls Jupiter or RugCheck.

### Daily loss cap and sessions: HF_DAILY_LOSS_CAP_V1, HF_SESSION_ROTATION_V1 (budget)

- The cap is set per book. It trips at -$100 per UTC day, counting the booked P&L of today's
  closes plus the sum of min(0, open booked marks).
- When it trips, the book places no new orders until 00:00 UTC. Already-ORDERED entries and
  open slots still run to their exits. The trip writes a journal `cap` row with its time and
  P&L.
- On UTC day d (d = floor(ms / 86,400,000)), new orders are allowed only from hour
  (7 x d) mod 24. Before that hour the reason is `hf_session_not_open`. The cap budget
  therefore covers different UTC hours each day: Oct 10 opens at 00:00, Oct 11 at 07:00, Oct
  12 at 14:00, Oct 13 at 21:00, and so on.
- Budget knobs, read from the environment (budget hash only):
  - `NEO_LAB_HF_DAILY_CAP_USD` (default 100);
  - `NEO_LAB_HF_START_BALANCE_USD` (default 1000).

  An invalid value keeps the default and is listed in `budget_env_errors`.

### Kill rule: HF_KILL_RULE_V1

Retirement stops new orders only: open slots run to their exits, and the balance and the
journal are never touched. A book is retired permanently on any of:

- **(a) Capital floor:** equity (balance + open booked marks) <= 50% of the start balance.
- **(b) Statistical checkpoint**, hypotheses only. The first checkpoint comes at >= 500
  closes of the current config and >= 3 UTC days with closes, then every 250 more closes. The
  book retires when all three hold:
  - mean `net50_usd` < 0;
  - the `lab_forward_tests.bootstrap_ci` upper bound (pair groups, 2,000 resamples, seed
    20261008) < 0;
  - (hypothesis mean `net50_pct` - control same-period mean `net50_pct`) <= the control's CI
    half-width.

  Every evaluation also records the within-fee-bucket gap.
- **(c) Operator flag:** `NEO_LAB_HF_RETIRE=<comma-separated ids>`.

**Control continuity:**

- The control keeps entering while any hypothesis can enter.
- It retires when every hypothesis has retired, or on its own floor or the operator flag.
- If it hits its floor first, the hypotheses keep running and show `control_retired`.
- The Lab's 12-close lifecycle heuristic never applies: these books are not Lab books.

Expected: the hypotheses retire at their first checkpoint, about day 4.

## Storage: HF_JOURNAL_V1 / HF_CHECKPOINT_V1

- **Journal:** append-only `strategy_lab_hf/journal/<BOOK>/<YYYY-MM-DD>.jsonl` (UTC day of
  the row). Each loop does one flush + fsync per touched file. Every state change is a row,
  and each row carries `v`, `seq`, `book`, `cfg`, `budget`, `at` and
  `entry_policy_version: LAB_HIGH_FREQUENCY_V1`.
- **Row kinds:**
  - `order`: `side: entry` or `side: exit`;
  - `fill`: the entry fill;
  - `cancel`;
  - `close`: it contains the exit fill;
  - `cap`, `session` and `retire`.
- **Row sizes:** orders and fills are about 1 KB, closes about 1.6 KB.
- **Close fields:**
  - identity: `seq`, `book`, `cfg`, `budget`, `address`, `pairAddress`, `symbol`;
  - pool and decision: `fee_bps`, `liq_at_fill`, `decision_at` / `decision_price`;
  - legs: entry and exit fill at / price / status / lag, plus `trigger_at` / `trigger_price`;
  - outcome: `close_kind`, `qty`, `capital_committed_usd`, `net_proceeds_usd`,
    `pnl_usd` / `pnl_pct` (booked), `net0_*`, `net50_*`, `calib_bps`;
  - shadows and audit: `research_fill`, `decision_print_shadow`, `print_guard`,
    `plm_v1_would_block` (at close) and `plm_v1_at_order`, `price_audit`,
    `late_exit_restart`;
  - balance: `balance_after`, `closed_at`.
- **Checkpoint:** `strategy_lab_hf/state.json` is written atomically every 15 s when anything
  changed, and on stop (`persist('stopped')` forces it). The journal is always flushed first.
  It holds:
  - balances and slots (with their fill legs);
  - the 6-hour pool memory: recent closes for the loss-memory shadow, and cooldowns;
  - toggle blocks, the governor, and the cap, session and kill state;
  - per-book aggregates, the last 20 closes and the last `seq`.

  It is well under 200 KB.
- **Restart:** the container loads the checkpoint and replays journal rows with `seq` above
  the checkpoint's, in `seq` order and exactly once. It then applies the restart rules above.
  - An unreadable checkpoint is rebuilt from the whole journal.
  - A torn last line (a crash mid-write) is closed with a newline, never removed, and
    skipped as malformed.
- **Aggregates per (book, cfg):**
  - counts, sums and sums of squares of booked, net0 and net50 in $ and %;
  - wins;
  - `by_pair [n, net50$, booked$]` and `by_fee_bucket`;
  - `by_day {orders, closes, cancels by reason, booked, net50, cap_tripped_at}`;
  - `by_hour_utc`;
  - close timestamps over the last 3,600 s;
  - the equity peak and the maximum drawdown of cumulative booked P&L.

  The tests check that the live aggregates equal a recomputation from the journal, across
  random restarts.
- **Reset:**
  - A Lab reset flag (`NEO_STRATEGY_LAB_RESET_FLAG`) moves `journal/` and `state.json` into
    `strategy_lab_hf/archive/reset-<ms>-<id>/`.
  - `paper_state_reset.reset_all_offline` moves the whole `strategy_lab_hf/` into the reset
    archive with a sha256 manifest and recreates it empty.
  - Nothing is deleted.

## Process integration (`backend/strategy_lab.py`)

- `HF_ROOT` is `NEO_STRATEGY_LAB_HF_DIR`, defaulting to `strategy_lab_hf/` next to
  `strategy_lab.json`.
- `main()` builds the container when `NEO_LAB_HF_ENABLED` is `'1'` (the default). It injects
  the Lab's cost functions, its defensive layer, `POSITION_MARK_FEED` and
  `pair_price_integrity`.
- Each loop the Lab calls:
  - `HF.update` after `update_positions`;
  - `HF.on_refresh` after `maybe_open` when the refresh is due.

  Each call has its own `try/except`. An error sets `STATE['hf_error']` and the Lab
  continues.
- **Time budget:** HF work over 250 ms in 3 consecutive loops makes HF `hf_degraded`: no new
  orders, while exits continue. It clears at the first loop within budget.
- **`persist()`:**
  - It checkpoints, forced on stop.
  - It publishes `activity_config.lab_high_frequency` (definition, hashes, budget, mismatches,
    `running`) and `persistence.hf` (loop time, journal and checkpoint writes, the refresh's
    universe counts, price-audit use and orders in the last 60 minutes).
  - It writes the HF dashboard view **only** into the compact projection
    (`strategy_lab_compact.json`, key `high_frequency`). The full ledger carries the
    definition and metrics, never HF trades.
- `lab_dashboard_projection.compact_strategy_lab` passes `high_frequency` through. Main and
  the gateway are unchanged, and so are `lab_activity`, the lifecycle, the migration and the
  defensive modules.
- HF positions are never in the Lab's `books`, so the tape scheduler, which pins Lab
  positions from `strategy_lab.books`, never gives them a seat. The books never read flow.
- **Measured cost:** 90 feed coins, 3 books with 3 slots each, the real defensive layer
  evaluated, one hour of 2 s loops.
  - The update and the decisions take 3.5 ms per loop on average (p95 6.5 ms, max 14 ms).
  - The checkpoint, view and metrics add 1.8 ms on average.
  - HF's own memory stays under 5 MB.
  - Each book places exactly 50 orders an hour (the governor).
  - The journal grows about 0.7 MB per hour of 3 x 50 trades. With the cap, the books trade
    about 3 hours a day.

## Dashboard (`src/components/LabHighFrequencyPanel.tsx`)

In the Strategies section, outside the advanced details, under the title
`Висока честота (HF)` with the amber badge `ЕКСПЕРИМЕНТ · ОЧАКВА СЕ ЗАГУБА`.

The note reads: "Около 50 сделки/час на книга, $25 на сделка, изход след 2 мин. Цени:
следващото опресняване на DexScreener + моделирани разходи (CALIB_V1, net50); не е изпълнима
котировка. Изследването очаква ≈ −3,6% на сделка (≈ $37–44/час на книга). Измерване, не
стратегия; без промоция."

Each book shows:

- its status: active, warming, session closed until HH:00 UTC, `спрян до 00:00 UTC` (cap),
  retired, degraded or cost model mismatch;
- trades in the last 60 min and today, and open slots x/3;
- booked and net50 $, today and in total;
- mean %/trade (booked and net50), the win rate and $/hour today;
- a cap bar and the kill evidence;
- the gap vs the control, with its CI and within fee buckets;
- the top-pair share and cancels by reason;
- an expandable list of the last 10 closes.

The view is at most 16 KB. Without the key the panel shows `Няма HF данни`.

## Reporting

- `scripts/paper_edge_report.py --lab-hf DIR` reads a **copy** of `strategy_lab_hf/`.
  - Journal closes are counted as Lab trades, deduplicated by (book, cfg, seq). Archived
    resets are skipped.
  - The report adds a `high_frequency` section: each hypothesis against the same-period
    control, with a pair-bootstrap CI and **always the within-fee-bucket gap**.
- `scripts/evaluate_paper_lab.py --hf-dir DIR` measures each (book, config hash) with
  `paper_lab_metrics.measure_hf_rows`. It reports multi-slot orders per hour, closes per
  hour, the most slots held at once, cancels by reason, close kinds, cap trips and
  retirements.
- Rescore daily from the journal against `syn_lib`
  (`research/hf_study_2026_10_09/hf_synthesis/`). Evaluate the gap with the pair CI at each
  checkpoint.

## Evidence ($25, 3 slots; holdout = last 17.2 h, all 16 hours live; train = first 25.8 h)

| Book | Trades/h, holdout (hourly p10 / median / max) | Trades/h, train (all hours / live hours) |
|---|---|---|
| HF_RND_E95 | 47.9 (45 / 49 / 50) | 34.3 / 45.3 |
| HF_QUIET_E95 | 44.6 (37 / 47 / 50) | 31.5 / 41.6 |
| HF_DIP15_E95 | 40.5 (34 / 43 / 50) | 32.0 / 42.3 |

| Book | net50/trade (pair CI95) | Booked/trade | net0/trade | $/trade net50 / booked | $/h net50 / booked |
|---|---|---|---|---|---|
| HF_RND_E95 | -3.71% (-3.86 to -3.49) | -2.74% | -1.58% | -0.93 / -0.69 | -44.4 / -32.8 |
| HF_QUIET_E95 | -3.57% (-3.74 to -3.43) | -2.60% | -1.38% | -0.89 / -0.65 | -39.8 / -29.0 |
| HF_DIP15_E95 | -3.63% (-3.83 to -3.41) | -2.67% | -1.48% | -0.91 / -0.67 | -36.8 / -27.0 |

| Book | Win rate net50 | Pairs | Top-pair share | Uncapped booked drawdown, 17 h | Gap vs control (CI) | Within fee buckets |
|---|---|---|---|---|---|---|
| HF_RND_E95 | 1.5% | 20 | 0.115 | $565 | n/a | n/a |
| HF_QUIET_E95 | 0.0% | 18 | 0.141 | $498 | +0.14 pp (-0.13 to +0.36) | -0.06 / +0.04 |
| HF_DIP15_E95 | 1.0% | 20 | 0.103 | $464 | +0.07 pp (-0.21 to +0.33) | ~0 |

- The control's five salts lost between -3.64% and -3.77% per trade. On train the books
  lost -3.90%, -3.75% and -3.80% net50 per trade.
- With the $100 cap the books make 137-159 closes and trade 2.8-3.5 hours a day. Each loses
  $100 booked and about $137 net50 per day, and the capped drawdown is about $203 over two
  UTC days.
- With POOL_LOSS_MEMORY_V1 enforced the books trade 3.5-4.0 times an hour.
- `syn_lib.py` reproduces `hf_signals/hfsim.py` exactly (1,814 trades, 0 mismatches). The
  Lab container reproduces `syn_lib` on real observations: every order, both fill times and
  the booked and net0 result of 41 trades in the parity fixture.

**Honest expectation.** Each book loses about 3.6% net50 per trade, about $0.90 at $25, and
roughly $37-44 per hour while it trades. The cap stops that at about $100 booked (about $137
net50) per book per UTC day. With about 500 closes per book, the CI half-width of the gap is
about 0.3 pp, so a quiet or dip gap of +0.1 to +0.2 pp will most likely stay unresolved, and
the hypotheses are expected to retire at their first checkpoint, about day 4. Nothing here
can become profitable by trading more often: more trades only realize the cost floor
faster.

## Rollout and verification

1. Deploy all three books together (one release, the lock check passing).
2. On main's `GET /state` (read-only), check:
   - `strategy_lab.activity_config.lab_high_frequency.config_hashes` equals the table above
     and the deployed `strategy-lock.json`;
   - `cost_model_mismatches` is empty and `running` is true;
   - `strategy_lab.persistence.hf.refresh.universe` (the HF universe count per refresh) is
     above 0 once the feed runs.
3. After the 15-minute heat warm-up, and while a book's session is open and its cap is not
   tripped, expect 40-50 orders an hour per book (`persistence.hf.orders_last_60m`).
4. Check that `persistence.hf.journal.last_flush_ms` and `checkpoint.last_ms` stay small,
   and that `persistence.hf.price_audit.checks_last_60s` is at most 2.
5. Rescore daily from a copy of the journal (`paper_edge_report.py --lab-hf`), and evaluate
   the gap at each kill checkpoint.

To disable the family, set `NEO_LAB_HF_ENABLED=0` and restart the Lab. The journal and the
checkpoint stay on disk, and the next enabled start resumes from them.

## Changing the test

- A strategy parameter change is a new config hash: a new evidence sample in the same books.
  The aggregates are kept per hash.
- A test with new funding or new rules that must not share a ledger needs new, versioned
  book ids. Never edit the journal.
