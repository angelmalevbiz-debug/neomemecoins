"""Main-engine-only quote accounting. No transaction building, signing or sending.
Preserves shared quote client's API throttling. Rejects stale/malformed replies.
OutAmount already includes AMM fees; explicit 10bps/leg execution cost is an
assumption, not an observed fill. Minimum slippage output is a separate bound.
"""
import math
import os
import threading
import time
from decimal import Decimal
import paper_execution_quotes as provider
import honest_quote_transport as transport

SLIPPAGE_BPS=provider.SLIPPAGE_BPS
USDC=provider.USDC_MINT
MAX_AGE_MS=2_000
MARK_TTL_MS=1_500
BUFFER_BPS=10
SIMULATED_DELAY_MS=max(0,int(os.getenv('NEO_PAPER_EXECUTION_DELAY_MS','250')))
MAX_SIGNAL_AGE_MS=int(os.getenv('NEO_PAPER_MAX_SIGNAL_AGE_MS','8000'))
_CACHE={}
_LOCK=threading.Lock()

def stamp(): return int(time.time()*1000)
def _int(v):
    if isinstance(v,bool): raise ValueError('boolean amount')
    if isinstance(v,int): return v
    if not isinstance(v,str) or not v.isdigit(): raise ValueError('integer amount required')
    return int(v)

def valid(data,input_mint,output_mint,amount,start):
    try:
        if not data or data.get('inputMint')!=input_mint or data.get('outputMint')!=output_mint: return False
        if _int(data.get('inAmount'))!=int(amount) or _int(data.get('outAmount'))<=0: return False
        floor=_int(data.get('otherAmountThreshold'))
        if not 0<floor<=_int(data['outAmount']): return False
        if data.get('swapMode')!='ExactIn' or not data.get('routePlan'): return False
        received=int(data.get('_received_at') or start)
        if not 0<=stamp()-received<=MAX_AGE_MS: return False
        if not math.isfinite(float(data['priceImpactPct'])): return False
        context=int(data.get('contextSlot') or 0); latest=int(data.get('_latest_slot') or 0)
        if latest and (not context or not 0<=latest-context<=25): return False
        return True
    except (ValueError,TypeError,KeyError): return False

def same_token_pool(data,mint,pair):
    legs=[leg.get('swapInfo',{}) for leg in data.get('routePlan',[]) if mint in
          (leg.get('swapInfo',{}).get('inputMint'),leg.get('swapInfo',{}).get('outputMint'))]
    return bool(pair) and bool(legs) and all(x.get('ammKey')==pair for x in legs)

def _request(a,b,amount,pair=None,token=None,purpose='entry',force=False):
    began=stamp(); q=transport.quote(a,b,int(amount),purpose=purpose,slippage_bps=SLIPPAGE_BPS,
                                   min_received_at=began if force else None)
    if not valid(q,a,b,amount,began): return None
    if pair and not same_token_pool(q,token,pair): return None
    # Simulated landing delay is a declared assumption, not an on-chain fill.
    # Re-check freshness after it; a quote cannot be booked retroactively.
    if SIMULATED_DELAY_MS: time.sleep(SIMULATED_DELAY_MS/1000)
    if not valid(q,a,b,amount,began): return None
    # Timestamp the received quote; queueing time is recorded separately.
    q['_observed_at']=int(q.get('_received_at') or stamp())
    q['_simulated_fill_at']=stamp()
    q['_simulated_delay_ms']=SIMULATED_DELAY_MS
    return q

