# Tape decoder shadow path and seat shedding

Status: engineering repair of observation capacity. No entry, exit, cost,
risk or ledger value changes. No profitability, win-rate or trade-frequency
claim is made or implied; more decoded observations are not evidence of edge.

## What was measured

A read-only analysis of a scratch copy of the PAPER tape journal (25.7 h,
last 3 h detailed, `research/analyst-funnel.md`) found:

| Fact | Value |
| --- | --- |
| Fetched bodies journaled `unclassified` | 34.6% (18,360 of 53,188 signatures, 3 h) |
| Dominant reason | `BASE_DECIMALS_OR_MINT_MISSING` 16,053 (87% of unclassified) |
| Pools with >= 40 bodies and zero decoded swaps | 26 of 117 (3 h); 33 of 138 with >= 20 signatures |
| Example | SHITCOIN pool `4fjeL4FP…`: 4,898 bodies, 4,898 `BASE_DECIMALS_OR_MINT_MISSING`, 0 events |
| Decoded events by orientation (whole journal) | TRACKED_BASE 18,047; TRACKED_QUOTE 7,167 |
| `BASE_DECIMALS_OR_MINT_MISSING` in pools already known as TRACKED_QUOTE | 4,270 of 16,053 |

The journal stores no transaction bodies, so four public `getTransaction`
bodies were fetched read-only on 2026-10-08 from the recorder's default RPC
host for signatures taken from that copy (no live NEO port was touched). They
are committed as `tests/fixtures/tape_real_bodies_reversed_wsol.json`:

| Body | Pool orientation | Journal result before this change | Finding |
| --- | --- | --- | --- |
| TWEETCRAFT, one SELL instruction | reversed (base WSOL, quote token) | `BASE_DECIMALS_OR_MINT_MISSING` | the user's wrapped SOL base account is created, synced and closed inside the transaction, so it has no `preTokenBalances`/`postTokenBalances` entry |
| SHITCOIN, SELL + BUY by two wallets | reversed | `BASE_DECIMALS_OR_MINT_MISSING` | same closed wrapped account pattern for both users |
| a reversed pool decoded by the default path | reversed | processed (TRACKED_QUOTE) | the wrapped SOL account kept balances, so the strict check passed |
| CLAUDIA, one SELL instruction | normal (base token, quote WSOL) | processed (TRACKED_BASE) | the user's wrapped SOL *quote* account is closed in the transaction and the default path tolerates the missing quote balance |

Since PR #7 (`78b2593`) the reversed branch required a balance entry for the
user's base (cash) account (`if reversed_pool and cash_info!=…`), while the
normal branch keeps the quote (cash) balance optional (`if cash_info and …`).
The asymmetry, not the orientation logic, produced the regression.

## Exactness proof

For every event in the fetched bodies, independently of the recorder:

- the event base leg (offset 16) equals the pool base vault balance delta
  (TWEETCRAFT: 252,034,887 lamports; SHITCOIN: 8,658,793,036 sold in minus
  6,187,764,270 bought out equals the vault delta 2,471,028,766);
- the event user quote leg (offset 112) equals the user's token account
  balance delta (TWEETCRAFT: 124,080,513,683; both SHITCOIN users exactly).

The cash mint is the instruction's base mint, already restricted to USDC/WSOL
before any event is read, and its decimals are fixed constants (9/6). No
scanner price, no stable-asset assumption and no balance delta is used for
quantities; the Pump event remains the only source. The same facts hold for
the default path body (base leg equals the vault delta).

## What changed in `backend/live_tape.py`

- Reversed orientation with a missing cash-leg balance entry is decoded only
  when a parsed `spl-token` `closeAccount` instruction in the same transaction
  targets that user base account. Any other missing or mismatching metadata
  still fails closed with the existing reason names.
- Every event now carries `decoder_version` and `decoder_validated`:
  - default path `PUMP_SWAP_EVENT_DECODER_V1` (validated),
  - shadow path `PUMP_SWAP_REVERSED_CASH_LEG_EVENT_V2` (not validated).
- A not-validated version adds the quality flag `DECODER_PATH_UNVALIDATED`,
  the transaction is journaled `unclassified` with that reason, the pool's
  coverage stays `DEGRADED` (`UNCLASSIFIED_TRANSACTIONS`), and
  `market_monitor.live_flow` / `strategy_lab.flow_map` exclude the event and
  report DEGRADED flow through their existing quality-flag handling. No
  engine file changed.
- Events carrying `DECODER_PATH_UNVALIDATED` are journaled in a separate
  `shadow_events` table (same columns as `events`, created by the recorder's
  existing `CREATE TABLE IF NOT EXISTS` schema path on start; existing rows
  are untouched). They are never projected into `live_tape_status.events`
  and never counted against `NEO_TAPE_MAX_EVENTS` (1600) in the 5-minute
  projection window: measured on the journal copy, 3 of 37 five-minute
  windows would otherwise exceed 1600, and truncation marks every tracked
  pool `DEGRADED` (`UI_WINDOW_TRUNCATED`), which blocks all PAPER entries.
  The snapshot publishes `shadow_events_window` (count only). Signature
  state/reason and the in-memory yield counters are unchanged by the table
  split.
