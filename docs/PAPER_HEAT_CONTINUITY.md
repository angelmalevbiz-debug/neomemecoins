# Actual heat-history continuity across PAPER restarts

`PAPER_OBSERVED_HEAT_CONTINUITY_V1` fixes operational downtime, not trading
expectancy. Repeated deployments previously erased `PairHistory`, restarting
the 15-minute crash-window warm-up and, for unpaid >=100 bps pools, the
60-minute paid-profile lookback. The manual `funded_heat_seed.json` often became
stale. A running service now saves its actual bounded history automatically.

Each engine/account, Lab and tape owns a separate `*.pair_history.json` next to
its `*.ticker_registry.json`. Checkpoints contain exact mint/pool identities,
observed prices and paid-profile flags, segment start, first/last sightings,
real gaps and the observation clock. Existing explicitly validated actual
funded seeds can still initialize Lab/tape history; no backdated observation is
generated. There is no access to order placement, capital or ledger history.

The checkpoint is atomic, fsynced, limited to 32 MiB, and written at most every
30 seconds during scans plus a forced clean-stop flush. Unchanged/empty input
does not advance `observed_until`; flushing cannot make stale data fresh. The
retention is evaluated at checkpoint time, including currently absent pairs.
The main engine, Lab and tape clean-stop hooks flush both memories.

On restart the entire file must validate before any pair is restored. Versions,
parameters, identity uniqueness, sample bounds/order, positive finite prices,
boolean flags, paid sightings, segment/gap metadata and timestamps are checked.
The observation clock must not be future and must be at most 120 seconds old.
An individual pair absent for longer than its 120-second gap still warms again,
even if a different pair kept the checkpoint fresh. Recorded crash highs and
paid flags survive a short restart; restoring does not declare any pool safe.

Missing, stale, future or incompatible data never admits a position. Stale or
future snapshots start cold; actual new observations can replace them. A
malformed/unreadable file is retained, not silently overwritten. That service
warms in memory and reports `INVALID_OR_UNREADABLE_SIDECAR` and
`kept_not_overwritten`; an operator may archive that exact sidecar while the
service is stopped. Do not delete account/trade ledgers or edit coverage dates.
Checkpoint failure keeps the previous complete file, reports `save_error`, and
does not stop genuine in-memory observation or position exits.

Inspect `defensive_entry_layer.pair_history` on an engine's `/state`, or
`strategy_lab.activity_config.defensive_entry_state.pair_history` for the Lab.
`load_status=RESTORED_OBSERVATIONS` confirms an actual restore. It does not prove
a signal, fill, win rate or net profit. Thresholds, entry notional, cash limits,
confirmed exact-pool flow, risk/independent-price checks and exits are unchanged.

Regression tests cover exact restoration, paid/crash evidence, per-pair gaps,
hour-vs-short-window coverage, bad/future/oversized files, all-or-nothing loading,
atomic write failure, checkpoint cadence and stale clocks. Synthetic checks
are not live trade or profitability evidence.
