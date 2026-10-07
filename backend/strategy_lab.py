#!/usr/bin/env python3
import json, math, os, threading, time
from pathlib import Path
from typing import Any, Callable
import requests
from astra_lab_bridge import merge_astra_snapshot
from lab_paired_bridge import merge_paired_snapshot
from lab_portfolio_migration import promote_strategy_lab
import lab_activity as activity
import momentum_rush_brain as rush_brain
import engine_rug_guard as rug_guard
from lab_position_marks import POSITION_MARK_FEED
import paper_execution_quotes as paper_quotes
import pair_price_integrity as price_integrity
from lab_dashboard_projection import compact_strategy_lab

API_URL=os.getenv('NEO_LOCAL_API','http://127.0.0.1:8788/state')
DEX='https://api.dexscreener.com'
STATE_PATH=Path(os.getenv('NEO_STRATEGY_LAB_PATH','/var/lib/neo-market/strategy_lab.json'))
COMPACT_PATH=Path(os.getenv('NEO_STRATEGY_LAB_COMPACT_PATH',str(STATE_PATH.parent/'strategy_lab_compact.json')))
RESET_FLAG_PATH=Path(os.getenv('NEO_STRATEGY_LAB_RESET_FLAG','/var/lib/neo-market/strategy_lab.reset'))
LIVE_TAPE_PATH=Path(os.getenv('NEO_LIVE_TAPE_PATH','/var/lib/neo-market/live_tape.json'))
START_BALANCE=float(os.getenv('NEO_LAB_START_BALANCE','500'))
PROMOTED_STRATEGIES=('EARLY','MOMENTUM','PRECISION','ULTRA_PRECISION')
PROMOTED_TOTAL_CAPITAL=1000.0
PROMOTED_ALLOCATION=250.0
PROMOTED_MAX_POSITION_FRACTION=0.25
PROMOTED_LOSS_COOLDOWN_SECONDS=1800
PROMOTED_LOSS_STREAK=3
PROMOTED_ROLLING_WINDOW=8
STRATEGY_START_BALANCES={
    'SCALPER':float(os.getenv('NEO_LAB_SCALPER_START_BALANCE','100')),
    **{strategy_id:PROMOTED_ALLOCATION for strategy_id in PROMOTED_STRATEGIES},
}
TRADE_NOTIONAL=float(os.getenv('NEO_LAB_TRADE_NOTIONAL','150'))
POLL_SECONDS=float(os.getenv('NEO_LAB_POLL_SECONDS','2'))
ENTRY_REFRESH_SECONDS=float(os.getenv('NEO_LAB_ENTRY_REFRESH_SECONDS','2'))
STOP_LOSS=3.0
TAKE_PROFIT=10.0
TRAILING=4.0
MAX_HOLD_MIN=60.0
RUSH_TAKE_PROFIT=7.0
RUSH_MAX_HOLD_MIN=20.0
RUSH_TRAIL_ARM=4.0
RUSH_TRAIL_DRAWDOWN=2.5
RUSH_LIQ_MIN_FRACTION=.65
RUSH_FLOW_MAX_AGE_MS=12_000
REENTRY_COOLDOWN_MIN=activity.REENTRY_SECONDS/60.0

# Realistic paper execution costs for Strategy Lab. These are applied equally to
# every strategy so comparisons stay fair. The main engine remains untouched.
GENERIC_DEX_FEE_BPS=float(os.getenv('NEO_LAB_GENERIC_DEX_FEE_BPS','30'))
BASE_SLIPPAGE_BPS=float(os.getenv('NEO_LAB_BASE_SLIPPAGE_BPS','10'))
LATENCY_BUFFER_BPS=float(os.getenv('NEO_LAB_LATENCY_BUFFER_BPS','10'))
NETWORK_FEE_SOL=float(os.getenv('NEO_LAB_NETWORK_FEE_SOL','0.0001'))
MAX_PRICE_IMPACT_PCT=float(os.getenv('NEO_LAB_MAX_PRICE_IMPACT_PCT','20'))
EXECUTION_MODEL_VERSION='DEX_SPOT_MODELED_COSTS_V2'
POSITION_STALE_AFTER_MS=12_000
LAB_PRICE_PROBE_NOTIONAL_USD=float(os.getenv('NEO_LAB_PRICE_PROBE_NOTIONAL_USD','10'))
LAB_PRICE_PROBE_TTL_MS=10_000
LAB_PRICE_PROBE_RETRY_MS=15_000
LAB_PRICE_PROBE_LOCK=threading.Lock()
LAB_PRICE_PROBE_CACHE={}
LAB_PRICE_PROBE_RETRY_AFTER={}
LAB_PRICE_PROBE_INFLIGHT=False

SESSION=requests.Session()
SESSION.headers.update({'user-agent':'NEO-Strategy-Lab/1.0','accept':'application/json'})
FLOW_TAPE_DIAGNOSTICS={'status':'unknown','coverage_pct':0.0,'backlog':0,'verified_events_60s':0}

def now_ms(): return int(time.time()*1000)
def num(v,d=0.0):
    try:
        x=float(v)
        return x if math.isfinite(x) else d
    except Exception: return d

def promoted_pause_remaining_ms(book,now):
    """Temporarily pause funded PAPER entries after a recent losing run."""
    trades=[t for t in book.get('history',[])
            if t.get('closed_at') and t.get('promotion_eligible') is not False]
    trades.sort(key=lambda t:num(t.get('closed_at')),reverse=True)
    if not trades: return 0
    streak=0
    for trade in trades:
        if num(trade.get('pnl_usd'))<0: streak+=1
        else: break
    recent=trades[:PROMOTED_ROLLING_WINDOW]
    rolling_loss=(len(recent)>=PROMOTED_ROLLING_WINDOW
                  and sum(num(t.get('pnl_usd')) for t in recent)<0)
    if streak<PROMOTED_LOSS_STREAK and not rolling_loss: return 0
    latest_close=int(num(trades[0].get('closed_at')))
    return max(0,latest_close+PROMOTED_LOSS_COOLDOWN_SECONDS*1000-now)

def pair_liquidity_usd(c):
    return num(c.get('liquidityUsd') or (c.get('liquidity') or {}).get('usd'))

def sol_usd_from_coin(c):
    p=num(c.get('priceUsd')); n=num(c.get('priceNative'))
    return p/n if p>0 and n>0 else 0.0

def pumpswap_fee_bps(c):
    if str(c.get('dexId') or '').lower()!='pumpswap':
        return GENERIC_DEX_FEE_BPS
    sol_usd=sol_usd_from_coin(c); mc=num(c.get('marketCap') or c.get('fdv'))
    if sol_usd<=0 or mc<=0: return 125.0
    mc_sol=mc/sol_usd
    tiers=(
      (420,125.0),(1470,120.0),(2460,115.0),(3440,110.0),(4420,105.0),
      (9820,100.0),(14740,95.0),(19650,90.0),(24560,85.0),(29470,80.0),
      (34380,75.0),(39300,70.0),(44210,65.0),(49120,60.0),(54030,55.0),
      (58940,52.5),(63860,50.0),(68770,47.5),(73681,45.0),(78590,42.5),
      (83500,40.0),(88400,37.5),(93330,35.0),(98240,32.5),
    )
    for max_mc,fee in tiers:
        if mc_sol<max_mc: return fee
    return 30.0

