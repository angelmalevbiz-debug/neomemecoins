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
