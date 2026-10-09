import assert from 'node:assert/strict';
import test from 'node:test';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import PaperPortfolioHistory, { exitImpactEmergencySummary } from '../src/components/PaperPortfolioHistory';
import { promotedPaperPortfolio } from '../src/lib/paperPortfolioState';
import {
  backendErrorMessage, CACHE_MAX_AGE_MS, clearAccountStateCache, dashboardConnectionStatus,
  initialDashboardConnection, moneyOrUnavailable, paperApiConfiguration, percentageOrUnavailable,
  quotePreparationSummary, readAccountStateCache, RESPONSE_MAX_AGE_MS, tokenDetailForAddress, writeAccountStateCache,
} from '../src/lib/paperDashboardState';

class MemoryStorage implements Storage {
  private items = new Map<string, string>();
  get length() { return this.items.size; }
  getItem(key: string) { return this.items.get(key) ?? null; }
  setItem(key: string, value: string) { this.items.set(key, value); }
  removeItem(key: string) { this.items.delete(key); }
  key(index: number) { return [...this.items.keys()][index] ?? null; }
  clear() { this.items.clear(); }
}
type Snapshot = { balance: number; trades: string[] };
const validSnapshot = (value: unknown): value is Snapshot => !!value && typeof value === 'object'
  && typeof (value as Snapshot).balance === 'number' && Array.isArray((value as Snapshot).trades);
const at = 1_000_000;

test('a newly authenticated user cannot read the previous account or legacy unscoped snapshot', () => {
  const storage = new MemoryStorage();
  writeAccountStateCache(storage, 'alice', { balance: 725, trades: ['alice-private-trade'] }, at);
  storage.setItem('neo-live-state-v1', JSON.stringify({ savedAt: at, state: { balance: 725, trades: ['alice-private-trade'] } }));
  assert.equal(readAccountStateCache(storage, 'bob', at + 1, validSnapshot), null);
  assert.equal(storage.getItem('neo-live-state-v1'), null);
  assert.equal(readAccountStateCache(storage, 'alice', at + 1, validSnapshot)?.state.balance, 725);
  assert.equal(readAccountStateCache(storage, '', at + 1, validSnapshot), null);
});

test('cache metadata must match the authenticated account, even under its storage key', () => {
  const storage = new MemoryStorage();
  storage.setItem('neo-paper-state-v2:bob', JSON.stringify({ version: 2, userId: 'alice', savedAt: at, state: { balance: 725, trades: [] } }));
  assert.equal(readAccountStateCache(storage, 'bob', at, validSnapshot), null);
  assert.equal(storage.getItem('neo-paper-state-v2:bob'), null);
});

test('expired, future-dated and corrupt account snapshots are not hydrated', () => {
  for (const now of [at - 1, at + CACHE_MAX_AGE_MS, at + CACHE_MAX_AGE_MS + 1]) {
    const storage = new MemoryStorage();
    writeAccountStateCache(storage, 'alice', { balance: 500, trades: [] }, at);
    assert.equal(readAccountStateCache(storage, 'alice', now, validSnapshot), null);
  }
  const storage = new MemoryStorage();
  storage.setItem('neo-paper-state-v2:alice', '{invalid-json');
  assert.equal(readAccountStateCache(storage, 'alice', at, validSnapshot), null);
  storage.setItem('neo-paper-state-v2:alice', JSON.stringify({ version: 2, userId: 'alice', savedAt: at, state: { balance: 'fake', trades: [] } }));
  assert.equal(readAccountStateCache(storage, 'alice', at, validSnapshot), null);
});

test('logout removes all PAPER account snapshots while preserving unrelated browser state', () => {
  const storage = new MemoryStorage();
  writeAccountStateCache(storage, 'alice', { balance: 700, trades: [] }, at);
  writeAccountStateCache(storage, 'bob', { balance: 800, trades: [] }, at);
  storage.setItem('neo-live-state-v1', 'old');
  storage.setItem('theme', 'dark');
  clearAccountStateCache(storage);
  assert.equal(storage.length, 1);
  assert.equal(storage.getItem('theme'), 'dark');
});

test('a confirmed reset replaces the cached balance and trade history on reload', () => {
  const storage = new MemoryStorage();
  writeAccountStateCache(storage, 'alice', { balance: 725, trades: ['old-trade'] }, at);
  writeAccountStateCache(storage, 'alice', { balance: 1000, trades: [] }, at + 1);
  assert.deepEqual(readAccountStateCache(storage, 'alice', at + 2, validSnapshot)?.state, { balance: 1000, trades: [] });
});