- `NEO_TAPE_VALIDATED_DECODER_VERSIONS` (comma-separated) lists additional
  validated versions. Default: only V1. `live_tape_status.decoder` publishes
  the default, shadow and validated versions.
- `cash_leg_balance_metadata` on each event records `PRESENT`,
  `WRAPPED_ACCOUNT_CLOSED_IN_TRANSACTION` (shadow path) or `ABSENT` (default
  path tolerance on the quote side).

Already journaled signatures keep their terminal classification; nothing is
re-decoded or rewritten.

## Seat shedding (`backend/tape_pool_scheduler.py`, policy `STABLE_COST_AWARE_TAPE_DISCOVERY_V3_YIELD_SHED`)

The recorder keeps an in-memory per-pool count of classified bodies and the
swaps they yielded (`TapeRecorder.decode_yield`; window
`NEO_TAPE_YIELD_WINDOW_MS`, default 2 h; reset on restart; no journal query).
It publishes two counts:

- `decoded_swaps`: events whose quality flags are a subset of
  {`QUOTE_USD_UNKNOWN`, `QUOTE_ASSET_USD_REFERENCE_ESTIMATE`}. The swap legs
  are exact; only the SOL/USD valuation is missing or estimated.
- `usable_swaps`: events without any quality flag (what the engine and the
  Lab accept as flow).

A supported entry candidate whose bodies reached `NEO_TAPE_SHED_MIN_BODIES`
(default 40) with zero decoded swaps is excluded from entry and exploration
seats and its lease is released for `NEO_TAPE_SHED_COOLDOWN_MS` (default
30 min). Shedding on zero *usable* swaps would, during a Jupiter SOL/USD
reference outage (every WSOL event flagged `QUOTE_USD_UNKNOWN` or
`QUOTE_ASSET_USD_REFERENCE_ESTIMATE`; 13% of journal events, one ~50-minute
stretch at ~60%), have shed every entry candidate for 30 minutes. After the
cooldown the pool is re-evaluated from bodies fetched after `retry_at` only;
the retry floor is kept even when the pool leaves the feed, until it is older
than the yield window, so a pool returning after its cooldown is not shed
again on the old evidence. Pinned exit pools are never shed. Shadow-path
events are neither decoded nor usable, so a pool that decodes only through
the shadow path is shed like any other until the version is validated; its
`shadow_swaps` count is published.

`TapeRecorder.poll` restricts the fresh pending-body queue (and discovery's
reservation for it) to pools in the current selection, so pending signatures
of shed or de-selected pools no longer spend the per-poll body budget. They
stay pending in the journal and age into the existing small historical
maintenance budget.

`live_tape_status.entry_scheduling` now publishes `shed_policy`,
`shed_pool_count`, `shed_pools_in_feed` and `shed_pools` (symbol, mint, pool,
reason `ZERO_DECODED_SWAPS_AFTER_BODIES`, bodies, decoded/usable/shadow swaps,
first and last body time, `shed_at`, `retry_at`).

## Cost-first seats and personal-engine pins (policy `STABLE_COST_AWARE_TAPE_DISCOVERY_V4_COST_FIRST_PINS`)

Status: observation capacity only. No entry, exit, cost, risk, size or
ledger rule changes; the tape still never decides whether to trade. The
seat-shedding rules above are unchanged under the V4 policy name.

Why:

- The tape follows at most `NEO_TAPE_MAX_PAIRS` pools (4 in the local
  runtime) and an entry needs a COMPLETE 30 s exact-pool window, so a pool
  without a seat can never pass the flow gate. The COST_FIRST Lab book pair
  (PR #16) found that its universe pools match neither the main nor the funded
  candidate screens and only reached the tape through exploration seats, while
  78% of the main screen's passers are fee-infeasible (research
  `analyst-funnel.md`) and cannot pass the quote gate anyway.
- `_held_coins` pinned only positions from the main engine's `/state` and Lab
  book positions. Positions of the gateway-spawned personal engines were never
  pinned, so their exits could lose exact-pool flow coverage.

What changed (`backend/tape_pool_scheduler.py`, `backend/live_tape.py`):

1. Seat groups, best first: estimated feasible main/funded candidate (V3 group
   0, unchanged) > **COST_FIRST universe pool**
   (`cost_first_established.candidate`, the Lab book pair's own definition:
   PumpSwap, SOL quote, fee tier <= 50 bps, liquidity >= $250k, modeled fee +
   impact <= 1.2% at the planning notional) > matched candidate whose modeled
   round trip exceeds the cost cap or is unknown (V3 group 1) > other
   exploration (V3 group 2). The planning notional is the Lab entry cap
   (`NEO_LAB_TRADE_NOTIONAL`, default $150, the same variable and default as
   `strategy_lab.TRADE_NOTIONAL`) with the Lab minimum notional. Within a
   group the existing V3 ordering (fair rotation, then the cost-aware activity
   priority) applies. Only group 0 pre-empts a running 60 s lease, exactly as
   before; a cost-first pool takes the next freed seat and is retained before
   lower groups when seats shrink. The seat budget
   (`NEO_TAPE_MAX_PAIRS` minus pins) and the per-poll body budget are
   unchanged. A cost-first pool can still be shed for zero decoded swaps.
