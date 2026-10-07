import assert from 'node:assert/strict';
import test from 'node:test';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import PaperTrainingPanel, { type PaperTrainingSnapshot, type TrainingBook } from '../src/components/PaperTrainingPanel';

const book = (overrides: Partial<TrainingBook> = {}): TrainingBook => ({
  id: 'CONTROL', starting_balance: 500, cash: 500, equity: 500, net_pnl_usd: 0,
  return_pct: 0, completed_trades: 0, open_positions: 0, pending_orders: 0,
  unique_completed_episodes: 0, wins: 0, losses: 0, max_drawdown_pct: 0,
  failed_executions: 0, feasibility: null, rejection_reasons: {}, risk_halt: null,
  positions: [], recent_trades: [], ...overrides,
});
const snapshot = (overrides: Partial<PaperTrainingSnapshot> = {}): PaperTrainingSnapshot => ({
  status: 'WAIT', paper_only: true, active_version: 'v0-control', simulation_count: 0,
  unique_market_episodes: 649, unique_observations: 402692,
  books: [book()], last_training: null,
  versions: [{ id: 'v0-control', status: 'initial', at: 0 }], ...overrides,
});
const render = (data?: PaperTrainingSnapshot) => renderToStaticMarkup(createElement(PaperTrainingPanel, { data }));

test('recordings and successful quote probes never become executed research deals or a verified baseline', () => {
  const html = render(snapshot({
    books: [book({ rejection_reasons: { missing_executable_buy_evidence: 402563 } })],
    quote_probe: { status: 'WAITING_FOR_FRESH_PREFLIGHT', reason: 'safety_not_fresh_pass', attempts: 28, successes: 16 },
    control_comparison: { net_improvement_usd: 0, paired_episode_count: 0, stress_net_pnl_usd: 0, approximate_cluster_mean_95_ci: null },
  }));
  assert.ok(html.includes('Няма отворени или затворени симулирани сделки'));
  assert.ok(html.includes('Начална версия без проверена доходност'));
  assert.ok(html.includes('Липсва свежа, пълна проверка за безопасност.'));
  assert.ok(html.includes('Няма достатъчно резултати за избор на нова версия.'));
  assert.ok(html.includes('Успешната проверка е котировка, не изпълнена сделка.'));
  assert.ok(!html.includes('Паралелни експерименти и проверени версии'));
  assert.ok(!html.includes('Измерена разлика спрямо контрол'));
  const diagnostics = html.slice(html.indexOf('Запис на данни и проверки за изпълнимост'));
  assert.ok(diagnostics.includes((402692).toLocaleString('bg-BG')));
  assert.ok(!html.slice(0, html.indexOf('Запис на данни и проверки за изпълнимост')).includes((402692).toLocaleString('bg-BG')));
});

test('missing reports do not invent a baseline, a zero deal result or a successful quote', () => {
  const html = render();
  assert.ok(html.includes('Изчаква отчет'));
  assert.ok(html.includes('Изчаква се отчет от изследователския процес.'));
  assert.ok(!html.includes('v0-control'));
  assert.ok(!html.includes('Няма отворени или затворени симулирани сделки'));
  assert.ok(!html.includes('Записана двупосочна котировка'));
});

test('active research positions are distinguished from completed results', () => {
  const html = render(snapshot({ status: 'ACTIVE', books: [book({ open_positions: 1, pending_orders: 2 })] }));
  assert.ok(html.includes('Има активни симулации'));
  assert.ok(html.includes('Има активни симулации, но още няма затворен резултат за оценка.'));
  assert.ok(!html.includes('Няма отворени или затворени симулирани сделки'));
});

test('a recorder failure remains visible even when the retained report contains positions', () => {
  const html = render(snapshot({
    status: 'degraded', books: [book({ open_positions: 1 })],
    recorder: { backlog: 8, dropped: 3, processed: 42, error: 'Training worker exited with code 1' },
  }));
  assert.ok(html.includes('Проблем с обучението'));
  assert.ok(html.includes('Training worker exited with code 1'));
  assert.ok(!html.includes('Има активни симулации'));
});

test('historical approval cannot label a reverted initial version as currently validated', () => {
  const html = render(snapshot({ versions: [
    { id: 'v0-control', status: 'initial', at: 0 },
    { id: 'v1-early', status: 'approved_isolated_paper', at: 1000 },
    { id: 'rollback-2', status: 'automatic_rollback', at: 2000 },
  ] }));
  const currentVersionCard = html.slice(html.indexOf('Текуща версия'), html.indexOf('Последна оценка'));
  assert.ok(currentVersionCard.includes('Начална версия без проверена доходност'));
  assert.ok(!currentVersionCard.includes('Приет само'));
});

test('actual closed research results show a measured comparison only with evaluated paired episodes', () => {
  const html = render(snapshot({
    books: [book({ completed_trades: 12 }), book({ id: 'EARLY', completed_trades: 8 })],
    active_version: 'v1-early', versions: [{ id: 'v1-early', status: 'approved_isolated_paper', at: 1000 }],
    control_comparison: { net_improvement_usd: 4, paired_episode_count: 9, stress_net_pnl_usd: 1, approximate_cluster_mean_95_ci: [0.1, 0.4] },
  }));
  assert.ok(html.includes('Има 20 затворени симулирани сделки в отделните портфейли.'));
  assert.ok(html.includes('Приет само за изследователския PAPER портфейл'));
  assert.ok(html.includes('Измерена разлика спрямо контрол: $4.00'));
  assert.ok(html.includes('9 сравнени епизода'));
});
