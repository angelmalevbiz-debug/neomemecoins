const CACHE_PREFIX = 'neo-paper-state-v2:';
const LEGACY_CACHE_KEY = 'neo-live-state-v1';
export const CACHE_MAX_AGE_MS = 15_000;
export const RESPONSE_MAX_AGE_MS = 8_000;

type CacheStorage = Pick<Storage, 'getItem' | 'setItem' | 'removeItem' | 'key' | 'length'>;
type CachedState<T> = { version: 2; userId: string; savedAt: number; state: T };
export type DashboardConnection = { source: 'none' | 'cache' | 'network'; receivedAt: number; failure: string };
export const initialDashboardConnection: DashboardConnection = { source: 'none', receivedAt: 0, failure: '' };
export type ConnectionStatus = 'CONNECTING' | 'OFFLINE' | 'CACHED' | 'STALE' | 'ONLINE';
const DEFAULT_PAPER_API = 'https://neo-meme-api.169-58-211-177.sslip.io';

export function paperApiConfiguration(configuredUrl?: string, development = false): { url: string; error: string } {
  if (!configuredUrl?.trim()) return { url: DEFAULT_PAPER_API, error: '' };
  try {
    const url = new URL(configuredUrl.trim());
    const localDevelopment = development && url.protocol === 'http:' && ['localhost', '127.0.0.1', '[::1]'].includes(url.hostname);
    if ((url.protocol !== 'https:' && !localDevelopment) || url.username || url.password || url.search || url.hash || url.pathname !== '/') {
      throw new Error('Invalid PAPER API URL');
    }
    return { url: url.origin, error: '' };
  } catch {
    return { url: '', error: 'Невалидна конфигурация на PAPER backend адреса.' };
  }
}

export function clearAccountStateCache(storage: CacheStorage, userId?: string) {
  try {
    storage.removeItem(LEGACY_CACHE_KEY);
    if (userId) {
      storage.removeItem(`${CACHE_PREFIX}${userId}`);
    } else {
      const keys = Array.from({ length: storage.length }, (_, index) => storage.key(index));
      keys.forEach(key => { if (key?.startsWith(CACHE_PREFIX)) storage.removeItem(key); });
    }
  } catch { /* Browser storage is optional. */ }
}

export function readAccountStateCache<T>(storage: CacheStorage, userId: string, now: number, validate: (value: unknown) => value is T): CachedState<T> | null {
  if (!userId) return null;
  const key = `${CACHE_PREFIX}${userId}`;
  try {
    storage.removeItem(LEGACY_CACHE_KEY);
    const raw = storage.getItem(key);
    if (!raw) return null;
    const cached = JSON.parse(raw) as CachedState<unknown>;
    const age = now - cached.savedAt;
    if (cached.version !== 2 || cached.userId !== userId || !Number.isFinite(cached.savedAt)
      || age < 0 || age >= CACHE_MAX_AGE_MS || !validate(cached.state)) {
      storage.removeItem(key);
      return null;
    }
    return cached as CachedState<T>;
  } catch {
    try { storage.removeItem(key); } catch { /* Browser storage is optional. */ }
    return null;
  }
}

export function writeAccountStateCache<T>(storage: CacheStorage, userId: string, state: T, now: number) {
  if (!userId) return;
  try {
    storage.removeItem(LEGACY_CACHE_KEY);
    storage.setItem(`${CACHE_PREFIX}${userId}`, JSON.stringify({ version: 2, userId, savedAt: now, state }));
  } catch { /* Browser storage is optional. */ }
}

export function dashboardConnectionStatus(connection: DashboardConnection, now: number): ConnectionStatus {
  if (connection.source === 'none') return connection.failure ? 'OFFLINE' : 'CONNECTING';
  if (connection.failure) return 'STALE';
  if (connection.source === 'cache') return 'CACHED';
  const age = now - connection.receivedAt;
  return age < 0 || age >= RESPONSE_MAX_AGE_MS ? 'STALE' : 'ONLINE';
}

export function backendErrorMessage(error: unknown): string {
  const message = error instanceof Error ? error.message : 'Backend unavailable';
  if (message === 'Session expired') return 'Сесията е изтекла. Влез отново в акаунта.';
  if (error instanceof Error && error.name === 'AbortError') return 'Backend не отговори навреме. Връзката се проверява отново.';
  if (/failed to fetch|networkerror|load failed|backend unavailable/i.test(message)) {
    return 'Няма връзка с backend. Провери мрежата и разрешението на сървъра за този Pages адрес.';
  }
  return message;
}

export function moneyOrUnavailable(value: number | undefined, signed = false, digits = 2): string {
  return typeof value === 'number' && Number.isFinite(value)
    ? `${signed && value >= 0 ? '+' : ''}$${value.toFixed(digits)}` : '—';
}

export function percentageOrUnavailable(value: number | undefined): string {
  return typeof value === 'number' && Number.isFinite(value) ? `${value >= 0 ? '+' : ''}${value.toFixed(2)}%` : '—';
}

export function tokenDetailForAddress<T extends { coin: { address: string } }>(detail: T | null, address: string): T | null {
  return detail?.coin.address === address ? detail : null;
}
