(() => {
  const BASE58_RE = /\b[1-9A-HJ-NP-Za-km-z]{32,44}\b/g;
  const SOCIAL_HOSTS = [
    'x.com', 'twitter.com', 't.me', 'telegram.me', 'discord.gg', 'discord.com',
    'youtube.com', 'youtu.be', 'instagram.com', 'tiktok.com',
  ];

  const RADAR_INTERVAL_MS = 12_000;
  const AUTO_DEEP_COOLDOWN_MS = 120_000;
  const MAX_RADAR_CANDIDATES = 12;

  let lastSeenUrl = location.href;
  let radarTimer = null;
  let deepTimer = null;
  let radarScanning = false;
  let deepScanning = false;
  let collapsed = false;
  let lastResult = null;
  let lastDeepToken = '';
  let radarResults = [];
  let observedCount = 0;
  const deepCooldown = new Map();

  function short(value, size = 5) {
    const text = String(value || '');
    return text.length > size * 2 + 2 ? `${text.slice(0, size)}…${text.slice(-size)}` : text;
  }

  function esc(value) {
    return String(value ?? '')
      .replaceAll('&', '&amp;')
      .replaceAll('<', '&lt;')
      .replaceAll('>', '&gt;')
      .replaceAll('"', '&quot;')
      .replaceAll("'", '&#039;');
  }

  function money(value) {
    const n = Number(value) || 0;
    if (n >= 1_000_000_000) return `$${(n / 1_000_000_000).toFixed(2)}B`;
    if (n >= 1_000_000) return `$${(n / 1_000_000).toFixed(2)}M`;
    if (n >= 1_000) return `$${(n / 1_000).toFixed(1)}K`;
    return `$${n.toFixed(0)}`;
  }

  function sourceName() {
    const host = location.hostname.toLowerCase();
    if (host === 'fomo.family' || host.endsWith('.fomo.family')) return 'FOMO';
    if (host.includes('axiom.trade')) return 'AXIOM';
    if (host.includes('tinyastro.io')) return 'PHOTON';
    return 'TERMINAL';
  }

  function addCandidate(map, raw, points, reason, context = '') {
    const values = String(raw || '').match(BASE58_RE) || [];
    for (const token of values) {
      const current = map.get(token) || {
        token,
        score: 0,
        reasons: [],
        context: [],
      };
      current.score += points;
      if (reason && !current.reasons.includes(reason)) current.reasons.push(reason);
      if (context && current.context.length < 6) current.context.push(String(context).slice(0, 240));
      map.set(token, current);
    }
  }

  function collectCandidates() {
    const scored = new Map();
    addCandidate(scored, location.href, 200, 'url', location.pathname);

    const selector = [
      'a[href]', '[data-token-address]', '[data-mint]', '[data-address]',
      '[data-ca]', '[data-contract]', '[data-token]', '[data-coin-address]',
    ].join(',');

    for (const node of [...document.querySelectorAll(selector)].slice(0, 4500)) {
      const href = node.getAttribute?.('href') || '';
      const attrs = [
        node.getAttribute?.('data-token-address'),
        node.getAttribute?.('data-mint'),
        node.getAttribute?.('data-address'),
        node.getAttribute?.('data-ca'),
        node.getAttribute?.('data-contract'),
        node.getAttribute?.('data-token'),
        node.getAttribute?.('data-coin-address'),
      ].filter(Boolean).join(' ');

      const container = node.closest?.('tr, [role="row"], article, li, [class*="card"], [class*="token"], [class*="coin"]');
      const nearby = String(container?.textContent || node.textContent || '').trim().replace(/\s+/g, ' ').slice(0, 320);
      const combined = `${href} ${attrs} ${nearby}`;
      const lower = combined.toLowerCase();

      let points = 22;
      let reason = 'page-link';
      if (lower.includes('solscan.io/token/') || lower.includes('pump.fun/coin/') || lower.includes('dexscreener.com/solana/')) {
        points = 155;
        reason = 'verified-token-link';
      } else if (attrs) {
        points = 135;
        reason = 'token-attribute';
      } else if (/\/(token|coin|trade|swap|pair)\//i.test(href)) {
        points = 105;
        reason = 'token-route';
      } else if (/contract|\bca\b|mint/i.test(nearby)) {
        points = 92;
        reason = 'token-label';
      }

      addCandidate(scored, combined, points, reason, nearby || href);
    }

    const visibleText = String(document.body?.innerText || '').slice(0, 220_000);
    const lines = visibleText.split('\n').filter(Boolean);
    for (const line of lines.slice(0, 3000)) {
      if (/\b(CA|Contract|Mint|Token address|Address)\b/i.test(line)) {
        addCandidate(scored, line, 96, 'visible-label', line);
      }
    }

    const candidates = [...scored.values()]
      .filter((item) => item.score >= 60)
      .sort((a, b) => b.score - a.score);

    observedCount = candidates.length;
    return candidates;
  }

  function collectPageContext(candidate = null, focused = false) {
    const socialLinks = [];
    const projectLinks = [];

    if (focused) {
      for (const anchor of [...document.querySelectorAll('a[href]')].slice(0, 3500)) {
        const href = anchor.href || '';
        try {
          const url = new URL(href);
          const host = url.hostname.toLowerCase();
          if (SOCIAL_HOSTS.some((allowed) => host === allowed || host.endsWith(`.${allowed}`))) {
            socialLinks.push(href);
          } else if (href.startsWith('http') && !host.includes(location.hostname)) {
            projectLinks.push(href);
          }
        } catch {
          // Ignore malformed links.
        }
        if (socialLinks.length >= 24 && projectLinks.length >= 14) break;
      }
    }

    return {
      source: sourceName(),
      url: location.href,
      title: document.title,
      text: focused
        ? String(document.body?.innerText || '').slice(0, 35_000)
        : String(candidate?.context?.join(' ') || '').slice(0, 4_000),
      socialLinks: [...new Set(socialLinks)].slice(0, 20),
      projectLinks: [...new Set(projectLinks)].slice(0, 12),
      observationMode: focused ? 'focused-coin' : 'sentinel-auto-deep',
    };
  }

  function pageIntelligence() {
    const text = String(document.body?.innerText || '').slice(0, 60_000);
    const lower = text.toLowerCase();
    return {
      hasTraderProfile: /trades|pnl|profit|hold time|leaderboard|top traders/i.test(text),
      hasFeed: /feed|alerts|trending|swaps|buys|sells|tokens/i.test(text),
      hasSocialNarrative: /twitter|\bx\b|telegram|discord|viral|news|community/i.test(text),
      hasPositions: /positions|open|closed|deposit|withdraw/i.test(text),
      keywords: [
        lower.includes('trending') ? 'trending' : '',
        lower.includes('leaderboard') ? 'traders' : '',
        lower.includes('swaps') ? 'swaps' : '',
        lower.includes('alerts') ? 'alerts' : '',
      ].filter(Boolean),
    };
  }

  const host = document.createElement('div');
  host.id = 'neo-meme-coins-root';
  host.style.all = 'initial';
  host.style.position = 'fixed';
  host.style.zIndex = '2147483647';
  host.style.right = '14px';
  host.style.top = '70px';
  document.documentElement.appendChild(host);
  const shadow = host.attachShadow({ mode: 'open' });

  shadow.innerHTML = `
    <style>
      *{box-sizing:border-box}button,a{font:inherit}button{cursor:pointer}
      .neo{width:382px;max-height:calc(100vh - 88px);overflow:hidden;border:1px solid rgba(255,255,255,.12);border-radius:18px;background:rgba(7,9,12,.975);color:#e8edf2;font-family:Inter,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;box-shadow:0 20px 65px rgba(0,0,0,.5);backdrop-filter:blur(18px)}
      .head{display:flex;align-items:center;gap:10px;padding:12px 13px;border-bottom:1px solid rgba(255,255,255,.08)}
      .mark{width:32px;height:32px;display:grid;place-items:center;border-radius:10px;background:#34d399;color:#06100c;font-weight:1000}.brand{font-size:12px;font-weight:950;color:#fff}.sub{font-size:9px;color:#657081;margin-top:2px}.source{margin-left:auto;font-size:8px;font-weight:900;letter-spacing:.1em;color:#6ee7b7;border:1px solid rgba(52,211,153,.22);padding:4px 6px;border-radius:7px}.collapse{border:0;background:transparent;color:#7d8796;font-size:16px;padding:2px 4px}
      .body{max-height:calc(100vh - 143px);overflow:auto;padding:12px}.body::-webkit-scrollbar{width:5px}.body::-webkit-scrollbar-thumb{background:#29303a;border-radius:4px}.hidden{display:none}
      .live{display:flex;align-items:center;justify-content:space-between;gap:8px;border:1px solid rgba(52,211,153,.16);background:rgba(52,211,153,.045);border-radius:13px;padding:9px 10px;margin-bottom:8px}.live-left{display:flex;align-items:center;gap:7px;font-size:8px;font-weight:950;letter-spacing:.12em;color:#6ee7b7}.dot{width:6px;height:6px;border-radius:50%;background:#34d399;box-shadow:0 0 12px rgba(52,211,153,.8)}.live small{font-size:8px;color:#6a7586}
      .status{border:1px solid rgba(255,255,255,.08);background:rgba(255,255,255,.025);border-radius:14px;padding:11px;font-size:9.5px;line-height:1.55;color:#8f9bab}.status b{color:#fff}.spin{display:inline-block;width:10px;height:10px;margin-right:7px;border:2px solid rgba(52,211,153,.25);border-top-color:#34d399;border-radius:999px;animation:spin .8s linear infinite}@keyframes spin{to{transform:rotate(360deg)}}
      .scope{display:grid;grid-template-columns:repeat(3,1fr);gap:5px;margin-top:8px}.scope span{border:1px solid rgba(255,255,255,.06);background:rgba(255,255,255,.02);border-radius:8px;padding:6px 4px;text-align:center;font-size:7.5px;font-weight:800;color:#6f7b8b}
      .label{margin:12px 2px 7px;font-size:8px;font-weight:950;letter-spacing:.15em;color:#667183}.radar{display:grid;gap:6px}.coin{border:1px solid rgba(255,255,255,.07);border-radius:12px;background:rgba(255,255,255,.02);padding:9px}.coin.top{border-color:rgba(52,211,153,.2);background:rgba(52,211,153,.035)}.coin-head{display:flex;align-items:flex-start;justify-content:space-between;gap:8px}.coin-name{font-size:10px;font-weight:900;color:#fff}.coin-sub{margin-top:2px;font-size:8px;color:#6f7b8b}.badge{border-radius:7px;padding:3px 5px;font-size:7px;font-weight:950;letter-spacing:.07em}.deep{background:rgba(52,211,153,.1);color:#6ee7b7}.watch{background:rgba(96,165,250,.1);color:#93c5fd}.caution{background:rgba(251,191,36,.1);color:#fde68a}.danger{background:rgba(248,113,113,.1);color:#fca5a5}.ignore{background:rgba(255,255,255,.05);color:#7d8796}.coin-metrics{display:grid;grid-template-columns:repeat(4,1fr);gap:4px;margin-top:7px}.coin-metrics div{font-size:7px;color:#626e7e}.coin-metrics b{display:block;margin-top:2px;font-size:8.5px;color:#dbe2e9}.coin-reason{margin-top:6px;font-size:8px;line-height:1.45;color:#748091}.coin button{width:100%;height:28px;margin-top:7px;border:1px solid rgba(255,255,255,.08);border-radius:8px;background:rgba(255,255,255,.035);color:#fff;font-size:8px;font-weight:900}
      .decision{border:1px solid rgba(255,255,255,.09);border-radius:15px;padding:12px}.decision.skip{border-color:rgba(248,113,113,.28);background:rgba(248,113,113,.06)}.decision.wait{border-color:rgba(251,146,60,.28);background:rgba(251,146,60,.06)}.decision.watch{border-color:rgba(251,191,36,.24);background:rgba(251,191,36,.05)}.decision.setup{border-color:rgba(52,211,153,.25);background:rgba(52,211,153,.06)}.eyebrow{font-size:8px;font-weight:950;letter-spacing:.15em;color:#697486}.posture{font-size:27px;line-height:1;font-weight:1000;letter-spacing:-.04em;color:white;margin-top:5px}.token{font-size:10px;color:#909aaa;margin-top:5px}
      .row{display:flex;gap:7px;margin-top:8px}.metric{flex:1;border:1px solid rgba(255,255,255,.08);border-radius:12px;background:rgba(255,255,255,.02);padding:9px}.metric span{display:block;font-size:7px;font-weight:900;letter-spacing:.12em;color:#657081}.metric strong{display:block;margin-top:5px;font-size:13px;color:white}.forecast{margin-top:8px;border:1px solid rgba(96,165,250,.18);background:rgba(96,165,250,.05);border-radius:14px;padding:11px}.forecast-top{display:flex;justify-content:space-between;gap:8px}.forecast strong{font-size:12px;color:#bfdbfe}.forecast-score{font-size:10px;font-weight:900;color:#93c5fd}.forecast p{font-size:9px;line-height:1.45;color:#7f8da0;margin:6px 0 0}
      .signals{display:grid;gap:6px}.signal{border:1px solid rgba(255,255,255,.07);border-radius:11px;background:rgba(255,255,255,.02);padding:9px}.signal-top{display:flex;gap:7px;justify-content:space-between}.signal b{font-size:10px;color:white}.pts{font-size:9px;font-weight:950}.signal p{margin:4px 0 0;font-size:8.5px;line-height:1.45;color:#7b8797}.signal.critical{border-color:rgba(248,113,113,.18)}.signal.critical .pts{color:#fca5a5}.signal.warning{border-color:rgba(251,191,36,.15)}.signal.warning .pts{color:#fde68a}.signal.positive{border-color:rgba(52,211,153,.15)}.signal.positive .pts{color:#6ee7b7}
      .socials{display:flex;flex-wrap:wrap;gap:6px}.social{display:inline-flex;align-items:center;min-height:28px;border:1px solid rgba(255,255,255,.09);border-radius:9px;padding:0 8px;background:rgba(255,255,255,.03);color:#dce3ea;text-decoration:none;font-size:8.5px;font-weight:800}.factors{display:grid;grid-template-columns:1fr 1fr;gap:7px}.factor{border:1px solid rgba(255,255,255,.07);border-radius:11px;padding:8px;font-size:8.5px;line-height:1.5;color:#8390a1}.factor b{display:block;font-size:8px;letter-spacing:.1em;margin-bottom:3px}.bull b{color:#6ee7b7}.bear b{color:#fca5a5}.warn{margin-top:8px;border:1px solid rgba(251,191,36,.16);background:rgba(251,191,36,.05);border-radius:11px;padding:8px;font-size:8.5px;line-height:1.45;color:#d8bd70}.foot{margin-top:9px;font-size:8px;line-height:1.45;color:#596474;text-align:center}.collapsed .body{display:none}.collapsed{width:210px}.collapsed .sub,.collapsed .source{display:none}
    </style>
    <section class="neo">
      <div class="head">
        <div class="mark">N</div>
        <div><div class="brand">NEO Meme Coins</div><div class="sub">Sentinel · autonomous market observer</div></div>
        <div class="source">${esc(sourceName())}</div>
        <button class="collapse" title="Свий">−</button>
      </div>
      <div class="body"></div>
    </section>
  `;

  const panel = shadow.querySelector('.neo');
  const body = shadow.querySelector('.body');
  const sourceEl = shadow.querySelector('.source');
  const collapseBtn = shadow.querySelector('.collapse');

  collapseBtn.addEventListener('click', () => {
    collapsed = !collapsed;
    panel.classList.toggle('collapsed', collapsed);
    collapseBtn.textContent = collapsed ? '+' : '−';
  });

  function liveHeader() {
    return `<div class="live"><div class="live-left"><span class="dot"></span>SENTINEL ACTIVE</div><small>${esc(observedCount)} candidates · ${esc(sourceName())}</small></div>`;
  }

  function scopeGrid() {
    const intel = pageIntelligence();
    return `<div class="scope">
      <span>MARKET FLOW</span><span>LIQUIDITY</span><span>HOLDERS</span>
      <span>FUNDING</span><span>SOCIALS</span><span>NARRATIVE</span>
      <span>FRESH WALLETS</span><span>CHART RISK</span><span>TRADER CONTEXT</span>
    </div>${intel.keywords.length ? `<div class="foot">Page context: ${esc(intel.keywords.join(' · '))}</div>` : ''}`;
  }

  function badgeClass(status) {
    if (status === 'DEEP CHECK') return 'deep';
    if (status === 'WATCH') return 'watch';
    if (status === 'CAUTION') return 'caution';
    if (status === 'HIGH RISK') return 'danger';
    return 'ignore';
  }

  function radarRows(limit = 6) {
    const rows = radarResults.filter((item) => !item.error).slice(0, limit);
    if (!rows.length) return '<div class="status">NEO сканира видимите token routes, swaps, cards и feeds. Все още няма валидиран Solana pair за оценка.</div>';

    return `<div class="radar">${rows.map((item, index) => `
      <div class="coin ${index === 0 ? 'top' : ''}">
        <div class="coin-head">
          <div><div class="coin-name">$${esc(item.symbol || '?')} · ${esc(item.name || 'Unknown')}</div><div class="coin-sub">${esc(short(item.tokenAddress))} · ${esc(item.narrative?.category || 'unknown')}</div></div>
          <span class="badge ${badgeClass(item.status)}">${esc(item.status)}</span>
        </div>
        <div class="coin-metrics">
          <div>PRIORITY<b>${esc(item.priority)}/100</b></div>
          <div>RISK<b>${esc(item.risk)}/100</b></div>
          <div>5M<b>${item.price5m >= 0 ? '+' : ''}${esc(Number(item.price5m || 0).toFixed(1))}%</b></div>
          <div>LIQ<b>${esc(money(item.liquidityUsd))}</b></div>
        </div>
        <div class="coin-reason">${esc((item.positives?.[0] || item.reasons?.[0] || 'наблюдавам market flow и risk profile'))}</div>
        <button data-deep-token="${esc(item.tokenAddress)}">ДЪЛБОК АНАЛИЗ</button>
      </div>`).join('')}</div>`;
  }

  function bindRadarButtons() {
    for (const btn of shadow.querySelectorAll('[data-deep-token]')) {
      btn.addEventListener('click', () => {
        const token = btn.getAttribute('data-deep-token');
        const candidate = collectCandidates().find((item) => item.token === token) || { token, context: [] };
        runDeep(token, candidate, true, true);
      });
    }
  }

  function renderRadarHome(message = '') {
    body.innerHTML = `
      ${liveHeader()}
      <div class="status"><b>NEO не чака да отвориш coin.</b><br>Наблюдава целия видим терминал, намира token-и, прави бърз market triage и сам избира кои заслужават deep check.${message ? `<div style="margin-top:6px;color:#657184">${esc(message)}</div>` : ''}${scopeGrid()}</div>
      <div class="label">LIVE RADAR · TOP VISIBLE CANDIDATES</div>
      ${radarRows(7)}
      <div class="foot">Deep holder/funding проверки се пускат селективно, за да не се убива public Solana RPC с безсмислени заявки.</div>
    `;
    bindRadarButtons();
  }

  function renderDeepLoading(token, auto = false) {
    body.innerHTML = `
      ${liveHeader()}
      <div class="status"><span class="spin"></span><b>${auto ? 'NEO избра candidate автоматично' : 'Deep check'}</b><br>Проверявам ${esc(short(token))}: holders, fresh wallets, funding clusters, liquidity, flow, socials, narrative и momentum.</div>
      <div class="label">RADAR CONTINUES IN BACKGROUND</div>${radarRows(4)}
    `;
  }

  function renderDeep(result) {
    lastResult = result;
    const socials = result.social?.links || [];
    const bull = result.outlook?.bull || [];
    const bear = result.outlook?.bear || [];
    const fresh = result.holders?.sampledFreshWalletPct;
    const linked = result.holders?.sharedFunderClusters?.[0];
    const signals = Array.isArray(result.signals) ? result.signals.slice(0, 8) : [];

    body.innerHTML = `
      ${liveHeader()}
      <div class="decision ${esc(String(result.posture || '').toLowerCase())}">
        <div class="eyebrow">DEEP ANALYSIS · CONFIDENCE ${esc(result.confidence ?? 0)}%</div>
        <div class="posture">${esc(result.posture)}</div>
        <div class="token">${esc(result.market?.name)} · $${esc(result.market?.symbol)} · ${esc(short(result.tokenAddress))}</div>
      </div>
      <div class="row">
        <div class="metric"><span>RISK</span><strong>${esc(result.risk)}/100</strong></div>
        <div class="metric"><span>LIQ/CAP</span><strong>${esc(Number(result.liquidityRatio || 0).toFixed((result.liquidityRatio || 0) >= 10 ? 1 : 2))}%</strong></div>
        <div class="metric"><span>TOP 5</span><strong>${result.holders ? `${esc(result.holders.top5Pct.toFixed(1))}%` : 'N/A'}</strong></div>
      </div>
      <div class="row">
        <div class="metric"><span>FRESH</span><strong>${fresh == null ? 'N/A' : `${esc(fresh.toFixed(0))}%`}</strong></div>
        <div class="metric"><span>LINKED</span><strong>${linked ? esc(linked.walletCount) : '0'}</strong></div>
        <div class="metric"><span>NARRATIVE</span><strong style="font-size:9px">${esc(result.narrative?.category || 'unknown')}</strong></div>
      </div>
      <div class="forecast"><div class="forecast-top"><strong>${esc(result.outlook?.label || 'MIXED')}</strong><span class="forecast-score">${esc(result.outlook?.score ?? 50)}/100</span></div><p>${esc(result.outlook?.horizon || '15–60m evidence window')} · confidence ${esc(result.outlook?.confidence ?? 0)}%. ${esc(result.outlook?.note || '')}</p></div>
      <div class="label">BULL / BEAR EVIDENCE</div>
      <div class="factors"><div class="factor bull"><b>BULL</b>${bull.length ? bull.map((x) => `• ${esc(x)}`).join('<br>') : '• няма силен bull сигнал'}</div><div class="factor bear"><b>BEAR</b>${bear.length ? bear.map((x) => `• ${esc(x)}`).join('<br>') : '• няма силен bear сигнал'}</div></div>
      <div class="label">SOCIAL / PROJECT EVIDENCE</div>
      <div class="socials">${socials.length ? socials.slice(0, 10).map((item) => `<a class="social" href="${esc(item.url)}" target="_blank" rel="noreferrer">${esc(item.platform || 'LINK')}</a>`).join('') : '<span style="font-size:9px;color:#657081">Няма намерени публични project links.</span>'}</div>
      <div class="label">КЛЮЧОВИ СИГНАЛИ</div>
      <div class="signals">${signals.map((signal) => {
        const severity = signal.severity === 'warning' ? 'warning' : signal.severity || '';
        const pts = Number(signal.points ?? signal.riskPoints ?? 0);
        return `<div class="signal ${esc(severity)}"><div class="signal-top"><b>${esc(signal.label || signal.title)}</b><span class="pts">${pts > 0 ? '+' : ''}${esc(pts)}</span></div><p>${esc(signal.detail || '')}</p></div>`;
      }).join('')}</div>
      ${result.holderError ? `<div class="warn">Holder/RPC evidence е непълно: ${esc(result.holderError)}</div>` : ''}
      <div class="label">SENTINEL CONTINUES WATCHING</div>${radarRows(4)}
      <div class="foot">NEO наблюдава останалите visible coins паралелно. Outlook е evidence-based сигнал, не гаранция за цена.</div>
    `;
    bindRadarButtons();
  }

  function renderDeepError(error) {
    body.innerHTML = `${liveHeader()}<div class="status" style="border-color:rgba(248,113,113,.2);color:#fca5a5">Deep analysis failed: ${esc(error)}</div><div class="label">RADAR STILL ACTIVE</div>${radarRows(6)}`;
    bindRadarButtons();
  }

  function findFocusedCandidate(candidates) {
    const urlCandidate = candidates.find((item) => item.reasons.includes('url'));
    if (urlCandidate) return urlCandidate;
    return null;
  }

  async function runDeep(token, candidate, force = false, userInitiated = false) {
    if (!token || deepScanning) return;
    const lastAt = deepCooldown.get(token) || 0;
    if (!force && Date.now() - lastAt < AUTO_DEEP_COOLDOWN_MS) return;

    deepScanning = true;
    deepCooldown.set(token, Date.now());
    lastDeepToken = token;
    renderDeepLoading(token, !userInitiated);

    try {
      const focused = Boolean(candidate?.reasons?.includes('url'));
      const response = await chrome.runtime.sendMessage({
        type: 'NEO_ANALYZE',
        input: token,
        pageContext: collectPageContext(candidate, focused),
      });
      if (!response?.ok) throw new Error(response?.error || 'Неуспешен deep analysis.');
      renderDeep(response.result);
    } catch (error) {
      renderDeepError(error instanceof Error ? error.message : String(error));
    } finally {
      deepScanning = false;
    }
  }

  async function scanRadar(force = false) {
    if (radarScanning) return;
    const candidates = collectCandidates();
    sourceEl.textContent = sourceName();

    if (!candidates.length) {
      radarResults = [];
      if (!deepScanning && !lastResult) renderRadarHome('В момента DOM-ът не показва валидиран Solana contract address. Продължавам да наблюдавам.');
      return;
    }

    radarScanning = true;
    try {
      const items = candidates.slice(0, MAX_RADAR_CANDIDATES).map((item) => ({
        token: item.token,
        context: item.context.join(' ').slice(0, 1200),
        reasons: item.reasons,
        sourceScore: item.score,
      }));
      const response = await chrome.runtime.sendMessage({ type: 'NEO_RADAR_SCAN', items });
      if (response?.ok) radarResults = Array.isArray(response.results) ? response.results : [];

      if (lastResult && !deepScanning) renderDeep(lastResult);
      else if (!deepScanning) renderRadarHome(force ? 'Radar refresh complete.' : '');

      const focused = findFocusedCandidate(candidates);
      if (focused) {
        if (focused.token !== lastDeepToken || force) runDeep(focused.token, focused, force, false);
        return;
      }

      const best = radarResults.find((item) => item.status === 'DEEP CHECK' && item.priority >= 70 && item.risk <= 48);
      if (best) {
        const candidate = candidates.find((item) => item.token === best.tokenAddress) || { token: best.tokenAddress, context: [], reasons: [] };
        runDeep(best.tokenAddress, candidate, false, false);
      }
    } catch (error) {
      if (!deepScanning && !lastResult) renderRadarHome(`Radar error: ${error instanceof Error ? error.message : String(error)}`);
    } finally {
      radarScanning = false;
    }
  }

  function scheduleRadar(force = false, delay = 900) {
    clearTimeout(radarTimer);
    radarTimer = setTimeout(() => scanRadar(force), delay);
  }

  function onNavigation() {
    if (location.href !== lastSeenUrl) {
      lastSeenUrl = location.href;
      lastResult = null;
      lastDeepToken = '';
    }
    scheduleRadar(false, 250);
  }

  const originalPushState = history.pushState.bind(history);
  history.pushState = (...args) => {
    const out = originalPushState(...args);
    setTimeout(onNavigation, 0);
    return out;
  };

  const originalReplaceState = history.replaceState.bind(history);
  history.replaceState = (...args) => {
    const out = originalReplaceState(...args);
    setTimeout(onNavigation, 0);
    return out;
  };

  window.addEventListener('popstate', onNavigation);
  window.addEventListener('hashchange', onNavigation);
  document.addEventListener('click', () => scheduleRadar(false, 700), true);

  const observer = new MutationObserver(() => {
    if (location.href !== lastSeenUrl) onNavigation();
    else scheduleRadar(false, 1400);
  });
  observer.observe(document.documentElement, {
    subtree: true,
    childList: true,
    attributes: true,
    attributeFilter: ['href', 'data-token-address', 'data-mint', 'data-address', 'data-ca', 'data-contract', 'data-token'],
  });

  chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
    if (message?.type === 'NEO_RESCAN') {
      scheduleRadar(true, 0);
      sendResponse({ ok: true, source: sourceName(), observedCount, deepScanning, radarScanning });
      return false;
    }
    if (message?.type === 'NEO_STATUS') {
      sendResponse({
        ok: true,
        source: sourceName(),
        observedCount,
        deepScanning,
        radarScanning,
        token: lastDeepToken,
        radarTop: radarResults.slice(0, 3).map((item) => ({ symbol: item.symbol, status: item.status, priority: item.priority, risk: item.risk })),
        result: lastResult ? {
          posture: lastResult.posture,
          risk: lastResult.risk,
          confidence: lastResult.confidence,
          symbol: lastResult.market?.symbol,
          name: lastResult.market?.name,
        } : null,
      });
      return false;
    }
    return false;
  });

  renderRadarHome('Starting autonomous watch…');
  scheduleRadar(false, 500);
  setInterval(() => scanRadar(false), RADAR_INTERVAL_MS);
})();
