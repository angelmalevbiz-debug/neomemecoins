import { analyze as analyzeSolana } from './engine-v2.js';

const SUPPORTED = new Set(['solana','robinhood','base','ethereum','bsc','monad']);
const CACHE_MS = 12000;
const searchCache = new Map();
const marketCache = new Map();
const n = (v) => Number.isFinite(Number(v)) ? Number(v) : 0;
const clamp = (v,min=0,max=100) => Math.max(min,Math.min(max,v));
const norm = (v) => String(v||'').replace(/^\$/,'').trim().toLowerCase();
const addrEq = (a,b) => String(a||'').toLowerCase() === String(b||'').toLowerCase();

function chainLabel(id){
  return ({solana:'Solana',robinhood:'Robinhood Chain',base:'Base',ethereum:'Ethereum',bsc:'BNB Chain',monad:'Monad'})[id] || id || 'Unknown';
}
function supportedPair(p){ return p?.baseToken?.address && SUPPORTED.has(String(p.chainId||'').toLowerCase()); }
function pairScore(p){ return n(p?.liquidity?.usd) + Math.min(n(p?.volume?.h1),1e6)*0.08; }
function pairToMarket(p){
  return {
    chainId:String(p?.chainId||'').toLowerCase(), chainLabel:chainLabel(String(p?.chainId||'').toLowerCase()),
    tokenAddress:String(p?.baseToken?.address||''), pairAddress:String(p?.pairAddress||''),
    symbol:String(p?.baseToken?.symbol||''), name:String(p?.baseToken?.name||''), priceUsd:n(p?.priceUsd),
    marketCapUsd:n(p?.marketCap||p?.fdv), liquidityUsd:n(p?.liquidity?.usd),
    volume5mUsd:n(p?.volume?.m5), volume1hUsd:n(p?.volume?.h1), volume24hUsd:n(p?.volume?.h24),
    buys5m:n(p?.txns?.m5?.buys), sells5m:n(p?.txns?.m5?.sells), buys1h:n(p?.txns?.h1?.buys), sells1h:n(p?.txns?.h1?.sells),
    priceChange5m:n(p?.priceChange?.m5), priceChange1h:n(p?.priceChange?.h1), priceChange24h:n(p?.priceChange?.h24),
    pairCreatedAt:n(p?.pairCreatedAt),
    socialLinks:(p?.info?.socials||[]).map(x=>String(x?.url||'')).filter(Boolean),
    websites:(p?.info?.websites||[]).map(x=>String(x?.url||'')).filter(Boolean),
  };
}

