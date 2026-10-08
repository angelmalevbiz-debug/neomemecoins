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

## STALE_MARKET_EXIT seconds after re-entry (archived main ledger, 2026-10-06/07)

Finding (archived pre-reset main account, session `20261006-210327`, 2026-10-06 21:03Z to 2026-10-07 08:40Z, `.runtime/accounts/archive/*/state.json`): 14 of 31 closed trades carry `exit_reason = STALE_MARKET_EXIT` with `closed_at - opened_at` of only 2.1-4.4 s and a combined -48.04 USD, although an entry requires the final feed row to be at most 8 s old (`stale_signal`), at most 30 s old (`stale_feed`) and at most 12 s old at the verified-flow gate, while `STALE_MARKET_EXIT` requires the newest exact-pair observation to be more than 60 s old.

**Root cause: candidate priority, not a timestamp mismatch.** Until the monitor restart at 2026-10-07 05:49:22Z the exit path in `Monitor.update_positions` chose its market observation as `by_address[(mint, pool)] or by_address[mint]`, falling back to `STATE.position_market` and the entry snapshot only when those were absent. `by_address[(mint, pool)]` is the record `scan_once` pins into `STATE.position_market` for every **open** position, and that record is never removed when the trade closes. On a re-entry into the same pool, always at least 20 minutes after the previous close because of the per-token cooldown, the first position-guard pass (0.5 s cadence) ran before `scan_once` had re-pinned the new position, so the exit path took the record pinned during the previous trade's hold. Its `updatedAt` was 20-248 minutes old, `stamp - updatedAt > 60_000` fired `STALE_MARKET_EXIT`, the forced sell quote waited about 1.8 s in the shared quote queue, and the trade was booked 2.1 s after entry (4.3 s when the cached quote had to be refreshed once). The fresh feed row and the entry snapshot were present but outranked.

Evidence from the archived records (`python scripts/stale_exit_forensics.py <archive>/state.json`, read-only):

| Trade | Pool | Opened UTC | Hold s | Signal age at entry ms | Snapshot age at close s | Previous trade in pool / gap min | Exit price equals a price recorded by an earlier trade in the pool |
| ---: | --- | --- | ---: | ---: | ---: | --- | --- |
| 6 | IRL | 10-06 23:05:24 | 4.3 | 467 | 17.6 | #5 / 20.7 | yes |
| 8 | CATCRAFT | 10-06 23:33:02 | 4.3 | 4835 | 9.1 | #7 / 20.8 | yes |
| 9 | CATCRAFT | 10-07 00:03:59 | 2.1 | 6343 | 8.5 | #8 / 30.9 | no (0.001225, later booked again by #10 and #11) |
| 10 | CATCRAFT | 10-07 00:24:07 | 2.2 | 120 | 9.0 | #9 / 20.1 | yes |
| 11 | CATCRAFT | 10-07 00:48:54 | 2.2 | 2068 | 7.5 | #10 / 24.7 | yes |
| 13 | A1 | 10-07 01:20:07 | 2.1 | 2375 | 7.7 | #1 / 247.8 | yes |
| 14 | CATCRAFT | 10-07 01:58:32 | 4.3 | 674 | 11.5 | #12 / 38.9 | yes |
| 17 | CATCRAFT | 10-07 02:45:49 | 4.4 | 1849 | 25.2 | #14 / 47.2 | no (0.00155) |
| 18 | A1 | 10-07 02:47:39 | 4.3 | 1233 | 18.5 | #15 / 34.4 | yes |
| 19 | CATCRAFT | 10-07 03:19:41 | 4.3 | 665 | 11.4 | #17 / 33.8 | yes |
| 20 | RARINU | 10-07 03:21:38 | 4.3 | 2942 | 10.8 | #16 / 42.5 | yes |
| 21 | RARINU | 10-07 03:42:10 | 2.2 | 492 | 25.1 | #20 / 20.5 | yes |
| 24 | CATCRAFT | 10-07 04:14:58 | 2.2 | 1166 | 13.0 | #22 / 20.1 | yes |
| 26 | RARINU | 10-07 05:43:41 | 2.2 | 2392 | 14.3 | #21 / 121.5 | yes |

Every stale close is a same-pool re-entry; no first entry into a pool closed stale. In 12 of 14 the booked `exit_price` is exactly a price recorded by an earlier trade in that pool, which is where the pinned record came from; the other two match a record pinned while the previous trade's exit was pending. The entry snapshot (`coin_snapshot.updatedAt`) was 7.5-25.2 s old at close, never more than 60 s, so a provider-timestamp mismatch is excluded: `make_coin` stamps `updatedAt` from receipt time (`_market_observed_at` or `now_ms()`), never from DexScreener `pairCreatedAt`. The ledger events record monitor restarts at 03:59:26Z, 05:49:22Z and 06:02:29Z on 2026-10-07; commit `566e941`, authored 05:48:03Z, replaced the tuple-key priority with newest-`updatedAt` selection. Before the 05:49:22Z restart 14 of 20 same-pool re-entries closed stale (the six exceptions, #4, #12, #15, #22, #23 and #25, are cases where a scan re-pinned the new position before the first guard pass); after it 0 of 4 same-pool re-entries (#27, #29, #30, #31) did. The -48.04 USD is the modeled round-trip cost of 14 positions that were sold back within seconds and stays in the archived ledger unchanged.

**Repair.** `566e941` (already on `main`, see `docs/MOMENTUM_RUSH_REPAIR.md`) selects the newest exact-mint/exact-pool observation among the feed row, the pinned record and the entry snapshot and rejects future timestamps. This change closes the remaining gap: the ENTRY commit now pins the feed row that passed the final 8 s freshness check into `STATE.position_market` atomically with the position, so until `scan_once` re-pins the held pool the guard always holds an observation at least as fresh as the entry gate. Without it, the only position-owned observation was the candidate snapshot handed to `maybe_open`, which may be up to 12 s older than the row that passed the gate, so a scanner pool switch or an empty feed before the first re-pin could still let the snapshot alone read as more than 60 s stale. Not changed: the 60 s `STALE_MARKET_EXIT` protection (a pinned entry observation ages like any other record and still exits without newer data), `coin_snapshot` contents, the cooldown, and the retention of pinned records after a close (harmless under newest-observation selection).

Regression tests in `tests/test_main_paper_v9.py`: `test_reentry_after_cooldown_is_judged_by_entry_observation_not_previous_trade_record` (archived scenario end to end: cooldown re-entry, 31-minute-old pinned record, guard pass 2.1 s after entry, empty feed), `test_entry_observation_outlives_scanner_pool_switch_before_first_repin` (fails without the entry-time pin) and `test_entry_pinned_observation_expires_like_any_other_record` (fail-closed exit retained), alongside the existing `test_reentry_guard_uses_fresh_feed_over_pinned_previous_trade` and `test_fresh_entry_snapshot_outlives_stale_pinned_tuple`.

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