test('no response and a first failed request remain distinguishable from a loaded account', () => {
  assert.equal(dashboardConnectionStatus(initialDashboardConnection, at), 'CONNECTING');
  assert.equal(dashboardConnectionStatus({ ...initialDashboardConnection, failure: 'Failed to fetch' }, at), 'OFFLINE');
  assert.equal(moneyOrUnavailable(undefined), '—');
  assert.equal(moneyOrUnavailable(0), '$0.00');
  assert.equal(percentageOrUnavailable(undefined), '—');
});

test('a recent cache is visibly cached and never reports an online backend', () => {
  assert.equal(dashboardConnectionStatus({ source: 'cache', receivedAt: at, failure: '' }, at + 1), 'CACHED');
});

test('a failed request after a successful response immediately marks existing data stale', () => {
  const connected = { source: 'network' as const, receivedAt: at, failure: '' };
  assert.equal(dashboardConnectionStatus(connected, at + 1), 'ONLINE');
  assert.equal(dashboardConnectionStatus({ ...connected, failure: 'Failed to fetch' }, at + 2), 'STALE');
  assert.equal(dashboardConnectionStatus({ ...connected, receivedAt: at + 3 }, at + 3), 'ONLINE');
});

test('a hanging poll cannot keep a past successful response online indefinitely', () => {
  const connected = { source: 'network' as const, receivedAt: at, failure: '' };
  assert.equal(dashboardConnectionStatus(connected, at + RESPONSE_MAX_AGE_MS), 'STALE');
  assert.equal(dashboardConnectionStatus(connected, at - 1), 'STALE');
});

test('network errors are readable without inventing an HTTP response or diagnosing one cause', () => {
  const message = backendErrorMessage(new TypeError('Failed to fetch'));
  assert.match(message, /Няма връзка с backend/);
  assert.doesNotMatch(message, /HTTP 200|успешно заредени/);
  assert.match(backendErrorMessage(new Error('Session expired')), /Сесията е изтекла/);
  assert.match(backendErrorMessage(new DOMException('aborted', 'AbortError')), /не отговори навреме/);
  assert.equal(backendErrorMessage(new Error('Backend HTTP 401')), 'Backend HTTP 401');
});

test('backend destinations come only from validated build configuration and permit local HTTP only in development', () => {
  assert.equal(paperApiConfiguration().url, 'https://neo-meme-api.169-58-211-177.sslip.io');
  assert.equal(paperApiConfiguration(undefined, true).url, '/api');
  assert.equal(paperApiConfiguration('   ', true).url, '/api');
  assert.deepEqual(paperApiConfiguration(' https://paper.example.com/ '), { url: 'https://paper.example.com', error: '' });
  for (const url of ['http://paper.example.com', 'javascript:alert(1)', 'https://user:password@paper.example.com/', 'https://paper.example.com/?token=abc', 'https://paper.example.com/#fragment', 'https://paper.example.com/unexpected-path', 'not-a-url']) {
    assert.equal(paperApiConfiguration(url).url, '');
    assert.notEqual(paperApiConfiguration(url).error, '');
  }
  assert.equal(paperApiConfiguration('http://localhost:8789').url, '');
  assert.equal(paperApiConfiguration('http://localhost:8789', true).url, 'http://localhost:8789');
  assert.equal(paperApiConfiguration('http://paper.example.com', true).url, '');
});

function promotedFixture() {
  const trade = (symbol: string, pnl: number, closedAt: number) => ({ symbol, address: `${symbol}-mint`, notional_usd: 50, opened_at: closedAt - 100, closed_at: closedAt, pnl_usd: pnl });
  const book = (name: string, balance: number, history: ReturnType<typeof trade>[], position: { notional_usd: number; open_pnl_usd: number; quote_status: string } | null = null) => ({ name, balance, history, position, starting_balance: 250, portfolio_group: 'PROMOTED_PAPER' });
  return {
    portfolio_setup: { status: 'ACTIVE', strategies: ['a', 'b', 'c', 'd'] },
    books: {
      a: book('Strategy A', 260, [trade('ALPHA', 10, 400)], { notional_usd: 50, open_pnl_usd: -20, quote_status: 'fresh' }),
      b: book('Strategy B', 240, [trade('BETA', -10, 300)]),
      c: book('Strategy C', 260, [trade('GAMMA', 10, 500)]),
      d: book('Strategy D', 250, []),
      test: { ...book('Momentum Rush Brain', 900, [trade('TEST', 650, 600)]), portfolio_group: 'TEST' },
      legacy: { ...book('Legacy exits', 500, [trade('LEGACY', 250, 700)]), portfolio_group: 'PROMOTION_DRAINING' },
    },
    stats: {
      a: { equity: 240, trades: 1, wins: 1 }, b: { equity: 240, trades: 1, wins: 0 },
      c: { equity: 260, trades: 1, wins: 1 }, d: { equity: 250, trades: 0, wins: 0 },
      test: { equity: 900, trades: 1, wins: 1 }, legacy: { equity: 500, trades: 1, wins: 1 },
    },
  };
}

