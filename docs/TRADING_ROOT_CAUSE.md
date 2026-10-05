# PAPER trading audit and repair

Audit base: `bccb0ed30bac1efbf2e16cfe89e0ee1af049f050` (repository HEAD retrieved on 2026-10-05). The public deployed API did **not** match this HEAD: its snapshot reported `ORDER_FLOW_ADAPTIVE`, five positions and older policy versions. Its deployed commit could not be established. Repository findings and deployed observations are therefore separate evidence.

## Confirmed defects

| Defect and reproduction | Repair and regression evidence |
| --- | --- |
| The old `STOP_LOSS_PCT=5` and `STOP_EXECUTION_BUFFER_PCT=4.5` formula armed a chart-price stop at only -0.5%. Chart returns excluded executable costs. | `market_monitor.Monitor.update_positions` calls the versioned net exit policy. Planned buffer is 0.5%; a stop is an intention, not a fill guarantee. Tests exercise cost-only fluctuations, genuine net stops and gaps. |
| `enforce_paper_stop_cap` rewrote an observed loss beyond -5% to exactly -5%. A $200 entry with $160 proceeds became a reported $10 loss instead of a $40 loss. The old accounting could return money never received. | Compatibility helper returns the original observed net amount; ledger records all costs and gap loss. Actual primary-engine comparison on the same synthetic quotes reports old -$10 versus repaired -$40.195. The additional $0.195 is the fixture's modeled network/account cost. This is correctness evidence, not a performance improvement. |
| Tape marked signatures processed before `getTransaction` succeeded; null, temporary RPC failures and a bounded first page could permanently lose trades. RPC arrays were treated as ordered; transfers and unknown DEX instructions could contaminate direction/wallet counts. | Durable SQLite pending queue, independent terminal status, retry/backoff, page anchors and cursors, explicit ID matching and strict successful PumpSwap IDL/event decoder. Regression includes >120 signatures, null-then-success, missing/shuffled IDs, unsuccessful transactions, transfer-only transactions and verified signer actors. |
| Entry thresholds were displayed as configuration while entry branch thresholds were hardcoded independently. A provisional pending rug response could pass as a scout. | Frozen validated `gold_order_flow.EntryThresholds` reaches every EARLY branch. Effective values and config hash are stored in snapshot and trade. Full safety, flow coverage and final signal age are mandatory. Pending/unknown is WAIT. |
| Scanner pool selection was reused for held positions, including tokens missing from feed. Pausing entries could prevent management. | Position monitoring pins `(mint,pair)` independently, retains position-market evidence and continues exits while entries are paused. Another pool's price cannot close the position. |
| Missing route/quote could hide losses behind an old mark or discard an unsellable position. | Explicit last-known diagnostic versus conservative full committed-capital risk; pending exit, bounded attempts/backoff and retained `UNSELLABLE` position. No fabricated sale or realized profit. |
| Primary history was truncated to 300; statistics and trade sequence mixed denominators. A zero-result trade could be a win and a no-loss profit factor was represented by an arbitrary finite number. | Complete authoritative history, scoped lifetime/session/rolling/version metrics, unknown legacy record count, breakeven distinct from wins, nullable profit factor with status. UI uses the same denominator. |
| State, balance and audit could diverge across an interrupted entry/close. A module import could load or touch account paths. | State is the atomic authority with persisted audit outbox and idempotent event IDs. Partial exits allocate entry costs once; closed trade aggregates all legs. Import is inert; main explicitly loads before threads. Crash/restart tests exercise critical commits and failed audit writes. |
| UTF-8 save followed by a locale-default read could fail after a Windows restart with multilingual token names. Astra's custom-root quote budget could bypass shared throttling/exit priority. | Explicit UTF-8 state/cache I/O with a real non-UTF-8 child restart regression. Astra delegates to the shared quote transport and requires a new receipt for an exit; 429/auth/cache integration cases cover the common path. |
| Fresh personal accounts could inherit the shared bot's old trades and PnL. | `user_gateway` starts independent capital/history, atomically persists the registry, refuses a corrupt registry and preserves explicitly existing accounts. |
| The old snapshot referred to undefined `live_quote`. | Snapshot no longer uses local entry variables. Frozen comparison exposes this defect and labels its limited raw-ledger metric scope. |
| Single-account experience and informal recent-loss size changes did not constitute validated learning. | Main remains a fixed control. Separate process runs independently funded PAPER books, saves actual candidate parameters, evaluates untouched subsequent episodes, promotes only in LEARNER, and rolls back under declared conditions. No main/live auto-promotion. |

