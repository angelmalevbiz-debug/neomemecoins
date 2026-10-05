const RT_DEFAULT_RPC = 'https://api.mainnet.solana.com';
const rtMarketCache = new Map();
const rtSocialCache = new Map();
let rtRpcId = 700000;
let socialBusy = false;

const rtNum = (v) => Number.isFinite(Number(v)) ? Number(v) : 0;
const rtClamp = (v, min = 0, max = 100) => Math.max(min, Math.min(max, v));

async function rtRpc(rpcUrl, method, params) {
  const response = await fetch(rpcUrl, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ jsonrpc: '2.0', id: rtRpcId++, method, params }),
  });
  if (!response.ok) throw new Error(`RPC HTTP ${response.status}`);
  const payload = await response.json();
  if (payload?.error) throw new Error(`RPC ${payload.error.code}: ${payload.error.message}`);
  return payload.result;
}

async function rtMarket(token) {
  const now = Date.now();
  const cached = rtMarketCache.get(token);
  if (cached && now - cached.at < 2200) return cached.value;

  const response = await fetch(`https://api.dexscreener.com/token-pairs/v1/solana/${encodeURIComponent(token)}`);
  if (!response.ok) throw new Error(`DEX ${response.status}`);
  const payload = await response.json();
  const pairs = Array.isArray(payload) ? payload : Array.isArray(payload?.pairs) ? payload.pairs : [];
  const pair = pairs
    .filter((x) => x?.chainId === 'solana' && x?.baseToken?.address === token)
    .sort((a, b) => rtNum(b?.liquidity?.usd) - rtNum(a?.liquidity?.usd))[0];
  if (!pair) throw new Error('No Solana pair');

  const value = {
    tokenAddress: token,
    symbol: String(pair?.baseToken?.symbol || token.slice(0, 5)),
    name: String(pair?.baseToken?.name || 'Unknown'),
    priceUsd: rtNum(pair?.priceUsd),
    marketCapUsd: rtNum(pair?.marketCap || pair?.fdv),
    liquidityUsd: rtNum(pair?.liquidity?.usd),
    volume5mUsd: rtNum(pair?.volume?.m5),
    volume1hUsd: rtNum(pair?.volume?.h1),
    buys5m: rtNum(pair?.txns?.m5?.buys),
    sells5m: rtNum(pair?.txns?.m5?.sells),
    buys1h: rtNum(pair?.txns?.h1?.buys),
    sells1h: rtNum(pair?.txns?.h1?.sells),
    price5m: rtNum(pair?.priceChange?.m5),
    price1h: rtNum(pair?.priceChange?.h1),
    pairCreatedAt: rtNum(pair?.pairCreatedAt),
    socialLinks: (pair?.info?.socials || []).map((x) => String(x?.url || '')).filter(Boolean),
    websites: (pair?.info?.websites || []).map((x) => String(x?.url || '')).filter(Boolean),
    at: now,
  };
  rtMarketCache.set(token, { at: now, value });
  return value;
}

function rtPriority(market, previous) {
  const tx = market.buys5m + market.sells5m;
  const buyShare = tx ? market.buys5m / tx : 0.5;
  const liqRatio = market.marketCapUsd > 0 ? market.liquidityUsd / market.marketCapUsd * 100 : 0;
  let score = 35;
  if (market.liquidityUsd >= 15_000) score += 7;
  if (market.liquidityUsd >= 50_000) score += 5;
  if (liqRatio >= 7 && liqRatio <= 55) score += 8;
  if (tx >= 10) score += 5;
  if (tx >= 30) score += 6;
  if (buyShare >= 0.56 && buyShare <= 0.82) score += 9;
  if (market.price5m >= 1 && market.price5m <= 35) score += 7;
  if (market.price5m < -15 || market.price5m > 90) score -= 9;
  if (previous?.priceUsd && market.priceUsd) {
    const delta = (market.priceUsd - previous.priceUsd) / previous.priceUsd * 100;
    if (delta >= 0.3 && delta <= 8) score += 7;
    if (delta <= -3) score -= 5;
  }
  return rtClamp(Math.round(score));
}

async function realtimePulse(tokens, previousMap = {}) {
  const unique = [...new Set((tokens || []).map((x) => String(x || '').trim()).filter(Boolean))].slice(0, 8);
  const rows = await Promise.all(unique.map(async (token) => {
    try {
      const market = await rtMarket(token);
      const previous = previousMap?.[token] || null;
      const priceDelta = previous?.priceUsd && market.priceUsd ? (market.priceUsd - previous.priceUsd) / previous.priceUsd * 100 : 0;
      const liquidityDelta = previous?.liquidityUsd ? market.liquidityUsd - previous.liquidityUsd : 0;
      const volumeDelta = previous?.volume5mUsd ? market.volume5mUsd - previous.volume5mUsd : 0;
      const tx = market.buys5m + market.sells5m;
      const buyShare = tx ? market.buys5m / tx * 100 : 50;
      return { ...market, priceDelta, liquidityDelta, volumeDelta, buyShare, realtimePriority: rtPriority(market, previous) };
    } catch (error) {
      return { tokenAddress: token, error: error instanceof Error ? error.message : String(error), realtimePriority: 0 };
    }
  }));
  return rows.filter(Boolean).sort((a, b) => (b.realtimePriority || 0) - (a.realtimePriority || 0));
}

