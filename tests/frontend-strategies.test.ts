import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import LabEntryStatus from '../src/components/LabEntryStatus';
import { isArchivedStrategy, labEntryStatus, labEntryView, partitionLabStrategies, planningCostStatus, quoteFailureStatus } from '../src/lib/labStrategyView';

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

test('cost limits and quote queue failures explain idleness without presenting model estimates as executable prices', () => {
  assert.equal(planningCostStatus({ checked_market_candidates: 0 }), null);
  assert.equal(planningCostStatus({ checked_market_candidates: 1, minimum_model_roundtrip_cost_pct: NaN, maximum_roundtrip_cost_pct: 1.5 }), null);
  const text = planningCostStatus({ checked_market_candidates: 2, fixed_cost_infeasible_candidates: 2,
    minimum_model_roundtrip_cost_pct: 2.49, maximum_roundtrip_cost_pct: 1.5 });
  assert.match(text!, /Всички проверени кандидати надхвърлят лимита в модела/);
  assert.match(text!, /2.49% · лимит 1.50%/);
  assert.match(text!, /нужна е изпълнима котировка/i);
  assert.match(quoteFailureStatus('ENTRY_SEQUENCE_BUSY'), /изчаква ред/);
  assert.match(quoteFailureStatus('PREFLIGHT_PREVIEW_STALE'), /остаряла/);
  assert.match(quoteFailureStatus('UNRECOGNIZED_FAILURE'), /UNRECOGNIZED_FAILURE/);
});

const costlyBook = () => ({ ...book('PRECISION', 250, 250, 'PROMOTED_PAPER'),
  entry_diagnostics: { signal_candidates: 4, affordable_candidates: 0,
    blocked_reason: 'promoted_verified_flow_unavailable',
    promoted_cost_feasibility: { checked_market_candidates: 4, fixed_cost_infeasible_candidates: 4,
      minimum_model_roundtrip_cost_pct: 2.775278, maximum_roundtrip_cost_pct: 1.5,
      best_candidates: [{ symbol: 'EXAMPLE', model_cost_feasible: false }] } },
});

test('all matched expensive funded candidates explain waiting before generic missing-flow status', () => {
  const view = labEntryView(costlyBook());
  assert.match(view.status, /Няма вход в модела/);
  assert.match(view.status, /2.78% > лимит 1.50%/);
  assert.equal(view.costLimited, true);
  assert.match(view.detail!, /всички текущи сигнали/);
  assert.match(view.detail!, /не е изпълнима котировка/i);
  assert.doesNotMatch(view.status, /Чака пресен потвърден поток|80%|печал/);
});

test('partial estimates, unknown costs, and contradictory aggregates never claim all opportunities are too costly', () => {
  const selected = costlyBook();
  const cost = selected.entry_diagnostics.promoted_cost_feasibility;
  const costs = [
    { ...cost, checked_market_candidates: 2, fixed_cost_infeasible_candidates: 2 },
    { ...cost, fixed_cost_infeasible_candidates: 3 },
    { ...cost, unknown_candidates: 1 },
    { ...cost, checked_market_candidates: 0, fixed_cost_infeasible_candidates: 0 },
    { ...cost, minimum_model_roundtrip_cost_pct: null },
    { ...cost, minimum_model_roundtrip_cost_pct: NaN },
    { ...cost, maximum_roundtrip_cost_pct: Infinity },
    { ...cost, minimum_model_roundtrip_cost_pct: 1.2 },
    { ...cost, best_candidates: [{ symbol: 'UNKNOWN', model_cost_feasible: null }] },
    { ...cost, best_candidates: [{ symbol: 'CHEAP', model_cost_feasible: true }] },
  ];
  for (const promoted_cost_feasibility of costs) {
    const view = labEntryView({ ...selected, entry_diagnostics: {
      ...selected.entry_diagnostics, promoted_cost_feasibility,
    } });
    assert.equal(view.status, 'Чака пресен потвърден поток');
    assert.equal(view.costLimited, false);
  }
  assert.equal(labEntryView({ ...selected, entry_diagnostics: {
    ...selected.entry_diagnostics, affordable_candidates: 1,
  } }).costLimited, false);
});

test('cost planning cannot conceal explicit safety failures, account pauses, or a missing market signal', () => {
  const selected = costlyBook();
  for (const blocked_reason of ['promoted_safety_unavailable', 'insufficient_balance', 'promoted_recent_loss_cooldown']) {
    assert.equal(labEntryView({ ...selected, entry_diagnostics: {
      ...selected.entry_diagnostics, blocked_reason,
    } }).costLimited, false);
    assert.equal(labEntryStatus({ ...selected, entry_diagnostics: {
      ...selected.entry_diagnostics, blocked_reason, signal_candidates: 0,
    } }), labEntryStatus({ ...selected, entry_diagnostics: {
      ...selected.entry_diagnostics, blocked_reason,
    } }));
  }
  assert.equal(labEntryStatus({ ...selected, entry_diagnostics: {
    ...selected.entry_diagnostics, signal_candidates: 0,
  } }), 'Няма пазарен сигнал по правилата');
  assert.equal(labEntryView({ ...selected, portfolio_group: 'TEST' }).costLimited, false);
});