async function dexSearch(q){
  const key=`q:${norm(q)}`; const c=searchCache.get(key); if(c && Date.now()-c.at<CACHE_MS) return c.value;
  const r=await fetch(`https://api.dexscreener.com/latest/dex/search?q=${encodeURIComponent(q)}`);
  if(!r.ok) throw new Error(`DEX search ${r.status}`);
  const j=await r.json(); const pairs=(Array.isArray(j?.pairs)?j.pairs:[]).filter(supportedPair);
  searchCache.set(key,{at:Date.now(),value:pairs}); return pairs;
}
async function pairByAddress(address, chainHint=''){
  const key=`a:${String(address).toLowerCase()}:${chainHint}`; const c=marketCache.get(key); if(c && Date.now()-c.at<CACHE_MS) return c.value;
  let pairs=[];
  if(chainHint && SUPPORTED.has(chainHint)){
    try{
      const r=await fetch(`https://api.dexscreener.com/token-pairs/v1/${encodeURIComponent(chainHint)}/${encodeURIComponent(address)}`);
      if(r.ok){ const j=await r.json(); pairs=(Array.isArray(j)?j:Array.isArray(j?.pairs)?j.pairs:[]).filter(supportedPair); }
    }catch{}
  }
  if(!pairs.length) pairs=await dexSearch(address);
  const exact=pairs.filter(p=>addrEq(p?.baseToken?.address,address)).sort((a,b)=>pairScore(b)-pairScore(a));
  const best=exact[0]||null; marketCache.set(key,{at:Date.now(),value:best}); return best;
}
function closeScore(obs,act,pts){
  const a=n(obs),b=n(act); if(!(a>0)||!(b>0)) return 0; const d=Math.abs(a-b)/Math.max(a,b);
  if(d<=.06)return pts;if(d<=.15)return Math.round(pts*.85);if(d<=.3)return Math.round(pts*.55);if(d<=.5)return Math.round(pts*.2);return -Math.round(pts*.55);
}
function inferChain(text=''){
  const s=String(text).toLowerCase();
  if(/robinhood/.test(s))return 'robinhood'; if(/solana|\bsol\b/.test(s))return 'solana'; if(/bnb|bsc|binance/.test(s))return 'bsc';
  if(/ethereum|\beth\b/.test(s))return 'ethereum'; if(/\bbase\b/.test(s))return 'base'; if(/monad/.test(s))return 'monad'; return '';
}
async function resolveItem(item){
  if(item?.address){
    const hint=item.chainHint||inferChain(item.context); const p=await pairByAddress(item.address,hint); if(!p)return null;
    const m=pairToMarket(p); return {...m,token:m.tokenAddress,resolutionConfidence:100,resolutionScore:100,source:'direct-address'};
  }
  const q=String(item?.query||'').replace(/^\$/,'').trim(); if(q.length<2||q.length>64)return null;
  const pairs=await dexSearch(q); const qn=norm(q); const hint=item.chainHint||inferChain(item.context);
  const scored=pairs.map(p=>{
    const sym=norm(p?.baseToken?.symbol), name=norm(p?.baseToken?.name); const exactS=sym===qn, exactN=name===qn;
    if(!exactS&&!exactN&&!name.startsWith(qn)&&!qn.startsWith(name))return null;
    const m=pairToMarket(p); let score=exactS?52:exactN?46:28;
    score+=closeScore(item?.marketCapUsd,m.marketCapUsd,34)+closeScore(item?.priceUsd,m.priceUsd,18);
    if(hint && m.chainId===hint)score+=18; if(m.liquidityUsd>=5000)score+=4;if(m.liquidityUsd>=25000)score+=4;if(m.volume1hUsd>0)score+=2;
    return {m,score};
  }).filter(Boolean).sort((a,b)=>b.score-a.score||b.m.liquidityUsd-a.m.liquidityUsd);
  if(!scored.length)return null; const best=scored[0], second=scored[1]; const margin=second?best.score-second.score:99;
  const anchored=n(item?.marketCapUsd)>0||n(item?.priceUsd)>0;
  if(best.score<(anchored?52:62) || (!anchored&&margin<8) || (anchored&&margin<3))return null;
  return {...best.m,token:best.m.tokenAddress,query:q,resolutionScore:Math.round(best.score),resolutionMargin:Math.round(margin),resolutionConfidence:clamp(Math.round(best.score*.72+Math.min(20,margin*1.4))),source:'label-resolve'};
}
async function discover(payload){
  const items=[];
  for(const a of payload?.addresses||[]) items.push({address:a.address||a,chainHint:a.chainHint||'',context:a.context||''});
  for(const i of payload?.items||[]) items.push(i);
  const out=[]; let cursor=0;
  async function worker(){ while(cursor<items.length){ const i=cursor++; try{const r=await resolveItem(items[i]);if(r)out.push(r);}catch{} } }
  await Promise.all(Array.from({length:Math.min(5,items.length||1)},worker));
  const by=new Map(); for(const r of out){const k=`${r.chainId}:${r.tokenAddress.toLowerCase()}`;const cur=by.get(k);if(!cur||r.resolutionConfidence>cur.resolutionConfidence)by.set(k,r);}
  return [...by.values()].sort((a,b)=>b.resolutionConfidence-a.resolutionConfidence).slice(0,20);
}

