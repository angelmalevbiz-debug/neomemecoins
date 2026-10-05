(() => {
  if (window.__NEO_PAGE_TAP_V08__) return;
  window.__NEO_PAGE_TAP_V08__ = true;

  const SOL_RE = /\b[1-9A-HJ-NP-Za-km-z]{32,44}\b/g;
  const EVM_RE = /\b0x[a-fA-F0-9]{40}\b/g;
  const MAX_TEXT = 1_500_000;
  const pending = new Map();
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
  const validAddress = (v) => /^(?:0x[a-fA-F0-9]{40}|[1-9A-HJ-NP-Za-km-z]{32,44})$/.test(String(v || '').trim());
  const chain = (v) => {
    const s = String(v || '').toLowerCase();
    if (!s) return '';
    if (/solana|\bsol\b/.test(s)) return 'solana';
    if (/robinhood/.test(s)) return 'robinhood';
    if (/base/.test(s)) return 'base';
    if (/ethereum|\beth\b|mainnet/.test(s)) return 'ethereum';
    if (/bnb|bsc|binance/.test(s)) return 'bsc';
    if (/monad/.test(s)) return 'monad';
    return clean(v).slice(0, 32).toLowerCase();
  };

  const ADDRESS_KEYS = ['address','tokenAddress','token_address','contractAddress','contract_address','contract','mint','mintAddress','mint_address','tokenMint','token_mint','ca','token'];
  const SYMBOL_KEYS = ['symbol','ticker','tokenSymbol','token_symbol'];
  const NAME_KEYS = ['name','tokenName','token_name'];
  const CHAIN_KEYS = ['chainId','chain_id','chain','network','networkName','network_name'];
  const PRICE_KEYS = ['priceUsd','price_usd','usdPrice','usd_price','price','currentPrice'];
  const MCAP_KEYS = ['marketCap','market_cap','marketCapUsd','market_cap_usd','mcap','fdv','fullyDilutedValuation'];
  const LIQ_KEYS = ['liquidityUsd','liquidity_usd','liquidity','poolLiquidity','pool_liquidity'];
  const VOL5_KEYS = ['volume5m','volume_5m','volume5mUsd','volume_5m_usd','m5Volume'];
  const VOL1H_KEYS = ['volume1h','volume_1h','volume1hUsd','volume_1h_usd','h1Volume'];
  const BUYS5_KEYS = ['buys5m','buys_5m','m5Buys','buyCount5m'];
  const SELLS5_KEYS = ['sells5m','sells_5m','m5Sells','sellCount5m'];

  function pick(obj, keys) {
    for (const k of keys) {
      if (obj && Object.prototype.hasOwnProperty.call(obj, k) && obj[k] != null) return obj[k];
    }
    return undefined;
  }

  function addCandidate(candidate, source) {
    const address = clean(candidate?.address);
    if (!validAddress(address)) return;
    const key = address.toLowerCase();
    const current = pending.get(key) || { address, sources: [], at: Date.now() };
    const next = {
      ...current,
      address,
      symbol: clean(candidate?.symbol || current.symbol).slice(0, 40),
      name: clean(candidate?.name || current.name).slice(0, 100),
      chainHint: chain(candidate?.chainHint || current.chainHint),
      priceUsd: num(candidate?.priceUsd) || current.priceUsd || 0,
      marketCapUsd: num(candidate?.marketCapUsd) || current.marketCapUsd || 0,
      liquidityUsd: num(candidate?.liquidityUsd) || current.liquidityUsd || 0,
      volume5mUsd: num(candidate?.volume5mUsd) || current.volume5mUsd || 0,
      volume1hUsd: num(candidate?.volume1hUsd) || current.volume1hUsd || 0,
      buys5m: num(candidate?.buys5m) || current.buys5m || 0,
      sells5m: num(candidate?.sells5m) || current.sells5m || 0,
      context: clean(candidate?.context || current.context).slice(0, 700),
      at: Date.now(),
      sources: [...new Set([...(current.sources || []), source].filter(Boolean))].slice(-6),
    };
    pending.set(key, next);
    scheduleFlush();
  }

  function addRawAddresses(text, source) {
    const s = String(text || '');
    for (const address of [...(s.match(SOL_RE) || []), ...(s.match(EVM_RE) || [])].slice(0, 120)) {
      addCandidate({ address, context: source }, source);
    }
  }

  function scanObject(root, source) {
    const seen = new WeakSet();
    let budget = 0;
    const walk = (value, depth, parentContext = '') => {
      if (budget++ > 7000 || depth > 7 || value == null) return;
      if (typeof value === 'string') {
        if (value.length <= MAX_TEXT) addRawAddresses(value, source);
        return;
      }
      if (typeof value !== 'object') return;
      if (seen.has(value)) return;
      seen.add(value);

      if (!Array.isArray(value)) {
        let address = pick(value, ADDRESS_KEYS);
        if (address && typeof address === 'object') address = pick(address, ADDRESS_KEYS);
        if (validAddress(address)) {
          const symbol = pick(value, SYMBOL_KEYS);
          const name = pick(value, NAME_KEYS);
          const chainHint = pick(value, CHAIN_KEYS);
          const context = clean(`${parentContext} ${symbol || ''} ${name || ''} ${chainHint || ''}`);
          addCandidate({
            address,
            symbol,
            name,
            chainHint,
            priceUsd: pick(value, PRICE_KEYS),
            marketCapUsd: pick(value, MCAP_KEYS),
            liquidityUsd: pick(value, LIQ_KEYS),
            volume5mUsd: pick(value, VOL5_KEYS),
            volume1hUsd: pick(value, VOL1H_KEYS),
            buys5m: pick(value, BUYS5_KEYS),
            sells5m: pick(value, SELLS5_KEYS),
            context,
          }, source);
        }
      }

      const entries = Array.isArray(value) ? value.entries() : Object.entries(value);
      let count = 0;
      for (const [k, child] of entries) {
        if (++count > 300) break;
        if (typeof child === 'object' && child != null) walk(child, depth + 1, `${parentContext} ${k}`);
        else if (typeof child === 'string' && child.length < 120000 && /0x[a-fA-F0-9]{40}|[1-9A-HJ-NP-Za-km-z]{32,44}/.test(child)) addRawAddresses(child, source);
      }
    };
    walk(root, 0, source);
  }

  function ingestText(text, source) {
    const s = String(text || '');
    if (!s || s.length > MAX_TEXT) return;
    addRawAddresses(s, source);
    const first = s.trim()[0];
    if (first !== '{' && first !== '[') return;
    try { scanObject(JSON.parse(s), source); } catch { /* non-JSON feed */ }
  }

  function scheduleFlush() {
    clearTimeout(flushTimer);
    flushTimer = setTimeout(() => {
      const now = Date.now();
      const candidates = [...pending.values()]
        .filter((x) => now - x.at < 120000)
        .sort((a, b) => (b.marketCapUsd > 0) - (a.marketCapUsd > 0) || b.at - a.at)
        .slice(0, 80);
      window.postMessage({ __neoMemeCoins: true, type: 'NEO_PAGE_FEED', candidates, at: now }, '*');
      for (const [k, v] of pending) if (now - v.at > 180000) pending.delete(k);
    }, 180);
  }

  const nativeFetch = window.fetch?.bind(window);
  if (nativeFetch) {
    window.fetch = async (...args) => {
      const response = await nativeFetch(...args);
      try {
        const url = String(args?.[0]?.url || args?.[0] || 'fetch');
        const clone = response.clone();
        clone.text().then((text) => ingestText(text, `fetch:${url.slice(0,180)}`)).catch(() => {});
      } catch { /* transparent */ }
      return response;
    };
  }

  try {
    const nativeSend = XMLHttpRequest.prototype.send;
    XMLHttpRequest.prototype.send = function(...args) {
      this.addEventListener('load', () => {
        try {
          if (typeof this.responseText === 'string') ingestText(this.responseText, `xhr:${String(this.responseURL || '').slice(0,180)}`);
        } catch { /* binary/blocked */ }
      }, { once: true });
      return nativeSend.apply(this, args);
    };
  } catch { /* ignore */ }

  try {
    const NativeWS = window.WebSocket;
    if (NativeWS) {
      window.WebSocket = new Proxy(NativeWS, {
        construct(Target, args) {
          const ws = new Target(...args);
          ws.addEventListener('message', async (event) => {
            try {
              if (typeof event.data === 'string') ingestText(event.data, `ws:${String(args[0] || '').slice(0,180)}`);
              else if (event.data instanceof Blob && event.data.size <= MAX_TEXT) ingestText(await event.data.text(), `ws:${String(args[0] || '').slice(0,180)}`);
              else if (event.data instanceof ArrayBuffer && event.data.byteLength <= MAX_TEXT) ingestText(new TextDecoder().decode(event.data), `ws:${String(args[0] || '').slice(0,180)}`);
            } catch { /* binary/protobuf feeds may not be readable */ }
          });
          return ws;
        },
      });
    }
  } catch { /* ignore */ }

  try {
    const NativeES = window.EventSource;
    if (NativeES) {
      window.EventSource = new Proxy(NativeES, {
        construct(Target, args) {
          const es = new Target(...args);
          es.addEventListener('message', (event) => {
            try { ingestText(event.data, `sse:${String(args[0] || '').slice(0,180)}`); } catch { /* ignore */ }
          });
          return es;
        },
      });
    }
  } catch { /* ignore */ }

  // Some apps hydrate useful token state before the wrappers are installed.
  setTimeout(() => {
    try {
      for (const script of [...document.scripts].slice(0, 160)) {
        const text = String(script.textContent || '');
        if (text && text.length <= MAX_TEXT && /token|mint|contract|address/i.test(text)) ingestText(text, 'hydration-script');
      }
    } catch { /* ignore */ }
    scheduleFlush();
  }, 500);
})();
