import { analyze as analyzeSolana } from './engine-v2.js';

const CACHE_MS = 10_000;
const searchCache = new Map();
const marketCache = new Map();
const n = (v) => Number.isFinite(Number(v)) ? Number(v) : 0;
const clamp = (v, min = 0, max = 100) => Math.max(min, Math.min(max, v));
const norm = (v) => String(v || '').replace(/^\$/, '').trim().toLowerCase();
const addrEq = (a, b) => String(a || '').toLowerCase() === String(b || '').toLowerCase();

function normalizeChain(raw) {
  const s = String(raw || '').toLowerCase();
  if (/solana|\bsol\b/.test(s)) return 'solana';
  if (/robinhood/.test(s)) return 'robinhood';
  if (/\bbase\b/.test(s)) return 'base';
  if (/ethereum|\beth\b/.test(s)) return 'ethereum';
  if (/bnb|bsc|binance/.test(s)) return 'bsc';
  if (/monad/.test(s)) return 'monad';
  return s.replace(/[^a-z0-9_-]/g, '').slice(0, 32);
}
function chainLabel(id) {
  return ({ solana:'Solana', robinhood:'Robinhood Chain', base:'Base', ethereum:'Ethereum', bsc:'BNB Chain', monad:'Monad' })[id] || id || 'Unknown';
}
function pairScore(p) {
  return n(p?.liquidity?.usd) + Math.min(n(p?.volume?.h1), 2_000_000) * 0.1 + Math.min(n(p?.txns?.h1?.buys) + n(p?.txns?.h1?.sells), 1000) * 100;
}
function pairToMarket(p) {
  const rawChain = String(p?.chainId || '').toLowerCase();
  const chainId = normalizeChain(rawChain) || rawChain || 'unknown';
  return {
    chainId,
    rawChainId: rawChain,
    chainLabel: chainLabel(chainId),
    tokenAddress: String(p?.baseToken?.address || ''),
    pairAddress: String(p?.pairAddress || ''),
    symbol: String(p?.baseToken?.symbol || ''),
    name: String(p?.baseToken?.name || ''),
    priceUsd: n(p?.priceUsd),
    marketCapUsd: n(p?.marketCap || p?.fdv),
    liquidityUsd: n(p?.liquidity?.usd),
    volume5mUsd: n(p?.volume?.m5),
    volume1hUsd: n(p?.volume?.h1),
    volume24hUsd: n(p?.volume?.h24),
    buys5m: n(p?.txns?.m5?.buys),
    sells5m: n(p?.txns?.m5?.sells),
    buys1h: n(p?.txns?.h1?.buys),
    sells1h: n(p?.txns?.h1?.sells),
    priceChange5m: n(p?.priceChange?.m5),
    priceChange1h: n(p?.priceChange?.h1),
    priceChange24h: n(p?.priceChange?.h24),
    pairCreatedAt: n(p?.pairCreatedAt),
    socialLinks: (p?.info?.socials || []).map((x) => String(x?.url || '')).filter(Boolean),
    websites: (p?.info?.websites || []).map((x) => String(x?.url || '')).filter(Boolean),
    dexBacked: true,
  };
}

async function dexSearch(q) {
  const key = `q:${norm(q)}`;
  const cached = searchCache.get(key);
  if (cached && Date.now() - cached.at < CACHE_MS) return cached.value;
  const response = await fetch(`https://api.dexscreener.com/latest/dex/search?q=${encodeURIComponent(q)}`);
  if (!response.ok) throw new Error(`DEX search ${response.status}`);
  const json = await response.json();
  const pairs = (Array.isArray(json?.pairs) ? json.pairs : []).filter((p) => p?.baseToken?.address);
  searchCache.set(key, { at: Date.now(), value: pairs });
  return pairs;
}