async function marketOne(item){
  const p=await pairByAddress(item.tokenAddress||item.token,item.chainId||''); if(!p)throw new Error('No supported pair');
  const m=pairToMarket(p), tx5=m.buys5m+m.sells5m, tx1=m.buys1h+m.sells1h;
  const buy5=tx5?m.buys5m/tx5:.5, buy1=tx1?m.buys1h/tx1:.5, liqRatio=m.marketCapUsd>0?m.liquidityUsd/m.marketCapUsd*100:0;
  let risk=12, priority=38; const reasons=[],positives=[];
  if(m.liquidityUsd<5000){risk+=30;reasons.push('very thin liquidity');}else if(m.liquidityUsd<15000){risk+=16;reasons.push('low liquidity');}else{priority+=8;if(m.liquidityUsd>=50000){risk-=3;positives.push('meaningful liquidity');}}
  if(m.marketCapUsd>0){if(liqRatio<2){risk+=26;reasons.push('liquidity tiny vs cap');}else if(liqRatio<5){risk+=15;reasons.push('weak liquidity/cap');}else if(liqRatio>=8&&liqRatio<=55){risk-=3;priority+=8;positives.push('supportive liquidity/cap');}}
  if(tx5>=20)priority+=10;else if(tx5>=8)priority+=5; if(buy5>=.54&&buy5<=.8)priority+=10;else if(buy5>.93||buy5<.2){risk+=8;priority-=7;reasons.push('extreme 5m flow');}
  if(m.priceChange5m>=1&&m.priceChange5m<=35)priority+=7;if(m.priceChange5m<-15||m.priceChange5m>100){risk+=8;priority-=8;reasons.push('extreme 5m move');}
  if(m.socialLinks.length+m.websites.length>=2){priority+=5;positives.push('public social/project links');}
  risk=clamp(Math.round(risk),5,100); priority=clamp(Math.round(priority-Math.max(0,risk-30)*.35));
  let status='IGNORE'; if(risk>=72)status='HIGH RISK';else if(priority>=68&&risk<=52)status='DEEP CHECK';else if(priority>=54&&risk<=62)status='WATCH';else if(risk>=48)status='CAUTION';
  return {...m,priority,risk,status,liquidityRatio:liqRatio,tx5m:tx5,tx1h:tx1,buyShare5m:buy5*100,buyShare1h:buy1*100,price5m:m.priceChange5m,price1h:m.priceChange1h,reasons:reasons.slice(0,4),positives:positives.slice(0,4),observedAt:Date.now()};
}
async function marketMany(items){
  const out=[]; let cursor=0; const q=(items||[]).slice(0,12);
  async function worker(){while(cursor<q.length){const i=cursor++;try{out.push(await marketOne(q[i]));}catch(e){out.push({tokenAddress:q[i]?.tokenAddress||q[i]?.token||'',chainId:q[i]?.chainId||'',error:String(e),priority:0,risk:100,status:'UNAVAILABLE'});}}}
  await Promise.all(Array.from({length:Math.min(5,q.length||1)},worker)); return out.sort((a,b)=>(b.priority||0)-(a.priority||0));
}

