# Prospective profit-speed comparison — PAPER_EXIT_RESEARCH_V1

Subsequent owner-authorized financial exits are now QUICK/SWING/HOLDER under
[PAPER_HORIZON_EXIT_V1_60_240_720](PAPER_HORIZON_EXITS.md). The experiment below
keeps its ORIGINAL frozen profiles/hash and samples; v5 is a REFERENCE, not the
current financial policy. Shadow net dollars are not portfolio return percent
or open financial positions that can be closed again. No automated promotion.

The owner requested a more developed program and faster net profit. The live
V5 admission/exits have only minutes of evidence; rewriting them again would
not establish improvement. The archived 2026-10-08 edge study found no robust
positive-after-cost alternative. This release adds a bounded, persistent,
same-entry comparison to find a reviewable alternative from FUTURE observations.
It does not claim to have found one, automatically promote it or reset risk.

## Frozen alternatives

| Profile | Upper net target | Arm protection | Giveback | Initial net stop |
|---|---:|---:|---:|---:|
| BASELINE_30_LOCK4 | $30 | $4 | max($1, 20% of peak) | $5 |
| QUICK_6_LOCK2 | $6 | $2 | max($0.75, 20% of peak) | $5 |
| RUNNER_LOCK4 | None | $4 | max($1, 25% of peak) | $5 |

These are declared hypotheses, not thresholds fitted to winners. Only the
baseline V5 still controls actual named-book PAPER exits. Three shadow legs
share each actual entry's fixed quantity, cost basis and market identity;
they never create orders, allocate/refill capital or mutate financial books.
No extra quote requests, price probes, network calls or tape seats are added.

After a trigger, a shadow exit uses the next fresh exact-pool observation at
least 2 seconds later with a CHANGED observed spot price. It does not fill at
the trigger, reuse an unchanged cached price as a new fill or clamp to a target.
The original modeled fees, impact, slippage, latency and network costs apply;
an additional 50 bps per leg ($1 on $100) is measured as stress. These remain
observed-pool modeled outcomes, NOT executable wallet quotes or live fills.
The approximation is not the calibrated historical research harness and
cannot on its own establish an executable edge. Delayed observations can
produce larger gains OR larger losses than a threshold. A model/config change
fails research closed; it never clears samples or blocks actual exits.

## Provenance and missing data

- `FULL_QUALITY_ENTRY`: registered at a genuine V4/V5 quality entry, not a
  capacity-test fill. These can eventually contribute to the comparison.
- `RESUMED_OPEN_LOT`: starts from a fresh quote after this study activates,
  including old capacity lots. Useful for observing exit mechanics, but
  excluded from improvement review because the original path is missing.
- Historical peaks are never imported. Restart preserves shadow peaks,
  pending triggers, frozen cost hash and observation history.
- Paths continue from the shared exact-pool feed after the actual lot closes.
  A missing/rotated market is not sold at zero or its old price. No new seat
  is pinned just for research. A gap over 60 seconds blocks that full-entry
  episode's use for review, even if all three legs later finish profitably.
- At a 60-minute OBSERVATION horizon, unresolved legs become CENSORED, not
  forced financial closes or zero-PnL rows. Mature incomplete paths remain
  visible and block review. Early-finished rows wait until the same horizon
  before being eligible, so short lucky paths cannot dominate early selection.
- At most 64 active / 256 retained completed episodes, 32 latest trace points
  per episode. Cumulative refusal/error counters prevent acceptance after
  lost observation coverage. Closed research rows may leave the bounded
  descriptive window; financial history is never trimmed by this module.

## What the program evaluates

The live report shows modeled closes, unknown WR before closes, average win,
average loss, profit factor, net after costs, median observed holding time,
pending/censored counts and unresolved last marked net (not booked profit).
It distinguishes resumed/full-entry paths and does not add shadow profit to
the real PAPER balance, portfolio PnL or actual trade count.

Minimum manual-review gate: 150 fully paired mature quality entries, 25 pools,
5 UTC days, complete observed coverage, maximum pool share 20%, positive lower
pool-bootstrap bounds for both stressed net and improvement over baseline,
and at least 5% faster median observed exit time. The 500-resample pool-block
intervals are descriptive diagnostics, not a probability of future success;
market/day dependence, overlapping entries and multiple comparisons remain.
A passing shadow result is `CANDIDATE_FOR_MANUAL_REVIEW_NOT_VALIDATED`, never
an automatic order permission or production strategy change. Independent
future confirmation and executable quote evidence would still be needed.

Reason for freezing a small declared comparison: repeated searches on the
same historical noise can select attractive but spurious strategies. See
Bailey et al., [The Probability of Backtest Overfitting](https://www.davidhbailey.com/dhbpapers/backtest-prob.pdf).
This module does not implement their CSCV/PBO procedure or claim that the
minimum counts guarantee statistical power or profitability.

## Runtime and safety

State is an isolated root `exit_research` in the existing durable Lab file;
the dashboard receives only `exit_research_summary`, never full traces.
Reports update at most every 10 seconds to bound analysis in the trading loop.
PAPER quality opt-in is required; MAIN, personal, HF/other books, current V5
exits/admission, balances, histories, fees, capital and daily limits unchanged.
Research failures are published and cannot prevent actual stop/profit exits.
The deployment backs up the Lab ledger and verifies the new locked module.
Tests use synthetic fixtures and temporary accounts, not a profit backtest.
