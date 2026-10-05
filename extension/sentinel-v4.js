(() => {
  if (window.__NEO_SENTINEL_V08__) return;
  window.__NEO_SENTINEL_V08__ = true;

  const DISCOVERY_MS = 2200;
  const MARKET_MS = 3000;
  const SOCIAL_MS = 45_000;
  const DEEP_COOLDOWN_MS = 75_000;
  const VALIDATION_KEY = 'neo-v08-validation';
  const SOL_RE = /\b[1-9A-HJ-NP-Za-km-z]{32,44}\b/g;
  const EVM_RE = /\b0x[a-fA-F0-9]{40}\b/g;
  const STOP = new Set(['buy','sell','buys','sells','buyers','sellers','market','markets','price','volume','vol','liquidity','liq','mcap','market cap','time','token','tokens','trending','watchlist','crypto','most held','alerts','leaderboard','feed','home','portfolio','positions','orders','holders','top traders','dev tokens','follow','following','followers','share','deposit','withdraw','cash','chart','auto','manual','search','settings','trade','swap','live','new','all','fomo','activity','stats']);

  let resolved = [];
  let marketRows = [];
  let deepResults = [];
  let validation = [];
  let discoveryBusy = false;
  let marketBusy = false;
  let deepBusy = false;
  let socialBusy = false;
  let collapsed = false;
  let lastSocialAt = 0;
  const feedMap = new Map();
  const socialState = new Map();
  const deepCooldown = new Map();
  const diagnostics = { feed:0, addresses:0, labels:0, resolved:0, discoveryAt:0, marketAt:0, socialAt:0, lastError:'' };

  const n = (v) => Number.isFinite(Number(v)) ? Number(v) : 0;
  const clean = (v) => String(v ?? '').replace(/\s+/g, ' ').trim();
  const esc = (v) => String(v ?? '').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;').replaceAll("'",'&#039;');
  const money = (v) => { const x=n(v); if(x>=1e9)return `$${(x/1e9).toFixed(2)}B`; if(x>=1e6)return `$${(x/1e6).toFixed(2)}M`; if(x>=1e3)return `$${(x/1e3).toFixed(1)}K`; return `$${x.toFixed(x<1?6:0)}`; };
  const short = (v, size=5) => { const s=String(v||''); return s.length>size*2+2?`${s.slice(0,size)}…${s.slice(-size)}`:s; };
  const sourceName = () => location.hostname.includes('axiom') ? 'AXIOM' : location.hostname.includes('tinyastro') ? 'PHOTON' : 'FOMO';
  const chainName = (id) => ({solana:'SOL',robinhood:'RHC',base:'BASE',ethereum:'ETH',bsc:'BNB',monad:'MONAD',unknown:'?'}[String(id||'').toLowerCase()] || String(id||'?').toUpperCase());
  const validAddress = (v) => /^(?:0x[a-fA-F0-9]{40}|[1-9A-HJ-NP-Za-km-z]{32,44})$/.test(String(v||'').trim());

  window.addEventListener('message', (event) => {
    if (event.source !== window || !event.data?.__neoMemeCoins || event.data?.type !== 'NEO_PAGE_FEED') return;
    const now = Date.now();
    for (const c of event.data.candidates || []) {
      if (!validAddress(c?.address)) continue;
      const key = String(c.address).toLowerCase();
      const old = feedMap.get(key) || {};
      feedMap.set(key, { ...old, ...c, address:c.address, at:n(c.at)||now });
    }
    for (const [k, c] of feedMap) if (now - n(c.at) > 180_000) feedMap.delete(k);
    diagnostics.feed = feedMap.size;
    setTimeout(() => discover(true), 40);
  });

  function parseMoney(raw) {
    const m=String(raw||'').replace(/,/g,'').match(/([0-9]*\.?[0-9]+)\s*([KMBT])?/i); if(!m)return 0;
    const x=Number(m[1])||0,u=String(m[2]||'').toUpperCase(); return x*(u==='T'?1e12:u==='B'?1e9:u==='M'?1e6:u==='K'?1e3:1);
  }
  function metricsFrom(text) {
    const s=String(text||''); let marketCapUsd=0, priceUsd=0;
    for (const re of [/\$\s*([0-9.,]+\s*[KMBT]?)\s*(?:MC|MCap)\b/i,/(?:MC|MCap|Market\s*Cap)\s*[:·-]?\s*\$?\s*([0-9.,]+\s*[KMBT]?)/i]) {
      const m=s.match(re); if(m){ marketCapUsd=parseMoney(m[1]); if(marketCapUsd) break; }
    }
    for (const m of s.matchAll(/\$\s*(0?\.[0-9]+|[0-9][0-9.,]*(?:\.[0-9]+)?)/g)) {
      const around=s.slice(Math.max(0,(m.index||0)-14),Math.min(s.length,(m.index||0)+m[0].length+14));
      if (/MC|MCap|Market\s*Cap/i.test(around)) continue;
      const x=Number(String(m[1]).replace(/,/g,'')); if(Number.isFinite(x)&&x>0&&(!priceUsd||x<priceUsd)) priceUsd=x;
    }
    return { marketCapUsd, priceUsd };
  }
  function chainHint(text='') {
    const s=String(text).toLowerCase();
    if(/robinhood/.test(s))return 'robinhood'; if(/solana|\bsol\b/.test(s))return 'solana'; if(/bnb|bsc|binance/.test(s))return 'bsc';
    if(/ethereum|\beth\b/.test(s))return 'ethereum'; if(/\bbase\b/.test(s))return 'base'; if(/monad/.test(s))return 'monad'; return '';
  }
  function usableLabel(raw) {
    const s=clean(raw).replace(/^\$/,'').trim();
    if(s.length<2||s.length>48||STOP.has(s.toLowerCase()))return false;
    if(/^\$?[0-9.,]+(?:[KMBT])?(?:\s*MC)?$/i.test(s)||/^[+\-▲▼]?\s*[0-9.,]+%$/.test(s)||/^(24h|7d|30d|1h|5m|4h|1d|h|d|w|m)$/i.test(s))return false;
    if(validAddress(s)||/https?:\/\//i.test(s))return false;
    return /[A-Za-z\u0080-\uFFFF]/.test(s);
  }
  function visible(el) { if(!(el instanceof HTMLElement))return false; const r=el.getBoundingClientRect(); return r.width>18&&r.height>8&&r.bottom>-120&&r.top<innerHeight+120; }

  function collectDomDiscovery() {
    const addressScores = new Map();
    const addAddr = (raw, points=1, context='') => {
      const text=String(raw||'');
      for (const a of [...(text.match(SOL_RE)||[]),...(text.match(EVM_RE)||[])]) {
        const key=a.toLowerCase(), cur=addressScores.get(key)||{address:a,score:0,context:''};
        cur.score+=points; if(context&&!cur.context)cur.context=clean(context).slice(0,500); addressScores.set(key,cur);
      }
    };
    addAddr(location.href,30,location.href);
    for (const node of [...document.querySelectorAll('a[href],[data-token-address],[data-mint],[data-address],[data-ca],[data-contract],[data-token],[data-coin-address]')].slice(0,7500)) {
      if(node.closest?.('#neo-meme-coins-root'))continue;
      const ctx=clean(node.closest?.('tr,[role="row"],li,article,[class*="card"],[class*="token"],[class*="coin"],[class*="row"]')?.innerText||node.innerText||'');
      addAddr(node.getAttribute?.('href'),10,ctx);
      for(const k of ['data-token-address','data-mint','data-address','data-ca','data-contract','data-token','data-coin-address'])addAddr(node.getAttribute?.(k),20,ctx);
    }
    try { for(const e of performance.getEntriesByType('resource').slice(-1400)) addAddr(e?.name,6,e?.name); } catch {}
    addAddr(String(document.body?.innerText||'').slice(0,220000),2,'visible-body');

    const labels = new Map();
    const addLabel = (raw, context='') => {
      const q=clean(raw).replace(/^\$/,'').trim(); if(!usableLabel(q))return;
      const ctx=clean(context).slice(0,850), metrics=metricsFrom(ctx), hint=chainHint(ctx);
      const key=`${q.toLowerCase()}|${Math.round(metrics.marketCapUsd/1000)}|${hint}`;
      const row={query:q,context:ctx,...metrics,chainHint:hint};
      const current=labels.get(key); const rank=(x)=>(x?.marketCapUsd?4:0)+(x?.priceUsd?3:0)+(x?.chainHint?2:0)+(x?.context?1:0);
      if(!current||rank(row)>rank(current))labels.set(key,row);
    };
    for(const p of String(document.title||'').split(/[|·–—]/).map(clean).filter(Boolean))if(!/^fomo$/i.test(p))addLabel(p,document.title);
    for (const node of [...document.querySelectorAll('h1,h2,h3,h4,[class*="symbol"],[class*="ticker"],[class*="token"],[class*="coin"],[class*="pair"],tr,[role="row"],li,a,button')].slice(0,9500)) {
      if(!visible(node)||node.closest?.('#neo-meme-coins-root'))continue;
      const text=String(node.innerText||''); if(text.length<2||text.length>900)continue;
      const contextSignal=/\$|%|buyers|sellers|volume|liq|mcap|market cap|buy|sell|holder|trade|price/i.test(text)||/token|coin|trade|swap|pair|market/i.test(node.getAttribute?.('href')||'');
      if(!contextSignal&&text.trim().length>28)continue;
      const lines=text.split(/\n+/).map(clean).filter(Boolean);
      for(const line of lines.slice(0,9)){
        const sm=line.match(/^\$([A-Za-z0-9_.\-]{2,22})$/); if(sm){addLabel(sm[1],text);break;}
        if(usableLabel(line)&&line.length<=38){addLabel(line,text);break;}
      }
    }
    const addresses=[...addressScores.values()].sort((a,b)=>b.score-a.score).slice(0,36).map(x=>({...x,chainHint:chainHint(x.context)}));
    const items=[...labels.values()].slice(0,30);
    diagnostics.addresses=addresses.length; diagnostics.labels=items.length;
    return { addresses, items };
  }

  async function discover(force=false) {
    if(discoveryBusy)return;
    discoveryBusy=true;
    try {
      const dom=collectDomDiscovery();
      const feedCandidates=[...feedMap.values()].sort((a,b)=>n(b.at)-n(a.at)).slice(0,60);
      diagnostics.feed=feedCandidates.length;
      const r=await chrome.runtime.sendMessage({type:'NEO8_DISCOVER',feedCandidates,addresses:dom.addresses,items:dom.items});
      if(r?.ok&&Array.isArray(r.results)){
        resolved=r.results; diagnostics.resolved=resolved.length; diagnostics.discoveryAt=Date.now(); diagnostics.lastError='';
        render(); if(force||!marketRows.length)marketPulse();
      } else if(r?.error) diagnostics.lastError=String(r.error).slice(0,180);
    } catch(error) { diagnostics.lastError=String(error).slice(0,180); }
    finally { discoveryBusy=false; render(); }
  }

  async function marketPulse() {
    if(marketBusy||!resolved.length){render();return;}
    marketBusy=true;
    try {
      const items=resolved.slice(0,16).map(x=>({tokenAddress:x.tokenAddress||x.token,chainId:x.chainId,fallback:x}));
      const r=await chrome.runtime.sendMessage({type:'NEO8_MARKET',items});
      if(r?.ok&&Array.isArray(r.rows)){
        marketRows=r.rows; diagnostics.marketAt=Date.now(); diagnostics.lastError=''; updateValidation(); render(); autoDeep();
      } else if(r?.error) diagnostics.lastError=String(r.error).slice(0,180);
    } catch(error) { diagnostics.lastError=String(error).slice(0,180); }
    finally { marketBusy=false; render(); }
  }

  function candidateFor(row) {
    return resolved.find(x=>String(x.tokenAddress||x.token).toLowerCase()===String(row.tokenAddress||'').toLowerCase()) || row;
  }
  function autoDeep() {
    if(deepBusy)return;
    const pick=marketRows.filter(x=>!x.error&&x.priority>=48&&x.risk<=72).sort((a,b)=>b.priority-a.priority||a.risk-b.risk)
      .find(x=>Date.now()-(deepCooldown.get(`${x.chainId}:${x.tokenAddress}`)||0)>DEEP_COOLDOWN_MS);
    if(pick)runDeep(pick);
  }
  async function runDeep(row) {
    if(deepBusy)return;
    deepBusy=true; const key=`${row.chainId}:${row.tokenAddress}`; deepCooldown.set(key,Date.now()); render();
    try {
      const candidate=candidateFor(row);
      const ctx={source:sourceName(),url:location.href,title:document.title,text:clean(document.body?.innerText||'').slice(0,30000),socialLinks:[...(row.socialLinks||[])],sourceMeta:{chainId:row.chainId,feedSource:candidate.source||''}};
      const r=await chrome.runtime.sendMessage({type:'NEO8_DEEP',item:{tokenAddress:row.tokenAddress,chainId:row.chainId,fallback:candidate},pageContext:ctx});
      if(r?.ok&&r.result){
        const result=r.result; deepResults=[result,...deepResults.filter(x=>!(String(x.tokenAddress).toLowerCase()===String(result.tokenAddress).toLowerCase()&&x.chainId===result.chainId))].slice(0,10);
        recordPrediction(result); diagnostics.lastError='';
      } else if(r?.error) diagnostics.lastError=String(r.error).slice(0,180);
    } catch(error) { diagnostics.lastError=String(error).slice(0,180); }
    finally { deepBusy=false; render(); setTimeout(autoDeep,700); }
  }

  async function socialPulse() {
    if(socialBusy||!marketRows.length||Date.now()-lastSocialAt<SOCIAL_MS)return;
    const pool=marketRows.filter(x=>!x.error&&x.priority>=40).slice(0,5); if(!pool.length)return;
    const item=pool[Math.floor(Date.now()/SOCIAL_MS)%pool.length];
    const urls=[...(item.socialLinks||[]),...(item.websites||[])];
    if(!urls.length&&!item.symbol)return;
    socialBusy=true; lastSocialAt=Date.now();
    try {
      const r=await chrome.runtime.sendMessage({type:'NEO_SOCIAL_SCAN',urls,tokenMeta:{tokenAddress:item.tokenAddress,symbol:item.symbol,name:item.name}});
      if(r?.ok&&r.result&&!r.result.busy){socialState.set(`${item.chainId}:${item.tokenAddress}`,r.result);diagnostics.socialAt=Date.now();}
    } catch { /* retry later */ }
    finally { socialBusy=false;render(); }
  }

  async function loadValidation(){try{const s=await chrome.storage.local.get([VALIDATION_KEY]);validation=Array.isArray(s[VALIDATION_KEY])?s[VALIDATION_KEY].slice(-400):[];}catch{validation=[];}}
  async function saveValidation(){try{await chrome.storage.local.set({[VALIDATION_KEY]:validation.slice(-400)});}catch{}}
  function recordPrediction(r){
    const p=n(r?.market?.priceUsd),score=n(r?.outlook?.score);if(!p||!r?.tokenAddress)return;
    const direction=score>=60?'UP':score<=40?'DOWN':'NEUTRAL';
    if(validation.some(x=>String(x.token).toLowerCase()===String(r.tokenAddress).toLowerCase()&&x.chainId===r.chainId&&Date.now()-x.at<45000))return;
    validation.push({token:r.tokenAddress,chainId:r.chainId,symbol:r.market?.symbol||'',at:Date.now(),entryPrice:p,direction,score,s30:null,m2:null,m15:null});saveValidation();
  }
  function updateValidation(){
    let changed=false;
    for(const v of validation){
      const row=marketRows.find(x=>String(x.tokenAddress).toLowerCase()===String(v.token).toLowerCase()&&(x.chainId===v.chainId||!v.chainId));
      if(!row?.priceUsd||!v.entryPrice)continue;
      const age=Date.now()-v.at,chg=(row.priceUsd-v.entryPrice)/v.entryPrice*100;
      const judge=()=>v.direction==='UP'?chg>=1:v.direction==='DOWN'?chg<=-1:Math.abs(chg)<3;
      if(age>=30000&&!v.s30){v.s30={changePct:chg,correct:judge()};changed=true;}
      if(age>=120000&&!v.m2){v.m2={changePct:chg,correct:judge()};changed=true;}
      if(age>=900000&&!v.m15){v.m15={changePct:chg,correct:judge()};changed=true;}
    }
    if(changed)saveValidation();
  }
  function valSummary(field){const a=validation.filter(x=>x[field]&&x.direction!=='NEUTRAL'),c=a.filter(x=>x[field].correct).length;return `${c}/${a.length}${a.length?` · ${Math.round(c/a.length*100)}%`:''}`;}

  const old=document.getElementById('neo-meme-coins-root');if(old)old.remove();
  const host=document.createElement('div');host.id='neo-meme-coins-root';Object.assign(host.style,{all:'initial',position:'fixed',zIndex:'2147483647',right:'12px',top:'66px'});document.documentElement.appendChild(host);
  const shadow=host.attachShadow({mode:'open'});
  shadow.innerHTML=`
    <style>
      *{box-sizing:border-box}button{font:inherit;cursor:pointer}.neo{width:420px;max-height:calc(100vh - 78px);overflow:hidden;border:1px solid rgba(255,255,255,.13);border-radius:18px;background:rgba(7,9,13,.985);color:#e8edf2;font-family:Inter,system-ui,sans-serif;box-shadow:0 22px 70px rgba(0,0,0,.6)}
      .head{display:flex;align-items:center;gap:10px;padding:12px 13px;border-bottom:1px solid rgba(255,255,255,.08)}.mark{width:34px;height:34px;display:grid;place-items:center;border-radius:10px;background:#34d399;color:#04100b;font-weight:1000}.brand{font-size:12px;font-weight:950;color:#fff}.sub{font-size:8px;color:#667385;margin-top:2px}.source{margin-left:auto;border:1px solid rgba(52,211,153,.25);color:#6ee7b7;border-radius:7px;padding:4px 7px;font-size:7px;font-weight:950}.collapse{border:0;background:transparent;color:#7a8695;font-size:15px}.body{max-height:calc(100vh - 136px);overflow:auto;padding:11px}.body::-webkit-scrollbar{width:5px}.body::-webkit-scrollbar-thumb{background:#29303a;border-radius:5px}
      .live{display:flex;justify-content:space-between;align-items:center;border:1px solid rgba(52,211,153,.2);background:rgba(52,211,153,.05);border-radius:11px;padding:8px 9px}.live b{font-size:8px;letter-spacing:.11em;color:#6ee7b7}.live small{font-size:7px;color:#687586}.dot{display:inline-block;width:6px;height:6px;border-radius:50%;background:#34d399;margin-right:6px;box-shadow:0 0 10px #34d399}
      .diag{border:1px solid rgba(96,165,250,.2);background:rgba(96,165,250,.045);border-radius:12px;padding:9px;margin-top:8px}.diag strong{font-size:8px;color:#93c5fd;letter-spacing:.1em}.diag p{margin:5px 0 0;font-size:8px;line-height:1.5;color:#7d8a9b}.label{font-size:7.5px;font-weight:950;letter-spacing:.14em;color:#657182;margin:11px 2px 6px}.list{display:grid;gap:6px}.card{border:1px solid rgba(255,255,255,.08);border-radius:11px;padding:9px;background:rgba(255,255,255,.022)}.card.top{border-color:rgba(52,211,153,.25);background:rgba(52,211,153,.035)}.row{display:flex;justify-content:space-between;gap:8px;align-items:start}.name{font-size:10px;font-weight:950;color:#fff}.meta{font-size:7.5px;color:#687586;margin-top:2px}.badge{font-size:7px;font-weight:950;border-radius:6px;padding:3px 5px;background:rgba(96,165,250,.1);color:#93c5fd}.metrics{display:grid;grid-template-columns:repeat(4,1fr);gap:4px;margin-top:7px}.metrics div{border:1px solid rgba(255,255,255,.055);border-radius:7px;padding:5px}.metrics span{display:block;font-size:6.5px;color:#647183}.metrics b{display:block;font-size:8.5px;color:#fff;margin-top:2px}.reason{font-size:7.5px;color:#7b8797;margin-top:6px;line-height:1.4}.deepDecision{font-size:15px;font-weight:1000;color:#fff}.empty{border:1px solid rgba(255,255,255,.07);border-radius:11px;padding:11px;font-size:8px;color:#7b8797;line-height:1.5}.validation{display:grid;grid-template-columns:repeat(3,1fr);gap:5px}.validation div{border:1px solid rgba(255,255,255,.07);border-radius:9px;padding:7px;text-align:center}.validation span{display:block;font-size:6.5px;color:#647183}.validation b{display:block;font-size:8px;color:#fff;margin-top:3px}.err{margin-top:7px;color:#fca5a5;font-size:7px;line-height:1.35}.foot{margin-top:9px;font-size:6.8px;color:#566272;line-height:1.45;text-align:center}
    </style>
    <div class="neo"><div class="head"><div class="mark">N</div><div><div class="brand">NEO Meme Coins</div><div class="sub">Sentinel v0.8 · terminal-feed intelligence</div></div><span class="source">${esc(sourceName())}</span><button class="collapse">−</button></div><div class="body"></div></div>`;
  const body=shadow.querySelector('.body');
  shadow.querySelector('.collapse').addEventListener('click',()=>{collapsed=!collapsed;body.style.display=collapsed?'none':'block';shadow.querySelector('.collapse').textContent=collapsed?'+':'−';});

  function render(){
    const top=marketRows.filter(x=>!x.error).slice(0,6);
    const deep=deepResults.slice(0,4);
    const age=(ts)=>ts?`${Math.max(0,Math.round((Date.now()-ts)/1000))}s ago`:'waiting';
    const radarHtml=top.map((x,i)=>{
      const social=socialState.get(`${x.chainId}:${x.tokenAddress}`); const socialScore=n(social?.score);
      return `<div class="card ${i===0?'top':''}"><div class="row"><div><div class="name">$${esc(x.symbol||'?')} · ${esc(x.name||'')}</div><div class="meta">${esc(chainName(x.chainId))} · ${esc(short(x.tokenAddress))}${x.dexBacked===false?' · terminal feed':''}</div></div><span class="badge">${esc(x.status||'WATCH')}</span></div><div class="metrics"><div><span>PRIORITY</span><b>${esc(x.priority||0)}/100</b></div><div><span>PRE-RISK</span><b>${esc(x.risk||0)}/100</b></div><div><span>5M</span><b>${n(x.price5m)>=0?'+':''}${esc(n(x.price5m).toFixed(1))}%</b></div><div><span>SOCIAL</span><b>${socialScore?`${socialScore}/100`:'…'}</b></div></div><div class="reason">${esc((x.positives||[])[0]||(x.reasons||[])[0]||`liq ${money(x.liquidityUsd)} · MC ${money(x.marketCapUsd)}`)}</div></div>`;
    }).join('');
    const deepHtml=deep.map((r)=>`<div class="card"><div class="row"><div><div class="deepDecision">${esc(r.posture||'WATCH')}</div><div class="meta">$${esc(r.market?.symbol||'?')} · ${esc(chainName(r.chainId))}</div></div><span class="badge">CONF ${esc(r.confidence??0)}%</span></div><div class="metrics"><div><span>RISK</span><b>${esc(r.risk??'-')}/100</b></div><div><span>OUTLOOK</span><b>${esc(r.outlook?.score??'-')}/100</b></div><div><span>LIQ</span><b>${esc(money(r.market?.liquidityUsd))}</b></div><div><span>MC</span><b>${esc(money(r.market?.marketCapUsd))}</b></div></div><div class="reason">${esc(r.signals?.[0]?.detail||r.outlook?.label||'Deep evidence collected.')}</div></div>`).join('');
    body.innerHTML=`<div class="live"><b><span class="dot"></span>SENTINEL ACTIVE</b><small>${esc(resolved.length)} resolved · ${esc(sourceName())}</small></div><div class="diag"><strong>⚡ REALTIME DISCOVERY + MARKET</strong><p>Feed ${diagnostics.feed} · DOM addresses ${diagnostics.addresses} · labels ${diagnostics.labels} · resolved ${diagnostics.resolved}<br>Discovery ${age(diagnostics.discoveryAt)} · market ${age(diagnostics.marketAt)} · social ${age(diagnostics.socialAt)}</p>${diagnostics.lastError?`<div class="err">${esc(diagnostics.lastError)}</div>`:''}</div><div class="label">LIVE RADAR · TOP CANDIDATES</div><div class="list">${radarHtml||'<div class="empty">NEO слуша network/WebSocket feed-а на терминала и паралелно сканира DOM-а. Ако Fomo изпрати token contract в своите данни, той ще бъде хванат автоматично.</div>'}</div><div class="label">AUTO DEEP ANALYSIS ${deepBusy?'· RUNNING':''}</div><div class="list">${deepHtml||'<div class="empty">Deep analysis ще стартира автоматично веднага щом има валидиран candidate.</div>'}</div><div class="label">LOCAL VALIDATION</div><div class="validation"><div><span>30 SEC</span><b>${esc(valSummary('s30'))}</b></div><div><span>2 MIN</span><b>${esc(valSummary('m2'))}</b></div><div><span>15 MIN</span><b>${esc(valSummary('m15'))}</b></div></div><div class="foot">PRE-RISK е market triage. Финалният Risk използва наличния deep evidence. NEO не гарантира бъдещо движение и не изпълнява сделки.</div>`;
  }

  chrome.runtime.onMessage.addListener((msg,_sender,sendResponse)=>{
    if(msg?.type==='NEO_RESCAN'){discover(true);sendResponse({ok:true});return false;}
    if(msg?.type==='NEO_STATUS'){
      const best=deepResults[0];sendResponse({ok:true,source:sourceName(),observedCount:resolved.length,deepScanning:deepBusy,radarScanning:marketBusy,queue:0,radarTop:marketRows.slice(0,3),result:best?{posture:best.posture,risk:best.risk,confidence:best.confidence,symbol:best.market?.symbol,name:best.market?.name}:null,diagnostics:{...diagnostics}});return false;
    }
    return false;
  });

  loadValidation().finally(()=>{render();discover(true);});
  setInterval(()=>discover(false),DISCOVERY_MS);
  setInterval(()=>marketPulse(),MARKET_MS);
  setInterval(()=>socialPulse(),5000);
  const observer=new MutationObserver(()=>setTimeout(()=>discover(false),220));
  observer.observe(document.documentElement,{subtree:true,childList:true,attributes:true,attributeFilter:['href','data-token-address','data-mint','data-address','data-ca','data-contract','data-token','data-coin-address']});
  document.addEventListener('click',()=>setTimeout(()=>discover(true),120),true);
})();
