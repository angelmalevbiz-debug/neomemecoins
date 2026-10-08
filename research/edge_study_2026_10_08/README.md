# Edge study, 2026-10-08 (PAPER research archive)

This folder preserves a deep, look-ahead-safe study of whether any way of trading the engine's
PumpSwap universe makes money after realistic costs. Everything is PAPER (simulated money).
Nothing here is a profit claim.

## Verdict

No strategy family has positive expectancy after costs on held-out data.

- 9 hypothesis families were tested: momentum, dips, new pools, paid attention, flow
  acceleration, rotation, exit engineering, on-chain tape and smart wallets, and market regime.
- About 65,000 configurations were evaluated on the train window.
- 27 configurations were pre-selected on train, then re-run with the live rule sets under the
  audited, calibrated harness.
- 0 configurations passed the candidate gate. The top leaderboard row was a random baseline
  with a lucky seed. The best real configuration, tape buyer breadth, was refuted by four
  independent verifiers.
- A pre-registered forward check on 1.9 hours of later data also lost:
  - the live cost-first rule: -1.9% per trade (n=36);
  - random entries: -7% to -8% per trade.

**Why it loses.** A buy and sell costs about 2% in large low-fee pools, which barely move. In
the 100-125 bps pools, where prices do move, it costs 4-6%, and those pools drain often. A
single trade's result varies by 9-13%, so a 15-minute or one-day P&L is noise. Confirming a
+1% per trade edge needs roughly 1,000-3,200 trades.

## What the evidence does support (defensive only)

- **STRUCTURAL_RUG_GUARD_V1.** Every one of the 19 drains among pools that ever held $250k or
  more came from one of two families:
  - LP-pullable pools: liquidity / market cap >= 1, where the creator can pull the liquidity.
  - Serial fake-market-cap pools: a $20M-$1B market cap with liquidity / market cap < 2%,
    with the same tickers relaunched as new mints.

  Young pools drain most: 87% of pools under 15 minutes old were drained within 2 hours.
  Code reference: `rug/rug_guard.py`.
- **HEAT_VETO_STACK.** Entries the veto would block lost about 5 percentage points more at 30
  minutes, a pre-registered forward result with a CI below 0. The veto covers:
  - momentum and buy-share spikes;
  - volume acceleration;
  - extreme 6- or 24-hour moves;
  - paid profiles in high-fee pools;
  - crashes in progress;
  - extreme turnover.
- **EXIT_IMPACT_EMERGENCY V1 fires on quote noise.** It closed 83% of trades in pools of 50 bps
  or less within about 44 seconds.
- **Harness lessons.**
  - DexScreener prices lag the chain by about 20 seconds and refresh about every 30 seconds.
  - Filling at the next point is zero-latency fiction when the price has not changed.
  - Dropping pairs that leave the feed hides drains: a "+7.69% at 60 minutes" base rate was
    really -46%.
  - PAPER fills must come from executable quotes.

The engine implementation of the rug guard, the heat veto and a pool loss memory is described
in `docs/DEFENSIVE_ENTRY_LAYER.md`.

## Lab hypotheses (pre-registered, frozen in `synthesis/strategies.py`)

| Book | Idea | Holdout net50 per trade | Random control |
|---|---|---|---|
| LAB_A_SURGE_EST_GUARD | fresh m5 buy surge in rug-screened pools, fee <= 95 bps | -3.2% (n=32) | -6.1% |
| LAB_B_DIP_MKTDIP_GUARD | coin dip while the whole market dips | -1.3% (n=12) | -4.5% |
| LAB_C_TAPE_BREADTH_EST | tape buyer breadth, no chase (needs tape seats) | +3.1% (n=11, refuted) | -5.3% |

All three are expected to fail. They are forward tests, not strategies.

LAB_A and LAB_B now run with their random controls as PAPER Strategy Lab books
(`LAB_FORWARD_TESTS_V1`, see `docs/LAB_FORWARD_TESTS.md`). LAB_C is not implemented: it
needs tape seats.

**Promotion gate (all must hold on trades entered after the Lab start):**
- at least 150 closes, at least 25 pairs and at least 3 days covering every UTC hour;
- net50 mean > 0, with the pair-bootstrap CI95 lower bound of mean $ per trade > 0;
- beats the same-period random control by more than the control's CI half-width;
- top pair share <= 0.20, and the result stays positive with the best pair removed.

