"""Independent exact-pool entry sanity check. No prices are invented or repaired.
Uses GeckoTerminal, separate from the DexScreener signal source. This detects
inconsistent quotes, not a guarantee of fresh executable prices or rug safety.
"""
import concurrent.futures as cf
import compat_file_lock as fcntl
import json,math,os,re,threading,time
from pathlib import Path
import requests

VERSION='PRICE_CROSSCHECK_V4'
ROOT=Path(os.getenv('NEO_PRICE_CHECK_DIR','/var/lib/neo-market/price-crosscheck'))
ADDRESS=re.compile(r'^[1-9A-HJ-NP-Za-km-z]{32,44}$')
TTL_MS=30_000
MAX_DIVERGENCE_PCT=5.0
JUPITER_REVIEW_MAX_DIVERGENCE_PCT=8.0
JUPITER_CONFIRM_OBSERVED_MAX_PCT=3.0
POOL=cf.ThreadPoolExecutor(max_workers=1,thread_name_prefix='price-crosscheck')
PENDING={};LOCK=threading.Lock()

def now():return int(time.time()*1000)
def num(x):
 try:
  v=float(x);return v if math.isfinite(v) else 0.
 except (ValueError,TypeError):return 0.

def validate(coin,ref,stamp=None):
 stamp=now() if stamp is None else stamp
 if ref.get('mint')!=coin.get('address') or ref.get('pair')!=coin.get('pairAddress'):
  return {'status':'blocked','reason':'price_identity_mismatch'}
 if not 0<=stamp-int(ref.get('received_at',0))<=TTL_MS:
  return {'status':'pending','reason':'price_reference_expired'}
 price=num(coin.get('priceUsd')); other=num(ref.get('price_usd'))
 if price<=0:return {'status':'blocked','reason':'price_unavailable'}
 if other<=0:
  return {'status':'review','reason':'price_unavailable_needs_jupiter',
          'version':VERSION,'observed_price':price,'reference_price':None,
          'reference_received_at':ref.get('received_at'),
          'source':'GeckoTerminal unavailable; Jupiter exact-pool confirmation required',
          'pair':ref.get('pair'),'mint':ref.get('mint')}
 divergence=abs(price/other-1)*100
 if divergence<=MAX_DIVERGENCE_PCT:
  status,reason='pass',''
 elif divergence<=JUPITER_REVIEW_MAX_DIVERGENCE_PCT:
  status,reason='review','price_source_disagreement_needs_jupiter'
 else:
  status,reason='blocked','price_source_disagreement'
 return {'status':status,'reason':reason,
         'version':VERSION,'observed_price':price,'reference_price':other,
         'divergence_pct':divergence,'reference_received_at':ref['received_at'],
         'source':'GeckoTerminal exact pool','pair':ref['pair'],'mint':ref['mint'],
         'provider_market_timestamp_available':False}

def jupiter_tiebreak(validation,jupiter_entry_price):
 observed=num(validation.get('observed_price')); jupiter=num(jupiter_entry_price)
 if validation.get('status')!='review' or validation.get('reason') not in {
  'price_source_disagreement_needs_jupiter','price_unavailable_needs_jupiter',
  'price_crosscheck_pending_needs_jupiter'
 }:
  return validation
 if observed<=0 or jupiter<=0:
  return {**validation,'status':'blocked','reason':'price_tiebreak_failed'}
 delta=abs(jupiter/observed-1)*100
 passed=delta<=JUPITER_CONFIRM_OBSERVED_MAX_PCT
 return {**validation,'status':'pass' if passed else 'blocked',
         'reason':'' if passed else 'price_tiebreak_failed',
         'jupiter_tiebreak':True,'jupiter_entry_price':jupiter,
         'jupiter_vs_observed_pct':delta,
         'jupiter_confirm_max_pct':JUPITER_CONFIRM_OBSERVED_MAX_PCT}

def _path(mint,pair):return ROOT/(mint+'-'+pair+'.json')
def _read(mint,pair):
 try:
  d=json.loads(_path(mint,pair).read_text());ttl=TTL_MS if d.get('price_usd') else 10_000
  if 0<=now()-d['received_at']<=ttl:return d
 except (OSError,ValueError,KeyError):pass
 return None

def _fetch(mint,pair):
 ROOT.mkdir(parents=True,exist_ok=True)
 with (ROOT/'fetch.lock').open('a+') as h:
  fcntl.flock(h,fcntl.LOCK_EX)
  d=_read(mint,pair)
  if d:return d
  stamp=ROOT/'last.txt'
  try:delay=2.1-(time.time()-float(stamp.read_text()))
  except (OSError,ValueError):delay=0
  if delay>0:time.sleep(delay)
  d={'mint':mint,'pair':pair,'received_at':now(),'price_usd':None}
  try:
   r=requests.get('https://api.geckoterminal.com/api/v2/networks/solana/pools/'+pair,timeout=(1,3))
   r.raise_for_status();data=r.json()['data'];a=data['attributes'];rel=data['relationships']
   if data['id']!='solana_'+pair or a['address']!=pair:raise ValueError('pair mismatch')
   if rel['base_token']['data']['id']!='solana_'+mint:raise ValueError('mint mismatch')
   price=num(a['base_token_price_usd'])
   if price<=0:raise ValueError('price missing')
   d.update(price_usd=price,received_at=now())
  except (requests.RequestException,ValueError,KeyError,TypeError) as exc:
   d.update(error=type(exc).__name__,received_at=now())
  finally:stamp.write_text(str(time.time()))
  p=_path(mint,pair);tmp=p.with_suffix('.tmp');tmp.write_text(json.dumps(d));tmp.replace(p)
  return d

def cached(coin):
 """Read-only lookup for the HF_PRICE_AUDIT_V1 log: the cached exact-pool reference validated
 against this observation, or None when none is cached. Never schedules a fetch."""
 mint=str(coin.get('address') or '');pair=str(coin.get('pairAddress') or '')
 if not ADDRESS.fullmatch(mint) or not ADDRESS.fullmatch(pair):return None
 ref=_read(mint,pair)
 return validate(coin,ref) if ref else None

def check(coin):
 mint=str(coin.get('address') or '');pair=str(coin.get('pairAddress') or '')
 if not ADDRESS.fullmatch(mint) or not ADDRESS.fullmatch(pair):return {'status':'blocked','reason':'price_identity_mismatch'}
 ref=_read(mint,pair)
 if ref:return validate(coin,ref)
 with LOCK:
  for k,f in list(PENDING.items()):
   if f.done():PENDING.pop(k,None)
  if (mint,pair) not in PENDING and len(PENDING)<12:
   PENDING[(mint,pair)]=POOL.submit(_fetch,mint,pair)
 observed=num(coin.get('priceUsd'))
 if observed<=0:
  return {'status':'blocked','reason':'price_unavailable'}
 return {'status':'review','reason':'price_crosscheck_pending_needs_jupiter',
         'version':VERSION,'observed_price':observed,'reference_price':None,
         'reference_received_at':None,
         'source':'GeckoTerminal pending; Jupiter exact-pool confirmation required',
         'pair':pair,'mint':mint}
