const statusText = document.getElementById('statusText');
const terminalName = document.getElementById('terminalName');
const currentCoin = document.getElementById('currentCoin');
const rescanBtn = document.getElementById('rescanBtn');
const errorBox = document.getElementById('error');
const resultBox = document.getElementById('result');
const postureEl = document.getElementById('posture');
const tokenNameEl = document.getElementById('tokenName');
const riskEl = document.getElementById('risk');
const confidenceEl = document.getElementById('confidence');
const rpcInput = document.getElementById('rpcInput');
const saveRpcBtn = document.getElementById('saveRpcBtn');
let activeTabId = null;
let repairingConnection = false;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
function showError(m){ errorBox.textContent=m; errorBox.classList.remove('hidden'); }
function clearError(){ errorBox.textContent=''; errorBox.classList.add('hidden'); }
function terminalFromUrl(raw){ try{ const h=new URL(raw).hostname.toLowerCase(); if(h==='fomo.family'||h.endsWith('.fomo.family'))return 'FOMO'; if(h.includes('axiom.trade'))return 'AXIOM'; if(h.includes('tinyastro.io'))return 'PHOTON'; }catch{} return null; }
async function send(message,repair=true){
  if(!activeTabId)throw new Error('Няма активен terminal tab.');
  try{return await chrome.tabs.sendMessage(activeTabId,message);}catch(error){
    if(!repair||repairingConnection)throw error; repairingConnection=true; statusText.textContent='Активирам Sentinel v0.9…';
    try{
      await chrome.scripting.executeScript({target:{tabId:activeTabId},files:['page-tap-v081.js'],world:'MAIN'});
      await chrome.scripting.executeScript({target:{tabId:activeTabId},files:['visual-label-discovery-v09.js','sentinel-v5.js']});
      await sleep(900);
      return await chrome.tabs.sendMessage(activeTabId,message);
    }finally{repairingConnection=false;}
  }
}
function render(r){
  if(!r?.ok)return; terminalName.textContent=r.source||'—'; const top=Array.isArray(r.radarTop)?r.radarTop[0]:null;
  currentCoin.textContent=top?`${r.observedCount||0} resolved · $${top.symbol||'?'} ${top.status||''}`:`${r.observedCount||0} resolved`;
  const d=r.diagnostics||{};
  const feedText=d.feed?` · feed ${d.feed}/${d.feedStructured||0} structured`:'';
  if(r.deepScanning)statusText.textContent=`Deep анализът върви; realtime market scan продължава${feedText}.`;
  else if(r.radarScanning)statusText.textContent=`NEO обновява market flow и liquidity${feedText}.`;
  else if((r.observedCount||0)>0)statusText.textContent=`Sentinel v0.9 е активен · ${r.observedCount} resolved${feedText}.`;
  else statusText.textContent=`Слушам terminal feed + visual DOM discovery${feedText}.`;
  if(r.result){resultBox.classList.remove('hidden');postureEl.textContent=r.result.posture||'—';tokenNameEl.textContent=`${r.result.name||'Token'}${r.result.symbol?` · $${r.result.symbol}`:''}`;riskEl.textContent=`${r.result.risk??'-'}/100`;confidenceEl.textContent=`${r.result.confidence??'-'}%`;postureEl.className=`posture ${String(r.result.posture||'').toLowerCase()}`;}else resultBox.classList.add('hidden');
}
async function readStatus(){
  clearError(); const [tab]=await chrome.tabs.query({active:true,currentWindow:true});activeTabId=tab?.id??null;const t=terminalFromUrl(tab?.url||'');
  if(!t||!activeTabId){terminalName.textContent='НЕПОДДЪРЖАН TAB';currentCoin.textContent='—';statusText.textContent='Отвори Fomo, Axiom или Photon.';resultBox.classList.add('hidden');return;}
  terminalName.textContent=t;try{render(await send({type:'NEO_STATUS'},true));}catch(e){statusText.textContent='Не успях да стартирам Sentinel.';showError(e instanceof Error?e.message:String(e));}
}
rescanBtn.addEventListener('click',async()=>{clearError();rescanBtn.disabled=true;rescanBtn.textContent='ОБНОВЯВАМ…';try{if(!activeTabId)await readStatus();if(activeTabId){await send({type:'NEO_RESCAN'},true);setTimeout(readStatus,900);setTimeout(readStatus,3000);}}catch(e){showError(e instanceof Error?e.message:String(e));}finally{setTimeout(()=>{rescanBtn.disabled=false;rescanBtn.textContent='ОБНОВИ SENTINEL';},1000);}});
saveRpcBtn.addEventListener('click',async()=>{clearError();const value=rpcInput.value.trim();if(!value){await chrome.storage.local.remove('rpcUrl');rpcInput.value='';saveRpcBtn.textContent='RESET';setTimeout(()=>saveRpcBtn.textContent='ЗАПАЗИ ЛОКАЛНО',1200);return;}try{const u=new URL(value);if(u.protocol!=='https:')throw new Error('RPC URL трябва да е HTTPS.');const ok=await chrome.permissions.request({origins:[`${u.origin}/*`]});if(!ok)throw new Error('Permission отказано.');await chrome.storage.local.set({rpcUrl:value});saveRpcBtn.textContent='ЗАПАЗЕНО ✓';setTimeout(()=>saveRpcBtn.textContent='ЗАПАЗИ ЛОКАЛНО',1200);}catch(e){showError(e instanceof Error?e.message:String(e));}});
(async()=>{try{const s=await chrome.storage.local.get(['rpcUrl']);rpcInput.value=s.rpcUrl||'';}catch{}await readStatus();setTimeout(readStatus,1400);})();
