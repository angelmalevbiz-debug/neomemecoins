# Named strategy PAPER repair — 2026-10-09

The owner authorized a prospective PAPER-only repair, retaining fees, all
historical outcomes and balances. MONEY.zip was inspected, not restored.

`NEO_LAB_FUNDED_ACTIVE_ENABLED=1` opts the four PROMOTED_PAPER books and the
observation scheduler into `FUNDED_ACTIVE_PAPER_V1`. Windows launcher and the
Lab/tape systemd units explicitly enable it. Other engines/TEST books do not
use its admission or exits. Turning the flag off prevents new active entries;
the position list and frozen active exits remain managed until flat.

## Coherent admission and capacity

The previous Early age <=120 min / Ultra <=360 min could never survive the
shared minimum age of 720 min. Active candidates use declared physical pool
conditions, not discovery-score bonuses. Their structural age floor is
scoped to the named Lab book; LP-pullability, fake cap, ticker reuse/coverage,
unknown input, heat veto and per-pool loss memory remain enforced. The tape
only relaxes its age screen for a matching active hypothesis; this supplies
observations, never permission for main or a personal engine to trade.

Four concurrent exact-pool positions, at most $25 and 10% of current book
balance each, at most 50% committed exposure, no leverage. Existing positions
consume capacity; duplicate mint entries in a book are prohibited. Each book
has a rolling cap of 50 new orders/hour (not a minimum). Four-minute maximum
holding time creates capacity for that order target; market evidence, RPC
coverage, safety, cost and risk gates can still yield fewer entries.

Fresh confirmed exact-pool flow, fresh completed full same-pool safety and
independent price identity are required at admission and rechecked before
commit. Fees/network/slippage/impact assumptions are unchanged. Early permits
2.75% modeled roundtrip cost against a 6% NET stop; other books retain the
1.5% cost cap with 4% / 4% / 3% NET stops. No gap loss is clamped to the stop.
These are cost-aware hypotheses, not a validated profitable strategy or
evidence of equivalence to on-chain fills.

New entries stop at 5% of starting allocation net daily loss (UTC day),
including marked open P&L, and retain the 30-minute losing-run pause and the
per-pool 6-hour repeated-loss cooldown. Entry rate never overrides risk.
Active exits use their stored net parameters, fresh exact-pool marks, net
take-profit and net trailing profit protection. They do not read tape flow,
so held active pools don't consume scarce entry observation seats.

## Honest outcome reporting

No old trades are removed or reclassified. All-time results remain all-time;
`stats[book].funded_active` separately reports current-policy trades, net P&L,
win rate (null before any close), rolling entries and closes, exposure and
daily loss. Both full and compact snapshots retain all open positions, plus
the first-position alias for older clients. Restarts deduplicate that alias.

No 80% win rate, 50 valid fills/hour or profit is promised. Acceptance requires
measured forward outcomes after costs, not passing unit tests or a green CI.