def execution_friction(c,trade_value):
    liq=max(pair_liquidity_usd(c),1.0)
    impact=min(MAX_PRICE_IMPACT_PCT,(2.0*max(0.0,trade_value)/liq)*100.0)
    return {
      'impact_pct':impact,
      'slippage_pct':BASE_SLIPPAGE_BPS/100.0,
      'latency_pct':LATENCY_BUFFER_BPS/100.0,
      'dex_fee_bps':pumpswap_fee_bps(c),
      'network_fee_usd':NETWORK_FEE_SOL*sol_usd_from_coin(c),
    }

def entry_execution(c,notional):
    market=num(c.get('priceUsd')); f=execution_friction(c,notional)
    penalty=(f['impact_pct']+f['slippage_pct']+f['latency_pct'])/100.0
    fill=market*(1+penalty)
    dex_fee=notional*f['dex_fee_bps']/10000.0
    qty=max(0.0,notional-dex_fee)/max(fill,1e-18)
    return {**f,'market_price':market,'fill_price':fill,'dex_fee_usd':dex_fee,
            'quantity':qty,'capital_committed_usd':notional+f['network_fee_usd']}

def exit_execution(c,qty):
    market=num(c.get('priceUsd')); market_value=max(0.0,qty*market)
    f=execution_friction(c,market_value)
    penalty=(f['impact_pct']+f['slippage_pct']+f['latency_pct'])/100.0
    fill=max(0.0,market*(1-penalty))
    gross=max(0.0,qty*fill)
    dex_fee=gross*f['dex_fee_bps']/10000.0
    net=max(0.0,gross-dex_fee-f['network_fee_usd'])
    return {**f,'market_price':market,'fill_price':fill,'market_value_usd':market_value,
            'gross_proceeds_usd':gross,'dex_fee_usd':dex_fee,'net_proceeds_usd':net}

def load_json(path,default):
    try: return json.loads(path.read_text(encoding='utf-8'))
    except Exception: return default

def atomic_write_path(path,data):
    path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_suffix(path.suffix+'.tmp')
    tmp.write_text(json.dumps(data,ensure_ascii=False),encoding='utf-8')
    tmp.replace(path)

def atomic_write(data):
    atomic_write_path(STATE_PATH,data)

def flow_map():
    global FLOW_TAPE_DIAGNOSTICS
    tape=load_json(LIVE_TAPE_PATH,{})
    now=now_ms(); cutoff=now-60_000
    coverage=tape.get('pair_coverage') or {}
    out={}; verified_events=0
    for e in tape.get('events',[]):
        pair=str(e.get('pairAddress') or '')
        if coverage.get(pair,{}).get('status')!='COMPLETE': continue
        event_time=num(e.get('event_time'),num(e.get('ts')))
        observed=num(e.get('observed_at'))
        available=num(e.get('available_at'),num(e.get('ingested_at')))
        if (e.get('confirmed_swap') is not True or e.get('quality_flags')
                or event_time<cutoff or event_time<=0 or observed<=0
                or event_time>observed or observed>available or available>now):
            continue
        a=e.get('address')
        wallet=e.get('wallet'); usd=num(e.get('usd_amount'))
        if not a or not wallet or usd<=0: continue
        if e.get('direction') not in {'BUY','SELL'}: continue
        f=out.setdefault((a,pair),{'trades':0,'buys':0,'sells':0,'buy_usd':0.0,'sell_usd':0.0,'wallets':set(),'max_sell':0.0})
        f['trades']+=1; verified_events+=1
        f['available_at']=max(num(f.get('available_at')),available)
        f['wallets'].add(wallet)
        if e.get('direction')=='BUY': f['buys']+=1; f['buy_usd']+=usd
        else: f['sells']+=1; f['sell_usd']+=usd; f['max_sell']=max(f['max_sell'],usd)
    for f in out.values():
        f['unique_wallets']=len(f.pop('wallets')); f['ratio']=f['buy_usd']/max(f['sell_usd'],1)
        f['window_at']=now
    # Keep the historical address lookup only where it identifies one pool.
    # Entry enrichment always prefers the exact mint/pair observation.
    exact_rows=list(out.items())
    addresses={key[0] for key,f in exact_rows}
    for address in addresses:
        rows=[f for (mint,pair),f in exact_rows if mint==address]
        if len(rows)==1: out[address]=rows[0]
    FLOW_TAPE_DIAGNOSTICS={
        'status':str(tape.get('status') or 'offline'),
        'coverage_pct':round(max(0.0,min(1.0,num(tape.get('coverage'))))*100,1),
        'backlog':max(0,int(num(tape.get('backlog')))),
        'verified_events_60s':verified_events,
    }
    return out
def enrich(c,flows):
    address=c.get('address'); pair=c.get('pairAddress')
    flow=flows.get((address,pair))
    if flow is None and not any(isinstance(key,tuple) and key[0]==address for key in flows):
        flow=flows.get(address)
    return activity.market_features(c,flow)

def cached_jupiter_tiebreak(validation,coin,*,now=None):
    """Use a fresh, same-token PAPER route quote to finish a price review.

    GeckoTerminal remains the first reference. When it is pending/unavailable,
    the shared, rate-limited Jupiter quote path can confirm the signal price.
    The risk check remains bound to the exact signal mint/pair; the independent
    quote may use any valid route for that mint. It does not place an order.
    """
    now=now_ms() if now is None else int(now)
    mint=str(coin.get('address') or ''); pair=str(coin.get('pairAddress') or '')
    key=(mint,pair)
    with LAB_PRICE_PROBE_LOCK:
        probe=LAB_PRICE_PROBE_CACHE.get(key)
    if not probe:
        return validation
    quote=probe.get('quote') or {}
    quoted_at=int(num(quote.get('quoted_at')))
    signal_at=int(num(coin.get('updatedAt')))
    decimals=probe.get('decimals')
    if (probe.get('mint')!=mint or probe.get('pair')!=pair
            or quote.get('input_mint')!=paper_quotes.USDC_MINT
            or quote.get('output_mint')!=mint
            or type(decimals) is not int or not 0<=decimals<=18
            or not 0<=now-quoted_at<=LAB_PRICE_PROBE_TTL_MS
            or signal_at<=0 or signal_at>quoted_at
            or not 0<=now-signal_at<=activity.MAX_FEED_AGE_MS):
        return validation
    try:
        raw=int(quote.get('token_raw_amount'))
        notional=float(quote.get('input_usdc_raw'))/1_000_000
        quantity=raw/(10**decimals)
        if raw<=0 or notional<=0 or quantity<=0:
            return validation
        quoted_price=notional/quantity
    except (TypeError,ValueError,OverflowError,ZeroDivisionError):
        return validation
    return price_integrity.jupiter_tiebreak(validation,quoted_price)

