const discoveryCache = new Map();

const dNum = (value) => Number.isFinite(Number(value)) ? Number(value) : 0;
const dNorm = (value) => String(value || '').replace(/^\$/,'').trim().toLowerCase();

async function fetchTokenPair(token) {
  const key = `addr:${token}`;
  const cached = discoveryCache.get(key);
  if (cached && Date.now() - cached.at < 20000) return cached.value;
  const response = await fetch(`https://api.dexscreener.com/token-pairs/v1/solana/${encodeURIComponent(token)}`);
  if (!response.ok) return null;
  const payload = await response.json();
  const pairs = Array.isArray(payload) ? payload : Array.isArray(payload?.pairs) ? payload.pairs : [];
  const pair = pairs.filter((x) => x?.chainId === 'solana' && x?.baseToken?.address === token)
    .sort((a,b) => dNum(b?.liquidity?.usd) - dNum(a?.liquidity?.usd))[0];
  if (!pair) return null;
  const value = {
    token,
    symbol: String(pair?.baseToken?.symbol || ''),
    name: String(pair?.baseToken?.name || ''),
    marketCapUsd: dNum(pair?.marketCap || pair?.fdv),
    priceUsd: dNum(pair?.priceUsd),
    liquidityUsd: dNum(pair?.liquidity?.usd),
    resolutionScore: 100,
    resolutionMargin: 99,
    resolutionConfidence: 100,
    source: 'direct-address',
  };
  discoveryCache.set(key,{at:Date.now(),value});
  return value;
}

async function validateAddresses(addresses) {
  const queue = [...new Set((addresses || []).map((x) => String(x || '').trim()).filter((x) => /^[1-9A-HJ-NP-Za-km-z]{32,44}$/.test(x)))].slice(0,40);
  const out = [];
  let cursor = 0;
  async function worker() {
    while (cursor < queue.length) {
      const token = queue[cursor++];
      try { const row = await fetchTokenPair(token); if (row) out.push(row); } catch { /* retry next cycle */ }
    }
  }
  await Promise.all(Array.from({length:Math.min(5, queue.length || 1)}, worker));
  return out.sort((a,b) => b.liquidityUsd - a.liquidityUsd).slice(0,18);
}

function closeness(observed, actual, points) {
  const a = dNum(observed), b = dNum(actual);
  if (!(a > 0) || !(b > 0)) return 0;
  const rel = Math.abs(a-b)/Math.max(a,b);
  if (rel <= .08) return points;
  if (rel <= .2) return Math.round(points*.75);
  if (rel <= .4) return Math.round(points*.4);
  return -Math.round(points*.5);
}

async function resolveLoose(item) {
  const query = String(item?.query || '').replace(/^\$/,'').trim();
  if (!query || query.length < 2 || query.length > 48) return null;
  const key = `loose:${dNorm(query)}:${Math.round(dNum(item?.priceUsd)*1e8)}:${Math.round(dNum(item?.marketCapUsd)/1000)}`;
  const cached = discoveryCache.get(key);
  if (cached && Date.now() - cached.at < 25000) return cached.value;

  const response = await fetch(`https://api.dexscreener.com/latest/dex/search?q=${encodeURIComponent(query)}`);
  if (!response.ok) return null;
  const payload = await response.json();
  const q = dNorm(query);
  const candidates = (Array.isArray(payload?.pairs) ? payload.pairs : [])
    .filter((p) => p?.chainId === 'solana' && p?.baseToken?.address)
    .map((p) => {
      const symbol = dNorm(p?.baseToken?.symbol);
      const name = dNorm(p?.baseToken?.name);
      const exactSymbol = symbol === q;
      const exactName = name === q;
      if (!exactSymbol && !exactName) return null;
      const liquidity = dNum(p?.liquidity?.usd);
      const mcap = dNum(p?.marketCap || p?.fdv);
      const price = dNum(p?.priceUsd);
      const vol = dNum(p?.volume?.h1);
      let score = exactSymbol ? 62 : 56;
      score += closeness(item?.marketCapUsd,mcap,26);
      score += closeness(item?.priceUsd,price,16);
      if (liquidity >= 5000) score += 4;
      if (liquidity >= 25000) score += 4;
      if (vol > 0) score += 2;
      return { p, score, liquidity, mcap, price };
    }).filter(Boolean).sort((a,b) => b.score-a.score || b.liquidity-a.liquidity);
  if (!candidates.length) return null;
  const best = candidates[0], second = candidates[1];
  const margin = second ? best.score-second.score : 99;
  const anchored = dNum(item?.marketCapUsd)>0 || dNum(item?.priceUsd)>0;
  if (best.score < (anchored ? 60 : 68) || (!anchored && margin < 10) || (anchored && margin < 4)) return null;
  const value = {
    token: String(best.p.baseToken.address),
    query,
    symbol: String(best.p.baseToken.symbol || query),
    name: String(best.p.baseToken.name || query),
    marketCapUsd: best.mcap,
    priceUsd: best.price,
    liquidityUsd: best.liquidity,
    resolutionScore: Math.round(best.score),
    resolutionMargin: Math.round(margin),
    resolutionConfidence: Math.max(0,Math.min(100,Math.round(best.score*.72 + Math.min(18,margin*1.2)))),
    source: 'loose-label',
  };
  discoveryCache.set(key,{at:Date.now(),value});
  return value;
}

async function resolveLooseMany(items) {
  const queue = Array.isArray(items) ? items.slice(0,16) : [];
  const out=[];
  let cursor=0;
  async function worker(){ while(cursor<queue.length){ const i=cursor++; try{ const row=await resolveLoose(queue[i]); if(row) out.push(row); }catch{} } }
  await Promise.all(Array.from({length:Math.min(4,queue.length||1)},worker));
  const byToken = new Map();
  for (const row of out) {
    const cur = byToken.get(row.token);
    if (!cur || row.resolutionConfidence > cur.resolutionConfidence) byToken.set(row.token,row);
  }
  return [...byToken.values()].sort((a,b)=>b.resolutionConfidence-a.resolutionConfidence).slice(0,18);
}

chrome.runtime.onMessage.addListener((message,_sender,sendResponse) => {
  if (message?.type === 'NEO_VALIDATE_DISCOVERY_ADDRESSES') {
    validateAddresses(message.addresses).then((results)=>sendResponse({ok:true,results})).catch((error)=>sendResponse({ok:false,error:String(error)}));
    return true;
  }
  if (message?.type === 'NEO_RESOLVE_LOOSE_LABELS') {
    resolveLooseMany(message.items).then((results)=>sendResponse({ok:true,results})).catch((error)=>sendResponse({ok:false,error:String(error)}));
    return true;
  }
  return false;
});