Historical boundaries found by `git log -S` and inspected diffs: `d745eb6` replaced adaptive exits with fixed 3/10; `1952e81` introduced the 4.5 buffer; `0b6610a` introduced the stop accounting cap; `9a75ba7` introduced the snapshot reference. These are code provenance, not proof of which commit the server ran.

## Deployed sample and missing evidence

A read-only public snapshot was archived locally before reset, with SHA256 `5525505772afd1849212627248c00855178abc82ce994b91159186358cc274a7`. It contained 33 closed primary records: 5 positive and 28 negative, total recorded net -$161.919839. Entry timestamps span 2026-10-04 20:28:20.801 UTC through 2026-10-05 10:49:25.253 UTC. Mixed policy groups:

| Recorded strategy / entry / exit version | Records | Wins / losses | Recorded net USD |
| --- | ---: | ---: | ---: |
| ORDER_FLOW_FAST_3_10_V5 / GOLD_VERIFIED_V6 / NET_3_10_V5 | 30 | 5 / 25 | -128.385851 |
| ORDER_FLOW_ADAPTIVE / GOLD_VERIFIED_V6 / NET_5_10_DYNAMIC_STOP_V7 | 1 | 0 / 1 | -26.037988 |
| ORDER_FLOW_ADAPTIVE / ORDER_FLOW_BALANCED_V4 / unknown | 2 | 0 / 2 | -7.496000 |

None contained sufficient paired raw execution and time-local market evidence to reconstruct a financial replay. This is the **available sample**, not an audited lifetime return or an independent homogeneous strategy sample. Old outcomes are never rewritten using today's quotes. No credentials or private account files are published with this audit.

Likely contributors needing further data: high friction in thin pools, delayed flow coverage, stale or differently selected pools, unsellable routes, and dependence among simultaneous meme tokens. Engineering fixes remove measured distortions; they do not establish positive trading expectancy.

## Authorization, reset and manifest

The latest user explicitly requested a history/money reset and clarified **all PAPER accounts**. This supersedes the attachment's earlier no-reset instruction. Local `reset-all` performs checksum archives before clearing each known account, training book, legacy Lab/Astra and paired experiment state; preserves independent starting capital; retains raw market recordings; and refuses unknown state schemas. Writers must be stopped for offline reset.

The deployed public `/control/reset` returned HTTP 200: shared primary account verified at $1,000, zero history and zero positions. A fresh pre-reset snapshot was archived separately. Remote Lab/Astra and private accounts were not reset by this route; administrative host access is required to run the prepared all-account command. Do not describe the whole server as reset.

Original `strategy-lock.json` is preserved byte-for-byte as `strategy-lock-before-repair.json`. The updated manifest records baseline commit, versions, hash changes and reasons. Hash verification remains required. New UTF-8/LF canonical hashing makes Windows and Linux verification equal while retaining raw-byte verification for older manifests. Baseline Gold fixtures were not overwritten.

## Reproduction

```powershell
.venv/Scripts/python.exe scripts/run_python_checks.py
.venv/Scripts/python.exe scripts/compare_main.py --input tests/fixtures/paper_gap_v9.jsonl --output-dir .runtime/gap-comparison
npm run check:strategy
```

The comparison creates three isolated account directories and invokes actual `Monitor.maybe_open`, `Monitor.update_positions` and durable State paths: frozen HEAD, repaired fixed, repaired adaptive. It disables network fallback. Missing exact historical quantities/proofs produce WAIT, not invented executions. See `STRATEGY_VALIDATION.md` and `PAPER_RUNBOOK.md`.