def schedule_jupiter_price_probe(coin):
    """Schedule one shared, non-blocking same-token cross-check at a time."""
    global LAB_PRICE_PROBE_INFLIGHT
    mint=str(coin.get('address') or ''); pair=str(coin.get('pairAddress') or '')
    if not mint or not pair:
        return False
    key=(mint,pair); stamp=now_ms()
    with LAB_PRICE_PROBE_LOCK:
        cached=LAB_PRICE_PROBE_CACHE.get(key)
        if cached and 0<=stamp-int(num((cached.get('quote') or {}).get('quoted_at')))<=LAB_PRICE_PROBE_TTL_MS:
            return False
        if LAB_PRICE_PROBE_INFLIGHT or LAB_PRICE_PROBE_RETRY_AFTER.get(key,0)>stamp:
            return False
        LAB_PRICE_PROBE_INFLIGHT=True

    def run():
        global LAB_PRICE_PROBE_INFLIGHT
        retry_ms=LAB_PRICE_PROBE_RETRY_MS
        try:
            # Reuse the main engine's cached full safety check for mint decimals.
            # Do not start an independent risk scan or bypass a missing result.
            guard=rug_guard.check(coin)
            decimals=(guard.get('metrics') or {}).get('decimals')
            if (guard.get('status')!='pass' or guard.get('mint')!=mint
                    or guard.get('pair')!=pair or type(decimals) is not int
                    or not 0<=decimals<=18):
                retry_ms=3_000
                return
            input_raw=int(round(LAB_PRICE_PROBE_NOTIONAL_USD*1_000_000))
            if input_raw<=0:
                return
            # This is a read-only quote for the same token mint, not a simulated
            # fill. The strategy's modeled execution still uses the signal pool.
            raw_quote=paper_quotes.quote(
                paper_quotes.USDC_MINT,mint,input_raw,purpose='background')
            if not raw_quote:
                return
            route_plan=raw_quote.get('routePlan') or []
            token_legs=[(leg.get('swapInfo') or {}) for leg in route_plan
                        if mint in ((leg.get('swapInfo') or {}).get('inputMint'),
                                    (leg.get('swapInfo') or {}).get('outputMint'))]
            quoted_at=int(num(raw_quote.get('_received_at')))
            if (raw_quote.get('inputMint')!=paper_quotes.USDC_MINT
                    or raw_quote.get('outputMint')!=mint
                    or int(num(raw_quote.get('inAmount'))) != input_raw
                    or int(num(raw_quote.get('outAmount')))<=0
                    or not token_legs or quoted_at<=0):
                return
            quote={
                'input_mint':raw_quote['inputMint'],'output_mint':raw_quote['outputMint'],
                'quoted_at':quoted_at,'input_usdc_raw':raw_quote['inAmount'],
                'token_raw_amount':raw_quote['outAmount'],
                'route_amm_keys':[leg.get('ammKey') for leg in token_legs],
            }
            with LAB_PRICE_PROBE_LOCK:
                LAB_PRICE_PROBE_CACHE[key]={
                    'mint':mint,'pair':pair,'decimals':decimals,'quote':quote,
                    'signal_observed_at':int(num(coin.get('updatedAt'))),
                }
                retry_ms=LAB_PRICE_PROBE_TTL_MS
                if len(LAB_PRICE_PROBE_CACHE)>128:
                    oldest=sorted(LAB_PRICE_PROBE_CACHE.items(),key=lambda item:int(num((item[1].get('quote') or {}).get('quoted_at'))))
                    for old_key,_ in oldest[:len(LAB_PRICE_PROBE_CACHE)-96]:
                        LAB_PRICE_PROBE_CACHE.pop(old_key,None)
        except Exception:
            # Quote/risk provider outages remain visible as a blocked signal;
            # the next bounded retry can recover without interrupting exits.
            pass
        finally:
            with LAB_PRICE_PROBE_LOCK:
                LAB_PRICE_PROBE_INFLIGHT=False
                LAB_PRICE_PROBE_RETRY_AFTER[key]=max(
                    LAB_PRICE_PROBE_RETRY_AFTER.get(key,0),now_ms()+retry_ms)

    try:
        threading.Thread(target=run,name='neo-lab-jupiter-price-check',daemon=True).start()
        return True
    except RuntimeError:
        with LAB_PRICE_PROBE_LOCK:
            LAB_PRICE_PROBE_INFLIGHT=False
            LAB_PRICE_PROBE_RETRY_AFTER[key]=stamp+LAB_PRICE_PROBE_RETRY_MS
        return False

