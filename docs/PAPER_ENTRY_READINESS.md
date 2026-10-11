# PAPER entry readiness repair — 2026-10-11

Scope: missing new entries, not an assurance of profitability or 50 trades/hour.
No funded-book thresholds, order sizes, fees, exits, balances or history change.

## Confirmed execution defect

FastScalp discarded a pending signal at the first Lab poll after its two-second
minimum delay when the independent market feed had not delivered a subsequent
observation. It then either lost the intent or replaced its original signal time
with a newer one. A two-second execution delay is not a promise that a market
observation will arrive in two seconds.

The pending intent now survives early polls and a temporarily absent exact pool,
but never beyond its ORIGINAL 12-second lifetime. No provider work or fill occurs
before a real subsequent observation with updatedAt >= signal_at + 2 seconds.
On arrival, the existing flow, cost, cash, defense, independent price and risk
checks still run, including their final-clock recheck. Stale, future, wrong-pool
and failed evidence does not authorize entry. New receipts and snapshots identify
PAPER_FAST_PENDING_OBSERVATION_V1; strategy/config hashes and frozen exits are
unchanged. Old pending intents already beyond the deadline are discarded.

## Confirmed misleading diagnostic

A fresh, exact-pool completed RugCheck rejection used to report
promoted_safety_unavailable, suggesting that waiting for a provider would suffice.
It now reports promoted_safety_blocked. A stale, provisional, wrong-identity or
unfinished report still reports unavailable. The admission boolean is unchanged.
The UI explains the completed refusal and displays every last-scan FastScalp
rejection count, not just whichever candidate happened to be visited last.

## Runtime evidence before repair

At 12:26 UTC all four services were healthy but the funded books had no new
entries. Main scan: 90 evaluated, zero signals, zero quotes. The two-hour service
outage legitimately invalidated the old heat history; low-fee pools require 15
minutes of actual observations, some high-fee pools require 60 minutes.

After 12:33 UTC warm-up was no longer the sole blocker. Completed risk reports
for candidate pools were blocked for reported_linked_insiders; low-cost pools
also often lacked the genuine 3-swap/2-wallet buying pulse. These are observed
refusals, not evidence that disabling a risk gate would be profitable. No finding
has been downgraded or unavailable data relabelled as a confirmed fill.

Historical current-policy cohort (small, non-independent samples): Momentum
four closes, zero wins, -$9.7493; Precision eight closes, seven wins, +$18.5999.
The dashboard's combined 13.6% includes older policy outcomes and is not an
estimate of the new policy's future win rate. These samples do not establish edge.

Synthetic regression tests prove waiting/fill timing, expiry, account isolation
and unchanged evidence gates. They do NOT count as live entries or profit.
