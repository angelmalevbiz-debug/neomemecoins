# ORDER_FLOW_ADAPTIVE — October 4, 2026 decision policy, restored as an opt-in profile

Version `ORDER_FLOW_ADAPTIVE_OCT4_RESTORE_V1`. PAPER only. Nothing in this change
submits swaps, resets a ledger, or promises a trade rate or a profit.

## What this is

The owner asked for the historical October 4, 2026 PAPER behaviour of the
personal account (strategy `ORDER_FLOW_ADAPTIVE`, learning mode
`ADAPTIVE_CONTEXT_HOLD`, entry policy `ORDER_FLOW_BALANCED_V4`) to run again on
the current engine while keeping every later correctness fix.

Source of truth for the historical logic: legacy repository
`angelmalev9-creator/neo-meme-trade`, commit `5b78efd` (2026-10-04 23:25 +03:00),
engine-identical through `28004bf`. `d745eb6` (00:23 +03:00 on October 5) was the
first commit to abandon the adaptive hold ladder and is therefore excluded. The
reconstruction was re-derived line by line from `git show 5b78efd:<path>` by an
independent check; every number below was confirmed against that snapshot.

The decision policy now lives in `backend/order_flow_adaptive_oct4.py` and is
selected per engine process with `NEO_SIGNAL_STRATEGY=ORDER_FLOW_ADAPTIVE`. The
default strategy (`WINNER_ENSEMBLE_PAPER_V1` / `WINNER_ENSEMBLE_VERIFIED_ENTRY_V4`)
is unchanged for every engine that does not opt in: same thresholds, cost caps,
exits, sizing and ledgers. The strategy lock `production_strategy_id` stays
`WINNER_ENSEMBLE_PAPER_V1`.

## Restored decision policy (strategy-owned values)

| Setting | Value | Owner |
| --- | --- | --- |
| scan / position scan | 15 s / 2 s | profile |
| max positions | 1 | profile |
| trade notional | $200 flat, one quote per candidate (`FLAT_NOTIONAL_NO_BACKOFF`) | profile |
| daily PAPER loss limit | $100 | profile |
| net stop | −5 % net executable (same as engine constant) | profile = engine |
| same-token re-entry cooldown | 20 min | engine constant (1200 s) |
| strict entry score / conviction / liquidity | 85 / 72 / $30,000 | profile |
| max entry impact / round-trip cost / worst-case cost | 2.0 % / 2.75 % / 4.50 % | profile |
| feed age / quote age / quoted candidates per scan | 30 s / 10 s / 2 | profile |
| entry decision flow window | 60 s exact-pool (conviction uses 30 s and 300 s) | profile |
| inert legacy constants | TP 18 %, trailing 4 %, max hold 7 min — published, never used | profile |

No environment variable changes a profile value. `/state.config.config_ownership`
names the owner of every effective key for both strategies, so the
systemd/env-versus-code drift found in the audit cannot recur silently.

### Entry filter `ORDER_FLOW_BALANCED_V4` (all must pass, rejection names are historical)

`invalid_pair`, `invalid_price`, `stale_feed` (> 30 s), `score` (< 85), `liquidity`
(< $30k), `momentum` (5 m change outside −3 … +25 %), `hour_trend` (1 h outside
−30 … +150 %), `market_buyers` (5 m buys/sells < 1.0), `liquidity_ratio`
(liquidity / market cap < 0.03), `flow_quality` (60 s exact-pool window not
COMPLETE — modern fail-closed addition), `flow_count` (< 4 verified swaps),
`flow_ratio` (buy/sell USD < 1.30), `buy_volume` (< $150), `wallet_count` (< 4),
`buyer_count` (< 3), `wallet_ratio` (buyers/sellers < 1.0), `large_sells`
(max sell ≥ max($250, buy USD × 0.5)), `conviction` (< 72).

Rejection examples now carry the observed values (score, liquidity, flow counts,
conviction, hold mode), so the dashboard can say which check failed.

### Conviction model and hold modes

`market_context()` starts at 50 and applies the October 4 table (fast 30 s flow,
slow 300 s flow, unique wallets, repeat buyers, whale flow, 5 m momentum, 1 h
trend, market buy/sell ratio, liquidity versus entry, NEO score), clamped to
0 … 100. The table was moved verbatim into `order_flow_adaptive_oct4.conviction_score`
and is unit-tested row by row; the ensemble still uses the same function.

