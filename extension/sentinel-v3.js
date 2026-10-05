(() => {
  if (window.__NEO_SENTINEL_V07__) return;
  window.__NEO_SENTINEL_V07__ = true;

  const DISCOVERY_MS = 6000;
  const MARKET_MS = 3000;
  const SOCIAL_MS = 45000;
  const DEEP_COOLDOWN_MS = 90000;
  const VALIDATION_KEY = 'neo-v07-validation';
  const BASE58_RE = /\b[1-9A-HJ-NP-Za-km-z]{32,44}\b/g;
  const EVM_RE = /\b0x[a-fA-F0-9]{40}\b/g;
  const STOP = new Set(['buy','sell','buys','sells','buyers','sellers','market','markets','price','volume','vol','liquidity','liq','mcap','market cap','time','token','tokens','trending','watchlist','crypto','most held','alerts','leaderboard','feed','home','portfolio','positions','orders','holders','top traders','dev tokens','follow','following','followers','share','deposit','withdraw','cash','chart','auto','manual','search','settings','trade','swap','live','new','all','fomo']);

  let resolved = [];
  let marketRows = [];
  let deepResults = [];
  let discoveryBusy = false;
  let marketBusy = false;
  let deepBusy = false;
  let socialBusy = false;
  let collapsed = false;
  let diagnostics = { addresses:0, labels:0, resolved:0, discoveryAt:0, marketAt:0, socialAt:0 };
  let validation = [];
  const deepCooldown = new Map();
  const socialState = new Map();

  const n = (v) => Number.isFinite(Number(v)) ? Number(v) : 0;
  const esc = (v) => String(v ?? '').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;').replaceAll("'",'&#039;');
  const clean = (v) => String(v||'').replace(/\s+/g,' ').trim();
  const short = (v,size=5) => { const s=String(v||''); return s.length>size*2+2?`${s.slice(0,size)}…${s.slice(-size)}`:s; };
  const money = (v) => { const x=n(v); if(x>=1e9)return `$${(x/1e9).toFixed(2)}B`;if(x>=1e6)return `$${(x/1e6).toFixed(2)}M`;if(x>=1e3)return `$${(x/1e3).toFixed(1)}K`;return `$${x.toFixed(x<1?5:0)}`; };
  const sourceName = () => location.hostname.includes('axiom')?'AXIOM':location.hostname.includes('tinyastro')?'PHOTON':'FOMO';
  const chainName = (id) => ({solana:'SOL',robinhood:'RHC',base:'BASE',ethereum:'ETH',bsc:'BNB',monad:'MONAD'})[id] || String(id||'?').toUpperCase();

  function parseMoney(raw){
    const m=String(raw||'').replace(/,/g,'').match(/([0-9]*\.?[0-9]+)\s*([KMB])?/i); if(!m)return 0;
    const x=Number(m[1])||0,u=String(m[2]||'').toUpperCase(); return x*(u==='B'?1e9:u==='M'?1e6:u==='K'?1e3:1);
  }
  function metricsFrom(text){
    const s=String(text||''); let marketCapUsd=0,priceUsd=0;
    for(const re of [/\$\s*([0-9.,]+\s*[KMB]?)\s*(?:MC|MCap)\b/i,/(?:MC|MCap|Market\s*Cap)\s*[:·-]?\s*\$?\s*([0-9.,]+\s*[KMB]?)/i]){
      const m=s.match(re); if(m){marketCapUsd=parseMoney(m[1]);if(marketCapUsd)break;}
    }
    for(const m of s.matchAll(/\$\s*(0?\.[0-9]+|[0-9][0-9.,]*(?:\.[0-9]+)?)/g)){
      const around=s.slice(Math.max(0,(m.index||0)-12),Math.min(s.length,(m.index||0)+m[0].length+12)); if(/MC|MCap|Market\s*Cap/i.test(around))continue;
      const x=Number(String(m[1]).replace(/,/g,'')); if(Number.isFinite(x)&&x>0&&(!priceUsd||x<priceUsd))priceUsd=x;
    }
    return {marketCapUsd,priceUsd};
  }
  function chainHint(text=''){
    const s=String(text).toLowerCase(); if(/robinhood/.test(s))return 'robinhood';if(/solana|\bsol\b/.test(s))return 'solana';if(/bnb|bsc|binance/.test(s))return 'bsc';if(/ethereum|\beth\b/.test(s))return 'ethereum';if(/\bbase\b/.test(s))return 'base';if(/monad/.test(s))return 'monad';return '';
  }
  function usableLabel(raw){
    const s=clean(raw).replace(/^\$/,'').trim(); if(s.length<2||s.length>42||STOP.has(s.toLowerCase()))return false;
    if(/^\$?[0-9.,]+(?:[KMB])?(?:\s*MC)?$/i.test(s)||/^[+\-▲▼]?\s*[0-9.,]+%$/.test(s)||/^(24h|7d|30d|1h|5m|h|d|w|m)$/i.test(s))return false;
    if(/^0x[a-f0-9]{40}$/i.test(s)||/^[1-9A-HJ-NP-Za-km-z]{32,44}$/.test(s)||/https?:\/\//i.test(s))return false;
    return /[A-Za-z\u0080-\uFFFF]/.test(s);
  }
  function visible(el){ if(!(el instanceof HTMLElement))return false;const r=el.getBoundingClientRect();return r.width>18&&r.height>8&&r.bottom>-120&&r.top<innerHeight+120; }

  function collectDiscovery(){
    const addressScores=new Map();
    const addAddr=(raw,points=1,context='')=>{
      const text=String(raw||'');
      for(const a of [...(text.match(BASE58_RE)||[]),...(text.match(EVM_RE)||[])]){
        const key=a.toLowerCase(); const cur=addressScores.get(key)||{address:a,score:0,context:''};cur.score+=points;if(context&&!cur.context)cur.context=clean(context).slice(0,400);addressScores.set(key,cur);
      }
    };
    addAddr(location.href,25,location.href);
    const attrNodes=[...document.querySelectorAll('a[href],[data-token-address],[data-mint],[data-address],[data-ca],[data-contract],[data-token],[data-coin-address]')].slice(0,7000);
    for(const node of attrNodes){
      if(node.closest?.('#neo-meme-coins-root'))continue; const txt=clean(node.closest?.('tr,[role="row"],li,article,[class*="card"],[class*="token"],[class*="coin"]')?.innerText||node.innerText||'');
      addAddr(node.getAttribute?.('href'),9,txt); for(const k of ['data-token-address','data-mint','data-address','data-ca','data-contract','data-token','data-coin-address'])addAddr(node.getAttribute?.(k),18,txt);
    }
    try{for(const e of performance.getEntriesByType('resource').slice(-1000))addAddr(e?.name,5,e?.name);}catch{}
    let budget=500000;for(const s of [...document.scripts].slice(0,80)){if(budget<=0)break;const t=String(s.textContent||'').slice(0,Math.min(80000,budget));budget-=t.length;if(/token|mint|contract|address|pair|chain/i.test(t))addAddr(t,3,'script-state');}
    addAddr(String(document.body?.innerText||'').slice(0,180000),2,'visible-body');

    const labels=new Map();
    const addLabel=(raw,context='')=>{
      const q=clean(raw).replace(/^\$/,'').trim();if(!usableLabel(q))return;const ctx=clean(context).slice(0,700);const m=metricsFrom(ctx);const key=`${q.toLowerCase()}|${Math.round(m.marketCapUsd/1000)}`;
      const row={query:q,context:ctx,...m,chainHint:chainHint(ctx)};const cur=labels.get(key);if(!cur||(row.marketCapUsd?1:0)+(row.priceUsd?1:0)>(cur.marketCapUsd?1:0)+(cur.priceUsd?1:0))labels.set(key,row);
    };
    for(const p of String(document.title||'').split(/[|·–—]/).map(clean).filter(Boolean))if(!/^fomo$/i.test(p))addLabel(p,document.title);
    const nodes=[...document.querySelectorAll('h1,h2,h3,[class*="symbol"],[class*="ticker"],[class*="token"],[class*="coin"],[class*="pair"],tr,[role="row"],li,a,button')].slice(0,8500);
    for(const node of nodes){
      if(!visible(node)||node.closest?.('#neo-meme-coins-root'))continue;const text=String(node.innerText||'');if(text.length<2||text.length>700)continue;
      const ctx=/\$|%|buyers|sellers|volume|liq|mcap|market cap|buy|sell|holder|trade/i.test(text)||/token|coin|trade|swap|pair|market/i.test(node.getAttribute?.('href')||'');
      if(!ctx&&text.trim().length>24)continue;const lines=text.split(/\n+/).map(clean).filter(Boolean);
      for(const line of lines.slice(0,7)){const sm=line.match(/^\$([A-Za-z0-9_.\-]{2,20})$/);if(sm){addLabel(sm[1],text);break;}if(usableLabel(line)&&line.length<=34){addLabel(line,text);break;}}
    }
    const addresses=[...addressScores.values()].sort((a,b)=>b.score-a.score).slice(0,28).map(x=>({...x,chainHint:chainHint(x.context)}));
    const items=[...labels.values()].slice(0,22);
    diagnostics.addresses=addresses.length;diagnostics.labels=items.length;
    return {addresses,items};
  }

  async function discover(force=false){
    if(discoveryBusy)return;discoveryBusy=true;
    try{
      const input=collectDiscovery();const r=await chrome.runtime.sendMessage({type:'NEO7_DISCOVER',...input});
      if(r?.ok&&Array.isArray(r.results)){resolved=r.results;diagnostics.resolved=resolved.length;diagnostics.discoveryAt=Date.now();render();if(force||!marketRows.length)marketPulse();}
    }catch{}finally{discoveryBusy=false;}
  }

  async function marketPulse(){
    if(marketBusy||!resolved.length){render();return;}marketBusy=true;
    try{
      const items=resolved.slice(0,12).map(x=>({tokenAddress:x.tokenAddress||x.token,chainId:x.chainId}));
      const r=await chrome.runtime.sendMessage({type:'NEO7_MARKET',items});if(r?.ok&&Array.isArray(r.rows)){marketRows=r.rows;diagnostics.marketAt=Date.now();updateValidation();render();autoDeep();}
    }catch{}finally{marketBusy=false;}
  }

  function candidateFor(row){return resolved.find(x=>String(x.tokenAddress||x.token).toLowerCase()===String(row.tokenAddress||'').toLowerCase()&&x.chainId===row.chainId)||row;}
  function autoDeep(){
    if(deepBusy)return;const pick=marketRows.filter(x=>!x.error&&x.priority>=52&&x.risk<=68).sort((a,b)=>b.priority-a.priority).find(x=>Date.now()-(deepCooldown.get(`${x.chainId}:${x.tokenAddress}`)||0)>DEEP_COOLDOWN_MS);
    if(pick)runDeep(pick);
  }
  async function runDeep(row){
    if(deepBusy)return;deepBusy=true;const key=`${row.chainId}:${row.tokenAddress}`;deepCooldown.set(key,Date.now());render();
    try{
      const item=candidateFor(row);const ctx={source:sourceName(),url:location.href,title:document.title,text:clean(document.body?.innerText||'').slice(0,25000),socialLinks:[...(row.socialLinks||[])],sourceMeta:{chainId:row.chainId}};
      const r=await chrome.runtime.sendMessage({type:'NEO7_DEEP',item:{tokenAddress:row.tokenAddress,chainId:row.chainId},pageContext:ctx});if(r?.ok&&r.result){deepResults=[r.result,...deepResults.filter(x=>!(x.tokenAddress===r.result.tokenAddress&&x.chainId===r.result.chainId))].slice(0,8);recordPrediction(r.result);render();}
    }catch{}finally{deepBusy=false;render();setTimeout(autoDeep,600);}
  }

  async function socialPulse(){
    if(socialBusy||!marketRows.length)return;const pool=marketRows.filter(x=>!x.error&&x.priority>=45).slice(0,4);if(!pool.length)return;socialBusy=true;
    try{
      const idx=Math.floor(Date.now()/SOCIAL_MS)%pool.length,item=pool[idx];const urls=[...(item.socialLinks||[]),...(item.websites||[])];
      const r=await chrome.runtime.sendMessage({type:'NEO_SOCIAL_SCAN',urls,tokenMeta:{tokenAddress:item.tokenAddress,symbol:item.symbol,name:item.name}});
      if(r?.ok&&r.result&&!r.result.busy){socialState.set(`${item.chainId}:${item.tokenAddress}`,r.result);diagnostics.socialAt=Date.now();render();}
    }catch{}finally{socialBusy=false;}
  }

  async function loadValidation(){try{const s=await chrome.storage.local.get([VALIDATION_KEY]);validation=Array.isArray(s[VALIDATION_KEY])?s[VALIDATION_KEY].slice(-300):[];}catch{validation=[];}}
  async function saveValidation(){try{await chrome.storage.local.set({[VALIDATION_KEY]:validation.slice(-300)});}catch{}}
  function recordPrediction(r){
    const p=n(r?.market?.priceUsd),score=n(r?.outlook?.score);if(!p||!r?.tokenAddress)return;const direction=score>=60?'UP':score<=40?'DOWN':'NEUTRAL';if(validation.some(x=>x.token===r.tokenAddress&&x.chainId===r.chainId&&Date.now()-x.at<60000))return;
    validation.push({token:r.tokenAddress,chainId:r.chainId,symbol:r.market?.symbol||'',at:Date.now(),entryPrice:p,direction,score,s30:null,m2:null,m15:null});saveValidation();
  }
  function updateValidation(){
    let changed=false;for(const v of validation){const row=marketRows.find(x=>x.tokenAddress===v.token&&x.chainId===v.chainId);if(!row?.priceUsd||!v.entryPrice)continue;const age=Date.now()-v.at,chg=(row.priceUsd-v.entryPrice)/v.entryPrice*100;
      const judge=()=>v.direction==='UP'?chg>=1:v.direction==='DOWN'?chg<=-1:Math.abs(chg)<3;
      if(age>=30000&&!v.s30){v.s30={changePct:chg,correct:judge()};changed=true;}if(age>=120000&&!v.m2){v.m2={changePct:chg,correct:judge()};changed=true;}if(age>=900000&&!v.m15){v.m15={changePct:chg,correct:judge()};changed=true;}}
    if(changed)saveValidation();
  }
  function valSummary(field){const a=validation.filter(x=>x[field]&&x.direction!=='NEUTRAL');const c=a.filter(x=>x[field].correct).length;return `${c}/${a.length}${a.length?` · ${Math.round(c/a.length*100)}%`:''}`;}

  const old=document.getElementById('neo-meme-coins-root');if(old)old.remove();
  const host=document.createElement('div');host.id='neo-meme-coins-root';Object.assign(host.style,{all:'initial',position:'fixed',zIndex:'2147483647',right:'12px',top:'66px'});document.documentElement.appendChild(host);const shadow=host.attachShadow({mode:'open'});
  shadow.innerHTML=`<style>*{box-sizing:border-box}.neo{width:400px;max-height:calc(100vh - 80px);overflow:hidden;border:1px solid rgba(255,255,255,.13);border-radius:18px;background:rgba(7,9,12,.985);color:#e8edf2;font-family:Inter,system-ui,sans-serif;box-shadow:0 20px 60px rgba(0,0,0,.55)}.head{display:flex;align-items:center;gap:9px;padding:11px 12px;border-bottom:1px solid rgba(255,255,255,.08)}.mark{width:32px;height:32px;border-radius:10px;background:#34d399;color:#06100c;display:grid;place-items:center;font-weight:1000}.brand{font-size:12px;font-weight:950;color:#fff}.sub{font-size:8px;color:#687487;margin-top:2px}.src{margin-left:auto;border:1px solid rgba(52,211,153,.25);border-radius:7px;padding:4px 6px;font-size:8px;font-weight:900;color:#6ee7b7}.collapse{border:0;background:transparent;color:#7c8797;font-size:16px;cursor:pointer}.body{padding:11px;max-height:calc(100vh - 135px);overflow:auto}.body::-webkit-scrollbar{width:5px}.body::-webkit-scrollbar-thumb{background:#29313c;border-radius:4px}.live{display:flex;justify-content:space-between;border:1px solid rgba(52,211,153,.18);border-radius:11px;padding:8px 9px;background:rgba(52,211,153,.04);font-size:8px;color:#6ee7b7;font-weight:900}.diag{margin-top:7px;border:1px solid rgba(96,165,250,.16);border-radius:11px;padding:8px;background:rgba(96,165,250,.035);font-size:8px;line-height:1.5;color:#8290a2}.label{margin:11px 2px 6px;font-size:7.5px;font-weight:950;letter-spacing:.14em;color:#657182}.list{display:grid;gap:6px}.card{border:1px solid rgba(255,255,255,.075);border-radius:11px;padding:8px;background:rgba(255,255,255,.02)}.top{border-color:rgba(52,211,153,.22)}.ch{display:flex;justify-content:space-between;gap:8px}.name{font-size:10px;font-weight:900;color:#fff}.meta{font-size:7.5px;color:#6c7889;margin-top:2px}.badge{font-size:7px;font-weight:900;border-radius:6px;padding:3px 5px;background:rgba(96,165,250,.1);color:#93c5fd}.metrics{display:grid;grid-template-columns:repeat(4,1fr);gap:4px;margin-top:6px}.metrics span{font-size:6.8px;color:#657183}.metrics b{display:block;color:#fff;font-size:8.5px;margin-top:2px}.reason{margin-top:5px;font-size:7.8px;color:#788596;line-height:1.4}.deep{border-color:rgba(96,165,250,.18);background:rgba(96,165,250,.035)}.decision{font-size:16px;font-weight:1000;color:#fff}.conf{font-size:7px;color:#93c5fd}.social{color:#c4b5fd}.val{display:grid;grid-template-columns:repeat(3,1fr);gap:5px}.val div{border:1px solid rgba(255,255,255,.07);border-radius:9px;padding:7px;text-align:center;font-size:7px;color:#657183}.val b{display:block;color:#fff;font-size:9px;margin-top:3px}.empty{border:1px solid rgba(255,255,255,.07);border-radius:11px;padding:9px;font-size:8.5px;line-height:1.5;color:#7d8999}.collapsed .body{display:none}.collapsed{width:220px}</style><section class="neo"><div class="head"><div class="mark">N</div><div><div class="brand">NEO Meme Coins</div><div class="sub">Sentinel v0.7 · multichain realtime intelligence</div></div><div class="src">${esc(sourceName())}</div><button class="collapse">−</button></div><div class="body"></div></section>`;
  const panel=shadow.querySelector('.neo'),body=shadow.querySelector('.body'),btn=shadow.querySelector('.collapse');btn.onclick=()=>{collapsed=!collapsed;panel.classList.toggle('collapsed',collapsed);btn.textContent=collapsed?'+':'−';};

  function render(){
    const age=diagnostics.marketAt?Math.round((Date.now()-diagnostics.marketAt)/1000):null;
    const radar=marketRows.filter(x=>!x.error).slice(0,6).map((x,i)=>{const s=socialState.get(`${x.chainId}:${x.tokenAddress}`);return `<div class="card ${i===0?'top':''}"><div class="ch"><div><div class="name">$${esc(x.symbol||'?')} · ${esc(x.name||'')}</div><div class="meta">${esc(chainName(x.chainId))} · ${esc(short(x.tokenAddress))}</div></div><span class="badge">${esc(x.status||'WATCH')}</span></div><div class="metrics"><span>PRIORITY<b>${esc(x.priority||0)}/100</b></span><span>RISK<b>${esc(x.risk||0)}/100</b></span><span>5M<b>${n(x.price5m)>=0?'+':''}${esc(n(x.price5m).toFixed(1))}%</b></span><span>LIQ<b>${esc(money(x.liquidityUsd))}</b></span></div><div class="reason">${esc(x.positives?.[0]||x.reasons?.[0]||'market flow active')}${s?` · <span class="social">social ${esc(s.score||0)}/100</span>`:''}</div></div>`;}).join('');
    const deep=deepResults.slice(0,4).map(r=>`<div class="card deep"><div class="ch"><div><div class="decision">${esc(r.posture||'WATCH')}</div><div class="meta">${esc(r.market?.symbol||'?')} · ${esc(chainName(r.chainId))} · ${esc(short(r.tokenAddress))}</div></div><div class="conf">CONF ${esc(r.confidence||0)}%</div></div><div class="metrics"><span>RISK<b>${esc(r.risk||0)}/100</b></span><span>OUTLOOK<b>${esc(r.outlook?.score??50)}/100</b></span><span>TOP 5<b>${r.holders?`${esc(n(r.holders.top5Pct).toFixed(1))}%`:'N/A'}</b></span><span>LIQ/CAP<b>${esc(n(r.liquidityRatio).toFixed(1))}%</b></span></div><div class="reason">${esc(r.signals?.[0]?.label||r.holderError||r.outlook?.label||'deep evidence')}</div></div>`).join('');
    body.innerHTML=`<div class="live"><span>● SENTINEL ACTIVE</span><span>${esc(resolved.length)} resolved · ${esc(sourceName())}</span></div><div class="diag"><b style="color:#fff">MULTICHAIN REALTIME</b><br>Discovery: ${diagnostics.addresses} addresses · ${diagnostics.labels} labels · ${diagnostics.resolved} resolved<br>Market: ~3s${age==null?' · starting':` · ${age}s ago`} · Social: ~45s · Deep: automatic<br>Chains: Solana · Robinhood · Base · Ethereum · BNB · Monad</div><div class="label">LIVE RADAR · TOP CANDIDATES</div><div class="list">${radar||'<div class="empty">Още няма валидиран market candidate. NEO продължава да чете token имена, contract addresses, DOM state и видимия Fomo/Axiom/Photon контекст.</div>'}</div><div class="label">AUTO DEEP ANALYSIS ${deepBusy?'· RUNNING':''}</div><div class="list">${deep||'<div class="empty">Deep анализът ще тръгне автоматично веднага щом има валидиран candidate.</div>'}</div><div class="label">LOCAL VALIDATION</div><div class="val"><div>30 SEC<b>${esc(valSummary('s30'))}</b></div><div>2 MIN<b>${esc(valSummary('m2'))}</b></div><div>15 MIN<b>${esc(valSummary('m15'))}</b></div></div>`;
  }

  chrome.runtime.onMessage.addListener((msg,_s,sendResponse)=>{
    if(msg?.type==='NEO_RESCAN'){discover(true);sendResponse({ok:true,source:sourceName(),observedCount:resolved.length,radarScanning:marketBusy,deepScanning:deepBusy});return false;}
    if(msg?.type==='NEO_STATUS'){const top=marketRows[0],d=deepResults[0];sendResponse({ok:true,source:sourceName(),observedCount:resolved.length,radarScanning:marketBusy,deepScanning:deepBusy,queue:0,radarTop:top?[top]:[],result:d?{posture:d.posture,risk:d.risk,confidence:d.confidence,symbol:d.market?.symbol,name:d.market?.name}:null,validation:{m15:{total:validation.filter(x=>x.m15&&x.direction!=='NEUTRAL').length,correct:validation.filter(x=>x.m15?.correct&&x.direction!=='NEUTRAL').length}}});return false;}
    return false;
  });

  loadValidation().finally(()=>{render();discover(true);setTimeout(marketPulse,1300);setTimeout(socialPulse,5000);});
  setInterval(()=>discover(false),DISCOVERY_MS);
  setInterval(marketPulse,MARKET_MS);
  setInterval(socialPulse,SOCIAL_MS);
  document.addEventListener('click',()=>setTimeout(()=>discover(false),250),true);
  new MutationObserver(()=>{ if(!discoveryBusy)setTimeout(()=>discover(false),700); }).observe(document.documentElement,{subtree:true,childList:true,attributes:true,attributeFilter:['href','data-token-address','data-mint','data-address','data-ca','data-contract','data-token','data-coin-address']});
})();
