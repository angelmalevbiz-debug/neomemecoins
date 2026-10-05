"""Read-only quotes. Shared rate limit, short identical-request cache, exit priority.
Never builds/signs/submits transactions. Timestamp refers to HTTP reception,
not time waiting for the rate limiter. Legacy clients use the same lock/stamp.
"""
import hashlib,json,math,os,threading,time
import compat_file_lock as fcntl
from pathlib import Path
import requests

ROOT=Path(os.getenv('NEO_JUPITER_LOCK_PATH','/var/lib/neo-market/jupiter_quote.lock')).parent
LOCK=Path(os.getenv('NEO_JUPITER_LOCK_PATH',str(ROOT/'jupiter_quote.lock')))
STAMP=Path(os.getenv('NEO_JUPITER_STAMP_PATH',str(ROOT/'jupiter_quote_last.txt')))
INTERVAL=float(os.getenv('NEO_JUPITER_KEYLESS_MIN_INTERVAL','2.10'))
KEY=os.getenv('JUPITER_API_KEY','').strip()
URL=os.getenv('NEO_JUPITER_QUOTE_URL','https://api.jup.ag/swap/v1/quote')
LOCAL=threading.local()
ADAPTER_VERSION='jupiter-swap-v1-quote/2'
COOLDOWN=ROOT/'quote-provider-backoff.json'
WSOL='So11111111111111111111111111111111111111112'
USDC='EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v'

def quote_asset_reference(*,max_age_ms=60000):
 try:
  reference=json.loads((ROOT/'quote-asset-usd-reference.json').read_text())
  if 0<=now_ms()-int(reference['observed_at'])<=max_age_ms:return reference
 except (OSError,ValueError,TypeError,KeyError):pass
 return None

def _store_reference(data):
 """Share one timestamped FX valuation across the recorder and every account."""
 try:
  legs=[leg.get('swapInfo') or {} for leg in data.get('routePlan') or []]
  for leg in legs:
   if leg.get('inputMint')==WSOL and leg.get('outputMint')==USDC:
    raw_in,raw_out=int(leg['inAmount']),int(leg['outAmount'])
   elif data.get('inputMint')==WSOL and data.get('outputMint')==USDC and len(legs)==1:
    raw_in,raw_out=int(data['inAmount']),int(data['outAmount'])
   else:continue
   if raw_in>0 and raw_out>0:
    _write(ROOT/'quote-asset-usd-reference.json',{'quote_asset':WSOL,'usd_per_unit':raw_out/raw_in*1000,
       'observed_at':data['_received_at'],'context_slot':data.get('contextSlot'),
       'source':'JUPITER_CONVERSION_QUOTE_REFERENCE','is_executed_conversion':False,
       'input_raw':str(raw_in),'output_usdc_raw':str(raw_out)})
    return
 except (KeyError,ValueError,TypeError):pass

def last_error():
 """Thread-local structured failure; callers can journal without secrets."""
 return dict(getattr(LOCAL,'error',{}) or {})

def _fail(code,**details):
 LOCAL.error={'code':code,'at':now_ms(),'adapter_version':ADAPTER_VERSION,**details}
 return None

def _schema(data,inp,out,amount):
 try:
  if not isinstance(data,dict) or data.get('inputMint')!=inp or data.get('outputMint')!=out:return False
  if data.get('swapMode')!='ExactIn' or int(data.get('inAmount',0))!=amount:return False
  if not all(isinstance(data.get(k),str) and data[k].isdigit() for k in ('inAmount','outAmount','otherAmountThreshold')):return False
  if not math.isfinite(float(data['priceImpactPct'])) or not 0<=float(data['priceImpactPct'])<=1:return False
  if type(data.get('slippageBps')) is not int or not 0<=data['slippageBps']<=10000:return False
  expected=int(data['outAmount']);floor=int(data['otherAmountThreshold'])
  if not (expected>0 and 0<floor<=expected and type(data.get('contextSlot')) is int and data['contextSlot']>0 and isinstance(data.get('routePlan'),list) and data['routePlan']):return False
  for leg in data['routePlan']:
   info=leg.get('swapInfo') or {}
   if not all(isinstance(info.get(k),str) and info[k] for k in ('ammKey','inputMint','outputMint')):return False
   if not all(isinstance(info.get(k),str) and info[k].isdigit() and int(info[k])>0 for k in ('inAmount','outAmount')):return False
  return True
 except (KeyError,ValueError,TypeError,AttributeError):return False

def now_ms():return int(time.time()*1000)
def _write(path,obj):
 tmp=path.with_name(path.name+'.'+str(os.getpid())+'.tmp')
 tmp.write_text(json.dumps(obj));tmp.replace(path)

def _cached(path,min_received_at=None):
 try:
  d=json.loads(path.read_text())
  received=int(d.get('_received_at',0))
  if 0<=now_ms()-received<=700 and (min_received_at is None or received>=min_received_at):return dict(d,_cache_hit=True)
 except (OSError,ValueError,TypeError):pass
 return None

