(() => {
  if (window.__NEO_VISUAL_LABEL_DISCOVERY_V09__) return;
  window.__NEO_VISUAL_LABEL_DISCOVERY_V09__ = true;

  const ROOT_ID = 'neo-visual-label-helper-v09';
  const STOP = new Set([
    'buy','sell','buys','sells','buyers','sellers','market','markets','price','volume','vol','liquidity','liq','mcap','market cap','time','token','tokens','trending','watchlist','crypto','most held','alerts','leaderboard','feed','home','portfolio','positions','orders','holders','top traders','dev tokens','follow','following','followers','share','deposit','withdraw','cash','chart','auto','manual','search','settings','trade','swap','live','new','all','fomo','activity','stats','deposit more','withdraw','edit profile','24h','7d','30d','1h','5m','4h','1d'
  ]);

  const clean = (v) => String(v ?? '').replace(/\s+/g, ' ').trim();
  const visible = (el) => {
    if (!(el instanceof HTMLElement)) return false;
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return r.width > 8 && r.height > 7 && r.bottom >= 0 && r.top <= innerHeight && s.display !== 'none' && s.visibility !== 'hidden' && Number(s.opacity || 1) > 0;
  };
  const isCandidate = (raw) => {
    const s = clean(raw).replace(/^\$/, '').trim();
    if (s.length < 2 || s.length > 34) return false;
    if (STOP.has(s.toLowerCase())) return false;
    if (/^\$?[0-9.,]+(?:[KMBT])?(?:\s*MC)?$/i.test(s)) return false;
    if (/^[+\-▲▼]?\s*[0-9.,]+%$/.test(s)) return false;
    if (/^(?:0x[a-fA-F0-9]{40}|[1-9A-HJ-NP-Za-km-z]{32,44})$/.test(s)) return false;
    if (/https?:\/\//i.test(s)) return false;
    if (/^[\W_]+$/.test(s)) return false;
    if ((s.match(/\s+/g) || []).length > 5) return false;
    return /[A-Za-z\u0080-\uFFFF]/.test(s);
  };

  function bestContext(el) {
    let node = el;
    let fallback = '';
    for (let i = 0; i < 6 && node; i += 1, node = node.parentElement) {
      const text = clean(node.innerText || '');
      if (!text || text.length > 900) continue;
      if (!fallback && text.length >= 4) fallback = text;
      const score =
        (/\$\s*[0-9]/.test(text) ? 3 : 0) +
        (/%/.test(text) ? 2 : 0) +
        (/\b(?:MC|MCap|Market\s*Cap|vol|volume|liq|liquidity|buyers?|sellers?|buys?|sells?|holders?)\b/i.test(text) ? 3 : 0);
      if (score >= 3) return text;
    }
    const parent = el.parentElement;
    if (parent) {
      const around = clean([...parent.children].slice(0, 12).map((x) => x.innerText || '').join(' '));
      if (around && around.length <= 900) return around;
    }
    return fallback;
  }

  function collect() {
    const map = new Map();
    const nodes = [...document.querySelectorAll('span,div,p,a,button,strong,b')].slice(0, 22000);
    for (const el of nodes) {
      if (!visible(el)) continue;
      if (el.closest(`#${ROOT_ID},#neo-meme-coins-root`)) continue;
      const text = clean(el.innerText || '');
      if (!isCandidate(text)) continue;

      // Prefer leaf-ish text nodes. Large containers often create fake labels.
      const childText = [...el.children].filter((c) => visible(c)).map((c) => clean(c.innerText || '')).filter(Boolean);
      if (childText.length && childText.some((x) => x === text)) continue;
      if (text.length > 22 && childText.length > 2) continue;

      const context = bestContext(el);
      if (!context) continue;
      const signal = (/\$\s*[0-9]/.test(context) ? 4 : 0) + (/%/.test(context) ? 2 : 0) + (/\b(?:MC|MCap|vol|volume|liq|buyers?|sellers?|buy|sell)\b/i.test(context) ? 3 : 0);
      const tickerish = /^\$?[A-Z0-9_.\-]{2,16}$/.test(text) ? 3 : 0;
      const key = text.toLowerCase();
      const row = { label: text.replace(/^\$/, ''), context, score: signal + tickerish };
      const current = map.get(key);
      if (!current || row.score > current.score) map.set(key, row);
    }
    return [...map.values()].sort((a, b) => b.score - a.score).slice(0, 80);
  }

  function renderHelper(rows) {
    let root = document.getElementById(ROOT_ID);
    if (!root) {
      root = document.createElement('div');
      root.id = ROOT_ID;
      Object.assign(root.style, {
        position: 'fixed', left: '-12000px', top: '0', width: '640px', maxHeight: '900px',
        overflow: 'hidden', opacity: '0.001', pointerEvents: 'none', zIndex: '-1'
      });
      document.documentElement.appendChild(root);
    }
    root.replaceChildren();
    for (const row of rows) {
      const item = document.createElement('div');
      item.className = 'neo-token-visual-candidate token market-asset-row';
      item.style.cssText = 'display:block;width:620px;min-height:14px;font-size:10px;line-height:12px;';
      item.textContent = `${row.label}\n${row.context}`;
      root.appendChild(item);
    }
    document.documentElement.setAttribute('data-neo-v09-visual-labels', String(rows.length));
  }

  let busy = false;
  function scan() {
    if (busy) return;
    busy = true;
    try { renderHelper(collect()); } finally { busy = false; }
  }

  setTimeout(scan, 500);
  setInterval(scan, 1800);
  document.addEventListener('click', () => setTimeout(scan, 180), true);
  const observer = new MutationObserver(() => setTimeout(scan, 450));
  observer.observe(document.documentElement, { childList: true, subtree: true, characterData: true });
})();
