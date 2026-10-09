import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import LabHighFrequencyPanel from '../src/components/LabHighFrequencyPanel';
import {
  HF_BADGE, HF_CAP_TEXT, HF_DISABLED, HF_EMPTY, HF_ERROR_PREFIX, HF_NOT_STARTED_PREFIX, HF_NOTE, HF_TITLE, hfAbsentText,
  hfCancelText, hfCapBar, hfErrorText, hfGapText, hfKillText, hfMoney, hfSnapshotOrNull, hfStatusText,
  type LabHighFrequencyBook, type LabHighFrequencySnapshot,
} from '../src/lib/labHighFrequencyView';

const book = (id: string, changes: Partial<LabHighFrequencyBook> = {}): LabHighFrequencyBook => ({
  id, name: id === 'HF_RND_E95' ? 'HF контрола · случайни входове' : id === 'HF_QUIET_E95' ? 'HF тихи пулове' : 'HF дъно 15 мин',
  role: id === 'HF_RND_E95' ? 'random_control' : 'hypothesis', control: id === 'HF_RND_E95' ? null : 'HF_RND_E95',
  status: 'active', trades_last_60m: 47, trades_today: 120, open_slots: 2, max_slots: 3,
  booked_today_usd: -80.2, net50_today_usd: -110.4, booked_total_usd: -80.2, net50_total_usd: -110.4,
  mean_booked_pct: -2.7, mean_net50_pct: -3.7, win_rate_net50_pct: 1.5,
  usd_per_hour_today: { booked: -32.8, net50: -44.4 }, cap: { limit_usd: 100, used_usd: 80.2, tripped_at: null },
  kill: { closes: 120, next_checkpoint: 500, utc_days_with_closes: 1, min_utc_days: 3, applies: id !== 'HF_RND_E95', last: null },
  gap_vs_control: id === 'HF_RND_E95' ? null : { gap_net50_pct: 0.14, ci95: [-0.13, 0.36], within_fee_bucket_pct: { 'fee<=50': -0.06, 'fee55-95': 0.04 } },
  top_pair_share: 0.115, pairs: 20, cancels_today: { hf_entry_no_next_observation: 3, restart: 1 },
  last_closes: [{ seq: 9, symbol: 'CATE', kind: 'HF_TIME_120', pnl_usd: -0.69, pnl_pct: -2.74, net50_usd: -0.93, fee_bps: 30, hold_s: 145, flags: [] }],
  ...changes,
});

const snapshot = (books: LabHighFrequencyBook[]): LabHighFrequencySnapshot => ({ version: 'LAB_HIGH_FREQUENCY_V1', books });

test('the HF panel carries the title, the amber expected-loss badge and the honest note', () => {
  const html = renderToStaticMarkup(createElement(LabHighFrequencyPanel, {
    data: snapshot([book('HF_RND_E95'), book('HF_QUIET_E95'), book('HF_DIP15_E95')]),
  }));
  assert.equal(HF_TITLE, 'Висока честота (HF)');
  assert.equal(HF_BADGE, 'ЕКСПЕРИМЕНТ · ОЧАКВА СЕ ЗАГУБА');
  assert.match(html, /Висока честота \(HF\)/);
  assert.match(html, /ЕКСПЕРИМЕНТ · ОЧАКВА СЕ ЗАГУБА/);
  assert.match(html, /amber/);
  assert.equal(HF_NOTE, 'Около 50 сделки/час на книга, $25 на сделка, изход след 2 мин. Цени: следващото опресняване на DexScreener + моделирани разходи (CALIB_V1, net50); не е изпълнима котировка. Изследването очаква ≈ −3,6% на сделка (≈ $37–44/час на книга). Измерване, не стратегия; без промоция.');
  assert.ok(html.includes(HF_NOTE));
  for (const name of ['HF контрола · случайни входове', 'HF тихи пулове', 'HF дъно 15 мин']) assert.ok(html.includes(name));
  assert.match(html, /2\/3/);
  assert.match(html, /Последни 10 сделки/);
  assert.match(html, /CATE/);
  assert.match(html, /−\$80\.20/);
});

