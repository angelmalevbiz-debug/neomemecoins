import { analyze as analyzeSolana } from './engine-v2.js';

const SEARCH_CACHE_MS = 8_000;
const searchCache = new Map();
const n = (v) => Number.isFinite(Number(v)) ? Number(v) : 0;
const clamp = (v,min=0,max=100) => Math.max(min,Math.min(max,v));
const norm = (v) => String(v||'').replace(/^\$/,'').trim().toLowerCase();
const addrEq = (a,b) => String(a||'').toLowerCase() === String(b||'').toLowerCase();

function normalizeChain(raw){
  const s=String(raw||'').toLowerCase();
  if(/solana|\bsol\b/.test(s))return 'solana';
  if(/robinhood|rhc/.test(s))return 'robinhood';
  if(/\bbase\b/.test(s))return 'base';
  if(/ethereum|\beth\b/.test(s))return 'ethereum';
  if(/bnb|bsc|binance/.test(s))return 'bsc';
  if(/monad/.test(s))return 'monad';
  return s.replace(/[^a-z0-9_-]/g,'').slice(0,32);
}
function chainLabel(id){return ({solana:'Solana',robinhood:'Robinhood Chain',base:'Base',ethereum:'Ethereum',bsc:'BNB Chain',monad:'Monad'})[id]||id||'Unknown';}
function pairScore(p){return n(p?.liquidity?.usd)+Math.min(n(p?.volume?.h1),2e6)*.1+Math.min(n(p?.txns?.h1?.buys)+n(p?.txns?.h1?.sells),1500)*120;}
function pairToMarket(p){
  const raw=String(p?.chainId||'').toLowerCase(),chainId=normalizeChain(raw)||raw||'unknown';
  return {chainId,rawChainId:raw,chainLabel:chainLabel(chainId),tokenAddress:String(p?.baseToken?.address||''),pairAddress:String(p?.pairAddress||''),symbol:String(p?.baseToken?.symbol||''),name:String(p?.baseToken?.name||''),priceUsd:n(p?.priceUsd),marketCapUsd:n(p?.marketCap||p?.fdv),liquidityUsd:n(p?.liquidity?.usd),volume5mUsd:n(p?.volume?.m5),volume1hUsd:n(p?.volume?.h1),volume24hUsd:n(p?.volume?.h24),buys5m:n(p?.txns?.m5?.buys),sells5m:n(p?.txns?.m5?.sells),buys1h:n(p?.txns?.h1?.buys),sells1h:n(p?.txns?.h1?.sells),priceChange5m:n(p?.priceChange?.m5),priceChange1h:n(p?.priceChange?.h1),priceChange24h:n(p?.priceChange?.h24),pairCreatedAt:n(p?.pairCreatedAt),socialLinks:(p?.info?.socials||[]).map(x=>String(x?.url||'')).filter(Boolean),websites:(p?.info?.websites||[]).map(x=>String(x?.url||'')).filter(Boolean),dexBacked:true};
}
async function dexSearch(q){
  const key=norm(q);const c=searchCache.get(key);if(c&&Date.now()-c.at<SEARCH_CACHE_MS)return c.value;
  const r=await fetch(`https://api.dexscreener.com/latest/dex/search?q=${encodeURIComponent(q)}`);if(!r.ok)throw new Error(`DEX ${r.status}`);
  const j=await r.json();const pairs=(Array.isArray(j?.pairs)?j.pairs:[]).filter(p=>p?.baseToken?.address);searchCache.set(key,{at:Date.now(),value:pairs});return pairs;
}
async function pairByAddress(address){const pairs=await dexSearch(address);return pairs.filter(p=>addrEq(p?.baseToken?.address,address)).sort((a,b)=>pairScore(b)-pairScore(a))[0]||null;}
function closeScore(obs,actual,pts){const a=n(obs),b=n(actual);if(!(a>0)||!(b>0))return 0;const d=Math.abs(a-b)/Math.max(a,b);if(d<=.05)return pts;if(d<=.12)return Math.round(pts*.88);if(d<=.25)return Math.round(pts*.62);if(d<=.45)return Math.round(pts*.28);if(d<=.75)return -Math.round(pts*.18);return -Math.round(pts*.6);}

