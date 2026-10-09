# Owner-requested PAPER holding profiles

`PAPER_HORIZON_EXIT_V1_60_240_720` separates fast trades from longer holds.
This is an owner-authorized experimental change, NOT promotion of the two/six
favorable shadow exits, an optimized win probability or a faster-profit claim.

| Strategy | Profile | Maximum hold | Net target | Arm protection | Giveback | Net stop |
|---|---|---:|---:|---:|---|---:|
| Early | QUICK | 60 minutes | $6 | $2 | max($0.75, 20% of peak) | $5 |
| Momentum | QUICK | 60 minutes | $6 | $2 | max($0.75, 20% of peak) | $5 |
| Precision | SWING | 240 minutes | $15 | $4 | max($1, 20% of peak) | $5 |
| Ultra Precision | HOLDER | 720 minutes | $30 | $4 | max($1, 20% of peak) | $5 |

Dollar thresholds are net of the unchanged modeled entry/exit costs. Stops,
targets and trail floors are triggers, not guaranteed fill prices or profits.
Priority: net stop, net target, armed profit protection, maximum holding time.
Maximum time is counted from the ORIGINAL `opened_at`, never the restart or
amendment. It applies at loss too: a timer must not become indefinite bag-holding.
Fresh exact-pool price, identity and identified SOL denomination are still
required. An overdue stale lot waits for a valid quote, not a fabricated sale.
No historical price/peak, arbitrary zero price or target-clamped outcome.

## Existing lots and financial continuity

Only recognized PAPER lots in the four promoted named books can be amended.
An appended exit_policy_changes event preserves old/new parameters, original
opened_at and prior observed protection. An already recorded V5 adaptive net
peak/floor is carried, and no later observation may LOWER that floor. Unrelated
historical peak_net_pct is never imported. Unknown/LIVE/other/pending cohorts
are untouched. Restart never reapplies the amendment or resets a holding clock.
Entry receipts, quantity, cost basis, funding and all previous closes unchanged.

Early/Momentum lots older than one hour may close on the first valid quote after
deployment, at a gain OR a loss. Precision/Ultra can exit earlier than their
maximum under profit protection or stop. They do not need to remain open for
four/twelve hours. New genuine quality entries freeze the corresponding profile.

Admission, safety, flow, price, cost, daily limits and loss pauses remain hard.
Closing releases exposure but never forces a replacement buy. A losing timeout
still contributes to daily loss and pool-loss memory. No capital refill/reset,
no wallet executor, no market orders with real money. MAIN/personal/HF unchanged.

## Research is separate

The V1 prospective experiment retains its original config/hash/episodes and
three frozen reference profiles. v5 is now labelled REFERENCE, not the current
financial policy. Its model net is a SUM IN USD, not portfolio percentage or
cash that can be withdrawn. It cannot automatically change financial exits.
Current entries still use the unchanged V5 admission label; the new EXIT version
is recorded separately. Entry-quality and exit-policy cohorts must not be mixed.
Previous documentation's no-fixed-timer description applies to v5, not this policy.

Synthetic tests verify mechanics and conservation, not profitability. Independent
prospective evidence is needed before claiming better net returns or win rate.