test('main PAPER totals and history use the same selected cohort, excluding TEST and legacy exits', () => {
  const portfolio = promotedPaperPortfolio(promotedFixture());
  assert.ok(portfolio);
  assert.equal(portfolio.startingBalance, 1000);
  assert.equal(portfolio.balance, 1010);
  assert.equal(portfolio.equity, 990);
  assert.equal(portfolio.available, 960);
  assert.equal(portfolio.reserved, 50);
  assert.equal(portfolio.realizedPnl, 10);
  assert.equal(portfolio.unrealizedPnl, -20);
  assert.equal(portfolio.returnPct, -1);
  assert.equal(portfolio.trades, 3);
  assert.equal(portfolio.wins, 2);
  assert.equal(portfolio.openPositions, 1);
  assert.equal(portfolio.valuationStale, false);
  assert.deepEqual(portfolio.history.map(trade => [trade.symbol, trade.strategy_name]), [['GAMMA', 'Strategy C'], ['ALPHA', 'Strategy A'], ['BETA', 'Strategy B']]);
});

test('multi-slot portfolio counts every position without duplicating the first-position alias', () => {
  const base = promotedFixture();
  const first = base.books.a.position!;
  const snapshot = {...base, books: {...base.books, a: {...base.books.a,
    positions: [first, {...first, notional_usd:25, open_pnl_usd:-3, quote_status:'stale'}]}}};
  const result = promotedPaperPortfolio(snapshot)!;
  assert.equal(result.openPositions, 2);
  assert.equal(result.reserved, 75);
  assert.equal(result.available, 935);
  assert.equal(result.valuationStale, true);
});

test('cohort aggregation refuses an incomplete active portfolio and does not count duplicate IDs twice', () => {
  const lab = promotedFixture();
  lab.portfolio_setup.strategies.push('missing');
  assert.equal(promotedPaperPortfolio(lab), null);
  lab.portfolio_setup.strategies = ['a', 'b', 'c', 'd', 'a'];
  assert.equal(promotedPaperPortfolio(lab)?.startingBalance, 1000);
  lab.books.a.balance = Number.NaN;
  assert.equal(promotedPaperPortfolio(lab), null);
  assert.equal(promotedPaperPortfolio(), null);
});

test('a stale open mark remains visible even when the backend omits the valuation flag', () => {
  const lab = promotedFixture();
  lab.books.a.position!.quote_status = 'stale';
  assert.equal(promotedPaperPortfolio(lab)?.valuationStale, true);
  lab.portfolio_setup.status = 'DRAINING';
  assert.equal(promotedPaperPortfolio(lab), null);
});

test('history renders the promoted trades, execution prices and source book without invented zero data', () => {
  const portfolio = promotedPaperPortfolio(promotedFixture())!;
  const trades = portfolio.history.map(trade => ({ ...trade, execution_entry_price: 0.02, entry_price: 99, execution_exit_price: 0.021, exit_price: 100 }));
  const rendered = renderToStaticMarkup(createElement(PaperPortfolioHistory, {
    trades, total: portfolio.trades, loaded: true, promoted: true,
    onSelectAddress: () => {}, formatPrice: value => `$${value.toFixed(4)}`, formatTime: value => String(value),
  }));
  assert.match(rendered, /Главен PAPER портфейл/);
  assert.match(rendered, /GAMMA/);
  assert.match(rendered, /Strategy C/);
  assert.match(rendered, /\$0\.0200/);
  assert.match(rendered, /\$0\.0210/);
  assert.match(rendered, /\+\$10\.00/);
  assert.doesNotMatch(rendered, /Momentum Rush Brain|LEGACY|\$99\.0000|\$100\.0000|→ \$0\.00|NaN/);
});

test('switching the selected mint cannot reuse the previous token chart or flow', () => {
  const detail = { coin: { address: 'mint-a' }, history: [{ price: 99 }], flow: { buy_usd: 50000 } };
  assert.equal(tokenDetailForAddress(detail, 'mint-b'), null);
  assert.equal(tokenDetailForAddress(detail, 'mint-a'), detail);
  assert.equal(tokenDetailForAddress(null, 'mint-a'), null);
});

