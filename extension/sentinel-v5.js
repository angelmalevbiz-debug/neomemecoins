(() => {
  if (window.__NEO_SENTINEL_V081__) return;
  window.__NEO_SENTINEL_V081__ = true;

  const DISCOVERY_MS = 2200;
  const MARKET_MS = 3000;
  const SOCIAL_MS = 45000;
  const DEEP_COOLDOWN_MS = 75000;
  const VALIDATION_KEY = 'neo-v081-validation';
  const SOL_RE = /\b[1-9A-HJ-NP-Za-km-z]{32,44}\b/g;
  const EVM_RE = /\b0x[a-fA-F0-9]{40}\b/g;
  const STOP = new Set(['buy','sell','buys','sells','buyers','sellers','market','markets','price','volume','vol','liquidity','liq','mcap','market cap','time','token','tokens','trending','watchlist','crypto','most held','alerts','leaderboard','feed','home','portfolio','positions','orders','holders','top traders','dev tokens','follow','following','followers','share','deposit','withdraw','cash','chart','auto','manual','search','settings','trade','swap','live','new','all','fomo','activity','stats']);

  let resolved = [], marketRows = [], deepResults = [], validation = [];
  let discoveryBusy = false, marketBusy = false, deepBusy = false, socialBusy = false, collapsed = false, lastSocialAt = 0;
  const feedMap = new Map(), socialState = new Map(), deepCooldown = new Map();
  const diagnostics = { feed:0, feedStructured:0, addresses:0, labels:0, resolved:0, discoveryAt:0, marketAt:0, socialAt:0, lastError:'', debug:null };

  const n = (v) => Number.isFinite(Number(v)) ? Number(v) : 0;
  const clean = (v) => String(v ?? '').replace(/\s+/g,' ').trim();
  const esc = (v) => String(v ?? '').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;').replaceAll("'",'&#039;');
  const money = (v) => { const x=n(v); if(x>=1e9)return `$${(x/1e9).toFixed(2)}B`; if(x>=1e6)return `$${(x/1e6).toFixed(2)}M`; if(x>=1e3)return `$${(x/1e3).toFixed(1)}K`; return `$${x.toFixed(x<1?6:0)}`; };
  const short = (v,s=5) => { const x=String(v||''); return x.length>s*2+2?`${x.slice(0,s)}…${x.slice(-s)}`:x; };
  const sourceName = () => location.hostname.includes('axiom')?'AXIOM':location.hostname.includes('tinyastro')?'PHOTON':'FOMO';
  const chainName = (id) => ({solana:'SOL',robinhood:'RHC',base:'BASE',ethereum:'ETH',bsc:'BNB',monad:'MONAD',unknown:'?'}[String(id||'').toLowerCase()]||String(id||'?').toUpperCase());
  const validAddress = (v) => /^(?:0x[a-fA-F0-9]{40}|[1-9A-HJ-NP-Za-km-z]{32,44})$/.test(String(v||'').trim());

  window.addEventListener('message',(event)=>{
    if(event.source!==window || !event.data?.__neoMemeCoins || event.data?.type!=='NEO_PAGE_FEED_081')return;
    const now=Date.now();
    for(const c of event.data.candidates||[]){
      if(!validAddress(c?.address))continue;
      const key=String(c.address).toLowerCase(),old=feedMap.get(key)||{};
      feedMap.set(key,{...old,...c,address:c.address,at:n(c.at)||now});
    }
    for(const [k,c] of feedMap)if(now-n(c.at)>240000)feedMap.delete(k);
    diagnostics.feed=feedMap.size;
    diagnostics.feedStructured=[...feedMap.values()].filter(x=>x.symbol||x.name||n(x.marketCapUsd)>0||n(x.priceUsd)>0).length;
    setTimeout(()=>discover(true),30);
  });

  function parseMoney(raw){const m=String(raw||'').replace(/,/g,'').match(/([0-9]*\.?[0-9]+)\s*([KMBT])?/i);if(!m)return 0;const x=Number(m[1])||0,u=String(m[2]||'').toUpperCase();return x*(u==='T'?1e12:u==='B'?1e9:u==='M'?1e6:u==='K'?1e3:1);}
  function metricsFrom(text){
    const s=String(text||'');let marketCapUsd=0,priceUsd=0;
    for(const re of [/\$\s*([0-9.,]+\s*[KMBT]?)\s*(?:MC|MCap)\b/i,/(?:MC|MCap|Market\s*Cap)\s*[:·-]?\s*\$?\s*([0-9.,]+\s*[KMBT]?)/i]){const m=s.match(re);if(m){marketCapUsd=parseMoney(m[1]);if(marketCapUsd)break;}}
    for(const m of s.matchAll(/\$\s*(0?\.[0-9]+|[0-9][0-9.,]*(?:\.[0-9]+)?)/g)){const around=s.slice(Math.max(0,(m.index||0)-14),Math.min(s.length,(m.index||0)+m[0].length+14));if(/MC|MCap|Market\s*Cap/i.test(around))continue;const x=Number(String(m[1]).replace(/,/g,''));if(Number.isFinite(x)&&x>0&&(!priceUsd||x<priceUsd))priceUsd=x;}
    return {marketCapUsd,priceUsd};
  }
  function chainHint(text=''){const s=String(text).toLowerCase();if(/robinhood|rhc/.test(s))return 'robinhood';if(/solana|\bsol\b/.test(s))return 'solana';if(/bnb|bsc|binance/.test(s))return 'bsc';if(/ethereum|\beth\b/.test(s))return 'ethereum';if(/\bbase\b/.test(s))return 'base';if(/monad/.test(s))return 'monad';return '';}
  function usableLabel(raw){const s=clean(raw).replace(/^\$/,'').trim();if(s.length<2||s.length>48||STOP.has(s.toLowerCase()))return false;if(/^\$?[0-9.,]+(?:[KMBT])?(?:\s*MC)?$/i.test(s)||/^[+\-▲▼]?\s*[0-9.,]+%$/.test(s)||/^(24h|7d|30d|1h|5m|4h|1d|h|d|w|m)$/i.test(s))return false;if(validAddress(s)||/https?:\/\//i.test(s))return false;return /[A-Za-z\u0080-\uFFFF]/.test(s);}
  function visible(el){if(!(el instanceof HTMLElement))return false;const r=el.getBoundingClientRect();return r.width>18&&r.height>8&&r.bottom>-150&&r.top<innerHeight+150;}

  function collectDomDiscovery(){
    const addressScores=new Map();
    const addAddr=(raw,points=1,context='')=>{const text=String(raw||'');for(const a of [...(text.match(SOL_RE)||[]),...(text.match(EVM_RE)||[])]){const key=a.toLowerCase(),cur=addressScores.get(key)||{address:a,score:0,context:''};cur.score+=points;if(context&&!cur.context)cur.context=clean(context).slice(0,700);addressScores.set(key,cur);}};
    addAddr(location.href,30,location.href);
    for(const node of [...document.querySelectorAll('a[href],[data-token-address],[data-mint],[data-address],[data-ca],[data-contract],[data-token],[data-coin-address]')].slice(0,9000)){
      if(node.closest?.('#neo-meme-coins-root'))continue;
      const ctx=clean(node.closest?.('tr,[role="row"],li,article,[class*="card"],[class*="token"],[class*="coin"],[class*="row"]')?.innerText||node.innerText||'');
      addAddr(node.getAttribute?.('href'),10,ctx);for(const k of ['data-token-address','data-mint','data-address','data-ca','data-contract','data-token','data-coin-address'])addAddr(node.getAttribute?.(k),20,ctx);
    }
    try{for(const e of performance.getEntriesByType('resource').slice(-1600))addAddr(e?.name,5,e?.name);}catch{}
    addAddr(String(document.body?.innerText||'').slice(0,260000),2,'visible-body');

    const labels=new Map();
    const addLabel=(raw,context='')=>{const q=clean(raw).replace(/^\$/,'').trim();if(!usableLabel(q))return;const ctx=clean(context).slice(0,1000),m=metricsFrom(ctx),hint=chainHint(ctx),key=`${q.toLowerCase()}|${Math.round(m.marketCapUsd/1000)}|${hint}`;const row={query:q,context:ctx,...m,chainHint:hint};const rank=(x)=>(x?.marketCapUsd?4:0)+(x?.priceUsd?3:0)+(x?.chainHint?2:0)+(x?.context?1:0);const cur=labels.get(key);if(!cur||rank(row)>rank(cur))labels.set(key,row);};
    for(const p of String(document.title||'').split(/[|·–—]/).map(clean).filter(Boolean))if(!/^fomo$/i.test(p))addLabel(p,document.title);
    const selectors='h1,h2,h3,h4,[class*="symbol"],[class*="ticker"],[class*="token"],[class*="coin"],[class*="pair"],tr,[role="row"],li,a,button,[class*="market"],[class*="asset"]';
    for(const node of [...document.querySelectorAll(selectors)].slice(0,12000)){
      if(!visible(node)||node.closest?.('#neo-meme-coins-root'))continue;const text=String(node.innerText||'');if(text.length<2||text.length>1200)continue;
      const signal=/\$|%|buyers|sellers|volume|liq|mcap|market cap|buy|sell|holder|trade|price/i.test(text)||/token|coin|trade|swap|pair|market|asset/i.test(node.getAttribute?.('href')||'');if(!signal&&text.trim().length>32)continue;
      const lines=text.split(/\n+/).map(clean).filter(Boolean);
      for(const line of lines.slice(0,12)){const sm=line.match(/^\$([A-Za-z0-9_.\-]{2,24})$/);if(sm){addLabel(sm[1],text);break;}if(usableLabel(line)&&line.length<=44){addLabel(line,text);break;}}
    }
    const addresses=[...addressScores.values()].sort((a,b)=>b.score-a.score).slice(0,48).map(x=>({...x,chainHint:chainHint(x.context)}));
    const items=[...labels.values()].slice(0,40);diagnostics.addresses=addresses.length;diagnostics.labels=items.length;return {addresses,items};
  }

  async function discover(force=false){
    if(discoveryBusy)return;discoveryBusy=true;
    try{
      const dom=collectDomDiscovery();const feedCandidates=[...feedMap.values()].sort((a,b)=>n(b.quality)-n(a.quality)||n(b.at)-n(a.at)).slice(0,120);
      diagnostics.feed=feedCandidates.length;diagnostics.feedStructured=feedCandidates.filter(x=>x.symbol||x.name||n(x.marketCapUsd)>0||n(x.priceUsd)>0).length;
      const r=await chrome.runtime.sendMessage({type:'NEO81_DISCOVER',feedCandidates,addresses:dom.addresses,items:dom.items});
      if(r?.ok&&Array.isArray(r.results)){resolved=r.results;diagnostics.resolved=resolved.length;diagnostics.discoveryAt=Date.now();diagnostics.lastError='';diagnostics.debug=r.debug||null;render();if(force||!marketRows.length)marketPulse();}
      else if(r?.error)diagnostics.lastError=String(r.error).slice(0,220);
    }catch(e){diagnostics.lastError=String(e).slice(0,220);}finally{discoveryBusy=false;render();}
  }

  async function marketPulse(){
    if(marketBusy||!resolved.length){render();return;}marketBusy=true;
    try{const items=resolved.slice(0,16).map(x=>({tokenAddress:x.tokenAddress||x.token,chainId:x.chainId,fallback:x}));const r=await chrome.runtime.sendMessage({type:'NEO81_MARKET',items});if(r?.ok&&Array.isArray(r.rows)){marketRows=r.rows;diagnostics.marketAt=Date.now();diagnostics.lastError='';updateValidation();render();autoDeep();}else if(r?.error)diagnostics.lastError=String(r.error).slice(0,220);}catch(e){diagnostics.lastError=String(e).slice(0,220);}finally{marketBusy=false;render();}
  }

  function candidateFor(row){return resolved.find(x=>String(x.tokenAddress||x.token).toLowerCase()===String(row.tokenAddress||'').toLowerCase())||row;}
  function autoDeep(){if(deepBusy)return;const pick=marketRows.filter(x=>!x.error&&x.priority>=44&&x.risk<=74).sort((a,b)=>b.priority-a.priority||a.risk-b.risk).find(x=>Date.now()-(deepCooldown.get(`${x.chainId}:${x.tokenAddress}`)||0)>DEEP_COOLDOWN_MS);if(pick)runDeep(pick);}
  async function runDeep(row){if(deepBusy)return;deepBusy=true;const key=`${row.chainId}:${row.tokenAddress}`;deepCooldown.set(key,Date.now());render();try{const candidate=candidateFor(row),ctx={source:sourceName(),url:location.href,title:document.title,text:clean(document.body?.innerText||'').slice(0,32000),socialLinks:[...(row.socialLinks||[])],sourceMeta:{chainId:row.chainId,feedSource:candidate.source||''}};const r=await chrome.runtime.sendMessage({type:'NEO81_DEEP',item:{tokenAddress:row.tokenAddress,chainId:row.chainId,fallback:candidate},pageContext:ctx});if(r?.ok&&r.result){const result=r.result;deepResults=[result,...deepResults.filter(x=>!(String(x.tokenAddress).toLowerCase()===String(result.tokenAddress).toLowerCase()&&x.chainId===result.chainId))].slice(0,10);recordPrediction(result);diagnostics.lastError='';}else if(r?.error)diagnostics.lastError=String(r.error).slice(0,220);}catch(e){diagnostics.lastError=String(e).slice(0,220);}finally{deepBusy=false;render();setTimeout(autoDeep,700);}}

  async function socialPulse(){if(socialBusy||!marketRows.length||Date.now()-lastSocialAt<SOCIAL_MS)return;const pool=marketRows.filter(x=>!x.error&&x.priority>=38).slice(0,5);if(!pool.length)return;const item=pool[Math.floor(Date.now()/SOCIAL_MS)%pool.length],urls=[...(item.socialLinks||[]),...(item.websites||[])];if(!urls.length&&!item.symbol)return;socialBusy=true;lastSocialAt=Date.now();try{const r=await chrome.runtime.sendMessage({type:'NEO_SOCIAL_SCAN',urls,tokenMeta:{tokenAddress:item.tokenAddress,symbol:item.symbol,name:item.name}});if(r?.ok&&r.result&&!r.result.busy){socialState.set(`${item.chainId}:${item.tokenAddress}`,r.result);diagnostics.socialAt=Date.now();}}catch{}finally{socialBusy=false;render();}}

  async function loadValidation(){try{const s=await chrome.storage.local.get([VALIDATION_KEY]);validation=Array.isArray(s[VALIDATION_KEY])?s[VALIDATION_KEY].slice(-400):[];}catch{validation=[];}}
  async function saveValidation(){try{await chrome.storage.local.set({[VALIDATION_KEY]:validation.slice(-400)});}catch{}}
  function recordPrediction(r){const p=n(r?.market?.priceUsd),score=n(r?.outlook?.score);if(!p||!r?.tokenAddress)return;const direction=score>=60?'UP':score<=40?'DOWN':'NEUTRAL';if(validation.some(x=>x.token===r.tokenAddress&&x.chainId===r.chainId&&Date.now()-x.at<60000))return;validation.push({token:r.tokenAddress,chainId:r.chainId,symbol:r.market?.symbol||'',at:Date.now(),entryPrice:p,direction,score,s30:null,m2:null,m15:null});saveValidation();}
  function updateValidation(){let changed=false;for(const v of validation){const row=marketRows.find(x=>String(x.tokenAddress).toLowerCase()===String(v.token).toLowerCase()&&x.chainId===v.chainId);if(!row?.priceUsd||!v.entryPrice)continue;const age=Date.now()-v.at,chg=(row.priceUsd-v.entryPrice)/v.entryPrice*100,judge=()=>v.direction==='UP'?chg>=1:v.direction==='DOWN'?chg<=-1:Math.abs(chg)<3;if(age>=30000&&!v.s30){v.s30={changePct:chg,correct:judge()};changed=true;}if(age>=120000&&!v.m2){v.m2={changePct:chg,correct:judge()};changed=true;}if(age>=900000&&!v.m15){v.m15={changePct:chg,correct:judge()};changed=true;}}if(changed)saveValidation();}
  function valSummary(field){const a=validation.filter(x=>x[field]&&x.direction!=='NEUTRAL'),c=a.filter(x=>x[field].correct).length;return `${c}/${a.length}${a.length?` · ${Math.round(c/a.length*100)}%`:''}`;}

  const old=document.getElementById('neo-meme-coins-root');if(old)old.remove();
  const host=document.createElement('div');host.id='neo-meme-coins-root';Object.assign(host.style,{all:'initial',position:'fixed',zIndex:'2147483647',right:'12px',top:'66px'});document.documentElement.appendChild(host);const shadow=host.attachShadow({mode:'open'});
  shadow.innerHTML=`<style>*{box-sizing:border-box}.p{width:390px;max-height:calc(100vh - 82px);overflow:auto;background:#080b10;border:1px solid #27313d;border-radius:18px;color:#dce6ef;font:11px/1.45 Inter,system-ui,sans-serif;box-shadow:0 18px 60px #0008}.h{display:flex;align-items:center;gap:10px;padding:12px;border-bottom:1px solid #1b2530;position:sticky;top:0;background:#080b10;z-index:2}.logo{width:34px;height:34px;border-radius:11px;background:#34d399;color:#04120d;display:grid;place-items:center;font-weight:1000}.brand{font-size:12px;font-weight:900;color:#fff}.sub{font-size:8px;color:#657386}.tag{margin-left:auto;border:1px solid #1f6a54;border-radius:7px;padding:4px 7px;color:#6ee7b7;font-size:8px;font-weight:900}.min{border:0;background:transparent;color:#7b8797;font-size:15px;cursor:pointer}.body{padding:10px}.live{border:1px solid #155b49;background:#0b1716;border-radius:12px;padding:8px;color:#6ee7b7;font-weight:900;letter-spacing:.08em;font-size:8px;display:flex;justify-content:space-between}.card{border:1px solid #202b37;background:#0d1218;border-radius:13px;padding:10px;margin-top:9px}.ey{font-size:8px;color:#6d7b8d;font-weight:900;letter-spacing:.11em;margin:10px 2px 4px}.muted{color:#738195}.diag{color:#8ba0b5;font-size:8px;margin-top:4px}.row{border:1px solid #263342;background:#101720;border-radius:11px;padding:9px;margin-top:6px}.top{display:flex;justify-content:space-between;gap:8px}.name{color:#fff;font-weight:900}.status{font-size:8px;padding:3px 6px;border-radius:6px;background:#112d27;color:#6ee7b7;font-weight:900}.grid{display:grid;grid-template-columns:repeat(4,1fr);gap:5px;margin-top:7px}.m{font-size:7px;color:#667486}.m b{display:block;color:#fff;font-size:9px;margin-top:2px}.deep{border:1px solid #263748;background:#111923;border-radius:12px;padding:9px;margin-top:6px}.post{font-size:15px;font-weight:1000;color:#fff}.conf{float:right;font-size:8px;color:#93c5fd}.err{color:#fca5a5;font-size:8px;margin-top:6px}.vals{display:grid;grid-template-columns:repeat(3,1fr);gap:5px}.val{border:1px solid #202a35;border-radius:10px;padding:7px;text-align:center;color:#718094;font-size:7px}.val b{display:block;color:#fff;font-size:9px}.hidden{display:none}</style><div class="p"><div class="h"><div class="logo">N</div><div><div class="brand">NEO Meme Coins</div><div class="sub">Sentinel v0.8.1 · resilient feed intelligence</div></div><span class="tag">${sourceName()}</span><button class="min">−</button></div><div class="body"></div></div>`;
  shadow.querySelector('.min').onclick=()=>{collapsed=!collapsed;shadow.querySelector('.body').classList.toggle('hidden',collapsed);shadow.querySelector('.min').textContent=collapsed?'+':'−';};

  function render(){
    const body=shadow.querySelector('.body');if(!body)return;
    const dbg=diagnostics.debug||{};const rej=dbg.rejected||{};
    const radar=marketRows.filter(x=>!x.error).slice(0,5).map(x=>{const soc=socialState.get(`${x.chainId}:${x.tokenAddress}`);return `<div class="row"><div class="top"><div><div class="name">$${esc(x.symbol||'?')} · ${esc(x.name||'')}</div><div class="muted">${chainName(x.chainId)} · ${short(x.tokenAddress)}${x.dexBacked===false?' · FEED ONLY':''}</div></div><span class="status">${esc(x.status||'WATCH')}</span></div><div class="grid"><div class="m">PRIORITY<b>${Math.round(n(x.priority))}/100</b></div><div class="m">RISK<b>${Math.round(n(x.risk))}/100</b></div><div class="m">5M<b>${n(x.price5m)>=0?'+':''}${n(x.price5m).toFixed(1)}%</b></div><div class="m">LIQ<b>${money(x.liquidityUsd)}</b></div></div>${soc?`<div class="diag">Social ${Math.round(n(soc.score))}/100</div>`:''}</div>`;}).join('');
    const deep=deepResults.slice(0,3).map(r=>`<div class="deep"><span class="conf">CONF ${Math.round(n(r.confidence))}%</span><div class="post">${esc(r.posture||'WATCH')}</div><div class="muted">${esc(r.market?.symbol||'')} · ${chainName(r.chainId)} · risk ${Math.round(n(r.risk))}/100 · outlook ${Math.round(n(r.outlook?.score))}/100</div></div>`).join('');
    body.innerHTML=`<div class="live"><span>● SENTINEL ACTIVE</span><span>${diagnostics.resolved} resolved · ${sourceName()}</span></div><div class="card"><b style="color:#93c5fd">⚡ REALTIME DISCOVERY + MARKET</b><div class="diag">Feed ${diagnostics.feed} · structured ${diagnostics.feedStructured} · DOM addresses ${diagnostics.addresses} · labels ${diagnostics.labels} · resolved ${diagnostics.resolved}</div><div class="diag">DEX-backed ${n(dbg.dexBacked)} · feed-only ${n(dbg.terminalOnly)} · rejected F/A/L ${n(rej.feed)}/${n(rej.address)}/${n(rej.label)}</div>${diagnostics.lastError?`<div class="err">${esc(diagnostics.lastError)}</div>`:''}</div><div class="ey">LIVE RADAR · TOP CANDIDATES</div>${radar||'<div class="card muted">NEO слуша network/WebSocket feed-а и DOM-а. Чакам валидиран market candidate.</div>'}<div class="ey">AUTO DEEP ANALYSIS</div>${deep||'<div class="card muted">Deep analysis ще тръгне автоматично веднага щом има валидиран candidate.</div>'}<div class="ey">LOCAL VALIDATION</div><div class="vals"><div class="val">30 SEC<b>${valSummary('s30')}</b></div><div class="val">2 MIN<b>${valSummary('m2')}</b></div><div class="val">15 MIN<b>${valSummary('m15')}</b></div></div>`;
  }

  chrome.runtime.onMessage.addListener((msg,_sender,sendResponse)=>{
    if(msg?.type==='NEO_STATUS'){const top=marketRows[0]||null,deep=deepResults[0]||null;sendResponse({ok:true,source:sourceName(),observedCount:resolved.length,radarScanning:marketBusy,deepScanning:deepBusy,queue:0,radarTop:top?[top]:[],result:deep?{posture:deep.posture,risk:deep.risk,confidence:deep.confidence,symbol:deep.market?.symbol,name:deep.market?.name}:null,validation:{m15:{total:validation.filter(x=>x.m15).length,correct:validation.filter(x=>x.m15?.correct).length}},diagnostics:{...diagnostics}});return false;}
    if(msg?.type==='NEO_RESCAN'){discover(true);sendResponse({ok:true});return false;}return false;
  });

  loadValidation().finally(()=>{render();setTimeout(()=>discover(true),350);setInterval(()=>discover(false),DISCOVERY_MS);setInterval(()=>marketPulse(),MARKET_MS);setInterval(()=>socialPulse(),5000);});
})();