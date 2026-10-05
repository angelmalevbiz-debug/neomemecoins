(() => {
  const BASE58_RE = /\b[1-9A-HJ-NP-Za-km-z]{32,44}\b/g;
  const RADAR_INTERVAL_MS = 10_000;
  const AUTO_DEEP_COOLDOWN_MS = 8 * 60_000;
  const MAX_RADAR_CANDIDATES = 14;
  const MAX_AUTO_DEEP_PER_CYCLE = 3;
  const VALIDATION_KEY = 'neo-sentinel-validation-v1';
  const SOCIAL_HOSTS = ['x.com','twitter.com','t.me','telegram.me','discord.gg','discord.com','youtube.com','youtu.be','instagram.com','tiktok.com'];

  let radarTimer = null;
  let radarScanning = false;
  let deepScanning = false;
  let collapsed = false;
  let observedCount = 0;
  let radarResults = [];
  let deepResults = [];
  let validationLog = [];
  let lastSeenUrl = location.href;
  const deepCooldown = new Map();
  const deepQueue = [];
  const queued = new Set();

  const previous = document.getElementById('neo-meme-coins-root');
  if (previous) previous.remove();

  const num = (v) => Number.isFinite(Number(v)) ? Number(v) : 0;
  const clamp = (v, min = 0, max = 100) => Math.max(min, Math.min(max, v));
  const short = (v, size = 5) => {
    const s = String(v || '');
    return s.length > size * 2 + 2 ? `${s.slice(0, size)}…${s.slice(-size)}` : s;
  };
  const esc = (v) => String(v ?? '').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;').replaceAll("'",'&#039;');
  const money = (v) => {
    const n = num(v);
    if (n >= 1e9) return `$${(n / 1e9).toFixed(2)}B`;
    if (n >= 1e6) return `$${(n / 1e6).toFixed(2)}M`;
    if (n >= 1e3) return `$${(n / 1e3).toFixed(1)}K`;
    return `$${n.toFixed(n < 1 ? 4 : 0)}`;
  };
  const sourceName = () => {
    const h = location.hostname.toLowerCase();
    if (h === 'fomo.family' || h.endsWith('.fomo.family')) return 'FOMO';
    if (h.includes('axiom.trade')) return 'AXIOM';
    if (h.includes('tinyastro.io')) return 'PHOTON';
    return 'TERMINAL';
  };

  async function loadValidation() {
    try {
      const stored = await chrome.storage.local.get([VALIDATION_KEY]);
      validationLog = Array.isArray(stored[VALIDATION_KEY]) ? stored[VALIDATION_KEY].slice(-250) : [];
    } catch { validationLog = []; }
  }

  async function saveValidation() {
    try { await chrome.storage.local.set({ [VALIDATION_KEY]: validationLog.slice(-250) }); } catch { /* local storage can fail */ }
  }

  function validationSummary() {
    const scoreWindow = (field) => {
      const rows = validationLog.filter((x) => x[field] && x.direction !== 'NEUTRAL');
      const correct = rows.filter((x) => x[field].correct).length;
      return { total: rows.length, correct, pct: rows.length ? Math.round(correct / rows.length * 100) : null };
    };
    return { m15: scoreWindow('m15'), m60: scoreWindow('m60') };
  }

  function recordPrediction(result) {
    const price = num(result?.market?.priceUsd);
    if (!result?.tokenAddress || !price || !result?.outlook) return;
    const score = num(result.outlook.score);
    const direction = score >= 60 ? 'UP' : score <= 40 ? 'DOWN' : 'NEUTRAL';
    if (validationLog.some((x) => x.token === result.tokenAddress && Date.now() - x.at < 10 * 60_000)) return;
    validationLog.push({
      token: result.tokenAddress,
      symbol: result.market.symbol || '',
      at: Date.now(),
      entryPrice: price,
      outlookScore: score,
      direction,
      posture: result.posture,
      m15: null,
      m60: null,
    });
    saveValidation();
  }

  function updateValidationFromRadar(rows) {
    if (!validationLog.length || !rows.length) return;
    const byToken = new Map(rows.map((x) => [x.tokenAddress, x]));
    let changed = false;
    for (const item of validationLog) {
      const live = byToken.get(item.token);
      const price = num(live?.priceUsd);
      if (!price || !item.entryPrice) continue;
      const age = Date.now() - item.at;
      const changePct = ((price - item.entryPrice) / item.entryPrice) * 100;
      const judge = () => {
        if (item.direction === 'UP') return changePct >= 2;
        if (item.direction === 'DOWN') return changePct <= -2;
        return Math.abs(changePct) < 5;
      };
      if (age >= 15 * 60_000 && !item.m15) { item.m15 = { changePct, correct: judge(), at: Date.now() }; changed = true; }
      if (age >= 60 * 60_000 && !item.m60) { item.m60 = { changePct, correct: judge(), at: Date.now() }; changed = true; }
    }
    if (changed) saveValidation();
  }

  function pageIntelligence() {
    const text = String(document.body?.innerText || '').slice(0, 70_000).toLowerCase();
    return [
      text.includes('trending') ? 'trending' : '',
      /leaderboard|top traders|trades|pnl/.test(text) ? 'traders' : '',
      /swaps|buys|sells/.test(text) ? 'flow' : '',
      text.includes('alerts') ? 'alerts' : '',
      /news|viral|community|twitter|telegram/.test(text) ? 'narrative' : '',
    ].filter(Boolean);
  }

  function addAddress(map, raw, score, reason, context = '', meta = {}) {
    const matches = String(raw || '').match(BASE58_RE) || [];
    for (const token of matches) {
      const current = map.get(token) || { token, score: 0, reasons: [], context: [], meta: {} };
      current.score += score;
      if (reason && !current.reasons.includes(reason)) current.reasons.push(reason);
      if (context && current.context.length < 5) current.context.push(String(context).replace(/\s+/g,' ').slice(0, 280));
      current.meta = { ...current.meta, ...meta };
      map.set(token, current);
    }
  }

  function collectCandidates() {
    const map = new Map();
    addAddress(map, location.href, 220, 'url', location.pathname);

    const nodes = [...document.querySelectorAll('a[href],[data-token-address],[data-mint],[data-address],[data-ca],[data-contract],[data-token],[data-coin-address]')].slice(0, 6000);
    for (const node of nodes) {
      const href = node.getAttribute?.('href') || '';
      const attrs = ['data-token-address','data-mint','data-address','data-ca','data-contract','data-token','data-coin-address']
        .map((k) => node.getAttribute?.(k)).filter(Boolean).join(' ');
      const container = node.closest?.('tr,[role="row"],article,li,[class*="card"],[class*="token"],[class*="coin"]');
      const nearby = String(container?.textContent || node.textContent || '').trim().replace(/\s+/g,' ').slice(0, 360);
      const combined = `${href} ${attrs} ${nearby}`;
      const injected = node.classList?.contains('neo-token-discovery') || node.closest?.('#neo-fomo-resolved-candidates');
      const resolutionScore = num(node.getAttribute?.('data-resolution-score'));
      const meta = {
        symbol: node.getAttribute?.('data-neo-symbol') || '',
        query: node.getAttribute?.('data-neo-query') || '',
        marketCapUsd: num(node.getAttribute?.('data-neo-mcap')),
        resolutionScore,
        injected: Boolean(injected),
      };
      let score = 22;
      let reason = 'page-link';
      if (/solscan\.io\/token\/|pump\.fun\/coin\/|dexscreener\.com\/solana\//i.test(combined)) { score = 165; reason = 'verified-token-link'; }
      else if (injected) { score = 145 + Math.min(30, resolutionScore / 4); reason = 'fomo-resolved'; }
      else if (attrs) { score = 135; reason = 'token-attribute'; }
      else if (/\/(token|coin|trade|swap|pair)\//i.test(href)) { score = 108; reason = 'token-route'; }
      addAddress(map, combined, score, reason, nearby || href, meta);
    }

    const raw = [...map.values()].filter((x) => x.score >= 65).sort((a,b) => b.score - a.score);
    const bySymbol = new Map();
    const out = [];
    for (const item of raw) {
      const symbol = String(item.meta?.symbol || '').trim().toLowerCase();
      if (!symbol) { out.push(item); continue; }
      const existing = bySymbol.get(symbol);
      if (!existing) { bySymbol.set(symbol, item); out.push(item); continue; }
      const a = num(existing.meta?.resolutionScore);
      const b = num(item.meta?.resolutionScore);
      if (b > a + 6) {
        const idx = out.indexOf(existing);
        if (idx >= 0) out[idx] = item;
        bySymbol.set(symbol, item);
      }
    }
    observedCount = out.length;
    return out.slice(0, MAX_RADAR_CANDIDATES);
  }

  function collectPageContext(candidate, focused = false) {
    const socialLinks = [];
    if (focused) {
      for (const a of [...document.querySelectorAll('a[href]')].slice(0, 4000)) {
        try {
          const u = new URL(a.href);
          const h = u.hostname.toLowerCase();
          if (SOCIAL_HOSTS.some((x) => h === x || h.endsWith(`.${x}`))) socialLinks.push(a.href);
        } catch { /* ignore */ }
        if (socialLinks.length >= 24) break;
      }
    }
    return {
      source: sourceName(),
      url: location.href,
      title: document.title,
      text: focused ? String(document.body?.innerText || '').slice(0, 40_000) : String(candidate?.context?.join(' ') || '').slice(0, 5000),
      socialLinks: [...new Set(socialLinks)].slice(0, 20),
      observationMode: focused ? 'focused-coin' : 'sentinel-auto-deep',
      sourceMeta: candidate?.meta || {},
    };
  }

  const host = document.createElement('div');
  host.id = 'neo-meme-coins-root';
  Object.assign(host.style, { all:'initial', position:'fixed', zIndex:'2147483647', right:'14px', top:'66px' });
  document.documentElement.appendChild(host);
  const shadow = host.attachShadow({ mode:'open' });
  shadow.innerHTML = `
    <style>
      *{box-sizing:border-box}button,a{font:inherit}button{cursor:pointer}.neo{width:404px;max-height:calc(100vh - 82px);overflow:hidden;border:1px solid rgba(255,255,255,.12);border-radius:18px;background:rgba(7,9,12,.98);color:#e8edf2;font-family:Inter,system-ui,sans-serif;box-shadow:0 20px 65px rgba(0,0,0,.55);backdrop-filter:blur(18px)}
      .head{display:flex;align-items:center;gap:10px;padding:12px 13px;border-bottom:1px solid rgba(255,255,255,.08)}.mark{width:32px;height:32px;display:grid;place-items:center;border-radius:10px;background:#34d399;color:#06100c;font-weight:1000}.brand{font-size:12px;font-weight:950;color:#fff}.sub{font-size:8.5px;color:#657081;margin-top:2px}.source{margin-left:auto;font-size:8px;font-weight:900;color:#6ee7b7;border:1px solid rgba(52,211,153,.22);padding:4px 6px;border-radius:7px}.collapse{border:0;background:transparent;color:#7d8796;font-size:16px}.body{max-height:calc(100vh - 140px);overflow:auto;padding:12px}.body::-webkit-scrollbar{width:5px}.body::-webkit-scrollbar-thumb{background:#29303a;border-radius:4px}
      .live{display:flex;justify-content:space-between;gap:8px;align-items:center;border:1px solid rgba(52,211,153,.18);background:rgba(52,211,153,.045);border-radius:12px;padding:8px 10px;margin-bottom:8px}.live b{font-size:8px;letter-spacing:.12em;color:#6ee7b7}.live small{font-size:8px;color:#677384}.dot{display:inline-block;width:6px;height:6px;border-radius:50%;background:#34d399;box-shadow:0 0 12px #34d399;margin-right:6px}.status{border:1px solid rgba(255,255,255,.08);background:rgba(255,255,255,.025);border-radius:13px;padding:10px;font-size:9px;line-height:1.55;color:#8895a6}.status b{color:#fff}.label{margin:11px 2px 6px;font-size:7.5px;font-weight:950;letter-spacing:.15em;color:#657182}.radar,.deep-list{display:grid;gap:6px}.coin,.deep-card{border:1px solid rgba(255,255,255,.075);border-radius:12px;background:rgba(255,255,255,.022);padding:9px}.coin.top{border-color:rgba(52,211,153,.22);background:rgba(52,211,153,.035)}.ch{display:flex;justify-content:space-between;gap:8px}.name{font-size:10px;font-weight:900;color:#fff}.sub2{font-size:8px;color:#687586;margin-top:2px}.badge{border-radius:7px;padding:3px 5px;font-size:7px;font-weight:950}.deep{background:rgba(52,211,153,.1);color:#6ee7b7}.watch{background:rgba(96,165,250,.1);color:#93c5fd}.caution{background:rgba(251,191,36,.1);color:#fde68a}.danger{background:rgba(248,113,113,.1);color:#fca5a5}.ignore{background:rgba(255,255,255,.05);color:#8993a0}.metrics{display:grid;grid-template-columns:repeat(4,1fr);gap:4px;margin-top:7px}.metrics span{font-size:7px;color:#5f6c7d}.metrics b{display:block;margin-top:2px;font-size:8.5px;color:#e3e8ee}.reason{font-size:8px;line-height:1.4;color:#738092;margin-top:6px}.deep-card.setup{border-color:rgba(52,211,153,.25)}.deep-card.skip{border-color:rgba(248,113,113,.24)}.deep-card.wait{border-color:rgba(251,146,60,.24)}.decision{font-size:16px;font-weight:1000;color:#fff}.deep-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:5px;margin-top:7px}.deep-grid div{border:1px solid rgba(255,255,255,.06);border-radius:8px;padding:6px}.deep-grid span{display:block;font-size:6.8px;color:#647081}.deep-grid b{display:block;font-size:9px;color:#fff;margin-top:2px}.flags{margin-top:6px;font-size:8px;line-height:1.45;color:#7d899a}.foot{margin-top:9px;font-size:7.5px;line-height:1.45;color:#576373;text-align:center}.validate{display:flex;gap:6px;margin-top:8px}.validate div{flex:1;border:1px solid rgba(255,255,255,.07);border-radius:9px;padding:7px;text-align:center}.validate span{display:block;font-size:6.8px;color:#657182}.validate b{font-size:10px;color:#dce3ea}.collapsed .body{display:none}.collapsed{width:220px}.collapsed .sub,.collapsed .source{display:none}
    </style>
    <section class="neo"><div class="head"><div class="mark">N</div><div><div class="brand">NEO Meme Coins</div><div class="sub">Sentinel v0.5 · autonomous evidence engine</div></div><div class="source">${esc(sourceName())}</div><button class="collapse">−</button></div><div class="body"></div></section>`;

  const panel = shadow.querySelector('.neo');
  const body = shadow.querySelector('.body');
  const sourceEl = shadow.querySelector('.source');
  shadow.querySelector('.collapse').addEventListener('click', (e) => {
    collapsed = !collapsed; panel.classList.toggle('collapsed', collapsed); e.currentTarget.textContent = collapsed ? '+' : '−';
  });

  function badgeClass(status) {
    if (status === 'DEEP CHECK') return 'deep';
    if (status === 'WATCH') return 'watch';
    if (status === 'CAUTION') return 'caution';
    if (status === 'HIGH RISK') return 'danger';
    return 'ignore';
  }

  function radarHtml(limit = 7) {
    const rows = radarResults.filter((x) => !x.error).slice(0, limit);
    if (!rows.length) return '<div class="status">Още няма валидиран Solana candidate. Продължавам да сканирам видимите Fomo/Axiom/Photon елементи.</div>';
    return `<div class="radar">${rows.map((x,i) => `<div class="coin ${i===0?'top':''}"><div class="ch"><div><div class="name">$${esc(x.symbol||'?')} · ${esc(x.name||'Unknown')}</div><div class="sub2">${esc(short(x.tokenAddress))} · ${esc(x.narrative?.category||'unknown')}</div></div><span class="badge ${badgeClass(x.status)}">${esc(x.status)}</span></div><div class="metrics"><span>PRIORITY<b>${esc(x.priority)}/100</b></span><span>PRE-RISK<b>${esc(x.risk)}/100</b></span><span>5M<b>${num(x.price5m)>=0?'+':''}${num(x.price5m).toFixed(1)}%</b></span><span>LIQ<b>${esc(money(x.liquidityUsd))}</b></span></div><div class="reason">${esc(x.positives?.[0] || x.reasons?.[0] || 'market precheck')}</div></div>`).join('')}</div>`;
  }

  function deepHtml() {
    const rows = deepResults.slice(0, 5);
    if (!rows.length) return '<div class="status">Auto deep queue ще провери най-силните WATCH/DEEP candidates. Holder/funding scan се пуска последователно, за да пази public RPC.</div>';
    return `<div class="deep-list">${rows.map((r) => {
      const flags = (r.signals || []).filter((s) => num(s.points) > 0).slice(0,3).map((s) => s.label || s.title).join(' · ') || 'няма силен red flag в наличните данни';
      return `<div class="deep-card ${String(r.posture||'').toLowerCase()}"><div class="ch"><div><div class="decision">${esc(r.posture)}</div><div class="sub2">$${esc(r.market?.symbol||'?')} · ${esc(r.market?.name||'')}</div></div><span class="badge ${r.posture==='SETUP'?'deep':r.posture==='SKIP'?'danger':'watch'}">CONF ${esc(r.confidence||0)}%</span></div><div class="deep-grid"><div><span>RISK</span><b>${esc(r.risk)}/100</b></div><div><span>TOP 5</span><b>${r.holders?`${num(r.holders.top5Pct).toFixed(1)}%`:'N/A'}</b></div><div><span>OUTLOOK</span><b>${esc(r.outlook?.score??50)}/100</b></div></div><div class="flags">${esc(flags)}</div></div>`;
    }).join('')}</div>`;
  }

  function render(message = '') {
    const v = validationSummary();
    body.innerHTML = `<div class="live"><b><span class="dot"></span>SENTINEL ACTIVE</b><small>${observedCount} candidates · ${esc(sourceName())}${deepScanning?' · DEEP':''}</small></div><div class="status"><b>NEO гледа целия терминал.</b><br>Бърз radar → автоматичен deep holder/funding check → запис на 15m/60m outcome за реална валидация.${message?`<div style="margin-top:5px;color:#647184">${esc(message)}</div>`:''}<div class="foot">Context: ${esc(pageIntelligence().join(' · ') || 'market')}</div></div><div class="label">LIVE RADAR · TOP CANDIDATES</div>${radarHtml()}<div class="label">AUTO DEEP ANALYSIS</div>${deepHtml()}<div class="label">LOCAL VALIDATION</div><div class="validate"><div><span>15 MIN</span><b>${v.m15.total ? `${v.m15.correct}/${v.m15.total} · ${v.m15.pct}%` : 'collecting'}</b></div><div><span>60 MIN</span><b>${v.m60.total ? `${v.m60.correct}/${v.m60.total} · ${v.m60.pct}%` : 'collecting'}</b></div></div><div class="foot">PRE-RISK е само market triage. Финалният Risk идва след holder/funding evidence. Няма гаранция за бъдещо движение.</div>`;
  }

  function rankDeepResult(result) {
    const posture = { SETUP:4, WATCH:3, WAIT:2, SKIP:1 }[result.posture] || 0;
    return posture * 1000 + num(result.outlook?.score) * 5 - num(result.risk) * 2 + num(result.confidence);
  }

  function upsertDeep(result) {
    deepResults = [result, ...deepResults.filter((x) => x.tokenAddress !== result.tokenAddress)]
      .sort((a,b) => rankDeepResult(b) - rankDeepResult(a)).slice(0, 10);
    recordPrediction(result);
  }

  function queueDeep(token, candidate, force = false) {
    if (!token || queued.has(token)) return;
    const last = deepCooldown.get(token) || 0;
    if (!force && Date.now() - last < AUTO_DEEP_COOLDOWN_MS) return;
    queued.add(token);
    deepQueue.push({ token, candidate, force });
    processDeepQueue();
  }

  async function processDeepQueue() {
    if (deepScanning || !deepQueue.length) return;
    const job = deepQueue.shift();
    queued.delete(job.token);
    deepScanning = true;
    deepCooldown.set(job.token, Date.now());
    render(`Deep check: ${short(job.token)}…`);
    try {
      const focused = Boolean(job.candidate?.reasons?.includes('url'));
      const response = await chrome.runtime.sendMessage({ type:'NEO_ANALYZE', input:job.token, pageContext:collectPageContext(job.candidate, focused) });
      if (!response?.ok) throw new Error(response?.error || 'Deep analysis failed');
      upsertDeep(response.result);
    } catch (error) {
      // Keep radar alive; a failed public RPC request must not freeze Sentinel.
    } finally {
      deepScanning = false;
      render();
      setTimeout(processDeepQueue, 900);
    }
  }

  function selectAutoDeep(candidates) {
    const eligible = radarResults
      .filter((x) => !x.error && x.risk <= 58 && x.priority >= 58 && ['DEEP CHECK','WATCH'].includes(x.status))
      .sort((a,b) => b.priority - a.priority || a.risk - b.risk)
      .slice(0, MAX_AUTO_DEEP_PER_CYCLE);
    for (const item of eligible) {
      const candidate = candidates.find((c) => c.token === item.tokenAddress) || { token:item.tokenAddress, reasons:[], context:[], meta:{} };
      queueDeep(item.tokenAddress, candidate, false);
    }
  }

  async function scanRadar(force = false) {
    if (radarScanning) return;
    const candidates = collectCandidates();
    sourceEl.textContent = sourceName();
    if (!candidates.length) { radarResults = []; render('Няма разпознат token в този момент; наблюдението продължава.'); return; }
    radarScanning = true;
    try {
      const items = candidates.map((c) => ({ token:c.token, context:c.context.join(' ').slice(0,1400), reasons:c.reasons, sourceScore:c.score, sourceMeta:c.meta }));
      const response = await chrome.runtime.sendMessage({ type:'NEO_RADAR_SCAN', items });
      if (response?.ok) radarResults = Array.isArray(response.results) ? response.results : [];
      updateValidationFromRadar(radarResults);
      render(force ? 'Radar refreshed.' : '');
      const focused = candidates.find((c) => c.reasons.includes('url'));
      if (focused) queueDeep(focused.token, focused, force);
      selectAutoDeep(candidates);
    } catch (error) {
      render(`Radar временно не успя: ${error instanceof Error ? error.message : String(error)}`);
    } finally { radarScanning = false; }
  }

  function schedule(force = false, delay = 500) {
    clearTimeout(radarTimer);
    radarTimer = setTimeout(() => scanRadar(force), delay);
  }

  function onNav() {
    if (location.href !== lastSeenUrl) lastSeenUrl = location.href;
    schedule(false, 250);
  }
  const push = history.pushState.bind(history); history.pushState = (...a) => { const r = push(...a); setTimeout(onNav,0); return r; };
  const replace = history.replaceState.bind(history); history.replaceState = (...a) => { const r = replace(...a); setTimeout(onNav,0); return r; };
  window.addEventListener('popstate', onNav); window.addEventListener('hashchange', onNav);
  document.addEventListener('click', () => schedule(false, 650), true);
  const observer = new MutationObserver(() => schedule(false, 1200));
  observer.observe(document.documentElement, { subtree:true, childList:true, attributes:true, attributeFilter:['href','data-token-address','data-mint','data-address','data-ca','data-contract','data-token','data-coin-address','data-resolution-score'] });

  chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
    if (message?.type === 'NEO_RESCAN') { schedule(true,0); sendResponse({ok:true,source:sourceName(),observedCount,deepScanning,radarScanning}); return false; }
    if (message?.type === 'NEO_STATUS') {
      const v = validationSummary();
      sendResponse({ ok:true, source:sourceName(), observedCount, deepScanning, radarScanning, queue:deepQueue.length, radarTop:radarResults.slice(0,3), result:deepResults[0] ? { posture:deepResults[0].posture,risk:deepResults[0].risk,confidence:deepResults[0].confidence,symbol:deepResults[0].market?.symbol,name:deepResults[0].market?.name } : null, validation:v });
      return false;
    }
    return false;
  });

  loadValidation().finally(() => { render('Starting full-terminal watch…'); schedule(false,450); });
  setInterval(() => scanRadar(false), RADAR_INTERVAL_MS);
})();
