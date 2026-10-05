(() => {
  if (window.__NEO_PAGE_TAP_V081__) return;
  window.__NEO_PAGE_TAP_V081__ = true;

  const SOL_RE = /\b[1-9A-HJ-NP-Za-km-z]{32,44}\b/g;
  const EVM_RE = /\b0x[a-fA-F0-9]{40}\b/g;
  const VALID_ADDR = /^(?:0x[a-fA-F0-9]{40}|[1-9A-HJ-NP-Za-km-z]{32,44})$/;
  const MAX_TEXT = 1_750_000;
  const pending = new Map();
  const stats = { messages: 0, json: 0, structured: 0, raw: 0, lastSource: '', lastAt: 0 };
  let flushTimer = null;

  const clean = (v) => String(v ?? '').replace(/\s+/g, ' ').trim();
  const num = (v) => {
    if (typeof v === 'number') return Number.isFinite(v) ? v : 0;
    const s = String(v ?? '').replace(/[$,%\s,]/g, '').trim();
    const m = s.match(/^(-?[0-9]*\.?[0-9]+)([KMBT])?$/i);
    if (!m) return 0;
    const x = Number(m[1]);
    if (!Number.isFinite(x)) return 0;
    const u = String(m[2] || '').toUpperCase();
    return x * (u === 'T' ? 1e12 : u === 'B' ? 1e9 : u === 'M' ? 1e6 : u === 'K' ? 1e3 : 1);
  };
  const chain = (v) => {
    const s = String(v || '').toLowerCase();
    if (/solana|\bsol\b/.test(s)) return 'solana';
    if (/robinhood|rhc/.test(s)) return 'robinhood';
    if (/\bbase\b/.test(s)) return 'base';
    if (/ethereum|\beth\b|mainnet/.test(s)) return 'ethereum';
    if (/bnb|bsc|binance/.test(s)) return 'bsc';
    if (/monad/.test(s)) return 'monad';
    return '';
  };
  const validAddress = (v) => VALID_ADDR.test(String(v || '').trim());

  const keyMatch = (key, re) => re.test(String(key || '').replace(/[_\-]/g, '').toLowerCase());
  const ADDR_KEY = /^(address|tokenaddress|contractaddress|contract|mint|mintaddress|tokenmint|ca|tokenid|assetaddress|assetid|coinaddress|coinid)$/i;
  const SYMBOL_KEY = /^(symbol|ticker|tokensymbol|assetsymbol|coinsymbol)$/i;
  const NAME_KEY = /^(name|tokenname|assetname|coinname|displayname)$/i;
  const CHAIN_KEY = /^(chain|chainid|network|networkname|blockchain)$/i;
  const PRICE_KEY = /^(price|priceusd|usdprice|currentprice|tokenprice)$/i;
  const MCAP_KEY = /^(marketcap|marketcapusd|mcap|fdv|fullydilutedvaluation|valuation)$/i;
  const LIQ_KEY = /^(liquidity|liquidityusd|poolliquidity|totalliquidity)$/i;
  const VOL5_KEY = /^(volume5m|volume5musd|m5volume|vol5m)$/i;
  const VOL1_KEY = /^(volume1h|volume1husd|h1volume|vol1h)$/i;
  const BUY5_KEY = /^(buys5m|m5buys|buycount5m|buyers5m)$/i;
  const SELL5_KEY = /^(sells5m|m5sells|sellcount5m|sellers5m)$/i;

  function flattenObject(obj, depth = 0, out = []) {
    if (!obj || typeof obj !== 'object' || depth > 3 || out.length > 300) return out;
    const entries = Array.isArray(obj) ? obj.entries() : Object.entries(obj);
    let i = 0;
    for (const [k, v] of entries) {
      if (++i > 120) break;
      if (v == null) continue;
      if (typeof v === 'object') flattenObject(v, depth + 1, out);
      else out.push([String(k), v]);
    }
    return out;
  }

  function pickHeuristic(entries, re) {
    for (const [k, v] of entries) if (keyMatch(k, re)) return v;
    return undefined;
  }

  function enrichFromContext(candidate, text) {
    const s = String(text || '');
    if (!candidate.symbol) {
      const m = s.match(/(?:"|')?(?:symbol|ticker|tokenSymbol|assetSymbol)(?:"|')?\s*[:=]\s*(?:"|')([^"'\s,}]{1,24})/i);
      if (m) candidate.symbol = clean(m[1]).replace(/^\$/,'');
    }
    if (!candidate.name) {
      const m = s.match(/(?:"|')?(?:tokenName|assetName|coinName|displayName|name)(?:"|')?\s*[:=]\s*(?:"|')([^"'}]{2,90})/i);
      if (m) candidate.name = clean(m[1]);
    }
    if (!candidate.chainHint) {
      const m = s.match(/(?:"|')?(?:chain|chainId|network|blockchain)(?:"|')?\s*[:=]\s*(?:"|')([^"'\s,}]{2,32})/i);
      candidate.chainHint = chain(m?.[1] || s);
    }
    if (!candidate.priceUsd) {
      const m = s.match(/(?:"|')?(?:priceUsd|usdPrice|currentPrice|price)(?:"|')?\s*[:=]\s*(?:"|')?([0-9.eE+\-]+)/i);
      candidate.priceUsd = num(m?.[1]);
    }
    if (!candidate.marketCapUsd) {
      const m = s.match(/(?:"|')?(?:marketCapUsd|marketCap|mcap|fdv)(?:"|')?\s*[:=]\s*(?:"|')?([0-9.eE+\-]+)/i);
      candidate.marketCapUsd = num(m?.[1]);
    }
    if (!candidate.liquidityUsd) {
      const m = s.match(/(?:"|')?(?:liquidityUsd|liquidity|poolLiquidity)(?:"|')?\s*[:=]\s*(?:"|')?([0-9.eE+\-]+)/i);
      candidate.liquidityUsd = num(m?.[1]);
    }
    return candidate;
  }

  function addCandidate(candidate, source, quality = 1) {
    const address = clean(candidate?.address);
    if (!validAddress(address)) return;
    const key = address.toLowerCase();
    const current = pending.get(key) || { address, sources: [], at: Date.now(), quality: 0 };
    const next = {
      ...current,
      address,
      symbol: clean(candidate?.symbol || current.symbol).replace(/^\$/,'').slice(0, 40),
      name: clean(candidate?.name || current.name).slice(0, 100),
      chainHint: chain(candidate?.chainHint || current.chainHint),
      priceUsd: num(candidate?.priceUsd) || current.priceUsd || 0,
      marketCapUsd: num(candidate?.marketCapUsd) || current.marketCapUsd || 0,
      liquidityUsd: num(candidate?.liquidityUsd) || current.liquidityUsd || 0,
      volume5mUsd: num(candidate?.volume5mUsd) || current.volume5mUsd || 0,
      volume1hUsd: num(candidate?.volume1hUsd) || current.volume1hUsd || 0,
      buys5m: num(candidate?.buys5m) || current.buys5m || 0,
      sells5m: num(candidate?.sells5m) || current.sells5m || 0,
      context: clean(candidate?.context || current.context).slice(0, 1000),
      quality: Math.max(Number(current.quality || 0), Number(quality || 0)),
      at: Date.now(),
      sources: [...new Set([...(current.sources || []), source].filter(Boolean))].slice(-8),
    };
    pending.set(key, next);
    if (next.symbol || next.name || next.marketCapUsd || next.priceUsd) stats.structured += 1;
    scheduleFlush();
  }

  function addRawNeighborhoods(text, source) {
    const s = String(text || '');
    const matches = [...(s.matchAll(SOL_RE) || []), ...(s.matchAll(EVM_RE) || [])].slice(0, 160);
    for (const m of matches) {
      const address = m[0];
      const idx = m.index || 0;
      const context = s.slice(Math.max(0, idx - 700), Math.min(s.length, idx + address.length + 700));
      const c = enrichFromContext({ address, context }, context);
      const quality = (c.symbol || c.name ? 3 : 0) + (c.marketCapUsd || c.priceUsd ? 3 : 0) + (c.chainHint ? 1 : 0) + 1;
      addCandidate(c, source, quality);
      stats.raw += 1;
    }
  }

  function scanObject(root, source) {
    const seen = new WeakSet();
    let budget = 0;
    const walk = (value, depth, parent = null, parentKey = '') => {
      if (budget++ > 12000 || depth > 9 || value == null) return;
      if (typeof value === 'string') {
        if (value.length <= MAX_TEXT) addRawNeighborhoods(value, source);
        return;
      }
      if (typeof value !== 'object' || seen.has(value)) return;
      seen.add(value);

      if (!Array.isArray(value)) {
        const entries = flattenObject(value, 0, []);
        let address = pickHeuristic(entries, ADDR_KEY);
        if (address && typeof address === 'object') address = pickHeuristic(flattenObject(address,0,[]), ADDR_KEY);
        if (validAddress(address)) {
          const candidate = enrichFromContext({
            address,
            symbol: pickHeuristic(entries, SYMBOL_KEY),
            name: pickHeuristic(entries, NAME_KEY),
            chainHint: pickHeuristic(entries, CHAIN_KEY),
            priceUsd: pickHeuristic(entries, PRICE_KEY),
            marketCapUsd: pickHeuristic(entries, MCAP_KEY),
            liquidityUsd: pickHeuristic(entries, LIQ_KEY),
            volume5mUsd: pickHeuristic(entries, VOL5_KEY),
            volume1hUsd: pickHeuristic(entries, VOL1_KEY),
            buys5m: pickHeuristic(entries, BUY5_KEY),
            sells5m: pickHeuristic(entries, SELL5_KEY),
            context: clean(`${parentKey} ${pickHeuristic(entries,SYMBOL_KEY)||''} ${pickHeuristic(entries,NAME_KEY)||''} ${pickHeuristic(entries,CHAIN_KEY)||''}`),
          }, JSON.stringify(value).slice(0, 5000));
          const q = (candidate.symbol || candidate.name ? 4 : 0) + (candidate.marketCapUsd || candidate.priceUsd ? 4 : 0) + (candidate.chainHint ? 1 : 0) + 2;
          addCandidate(candidate, source, q);
        }
      }

      const entries = Array.isArray(value) ? value.entries() : Object.entries(value);
      let count = 0;
      for (const [k, child] of entries) {
        if (++count > 450) break;
        if (typeof child === 'object' && child != null) walk(child, depth + 1, value, String(k));
        else if (typeof child === 'string' && child.length < 180000 && /0x[a-fA-F0-9]{40}|[1-9A-HJ-NP-Za-km-z]{32,44}/.test(child)) addRawNeighborhoods(child, source);
      }
    };
    walk(root, 0, null, source);
  }

  function ingestText(text, source) {
    const s = String(text || '');
    if (!s || s.length > MAX_TEXT) return;
    stats.messages += 1; stats.lastSource = source; stats.lastAt = Date.now();
    addRawNeighborhoods(s, source);
    const first = s.trim()[0];
    if (first !== '{' && first !== '[') return;
    try { stats.json += 1; scanObject(JSON.parse(s), source); } catch { /* non-JSON */ }
  }

  function scheduleFlush() {
    clearTimeout(flushTimer);
    flushTimer = setTimeout(() => {
      const now = Date.now();
      const candidates = [...pending.values()]
        .filter((x) => now - x.at < 150000)
        .sort((a,b) => (b.quality||0)-(a.quality||0) || (Boolean(b.symbol||b.name)-Boolean(a.symbol||a.name)) || b.at-a.at)
        .slice(0, 120);
      window.postMessage({ __neoMemeCoins: true, type: 'NEO_PAGE_FEED_081', candidates, stats: { ...stats, candidateCount:candidates.length }, at: now }, '*');
      for (const [k,v] of pending) if (now - v.at > 240000) pending.delete(k);
    }, 120);
  }

  const nativeFetch = window.fetch?.bind(window);
  if (nativeFetch) {
    window.fetch = async (...args) => {
      const response = await nativeFetch(...args);
      try {
        const url = String(args?.[0]?.url || args?.[0] || 'fetch');
        response.clone().text().then((t) => ingestText(t, `fetch:${url.slice(0,220)}`)).catch(()=>{});
      } catch {}
      return response;
    };
  }

  try {
    const nativeOpen = XMLHttpRequest.prototype.open;
    const nativeSend = XMLHttpRequest.prototype.send;
    XMLHttpRequest.prototype.open = function(method,url,...rest){ this.__neoUrl = String(url||''); return nativeOpen.call(this,method,url,...rest); };
    XMLHttpRequest.prototype.send = function(...args){
      this.addEventListener('load',()=>{ try{ if(typeof this.responseText==='string') ingestText(this.responseText,`xhr:${String(this.responseURL||this.__neoUrl||'').slice(0,220)}`); }catch{} },{once:true});
      return nativeSend.apply(this,args);
    };
  } catch {}

  try {
    const NativeWS = window.WebSocket;
    if (NativeWS) window.WebSocket = new Proxy(NativeWS,{construct(Target,args){
      const ws = new Target(...args);
      ws.addEventListener('message',async(event)=>{try{
        const source=`ws:${String(args[0]||'').slice(0,220)}`;
        if(typeof event.data==='string')ingestText(event.data,source);
        else if(event.data instanceof Blob && event.data.size<=MAX_TEXT)ingestText(await event.data.text(),source);
        else if(event.data instanceof ArrayBuffer && event.data.byteLength<=MAX_TEXT)ingestText(new TextDecoder().decode(event.data),source);
      }catch{}});
      return ws;
    }});
  } catch {}

  try {
    const NativeES = window.EventSource;
    if (NativeES) window.EventSource = new Proxy(NativeES,{construct(Target,args){
      const es=new Target(...args); es.addEventListener('message',(event)=>{try{ingestText(event.data,`sse:${String(args[0]||'').slice(0,220)}`);}catch{}}); return es;
    }});
  } catch {}

  setTimeout(()=>{
    try {
      for(const script of [...document.scripts].slice(0,180)){
        const text=String(script.textContent||'');
        if(text && text.length<=MAX_TEXT && /token|mint|contract|address|symbol|marketCap|mcap/i.test(text)) ingestText(text,'hydration-script');
      }
    } catch {}
    scheduleFlush();
  },350);
})();