STRATEGIES=[
 {'id':'ULTRA_PRECISION','name':'Ultra Precision','rule':lambda f: f['score']>=98 and f['liq']>=25000 and 3<=f['m5']<=18 and 1.1<=f['bs']<=3.0 and f['lmc']>=.15 and 8<=f['age']<=180},
 {'id':'PRECISION','name':'Precision','rule':lambda f: f['score']>=95 and f['liq']>=20000 and 2<=f['m5']<=22 and 1.0<=f['bs']<=3.2 and f['lmc']>=.12 and 5<=f['age']<=240},
 {'id':'MOMENTUM','name':'Momentum','rule':lambda f: f['score']>=90 and f['liq']>=15000 and 5<=f['m5']<=30 and f['bs']>=1.15 and f['lmc']>=.08 and 3<=f['age']<=300},
 {'id':'MOMENTUM_RUSH_BRAIN','name':'Momentum Rush Brain','rule':lambda f: activity.RULES['MOMENTUM_RUSH_BRAIN'].matches(f)},
 {'id':'BREAKOUT','name':'Breakout','rule':lambda f: f['score']>=90 and f['liq']>=20000 and 15<f['m5']<=55 and f['bs']>=1.4 and f['lmc']>=.08 and 3<=f['age']<=300},
 {'id':'LIQUIDITY','name':'Liquidity First','rule':lambda f: f['score']>=85 and f['liq']>=40000 and -2<=f['m5']<=20 and f['bs']>=.9 and f['lmc']>=.12 and 5<=f['age']<=720},
 {'id':'ORDER_FLOW','name':'Order Flow','rule':lambda f: f['score']>=85 and f['liq']>=15000 and -5<=f['m5']<=25 and f['flow']['trades']>=3 and f['flow']['ratio']>=1.3 and f['flow']['unique_wallets']>=1 and f['flow']['max_sell']<max(750,f['flow']['buy_usd']*.8)},
 {'id':'EARLY','name':'Early Runner','rule':lambda f: f['score']>=90 and f['liq']>=15000 and 1<=f['m5']<=20 and f['bs']>=1.05 and f['lmc']>=.10 and 2<=f['age']<=60},
 {'id':'TREND','name':'Balanced Trend','rule':lambda f: f['score']>=88 and f['liq']>=20000 and 0<=f['m5']<=15 and 0<=f['h1']<=150 and .9<=f['bs']<=3.0 and f['lmc']>=.10 and 15<=f['age']<=480},
 {'id':'SCALPER','name':'Fast Scalper 3/10','rule':lambda f: f['score']>=80 and f['liq']>=10000 and -5<=f['m5']<=15 and f['bs']>=.90 and f['lmc']>=.05 and 2<=f['age']<=300},
 {'id':'VOLUME_SURGE','name':'Volume Surge','rule':lambda f: f['score']>=88 and f['liq']>=15000 and 2<=f['m5']<=28 and f['vol_liq']>=.35 and f['bs']>=1.1 and f['lmc']>=.08 and 3<=f['age']<=360},
 {'id':'REVERSAL','name':'Reversal Catch','rule':lambda f: f['score']>=85 and f['liq']>=20000 and -10<=f['m5']<=3 and f['h1']>-25 and f['bs']>=1.15 and f['lmc']>=.10 and 10<=f['age']<=480},
 {'id':'FLOW_MOMENTUM','name':'Flow Momentum','rule':lambda f: f['score']>=85 and f['liq']>=15000 and 0<=f['m5']<=30 and f['flow']['trades']>=3 and f['flow']['ratio']>=1.8 and f['flow']['buy_usd']>=150},
 {'id':'FLOW_MOMENTUM_SCALE_OUT','name':'Flow Momentum Scale-Out','rule':lambda f: f['score']>=85 and f['liq']>=15000 and 0<=f['m5']<=30 and f['flow']['trades']>=3 and f['flow']['ratio']>=1.8 and f['flow']['buy_usd']>=150},

 # 20 selective candidates designed for higher hit-rate testing. They are
 # hypotheses, not guaranteed winners; the lab exists to prove or reject them.
 {'id':'FLOW_ELITE','name':'Flow Elite','rule':lambda f: f['score']>=92 and f['liq']>=30000 and 0<=f['m5']<=18 and f['flow']['trades']>=5 and f['flow']['ratio']>=2.2 and f['flow']['buy_usd']>=250 and f['flow']['unique_wallets']>=4 and f['flow']['max_sell']<max(250,f['flow']['buy_usd']*.45)},
 {'id':'FLOW_SNIPER','name':'Flow Sniper','rule':lambda f: f['score']>=95 and f['liq']>=25000 and 1<=f['m5']<=12 and f['flow']['trades']>=5 and f['flow']['ratio']>=2.5 and f['flow']['buy_usd']>=300 and f['flow']['unique_wallets']>=5 and f['flow']['max_sell']<max(200,f['flow']['buy_usd']*.40)},
 {'id':'LIQ_FLOW_CONFLUENCE','name':'Liquidity + Flow','rule':lambda f: f['score']>=90 and f['liq']>=50000 and -1<=f['m5']<=15 and f['lmc']>=.12 and f['flow']['trades']>=4 and f['flow']['ratio']>=1.8 and f['flow']['buy_usd']>=200},
 {'id':'BREAKOUT_CONFIRM','name':'Confirmed Breakout','rule':lambda f: f['score']>=92 and f['liq']>=30000 and 10<=f['m5']<=30 and f['bs']>=1.35 and f['flow']['trades']>=4 and f['flow']['ratio']>=1.6 and f['flow']['buy_usd']>=200 and .15<=f['vol_liq']<=6},
 {'id':'EARLY_FLOW','name':'Early Flow','rule':lambda f: f['score']>=92 and f['liq']>=22000 and 1<=f['m5']<=15 and 3<=f['age']<=75 and f['lmc']>=.12 and f['flow']['trades']>=4 and f['flow']['ratio']>=1.8 and f['flow']['unique_wallets']>=3},
 {'id':'TREND_FLOW','name':'Trend + Flow','rule':lambda f: f['score']>=90 and f['liq']>=30000 and 1<=f['m5']<=12 and 0<=f['h1']<=80 and f['bs']>=1.15 and f['flow']['ratio']>=1.7 and f['flow']['trades']>=4},
 {'id':'DEEP_LIQ_MOMENTUM','name':'Deep Liquidity Momentum','rule':lambda f: f['score']>=90 and f['liq']>=80000 and 2<=f['m5']<=20 and f['bs']>=1.15 and f['lmc']>=.08 and .10<=f['vol_liq']<=5},
 {'id':'LOW_VOL_FLOW','name':'Low Volatility Flow','rule':lambda f: f['score']>=90 and f['liq']>=30000 and -1<=f['m5']<=8 and f['h1']>-20 and f['flow']['ratio']>=2.0 and f['flow']['trades']>=5 and f['flow']['max_sell']<max(200,f['flow']['buy_usd']*.5)},
 {'id':'HIGH_LMC_FLOW','name':'High L/MC Flow','rule':lambda f: f['score']>=90 and f['liq']>=25000 and f['lmc']>=.22 and 0<=f['m5']<=15 and f['flow']['ratio']>=1.6 and f['flow']['trades']>=4},
 {'id':'VOLUME_QUALITY','name':'Quality Volume','rule':lambda f: f['score']>=92 and f['liq']>=30000 and 1<=f['m5']<=18 and .30<=f['vol_liq']<=4 and f['bs']>=1.2 and f['flow']['ratio']>=1.5},
 {'id':'BUY_PRESSURE','name':'Buy Pressure','rule':lambda f: f['score']>=90 and f['liq']>=25000 and 0<=f['m5']<=18 and f['bs']>=1.5 and f['flow']['ratio']>=2.0 and f['flow']['trades']>=5 and f['flow']['buy_usd']>=250},
 {'id':'SELL_WALL_SAFE','name':'Sell Wall Safe','rule':lambda f: f['score']>=90 and f['liq']>=30000 and 0<=f['m5']<=15 and f['flow']['trades']>=5 and f['flow']['ratio']>=1.8 and f['flow']['max_sell']<180 and f['flow']['buy_usd']>=220},
 {'id':'MICRO_BREAKOUT_SAFE','name':'Micro Breakout Safe','rule':lambda f: f['score']>=94 and f['liq']>=35000 and 5<=f['m5']<=16 and f['bs']>=1.25 and f['lmc']>=.15 and f['flow']['ratio']>=1.6},
 {'id':'MATURE_FLOW','name':'Mature Flow','rule':lambda f: f['score']>=90 and f['liq']>=40000 and 30<=f['age']<=720 and -1<=f['m5']<=14 and f['h1']>-30 and f['flow']['ratio']>=1.8 and f['flow']['trades']>=4},
 {'id':'YOUNG_LIQ','name':'Young + Liquid','rule':lambda f: f['score']>=93 and f['liq']>=35000 and 3<=f['age']<=90 and 0<=f['m5']<=16 and f['lmc']>=.15 and f['flow']['ratio']>=1.5},
 {'id':'HIGH_SCORE_FLOW','name':'High Score Flow','rule':lambda f: f['score']>=97 and f['liq']>=20000 and -1<=f['m5']<=16 and f['flow']['trades']>=4 and f['flow']['ratio']>=1.6 and f['flow']['buy_usd']>=180},
 {'id':'FLOW_PULLBACK','name':'Flow Pullback','rule':lambda f: f['score']>=90 and f['liq']>=30000 and -4<=f['m5']<=4 and f['h1']>=0 and f['flow']['trades']>=5 and f['flow']['ratio']>=2.0 and f['flow']['buy_usd']>=250},
 {'id':'SECOND_WAVE','name':'Second Wave','rule':lambda f: f['score']>=90 and f['liq']>=30000 and 2<=f['m5']<=12 and 10<=f['h1']<=120 and f['flow']['ratio']>=1.7 and f['flow']['trades']>=4 and .15<=f['vol_liq']<=5},
 {'id':'CLEAN_MOMENTUM','name':'Clean Momentum','rule':lambda f: f['score']>=92 and f['liq']>=30000 and 3<=f['m5']<=18 and f['bs']>=1.2 and .20<=f['vol_liq']<=3.5 and f['flow']['ratio']>=1.5 and f['flow']['max_sell']<max(250,f['flow']['buy_usd']*.6)},
 {'id':'CONFLUENCE_MAX','name':'Confluence Max','rule':lambda f: f['score']>=95 and f['liq']>=40000 and 1<=f['m5']<=12 and f['h1']>-10 and f['bs']>=1.25 and f['lmc']>=.15 and .20<=f['vol_liq']<=4 and f['flow']['trades']>=5 and f['flow']['ratio']>=2.0 and f['flow']['unique_wallets']>=4 and f['flow']['max_sell']<max(200,f['flow']['buy_usd']*.45)},
]
def empty_book(s):
    start=STRATEGY_START_BALANCES.get(s['id'],START_BALANCE)
    return {'id':s['id'],'name':s['name'],'starting_balance':start,'balance':start,
            'portfolio_group':'PROMOTED_PAPER' if s['id'] in PROMOTED_STRATEGIES else 'TEST',
            'allocation_usd':start,
            'max_position_fraction':(PROMOTED_MAX_POSITION_FRACTION if s['id'] in PROMOTED_STRATEGIES
                                     else activity.RUSH_MAX_BALANCE_FRACTION if s['id']==rush_brain.STRATEGY_ID else 1.0),
            'position':None,'history':[],'trade_seq':0,'last_entry_by_address':{},'created_at':now_ms()}