async function holderPulse(tokens) {
  const stored = await chrome.storage.local.get(['rpcUrl']);
  const rpcUrl = stored.rpcUrl || RT_DEFAULT_RPC;
  const unique = [...new Set((tokens || []).map((x) => String(x || '').trim()).filter(Boolean))].slice(0, 3);
  const rows = [];
  for (const token of unique) {
    try {
      const [supplyRes, largestRes] = await Promise.all([
        rtRpc(rpcUrl, 'getTokenSupply', [token, { commitment: 'confirmed' }]),
        rtRpc(rpcUrl, 'getTokenLargestAccounts', [token, { commitment: 'confirmed' }]),
      ]);
      const supply = rtNum(supplyRes?.value?.uiAmountString ?? supplyRes?.value?.uiAmount);
      const largest = Array.isArray(largestRes?.value) ? largestRes.value.slice(0, 10) : [];
      const pct = largest.map((x) => supply > 0 ? rtNum(x?.uiAmountString ?? x?.uiAmount) / supply * 100 : 0);
      const sum = (count) => pct.slice(0, count).reduce((a, b) => a + b, 0);
      rows.push({ tokenAddress: token, top1Pct: sum(1), top5Pct: sum(5), top10Pct: sum(10), at: Date.now() });
    } catch (error) {
      rows.push({ tokenAddress: token, error: error instanceof Error ? error.message : String(error), at: Date.now() });
    }
  }
  return rows;
}

function safeSocialUrl(raw) {
  try {
    const url = new URL(String(raw || ''));
    if (url.protocol !== 'https:') return null;
    const lower = `${url.hostname}${url.pathname}`.toLowerCase();
    if (/\/messages|\/settings|\/account|\/i\/flow|\/compose/.test(lower)) return null;
    return url.href;
  } catch { return null; }
}

async function scrapePublicPage() {
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const parseMetric = (raw) => {
    const s = String(raw || '').replace(/,/g, '').trim();
    const m = s.match(/([0-9]*\.?[0-9]+)\s*([KMB])?/i);
    if (!m) return 0;
    const v = Number(m[1]) || 0;
    const unit = String(m[2] || '').toUpperCase();
    return v * (unit === 'B' ? 1e9 : unit === 'M' ? 1e6 : unit === 'K' ? 1e3 : 1);
  };

  for (let i = 0; i < 3; i += 1) {
    window.scrollBy({ top: Math.max(450, innerHeight * 0.72), behavior: 'instant' });
    await sleep(650);
  }
  window.scrollTo({ top: 0, behavior: 'instant' });

  const text = String(document.body?.innerText || '').replace(/\s+/g, ' ').slice(0, 60_000);
  const times = [...document.querySelectorAll('time[datetime]')].slice(0, 25).map((el) => el.getAttribute('datetime')).filter(Boolean);
  const articles = [...document.querySelectorAll('article')].slice(0, 20);
  const posts = articles.map((article) => {
    const postText = String(article.innerText || '').replace(/\s+/g, ' ').slice(0, 1200);
    const datetime = article.querySelector('time[datetime]')?.getAttribute('datetime') || '';
    let engagement = 0;
    for (const el of [...article.querySelectorAll('[aria-label]')].slice(0, 50)) {
      const label = String(el.getAttribute('aria-label') || '');
      if (/reply|repost|retweet|like|view|bookmark/i.test(label)) engagement += parseMetric(label);
    }
    return { text: postText, datetime, engagement };
  }).filter((x) => x.text);

  return {
    url: location.href,
    title: document.title,
    text,
    times,
    posts,
    articleCount: articles.length,
    capturedAt: Date.now(),
  };
}

