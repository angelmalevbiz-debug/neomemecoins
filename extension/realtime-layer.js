(() => {
  const MARKET_MS = 3000;
  const HOLDER_MS = 15000;
  const SOCIAL_MS = 45000;
  const MAX_MARKET = 8;
  const previous = new Map();
  const holderState = new Map();
  const socialState = new Map();
  let rows = [];
  let marketBusy = false;
  let holderBusy = false;
  let socialBusy = false;
  let lastMarketAt = 0;
  let lastHolderAt = 0;
  let lastSocialAt = 0;
  let socialCursor = 0;

  const num = (v) => Number.isFinite(Number(v)) ? Number(v) : 0;
  const esc = (v) => String(v ?? '').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;').replaceAll("'",'&#039;');
  const money = (v) => {
    const n = num(v);
    if (n >= 1e9) return `$${(n / 1e9).toFixed(2)}B`;
    if (n >= 1e6) return `$${(n / 1e6).toFixed(2)}M`;
    if (n >= 1e3) return `$${(n / 1e3).toFixed(1)}K`;
    return `$${n.toFixed(n < 1 ? 4 : 0)}`;
  };

  function collectTokens() {
    const found = [];
    const seen = new Set();
    const add = (raw) => {
      const token = String(raw || '').trim();
      if (!/^[1-9A-HJ-NP-Za-km-z]{32,44}$/.test(token) || seen.has(token)) return;
      seen.add(token);
      found.push(token);
    };

    for (const node of [...document.querySelectorAll('#neo-fomo-resolved-candidates [data-token-address]')].slice(0, 20)) {
      add(node.getAttribute('data-token-address'));
    }
    for (const node of [...document.querySelectorAll('[data-token-address],[data-mint],[data-ca],[data-contract],[data-coin-address]')].slice(0, 2500)) {
      add(node.getAttribute('data-token-address'));
      add(node.getAttribute('data-mint'));
      add(node.getAttribute('data-ca'));
      add(node.getAttribute('data-contract'));
      add(node.getAttribute('data-coin-address'));
      if (found.length >= MAX_MARKET) break;
    }
    return found.slice(0, MAX_MARKET);
  }

  function compactPrevious(tokens) {
    const out = {};
    for (const token of tokens) {
      const item = previous.get(token);
      if (item) out[token] = { priceUsd: item.priceUsd, liquidityUsd: item.liquidityUsd, volume5mUsd: item.volume5mUsd };
    }
    return out;
  }

  function trendClass(v) {
    if (v > 0.2) return '#6ee7b7';
    if (v < -0.2) return '#fca5a5';
    return '#dbe4ee';
  }

  function render() {
    const root = document.getElementById('neo-meme-coins-root');
    const shadow = root?.shadowRoot;
    const body = shadow?.querySelector('.body');
    if (!body) return;

    shadow.getElementById('neo-realtime-layer')?.remove();
    const block = document.createElement('div');
    block.id = 'neo-realtime-layer';
    block.style.cssText = 'margin:0 0 10px 0;border:1px solid rgba(96,165,250,.18);background:rgba(96,165,250,.045);border-radius:13px;padding:9px;color:#dbe4ee;font-family:Inter,system-ui,sans-serif';

    const top = rows.filter((x) => !x.error).slice(0, 3);
    const now = Date.now();
    const age = lastMarketAt ? Math.max(0, Math.round((now - lastMarketAt) / 1000)) : null;
    const cards = top.map((item) => {
      const holder = holderState.get(item.tokenAddress);
      const social = socialState.get(item.tokenAddress);
      const delta = num(item.priceDelta);
      return `<div style="margin-top:7px;padding:7px;border:1px solid rgba(255,255,255,.06);border-radius:9px;background:rgba(0,0,0,.12)">
        <div style="display:flex;justify-content:space-between;gap:8px"><b style="font-size:9px;color:#fff">$${esc(item.symbol || '?')}</b><span style="font-size:8px;color:#93c5fd">LIVE ${esc(item.realtimePriority || 0)}/100</span></div>
        <div style="display:grid;grid-template-columns:repeat(4,1fr);gap:4px;margin-top:5px">
          <span style="font-size:7px;color:#667384">3S Δ<b style="display:block;color:${trendClass(delta)};font-size:8.5px">${delta >= 0 ? '+' : ''}${esc(delta.toFixed(2))}%</b></span>
          <span style="font-size:7px;color:#667384">5M FLOW<b style="display:block;color:#fff;font-size:8.5px">${esc(Math.round(num(item.buyShare)))}% buy</b></span>
          <span style="font-size:7px;color:#667384">TOP 5<b style="display:block;color:#fff;font-size:8.5px">${holder && !holder.error ? `${esc(num(holder.top5Pct).toFixed(1))}%` : '…'}</b></span>
          <span style="font-size:7px;color:#667384">SOCIAL<b style="display:block;color:#fff;font-size:8.5px">${social ? `${esc(social.score || 0)}/100` : '…'}</b></span>
        </div>
        <div style="margin-top:5px;font-size:7.5px;color:#718094">liq ${esc(money(item.liquidityUsd))} · vol5m ${esc(money(item.volume5mUsd))}${social?.latest ? ` · social: ${esc(social.latest)}` : ''}</div>
      </div>`;
    }).join('');

    block.innerHTML = `
      <div style="display:flex;align-items:center;justify-content:space-between;gap:8px">
        <div style="font-size:8px;font-weight:950;letter-spacing:.12em;color:#93c5fd">⚡ REALTIME ENGINE · 3S</div>
        <div style="font-size:7.5px;color:#647184">${age == null ? 'starting' : `${age}s ago`}</div>
      </div>
      <div style="font-size:8px;line-height:1.45;color:#7d8a9b;margin-top:4px">Market flow 3s · holders 15s · socials/web 45s. Работи непрекъснато, докато terminal tab-ът е отворен.</div>
      ${cards || '<div style="margin-top:7px;font-size:8px;color:#667384">Чакам resolved token-и…</div>'}
    `;

    const live = body.querySelector('.live');
    if (live?.nextSibling) body.insertBefore(block, live.nextSibling);
    else body.prepend(block);
  }

  async function pulseMarket() {
    if (marketBusy) return;
    const tokens = collectTokens();
    if (!tokens.length) { render(); return; }
    marketBusy = true;
    try {
      const response = await chrome.runtime.sendMessage({ type: 'NEO_REALTIME_PULSE', tokens, previous: compactPrevious(tokens) });
      if (response?.ok && Array.isArray(response.rows)) {
        rows = response.rows;
        for (const item of rows) if (!item.error) previous.set(item.tokenAddress, item);
        lastMarketAt = Date.now();
        render();
      }
    } catch { /* main Sentinel remains alive */ }
    finally { marketBusy = false; }
  }

  async function pulseHolders() {
    if (holderBusy || !rows.length) return;
    holderBusy = true;
    try {
      const tokens = rows.filter((x) => !x.error).slice(0, 3).map((x) => x.tokenAddress);
      const response = await chrome.runtime.sendMessage({ type: 'NEO_HOLDER_PULSE', tokens });
      if (response?.ok) {
        for (const item of response.rows || []) holderState.set(item.tokenAddress, item);
        lastHolderAt = Date.now();
        render();
      }
    } catch { /* ignore transient RPC limit */ }
    finally { holderBusy = false; }
  }

  async function pulseSocial() {
    if (socialBusy || !rows.length) return;
    const candidates = rows.filter((x) => !x.error && x.realtimePriority >= 45).slice(0, 3);
    if (!candidates.length) return;
    const item = candidates[socialCursor % candidates.length];
    socialCursor += 1;
    socialBusy = true;
    try {
      const urls = [...(item.socialLinks || []), ...(item.websites || [])];
      const response = await chrome.runtime.sendMessage({
        type: 'NEO_SOCIAL_SCAN',
        urls,
        tokenMeta: { tokenAddress: item.tokenAddress, symbol: item.symbol, name: item.name },
      });
      if (response?.ok && response.result && !response.result.busy) {
        const scans = response.result.scans || [];
        const best = scans.filter((x) => !x.error).sort((a, b) => num(b.score) - num(a.score))[0];
        socialState.set(item.tokenAddress, {
          score: response.result.score || 0,
          at: response.result.at || Date.now(),
          latest: best ? `${best.postCount || 0} posts · ${best.latestMinutes == null ? '?' : Math.round(best.latestMinutes)}m` : 'no readable posts',
          scans,
        });
        lastSocialAt = Date.now();
        render();
      }
    } catch { /* social page can block automation; retry on next cycle */ }
    finally { socialBusy = false; }
  }

  function tick() {
    pulseMarket();
    if (Date.now() - lastHolderAt >= HOLDER_MS) pulseHolders();
    if (Date.now() - lastSocialAt >= SOCIAL_MS) pulseSocial();
  }

  setTimeout(tick, 1100);
  setInterval(tick, MARKET_MS);

  chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
    if (message?.type === 'NEO_REALTIME_STATUS') {
      sendResponse({ ok: true, rows: rows.slice(0, 3), marketAt: lastMarketAt, holderAt: lastHolderAt, socialAt: lastSocialAt });
      return false;
    }
    return false;
  });
})();
