# High-frequency study, 2026-10-09 (PAPER research archive)

This folder preserves the studies behind `LAB_HIGH_FREQUENCY_V1`
([docs/LAB_HIGH_FREQUENCY.md](../../docs/LAB_HIGH_FREQUENCY.md)). Everything is PAPER
(simulated money). Nothing here is a profit claim.

## Question and answer

The owner asked on 2026-10-09: "it has to trade more often like 50 trades per hour for each
strategy". The studies asked how a PAPER book can trade about 50 times an hour as honestly
and as cheaply as possible, and what that costs.

- About 50 trades an hour per book is reachable: on the 17.2-hour holdout the random control
  traded 47.9 times an hour, the quiet-pool book 44.6 and the 15-minute-low book 40.5.
- Every configuration loses about the round-trip cost on every trade. On the holdout the books
  lost 3.57-3.71% per trade on the net50 basis and 2.60-2.74% on the booked basis.
- No signal beat random entries by more than its noise. The quiet-pool gap, +0.14 pp, comes
  from choosing cheaper, deeper pools: within one fee bucket it is -0.06 / +0.04 pp.

## Studies

All five use the audited research harness `harness_final.py` of the edge study
(`research/edge_study_2026_10_08/leaderboard/`) on the same 43-hour scan log: next-refresh
fills (F1), feed gaps (F2), VANISHED pools, drained pools at 0, and the CALIB_V1 cost
calibration. The data itself (`obs.sqlite3`, `series.pkl`, the event and point tables) is
regenerated locally and never committed (`.gitignore`).

| Folder | Question | Main output |
|---|---|---|
| `hf_signals/` | Which refresh-event signals lose least, against random entries in the same universe? | `final_eval.out`, `lossmem.out` |
| `hf_cost_floor/` | How cheap can a round trip get, and how does re-entry change the rate? | `p06_holdout.out` |
| `hf_architecture/` | Slots, kill floors, daily caps and the container's CPU and storage cost | `out/s2.out`, `out/s3_bench.json` |
| `hf_tape/` | Can on-chain tape bursts beat DexScreener refresh events? | `eval_holdout.out` |
| `hf_synthesis/` | One consistent multi-slot simulator, rules chosen on train, a single pre-registered holdout | `syn_holdout.out`, `holdout_results.json` |

`hf_synthesis/syn_lib.py` is a multi-slot simulator built on harness primitives. It
reproduces `hf_signals/hfsim.py` exactly: `syn_train.out` line 1 reports 1,814 trades on
both and 0 mismatches.

### Selection and pre-registration

- Train is the first 60% of the scan log (25.8 h). The holdout is the last 40% (17.2 h).
- The shared rules were chosen on train only, by the frozen rule in `syn_train2.py`. The
  chosen configuration was `h120_s3_cd120_gov50_nogb` (`train/chosen_v2.json`): a 120 s
  hold, 3 slots, a 120 s pool cooldown, at most 50 orders per trailing hour, and no loss
  brake.
- `PREREG_holdout.json` recorded the three configurations and the sha256 of
  `syn_holdout.py` and `syn_lib.py` before the holdout ran. The holdout then ran exactly
  once.
  - sha256 of `PREREG_holdout.json`: `a545c392bfb7896b5e42404d0d278a29ff8f1193fec23d4a1bce75088d92ea85`
  - `syn_lib.py`: `407befddb64e04f3c5727a12d945def498cfd2c3e8d43c76a25b7b5c38b903d5`
  - `syn_holdout.py`: `2f1b39cc316d4e328b0f7ce117803180e00dcdbf4168ffa49b4ba24fb883800a`
- `syn_checks.py`, `syn_spikes.py` and `syn_toggle.py` are diagnostics on the chosen
  configurations. They select nothing.

## Holdout results ($25, 3 slots, 17.2 h, all 16 full hours live)

| Book | Trades/h (hourly p10 / median / max) | net50 per trade (pair CI95) | Booked | net0 | $/h net50 / booked |
|---|---|---|---|---|---|
| HF_RND_E95 | 47.9 (45 / 49 / 50) | -3.71% (-3.86 to -3.49) | -2.74% | -1.58% | -44.4 / -32.8 |
| HF_QUIET_E95 | 44.6 (37 / 47 / 50) | -3.57% (-3.74 to -3.43) | -2.60% | -1.38% | -39.8 / -29.0 |
| HF_DIP15_E95 | 40.5 (34 / 43 / 50) | -3.63% (-3.83 to -3.41) | -2.67% | -1.48% | -36.8 / -27.0 |

On train (first 25.8 h) the books lost -3.90%, -3.75% and -3.80% net50 per trade. They traded
34.3, 31.5 and 32.0 times an hour over all hours, and 45.3, 41.6 and 42.3 over live hours.

Other holdout facts (`syn_holdout.out`, `syn_checks.out`, `syn_toggle.out`):

- The random control's five salts lost between -3.64% and -3.77% per trade.
- The modeled round trip at $25 in the E95 universe has a median of 1.73% and a maximum of
  2.47%. The 2.75% cap never binds.
- With POOL_LOSS_MEMORY_V1 enforced (2 losses, then 6 h), the books trade 3.5-4.0 times an
  hour, because about 99% of HF closes lose.
- With a $100 booked loss cap per UTC day, the books make 137-159 closes and trade 2.8-3.5
  hours a day. Each loses $100 booked and about $137 net50 per day, and the capped drawdown is
  about $203 over two UTC days.
- 11 pools toggled in 43 h, flipping between two price levels. One pool with $2.8-3.5M of
  liquidity moved about ±44% per flip. On the holdout the toggle guard plus the outlier
  valuation changed net50 per trade by -0.07 pp (control), -0.05 pp (quiet) and -0.05 pp
  (15-minute low), and the trading rate by -1% to -6% (`syn_toggle.out`).

## The parity fixture

`hf_impl/build_fixture.py` wrote `tests/fixtures/lab_hf_obs_20261008.json`. It holds six E95
pools, from 16 minutes before to 6 minutes after a 30-minute holdout window starting
2026-10-08 19:00 UTC. It stores their scan-log observations, the research engine-guard and
heat flags of their refresh events, and the decisions `syn_lib.run_book` makes on exactly
those rows.

`tests/test_lab_high_frequency.py` replays the fixture through the Lab container. It checks
that the container reproduces every order, both fill times and the booked and net0 result of
each trade. net50 differs by about 0.0015 pp, because the Lab applies the 50 bps stress to the
booked result (`lab_forward_tests.net50`) while the research applied it inside the per-leg
price penalty.

## Rerunning

The scripts expect the scratchpad layout they ran in: these folders beside a
`research/edge_study_2026_10_08/` copy that holds `series.pkl` and `obs.sqlite3`. Some
studies also build their own event or point tables first (`hf_signals/build_events.py`,
`hf_tape/build_points.py`). `MANIFEST.json` lists every archived file with its sha256, and
the outputs that were not archived because they are data or large grids.