def load_state():
    reset_requested=RESET_FLAG_PATH.exists()
    if reset_requested:
        raw={}
        try: RESET_FLAG_PATH.unlink()
        except Exception: pass
    else:
        raw=load_json(STATE_PATH,{})
    books={}
    stored_books=raw.get('books') or {}
    setup=raw.get('portfolio_setup') or {}
    cohort_active=setup.get('version')=='PROMOTED_PAPER_COHORT_V1' and setup.get('status')=='ACTIVE'
    cohort_draining=setup.get('version')=='PROMOTED_PAPER_COHORT_V1' and setup.get('status')=='DRAINING'
    for s in STRATEGIES:
        existing=stored_books.get(s['id'])
        b=existing or empty_book(s)
        b['id']=s['id']; b['name']=s['name']
        if s['id'] in PROMOTED_STRATEGIES and (cohort_draining or b.get('promotion_pending')):
            b['portfolio_group']='PROMOTION_DRAINING'
            b['allocation_usd']=num(b.get('starting_balance'),START_BALANCE)
            b['max_position_fraction']=1.0
        elif s['id'] in PROMOTED_STRATEGIES and (cohort_active or existing is None):
            b['portfolio_group']='PROMOTED_PAPER'
            b['allocation_usd']=PROMOTED_ALLOCATION
            b['max_position_fraction']=PROMOTED_MAX_POSITION_FRACTION
        elif s['id'] in PROMOTED_STRATEGIES:
            b['portfolio_group']='TEST'
            b['allocation_usd']=num(b.get('starting_balance'),START_BALANCE)
            b['max_position_fraction']=1.0
        else:
            b['portfolio_group']='TEST'
            b['allocation_usd']=b.get('allocation_usd',b.get('starting_balance',START_BALANCE))
            b['max_position_fraction']=activity.RUSH_MAX_BALANCE_FRACTION if s['id']==rush_brain.STRATEGY_ID else 1.0
        books[s['id']]=b
    return {'started_at':now_ms() if reset_requested else (raw.get('started_at') or now_ms()),
            'updated_at':now_ms(),'status':'starting','books':books,
            'activity_version':raw.get('activity_version'),
            'activity_started_at':raw.get('activity_started_at'),
            'portfolio_setup':raw.get('portfolio_setup')}

STATE={'started_at':now_ms(),'updated_at':now_ms(),'status':'starting',
       'books':{s['id']:empty_book(s) for s in STRATEGIES}}
if STATE.get('activity_version')!=activity.POLICY_VERSION:
    STATE['activity_version']=activity.POLICY_VERSION
    STATE['activity_started_at']=now_ms()
assert set(activity.RULES)=={s['id'] for s in STRATEGIES}, 'Every Lab strategy needs an activity policy'

def close_position(book,pos,coin,reason):
    market_price=num(coin.get('priceUsd')); qty=num(pos.get('quantity'))
    quote=exit_execution(coin,qty)
    cost_basis=num(pos.get('remaining_cost_basis_usd'),num(pos.get('notional_usd'))+num(pos.get('entry_network_fee_usd')))
    final_pnl=quote['net_proceeds_usd']-cost_basis
    partial_pnl=num(pos.get('partial_realized_pnl'))
    total_pnl=partial_pnl+final_pnl
    original_notional=num(pos.get('notional_usd'))
    pct=total_pnl/max(original_notional,1e-18)*100
    book['balance']=round(num(book['balance'])+final_pnl,8)
    trade={**pos,'exit_price':market_price,'execution_exit_price':round(quote['fill_price'],12),
           'closed_at':now_ms(),'exit_reason':reason,'final_leg_pnl_usd':round(final_pnl,4),
           'pnl_usd':round(total_pnl,4),'pnl_pct':round(pct,3),'balance_after':round(book['balance'],4),
           'exit_dex_fee_usd':round(quote['dex_fee_usd'],6),'exit_network_fee_usd':round(quote['network_fee_usd'],6),
           'exit_price_impact_pct':round(quote['impact_pct'],4),
           'exit_slippage_pct':round(quote['slippage_pct']+quote['latency_pct'],4),
           'execution_mode':EXECUTION_MODEL_VERSION,
           'execution_source':'DEX_SPOT_WITH_MODELED_FRICTION'}
    book['history'].insert(0,trade); book['position']=None

def realize_partial(book,pos,coin,fraction,label):
    market_price=num(coin.get('priceUsd'))
    original_qty=num(pos.get('original_quantity'),num(pos.get('quantity')))
    remaining_qty=num(pos.get('quantity'))
    sell_qty=min(remaining_qty,original_qty*fraction)
    if sell_qty<=0: return 0.0
    quote=exit_execution(coin,sell_qty)
    remaining_basis=num(pos.get('remaining_cost_basis_usd'),num(pos.get('notional_usd'))+num(pos.get('entry_network_fee_usd')))
    basis_sold=remaining_basis*(sell_qty/max(remaining_qty,1e-18))
    pnl=quote['net_proceeds_usd']-basis_sold
    book['balance']=round(num(book['balance'])+pnl,8)
    pos['quantity']=max(0.0,remaining_qty-sell_qty)
    pos['remaining_cost_basis_usd']=max(0.0,remaining_basis-basis_sold)
    pos['partial_realized_pnl']=round(num(pos.get('partial_realized_pnl'))+pnl,8)
    exits=pos.setdefault('partial_exits',[])
    exits.append({'stage':label,'ts':now_ms(),'price':market_price,'execution_price':round(quote['fill_price'],12),
                  'quantity':sell_qty,'fraction_of_original':fraction,'pnl_usd':round(pnl,4),
                  'dex_fee_usd':round(quote['dex_fee_usd'],6),'network_fee_usd':round(quote['network_fee_usd'],6),
                  'price_impact_pct':round(quote['impact_pct'],4),
                  'move_pct':round((market_price-num(pos.get('entry_price')))/max(num(pos.get('entry_price')),1e-18)*100,3)})
    return pnl