def entry_quote(token_mint,pair_address,notional_usd):
    try:
        value=Decimal(str(notional_usd))
        if not value.is_finite() or value<=0:return None
        raw=int(value*1_000_000)
    except (ValueError,TypeError,ArithmeticError):return None
    d=_request(USDC,token_mint,raw,pair_address,token_mint)
    if not d: return None
    expected=_int(d['outAmount']); assumed=expected*(10000-BUFFER_BPS)//10000
    if assumed<_int(d['otherAmountThreshold']):return None
    return {'input_usdc_raw':raw,'token_raw_expected':expected,'token_raw_amount':assumed,
            'token_raw_floor':_int(d['otherAmountThreshold']),
            'price_impact_pct':float(d['priceImpactPct'])*100,
            'slippage_bps':int(d.get('slippageBps',SLIPPAGE_BPS)),
            'route':provider._compact_route(d),'quoted_at':d['_observed_at'],
            'context_slot':d.get('contextSlot'),'assumed_buffer_bps':BUFFER_BPS,'raw_quote':d,
            'simulated_fill_at':d['_simulated_fill_at'],'simulated_delay_ms':SIMULATED_DELAY_MS,
            'execution_model_version':'QUOTE_LATENCY_BUFFER_V6','is_simulated_fill':True,
            'queue_ms':d.get('_queue_ms'),'http_ms':d.get('_http_ms')}

def exit_quote(token_mint,token_raw_amount,pair_address=None,purpose='exit',force=False):
    try:raw=_int(token_raw_amount)
    except (ValueError,TypeError):return None
    if raw<=0:return None
    # Liquidate the same exact mint/quantity via Jupiter's best valid route.
    # A different exit AMM is not a different asset or a chart-price substitution.
    # Refusing a valid sale solely because it changes AMM can strand a position.
    d=_request(token_mint,USDC,raw,None,token_mint,purpose,force=force)
    if not d: return None
    token_legs=[x.get('swapInfo',{}) for x in d.get('routePlan',[])
                if x.get('swapInfo',{}).get('inputMint')==token_mint]
    if not token_legs: return None
    expected=_int(d['outAmount'])/1_000_000
    assumed=expected*(1-BUFFER_BPS/10000)
    if assumed<_int(d['otherAmountThreshold'])/1_000_000:return None
    return {'expected_usdc':assumed,'provider_expected_usdc':expected,
            'floor_usdc':_int(d['otherAmountThreshold'])/1_000_000,
            'price_impact_pct':float(d['priceImpactPct'])*100,
            'slippage_bps':int(d.get('slippageBps',SLIPPAGE_BPS)),
            'route':provider._compact_route(d),'quoted_at':d['_observed_at'],
            'context_slot':d.get('contextSlot'),'token_input_raw':raw,
            'route_matches_entry_pool':same_token_pool(d,token_mint,pair_address),
            'assumed_buffer_bps':BUFFER_BPS,'raw_quote':d,
            'simulated_fill_at':d['_simulated_fill_at'],'simulated_delay_ms':SIMULATED_DELAY_MS,
            'execution_model_version':'QUOTE_LATENCY_BUFFER_V6','is_simulated_fill':True}