function terminalFallback(c){
  const address=String(c?.address||'').trim();if(!address)return null;
  const hasIdentity=Boolean(c?.symbol||c?.name);const hasMarket=n(c?.priceUsd)>0||n(c?.marketCapUsd)>0||n(c?.liquidityUsd)>0||n(c?.volume5mUsd)>0;
  const quality=n(c?.quality);if(!hasIdentity || (!hasMarket && quality<7))return null;
  const chainId=normalizeChain(c?.chainHint)||'unknown';
  const conf=clamp(42+Math.min(28,quality*4)+(hasMarket?15:0)+(chainId!=='unknown'?8:0),35,88);
  return {token:address,tokenAddress:address,chainId,rawChainId:chainId,chainLabel:chainLabel(chainId),symbol:String(c?.symbol||'').slice(0,40),name:String(c?.name||'').slice(0,100),priceUsd:n(c?.priceUsd),marketCapUsd:n(c?.marketCapUsd),liquidityUsd:n(c?.liquidityUsd),volume5mUsd:n(c?.volume5mUsd),volume1hUsd:n(c?.volume1hUsd),volume24hUsd:0,buys5m:n(c?.buys5m),sells5m:n(c?.sells5m),buys1h:0,sells1h:0,priceChange5m:0,priceChange1h:0,priceChange24h:0,pairCreatedAt:0,socialLinks:[],websites:[],dexBacked:false,source:'terminal-feed',resolutionConfidence:conf,resolutionScore:conf,context:String(c?.context||'').slice(0,1000),feedAt:n(c?.at),feedQuality:quality};
}
async function resolveFeed(c){
  const address=String(c?.address||'').trim();if(!address)return null;
  try{const p=await pairByAddress(address);if(p){const m=pairToMarket(p);return {...m,token:m.tokenAddress,source:'terminal-feed+dex',resolutionConfidence:100,resolutionScore:100,context:String(c?.context||'').slice(0,1000),feedQuality:n(c?.quality)};}}catch{}
  return terminalFallback(c);
}
async function resolveAddress(c){const address=String(c?.address||c||'').trim();if(!address)return null;try{const p=await pairByAddress(address);if(!p)return null;const m=pairToMarket(p);return {...m,token:m.tokenAddress,source:'direct-address',resolutionConfidence:100,resolutionScore:100};}catch{return null;}}
async function resolveLabel(item){
  const q=String(item?.query||'').replace(/^\$/,'').trim();if(q.length<2||q.length>64)return null;
  let pairs=[];try{pairs=await dexSearch(q);}catch{return null;}const qn=norm(q),hint=normalizeChain(item?.chainHint||item?.context||'');
  const scored=pairs.map(p=>{const sym=norm(p?.baseToken?.symbol),name=norm(p?.baseToken?.name),exactS=sym===qn,exactN=name===qn;const compact=qn.replace(/[^a-z0-9]/g,''),cs=sym.replace(/[^a-z0-9]/g,''),cn=name.replace(/[^a-z0-9]/g,'');const fuzzy=compact.length>=3&&(cs===compact||cn===compact||cn.startsWith(compact)||compact.startsWith(cn));if(!exactS&&!exactN&&!fuzzy)return null;const m=pairToMarket(p);let score=exactS?62:exactN?56:34;score+=closeScore(item?.marketCapUsd,m.marketCapUsd,46)+closeScore(item?.priceUsd,m.priceUsd,26);if(hint&&(m.chainId===hint||m.rawChainId.includes(hint)))score+=20;if(m.liquidityUsd>=5e3)score+=4;if(m.liquidityUsd>=25e3)score+=5;if(m.volume1hUsd>0)score+=3;return {m,score};}).filter(Boolean).sort((a,b)=>b.score-a.score||pairScore(b.m)-pairScore(a.m));
  if(!scored.length)return null;const best=scored[0],second=scored[1],margin=second?best.score-second.score:99;const anchored=n(item?.marketCapUsd)>0||n(item?.priceUsd)>0||Boolean(hint);const exact=norm(best.m.symbol)===qn||norm(best.m.name)===qn;const strong=best.m.liquidityUsd>=12e3||best.m.volume1hUsd>=8e3;
  if(anchored){if(best.score<46||margin<0)return null;}else{if(!exact||!strong||best.score<60)return null;if(second&&margin<2&&pairScore(best.m)<pairScore(second.m)*1.6)return null;}
  const conf=clamp(Math.round(best.score*.78+Math.min(18,Math.max(0,margin)*1.2)),25,100);return {...best.m,token:best.m.tokenAddress,query:q,source:'label-resolve',resolutionScore:Math.round(best.score),resolutionMargin:Math.round(margin),resolutionConfidence:conf};
}