def update_positions(flows,feed):
    # Use the shared discovery feed when it contains the exact held pool. If
    # the pool rotated out, refresh it independently without blocking this loop.
    prices={}
    for coin in feed or []:
        address=coin.get('address'); pair=coin.get('pairAddress')
        if address and pair and num(coin.get('priceUsd'))>0:
            prices[(address,pair)]=coin
    portfolio_setup=STATE.get('portfolio_setup') or {}
    exit_only_books=(portfolio_setup.get('legacy_draining_books') or {}).values()
    for book in [*STATE['books'].values(), *exit_only_books]:
        pos=book.get('position')
        if not pos: continue
        decision_at=now_ms()
        is_rush=pos.get('strategy_id',book.get('id'))==rush_brain.STRATEGY_ID
        coin=POSITION_MARK_FEED.resolve(pos,prices,decision_at)
        mark_stamp=num((coin or {}).get('mark_received_at'),num((coin or {}).get('updatedAt')))
        rush_mark_fresh=(coin and coin.get('address')==pos.get('address')
                         and coin.get('pairAddress')==pos.get('pairAddress')
                         and num(coin.get('priceUsd'))>0 and mark_stamp>0
                         and 0<=decision_at-mark_stamp<=POSITION_STALE_AFTER_MS)
        if not coin or (is_rush and not rush_mark_fresh):
            age=max(0,now_ms()-int(num(pos.get('updated_at'))))
            pos['quote_status']='stale' if age>POSITION_STALE_AFTER_MS else 'refreshing'
            pos['quote_age_ms']=age
            pos['quote_unavailable_reason']='exact_pool_not_in_recent_entry_feed'
            continue
        price=num(coin.get('priceUsd'))
        entry=num(pos['entry_price']); peak=max(num(pos.get('peak_price'),entry),price)
        pct=(price-entry)/entry*100; hold=(now_ms()-int(pos['opened_at']))/60000
        # Rush reversal signals require an available, recent window from this
        # held pool. Another pool of the mint cannot trigger an exit.
        f=flows.get((pos['address'],pos.get('pairAddress')),{}) if is_rush else flows.get(pos['address'],{})
        window_at=num(f.get('window_at')); available_at=num(f.get('available_at'))
        flow_fresh=(window_at>0 and available_at>0
                    and 0<=decision_at-window_at<=RUSH_FLOW_MAX_AGE_MS
                    and 0<=decision_at-available_at<=RUSH_FLOW_MAX_AGE_MS)
        reason=None

        remaining_qty=num(pos.get('quantity'))
        live_quote=exit_execution(coin,remaining_qty)
        remaining_basis=num(pos.get('remaining_cost_basis_usd'),num(pos.get('notional_usd'))+num(pos.get('entry_network_fee_usd')))
        open_pnl=live_quote['net_proceeds_usd']-remaining_basis
        total_live_pnl=num(pos.get('partial_realized_pnl'))+open_pnl
        total_live_pct=total_live_pnl/max(num(pos.get('notional_usd')),1e-18)*100

        # Rush's faster TEST exits use the same net cost model and 3% stop.
        # Standard books retain their 3/10/60 framework.
        peak_net=max(num(pos.get('peak_net_pct'),total_live_pct),total_live_pct)
        entry_liq=num(pos.get('entry_liquidity_usd'))
        current_liq=num(coin.get('liquidityUsd'),num((coin.get('liquidity') or {}).get('usd'),math.nan))
        if total_live_pct<=-STOP_LOSS:
            reason='STOP_LOSS_3_NET'
        elif is_rush and entry_liq>0 and math.isfinite(current_liq) and 0<=current_liq<entry_liq*RUSH_LIQ_MIN_FRACTION:
            reason='RUSH_LIQUIDITY_COLLAPSE'
        elif is_rush and flow_fresh and num(f.get('trades'))>=4 and num(f.get('ratio'))<.65 and num(f.get('sell_usd'))>num(f.get('buy_usd')):
            reason='RUSH_FLOW_REVERSAL'
        elif is_rush and peak_net>=RUSH_TRAIL_ARM and peak_net-total_live_pct>=RUSH_TRAIL_DRAWDOWN-1e-9:
            reason='RUSH_PROFIT_TRAIL'
        elif is_rush and total_live_pct>=RUSH_TAKE_PROFIT:
            reason='RUSH_TAKE_PROFIT_7_NET'
        elif not is_rush and total_live_pct>=TAKE_PROFIT:
            reason='TAKE_PROFIT_10_NET'
        elif is_rush and hold>=RUSH_MAX_HOLD_MIN:
            reason='RUSH_MAX_HOLD_20'
        elif not is_rush and hold>=MAX_HOLD_MIN:
            reason='ABSOLUTE_MAX_HOLD_60'

        marked_at=num(coin.get('mark_received_at'),now_ms())
        quote_age=max(0,now_ms()-int(marked_at))
        pos.update({'current_price':price,'peak_price':peak,'execution_exit_price':round(live_quote['fill_price'],12),
                    'pnl_pct':round(total_live_pct,3),'open_pnl_usd':round(open_pnl,4),
                    'estimated_exit_fee_usd':round(live_quote['dex_fee_usd']+live_quote['network_fee_usd'],6),
                    'estimated_exit_impact_pct':round(live_quote['impact_pct'],4),
                    'partial_realized_pnl':round(num(pos.get('partial_realized_pnl')),4),
                    'remaining_fraction':round(remaining_qty/max(num(pos.get('original_quantity'),remaining_qty),1e-18),4),
                    'updated_at':now_ms(),'mark_received_at':marked_at,
                    'mark_source':coin.get('mark_source','SHARED_LIVE_FEED_EXACT_POOL'),
                    'quote_status':'fresh' if quote_age<=POSITION_STALE_AFTER_MS else 'stale',
                    'quote_age_ms':quote_age,'quote_unavailable_reason':None})
        if is_rush: pos['peak_net_pct']=peak_net
        if reason: close_position(book,pos,coin,reason)