def position_mark(position,coin,network_fee_usd,force=False):
    mint=position.get('address'); pair=position.get('pairAddress')
    # Use the actually recorded paper amount, never silently give old positions
    # extra tokens by replacing a floor/assumed amount with expected output.
    raw=int(position.get('jupiter_token_raw_amount') or 0)
    if not raw: return None
    key=(mint,pair,raw)
    with _LOCK: cached=_CACHE.get(key)
    if not force and cached and 0<=stamp()-cached['quoted_at']<=MARK_TTL_MS:
        return dict(cached,from_cache=True)
    fresh=exit_quote(mint,raw,pair,force=force)
    if not fresh: return None
    gross=fresh['expected_usdc']; fee=max(0,float(network_fee_usd))
    qty=float(position.get('quantity') or 0)
    result={'execution_source':'JUPITER_QUOTE_EXPECTED_WITH_BUFFER_V5','market_price':float(coin.get('priceUsd') or 0),
            'fill_price':gross/qty if qty>0 else 0,'market_value_usd':fresh['provider_expected_usdc'],
            'gross_proceeds_usd':gross,'dex_fee_usd':0.0,'network_fee_usd':fee,
            'net_proceeds_usd':max(0,gross-fee),'impact_pct':fresh['price_impact_pct'],
            'slippage_pct':BUFFER_BPS/100,'latency_pct':0.0,
            'slippage_tolerance_pct':fresh['slippage_bps']/100,
            'quoted_at':fresh['quoted_at'],'route':fresh['route'],'context_slot':fresh['context_slot'],
            'token_input_raw':raw,'from_cache':bool((fresh.get('raw_quote') or {}).get('_cache_hit')),'fees_included_in_quote':True,
            'execution_buffer_estimated':True,'raw_quote':fresh.get('raw_quote'),
            'queue_ms':(fresh.get('raw_quote') or {}).get('_queue_ms'),
            'http_ms':(fresh.get('raw_quote') or {}).get('_http_ms'),
            'route_matches_entry_pool':fresh.get('route_matches_entry_pool')}
    result.update(simulated_fill_at=fresh.get('simulated_fill_at'),simulated_delay_ms=SIMULATED_DELAY_MS,
                  execution_model_version='QUOTE_LATENCY_BUFFER_V6',is_simulated_fill=True,
                  slippage_floor_is_guarantee=False,quote_age_ms=max(0,stamp()-fresh['quoted_at']))
    with _LOCK:
        for k,v in list(_CACHE.items()):
            if stamp()-v['quoted_at']>MAX_AGE_MS: _CACHE.pop(k,None)
        _CACHE[key]=dict(result)
    return result


def consistent_preflight(first,sell,final,now=None):
    now=stamp() if now is None else now
    try:
        old=int(first['token_raw_amount']);new=int(final['token_raw_amount'])
        if old<=0 or new<=0:return False
        if abs(new/old-1)>.005:return False
        if not 0<=now-int(final['quoted_at'])<=750:return False
        if not 0<=int(final['quoted_at'])-int(sell['quoted_at'])<=4000:return False
        slot1=int(first.get('context_slot') or 0);slot2=int(final.get('context_slot') or 0)
        if slot1 and slot2 and not 0<=slot2-slot1<=25:return False
        # A positive preflight can be price movement, not negative trading costs.
        initial=int(first['input_usdc_raw'])/1e6
        if float(sell['provider_expected_usdc'])>initial*1.001:return False
        return True
    except (KeyError,ValueError,TypeError,ZeroDivisionError):return False


def signal_fresh_at_commit(signal_at,quote,*,now=None,max_age_ms=MAX_SIGNAL_AGE_MS):
    current=stamp() if now is None else int(now)
    try:
        observed=int(signal_at)
        quoted=int(quote['quoted_at'])
        fill=int(quote.get('simulated_fill_at') or quoted)
        return 0<=current-observed<=max_age_ms and observed<=fill<=current and 0<=current-quoted<=MAX_AGE_MS
    except (TypeError,ValueError,KeyError):return False


def prepare_entry(mint,pair,notional,signal_at=None):
    first=entry_quote(mint,pair,notional)
    if not first:return None
    sale=exit_quote(mint,first['token_raw_amount'],pair,purpose='entry')
    if not sale:return None
    # The simulated buy uses a newly received quote AFTER preflight, never an
    # old cheap quote selected by observing a later favourable sell quote.
    final=entry_quote(mint,pair,notional)
    if not final or not consistent_preflight(first,sale,final):return None
    if signal_at is not None and not signal_fresh_at_commit(signal_at,final):return None
    adjustment=min(1.,final['token_raw_amount']/first['token_raw_amount'])
    preview=dict(sale)
    for k in ['expected_usdc','floor_usdc']:preview[k]*=adjustment
    preview['is_preflight_estimate']=True
    final['preflight_buy_quote']=first['raw_quote']
    final['preflight_sell_quote']=sale['raw_quote']
    final['preflight_quantity_adjustment']=adjustment
    final['preflight_started_at']=first.get('quoted_at')
    final['end_to_end_ms']=stamp()-int(signal_at if signal_at is not None else first['quoted_at'])
    final['preflight_is_guarantee']=False
    return final,preview
