# Recorded PAPER outcomes — 2026-10-09, before quality mode

Initial snapshot: **18:03:18.784 UTC / 21:03:18.784 Europe/Kiev**. See the
subsequent genuine winning close below; the initial zero-win count is not
presented as the latest live outcome.

Read-only source: the running Windows runtime's `.runtime/accounts/strategy_lab.json`,
not the source checkout's older account files. Reproduce with:

```powershell
python scripts/audit_lab_outcomes.py C:/Users/Chavd/neomemecoins/.runtime/accounts/strategy_lab.json
```

The local before-change JSON report is `.runtime/paper-quality-before.json`
(includes snapshot SHA-256). Runtime keeps changing; a later audit need not
have the same marks, number of closes or checksum. This is recorded-outcome
forensics, not a replay, alternative exit backtest or performance guarantee.

## Evidence

| Entry cohort | Closes | Wins | Net PAPER PnL |
| --- | ---: | ---: | ---: |
| All four primary books, all policies | 143 | 5 | -$373.8509 |
| Forced capacity test | 106 | 0 | -$290.0473 |
| Ordinary active V3 | 2 | 0 | -$2.3041 |
| Older promoted V3/V4/V5 | 35 | 5 | -$81.4995 |

These cohorts use different exits, notionals and time periods. Their all-time
3.5% win rate is descriptive, not a prediction for the new policy. The capacity
rows cover only 18 distinct pools; simultaneous copies in four books and
repeat entries are correlated. They are not 106 independent experiments.

Among the 106 capacity-test entries, only 6 passed the original strategy market
screen, **0 passed confirmed flow**, 15 passed the full defensive layer and
45 passed their original modeled cost cap. None passed all recorded gates.
The forced-fill implementation correctly labelled these as load tests; they
were not evidence of successful strategic admissions. Removing those bypasses
would refuse those recorded entries. That is not a counterfactual profit claim
or a measured winner-selection benefit.

Capacity entry friction had median **2.2515%**: each $100 order began about
$2.25 down at unchanged spot price after both modeled legs. 69 closes were
the old four-minute timer, 36 the old net stop, one the new -$10 dollar stop.
Only one of the 106 closing rows recorded a peak net PnL >=2%, and none >=30%.
Recorded peaks are sampled marks, not guaranteed achievable alternative fills.

The one new-exit close (PQC) recorded a peak +4.0845% before closing -$10.5149.
Its entry screen, confirmed flow and defense all rejected the load-test entry.
It is one observation, not enough to optimize an exit rule. The -$10 stop
trigger did not cap the booked loss, as intended by the gap-aware model.

All five older winning rows contained confirmed entry-flow evidence and
independent price checks, but many losing rows did too: evidence is necessary,
not sufficient for an edge. Two winning USDF rows copied the same market event.
Other winners were VSOF +$0.713, TDF +$0.5421 and swordinu +$12.8094; these are
not a stable 80% strategy. Small positive unrealized marks in today's UI are
not those historical closes and must not be counted as wins.

### Subsequent pre-deployment result — 18:04:16.871 UTC

Early trade 27, QI, closed **+$30.6503 net / +30.6503%** on the existing
`PAPER_CAPACITY_EXIT_V2_NET30_STOP10` target. Entry spot $0.001144, exit spot
$0.001536; $100 notional, recorded net peak +30.65027%, exit reason
`CAPACITY_TEST_TAKE_PROFIT_NET_USD`. This is an actual PAPER ledger close,
not a synthetic replay or an open green mark, and not a wallet fill.

The updated capacity cohort is **107 closes, 1 win (0.93%), -$259.3970 net**;
all-primary history is 144 closes, 6 wins, -$343.2006 net. This confirms that
the existing $30 net exit can trigger, not that the entry strategy is profitable.
QI also failed its shadow strategy market screen, confirmed flow and defense.
The proposed mandatory gates would have refused this winner too. They exclude
unconfirmed exposure, not demonstrably distinguish winners from losers.
No new cutoff was fitted to QI after seeing its result. The later local snapshot
is `.runtime/paper-quality-predeploy.json`, with its own source SHA-256.

## Scoped prospective change, not a fitted promise

V4 ends load-test admission, reinstates all vetoes/risk budgets, caps roundtrip
cost at 1.5%, requires a stronger size-scaled confirmed buy pulse, prevents
new cross-book duplicate exposure, and pauses losing pools across the cohort.
The new flow cutoffs are declared hypotheses, not values optimized against
the five winners. Distinct market screens, exact-pool freshness, safety, price
identity, contribution ledger and unchanged real cost assumptions remain.

For NEW $100 lots, retain the owner's +$30 NET target and reduce the stop trigger
to -$5 NET. Existing lots keep their frozen exits; no realized or unrealized
loss is erased. A +$30 target on $100 requires a large move after costs and
can conflict with a high win rate or frequent entries. Lowering entry frequency
or the stop does not, by itself, prove increased win rate or expectancy.

Evaluate new-policy closed net PnL, average win/loss, profit factor, drawdown
AND open PnL, over distinct pool episodes and multiple days. Do not promote
based on utilization, tests, a handful of wins, higher closed win rate with
hidden open losses, or in-sample threshold tuning. No real-money authorization
or profitability claim is part of this release.
