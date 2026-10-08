# PAPER learning and strategy validation

Status: engineering correctness checked; **insufficient evidence of statistical trading advantage**. No promised win rate, positive return, live trade or production promotion.

## Versions and independent accounts

Current primary PAPER account (`strategy-lock.json`, locked 2026-10-07): strategy `WINNER_ENSEMBLE_PAPER_V1`, entry `WINNER_ENSEMBLE_VERIFIED_ENTRY_V4` (published as `config.entry_policy_version`), exit `HONEST_NET_EXIT_V1`. The opt-in per-account `ORDER_FLOW_ADAPTIVE` profile proposed in [PR #10](https://github.com/angelmalevbiz-debug/neomemecoins/pull/10) publishes entry `ORDER_FLOW_BALANCED_V4` where it is selected. Adaptive exit candidate: `GOLD_ADAPTIVE_NET_CANDIDATE_V1` preserves conviction/order-flow exits, dynamic target/trailing and holding decisions on the same net liquidation basis.

Historical: the 2026-10-05 audit's primary control was `ORDER_FLOW_EARLY_FIXED_PAPER_V9` with entry `ORDER_FLOW_VALIDATED_THRESHOLDS_V9`; the three-arm replay and the dataset rows below refer to that control. `backend/engine_entry_policy.py` still carries `POLICY_VERSION = 'ORDER_FLOW_VALIDATED_THRESHOLDS_V9'` as an internal label that the engine does not publish; renaming it requires a strategy-lock hash update and is left to the owner.

The `PAPER_TRAINING_V1` worker consumes one shared immutable stream. Default books: CONTROL, EARLY, STRICT, FAST_EXIT, PROTECT, EARLY_PROTECT, STRICT_FAST, GOLD_ADAPTIVE, LEARNER. Each owns $500 and its own orders, positions, history and limits. No sum of balances is presented as one account's return. Default maximum positions is four per book, full committed capital per position 5%, total exposure 20%, daily realized-plus-conservative-open loss 5%, maximum drawdown 10%. Parameters and book capital are in the saved state; a different supplied configuration is rejected on restart.

Training CONTROL is a separate predefined hypothesis: score 70, flow ratio 1.20, net stop 3%, target 10%, hold limit 60 minutes. It is not the primary bot's account. Training comparisons share capital and execution model; the three-arm primary replay separately compares the actual historical/main policies.

Default notional is $20 plus explicit fees/rent within the same full-capital limit. The earlier $10 design was changed because realistic fixed account costs dominated tiny entries. This adjustment preserves all risk fractions. Unknown costs block admission; costly Pump fee assumptions can legitimately produce WAIT.

Fill model, fee assumptions and quality requirements are described in `EXECUTION_MODEL.md`. A valid input can be rejected for cost, missing proofs or risk. More simulations is not a reason to manufacture opportunities or erase waiting periods. Unknown marks count as full-loss risk and block new exposure. No martingale or automatic loss-driven size increase exists.

## Actual learning loop

1. Validate availability, mint/pool, flow, price, safety and execution proof; persist accepted and rejected observations.
2. Each book evaluates a predefined hypothesis and queues its own order. At modeled landing time it requires a subsequent fresh same-pool observation. An observation's receipt being newer does not make its source price newer.
3. Record completed trades, failed attempts, unavailable liquidation and costs. Open trades never become training wins. Rejected signal follow-up paths are evaluated separately and never added to executed PnL.
4. After at least 30 new completed market episodes, score the bounded predefined parameter grid on completed training outcomes. Freeze selected parameters, version, evidence IDs, cutoff and hash.
5. Run fresh candidate/control validation books on only later observations, with a 60-second embargo and exclusion of all training episodes. No overlapping old outcomes are reused as holdout evidence. Later rounds may use older validation as training only in a walk-forward cycle.
6. Save/promote to LEARNER only when all declared gates pass. Persist exact active parameters; the next LEARNER decision reads them. Frozen CONTROL remains unchanged, verified by hash. Candidate tampering or incompatible restart configuration fails closed.
7. Start separate monitoring books after promotion. After at least 30 monitoring episodes, roll back if underperformance reaches $5 or drawdown exceeds the configured limit. Version history, training history and rollback reason persist. A few losses do not trigger a new policy.

Default acceptance: 30 future episode clusters, 20 completed trades, five calendar-day blocks; positive net result; at least $1 net improvement; feasibility at least 90%; drawdown at most 10%; positive net after another 50 bps per side; positive lower bound for approximate episode- and calendar-day-clustered net-improvement intervals. Queue drops mark coverage incomplete and block automatic promotion. Validation exceeding seven days with insufficient sample closes as inconclusive; unresolved positions prevent acceptance. Gate values are configured before the run, not tuned against a winning holdout.

The approximate confidence intervals are diagnostics, not a guarantee. Shared tokens, a shared market and multiple candidate searches can remain correlated. `paired_episode_count` counts common market episode coverage, not necessarily matched executed trades. The evidence panel displays this limitation. A finer independent-market study and multiple-testing correction are needed before any edge claim.

## Strategy Lab cost cap: LAB_ACTIVE_V6 (2026-10-08)

Defect: TEST books admitted a modeled round-trip cost of up to 2.75% against the same 3% net stop that the funded books protect with a 1.5% cap (`promoted_guard.max_entry_cost_pct(STOP_LOSS)` = 0.5 x stop). The 2026-10-06/07 ledgers (1,596 Lab closes, research `analyst-losses.md`) show the consequence: median gross headroom before the stop 0.28%, and 40.4% of 1,398 `STOP_LOSS_3_NET` closes fired with the mark down less than 1%. That is a measurement-validity defect, not a profit lever: a book that pays 2.7% to enter cannot tell a good entry rule from a bad one at a 3% stop.

`lab_activity.POLICY_VERSION = LAB_ACTIVE_V6_STOP_BUDGET_COST_CAP`: every Lab book (TEST, Rush, Scalper, funded and the cost-first pair) admits through `lab_activity.admission_cost_cap_pct(STOP_LOSS)`, which is `promoted_guard.max_entry_cost_pct(STOP_LOSS)` (1.5%) bounded by the unchanged model ceiling `MAX_ENTRY_COST_PCT = 2.75`. Each new position and close records `entry_cost_cap_pct`, `stop_loss_net_pct` and `stop_headroom_pct` (= stop + modeled immediate-exit result, so 1.5% or more at admission). Existing books, balances, histories and open positions are untouched; earlier rows keep `LAB_ACTIVE_V5_CAUSAL_MOMENTUM_RUSH_BRAIN`, and only new closes carry V6. The lifecycle retirement heuristic counts evidence per entry-policy version, so a V6 book starts with a clean evidence window while any earlier retirement marker stays in place.

Expected effect: far fewer Lab entries (17 of the 1,596 historical closes would have passed). Every book publishes `max_entry_roundtrip_cost_pct`, `cost_infeasible_candidates`, `cost_feasibility` and the blocked reason `modeled_roundtrip_cost_limit`; the dashboard shows "cost-infeasible candidates" with the cap and stop. Fewer trades are the finding, never a reason to raise the cap, remove impact, or shrink the stop. Tiny notionals (for example the Scalper's 25%-of-balance cap near $5) are now correctly infeasible because fixed network fees dominate them.

Evaluation of V6 itself (engineering, not performance): on the next >= 30 V6 closes, median `stop_headroom_pct` >= 1.5% (from 0.28%) and the share of `STOP_LOSS_3_NET` closes with |gross mark move| < 1% <= 10% (from 40.4%). No return claim follows from either number.

## COST_FIRST_ESTABLISHED_V1: isolated Lab book pair (2026-10-08)

Hypothesis (research cost-first lens, judged "test as isolated book"): the only observed universe whose fee+impact round trip leaves several percentage points of gross headroom before a 3% net stop is PumpSwap pools in fee tiers <= 50 bps with >= $250,000 liquidity (92 of 100 engine closes and 96% of Lab closes were in 90-125 bps tiers at a 2.4-2.7% round trip; the 30 bps tier paid about 1.1%). Whether that universe has positive expectancy is unknown: the only such pool traded so far went 0W/8L on the engine and n=17 Lab trades at <= 1.5% round trip had PF 0.43.

Definition (`backend/cost_first_established.py`, pure, importable by a later per-account engine profile):

- Universe `COST_FIRST_UNIVERSE_V1`: `dexId == pumpswap`, quote token SOL, PumpSwap fee tier from market cap in SOL (`marketCap / (priceUsd / priceNative)` against `PUMP_FEE_TIERS`) <= 50 bps, liquidity >= $250,000, modeled fee + constant-product impact round trip <= 1.2% at the sized entry. Score, age and momentum are not gates. Unknown denomination, market cap or liquidity is a rejection, never the cheapest tier.
- Size: `min(book entry cap, liquidity x 0.001)`, where the book entry cap is the Lab's standard `min($150, balance)`; the rule only shrinks a size.
- Admission in `strategy_lab.py` is otherwise the funded path unchanged: `promoted_guard.flow_admission` (CONFIRMED_PUMPSWAP_WINDOW, 30 s, >= 3 trades, >= 2 wallets, buys >= 1.2 x sells, 12 s freshness), `risk_admission` (fresh full exact-pool safety), exact mint/pool price identity, the shared V6 1.5% cost cap under the full model (fees, impact, slippage/latency buffers, network) and the commit-time recheck. A tier-50 pool therefore still needs enough liquidity for the full model to fit 1.5%.
- Book A `COST_FIRST_CONTROL`: -3% net stop / +10% net target / 60 min max hold (the Lab standard, `LAB_STANDARD_3_10_60`). Book B `COST_FIRST_SCALED`: -3% net stop / +4% net target / 60 min max hold (`COST_SCALED_3_4_60`). Same universe, same evidence gates, both at $500, `portfolio_group TEST`, `entry_policy_version COST_FIRST_ESTABLISHED_V1`, `promotion_eligible false`, never promoted automatically; gaps below the stop are recorded in full.
- Lifecycle: the shared repeated-loss retirement heuristic (`lab_strategy_lifecycle.py`) applies to both books exactly as to every other TEST book, counted on their own closes stamped `COST_FIRST_ESTABLISHED_V1` (`strategy_lab.lifecycle_activity_versions()`; the other TEST books count `LAB_ACTIVE_V6_STOP_BUDGET_COST_CAP`). A retirement only stops new entries; it never touches balance, history or the exits of an open position, and the acceptance block below stays the only promotion criterion.
- Coverage caveat: the pair can only enter pools the shared tape observes with COMPLETE coverage. The tape scheduler is unchanged in this change set, so cost-first pools that match neither the main nor the funded screens compete for exploration seats only. The diagnostics separate `universe_candidates` from `flow_rejected`/`safety_rejected`; a frequency failure must be attributed to coverage or to the universe before any follow-up, and a scheduler seat rule for this universe would be its own versioned change.

Acceptance criteria for the pair (fixed before the run; evaluated on future observations only; no retune on the same holdout):

| Gate | Threshold |
| --- | --- |
| Sample | >= 20 closed trades per book across >= 30 distinct `(mint, pool, hour)` clusters and >= 5 calendar days, within 7 days of the first close |
| Net result | net PnL > 0 and expectancy per trade > 0 after a +50 bps per leg cost stress recomputed on the same closes |
| Uncertainty | cluster-bootstrap lower bound of expectancy (clustered by `(mint, pool, hour)`) > 0 |
| Profit factor | > 1; null (fewer than 10 losses) means inconclusive, not a pass |
| Drawdown | maximum drawdown <= 10% of the $500 book |
| Frequency | >= 1 closed trade per calendar day on average |
| Failure | any gate missed = "inconclusive" (or "rejected on frequency" when the sample gate fails); the books stay TEST, nothing is retuned, no default account changes |

Passing the gates on book B (or A) is the precondition for a Stage 2 per-account engine profile that imports the same module; it is not a profitability claim, and the 80% win-rate target is not an acceptance criterion.

The Stage 2 profile `COST_FIRST_ESTABLISHED_PAPER_V1` (entry `COST_FIRST_ESTABLISHED_ENTRY_V1`, exit `COST_FIRST_NET_EXIT_V1` with `EXIT_IMPACT_EMERGENCY_V2`) is implemented as an opt-in, per-account choice that nothing enables by default; see [COST_FIRST_ENGINE_PROFILE.md](COST_FIRST_ENGINE_PROFILE.md). Enabling it before the Lab pair passes is an owner decision for one PAPER test account; that account is evaluated as its own sample under the same gates plus feasibility and a −$25-before-20-closes halt, never as evidence for the Lab gates.

## Defensive entry layer (DEFENSIVE_ENTRY_LAYER_V1, 2026-10-08)

Every entry path now runs `STRUCTURAL_RUG_GUARD_V1`, `HEAT_VETO_STACK_V1` and `POOL_LOSS_MEMORY_V1` before any quote, flow promotion or RugCheck call; see [DEFENSIVE_ENTRY_LAYER.md](DEFENSIVE_ENTRY_LAYER.md). The research behind it (22.8 h scan log, PAPER) found no strategy with positive expectancy after costs; the layer removes measured loss tails (holdout drain hazard 4.39% → 1.31% per position-hour; forward heat-veto difference −5.07 pp, CI [−11.0, −0.95]) and creates no edge. Consequences for validation:

- Closes after this change carry new entry-policy versions (`WINNER_ENSEMBLE_VERIFIED_ENTRY_V5`, `ORDER_FLOW_BALANCED_V5`, `COST_FIRST_ESTABLISHED_ENTRY_V2`, `COST_FIRST_ESTABLISHED_V2`, `LAB_ACTIVE_V7_DEFENSIVE_ENTRY`, `PROMOTED_MARKET_BRANCHES_EVIDENCE_COST_V5`) and are a new sample; earlier closes are never relabelled or pooled into it as acceptance evidence.
- The cost-first pair's acceptance criteria above apply unchanged to its V2 sample (structural guard inside the universe). Its V1 sample is a different universe that included rug-family pools.
- A book or account that the layer leaves with too few entries to reach the gates is reported as such; filters are never loosened to force trades.
- The layer's own precision is not yet measurable forward. Entry diagnostics keep only per-scan counts and a few examples, overwritten every scan; no per-candidate record of blocked pools is persisted. Matching structural reason codes against later drains (liquidity → 0 or price −80% within 10 min) needs the bounded forward log listed as a follow-up in DEFENSIVE_ENTRY_LAYER.md; until it exists and has run for several days, the research precision figures are not confirmed on live data.
- Exits are unchanged: the ORDER_FLOW_ADAPTIVE exit context keeps the V1 market score (`EXIT_CONTEXT_SCORE_VERSION`), so an adaptive close is comparable across the change; the V2 score affects entries only.

## Dataset provenance and measurements

| Dataset | Scope | Valid conclusion |
| --- | --- | --- |
| Archived deployed public snapshot, retrieved 2026-10-05 17:09:59 UTC | 33 mixed-version closed primary records, -$161.919839 recorded net; no sufficient raw quote pairs | Available historical sample only. No comparable challenger replay or lifetime inference. |
| `tests/fixtures/paper_gap_v9.jsonl` | Explicit synthetic two-observation correctness case; no real token or market period | Old loss clamp reproduction versus honest gap accounting. |
| Controlled unittest streams | Future timestamps, failed routes, losses, blocked entries, accepted/rejected candidates and restart | Decision/persistence/no-look-ahead correctness, not empirical profitability. |
| New bridge `observations.jsonl` | Actual captured provider proofs and timestamps once repaired code runs | Eligible for subsequent causal replay; raw missing evidence must stay missing. No such complete multi-day production dataset was available during this audit. |
| `tests/fixtures/replay_identity_v1.jsonl` (REPLAY_IDENTITY_V1) | Synthetic recorded-schema rows with the live timing measured on 2026-10-07 (4.65 s preflight, 47 s cached risk pass, 8.3 s candidate snapshot refreshed at commit) | Replay identity only: the primary replay must book the live trade within $0.01 and WAIT with the recorded reasons; labelled exit variants change exits only. |
| Main journal slice 2026-10-07 (23,452 rows, two pools; not committed) | Three live closes and one open position on one pool in 14 h | Reproduced by the reconciled replay (see `PAPER_RUNBOOK.md`); a measurement check, not a sample for any expectancy statement. |
| `tests/fixtures/replay_main_20261007_slice.jsonl.gz` (1,339 recorded lines of that journal, SHA-256 pinned) with `replay_main_20261007.expected.json` | The 10 entry-evidence rows, their episodes' marks, guard and linked commit-outcome rows, and the pre-preflight exact-pool context; the booked live pnl values | Replay identity on real data: the three closes and the open position within $0.01 of the booked ledger. |

Synthetic same-input comparison: frozen HEAD one completed trade, -$10; repaired fixed and adaptive each one completed trade, -$40.195. This proves removal of fabricated stop accounting and consistent cost treatment. It offers no ranking of economic performance between fixed/adaptive strategies. A two-point path contains no reliable MFE/MAE or dependence-adjusted confidence interval.

`simulation_count`, unique observation IDs and `(mint,pool,hour)` episode clusters are distinct counters. Duplicate observations are skipped across restart. Replaying the same dataset does not add independent observations; repeated outcomes are not presented as new evidence. Episode clustering is a conservative accounting convention, not proof that clusters are independent.

The primary replay is separate from the multi-book learner, runs the actual main engine and checks raw quotes, exact quantities, initial/final preflight chronology, mint/pool and source timestamps. All arms receive the identical evidence stream and hash. Unequal/sparse exact-quantity coverage can block an arm; such missing trades are coverage limitations rather than economic superiority. Its freshness windows are derived from the live engine/guard constants and reported per run (`REPLAY_LIVE_DERIVED_WINDOWS_V1`); a replay that books the recorded ledger proves the instrument, nothing about the strategy. Exit hypotheses are run as labelled offline variants (`REPLAY_EXIT_VARIANT_V1`) and can only be valued up to the live exit, because recorded sell quotes stop there.

## Checks and reporting

`scripts/run_python_checks.py` isolates account/audit/cache paths and runs both regression suites. Coverage includes real primary entry/exit/restart, partial-leg accounting, history beyond 300, tape pagination/retry, alternate pool, no-route/unknown risk, no live mode, actual child process, reset journal boundary, learned parameters changing subsequent decisions, future validation and unchanged control. Existing baseline correctness fixtures and old expected thresholds remain provenance; changed expectations are tied to the declared repair/defaults.

Frontend checks are `npm run lint`, `npm run check:strategy`, `npm run build`; build includes strategy/extension guards. The read-only contract probe is `python scripts/check_quote_contract.py`; API success establishes contract reachability/schema only, not future availability or a filled trade.

Final exact command results are recorded in `PAPER_RUNBOOK.md`. Financial challenger validation remains **SKIPPED: no adequate real recorded future holdout**. Automatic acceptance is enabled only in isolated PAPER training, never on the primary or a real account.
