import { analyze } from './engine.js';
import { radarAnalyze } from './radar.js';

const RADAR_CACHE_MS = 15_000;
const RESOLVE_CACHE_MS = 25_000;
const radarCache = new Map();
const resolveCache = new Map();

function n(value) {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : 0;
}

function closenessScore(observed, actual, maxPoints) {
  const a = n(observed);
  const b = n(actual);
  if (!(a > 0) || !(b > 0)) return 0;
  const rel = Math.abs(a - b) / Math.max(a, b);
  if (rel <= 0.08) return maxPoints;
  if (rel <= 0.16) return Math.round(maxPoints * 0.88);
  if (rel <= 0.28) return Math.round(maxPoints * 0.68);
  if (rel <= 0.45) return Math.round(maxPoints * 0.4);
  if (rel <= 0.7) return Math.round(maxPoints * 0.1);
  return -Math.round(maxPoints * 0.75);
}

function normalizeQuery(value) {
  return String(value || '').replace(/^\$/, '').trim().toLowerCase();
}

async function resolveVisibleItem(item) {
  const query = String(item?.query || item?.symbol || '').replace(/^\$/, '').trim();
  if (!query) return null;

  const cacheKey = `${query.toLowerCase()}|${Math.round(n(item?.marketCapUsd) / 1000)}|${n(item?.priceUsd).toPrecision(6)}`;
  const cached = resolveCache.get(cacheKey);
  if (cached && Date.now() - cached.at < RESOLVE_CACHE_MS) return cached.result;

  const response = await fetch(`https://api.dexscreener.com/latest/dex/search?q=${encodeURIComponent(query)}`);
  if (!response.ok) throw new Error(`DEX search HTTP ${response.status}`);
  const payload = await response.json();
  const pairs = Array.isArray(payload?.pairs) ? payload.pairs : [];
  const q = normalizeQuery(query);

  const candidates = pairs
    .filter((pair) => pair?.chainId === 'solana' && pair?.baseToken?.address)
    .map((pair) => {
      const symbol = String(pair?.baseToken?.symbol || '');
      const name = String(pair?.baseToken?.name || '');
      const exactSymbol = normalizeQuery(symbol) === q;
      const exactName = normalizeQuery(name) === q;
      const startsName = normalizeQuery(name).startsWith(q) || q.startsWith(normalizeQuery(name));
      if (!exactSymbol && !exactName && !startsName) return null;

      const marketCap = n(pair?.marketCap || pair?.fdv);
      const price = n(pair?.priceUsd);
      const liquidity = n(pair?.liquidity?.usd);
      const volume1h = n(pair?.volume?.h1);
      let score = exactSymbol ? 42 : exactName ? 36 : 20;
      score += closenessScore(item?.marketCapUsd, marketCap, 48);
      score += closenessScore(item?.priceUsd, price, 18);
      if (liquidity >= 10_000) score += 4;
      if (liquidity >= 50_000) score += 3;
      if (volume1h > 0) score += 2;

      return { pair, score, marketCap, price, liquidity, exactSymbol, exactName };
    })
    .filter(Boolean)
    .sort((a, b) => b.score - a.score);

  if (!candidates.length) {
    resolveCache.set(cacheKey, { at: Date.now(), result: null });
    return null;
  }

  const best = candidates[0];
  const second = candidates[1];
  const margin = second ? best.score - second.score : 99;
  const hasMarketAnchor = n(item?.marketCapUsd) > 0;
  const hasPriceAnchor = n(item?.priceUsd) > 0;
  const anchored = hasMarketAnchor || hasPriceAnchor;

  // Prefer missing a token over silently resolving the wrong same-symbol meme coin.
  if (best.score < 56 || (!anchored && margin < 12) || (anchored && margin < 5)) {
    resolveCache.set(cacheKey, { at: Date.now(), result: null });
    return null;
  }

  const result = {
    token: String(best.pair.baseToken.address),
    query,
    symbol: String(best.pair.baseToken.symbol || query),
    name: String(best.pair.baseToken.name || query),
    pairAddress: String(best.pair.pairAddress || ''),
    marketCapUsd: best.marketCap,
    priceUsd: best.price,
    liquidityUsd: best.liquidity,
    resolutionScore: Math.round(best.score),
    resolutionMargin: Math.round(margin),
    resolutionConfidence: Math.max(0, Math.min(100, Math.round(best.score * 0.72 + Math.min(20, margin * 1.4)))),
    context: String(item?.context || '').slice(0, 600),
  };
  resolveCache.set(cacheKey, { at: Date.now(), result });
  return result;
}