2. Open positions of every personal PAPER engine are pinned by exact
   (mint, pool), after main's and Lab's pins, with duplicates taking one seat.
   The recorder reads the account registry `NEO_USER_STATE_PATH` read-only
   (missing: no personal pins; unreadable: the file is skipped and the last
   good port list, if any, stays in use) and, for each listed `engine_port`
   (1024-65535, deduplicated, at most `NEO_TAPE_PERSONAL_ENGINE_LIMIT`, default
   16), GETs `http://127.0.0.1:<port>/state` with a
   `NEO_TAPE_PERSONAL_STATE_TIMEOUT_SECONDS` timeout (default 0.75 s, capped
   at 2 s). Each port and the registry are read at most once per
   `NEO_TAPE_PERSONAL_STATE_CACHE_MS` (default 5 s), including after a
   failure. A failed engine keeps its last successful positions for
   `NEO_TAPE_PERSONAL_STATE_RETAIN_MS` (default 30 s), then contributes
   nothing. Pins still take precedence over entry seats and are never shed.

Published in `live_tape_status.entry_scheduling` (no account identifiers):
`selected_cost_first_pools`, `unselected_cost_first_pools`, a `cost_first`
block (universe version, planning notional, candidate/selected/unselected
counts, members also estimated feasible, rejection reasons, up to 6 examples
with fee tier, liquidity and modeled fee + impact, `is_entry_authorization:
false`), `pinned_personal_pools`, `pinned_personal_only_pools` and
`personal_engines` (registry status, engines listed/reachable/retained/
unavailable/over limit, fetches this poll, open positions, cache, timeout).

Proof that nothing else moved: on 300 random seeds x 40 polls without a
cost-first pool or personal position the V4 scheduler returns the same pools,
order, leases, shed records and V3 report fields as V3; the default
`WINNER_ENSEMBLE_PAPER_V1` and `ORDER_FLOW_ADAPTIVE` `effective_config_hash`
values are identical before and after (no engine file changed). Tests:
`tests/test_tape_cost_first_seats_and_personal_pins.py`.

How it will be evaluated: over at least 24 h after deployment, compare with
the preceding 24 h (same tape budget): the share of polls in which at least
one cost-first pool has a seat, the share of cost-first pools reaching
`COMPLETE` coverage, the COST_FIRST Lab books' `cost_first_universe` versus
flow-confirmed candidate counts, and, for every personal-engine close, whether
its pool was `COMPLETE` through the hold. Seat counts and coverage are not
evidence of edge; the COST_FIRST books are still judged only under
`docs/STRATEGY_VALIDATION.md` on future, untouched observations.

Limits: cost-first seats displace exploration of over-budget matched pools
when seats are scarce; a personal engine slower than the timeout for longer
than the retention period loses its pins until it answers again; each personal
`/state` read transfers that engine's full snapshot (bounded by the cache
period).

## How it will be evaluated (24 h shadow comparison, before any validation)

1. Count shadow events per pool and per hour from the journal's
   `shadow_events` table (every row there is a
   `PUMP_SWAP_REVERSED_CASH_LEG_EVENT_V2` event; V1 events stay in `events`)
   against the pool's DexScreener
   `txns.m5`/`volume` recorded in `training/observations.jsonl`; the decoded
   share must rise for reversed pools without creating events for pools whose
   DexScreener counts are zero.
2. For reversed pools with both V1 events (`events`) and V2 events
   (`shadow_events`), compare the distributions of `quote_amount` and
   `token_amount` per direction; a systematic difference would indicate a
   leg or direction error.
3. Re-run the body cross-check (event base leg against pool vault delta, user
   quote leg against the user's token account delta) on a fresh sample of V2
   signatures.
4. Confirm `entry_scheduling.shed_pools` lists only pools with zero decoded
   swaps and that seats were reassigned to pools that later reached
   `COMPLETE` coverage.
5. Only then may the owner set
   `NEO_TAPE_VALIDATED_DECODER_VERSIONS=PUMP_SWAP_REVERSED_CASH_LEG_EVENT_V2`
   for the recorder; this is a configuration change for the tape service and
   does not alter any strategy threshold.

## Still unproven

- Whether the shedded pools' RPC bodies are reassigned to pools that become
  admissible depends on the feed; this is a capacity change, not an admission
  change, and may not increase trades.
- The two fetched reversed bodies both used WSOL; a USDC-base reversed pool
  is covered by synthetic fixtures only.
- Fee evidence for the fetched bodies was rejected by the strict fee
  reconciler (unchanged behaviour); no fee rate is inferred from them.