async function pairByAddress(address) {
  const key = `a:${String(address || '').toLowerCase()}`;
  const cached = marketCache.get(key);
  if (cached && Date.now() - cached.at < CACHE_MS) return cached.value;
  const pairs = await dexSearch(address);
  const exact = pairs.filter((p) => addrEq(p?.baseToken?.address, address)).sort((a, b) => pairScore(b) - pairScore(a));
  const best = exact[0] || null;
  marketCache.set(key, { at: Date.now(), value: best });
  return best;
}

function closeScore(observed, actual, points) {
  const a = n(observed), b = n(actual);
  if (!(a > 0) || !(b > 0)) return 0;
  const d = Math.abs(a - b) / Math.max(a, b);
  if (d <= 0.05) return points;
  if (d <= 0.12) return Math.round(points * 0.88);
  if (d <= 0.25) return Math.round(points * 0.62);
  if (d <= 0.45) return Math.round(points * 0.28);
  if (d <= 0.75) return -Math.round(points * 0.2);
  return -Math.round(points * 0.65);
}

function terminalFallback(candidate) {
  if (!candidate?.address) return null;
  const hasMarket = n(candidate.priceUsd) > 0 || n(candidate.marketCapUsd) > 0 || n(candidate.liquidityUsd) > 0;
  if (!hasMarket || (!candidate.symbol && !candidate.name)) return null;
  const chainId = normalizeChain(candidate.chainHint) || 'unknown';
  return {
    token: String(candidate.address), tokenAddress: String(candidate.address), chainId, rawChainId: chainId,
    chainLabel: chainLabel(chainId), symbol: String(candidate.symbol || '').slice(0, 40), name: String(candidate.name || '').slice(0, 100),
    priceUsd: n(candidate.priceUsd), marketCapUsd: n(candidate.marketCapUsd), liquidityUsd: n(candidate.liquidityUsd),
    volume5mUsd: n(candidate.volume5mUsd), volume1hUsd: n(candidate.volume1hUsd), volume24hUsd: 0,
    buys5m: n(candidate.buys5m), sells5m: n(candidate.sells5m), buys1h: 0, sells1h: 0,
    priceChange5m: 0, priceChange1h: 0, priceChange24h: 0, pairCreatedAt: 0,
    socialLinks: [], websites: [], dexBacked: false, source: 'terminal-feed', resolutionConfidence: 88, resolutionScore: 88,
    context: String(candidate.context || '').slice(0, 700), feedAt: n(candidate.at),
  };
}

async function resolveFeed(candidate) {
  const address = String(candidate?.address || '').trim();
  if (!address) return null;
  try {
    const p = await pairByAddress(address);
    if (p) {
      const m = pairToMarket(p);
      return { ...m, token: m.tokenAddress, source: 'terminal-feed+dex', resolutionConfidence: 100, resolutionScore: 100, context: String(candidate.context || '').slice(0, 700) };
    }
  } catch { /* terminal fallback below */ }
  return terminalFallback(candidate);
}

async function resolveAddress(candidate) {
  const address = String(candidate?.address || candidate || '').trim();
  if (!address) return null;
  const p = await pairByAddress(address);
  if (!p) return null;
  const m = pairToMarket(p);
  return { ...m, token: m.tokenAddress, source: 'direct-address', resolutionConfidence: 100, resolutionScore: 100, context: String(candidate?.context || '').slice(0, 700) };
}