async function resolveVisibleMany(items) {
  const queue = Array.isArray(items) ? items.slice(0, 18) : [];
  const resolved = [];
  let cursor = 0;

  async function worker() {
    while (cursor < queue.length) {
      const index = cursor++;
      try {
        const result = await resolveVisibleItem(queue[index]);
        if (result) resolved.push(result);
      } catch {
        // Retry naturally on next Sentinel cycle.
      }
    }
  }

  await Promise.all(Array.from({ length: Math.min(4, queue.length || 1) }, worker));

  // One high-confidence resolution per visible query. This prevents duplicate same-symbol ghost candidates.
  const byQuery = new Map();
  for (const item of resolved) {
    const key = normalizeQuery(item.query || item.symbol);
    const current = byQuery.get(key);
    if (!current || item.resolutionConfidence > current.resolutionConfidence ||
        (item.resolutionConfidence === current.resolutionConfidence && item.resolutionScore > current.resolutionScore)) {
      byQuery.set(key, item);
    }
  }

  const byToken = new Map();
  for (const item of byQuery.values()) {
    const current = byToken.get(item.token);
    if (!current || item.resolutionConfidence > current.resolutionConfidence) byToken.set(item.token, item);
  }
  return [...byToken.values()].sort((a, b) => b.resolutionConfidence - a.resolutionConfidence);
}

async function radarOne(item) {
  const token = String(item?.token || item?.tokenAddress || '').trim();
  if (!token) throw new Error('Missing radar token.');
  const cached = radarCache.get(token);
  if (cached && Date.now() - cached.at < RADAR_CACHE_MS) return cached.result;
  const result = await radarAnalyze(token, item || {});
  radarCache.set(token, { at: Date.now(), result });
  return result;
}

async function radarMany(items) {
  const queue = Array.isArray(items) ? items.slice(0, 14) : [];
  const results = [];
  let cursor = 0;

  async function worker() {
    while (cursor < queue.length) {
      const index = cursor++;
      try {
        results[index] = await radarOne(queue[index]);
      } catch (error) {
        results[index] = {
          tokenAddress: queue[index]?.token || '',
          error: error instanceof Error ? error.message : String(error),
          priority: 0,
          risk: 100,
          status: 'UNAVAILABLE',
          dataCoverage: 0,
        };
      }
    }
  }

  await Promise.all(Array.from({ length: Math.min(4, queue.length || 1) }, worker));
  return results.filter(Boolean).sort((a, b) => (b.priority || 0) - (a.priority || 0) || (a.risk || 0) - (b.risk || 0));
}

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (message?.type === 'NEO_ANALYZE') {
    analyze(message.input, message.pageContext || {})
      .then((result) => sendResponse({ ok: true, result }))
      .catch((error) => sendResponse({ ok: false, error: error instanceof Error ? error.message : String(error) }));
    return true;
  }

  if (message?.type === 'NEO_RADAR_SCAN') {
    radarMany(message.items)
      .then((results) => sendResponse({ ok: true, results }))
      .catch((error) => sendResponse({ ok: false, error: error instanceof Error ? error.message : String(error) }));
    return true;
  }

  if (message?.type === 'NEO_RESOLVE_VISIBLE_TOKENS') {
    resolveVisibleMany(message.items)
      .then((results) => sendResponse({ ok: true, results }))
      .catch((error) => sendResponse({ ok: false, error: error instanceof Error ? error.message : String(error) }));
    return true;
  }

  return false;
});
