(() => {
  const host = location.hostname.toLowerCase();
  if (!(host === 'fomo.family' || host.endsWith('.fomo.family'))) return;
  if (window.__NEO_FOMO_DISCOVERY_V061__) return;
  window.__NEO_FOMO_DISCOVERY_V061__ = true;

  const ROOT_ID = 'neo-fomo-resolved-candidates';
  const SCAN_MS = 6000;
  const BASE58_RE = /\b[1-9A-HJ-NP-Za-km-z]{32,44}\b/g;
  let running = false;
  let lastFingerprint = '';
  let mutationTimer = null;

  const clean = (raw) => String(raw || '').replace(/\s+/g, ' ').trim();

  function parseCompactMoney(raw) {
    const match = String(raw || '').replace(/,/g, '').match(/([0-9]*\.?[0-9]+)\s*([KMB])?/i);
    if (!match) return 0;
    const value = Number(match[1]) || 0;
    const unit = String(match[2] || '').toUpperCase();
    return value * (unit === 'B' ? 1e9 : unit === 'M' ? 1e6 : unit === 'K' ? 1e3 : 1);
  }

  function visible(node) {
    if (!(node instanceof HTMLElement)) return false;
    const rect = node.getBoundingClientRect();
    return rect.width >= 20 && rect.height >= 10 && rect.bottom >= -100 && rect.top <= innerHeight + 100;
  }

  const STOP = new Set([
    'buy','sell','buys','sells','all swaps','action','amount','mcap','market cap','time','tokens','token','trending','watchlist','crypto','most held','alerts','leaderboard','feed','home','portfolio','positions','orders','holders','top traders','dev tokens','follow','following','followers','share','deposit','withdraw','cash','volume','vol','buyers','sellers','price','liquidity','liq','chart','auto','manual','search','settings','trade','swap','live','new','graduated','graduating'
  ]);

  function usableLabel(raw) {
    let value = clean(raw).replace(/^\$/,'').trim();
    if (!value || value.length < 2 || value.length > 42) return false;
    if (STOP.has(value.toLowerCase())) return false;
    if (/^\$?[0-9.,]+(?:[KMB])?(?:\s*MC)?$/i.test(value)) return false;
    if (/^[+\-▲▼]?\s*[0-9.,]+%$/.test(value)) return false;
    if (/^(h|d|w|m|min|hr|hrs|day|days|week|weeks|24h|7d|30d|all)$/i.test(value)) return false;
    if (/https?:\/\//i.test(value)) return false;
    if (/^[0-9a-f]{16,}$/i.test(value)) return false;
    if (/^[1-9A-HJ-NP-Za-km-z]{32,44}$/.test(value)) return false;
    return /[A-Za-z\u0080-\uFFFF]/.test(value);
  }

  function extractMetrics(text) {
    const raw = String(text || '');
    let marketCapUsd = 0;
    const mcPatterns = [
      /\$\s*([0-9.,]+\s*[KMB]?)\s*(?:MC|MCap)\b/i,
      /(?:MC|MCap|Market\s*Cap)\s*[:·-]?\s*\$?\s*([0-9.,]+\s*[KMB]?)/i,
    ];
    for (const re of mcPatterns) {
      const m = raw.match(re);
      if (m) { marketCapUsd = parseCompactMoney(m[1]); if (marketCapUsd) break; }
    }

    let priceUsd = 0;
    const priceMatches = [...raw.matchAll(/\$\s*(0?\.[0-9]+|[0-9][0-9.,]*(?:\.[0-9]+)?)/g)];
    for (const match of priceMatches) {
      const around = raw.slice(Math.max(0, match.index - 12), Math.min(raw.length, (match.index || 0) + match[0].length + 12));
      if (/MC|MCap|Market\s*Cap/i.test(around)) continue;
      const v = Number(String(match[1]).replace(/,/g,''));
      if (Number.isFinite(v) && v > 0 && (!priceUsd || v < priceUsd)) priceUsd = v;
    }
    return { marketCapUsd, priceUsd };
  }

  function parseAnchoredBlock(text) {
    const raw = String(text || '');
    const metrics = extractMetrics(raw);
    if (!metrics.marketCapUsd) return null;
    const lines = raw.split(/\n+/).map(clean).filter(Boolean);
    let query = '';
    const metricIndex = lines.findIndex((line) => /\b(?:MC|MCap|Market\s*Cap)\b/i.test(line));
    const start = metricIndex >= 0 ? metricIndex - 1 : lines.length - 1;
    for (let i = Math.min(start, lines.length - 1); i >= Math.max(0, start - 6); i -= 1) {
      if (usableLabel(lines[i])) { query = lines[i].replace(/^\$/,'').trim(); break; }
    }
    if (!query) {
      for (const line of lines.slice(0,6)) if (usableLabel(line)) { query = line.replace(/^\$/,'').trim(); break; }
    }
    if (!query) return null;
    return { query, ...metrics, context: lines.slice(0,12).join(' · ').slice(0,600) };
  }

  function collectAnchored() {
    const found = new Map();
    const add = (item) => {
      if (!item?.query || !item.marketCapUsd) return;
      const key = `${item.query.toLowerCase()}|${Math.round(item.marketCapUsd/1000)}`;
      if (!found.has(key)) found.set(key,item);
    };
    const selectors = 'tr,[role="row"],li,a,button,[class*="token"],[class*="coin"],[class*="card"],[class*="row"]';
    for (const node of [...document.querySelectorAll(selectors)].slice(0,6500)) {
      if (!visible(node) || node.closest?.(`#${ROOT_ID},#neo-meme-coins-root`)) continue;
      const text = node.innerText || '';
      if (text.length < 4 || text.length > 900 || !/\b(?:MC|MCap|Market\s*Cap)\b/i.test(text)) continue;
      add(parseAnchoredBlock(text));
      if (found.size >= 28) break;
    }
    return [...found.values()].slice(0,18);
  }

  function collectLooseLabels() {
    const found = new Map();
    const add = (query, context = '', metrics = {}) => {
      query = clean(query).replace(/^\$/,'').trim();
      if (!usableLabel(query)) return;
      const key = query.toLowerCase();
      const next = { query, marketCapUsd: metrics.marketCapUsd || 0, priceUsd: metrics.priceUsd || 0, context: clean(context).slice(0,650) };
      const current = found.get(key);
      const score = (next.marketCapUsd ? 3 : 0) + (next.priceUsd ? 2 : 0) + (next.context ? 1 : 0);
      const curScore = current ? (current.marketCapUsd ? 3 : 0) + (current.priceUsd ? 2 : 0) + (current.context ? 1 : 0) : -1;
      if (!current || score > curScore) found.set(key,next);
    };

    const titleParts = String(document.title || '').split(/[|·–—-]/).map(clean).filter(Boolean);
    for (const part of titleParts.slice(0,5)) if (!/fomo/i.test(part)) add(part, document.title);

    const selectors = 'h1,h2,h3,[class*="symbol"],[class*="ticker"],[class*="token"],[class*="coin"],[class*="pair"],tr,[role="row"],li,a,button';
    for (const node of [...document.querySelectorAll(selectors)].slice(0,7000)) {
      if (!visible(node) || node.closest?.(`#${ROOT_ID},#neo-meme-coins-root`)) continue;
      const text = String(node.innerText || '');
      if (text.length < 2 || text.length > 500) continue;
      const contextSignal = /\$\s*[0-9]|%|\b(?:vol|volume|buyers|sellers|liq|liquidity|MC|MCap|market cap|buy|sell)\b/i.test(text);
      const hrefSignal = /token|coin|trade|swap|pair|market/i.test(node.getAttribute?.('href') || '');
      if (!contextSignal && !hrefSignal && !/^[\s$A-Za-z0-9_.\-]{2,22}$/.test(text.trim())) continue;
      const lines = text.split(/\n+/).map(clean).filter(Boolean);
      const metrics = extractMetrics(text);
      for (const line of lines.slice(0,5)) {
        const symbolMatch = line.match(/^\$([A-Za-z0-9_.\-]{2,18})$/);
        if (symbolMatch) { add(symbolMatch[1],text,metrics); break; }
        if (usableLabel(line) && line.length <= 30) { add(line,text,metrics); break; }
      }
      if (found.size >= 28) break;
    }
    return [...found.values()].slice(0,16);
  }

  function collectRawAddresses() {
    const scores = new Map();
    const add = (raw, points = 1) => {
      for (const token of String(raw || '').match(BASE58_RE) || []) scores.set(token,(scores.get(token)||0)+points);
    };
    add(location.href,20);

    for (const node of [...document.querySelectorAll('a[href],[data-token-address],[data-mint],[data-address],[data-ca],[data-contract],[data-token],[data-coin-address]')].slice(0,6500)) {
      if (node.closest?.(`#${ROOT_ID},#neo-meme-coins-root`)) continue;
      add(node.getAttribute?.('href'),8);
      for (const key of ['data-token-address','data-mint','data-address','data-ca','data-contract','data-token','data-coin-address']) add(node.getAttribute?.(key),15);
    }

    try {
      for (const entry of performance.getEntriesByType('resource').slice(-1200)) add(entry?.name,6);
    } catch { /* ignore */ }

    let scriptBudget = 900000;
    for (const script of [...document.scripts].slice(0,120)) {
      if (scriptBudget <= 0) break;
      const text = String(script.textContent || '').slice(0,Math.min(scriptBudget,120000));
      scriptBudget -= text.length;
      if (/token|mint|address|pair|solana/i.test(text)) add(text,4);
    }

    const bodyText = String(document.body?.innerText || '').slice(0,280000);
    add(bodyText,3);

    return [...scores.entries()].sort((a,b)=>b[1]-a[1]).map(([token])=>token).slice(0,40);
  }

  function rootNode() {
    let root = document.getElementById(ROOT_ID);
    if (!root) {
      root = document.createElement('div');
      root.id = ROOT_ID;
      root.style.display = 'none';
      root.setAttribute('aria-hidden','true');
      document.documentElement.appendChild(root);
    }
    return root;
  }

  function injectResolved(results) {
    const root = rootNode();
    root.replaceChildren();
    const seen = new Set();
    for (const item of results || []) {
      if (!item?.token || seen.has(item.token)) continue;
      seen.add(item.token);
      const row = document.createElement('div');
      row.className = 'neo-token-discovery';
      row.setAttribute('data-token-address',item.token);
      row.setAttribute('data-coin-address',item.token);
      row.setAttribute('data-resolution-score',String(item.resolutionScore || 0));
      row.setAttribute('data-neo-symbol',String(item.symbol || item.query || ''));
      row.setAttribute('data-neo-query',String(item.query || item.symbol || ''));
      row.setAttribute('data-neo-mcap',String(item.marketCapUsd || 0));
      row.setAttribute('data-neo-discovery-source',String(item.source || 'resolved'));
      row.textContent = `${item.symbol || item.query || 'TOKEN'} ${item.name || ''} ${item.context || ''}`;
      root.appendChild(row);
    }
  }

  function mergeResults(groups) {
    const byToken = new Map();
    for (const group of groups) {
      for (const item of group || []) {
        if (!item?.token) continue;
        const current = byToken.get(item.token);
        const conf = Number(item.resolutionConfidence || item.resolutionScore || 0);
        const curConf = Number(current?.resolutionConfidence || current?.resolutionScore || 0);
        if (!current || conf > curConf) byToken.set(item.token,item);
      }
    }
    return [...byToken.values()].sort((a,b)=>Number(b.resolutionConfidence||b.resolutionScore||0)-Number(a.resolutionConfidence||a.resolutionScore||0)).slice(0,18);
  }

  async function scan(force = false) {
    if (running) return;
    const anchored = collectAnchored();
    const loose = collectLooseLabels();
    const addresses = collectRawAddresses();
    const fingerprint = `${anchored.map(x=>`${x.query}:${Math.round(x.marketCapUsd/1000)}`).join('|')}#${loose.map(x=>x.query).join('|')}#${addresses.slice(0,12).join('|')}`;
    const existing = document.getElementById(ROOT_ID)?.children.length || 0;
    if (!force && fingerprint === lastFingerprint && existing) return;

    running = true;
    try {
      const tasks = [];
      tasks.push(anchored.length ? chrome.runtime.sendMessage({type:'NEO_RESOLVE_VISIBLE_TOKENS',items:anchored}) : Promise.resolve({ok:true,results:[]}));
      tasks.push(loose.length ? chrome.runtime.sendMessage({type:'NEO_RESOLVE_LOOSE_LABELS',items:loose}) : Promise.resolve({ok:true,results:[]}));
      tasks.push(addresses.length ? chrome.runtime.sendMessage({type:'NEO_VALIDATE_DISCOVERY_ADDRESSES',addresses}) : Promise.resolve({ok:true,results:[]}));
      const settled = await Promise.allSettled(tasks);
      const groups = settled.map((r)=>r.status==='fulfilled' && r.value?.ok ? r.value.results || [] : []);
      const merged = mergeResults(groups);
      injectResolved(merged);
      lastFingerprint = fingerprint;
      document.documentElement.setAttribute('data-neo-fomo-discovered',String(merged.length));
      document.documentElement.setAttribute('data-neo-fomo-last-scan',String(Date.now()));
    } catch {
      document.documentElement.setAttribute('data-neo-fomo-discovery-error',String(Date.now()));
    } finally { running = false; }
  }

  const schedule = (delay = 350) => {
    clearTimeout(mutationTimer);
    mutationTimer = setTimeout(()=>scan(false),delay);
  };

  setTimeout(()=>scan(true),350);
  setInterval(()=>scan(false),SCAN_MS);
  document.addEventListener('click',()=>schedule(250),true);
  const observer = new MutationObserver(()=>schedule(900));
  observer.observe(document.documentElement,{subtree:true,childList:true,attributes:true,attributeFilter:['href','data-token-address','data-mint','data-address','data-ca','data-contract','data-token','data-coin-address']});
})();