async function resolveLabel(item) {
  const query = String(item?.query || '').replace(/^\$/, '').trim();
  if (query.length < 2 || query.length > 64) return null;
  const pairs = await dexSearch(query);
  const q = norm(query);
  const hint = normalizeChain(item?.chainHint || item?.context || '');
  const scored = pairs.map((p) => {
    const symbol = norm(p?.baseToken?.symbol), name = norm(p?.baseToken?.name);
    const exactSymbol = symbol === q, exactName = name === q;
    const compactQ = q.replace(/[^a-z0-9]/g, '');
    const compactS = symbol.replace(/[^a-z0-9]/g, '');
    const compactN = name.replace(/[^a-z0-9]/g, '');
    const fuzzy = compactQ.length >= 3 && (compactS === compactQ || compactN === compactQ || compactN.startsWith(compactQ) || compactQ.startsWith(compactN));
    if (!exactSymbol && !exactName && !fuzzy) return null;
    const m = pairToMarket(p);
    let score = exactSymbol ? 60 : exactName ? 54 : 34;
    score += closeScore(item?.marketCapUsd, m.marketCapUsd, 42);
    score += closeScore(item?.priceUsd, m.priceUsd, 24);
    if (hint && (m.chainId === hint || m.rawChainId.includes(hint))) score += 20;
    if (m.liquidityUsd >= 5_000) score += 5;
    if (m.liquidityUsd >= 25_000) score += 5;
    if (m.liquidityUsd >= 100_000) score += 3;
    if (m.volume1hUsd > 0) score += 3;
    if (m.buys5m + m.sells5m > 0) score += 2;
    return { m, score };
  }).filter(Boolean).sort((a, b) => b.score - a.score || b.m.liquidityUsd - a.m.liquidityUsd);

  if (!scored.length) return null;
  const best = scored[0], second = scored[1];
  const margin = second ? best.score - second.score : 99;
  const anchored = n(item?.marketCapUsd) > 0 || n(item?.priceUsd) > 0 || Boolean(hint);
  const exact = norm(best.m.symbol) === q || norm(best.m.name) === q;
  const strongMarket = best.m.liquidityUsd >= 20_000 || best.m.volume1hUsd >= 10_000;
  if (anchored) {
    if (best.score < 48 || margin < 1) return null;
  } else {
    if (!exact || !strongMarket || best.score < 62) return null;
    if (second && margin < 4 && pairScore(scored[0].m) < pairScore(scored[1].m) * 2.5) return null;
  }
  return { ...best.m, token: best.m.tokenAddress, query, source: 'label-resolve', resolutionScore: Math.round(best.score), resolutionMargin: Math.round(margin), resolutionConfidence: clamp(Math.round(best.score * 0.78 + Math.min(18, margin * 1.2))) };
}

async function discover(payload) {
  const jobs = [];
  for (const c of payload?.feedCandidates || []) jobs.push({ type:'feed', value:c });
  for (const a of payload?.addresses || []) jobs.push({ type:'address', value:a });
  for (const i of payload?.items || []) jobs.push({ type:'label', value:i });
  const out = [];
  let cursor = 0;
  async function worker() {
    while (cursor < jobs.length) {
      const job = jobs[cursor++];
      try {
        const row = job.type === 'feed' ? await resolveFeed(job.value) : job.type === 'address' ? await resolveAddress(job.value) : await resolveLabel(job.value);
        if (row) out.push(row);
      } catch { /* retry next cycle */ }
    }
  }
  await Promise.all(Array.from({ length: Math.min(7, jobs.length || 1) }, worker));
  const by = new Map();
  for (const r of out) {
    const key = `${r.chainId}:${String(r.tokenAddress || r.token).toLowerCase()}`;
    const current = by.get(key);
    if (!current || n(r.resolutionConfidence) > n(current.resolutionConfidence) || (r.dexBacked && !current.dexBacked)) by.set(key, r);
  }
  return [...by.values()].sort((a, b) => n(b.resolutionConfidence) - n(a.resolutionConfidence) || n(b.liquidityUsd) - n(a.liquidityUsd)).slice(0, 24);
}