| Mode | Conviction | Max hold | Fixed target | Trail arm | Trail |
| --- | --- | --- | --- | --- | --- |
| RUNNER | ≥ 85 | 60 min | none | +15 % | 7 % |
| STRONG | ≥ 72 | 30 min | none | +12 % | 6 % |
| NORMAL | ≥ 58 | 15 min | +20 % | +9 % | 5 % |
| CAUTIOUS | ≥ 45 | 8 min | +14 % | +7 % | 4 % |
| WEAK | < 45 | 4 min | +8 % | +5 % | 3 % |

### Exit ladder (evaluated on the net executable mark, in this order)

1. `STOP_LOSS_NET_TARGET` at net ≤ −5 % (never clamped; gap losses stay real)
2. `CONVICTION_EXIT` — conviction < 35 and net < 0
3. `ORDERFLOW_EXIT` — fast window trades ≥ 4, sells ≥ 3, sell USD ≥ max($200, 2 × buy USD), net < +3 %
4. `ADAPTIVE_TP_20 / _14 / _8` — only modes with a fixed target
5. `CONVICTION_PROFIT_LOCK` — peak ≥ +10 %, conviction < 50, net > +2 %
6. `ADAPTIVE_TRAILING` — after the mode's arm, drawdown from the peak beyond the mode's trail
7. `ADAPTIVE_MAX_HOLD` — mode max hold reached and conviction < 72
8. `ABSOLUTE_MAX_HOLD` — 120 minutes

When the held pool has no COMPLETE 30 s exact-pool window (the shared tape follows
a bounded set of pools), missing flow is not treated as selling: the position keeps
its entry hold mode, `CONVICTION_EXIT`, `ORDERFLOW_EXIT` and `CONVICTION_PROFIT_LOCK`
are suspended, and the mode's max hold still applies (`flow_context_unavailable`
is recorded on the position).

This is `engine_exit_policy.exit_reason(policy='adaptive')`
(`GOLD_ADAPTIVE_NET_CANDIDATE_V1`), already present in the engine and now
stamped on every position the profile opens. The modern safety exits
`LIQUIDITY_EMERGENCY`, `STALE_MARKET_EXIT` and `EXIT_IMPACT_EMERGENCY` remain in
front of the ladder, exactly as for the default strategy.

## What is deliberately not restored

- the chart-price stop trigger with its −4.5 % re-quote cancel (exits are net only)
- the −5 % loss clamp and synthetic fills from chart prices
- the unpinned 60 s flow read: decisions use the exact pool, and the confirmed
  30 s `CONFIRMED_PUMPSWAP_WINDOW` evidence gate (`promoted_entry_guard`) still
  runs first for every strategy
- any history, balance, session or `trade_seq` reset; a strategy switch keeps
  the account ledger and continues the sequence
- smaller-size quote retries (the profile quotes the flat $200 once); the
  modern 15 s per-token quote-retry cooldown still applies afterwards
- quoting on a feed older than 6 s: the modern commit gate rejects signals older
  than 8 s, so with the 15 s feed cadence a candidate is quoted only in the first
  seconds after a scan and is otherwise reported as `stale_signal` without a quote

Retained from the modern engine: exact mint/pool identity, rug guard, independent
price confirmation with Jupiter tie-break, quote evidence `QUOTE_EVIDENCE_V9`,
`RUNTIME_DURABLE_PAPER_V2` atomic ledgers and audit outbox, daily/drawdown/exposure
caps, per-user isolation and stable engine ports.

## Cost reality the owner must know

The 2.75 % round-trip cap is the October 4 value and is strategy-owned; the
default engine keeps 1.5 % / 2.5 %. With a −5 % net stop that leaves 2.25 % of
adverse move before the stop. Break-even needs a gross move of about +2.75 %;
the NORMAL target (+20 % net) needs about +23 % gross; a CAUTIOUS target (+14 %)
about +17 %.

PumpSwap fee tiers set the floor: a 125 bps pool costs 2.48 % round trip before
impact and network fees, so the youngest tiers cannot pass even this cap; pools
that meet the $30k liquidity and liquidity/market-cap ≥ 0.03 screen typically sit
in lower tiers. The live tape tracks `NEO_TAPE_MAX_PAIRS` (4) pairs, so at most
four feed coins can ever satisfy `flow_quality`/`flow_count` per cycle; expect
`flow_quality` and `flow_count` to dominate the rejection histogram until a
candidate is among the tracked pools. Those rejections are reported, not hidden.