test('cap, session, retired, warming and degraded states are named in Bulgarian', () => {
  assert.equal(hfStatusText({ status: 'cap' }), HF_CAP_TEXT);
  assert.equal(HF_CAP_TEXT, 'спрян до 00:00 UTC');
  assert.equal(hfStatusText({ status: 'session_closed', open_hour: 7 }), 'Сесията е затворена до 07:00 UTC');
  assert.equal(hfStatusText({ status: 'session_closed', open_hour: 14 }), 'Сесията е затворена до 14:00 UTC');
  assert.match(hfStatusText({ status: 'retired', reason: 'kill_checkpoint' }), /Спряна окончателно: статистическата проверка/);
  assert.match(hfStatusText({ status: 'retired', reason: 'capital_floor' }), /50%/);
  assert.match(hfStatusText({ status: 'warming' }), /Загрява/);
  assert.match(hfStatusText({ status: 'degraded' }), /без нови поръчки/);
  assert.equal(hfStatusText({ status: 'active' }), 'Търгува');
  const html = renderToStaticMarkup(createElement(LabHighFrequencyPanel, {
    data: snapshot([book('HF_RND_E95', { status: 'cap', cap: { limit_usd: 100, used_usd: 101.3, tripped_at: 1 } }),
      book('HF_QUIET_E95', { status: 'session_closed', open_hour: 21 }),
      book('HF_DIP15_E95', { status: 'retired', reason: 'kill_checkpoint', control_retired: true })]),
  }));
  assert.match(html, /спрян до 00:00 UTC/);
  assert.match(html, /до 21:00 UTC/);
  assert.match(html, /Спряна окончателно/);
  assert.match(html, /Контролата е спряна/);
  assert.match(html, /width:100%/);
});

test('the fallback says there is no HF data when the key is absent or malformed', () => {
  for (const data of [undefined, null, {}, { books: 'x' }]) {
    const html = renderToStaticMarkup(createElement(LabHighFrequencyPanel, { data }));
    assert.ok(html.includes(HF_EMPTY));
    assert.equal(HF_EMPTY, 'Няма HF данни');
    assert.match(html, /ОЧАКВА СЕ ЗАГУБА/);
  }
  assert.equal(hfSnapshotOrNull({ books: [] })?.books.length, 0);
  assert.equal(hfSnapshotOrNull('nope'), null);
});

test('without a view the panel says why: disabled, a failed start with its error, or no data yet', () => {
  // Review finding: a failed HF build left only 'Няма HF данни' while strategy_lab.hf_error and
  // activity_config.lab_high_frequency (enabled, running, build) were in the same /state payload.
  const failed = { enabled: true, running: false, build: { attempts: 3, failures: 3, last_error: 'build: PermissionError: state.json' } };
  const text = hfAbsentText('build: UnicodeDecodeError: invalid continuation byte', failed);
  assert.equal(HF_NOT_STARTED_PREFIX, 'HF не стартира');
  assert.equal(text, 'HF не стартира: build: UnicodeDecodeError: invalid continuation byte. Lab опитва отново всяка минута (опити досега: 3).');
  // The build status alone (hf_error cleared by a later loop) still names the error.
  assert.match(hfAbsentText(undefined, failed), /^HF не стартира: build: PermissionError: state\.json\./);
  assert.equal(hfAbsentText(undefined, { enabled: false, running: false }), HF_DISABLED);
  assert.equal(HF_DISABLED, 'HF книгите са изключени (NEO_LAB_HF_ENABLED=0).');
  assert.equal(hfAbsentText(undefined, { enabled: true, running: false, build: { attempts: 0, last_error: null } }), HF_EMPTY);
  assert.equal(hfAbsentText(), HF_EMPTY);
  const html = renderToStaticMarkup(createElement(LabHighFrequencyPanel, {
    data: undefined, error: 'build: UnicodeDecodeError: invalid continuation byte', config: failed,
  }));
  assert.match(html, /HF не стартира: build: UnicodeDecodeError/);
  assert.match(html, /опити досега: 3/);
  assert.match(html, /text-red-200/);
  assert.doesNotMatch(html, /Няма HF данни/);
  const disabled = renderToStaticMarkup(createElement(LabHighFrequencyPanel, { config: { enabled: false } }));
  assert.ok(disabled.includes(HF_DISABLED));
  assert.doesNotMatch(disabled, /text-red-200/);
  // A view, when present, wins over the absent texts.
  const running = renderToStaticMarkup(createElement(LabHighFrequencyPanel, {
    data: snapshot([book('HF_RND_E95')]), error: 'build: old', config: { enabled: true, running: true },
  }));
  assert.doesNotMatch(running, /HF не стартира/);
});