function scoreMarket(m) {
  const tx5 = n(m.buys5m) + n(m.sells5m);
  const buy5 = tx5 ? n(m.buys5m) / tx5 : 0.5;
  const liqRatio = n(m.marketCapUsd) > 0 ? n(m.liquidityUsd) / n(m.marketCapUsd) * 100 : 0;
  let risk = m.dexBacked === false ? 22 : 12;
  let priority = 38;
  const reasons = [], positives = [];
  if (n(m.liquidityUsd) > 0 && n(m.liquidityUsd) < 5_000) { risk += 30; reasons.push('very thin liquidity'); }
  else if (n(m.liquidityUsd) > 0 && n(m.liquidityUsd) < 15_000) { risk += 16; reasons.push('low liquidity'); }
  else if (n(m.liquidityUsd) >= 50_000) { risk -= 3; priority += 8; positives.push('meaningful liquidity'); }
  if (n(m.marketCapUsd) > 0 && n(m.liquidityUsd) > 0) {
    if (liqRatio < 2) { risk += 26; reasons.push('liquidity tiny vs cap'); }
    else if (liqRatio < 5) { risk += 15; reasons.push('weak liquidity/cap'); }
    else if (liqRatio >= 8 && liqRatio <= 60) { risk -= 3; priority += 8; positives.push('supportive liquidity/cap'); }
  }
  if (tx5 >= 20) priority += 10; else if (tx5 >= 8) priority += 5;
  if (tx5 > 0 && buy5 >= .54 && buy5 <= .8) priority += 10;
  else if (tx5 > 8 && (buy5 > .94 || buy5 < .18)) { risk += 8; priority -= 7; reasons.push('extreme 5m flow'); }
  if (n(m.priceChange5m) >= 1 && n(m.priceChange5m) <= 35) priority += 7;
  if (n(m.priceChange5m) < -15 || n(m.priceChange5m) > 100) { risk += 8; priority -= 8; reasons.push('extreme 5m move'); }
  const links = (m.socialLinks || []).length + (m.websites || []).length;
  if (links >= 2) { priority += 5; positives.push('public social/project links'); }
  if (m.dexBacked === false) reasons.push('terminal feed only — DEX pair not independently confirmed');
  risk = clamp(Math.round(risk), 5, 100);
  priority = clamp(Math.round(priority - Math.max(0, risk - 30) * .35));
  let status = 'IGNORE';
  if (risk >= 72) status = 'HIGH RISK';
  else if (priority >= 68 && risk <= 52) status = 'DEEP CHECK';
  else if (priority >= 52 && risk <= 65) status = 'WATCH';
  else if (risk >= 48) status = 'CAUTION';
  return { ...m, priority, risk, status, liquidityRatio: liqRatio, tx5m: tx5, buyShare5m: buy5 * 100, price5m: n(m.priceChange5m), price1h: n(m.priceChange1h), reasons: reasons.slice(0, 5), positives: positives.slice(0, 5), observedAt: Date.now() };
}

async function marketOne(item) {
  const address = item?.tokenAddress || item?.token;
  try {
    const p = await pairByAddress(address);
    if (p) return scoreMarket(pairToMarket(p));
  } catch { /* fallback below */ }
  const fallback = item?.fallback || item;
  const market = terminalFallback({ ...fallback, address, chainHint: fallback.chainId || fallback.chainHint });
  if (!market) throw new Error('No market pair or terminal market snapshot');
  return scoreMarket(market);
}
async function marketMany(items) {
  const q = (items || []).slice(0, 16), out = [];
  let cursor = 0;
  async function worker() {
    while (cursor < q.length) {
      const item = q[cursor++];
      try { out.push(await marketOne(item)); }
      catch (e) { out.push({ tokenAddress:item?.tokenAddress || item?.token || '', chainId:item?.chainId || 'unknown', error:String(e), priority:0, risk:100, status:'UNAVAILABLE' }); }
    }
  }
  await Promise.all(Array.from({ length: Math.min(6, q.length || 1) }, worker));
  return out.sort((a, b) => n(b.priority) - n(a.priority));
}