def quote(inp,out,amount,*,purpose='entry',slippage_bps=100,min_received_at=None):
 LOCAL.error=None
 if type(amount) is not int or amount<=0:return _fail('INVALID_AMOUNT')
 if type(slippage_bps) is not int or not 0<=slippage_bps<=10000:return _fail('INVALID_SLIPPAGE')
 if '/swap/v1/quote' not in URL:return _fail('UNSUPPORTED_ADAPTER_ENDPOINT')
 ROOT.mkdir(parents=True,exist_ok=True)
 cache=ROOT/'quote-response-cache';cache.mkdir(exist_ok=True)
 params=dict(inputMint=inp,outputMint=out,amount=str(amount),swapMode='ExactIn',
             slippageBps=int(slippage_bps),instructionVersion='V2',restrictIntermediateTokens='true')
 digest=hashlib.sha256(json.dumps({'params':params,'adapter':ADAPTER_VERSION,'url':URL},sort_keys=True).encode()).hexdigest()
 file=cache/(digest+'.json'); data=_cached(file,min_received_at)
 if data:return data
 priority=ROOT/'quote-exit-priority';priority.mkdir(exist_ok=True)
 marker=priority/(str(os.getpid())+'-'+str(threading.get_ident())+'.json')
 begin=now_ms(); is_exit=purpose=='exit'; deadline=time.monotonic()+(3.0 if is_exit else 4.5)
 if is_exit:_write(marker,{'requested_at':begin})
 try:
  while True:
   waiters=[]
   for p in priority.glob('*.json'):
    try:
     age=time.time()-p.stat().st_mtime
     if age>5:p.unlink(missing_ok=True)
     else:waiters.append((p.stat().st_mtime,p.name))
    except OSError:pass
   waiters.sort()
   other_priority=bool(waiters) and (not is_exit or waiters[0][1]!=marker.name)
   if not other_priority:
    with LOCK.open('a+') as h:
     acquired=False
     try:
      fcntl.flock(h,fcntl.LOCK_EX|fcntl.LOCK_NB); acquired=True
      data=_cached(file,min_received_at)
      if data:return data
      try:last=float(STAMP.read_text())
      except (OSError,ValueError):last=0
      try:backoff=float(json.loads(COOLDOWN.read_text()).get('until',0))
      except (OSError,ValueError,TypeError):backoff=0
      # Keep the existing shared quota; never invent a faster paid limit.
      if time.time()-last>=INTERVAL and time.time()>=backoff:
       http=getattr(LOCAL,'http',None)
       if http is None:
        http=requests.Session();http.headers['User-Agent']='NEO-Paper-Quotes-Audited/1.0';LOCAL.http=http
       sent=now_ms()
       try:
        r=http.get(URL,params=params,headers={'x-api-key':KEY} if KEY else {},timeout=(1.0,2.5))
        received=now_ms()
        if r.status_code in (401,403):return _fail('AUTHENTICATION_REQUIRED' if r.status_code==401 else 'ACCESS_DENIED',http_status=r.status_code,queue_ms=sent-begin,http_ms=received-sent)
        if r.status_code==429:
         try:until=float(r.headers.get('x-ratelimit-reset') or 0)
         except (TypeError,ValueError):until=0
         if until<=time.time():
          try:delay=float(r.headers.get('Retry-After') or 1)
          except (TypeError,ValueError):delay=1
          until=time.time()+max(1.,delay)
         _write(COOLDOWN,{'until':until})
         return _fail('RATE_LIMITED',http_status=429,retry_at=int(until*1000),queue_ms=sent-begin,http_ms=received-sent)
        r.raise_for_status();data=r.json()
        if not _schema(data,inp,out,amount):return _fail('SCHEMA_MISMATCH',http_status=r.status_code,queue_ms=sent-begin,http_ms=received-sent)
        data.update(_requested_at=begin,_sent_at=sent,_received_at=received,
                    _quotedAtMs=received,_queue_ms=sent-begin,_http_ms=received-sent,
                    _cache_hit=False,_adapter_version=ADAPTER_VERSION)
        _write(file,data)
        _store_reference(data)
        # Bounded cache, no historical account data or credentials are stored here.
        if len(list(cache.glob('*.json')))>500:
         for old in cache.glob('*.json'):
          try:
           if time.time()-old.stat().st_mtime>30:old.unlink(missing_ok=True)
          except OSError:pass
        return data
       except requests.Timeout:return _fail('TIMEOUT',queue_ms=sent-begin,http_ms=now_ms()-sent)
       except requests.RequestException as exc:return _fail('HTTP_ERROR',http_status=getattr(getattr(exc,'response',None),'status_code',None))
       except (ValueError,TypeError):return _fail('SCHEMA_MISMATCH')
       finally:STAMP.write_text(str(time.time()))
     except BlockingIOError:pass
     finally:
      if acquired:fcntl.flock(h,fcntl.LOCK_UN)
   if time.monotonic()>=deadline:return _fail('QUOTE_BUDGET_TIMEOUT',queue_ms=now_ms()-begin,purpose=purpose)
   time.sleep(.04)
 finally:
  if is_exit:marker.unlink(missing_ok=True)