def maybe_open(feed,flows):
    now=now_ms()
    by_pair={}
    for c in feed:
        if activity.usable_feed_coin(c,now):
            key=(c['address'],c['pairAddress'])
            if key not in by_pair or num(c.get('updatedAt'))>num(by_pair[key].get('updatedAt')):
                by_pair[key]=c
    candidates=[(c,enrich(c,flows)) for c in by_pair.values()]
    # Continue gathering causal observations while the Rush book has a position;
    # another strategy's outcomes or number of polls do not affect this memory.
    rush_observations={
        (coin['address'],coin['pairAddress']):rush_brain.observe(coin,features,now)
        for coin,features in candidates
    }
    for strategy in STRATEGIES:
        book=STATE['books'][strategy['id']]
        if book.get('position'):
            continue
        if book.get('promotion_pending'):
            book['entry_diagnostics']={
                'at':now,'matched_candidates':0,'cost_rejected':0,
                'cooldown_rejected':0,'affordable_candidates':0,
                'price_verification_rejected':0,
                'blocked_reason':'promotion_waiting_for_existing_position_exit',
                'balance_usd':round(num(book.get('balance')),4),
            }
            continue
        if book.get('portfolio_group')=='PROMOTED_PAPER':
            pause_ms=promoted_pause_remaining_ms(book,now)
            if pause_ms>0:
                book['entry_diagnostics']={
                    'at':now,'matched_candidates':0,'cost_rejected':0,
                    'cooldown_rejected':0,'affordable_candidates':0,
                    'price_verification_rejected':0,
                    'blocked_reason':'promoted_recent_loss_cooldown',
                    'promoted_cooldown_remaining_ms':pause_ms,
                    'balance_usd':round(num(book.get('balance')),4),
                }
                continue
        balance=num(book.get('balance'))
        entry_limit=activity.entry_notional_limit(strategy['id'],balance,TRADE_NOTIONAL)
        if strategy['id'] in PROMOTED_STRATEGIES:
            entry_limit=min(entry_limit,balance*PROMOTED_MAX_POSITION_FRACTION)
        min_notional=activity.entry_minimum_notional(strategy['id'])
        if entry_limit<min_notional:
            book['entry_diagnostics']={
                'at':now,'matched_candidates':0,'cost_rejected':0,
                'cooldown_rejected':0,'affordable_candidates':0,
                'price_verification_rejected':0,
                'blocked_reason':('scalper_risk_cap_below_minimum'
                                  if strategy['id']=='SCALPER' else 'insufficient_balance'),
                'balance_usd':round(balance,4),'risk_limited_notional_usd':round(entry_limit,4),
            }
            continue
        eligible=[]
        checked=0
        signal_candidates=0
        blocked_cost=0
        blocked_price=0
        price_crosscheck_pending=0
        blocked_cooldown=0
        flow_rejected=0
        brain_rejected=0
        temporal_warmup=0
        risk_rejected=0
        candidate_risk_rejected=0
        rule=activity.RULES[strategy['id']]
        for coin,features in candidates:
            matched=(strategy['rule'](features) if book.get('portfolio_group')=='PROMOTED_PAPER'
                     else rule.matches(features))
            if not matched:
                if rule.matches(features,require_flow=False):
                    flow_rejected+=1
                continue
            signal_candidates+=1
            brain=None; risk=None; candidate_limit=entry_limit
            if strategy['id']==rush_brain.STRATEGY_ID:
                brain=rush_brain.evaluate(
                    book,coin,features,now,
                    temporal=rush_observations[(coin['address'],coin['pairAddress'])])
                if not brain['allow']:
                    brain_rejected+=1
                    temporal_warmup+=not (brain.get('temporal') or {}).get('ready',False)
                    continue
                # A broad research universe does not relax rug, identity or
                # freshness protections, even if Gecko's price check passes.
                risk=rug_guard.check(coin)
                checked_at=int(num(risk.get('checked_at')))
                if (risk.get('status')!='pass' or risk.get('mint')!=coin['address']
                        or risk.get('pair')!=coin['pairAddress']
                        or checked_at<=0 or not 0<=now-checked_at<=rug_guard.TTL_MS):
                    risk_rejected+=1
                    continue
                candidate_limit=rush_brain.candidate_notional_limit(brain,balance,entry_limit)
                if candidate_limit<min_notional:
                    candidate_risk_rejected+=1
                    continue
            validation=price_integrity.check(coin)
            if validation.get('status')=='review':
                validation=cached_jupiter_tiebreak(validation,coin,now=now)
                if validation.get('status')!='pass':
                    if validation.get('status')=='review':
                        price_crosscheck_pending+=1
                        schedule_jupiter_price_probe(coin)
                    else:
                        blocked_price+=1
                    continue
            if (validation.get('status')!='pass'
                    or (brain and (validation.get('mint')!=coin['address']
                                   or validation.get('pair')!=coin['pairAddress']))):
                blocked_price+=1; continue
            address=coin['address']
            if activity.cooldown_remaining_ms(book,address,now)>0:
                blocked_cooldown+=1
                continue
            checked+=1
            proposed=activity.affordable_entry(
                coin,balance,candidate_limit,entry_execution,exit_execution,
                minimum_notional=min_notional
            )
            if proposed is None:
                blocked_cost+=1
                continue
            eligible.append((proposed['initial_pnl_pct'],num(features.get('score')),
                             coin,features,proposed,validation,brain,risk,candidate_limit))
        book['entry_diagnostics']={
            'at':now,'signal_candidates':signal_candidates,
            'matched_candidates':checked,'cost_rejected':blocked_cost,
            'cooldown_rejected':blocked_cooldown,'affordable_candidates':len(eligible),
            'price_verification_rejected':blocked_price,
            'price_crosscheck_pending':price_crosscheck_pending,
            'flow_missing_candidates':flow_rejected,
            'risk_limited_notional_usd':round(entry_limit,4),
        }
        if strategy['id']==rush_brain.STRATEGY_ID:
            book['entry_diagnostics'].update({
                'brain_rejected_candidates':brain_rejected,
                'temporal_warmup_candidates':temporal_warmup,
                'risk_rejected_candidates':risk_rejected,
                'candidate_risk_rejected':candidate_risk_rejected,
                'research_target_win_rate_pct':rush_brain.TARGET_WIN_RATE_PCT,
                'target_is_guarantee':False,
            })
            if not eligible:
                if temporal_warmup: reason='temporal_warmup'
                elif risk_rejected: reason='risk_verification_unavailable'
                elif brain_rejected: reason='momentum_not_confirmed'
                elif candidate_risk_rejected: reason='rush_risk_cap_below_minimum'
                elif blocked_cost: reason='modeled_roundtrip_cost_limit'
                elif blocked_price or price_crosscheck_pending: reason='price_verification_unavailable'
                elif blocked_cooldown: reason='reentry_cooldown'
                else: reason='no_matching_market_candidate'
                book['entry_diagnostics']['blocked_reason']=reason
        if strategy['id']=='SCALPER':
            book['entry_diagnostics'].update({
                'flow_rejected_candidates':flow_rejected,
                'flow_tape_status':FLOW_TAPE_DIAGNOSTICS['status'],
                'flow_tape_coverage_pct':FLOW_TAPE_DIAGNOSTICS['coverage_pct'],
                'verified_flow_events_60s':FLOW_TAPE_DIAGNOSTICS['verified_events_60s'],
                'flow_tape_backlog':FLOW_TAPE_DIAGNOSTICS['backlog'],
            })
            if flow_rejected:
                book['entry_diagnostics']['blocked_reason']='verified_flow_unavailable'
        if not eligible:
            continue
        rank=(lambda item:(num((item[6] or {}).get('final_score')),item[0],item[1])) if strategy['id']==rush_brain.STRATEGY_ID else (lambda item:(item[0],item[1]))
        _,_,coin,features,proposed,validation,brain,risk,candidate_limit=max(eligible,key=rank)
        if brain:
            book['entry_diagnostics']['risk_limited_notional_usd']=round(candidate_limit,4)
        address=coin['address']; price=num(coin['priceUsd'])
        notional=proposed['notional']; opening=proposed['entry']; mark=proposed['mark']
        qty=num(opening['quantity'])
        capital_basis=num(opening['capital_committed_usd'])
        book['trade_seq']=int(book.get('trade_seq',0))+1
        stamp=now_ms()
        position={
            'trade_no':book['trade_seq'],'strategy_id':strategy['id'],
            'symbol':coin.get('symbol'),'name':coin.get('name'),
            'address':address,'pairAddress':coin['pairAddress'],'entry_price':price,
            'execution_entry_price':round(opening['fill_price'],12),
            'current_price':price,'peak_price':price,'quantity':qty,'original_quantity':qty,
            'notional_usd':notional,'opened_at':stamp,'updated_at':stamp,
            'score':coin.get('score'),'entry_features':features,
            'partial_realized_pnl':0.0,'partial_exits':[],
            'remaining_cost_basis_usd':capital_basis,
            'entry_dex_fee_bps':round(opening['dex_fee_bps'],4),
            'entry_dex_fee_usd':round(opening['dex_fee_usd'],6),
            'entry_network_fee_usd':round(opening['network_fee_usd'],6),
            'entry_price_impact_pct':round(opening['impact_pct'],4),
            'entry_slippage_pct':round(opening['slippage_pct']+opening['latency_pct'],4),
            'execution_mode':EXECUTION_MODEL_VERSION,
            'execution_source':'DEX_SPOT_WITH_MODELED_FRICTION',
            'quote_status':'fresh','mark_received_at':stamp,
            'mark_source':'SHARED_LIVE_FEED_EXACT_POOL','quote_age_ms':0,
            'price_crosscheck':validation,
            'entry_policy_version':activity.POLICY_VERSION,
            'entry_roundtrip_pnl_pct':round(proposed['initial_pnl_pct'],6),
            'entry_size_reduced':notional+0.02<candidate_limit,
            'pnl_pct':round(proposed['initial_pnl_pct'],3),
            'open_pnl_usd':round(proposed['initial_pnl_usd'],4),
            'execution_exit_price':round(mark['fill_price'],12),
            'remaining_fraction':1.0,
        }
        if brain:
            position['momentum_rush_brain']=brain
            position['risk_guard']=risk
            position['promotion_eligible']=False
            position['entry_liquidity_usd']=pair_liquidity_usd(coin)
            position['entry_market_cap_usd']=num(coin.get('marketCap') or coin.get('fdv'))
            position['peak_net_pct']=proposed['initial_pnl_pct']
        book['position']=position
        book.setdefault('last_entry_by_address',{})[address]=stamp


