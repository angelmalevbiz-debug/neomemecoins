# Independent Fast PAPER Scalper V1

Owner request 2026-10-11: preserve the existing slower books and add a separate
fast strategy. This is a prospective unvalidated hypothesis, not a promise of
profitable trades, a fitted win probability or a LIVE executor.

`PAPER_FAST_SCALP_V1_250_5M` owns `fast_scalp.json` beside the Lab ledger,
an independent exact-pool mark feed and $1,000 of initial virtual experiment
capital. The initial contribution is durable, recorded as not profit and not
a transfer from the four funded books. It never tops up losses, borrows,
resets on a Lab reset, alters old trades, or reclassifies old HF tests.
Unknown/corrupt/config-mismatched ledgers fail closed. Enable entries only
with the pinned Lab V3 cost model (10bps slippage + 10bps latency per leg,
0.0001 SOL network per leg, tiered PumpSwap fees, size-based pool impact).
A cost-model override refuses this experiment, not the four old books.
The resolved cost model and full prospective parameters are in its config hash.
Enable entries only
with literal `NEO_LAB_FAST_SCALP_ENABLED=1` and both execution/engine modes
literal `PAPER`. Disabling entries continues held-lot exits while in PAPER.

The V9 Momentum physical universe is reused without changing its thresholds.
An exact-pool, genuinely observed 60-second return must exceed the full
$250 modeled round-trip friction by 0.25 percentage points, while remaining
below +3%. This past movement is NOT a forecast that the next movement pays
the costs. Require complete confirmed 30-second flow (3 swaps, 2 signing
wallets, positive buys and >=1.2 buy/sell USD), <=12s observations,
structural checks, enforced heat veto (including real history warm-up),
pool loss memory, fresh completed exact-pool risk and independent price.
Ticker reuse remains a veto. Independent price receipt TTL <=30s; safety
TTL <=60s. All short evidence, cash, defense, signal and cost gates are
rechecked after provider work, before commit. At most one provider candidate
per refresh, after existing books have run; no new tape/RPC quota.

Full $250 entries, no size backoff, round-trip cost <=1.5%, free cash covers
notional AND network fee. Balance accounting reserves each lot's actual
committed capital and applies only its realized net close to balance.
No duplicate mint/pool within this book. Other independent accounts may
observe the same pool: that is correlated exposure, not diversified proof.
Winners pause 30s; losses pause >=5min and existing repeated-loss memory
may require longer. Frequency is driven by evidence/cash, not a forced quota.

New lots freeze: +$5 net target, -$7.50 net stop, protection from +$2 with
$0.75 giveback and a non-decreasing floor, maximum 5min from ORIGINAL entry.
Targets, stops and timeouts signal a sale; execution uses a subsequently
observed exact-pool price >=2s later, with fees/impact/slippage/latency.
Prices cannot be clamped to the trigger or invented on a stale feed. Gaps,
missing marks or provider failure can exceed stop and maximum hold.
An entry also waits >=2s and must requalify on its current fresh observation.
Pending exit intent survives restart; no restart extends the holding clock.

The dashboard shows this book separately: capital is not PnL, open marks
are not realized profit, zero closes yield unknown win rate, and all
losses and rejected signals remain visible. The UI includes only the last
20 closes; the dedicated durable ledger keeps full history and receipts.
Synthetic regressions validate mechanics, not profitability. No automatic
promotion or automatic optimization is allowed by this release.