## Enabling it for one account (owner action; nothing here is automatic)

1. Deploy the reviewed source to the checkout that runs the gateway.
2. Wait until every engine is flat: `GET /state` on the main engine (8878) and on
   each personal engine port must show no open positions. Exits are not managed
   while services are stopped, and the adaptive profile checks positions every
   2 s instead of 0.5 s.
3. Stop the services from the runtime checkout and require a clean exit:

   ```powershell
   .\scripts\start_local_paper.ps1 -Action Stop
   ```

   Then confirm that no `python.exe` whose command line contains the runtime
   checkout path remains (venv and system Python, including personal engines and
   training workers) and that nothing listens on 8878, 8879 or the personal ports.
4. Record the choice in the gateway registry while everything is stopped (touches
   only that field):

   ```powershell
   .venv\Scripts\python.exe scripts\paper_runtime.py set-account-strategy --registry C:\Users\Chavd\neomemecoins\.runtime\accounts\user_accounts.json --user <account-uuid-prefix> --strategy ORDER_FLOW_ADAPTIVE
   ```

   Use the registry of the checkout that runs the gateway (the output prints the
   resolved path). `--strategy default` removes the choice. Short (< 8 characters),
   ambiguous prefixes and unknown strategy names refuse to change anything. The
   gateway also merges this field from disk before every registry write, so an
   edit made while it runs is not reverted at shutdown.
5. Start the services:

   ```powershell
   .\scripts\start_local_paper.ps1 -Action Start
   ```

   The gateway restarts the known personal engines on their registered ports with
   their registry strategies. Each engine holds an exclusive lock on its ledger, so
   a second engine for the same account refuses to start, and a shutdown never
   writes a ledger it did not load. Do not issue another Stop until every engine
   answers `/health`.
6. Verify on the account engine port (`/state`) and through the authenticated
   dashboard (`/user/state`): `config.signal_strategy = ORDER_FLOW_ADAPTIVE`,
   `entry_policy_version = ORDER_FLOW_BALANCED_V4`, `exit_policy = adaptive`,
   `exit_policy_version = GOLD_ADAPTIVE_NET_CANDIDATE_V1`, `scan_seconds = 15`,
   `max_positions = 1`, `trade_notional_usd = 200`, `max_daily_loss_usd = 100`,
   `strict_max_roundtrip_cost_pct = 2.75`, `same_token_cooldown_seconds = 1200`,
   `paper_only = true`, `strategy_profile.restore_version = ORDER_FLOW_ADAPTIVE_OCT4_RESTORE_V1`,
   `effective_config_hash` changed, and `account_scope.strategy_matches_request = true`.
   Compare `trade_seq`, history length, `demo_balance_usd` and `demo_session_id`
   in each `state.json` with the values before the stop, and check that the shared
   main engine (8878) and the other account still report `WINNER_ENSEMBLE_PAPER_V1`
   with their previous `effective_config_hash`.

`account_scope.strategy_matches_request = false` means the registry asks for a
strategy the running engine was not started with — restart, do not guess.

## Regression coverage

`tests/test_order_flow_adaptive_oct4.py` covers the owner's list: valid entry
(1), score 84 (2), liquidity < $30k (3), conviction 71 (4), flow ratio 1.29 (5),
three wallets (6), large sells (7), the five hold modes (8–12), stop priority
(13, plus an uncapped engine stop), RUNNER/STRONG open at +18 % (14, plus the
engine path), ORDERFLOW_EXIT (15), CONVICTION_EXIT (16, plus the engine path),
trailing (17), adaptive max hold (18), 120-minute absolute hold (19), 20-minute
cooldown (20), one position (21), $100 daily limit (22), history/balance/sequence
survive activation (23–24), wrong-pool observation cannot mark the position (25),
stale quote cannot enter (26), missing safety or price evidence fails closed
(27). `tests/test_gateway_account_strategy.py` covers account isolation (28):
the registry choice reaches only that account's engine, a gateway-wide variable
never leaks, unknown values start nothing, operator edits are adopted before a
spawn, and `/user/state` reports requested versus running strategy. Existing
suites prove the default strategy is byte-for-byte unchanged in behaviour.