def stats(book):
    start=num(book.get('starting_balance'),START_BALANCE)
    h=book.get('history') or []; wins=[t for t in h if num(t.get('pnl_usd'))>0]
    gp=sum(max(0,num(t.get('pnl_usd'))) for t in h); gl=-sum(min(0,num(t.get('pnl_usd'))) for t in h)
    unreal=0.0
    p=book.get('position')
    if p: unreal=num(p.get('open_pnl_usd'))
    marked_at=num((p or {}).get('mark_received_at'),num((p or {}).get('updated_at')))
    mark_age_ms=max(0,now_ms()-int(marked_at)) if p else 0
    valuation_stale=bool(p and (mark_age_ms>POSITION_STALE_AFTER_MS or p.get('quote_status')=='stale'))
    equity=num(book.get('balance'))+unreal
    partial_count=sum(len(t.get('partial_exits') or []) for t in h)+len((p or {}).get('partial_exits') or [])
    locked_partial=sum(num(t.get('partial_realized_pnl')) for t in h)+num((p or {}).get('partial_realized_pnl'))
    return {'trades':len(h),'wins':len(wins),'losses':len(h)-len(wins),'win_rate':round(len(wins)/len(h)*100,1) if h else 0,
            'profit_factor':round(gp/gl,2) if gl>0 else None,
            'profit_factor_status':'finite' if gl>0 else ('infinite_no_losses' if gp>0 else 'undefined_no_results'),
            'realized_pnl':round(num(book.get('balance'))-start,2),
            'unrealized_pnl':round(unreal,2),'total_pnl':round(equity-start,2),
            'equity':round(equity,2),'return_pct':round((equity-start)/max(start,1e-18)*100,2),'open':bool(p),
            'valuation_stale':valuation_stale,'mark_age_ms':mark_age_ms,
            'partial_exits':partial_count,'partial_locked_pnl':round(locked_partial,2),
            'active_policy_trades':sum(t.get('entry_policy_version')==activity.POLICY_VERSION for t in h),
            'active_policy_wins':sum(t.get('entry_policy_version')==activity.POLICY_VERSION and num(t.get('pnl_usd'))>0 for t in h)}

def persist(status='online',error=None):
    STATE['status']=status; STATE['updated_at']=now_ms()
    STATE['stats']={k:stats(v) for k,v in STATE['books'].items()}
    STATE['data_integrity_note']='Историята съдържа непотвърдени цени, включително XFUN. Не е доказателство за реална доходност. Новите входове минават независима проверка.'
    STATE['execution_basis']=EXECUTION_MODEL_VERSION
    STATE['execution_note']='DEX exact-pool spot marks with modeled fees, impact, slippage and latency; paper estimate only, no transaction is built, signed, or sent.'
    STATE['activity_config']={**activity.policy_config(),'stop_loss_net_pct':STOP_LOSS,
                              'take_profit_net_pct':TAKE_PROFIT,'trade_limit_usd':TRADE_NOTIONAL,
                              'rush_brain_version':rush_brain.VERSION,
                              'rush_research_target_win_rate_pct':rush_brain.TARGET_WIN_RATE_PCT,
                              'rush_target_is_guarantee':False,
                              'rush_max_memory_closed_trades':rush_brain.MAX_MEMORY_TRADES,
                              'rush_max_memory_scan_rows':rush_brain.MAX_MEMORY_SCAN_ROWS,
                              'rush_low_cap_max_balance_fraction':rush_brain.LOW_CAP_MAX_BALANCE_FRACTION,
                              'rush_low_cap_max_notional_usd':rush_brain.LOW_CAP_MAX_NOTIONAL_USD}
    STATE['activity_config'].update({
        'rush_stop_loss_net_pct':STOP_LOSS,'rush_take_profit_net_pct':RUSH_TAKE_PROFIT,
        'rush_max_hold_minutes':RUSH_MAX_HOLD_MIN,'rush_trail_arm_net_pct':RUSH_TRAIL_ARM,
        'rush_trail_drawdown_net_pct':RUSH_TRAIL_DRAWDOWN,
        'rush_liquidity_min_fraction':RUSH_LIQ_MIN_FRACTION,
        'rush_flow_max_age_ms':RUSH_FLOW_MAX_AGE_MS})
    previous_setup=STATE.get('portfolio_setup') or {}
    default_setup_status='ACTIVE' if all(
        abs(num(STATE['books'][key].get('starting_balance'))-PROMOTED_ALLOCATION)<1e-8
        and not STATE['books'][key].get('history')
        and not STATE['books'][key].get('promotion_pending')
        for key in PROMOTED_STRATEGIES
    ) else 'UNCONFIGURED'
    STATE['portfolio_setup']={**previous_setup,
        'version':'PROMOTED_PAPER_COHORT_V1',
        'status':previous_setup.get('status') or default_setup_status,
        'group':'PROMOTED_PAPER',
        'total_allocated_capital_usd':PROMOTED_TOTAL_CAPITAL,
        'allocation_per_strategy_usd':PROMOTED_ALLOCATION,
        'max_position_fraction':PROMOTED_MAX_POSITION_FRACTION,
        'strategies':list(PROMOTED_STRATEGIES),
        'accounts_are_independent':True,
        'real_execution_enabled':False,
        'legacy_open_positions':sorted(
            key for key,book in (previous_setup.get('legacy_draining_books') or {}).items()
            if book.get('position')
        ),
    }
    if error: STATE['error']=str(error)[:200]
    else: STATE.pop('error',None)
    published=merge_paired_snapshot(merge_astra_snapshot(STATE))
    atomic_write(published)
    atomic_write_path(COMPACT_PATH,compact_strategy_lab(published))

def main():
    global STATE
    STATE=load_state()
    if (STATE.get('portfolio_setup') or {}).get('status')=='DRAINING':
        try:
            promote_strategy_lab(STATE_PATH.parent)
            STATE=load_state()
        except Exception as e:
            setup=STATE.get('portfolio_setup') or {}
            setup['promotion_error']=str(e)[:200]
            STATE['portfolio_setup']=setup
    if STATE.get('activity_version')!=activity.POLICY_VERSION:
        STATE['activity_version']=activity.POLICY_VERSION
        STATE['activity_started_at']=now_ms()
    last_entry=0
    feed=[]
    while True:
        started=time.time()
        try:
            flows=flow_map()
            refresh_due=time.time()-last_entry>=ENTRY_REFRESH_SECONDS
            if refresh_due:
                r=SESSION.get(API_URL,timeout=5); r.raise_for_status()
                feed=r.json().get('feed') or []
                last_entry=time.time()
            update_positions(flows,feed)
            if refresh_due:
                maybe_open(feed,flows)
            persist('online')
        except Exception as e:
            persist('degraded',e)
        time.sleep(max(.25,POLL_SECONDS-(time.time()-started)))

if __name__=='__main__': main()