net50 is the engine cost model, plus CALIB_V1, plus 50 bps per leg, plus 200 bps on stop exits
and 100 bps on trailing exits.

## Reproduce or re-score

The data files are not committed; see `.gitignore`. To rebuild them locally, read-only from the
live runtime:

```bash
python research/edge_study_2026_10_08/build_dataset.py 8
python research/edge_study_2026_10_08/harness.py --build
python research/edge_study_2026_10_08/rescore_lab_hypotheses.py
```

- `build_dataset.py` writes `obs.sqlite3`. It reads `NEO_OBSERVATIONS`, which defaults to the
  live `observations.jsonl`, and only reads it.
- `harness.py --build` writes `series.pkl`.
- Tape studies also need `tape_snapshot.sqlite3`. Make it with the SQLite online backup of
  `live_tape.sqlite3` in a single step (`src.backup(dst, pages=-1)` with a `mode=ro` source).
  Multi-step backups restart forever while the tape writes.
- `rescore_lab_hypotheses.py` runs the frozen hypotheses and their random controls under
  `leaderboard/harness_final.py`.
- `lab_forward_parity.py` replays the scan-log points through the Lab's `LAB_FORWARD_TESTS_V1`
  implementation and compares every LAB_A, LAB_B and random-control signal point, and the
  per-minute regime, with the frozen research functions. Rug screens are excluded on both
  sides because the Lab applies them in its defensive layer. On 2026-10-08, over 21 hours
  (780,718 points), it found:
  - the regime equal in all 1,191 minutes;
  - both random controls identical;
  - LAB_A 130 of 132 signal points matched;
  - LAB_B 3,286 of 3,290 signal points matched.

  The 6 research-only points needed an observation older than the Lab's 61-minute pair
  history.
- `leaderboard/run_all.py` rebuilds the full leaderboard. It is slow.

Use the repo virtualenv's Python. The research code uses only the standard library and
imports backend modules read-only: `backend/paper_market_feasibility.py` everywhere, and in
`forensics/` (`funnel.py`, `families.py`, `seatuse.py`) also the live rule modules
(`winner_ensemble`, `order_flow_adaptive_oct4`, `cost_first_engine_profile`,
`promoted_entry_guard`). Their COST_FIRST stage calls
`cost_first_engine_profile.physical_universe_rejections`, the `COST_FIRST_UNIVERSE_V1` rule this
study measured. The live universe is now `COST_FIRST_UNIVERSE_V2_STRUCTURAL_RUG_GUARD`: it adds
`STRUCTURAL_RUG_GUARD_V1`, which needs a ticker registry and a decision time and fails closed
without them (every pool would be `rug_input_unknown`), so it cannot reproduce these numbers.

## Map

- `harness.py`: the v1 harness, kept for provenance.
- `audit/harness_v2.py`: the audited fixes and the audit tests (`t*.py` / `*.out`).
- `leaderboard/harness_final.py`: v2, plus the rug guard, plus CALIB_V1. This is the basis for
  all conclusions.
- `leaderboard/LEADERBOARD.md` and `leaderboard/leaderboard.json`: the final leaderboard.
- `rug/`: the drain census, features, the train-only threshold search and the forward check.
- `calib/`: cost-model calibration against 117 Jupiter-quoted PAPER trades from ledger copies.
- `forensics/`: why the live engines trade so little, and why one token dominated the history.
- `f1_momentum/` to `f9_regime/`: one folder per hypothesis family, with scripts, outputs and
  `strategies.py`.
- `verify_*`: the adversarial verifications of the leaderboard survivors.
- `synthesis/`: the pre-registered forward check and the frozen Lab hypotheses.
- `synthesis_specs.txt` and `workflow_result.json`: the full structured findings.

## Caveats

- 22.8 hours of one market regime, plus a 1.9-hour forward window. That is enough to rule out
  large edges, but not small ones.
- Some agents could not write `REPORT.md` files. Their reports are in `workflow_result.json`
  and `synthesis_specs.txt`.
- Addresses in outputs are public on-chain identifiers. No secrets are included.