function socialScore(snapshot, tokenMeta, previous) {
  const now = Date.now();
  const symbol = String(tokenMeta?.symbol || '').replace(/^\$/, '').toLowerCase();
  const name = String(tokenMeta?.name || '').toLowerCase();
  const ca = String(tokenMeta?.tokenAddress || '').toLowerCase();
  const combined = `${snapshot?.title || ''} ${snapshot?.text || ''}`.toLowerCase();
  const posts = Array.isArray(snapshot?.posts) ? snapshot.posts : [];
  const engagement = posts.reduce((sum, p) => sum + rtNum(p.engagement), 0);
  const latestMs = (snapshot?.times || []).map((x) => Date.parse(x)).filter(Number.isFinite).sort((a, b) => b - a)[0] || 0;
  const latestMinutes = latestMs ? Math.max(0, (now - latestMs) / 60000) : null;
  const mentions = [symbol, name, ca].filter((x) => x && x.length >= 3).reduce((sum, needle) => sum + (combined.split(needle).length - 1), 0);
  let score = 22;
  if (posts.length >= 2) score += 10;
  if (posts.length >= 6) score += 8;
  if (mentions >= 2) score += 12;
  if (mentions >= 5) score += 8;
  if (latestMinutes !== null && latestMinutes <= 15) score += 18;
  else if (latestMinutes !== null && latestMinutes <= 60) score += 10;
  if (engagement >= 50) score += 7;
  if (engagement >= 500) score += 8;
  let velocity = 0;
  if (previous) {
    velocity += Math.max(0, engagement - rtNum(previous.engagement));
    velocity += Math.max(0, posts.length - rtNum(previous.postCount)) * 25;
    if (velocity > 0) score += Math.min(15, 4 + Math.log10(velocity + 1) * 4);
  }
  return { score: rtClamp(Math.round(score)), postCount: posts.length, engagement: Math.round(engagement), latestMinutes, mentions, velocity: Math.round(velocity) };
}

async function scanOneSocial(url, tokenMeta) {
  const safe = safeSocialUrl(url);
  if (!safe) return null;
  let tabId = null;
  try {
    const tab = await chrome.tabs.create({ url: safe, active: false });
    tabId = tab?.id;
    if (!tabId) throw new Error('Could not create social tab');
    await new Promise((r) => setTimeout(r, 3200));
    const executed = await chrome.scripting.executeScript({ target: { tabId }, func: scrapePublicPage });
    const snapshot = executed?.[0]?.result || null;
    if (!snapshot) throw new Error('No page snapshot');
    const key = `${tokenMeta?.tokenAddress || tokenMeta?.symbol || ''}|${new URL(safe).hostname}`;
    const previous = rtSocialCache.get(key)?.metrics || null;
    const metrics = socialScore(snapshot, tokenMeta, previous);
    rtSocialCache.set(key, { at: Date.now(), metrics, snapshot: { url: snapshot.url, title: snapshot.title } });
    return {
      url: snapshot.url,
      title: snapshot.title,
      ...metrics,
      snippets: (snapshot.posts || []).slice(0, 3).map((p) => ({ text: p.text.slice(0, 240), datetime: p.datetime, engagement: p.engagement })),
    };
  } finally {
    if (tabId) { try { await chrome.tabs.remove(tabId); } catch { /* ignore */ } }
  }
}

async function socialScan(payload) {
  if (socialBusy) return { busy: true, scans: [], score: 0 };
  socialBusy = true;
  try {
    const tokenMeta = payload?.tokenMeta || {};
    const urls = [...new Set((payload?.urls || []).map(safeSocialUrl).filter(Boolean))];
    const xUrl = urls.find((u) => /(^|\.)x\.com$|(^|\.)twitter\.com$/i.test(new URL(u).hostname));
    const website = urls.find((u) => !/(^|\.)x\.com$|(^|\.)twitter\.com$|(^|\.)t\.me$|telegram|discord|youtube|instagram|tiktok/i.test(new URL(u).hostname));
    const targets = [xUrl, website].filter(Boolean).slice(0, 2);
    if (!xUrl && tokenMeta?.symbol && String(tokenMeta.symbol).length >= 3) {
      targets.unshift(`https://x.com/search?q=${encodeURIComponent(`$${String(tokenMeta.symbol).replace(/^\$/, '')}`)}&src=typed_query&f=live`);
    }

    const scans = [];
    for (const target of targets.slice(0, 2)) {
      try {
        const result = await scanOneSocial(target, tokenMeta);
        if (result) scans.push(result);
      } catch (error) {
        scans.push({ url: target, error: error instanceof Error ? error.message : String(error), score: 0 });
      }
    }
    const valid = scans.filter((x) => !x.error);
    const score = valid.length ? Math.round(valid.reduce((s, x) => s + rtNum(x.score), 0) / valid.length) : 0;
    return { busy: false, scans, score, at: Date.now() };
  } finally {
    socialBusy = false;
  }
}

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (message?.type === 'NEO_REALTIME_PULSE') {
    realtimePulse(message.tokens, message.previous || {}).then((rows) => sendResponse({ ok: true, rows })).catch((error) => sendResponse({ ok: false, error: String(error) }));
    return true;
  }
  if (message?.type === 'NEO_HOLDER_PULSE') {
    holderPulse(message.tokens).then((rows) => sendResponse({ ok: true, rows })).catch((error) => sendResponse({ ok: false, error: String(error) }));
    return true;
  }
  if (message?.type === 'NEO_SOCIAL_SCAN') {
    socialScan(message).then((result) => sendResponse({ ok: true, result })).catch((error) => sendResponse({ ok: false, error: error instanceof Error ? error.message : String(error) }));
    return true;
  }
  return false;
});