function genericDeep(m) {
  const scored = scoreMarket(m);
  let confidence = m.dexBacked === false ? 42 : 56;
  if (n(m.liquidityUsd) > 0) confidence += 5;
  if (n(m.marketCapUsd) > 0) confidence += 5;
  if (n(m.buys5m) + n(m.sells5m) > 0) confidence += 5;
  confidence = clamp(confidence, 20, 68);
  const buy5 = scored.tx5m ? n(m.buys5m) / scored.tx5m : .5;
  let outlook = 50 + clamp(n(m.priceChange5m), -20, 20) * .7 + (buy5 - .5) * 40 - Math.max(0, scored.risk - 35) * .4;
  outlook = clamp(Math.round(outlook));
  const posture = scored.risk >= 65 ? 'SKIP' : scored.risk >= 45 ? 'WAIT' : 'WATCH';
  return {
    risk: scored.risk, posture, confidence, liquidityRatio: scored.liquidityRatio,
    signals: [
      ...scored.reasons.map((x) => ({ label:x, detail:x, points:0, severity:'warning' })),
      ...scored.positives.map((x) => ({ label:x, detail:x, points:0, severity:'positive' })),
      { label:'Evidence scope', detail:m.dexBacked === false ? 'Terminal feed evidence only; independent DEX/on-chain confirmation unavailable for this candidate.' : `${chainLabel(m.chainId)} market evidence is confirmed by DEX data; chain-specific holder/funding depth may still be limited.`, points:0, severity:'info' },
    ],
    outlook:{ score:outlook, label:outlook >= 65 ? 'POSITIVE MOMENTUM' : outlook <= 35 ? 'DOWNSIDE RISK' : 'MIXED', horizon:'seconds–minutes evidence window', confidence:Math.min(64, confidence), bull:[], bear:[], note:'Evidence-based momentum estimate, not a guaranteed price prediction.' },
    social:{ links:[...(m.socialLinks || []).map(url=>({url,platform:'Social'})),...(m.websites || []).map(url=>({url,platform:'Website'}))], count:(m.socialLinks || []).length + (m.websites || []).length },
    narrative:{ category:'unknown', confidence:10, matched:[] }, bundleRisk:null, flowRisk:null,
  };
}

async function deepAnalyze(item, pageContext = {}) {
  let m;
  try {
    const p = await pairByAddress(item?.tokenAddress || item?.token);
    if (p) m = pairToMarket(p);
  } catch { /* fallback */ }
  if (!m) m = terminalFallback({ ...(item?.fallback || item), address:item?.tokenAddress || item?.token, chainHint:item?.chainId || item?.chainHint });
  if (!m) throw new Error('No market evidence for deep analysis');
  if (m.chainId === 'solana' && m.dexBacked) {
    const result = await analyzeSolana(m.tokenAddress, pageContext);
    return { ...result, chainId:'solana', chainLabel:'Solana' };
  }
  return { tokenAddress:m.tokenAddress, market:m, holders:null, holderError:`${chainLabel(m.chainId)} holder/funding deep indexer is unavailable in free device-only mode.`, mintControls:null, mintError:'Chain-specific token controls not fully indexed.', generatedAt:Date.now(), chainId:m.chainId, chainLabel:m.chainLabel, ...genericDeep(m) };
}

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (msg?.type === 'NEO8_DISCOVER') {
    discover(msg).then((results) => sendResponse({ ok:true, results })).catch((e) => sendResponse({ ok:false, error:String(e) }));
    return true;
  }
  if (msg?.type === 'NEO8_MARKET') {
    marketMany(msg.items || []).then((rows) => sendResponse({ ok:true, rows })).catch((e) => sendResponse({ ok:false, error:String(e) }));
    return true;
  }
  if (msg?.type === 'NEO8_DEEP') {
    deepAnalyze(msg.item || {}, msg.pageContext || {}).then((result) => sendResponse({ ok:true, result })).catch((e) => sendResponse({ ok:false, error:String(e) }));
    return true;
  }
  return false;
});
