# Owner-requested PAPER holding profiles

## Fast-scalping continuation

New Momentum entries use `PAPER_MOMENTUM_SCALP_EXIT_V1_15`: SCALP, maximum
15 minutes, $6 net target, $2 protection arm and $5 net stop. Other profiles
below are unchanged. Existing V1 horizon positions keep their frozen exits,
original timestamps and observed profit protection. No holding lot is sold
just to create a scalp and no capital or daily loss limit is reset.

Momentum already has an independent entry path: physical PumpSwap/SOL market
screen plus fresh exact-pool confirmed 30-second buy pressure (at least six
swaps, four wallets, $300 buys, $100 net buys and 1.5 buy/sell ratio). The
existing full defense, safety, independent price and 1.5% round-trip cost cap
remain mandatory. This change does not claim a newly validated entry edge.

The shared bounded market feed now retains candidates for these four funded
books as well as main. Previously the retention predicate consulted only main;
therefore a valid funded candidate could fall below the 90-row cut. This is a
coverage repair, not evidence that it caused a particular missed trade. Main's
quote prewarm/entry predicates remain unchanged. Selection is still bounded;
it does not guarantee retention of every candidate when more than 90 qualify.

The UI reports the next UTC risk day when daily losses block entries. Open
marks remain part of the next day's risk too. A clock rollover is not capital
replenishment or permission to skip signals. No new entry, higher win rate or
faster profit is guaranteed. Losing timeouts and gaps/slippage remain possible.

## Original V1 horizons (retained on existing V1 lots)

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