test('history shows the recorded EXIT_IMPACT_EMERGENCY evidence and invents nothing without it', () => {
  const base = {
    id: 't1', trade_no: 7, symbol: 'SWORD', address: 'mint-a', pairAddress: 'pool-a', opened_at: at, closed_at: at + 26_000,
    execution_entry_price: 0.02, execution_exit_price: 0.0195, notional_usd: 200, pnl_usd: -4.9, pnl_pct: -2.45,
    balance_before: 1000, balance_after: 995.1, exit_reason: 'EXIT_IMPACT_EMERGENCY',
  };
  const record = {
    threshold_pct: 1.35, booked_impact_pct: 1.62, booked_quote: 'confirming',
    entry_preflight: { buy_impact_pct: 0.85, preflight_sell_impact_pct: 1.18 },
    trigger_quote: { impact_pct: 1.5, quoted_at: at + 25_000, route_pools: ['pool-a'], route_matches_entry_pool: true, from_cache: true },
    confirming_quote: { impact_pct: 1.62, quoted_at: at + 25_400, route_pools: ['pool-b'], route_matches_entry_pool: false, from_cache: false },
    confirming_quote_meets_threshold: true,
    liquidity: { entry_usd: 450_000, exit_usd: 450_000, exit_to_entry_ratio: 1 },
  };
  const render = (trades: Parameters<typeof PaperPortfolioHistory>[0]['trades']) => renderToStaticMarkup(createElement(PaperPortfolioHistory, {
    trades, total: trades.length, loaded: true, promoted: false,
    onSelectAddress: () => {}, formatPrice: value => `$${value.toFixed(4)}`, formatTime: value => String(value),
  }));
  const withRecord = render([{ ...base, exit_impact_emergency: record }]);
  assert.match(withRecord, /EXIT_IMPACT_EMERGENCY/);
  assert.match(withRecord, /тригер impact 1\.50% ≥ праг 1\.35%/);
  assert.match(withRecord, /вход buy 0\.85% \/ preflight sell 1\.18%/);
  assert.match(withRecord, /потвърждаваща котировка 1\.62%/);
  assert.match(withRecord, /ликвидност изход\/вход ×1\.00/);
  assert.doesNotMatch(withRecord, /под прага|NaN|undefined/);
  const withoutRecord = render([base]);
  assert.match(withoutRecord, /EXIT_IMPACT_EMERGENCY/);
  assert.doesNotMatch(withoutRecord, /тригер impact|праг|exit-impact-emergency/);
  assert.equal(exitImpactEmergencySummary(null), '');
  assert.match(exitImpactEmergencySummary({ threshold_pct: 0.75, trigger_quote: { impact_pct: 0.9 }, confirming_quote: { impact_pct: 0.6 }, confirming_quote_meets_threshold: false }), /0\.60% \(под прага\)/);
});

test('quote preparation summary labels the rolling window length and the since-engine-start scope', () => {
  const text = quotePreparationSummary({
    quote_attempts: 4, quote_defers: 9,
    quote_preparation_codes: { ENTRY_SEQUENCE_BUSY: 2 },
    quote_preparation_codes_rolling: { ENTRY_SEQUENCE_BUSY: 30, TIMEOUT: 3 },
    quote_preparation_rolling_scans: 25, quote_preparation_rolling_window_minutes: 60,
    quote_preparation_codes_lifetime: { TIMEOUT: 5, ENTRY_SEQUENCE_BUSY: 70 },
    quote_preparation_lifetime_scope: 'SINCE_ENGINE_START',
  });
  assert.match(text, /от старта на engine-а, без запазване при рестарт/);
  assert.match(text, /това сканиране \/ последните 60 мин, 25 сканирания с откази/);
  assert.match(text, /ENTRY_SEQUENCE_BUSY 70 \(2 \/ 30\) · TIMEOUT 5 \(0 \/ 3\)/);
  assert.match(text, /Отложени заради чужд entry lease или чакащ изход: 9 \(не се броят към бюджета от 4 проверки\)/);
  assert.doesNotMatch(text, /undefined|NaN/);
  assert.equal(quotePreparationSummary({}), '');
  assert.equal(quotePreparationSummary(null), '');
  assert.match(quotePreparationSummary({ quote_preparation_codes_lifetime: { TIMEOUT: 1 } }), /последния прозорец, 0 сканирания/);
});
