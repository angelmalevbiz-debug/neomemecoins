# Lab paired exits V1 — 2026-10-05

## Scope and protected data
Independent PAPER-ONLY sidecar, own state under `/var/lib/neo-lab-paired`.
No wallet keys, swaps, live orders, main-engine restarts, resets, or edits to historical trades.
`strategy_lab.py` has only an import and a read-only display projection changed; its 33 original entry/exit rules are untouched. Astra is untouched.
The main engine, its strategy lock and daily loss cap remain unchanged.

## Predefined hypotheses (not established profitable rules)
Three groups, each with CONTROL, EARLY, PROTECT. New $500 simulated books only.
- FLOW_EXIT_AB_V1: existing Lab ORDER_FLOW entry rule, cost cap 2.75%.
- MICRO_BREAKOUT_V3: parent MICRO_BREAKOUT_SAFE; age 2–720 minutes, m5 3–18%, >=5 recent trades, >=3 wallets, >=$150 buys, fresh flow, cost cap 2.2%.
- ULTRA_PRECISION_V3: parent ULTRA_PRECISION; m5 <=18%; block fresh observed negative USD flow when >=3 trades, >=2 wallets and >=$100 total. Unknown/thin flow is NOT negative and NOT proof of safety. Cost cap 2.2%.
All groups require positive known SOL quote conversion, valid fresh source data and a cached independent exact-pool reference. No additional Jupiter request budget.
Sizes are reduced from <=$150 until within the group's estimated cost cap, with $10 minimum, bounded by the weakest arm's remaining capital.

## Matched design
All three arms share the exact mint, pool, timestamp, quantity, notional and entry evidence hash. No arm opens another entry until all three complete. All arms wait 20 minutes after the last exit on that mint. The next group episode is blocked after a $50 daily loss in ANY arm (Europe/Sofia); existing positions continue to be marked/exited. This is independent of the main engine's risk account.
Compare exits ONLY within a group. Comparing separate groups or the legacy dashboard does NOT identify the causal impact of entry filters. Entry hypotheses need further controlled/out-of-sample evaluation.
CONTROL uses -3% net / +10% net / 60 minutes, same emergency handling.
EARLY additionally exits on sustained strong negative 30s and 300s USD flow (or a substantial evidence-score deterioration), after >=15s holding, >=6s weakness and >=2 different evidence updates. Thin, stale and pre-entry-only evidence cannot trigger it.
PROTECT adds net-profit floors: peak >=2% -> .5%, >=3% -> 1%, >=5% -> max(2.5%, peak-2%). These are triggers, NOT guaranteed fills.

## Accounting and measurement limits
Frozen cost functions match the original Lab AST. Fees, impact, slippage/latency and network are MODEL ESTIMATES, not actual fills. No historical Jupiter replay or live execution is asserted. Reference prices can lag even when received recently. Entry comparison uses the same model, not magic elimination of execution risk.
No stop/TP clamping: gaps are booked at the next observed modeled fill, including losses beyond -3%. Price outages keep positions open with explicitly stale valuations. After >=30s stale, fresh recovery triggers an exit at the recovered price, never an invented price.
Each trade stores entry snapshot and hash, exit source receipt time, modeled cost components, gross chart move, net PnL, peak net PnL and exit reason.
Paired deltas include only completed triplets; pending episodes and independent token count are visible. PF is null when no losses, never an arbitrary 99. No automatic winner promotion. 100 samples is a review prompt, not statistical proof; repeated tokens, day/regime dependence and multiple testing require scrutiny.

## Verification
`python3 -m unittest discover -s backend/tests -p test_lab_paired.py -v`
`npm run check:strategy && npm run lint && npm run build`
Tests cover frozen cost parity, duplicate/future/wrong-pool events, unknown flow, sustained exits, profit floors, unclamped stops, matched entries, shared cooldown, stale prices, balance conservation, corrupt-state refusal, persistence and read-only projection.

## Operations
Service: `neo-lab-paired.service`; serialized process lock, atomic fsync state writes, clean SIGTERM.
Read-only protection on `/var/lib/neo-market` and filesystem except its own experiment directory.
Display projection adds only `strategy_lab.paired`; original books/stats are not mutated.
Stop this separate service to stop new experiment work; never delete/reset its state. Keep it running to continue management of open paper experiments. Removing the display projection does not alter legacy trading.
