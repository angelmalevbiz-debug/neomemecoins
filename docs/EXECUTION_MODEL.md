# PAPER execution and market-data model

This document describes the repaired implementation, rather than a claim about
profitability. The primary engine, Astra, the parallel teaching accounts and
recorded replay are separate accounts and execution paths. Their balances, completed
positions and denominators must not be combined.

## Execution boundary and provenance

The supported operating modes are PAPER, REPLAY and SHADOW. The quote adapters
only call read-only quote/account methods. They do not build, sign, submit or
confirm transactions. A provider reporting a context slot is not evidence that
the hypothetical trade landed, and `confirmed` account reads are not settlement
of a simulated order.

| Evidence | Meaning | What it does not prove |
| --- | --- | --- |
| Scanner price, `priceUsd`, `updatedAt` | Observed market reference for the identified mint/pool | An executable sale for the account's exact quantity |
| Confirmed external PumpSwap event | A successful market participant swap with decoded base and quote legs | A fill of this bot's account, or an independent wallet cluster |
| Jupiter quote, raw `inAmount`/`outAmount` | Read-only expected route output for an exact raw input amount | A signed order, liquidity at future execution time, or guaranteed proceeds |
| `is_simulated_fill`, `simulated_fill_at` | A result booked by a documented PAPER model | On-chain execution |
| Native reserve estimate | A modeled token-to-SOL result from verified account data | Realized USDC without a validated conversion route |

The current primary quote model is `QUOTE_LATENCY_BUFFER_V6`, with transport
adapter `jupiter-swap-v1-quote/2`. Some compatibility fields retain older labels,
including `JUPITER_QUOTE_V2` and
`JUPITER_QUOTE_EXPECTED_WITH_BUFFER_V5`; those labels identify historical schema
and accounting paths, not Jupiter's HTTP API version. `instructionVersion=V2`
is an instruction-version request on the V1 quote endpoint.

## Primary account: units, cash and net result

The primary PAPER account is denominated in USD/USDC. USDC quantities have six
decimal places; token raw quantities use the decimals obtained from validated
mint evidence. Mint and pool public keys are case-sensitive. Boolean and
fractional raw input quantities are rejected by the quote API facade.

For a notional `N`, entry requests `floor(N * 1_000_000)` raw USDC. The recorded
paper token amount is the integer quote output multiplied by `9990 / 10000`,
rounded down. The 10 bps reduction is an explicit adverse execution assumption.
The provider expected amount, acceptance threshold and recorded paper quantity
remain separate fields. Liquidation uses the recorded amount, not the larger
expected entry quantity.

The primary ledger uses these relationships:

```text
committed capital = entry notional + entry network estimate + token-account reserve
available capital = realized account balance - outstanding committed capital
exit net proceeds = buffered quoted USDC output - exit network estimate
position net PnL = exit net proceeds - allocated entry committed capital
equity = realized account balance + marked unrealized net PnL
```

Opening reserves committed capital; closing changes the realized balance by net
PnL. This is equivalent to subtracting committed cash on opening and returning
net proceeds on closing, provided reservations and entry costs are allocated
consistently. Partial exits allocate entry notional, network estimate and account
reserve to the disposed fraction. Their PnL is aggregated into one completed
position, rather than counting each partial leg as a separate win.

The token-account reserve is an explicit capital/cost assumption. No refundable
rent credit or SOL conversion is invented. The historical first-seen-mint
heuristic is not proof that a real associated token account exists or that rent
was paid. Main-account network cost is a configured SOL estimate converted using
the available SOL/USD reference, with a minimum budget; it is not an observed
network fee, priority bid or transaction landing measurement.

