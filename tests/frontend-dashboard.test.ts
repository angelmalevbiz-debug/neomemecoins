import assert from 'node:assert/strict';
import test from 'node:test';
import {
  backendErrorMessage, CACHE_MAX_AGE_MS, clearAccountStateCache, dashboardConnectionStatus,
  initialDashboardConnection, moneyOrUnavailable, paperApiConfiguration, percentageOrUnavailable,
  readAccountStateCache, RESPONSE_MAX_AGE_MS, writeAccountStateCache,
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
  assert.deepEqual(paperApiConfiguration(' https://paper.example.com/ '), { url: 'https://paper.example.com', error: '' });
  for (const url of ['http://paper.example.com', 'javascript:alert(1)', 'https://user:password@paper.example.com/', 'https://paper.example.com/?token=abc', 'https://paper.example.com/#fragment', 'https://paper.example.com/unexpected-path', 'not-a-url']) {
    assert.equal(paperApiConfiguration(url).url, '');
    assert.notEqual(paperApiConfiguration(url).error, '');
  }
  assert.equal(paperApiConfiguration('http://localhost:8789').url, '');
  assert.equal(paperApiConfiguration('http://localhost:8789', true).url, 'http://localhost:8789');
  assert.equal(paperApiConfiguration('http://paper.example.com', true).url, '');
});