function genericDeepScore(m){
  const tx5=m.buys5m+m.sells5m, buy5=tx5?m.buys5m/tx5:.5, liqRatio=m.marketCapUsd>0?m.liquidityUsd/m.marketCapUsd*100:0;
  let risk=15, confidence=48; const signals=[]; const add=(label,detail,points,severity='info')=>{risk+=points;signals.push({label,detail,points,severity});};
  if(m.liquidityUsd<5000)add('Very thin liquidity',`$${Math.round(m.liquidityUsd)} visible liquidity`,28,'critical');else if(m.liquidityUsd<15000)add('Low liquidity',`$${Math.round(m.liquidityUsd)} visible liquidity`,15,'warning');else add('Visible liquidity','Liquidity is above the basic thin-liquidity threshold.',-3,'positive');
  if(liqRatio>0&&liqRatio<2)add('Liquidity tiny vs market cap',`${liqRatio.toFixed(2)}%`,24,'critical');else if(liqRatio>0&&liqRatio<5)add('Weak liquidity ratio',`${liqRatio.toFixed(2)}%`,14,'warning');
  if(tx5>8&&(buy5>.95||buy5<.05))add('Extreme 5m flow imbalance',`${Math.round(buy5*100)}% buys`,10,'warning');
  if(Math.abs(m.priceChange5m)>80)add('Extreme 5m displacement',`${m.priceChange5m.toFixed(1)}%`,10,'warning');
  const links=m.socialLinks.length+m.websites.length;if(!links)add('No public project links','No public social/site links were returned in market metadata.',6,'warning');else{confidence+=Math.min(8,links*2);signals.push({label:'Public footprint',detail:`${links} public link(s) found.`,points:0,severity:'info'});}
  signals.push({label:'Chain-aware limitation',detail:`${chainLabel(m.chainId)} holder/funding forensics are not yet indexer-backed; NEO will not treat missing evidence as safety.`,points:0,severity:'warning'});
  risk=clamp(Math.round(risk)); confidence=Math.min(62,clamp(Math.round(confidence)));
  let posture=risk>=65?'SKIP':risk>=45?'WAIT':'WATCH';
  let outlook=50+clamp(m.priceChange5m,-20,20)*.7+(buy5-.5)*40-Math.max(0,risk-35)*.4; outlook=clamp(Math.round(outlook));
  return {risk,posture,confidence,liquidityRatio:liqRatio,signals,outlook:{score:outlook,label:outlook>=65?'POSITIVE MOMENTUM':outlook<=35?'DOWNSIDE RISK':'MIXED',horizon:'seconds–minutes evidence window',confidence:Math.min(58,confidence),bull:[],bear:[],note:'Market/social evidence only for this chain; no guaranteed price prediction.'},social:{links:[...m.socialLinks.map(url=>({url,platform:'Social'})),...m.websites.map(url=>({url,platform:'Website'}))],count:links},narrative:{category:'unknown',confidence:10,matched:[]},bundleRisk:null,flowRisk:null};
}
async function deepAnalyze(item,pageContext={}){
  const p=await pairByAddress(item.tokenAddress||item.token,item.chainId||''); if(!p)throw new Error('No supported market pair'); const m=pairToMarket(p);
  if(m.chainId==='solana'){
    const r=await analyzeSolana(m.tokenAddress,pageContext); return {...r,chainId:'solana',chainLabel:'Solana'};
  }
  return {tokenAddress:m.tokenAddress,market:m,holders:null,holderError:`${chainLabel(m.chainId)} holder/funding indexer unavailable in free device-only mode.`,mintControls:null,mintError:'Non-Solana token controls use different chain primitives.',generatedAt:Date.now(),chainId:m.chainId,chainLabel:m.chainLabel,...genericDeepScore(m)};
}

chrome.runtime.onMessage.addListener((msg,_sender,sendResponse)=>{
  if(msg?.type==='NEO7_DISCOVER'){discover(msg).then(results=>sendResponse({ok:true,results})).catch(e=>sendResponse({ok:false,error:String(e)}));return true;}
  if(msg?.type==='NEO7_MARKET'){marketMany(msg.items||[]).then(rows=>sendResponse({ok:true,rows})).catch(e=>sendResponse({ok:false,error:String(e)}));return true;}
  if(msg?.type==='NEO7_DEEP'){deepAnalyze(msg.item||{},msg.pageContext||{}).then(result=>sendResponse({ok:true,result})).catch(e=>sendResponse({ok:false,error:String(e)}));return true;}
  return false;
});