`gross_proceeds_usd` in quote-backed marks means buffered output **after the
provider's AMM/platform fees and price impact**, but before the separately
modeled network cost. It does not mean pre-AMM-fee proceeds. The explicit
`dex_fee_usd=0` field means those costs are already included, not that the route
is fee-free. Do not subtract a scanner fee or price-impact percentage a second
time. Jupiter documents `outAmount` as including AMM and platform fees, while
excluding slippage. [Jupiter V1 quote contract](https://developers.jup.ag/docs/api-reference/swap/v1/quote)

## Stops and quote bounds

Planned stop distance, exit signal, planned risk budget and modeled sale are
distinct. The old chart formula collapsed to a -0.5% trigger; the active repaired
exit policy evaluates net executable marks. The original `stop_signal_trigger_pct`
may remain as a historical diagnostic, but it does not clamp proceeds.

There is no PAPER loss cap rewriting a quote to exactly -5%. With $200 committed,
$160 net proceeds and no other costs, the recorded result is -$40. A price gap,
large impact, unavailable route or exhausted liquidity can exceed planned stop
risk, including loss of the full committed amount.

`otherAmountThreshold` is a quote's acceptance threshold. Slippage tolerance is
not an automatically charged fee, and the threshold is not guaranteed future
liquidation value. Historical `worst_case_*` names are compatibility fields for
this bound; they must not be interpreted as guaranteed worst-case loss.

Entry preflight requests an initial buy, a sell preview for its recorded amount,
then a new final buy. Only the final buy can determine booked entry quantity.
Preflight verifies bounded quantity drift (0.5%), final freshness (750 ms),
sale/final ordering (at most 4 seconds) and, when available, bounded monotonic
context-slot drift (25 slots). A positive preview above the initial notional by
more than 0.1% is rejected as an inconsistent cost observation. Proportional
preview adjustment is only a screening estimate; actual later liquidation still
requires the held exact raw quantity.

## Shared budget, timing and failures

`honest_quote_transport.py` provides the shared process/file lock, request stamp,
short cache and exit queue. The legacy quote facade, current primary engine,
Astra client and tape FX-reference reader use this transport. Astra's
`NEO_ASTRA_DATA_DIR` selects its account files, and does not create another quote
budget. The shared paths are configured by `NEO_JUPITER_LOCK_PATH` and
`NEO_JUPITER_STAMP_PATH`; clients running as separate processes must keep those
settings consistent. Every experiment does not receive a fresh provider quota.

Astra preserves its separate `ASTRA_QUOTE_PAPER_V1` account model: 50 bps
slippage tolerance, 10 bps assumed friction per leg, an 8-second quote-age limit
and its existing rent/network reservations. The primary model's 250 ms modeled
landing delay and three-quote final-buy revalidation are not implemented in
Astra. Its preflight sales use entry priority; held-position sale/mark requests
use exit priority and require reception at or after that sale request. Cached
observations retain their original `_received_at`/`_at`, including the SOL fee
conversion reference, so cache reuse cannot make old evidence newly available.
Astra reports transport failure codes in `last_quote_error` and counts failures
separately. Its `quote_requests` counts attempted HTTP exchanges; a cache hit is
not another API request or another independent market observation.

| Default | Purpose |
| --- | --- |
| 2.10 seconds between HTTP requests | Existing conservative shared keyless budget |
| 700 ms identical-request cache | Reuse read-only observations without a new HTTP request |
| 1,500 ms primary mark cache | Bounded observational mark reuse |
| 2,000 ms primary quote acceptance age | Reject stale/future received quotes |
| 250 ms modeled landing delay | Declared PAPER assumption, followed by another freshness check |
| 8,000 ms signal age at commit | Reject an old signal even if the final quote is recent |
| 3.0 seconds exit / 4.5 seconds other queue deadline | Bound waiting for the shared request budget |
| 1.0-second connect / 2.5-second read timeout | Bound HTTP transport waiting |

Queue deadlines do not guarantee an overall execution deadline; an already
started HTTP request can extend beyond the queue wait. Exit requests take
priority over queued entries and the recorder's FX reference request, but an
in-flight request cannot be preempted. Do not infer a paid quota from the mere
presence of an API key. Jupiter's published limit is per organisation, and most
methods share its main sliding-window bucket. [Jupiter rate limits](https://developers.jup.ag/docs/portal/rate-limits)

The transport records `_requested_at`, `_sent_at`, `_received_at`, `_queue_ms`,
`_http_ms`, adapter version and cache use. The primary model also records quote
age, modeled delay and simulated fill time. A received quote timestamp does not
erase the time spent waiting for three preflight requests. The primary decision
is revalidated immediately before ledger commit, including the signal's own
source timestamp and current quality/risk gates.

A trigger based on a cached mark requires refresh. Forced refresh sets a minimum
received timestamp equal to the new request/decision time, so the transport
cannot return an earlier favorable cached quote after later evidence has been
observed. Profit exits are re-evaluated on the refreshed sale.

Failures remain explicit: `AUTHENTICATION_REQUIRED` (401), `ACCESS_DENIED` (403),
`RATE_LIMITED` (429), `TIMEOUT`, `HTTP_ERROR`, `SCHEMA_MISMATCH`,
`QUOTE_BUDGET_TIMEOUT` and invalid request/unsupported adapter errors. Rate-limit
reset or retry headers create a shared backoff. Failure never becomes a chart
sale or a synthetic successful fill. A failed read-only quote itself incurs no
on-chain fee; hypothetical failed transaction costs belong to an explicit
simulation scenario, not a provider observation.

An unavailable primary exit retains the position, pending reason, last known
valuation, age, retry count and scheduled backoff. Persistent failure is visible
as unliquidatable risk. The conservative committed amount is shown separately
from the last known mark; unknown liquidation is not fresh price or zero risk.
Pausing new entries does not stop management of existing positions.

## Jupiter adapter and observed contracts

The adapter remains on `/swap/v1/quote`; it rejects an unrelated endpoint rather
than guessing a V2 schema. Official documentation calls V1 superseded by Swap
V2, while still publishing its quote contract. It is therefore inaccurate to
claim that V1 necessarily stopped working. Migration to V2 needs its own
read-only adapter/schema validation; order/build/execute/submit methods must not
be added to this PAPER path merely to migrate quotes. [Jupiter V1 documentation](https://developers.jup.ag/docs/api-reference/swap/v1/quote)

The reproducible probe is:

```powershell
.venv/Scripts/python.exe scripts/check_quote_contract.py
```

It uses a temporary quote cache and calls only Jupiter quote plus Solana
`getVersion`/`getSlot`. The measured probe ran during **2026-10-05 17:29:11–17:29:12
UTC**, or **20:29:11–20:29:12 Europe/Kiev**:

| Check | Actual result |
| --- | --- |
| Jupiter V1 USDC → WSOL quote schema | PASS, context slot `453646461`, HTTP duration `130 ms` |
| Solana RPC `getVersion` | PASS, reported core version `4.3.0` |
| Solana RPC confirmed `getSlot` | PASS, slot `453646461`, HTTP `200` |

These are availability/schema observations from that interval. They do not prove
long-term uptime, future authentication eligibility, future quota, route landing
or financial performance. No account wallet, signature or transaction submission
was used. Equal slots in this probe do not make separate providers' responses an
atomic snapshot. Optional latest-slot evidence is checked when present; missing
independent latest-slot evidence cannot be described as a measured chain lag.

## PumpSwap reserve evidence and conversion

`pumpswap_stop_quote.py` verifies pool program owner/discriminator, exact base and
quote mint identities, vault identities and authority, token-program owners,
initialized accounts, mint decimals and a nonzero confirmed RPC context slot.
Pool and reserve reads are batched through `getMultipleAccounts`, and fee-config
owner/discriminator are checked. Unsupported token extensions are rejected by
the direct reserve decoder. Fee calculations use integer ceiling/division,
including signed virtual quote reserves and real-reserve sufficiency.

The current official IDL includes cashback, holder rewards and buyback-related
configuration beyond the old three-component fee assumptions. The native reserve
estimate rejects unsupported modes, missing/fallback fee evidence or excessive
fee/snapshot slot disagreement. Raw `buyback_basis_points` is not automatically
interpreted as an additional percentage fee on the entire trade. [Official PumpSwap IDL](https://github.com/pump-fun/pump-public-docs/blob/main/idl/pump_amm.json),
[official PumpSwap documentation](https://github.com/pump-fun/pump-public-docs/blob/main/docs/PUMP_SWAP_README.md)

A separate read-only account check in this repair session observed confirmed
slot `453645181`, HTTP duration `121 ms`, verified WSOL decimals `9`, the expected
fee-config owner and a 4,097-byte fee account. Global configuration decoded
cashback enabled and raw buyback basis points `5000`. A second-accurate timestamp
was not saved for this earlier check; the date is 2026-10-05, and no more precise
time is asserted. These observations motivated rejecting unmodeled fee paths,
not claiming that the direct math fully reproduces every current program mode.

USD-account entries and marks now use the full validated Jupiter USDC ↔ token
route. Dividing by scanner SOL/USD and adding 10 bps does not establish an
executable conversion. `cached_snapshot(pair,mint)` shares verified reserve
observations without API calls; `reserve_inventory_mark` can return native WSOL
quantity with `realized_usdc=None`. It is diagnostic inventory evidence, not a
cash credit. The primary engine cannot clear a missing token → SOL → USDC route
by pretending scanner FX settled.

## Durable tape and causal availability

`live_tape.py` uses an SQLite journal (`NEO_TAPE_DB_PATH`, default adjacent to
`NEO_LIVE_TAPE_PATH`) with WAL and full synchronization. `pairs` records committed
cursors/page anchors, `signatures` records pending and terminal classifications,
and `events` records decoded swaps. Imports create no database or account file.

Signature pages use `before`/`until`, newest-first ordering and confirmed
commitment. The cursor and every discovered pending signature are committed in
the same database transaction. A page budget pauses catch-up and preserves its
anchor; it never marks truncated work as processed. Default pages contain 100
signatures, at most two rounds are fetched per poll, and HTTP batches are split
at 20 calls. Provider limits are configurable rather than presumed unlimited.
[Solana getSignaturesForAddress](https://solana.com/docs/rpc/http/getsignaturesforaddress)

Transactions are fetched under a bounded per-poll budget, deduplicated by
signature/pool and matched by JSON-RPC ID. Missing or duplicate IDs remain
explicit errors. A null body or transient RPC failure remains pending, with
exponential retry delay up to 60 seconds. Solana explicitly allows
`getTransaction` to return null when the transaction is unavailable at the
requested commitment. [Solana getTransaction](https://solana.com/docs/rpc/http/gettransaction)

Terminal classifications distinguish processed swaps, verified non-swaps,
failed transactions and unclassified/unsupported evidence. Events and terminal
classification commit together. Repeating a page, re-fetching a signature or
restarting between discovery and processing does not duplicate a recorded event
or permanently lose a temporarily null body.

The strict trade decoder currently supports PumpSwap only. It requires the
official swap program/discriminator and exact mint/pool/user-account evidence,
and decodes actual base/quote quantities from successful BUY/SELL events,
including Anchor CPI events. Multiple swaps remain separate event indices.
Transfers, mint/burn, liquidity operations and arbitrary token balance deltas do
not automatically become BUY/SELL. Unknown programs, mismatched metadata,
missing events, unsupported quote assets and invalid timestamps reduce coverage.
The swap actor must be a transaction signer before it is accepted as wallet-flow
evidence; public-key diversity still does not prove economic independence or
absence of Sybil/wash trading.

Each event keeps event time (`ts`), first signature observation, ingestion/
`available_at`, slot, signature, event ID, sanitized provider identity, raw
amounts, decimals and quality/provenance fields. Decisions consume only events
available by the decision time. An old confirmed event arriving late becomes
available late; replay does not insert it retroactively at its block time.

For USDC quote legs, USD size is the actual decoded quote-leg amount. For WSOL,
the recorder shares a timestamped Jupiter conversion-quote reference across all
pools/accounts. It refreshes at most once per 45 seconds, yields to exit priority,
and requires reference/ingestion age and event/reference separation no greater
than 60 seconds. This is an explicitly estimated USD valuation of an actual SOL
leg, not an executed conversion or a reconstructed historical USD price. A
scanner-implied FX fallback remains flagged and unclassified, and cannot enter
the fresh order-flow signal.

The JSON projection retains a five-minute event window and a bounded UI list
(default 1,600 events). SQLite retains the complete recorded event/classification
journal. Projection truncation is explicit and degrades coverage; it does not
silently claim a complete five-minute signal. Position pools are pinned ahead of
discovery-ranked pools. `pair_coverage` exposes COMPLETE/DEGRADED/UNKNOWN,
`complete_since_ms`, last poll, backlog, pagination state, unclassified count,
oldest pending time, lag and reason. A complete provider fetch is evidence within
the recorder's declared horizon and provider capabilities, not proof that the
provider has unlimited historical retention.

## Shared recording and parallel PAPER/replay models

`training_bridge.py` copies the primary engine's already-observed market, flow,
price/safety proof and quote evidence into one asynchronous JSONL journal.
Teaching accounts replay the same stream; they do not request an API per
portfolio. The worker runs separately from primary exits. Queue loss is counted
and invalidates evidence coverage; it is not hidden as extra independent samples.

There are two distinct teaching execution modes:

1. Recorded exact-quantity quote mode consumes only saved mint/route/amount/
   timestamp evidence. Its selected quote threshold is a stress-model output,
   not a guaranteed fill. Quote-included AMM costs are not charged again.
2. `RECORDED_LIQUIDITY_MODEL` estimates symmetric constant-product reserves from
   independently checked recorded price and scanner liquidity. It applies an
   explicit fee/network/rent scenario and adverse slippage. This is not observed
   on-chain reserve state, is not an exact PumpSwap/CLMM/transfer-extension
   simulator and is not allowed to imply the same financial result as the
   primary Jupiter route.

Both modes require causal freshness, route/identity evidence, capital and risk
limits. The liquidity model requires fresh checked price proof for the held exact
mint/pool; a new wall-clock stamp on an old cached pass does not renew that proof.
A route-evidence TTL is distinct from a fill-quote TTL. The preflight can support
recent route feasibility without being treated as a fresh later fill.

The bridge caches buy and sell route proofs independently for 30 seconds, with
their original receipt times. A sale 2.35 seconds before the final buy remains
route evidence, not a 2-second-valid execution quote. Fresh sell marks never
extend expired buy proof. The bridge submits `quotes=[]` for the geometry model;
the primary raw quotes remain in source evidence for the separate exact replay.

New teaching books default to $20 notional within the unchanged $25 full-capital
position limit on $500, four positions and 20% exposure. Fee/rent costs are
included before admitting the pending order and again at modeled landing. The
$20 choice reduces fixed-cost distortion compared with the initial $10 design;
it does not increase the capital/risk limits.

Network estimates use the actual main budget or a fresh SOL-denominated budget,
whichever is larger. The independent book charges the maximum of the observed
main account reserve and estimated first-token-account rent on every model entry.
Its ATA inventory and rent recovery are unknown: this can overestimate repeated
rent, and is explicitly labeled. A main-account zero reserve does not establish
that another training account has a free ATA. Missing proof is unknown cost.

Complete supported raw fee-asset legs provide separate buy/sell estimated fee
rates. Otherwise the versioned AMM fee hypothesis applies (PumpSwap fallback
125 bps per side, **not a guaranteed bound**). That fallback usually fails the
2.75% roundtrip quality guard; it is not weakened to manufacture trades. Fresh
valid fee observations may make a cheaper hypothesis executable. Imported exact
quotes likewise require explicitly declared network and entry-account reserves;
missing cost metadata does not default to a free trade.

Fee and account-reserve assumptions are versioned evidence fields. A fixed DEX
fee schedule is a hypothesis, not a proven conservative bound for all current
program modes. Network and refundable account-reserve estimates must remain
explicitly estimated; missing measured costs cannot be labeled observed. The
liquidity model starts from price/liquidity rather than provider quote output,
so its fee is applied once to its own modeled input, not added on top of an
already fee-deducted quote.

Replay advances a logical clock without waiting in real time. An order created
at a decision time cannot fill from an older favorable observation after its
modeled delay; it requires subsequent matching, available evidence. Missing
routes, stale marks and failed attempts remain in positions, cash costs and risk.
Conservative zero liquidation marks represent unknown risk, not a completed sale.
Every portfolio has independent capital and reservations. Simulation count,
unique observations and dependent market episodes are different metrics.

Reproducible commands use a new isolated state/output path:

```powershell
.venv/Scripts/python.exe scripts/paper_training.py import --input saved-observations.jsonl --output replay-observations.jsonl
.venv/Scripts/python.exe scripts/paper_training.py replay --input replay-observations.jsonl --state isolated-training/state.json --snapshot isolated-training/result.json
.venv/Scripts/python.exe scripts/paper_training.py compare --state isolated-training/state.json
.venv/Scripts/python.exe scripts/replay_main.py --help
.venv/Scripts/python.exe scripts/compare_main.py --help
```

The primary replay separately exercises the actual primary decision/execution/
state path using recorded adapters with no network fallback. Missing historical
quotes are not rebuilt from today's prices. Synthetic correctness fixtures prove
behavior, not positive net expectancy. Statistical acceptance and comparison
criteria are described in [STRATEGY_VALIDATION.md](STRATEGY_VALIDATION.md).

## Verification relevant to this model

```powershell
$env:PYTHONPATH='backend'
.venv/Scripts/python.exe -m unittest discover -s tests -p test_tape_execution_repair.py -q
.venv/Scripts/python.exe -m unittest discover -s tests -p test_pumpswap_stop_quote.py -q
.venv/Scripts/python.exe -m unittest discover -s tests -p 'test_astra*' -q
```

The recorded executions of these commands passed 28, 6 and 29 tests respectively.
Coverage includes more than 60 signatures, durable paging/restart, null then
success, shuffled/missing/duplicate IDs, transfers, multiple/Anchor-CPI swaps,
future timestamps, unknown SOL valuation and unverified swap actors, stale
signal/slot checks, post-delay freshness, refusal of an earlier favorable cached
exit, exact raw units, vault/mint/owner validation, missing conversion and
integer fee rounding. The account/stop/partial-exit crash tests live in the
primary-engine regression suites. Astra's additional six shared-client tests
exercise both account clients through the real transport with fake read-only
HTTP: shared request spacing despite a custom account directory, exit queue
markers, original cache timestamps, fresh post-decision sales replacing an older
favorable preview, shared 429 cooldown and explicit authentication failures.
Its state reads and atomic writes explicitly use UTF-8; a multilingual-state
regression verifies that Cyrillic, CJK and emoji survive restart.
These checks establish engineering behavior;
they do not establish a trading edge.