async function discover(payload){
  const feed=(payload?.feedCandidates||[]).slice(0,120).sort((a,b)=>n(b.quality)-n(a.quality)||n(b.at)-n(a.at));
  const addresses=(payload?.addresses||[]).slice(0,48),items=(payload?.items||[]).slice(0,40);
  const jobs=[...feed.map(value=>({type:'feed',value})),...addresses.map(value=>({type:'address',value})),...items.map(value=>({type:'label',value}))];
  const out=[];let cursor=0,rejected={feed:0,address:0,label:0};
  async function worker(){while(cursor<jobs.length){const job=jobs[cursor++];try{const r=job.type==='feed'?await resolveFeed(job.value):job.type==='address'?await resolveAddress(job.value):await resolveLabel(job.value);if(r)out.push(r);else rejected[job.type]+=1;}catch{rejected[job.type]+=1;}}}
  await Promise.all(Array.from({length:Math.min(8,jobs.length||1)},worker));
  const by=new Map();for(const r of out){const key=`${r.chainId}:${String(r.tokenAddress||r.token).toLowerCase()}`;const cur=by.get(key);if(!cur||n(r.resolutionConfidence)>n(cur.resolutionConfidence)||(r.dexBacked&&!cur.dexBacked))by.set(key,r);}
  const results=[...by.values()].sort((a,b)=>n(b.resolutionConfidence)-n(a.resolutionConfidence)||n(b.feedQuality)-n(a.feedQuality)||n(b.liquidityUsd)-n(a.liquidityUsd)).slice(0,24);
  return {results,debug:{feedIn:feed.length,addressIn:addresses.length,labelIn:items.length,rejected,terminalOnly:results.filter(x=>!x.dexBacked).length,dexBacked:results.filter(x=>x.dexBacked).length}};
}

function scoreMarket(m){
  const tx5=n(m.buys5m)+n(m.sells5m),buy5=tx5?n(m.buys5m)/tx5:.5,liqRatio=n(m.marketCapUsd)>0?n(m.liquidityUsd)/n(m.marketCapUsd)*100:0;
  let risk=m.dexBacked===false?26:12,priority=34;const reasons=[],positives=[];
  if(n(m.liquidityUsd)>0&&n(m.liquidityUsd)<5000){risk+=30;reasons.push('very thin liquidity');}else if(n(m.liquidityUsd)>0&&n(m.liquidityUsd)<15000){risk+=16;reasons.push('low liquidity');}else if(n(m.liquidityUsd)>=50000){risk-=3;priority+=8;positives.push('meaningful liquidity');}
  if(n(m.marketCapUsd)>0&&n(m.liquidityUsd)>0){if(liqRatio<2){risk+=26;reasons.push('liquidity tiny vs cap');}else if(liqRatio<5){risk+=15;reasons.push('weak liquidity/cap');}else if(liqRatio>=8&&liqRatio<=60){risk-=3;priority+=8;positives.push('supportive liquidity/cap');}}
  if(tx5>=20)priority+=10;else if(tx5>=8)priority+=5;if(tx5>0&&buy5>=.54&&buy5<=.8)priority+=10;else if(tx5>8&&(buy5>.94||buy5<.18)){risk+=8;priority-=7;reasons.push('extreme 5m flow');}
  if(n(m.priceChange5m)>=1&&n(m.priceChange5m)<=35)priority+=7;if(n(m.priceChange5m)<-15||n(m.priceChange5m)>100){risk+=8;priority-=8;reasons.push('extreme 5m move');}
  const links=(m.socialLinks||[]).length+(m.websites||[]).length;if(links>=2){priority+=5;positives.push('public social/project links');}
  if(m.dexBacked===false){reasons.push('terminal-feed market not independently DEX-confirmed');priority+=Math.min(10,Math.max(0,n(m.feedQuality)-4));}
  risk=clamp(Math.round(risk),5,100);priority=clamp(Math.round(priority-Math.max(0,risk-30)*.35));let status='IGNORE';if(risk>=72)status='HIGH RISK';else if(priority>=66&&risk<=54)status='DEEP CHECK';else if(priority>=48&&risk<=68)status='WATCH';else if(risk>=48)status='CAUTION';
  return {...m,priority,risk,status,liquidityRatio:liqRatio,tx5m:tx5,buyShare5m:buy5*100,price5m:n(m.priceChange5m),price1h:n(m.priceChange1h),reasons:reasons.slice(0,5),positives:positives.slice(0,5),observedAt:Date.now()};
}
async function marketOne(item){
  const fallback=item?.fallback||item||{};const address=String(item?.tokenAddress||item?.token||fallback?.tokenAddress||fallback?.token||'');
  try{const p=await pairByAddress(address);if(p)return scoreMarket({...pairToMarket(p),feedQuality:n(fallback?.feedQuality)});}catch{}
  const fb={...fallback,tokenAddress:address,token:address,dexBacked:false,chainId:normalizeChain(fallback?.chainId)||fallback?.chainId||'unknown',chainLabel:chainLabel(normalizeChain(fallback?.chainId)||fallback?.chainId||'unknown')};
  return scoreMarket(fb);
}
async function marketMany(items){const q=(items||[]).slice(0,16),out=[];let cursor=0;async function worker(){while(cursor<q.length){const i=cursor++;try{out.push(await marketOne(q[i]));}catch(e){out.push({tokenAddress:q[i]?.tokenAddress||'',chainId:q[i]?.chainId||'',error:String(e),priority:0,risk:100,status:'UNAVAILABLE'});}}}await Promise.all(Array.from({length:Math.min(6,q.length||1)},worker));return out.sort((a,b)=>n(b.priority)-n(a.priority)||n(a.risk)-n(b.risk));}