test('LAB_ACTIVE_V6: a TEST book whose candidates all exceed the shared cost cap says so instead of idling silently', () => {
  const view = labEntryView({ ...book('TREND', 500, 500, 'TEST'), entry_diagnostics: {
    signal_candidates: 3, matched_candidates: 3, cost_rejected: 3, cost_infeasible_candidates: 3,
    affordable_candidates: 0, max_entry_roundtrip_cost_pct: 1.5, stop_loss_net_pct: 3,
    blocked_reason: 'modeled_roundtrip_cost_limit',
  } });
  assert.equal(view.costLimited, true);
  assert.match(view.status, /Няма вход: 3 от 3 кандидата над лимита на разходите 1\.50%/);
  assert.match(view.detail!, /0\.5 × нетен стоп 3\.00%/);
  assert.match(view.detail!, /не са скрити/);
  assert.match(view.detail!, /не се сваля/i);
  assert.doesNotMatch(view.status, /печал|80%/);
  // Without a positive rejected count the reason falls back to the plain label.
  assert.equal(labEntryStatus({ ...book('TREND', 500, 500, 'TEST'), entry_diagnostics: {
    signal_candidates: 1, blocked_reason: 'modeled_roundtrip_cost_limit',
  } }), 'Няма вход: моделираните разходи надхвърлят лимита');
});

test('LAB_ACTIVE_V6: the fee-and-buffer floor explains a TEST book too, but the funded-only summary never leaks to TEST books', () => {
  const diagnostics = { signal_candidates: 2, affordable_candidates: 0,
    cost_feasibility: { checked_market_candidates: 2, fixed_cost_infeasible_candidates: 2,
      minimum_model_roundtrip_cost_pct: 2.3, maximum_roundtrip_cost_pct: 1.5,
      best_candidates: [{ model_cost_feasible: false }] } };
  const view = labEntryView({ ...book('TREND', 500, 500, 'TEST'), entry_diagnostics: diagnostics });
  assert.equal(view.costLimited, true);
  assert.match(view.status, /2\.30% > лимит 1\.50%/);
  const funded = labEntryView({ ...book('EARLY', 250, 250, 'PROMOTED_PAPER'), entry_diagnostics: {
    ...diagnostics, blocked_reason: 'promoted_verified_flow_unavailable',
  } });
  assert.equal(funded.costLimited, true);
  const legacyOnly = labEntryView({ ...book('TREND', 500, 500, 'TEST'), entry_diagnostics: {
    signal_candidates: 2, affordable_candidates: 0, promoted_cost_feasibility: diagnostics.cost_feasibility,
  } });
  assert.equal(legacyOnly.costLimited, false);
  assert.equal(legacyOnly.status, 'Проверява сигнал, цена и разходи');
});

test('a disconnected or stale backend is distinguished from a loaded strategy without entry opportunities', () => {
  const view = labEntryView(costlyBook(), false);
  assert.equal(view.status, 'Изчаква актуални данни от backend');
  assert.equal(view.costLimited, false);
  assert.match(view.detail!, /не потвърждава текущите възможности/);
  assert.doesNotMatch(view.status, /2.78|Няма вход в модела|Чака пресен/);
});

test('collapsed strategy row exposes modeled cost limits and their provenance without expanding', () => {
  const html = renderToStaticMarkup(createElement(LabEntryStatus, { book: costlyBook(), backendAvailable: true }));
  assert.match(html, /data-testid="lab-entry-status"/);
  assert.match(html, /Няма вход в модела/);
  assert.match(html, /2.78%.*лимит 1.50%/);
  assert.match(html, /не само комисиона/);
  assert.match(html, /нетен стоп 3\.00%.*0\.22 п\.п\./);
  assert.match(html, /impact, мрежа и rent/);
  const stale = renderToStaticMarkup(createElement(LabEntryStatus, { book: costlyBook(), backendAvailable: false }));
  assert.match(stale, /Изчаква актуални данни от backend/);
  assert.doesNotMatch(stale, /2.78%/);
});

test('COST_FIRST: size-only and cooldown-only blocks are named, not shown as a generic wait', () => {
  const diagnostics = { signal_candidates: 0, affordable_candidates: 0 };
  assert.equal(labEntryStatus({ ...book('COST_FIRST_CONTROL', 500, 500, 'TEST'), entry_diagnostics: {
    ...diagnostics, blocked_reason: 'cost_first_size_below_minimum',
  } }), 'Размерът по ликвидност е под минималния вход');
  const cooldown = labEntryView({ ...book('COST_FIRST_SCALED', 500, 500, 'TEST'), entry_diagnostics: {
    signal_candidates: 1, affordable_candidates: 0, blocked_reason: 'reentry_cooldown',
  } });
  assert.equal(cooldown.status, 'Пауза преди повторен вход в същия токен');
  assert.equal(cooldown.costLimited, false);
});

test('TEST books explain the cost floor from the scalar summary alone (best_candidates is funded-only in the compact payload)', () => {
  const view = labEntryView({ ...book('TREND', 500, 500, 'TEST'), entry_diagnostics: {
    signal_candidates: 3, affordable_candidates: 0,
    cost_feasibility: { checked_market_candidates: 3, fixed_cost_infeasible_candidates: 3,
      minimum_model_roundtrip_cost_pct: 2.4, maximum_roundtrip_cost_pct: 1.5 } } });
  assert.equal(view.costLimited, true);
  assert.match(view.status, /2\.40% > лимит 1\.50%/);
});

test('funded rows with the published V6 cap do not repeat the strict cost limit segment', () => {
  const source = readFileSync(new URL('../src/App.tsx', import.meta.url), 'utf8');
  const segment = source.indexOf('строг лимит разходи');
  assert.ok(segment > 0);
  assert.match(source.slice(segment - 200, segment), /diagnostics\.max_entry_roundtrip_cost_pct == null \?/);
});
