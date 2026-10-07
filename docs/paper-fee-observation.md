# Complete Pump event fee observations

The recorder now preserves fee facts from a fully recognized Pump Buy or Sell
event whose gross and user quote quantities reconcile exactly. The caller
matches the actual onchain instruction name, exact pool, actor and token
accounts. Successful complete transaction coverage, valid signer and currency
reference remain required for authenticated evidence. Reversed token direction
does not reverse the fee event's onchain instruction side.
An unknown instruction referencing the pool prevents fee authentication while
the existing recorder can still retain independently recognized swaps.

Only the complete public IDL schema and the explicit SDK 2.0.0 creator-unclaimed
tail are recognized. Unknown, truncated or inconsistent fee schemas cannot
create fee evidence, while an independently proven supported swap remains
recorded. Unsupported cashback/routing remains unknown. Holder rewards repeat
the creator charge and are counted once. Integer fee rounding is preserved;
raw u64 amounts are decimal strings in JSON.

These observations do not change PAPER entry or exit fees, cost limits,
candidate rules, risk limits or accounting. They are not executable quotes or
permission to trade. A lower fee must not be inferred from a pool name, token
age, market-cap score or a default coin-creator key.

An October 7 read-only audit proved one observed TWEETCRAFT pool noncanonical
using its full pool PDA and a creator comparison against the canonical
authority. The current fee model applies canonical tiers to PumpSwap as a
fallback, so its estimate can overstate that pool's fees. Complete current fee
evidence was unavailable in that audit. Recording reconciled amounts permits
subsequent evaluation without silently lowering costs or reclassifying old
trades. No current live fee rate is established by synthetic parser fixtures.

Sources: [official fee selection](https://raw.githubusercontent.com/pump-fun/pump-public-docs/main/docs/FEE_PROGRAM_README.md),
[current event IDL](https://raw.githubusercontent.com/pump-fun/pump-public-docs/main/idl/pump_amm.json),
[holder rewards](https://raw.githubusercontent.com/pump-fun/pump-public-docs/main/docs/HOLDER_REWARDS_README.md).

The dashboard also distinguishes open positions from completed trades. Opening
a position does not increment the closed-trade denominator or the win rate.