function genericDeep(m){
  const scored=scoreMarket(m),links=(m.socialLinks||[]).length+(m.websites||[]).length;let confidence=m.dexBacked===false?42:56;if(links)confidence+=Math.min(8,links*2);confidence=clamp(confidence,20,m.dexBacked===false?58:70);
  let posture=scored.risk>=68?'SKIP':scored.risk>=46?'WAIT':'WATCH';const outlook=clamp(Math.round(50+clamp(n(m.priceChange5m),-20,20)*.7+(scored.buyShare5m/100-.5)*40-Math.max(0,scored.risk-35)*.4));
  return {risk:scored.risk,posture,confidence,liquidityRatio:scored.liquidityRatio,signals:[...scored.reasons.map(x=>({label:x,detail:x,points:0,severity:'warning'})),...scored.positives.map(x=>({label:x,detail:x,points:0,severity:'positive'}))],outlook:{score:outlook,label:outlook>=65?'POSITIVE MOMENTUM':outlook<=35?'DOWNSIDE RISK':'MIXED',horizon:'seconds–minutes evidence window',confidence:Math.min(62,confidence),bull:scored.positives.slice(0,3),bear:scored.reasons.slice(0,3),note:'Current evidence only; not a guaranteed price prediction.'},social:{links:[...(m.socialLinks||[]).map(url=>({url,platform:'Social'})),...(m.websites||[]).map(url=>({url,platform:'Website'}))],count:links},narrative:{category:'unknown',confidence:10,matched:[]},bundleRisk:null,flowRisk:null};
}
async function deepAnalyze(item,pageContext={}){
  const fallback=item?.fallback||item||{};const market=await marketOne({tokenAddress:item?.tokenAddress||fallback?.tokenAddress,chainId:item?.chainId||fallback?.chainId,fallback});
  if(market.chainId==='solana'&&market.dexBacked!==false){try{const r=await analyzeSolana(market.tokenAddress,pageContext);return {...r,chainId:'solana',chainLabel:'Solana'};}catch{}}
  return {tokenAddress:market.tokenAddress,market,holders:null,holderError:market.chainId==='solana'?'Solana deep holder check unavailable in this cycle.':`${chainLabel(market.chainId)} holder/funding indexer is not available in free device-only mode.`,mintControls:null,mintError:'Chain-specific token controls unavailable.',generatedAt:Date.now(),chainId:market.chainId,chainLabel:chainLabel(market.chainId),...genericDeep(market)};
}

chrome.runtime.onMessage.addListener((msg,_sender,sendResponse)=>{
  if(msg?.type==='NEO81_DISCOVER'){discover(msg).then(({results,debug})=>sendResponse({ok:true,results,debug})).catch(e=>sendResponse({ok:false,error:String(e)}));return true;}
  if(msg?.type==='NEO81_MARKET'){marketMany(msg.items||[]).then(rows=>sendResponse({ok:true,rows})).catch(e=>sendResponse({ok:false,error:String(e)}));return true;}
  if(msg?.type==='NEO81_DEEP'){deepAnalyze(msg.item||{},msg.pageContext||{}).then(result=>sendResponse({ok:true,result})).catch(e=>sendResponse({ok:false,error:String(e)}));return true;}
  return false;
});