test('a recent HF failure is shown above the books and nothing is shown without one', () => {
  const failing: LabHighFrequencySnapshot = {
    ...snapshot([book('HF_RND_E95')]),
    last_error: { text: 'update: PermissionError: journal', at: 1, count: 4, consecutive_loops: 3 },
  };
  assert.equal(HF_ERROR_PREFIX, 'Грешка в HF');
  const text = hfErrorText(failing)!;
  assert.match(text, /^Грешка в HF: update: PermissionError: journal \(поредни цикли с грешка: 3\)/);
  const html = renderToStaticMarkup(createElement(LabHighFrequencyPanel, { data: failing }));
  assert.match(html, /data-testid="hf-error"/);
  assert.match(html, /PermissionError: journal/);
  assert.equal(hfErrorText(snapshot([book('HF_RND_E95')])), null);
  assert.equal(hfErrorText({ last_error: { text: '' } }), null);
  assert.equal(hfErrorText(null), null);
  const clean = renderToStaticMarkup(createElement(LabHighFrequencyPanel, { data: snapshot([book('HF_RND_E95')]) }));
  assert.doesNotMatch(clean, /hf-error/);
});

test('cap bar, kill evidence, gap with fee buckets and cancels never overstate', () => {
  assert.deepEqual(hfCapBar({ cap: { limit_usd: 100, used_usd: 37.5 } }), { usedPct: 37.5, text: 'Дневен лимит на загубата: $37.50 от $100' });
  assert.equal(hfCapBar({ cap: { limit_usd: 100, used_usd: 250 } }).usedPct, 100);
  assert.equal(hfCapBar({}).usedPct, 0);
  assert.match(hfKillText(book('HF_RND_E95')), /Контролата спира/);
  assert.match(hfKillText(book('HF_QUIET_E95')), /120\/500 сделки, 1 дни/);
  const evaluated = hfKillText(book('HF_DIP15_E95', { kill: { closes: 500, next_checkpoint: 750, utc_days_with_closes: 4, min_utc_days: 3,
    last: { met: true, mean_net50_usd: -0.91, ci95_net50_usd: [-0.96, -0.85], gap_net50_pct: 0.07, control_ci_half_width_pct: 0.19 } } }));
  assert.match(evaluated, /−\$0\.910/);
  assert.match(evaluated, /спира\./);
  const gap = hfGapText(book('HF_QUIET_E95'))!;
  assert.match(gap, /\+0\.14 pp/);
  assert.match(gap, /CI95 −0\.13 pp … \+0\.36 pp/);
  assert.match(gap, /такса ≤ 50 bps: −0\.06 pp/);
  assert.match(gap, /такса 55–95 bps: \+0\.04 pp/);
  assert.equal(hfGapText(book('HF_RND_E95')), null);
  assert.match(hfCancelText({ hf_entry_no_next_observation: 3, restart: 1 }), /няма ново наблюдение до 60 с 3 · рестарт 1/);
  assert.match(hfCancelText({}), /няма/);
  assert.equal(hfMoney(-0.5), '−$0.50');
  assert.equal(hfMoney(null), '—');
});

test('the dashboard shows the HF panel in the Strategies section outside the advanced details', () => {
  const app = readFileSync(new URL('../src/App.tsx', import.meta.url), 'utf8');
  assert.match(app, /high_frequency\?: LabHighFrequencySnapshot/);
  assert.match(app, /hf_error\?: string/);
  assert.match(app, /activity_config\?: \{ lab_high_frequency\?: LabHighFrequencyConfig \}/);
  const panel = app.indexOf('<LabHighFrequencyPanel data={state?.strategy_lab?.high_frequency} '
    + 'error={state?.strategy_lab?.hf_error} config={state?.strategy_lab?.activity_config?.lab_high_frequency} />');
  const advancedStart = app.indexOf('{showAdvanced && <div');
  const strategiesTable = app.indexOf('partitionLabStrategies(Object.values(state?.strategy_lab?.books');
  assert.ok(panel > 0 && advancedStart > 0 && strategiesTable > 0);
  assert.ok(panel > advancedStart && panel < strategiesTable);
  const advancedBlock = app.slice(advancedStart, panel);
  assert.ok(advancedBlock.trimEnd().endsWith('</div>}'));
});
