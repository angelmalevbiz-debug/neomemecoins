# Named strategy PAPER repair — 2026-10-09

The owner authorized a prospective PAPER-only repair, retaining fees, all
historical outcomes and balances. MONEY.zip was inspected, not restored.

`NEO_LAB_FUNDED_ACTIVE_ENABLED=1` opts the four PROMOTED_PAPER books and the
observation scheduler into `FUNDED_ACTIVE_PAPER_V3_MOMENTUM_PULSE_100`. Windows launcher and the
Lab/tape systemd units explicitly enable it. Other engines/TEST books do not
use its admission or exits. Turning the flag off prevents new active entries;
the position list and frozen active exits remain managed until flat.

## Coherent admission and capacity

The previous Early age <=120 min / Ultra <=360 min could never survive the
shared minimum age of 720 min. Active candidates use declared physical pool
conditions, not discovery-score bonuses. Their structural age floor is
scoped to the named Lab book; LP-pullability, fake cap, ticker reuse/coverage,
unknown input, heat veto and per-pool loss memory remain enforced. Active PAPER
ticker continuity requires 60 minutes rather than main's unchanged 24 hours;
all persisted sightings and the reuse veto remain. This explicitly trades
less continuity evidence for bounded PAPER research, not LIVE safety proof. The tape
only applies these scoped age/continuity thresholds for a matching active hypothesis; this supplies
observations, never permission for main or a personal engine to trade.

The owner requested larger entries: exactly $100, without a $25 fallback,
and at most 50% of the current book balance per entry. At most four concurrent
exact-pool positions and 50% total committed exposure, no leverage. With the
existing $201–250 balances this normally means **one $100 position**, not four.
Insufficient remaining exposure blocks entry instead of buying a small remainder.
V1/V2 positions retain frozen exits; prior active losses and orders still consume
daily/hourly risk limits and losing-run cooldown. V3 performance is reported separately.
Existing positions
consume capacity; duplicate mint entries in a book are prohibited. Each book
has a rolling cap of 50 new orders/hour (not a minimum). Four-minute maximum
holding time creates capacity for that order target; market evidence, RPC
coverage, safety, cost and risk gates can still yield fewer entries.

Fresh confirmed exact-pool flow, fresh completed full same-pool safety and
independent price identity are required at admission and rechecked before
commit. Fees/network/slippage/impact assumptions are unchanged. Early permits
2.75% modeled roundtrip cost against a 6% NET stop; Momentum permits 2% against
a 4% NET stop. Precision and Ultra retain a 1.5% cap with 4% / 3% NET stops.
No gap loss is clamped to the stop.
These are cost-aware hypotheses, not a validated profitable strategy or
evidence of equivalence to on-chain fills.

V3 prospectively changes **only Momentum's physical candidate screen**: m5
0–15% rather than 0.5–15%, h1 −10–40% rather than 0–40%, five-minute buy/sell
ratio >=1 rather than >=1.2. It still needs independently observed, fresh
30-second flow with >=3 swaps, >=2 transaction-signing wallets and buy USD
>=1.2x sell USD, plus full same-pool safety, price, heat and risk checks. The
2% cost ceiling is half the unchanged 4% net stop, not a removal of fees.
This recognizes emerging buy pulses before a lagged candle confirms; it is
not calibrated against winners, and no profitable expectancy is claimed.

### CPI coverage repair

Successful PumpSwap CPI can use program-authorized PDA signers, not a message
signer: [Solana CPI](https://solana.com/docs/core/cpi) and
[official PumpSwap IDL](https://github.com/pump-fun/pump-public-docs/blob/main/idl/pump_amm.json).
The recorder previously marked these exact decoded swaps as unclassified,
invalidating the whole pool's current window. With matching inner instruction,
parent context, stack height, successful execution, exact event and token legs,
they now receive a known terminal classification. Their non-wallet quality flag
is **retained**: they never count as independent wallets or admission flow.
Unknown actors, missing CPI context, shadow decoders and other defects still
degrade coverage. Existing terminal database rows are not rewritten.

New entries stop at 5% of starting allocation net daily loss (UTC day),
including marked open P&L, and retain the 30-minute losing-run pause and the
per-pool 6-hour repeated-loss cooldown. Entry rate never overrides risk.
Active exits use their stored net parameters, fresh exact-pool marks, net
take-profit and net trailing profit protection. They do not read tape flow,
so held active pools don't consume scarce entry observation seats.

`scripts/build_funded_heat_seed.py` can prepare `funded_heat_seed.json` in the
runtime accounts directory from the recorder journal (input opened read-only,
bounded byte/time span, no history-ledger writes). Lab/tape may replay only a
fresh (<2 min), completely validated cache of actual recent main-feed prices
and paid-profile source flags. Future, stale, held-only, missing-source or
malformed records never supply synthetic warm-up. Real gaps still reset the
pair's coverage. A missing/stale/invalid cache retains the normal warm-up.

## Honest outcome reporting

No old trades are removed or reclassified. All-time results remain all-time;
`stats[book].funded_active` separately reports current-policy trades, net P&L,
win rate (null before any close), rolling entries and closes, exposure and
daily loss. Both full and compact snapshots retain all open positions, plus
the first-position alias for older clients. Restarts deduplicate that alias.

No 80% win rate, 50 valid fills/hour or profit is promised. Acceptance requires
measured forward outcomes after costs, not passing unit tests or a green CI.
