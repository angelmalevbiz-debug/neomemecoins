import assert from 'node:assert/strict';
import test from 'node:test';
import { isArchivedStrategy, labEntryStatus, partitionLabStrategies } from '../src/lib/labStrategyView';

const book = (id: string, balance: number, starting = 250, group = 'TEST') => ({
  id, balance, starting_balance: starting, portfolio_group: group,
});

test('retired entries leave the active view but retain their ledger and open position for archive inspection', () => {
  const held = { ...book('loser', 210), history: [{ pnl_usd: -40 }], position: { symbol: 'HELD' },
    strategy_lifecycle: { status: 'retired', entry_enabled: false, position_management_enabled: true } };
  const groups = partitionLabStrategies([held, { ...held, ...book('funded', 250, 250, 'PROMOTED_PAPER'), strategy_lifecycle: undefined }]);
  assert.deepEqual(groups.funded.map(row => row.id), ['funded']);
  assert.equal(groups.research.length, 0);
  assert.strictEqual(groups.archived[0], held);
  assert.equal(groups.archived[0].position?.symbol, 'HELD');
  assert.equal(groups.archived[0].history[0].pnl_usd, -40);
  assert.equal(labEntryStatus(held), 'Новите входове са спрени');
});

test('strategy results are compared by return rather than unequal account allocations', () => {
  const groups = partitionLabStrategies([book('large-flat', 1000, 1000), book('small-profitable', 275), book('unvalidated', 250)]);
  assert.deepEqual(groups.research.map(row => row.id), ['small-profitable', 'large-flat', 'unvalidated']);
  const withMarkedReturns = partitionLabStrategies([book('large-flat', 1000, 1000), book('small-profitable', 275)], {
    'small-profitable': { return_pct: -5 }, 'large-flat': { return_pct: 2 },
  });
  assert.deepEqual(withMarkedReturns.research.map(row => row.id), ['large-flat', 'small-profitable']);
});

test('no evidence and no matching market signal never masquerade as a profitable strategy or a flow failure', () => {
  const selected = book('EARLY', 250, 250, 'PROMOTED_PAPER');
  assert.equal(isArchivedStrategy(selected), false);
  assert.equal(labEntryStatus(selected), 'Чака данни за входа');
  assert.equal(labEntryStatus({ ...selected, entry_diagnostics: { signal_candidates: 0 } }), 'Няма пазарен сигнал по правилата');
  assert.equal(labEntryStatus({ ...selected, entry_diagnostics: { signal_candidates: 1, blocked_reason: 'promoted_verified_flow_unavailable' } }), 'Чака пресен потвърден поток');
});

test('custom inactive books remain in archive and separate Astra panel is not duplicated', () => {
  const custom = { ...book('custom', 230), runtime_compatibility: { status: 'preserved_inactive' } };
  const groups = partitionLabStrategies([custom, book('ASTRA_6_BRAIN', 250)]);
  assert.deepEqual(groups.archived.map(row => row.id), ['custom']);
  assert.equal(groups.research.length, 0);
});
