#!/usr/bin/env python3
import json, math, os, time
from pathlib import Path
from typing import Any, Callable
import requests
from astra_lab_bridge import merge_astra_snapshot
from lab_paired_bridge import merge_paired_snapshot
import lab_activity as activity
import pair_price_integrity as price_integrity
from lab_dashboard_projection import compact_strategy_lab

API_URL=os.getenv('NEO_LOCAL_API','http://127.0.0.1:8788/state')
DEX='https://api.dexscreener.com'
STATE_PATH=Path(os.getenv('NEO_STRATEGY_LAB_PATH','/var/lib/neo-market/strategy_lab.json'))
COMPACT_PATH=Path(os.getenv('NEO_STRATEGY_LAB_COMPACT_PATH',str(STATE_PATH.parent/'strategy_lab_compact.json')))
RESET_FLAG_PATH=Path(os.getenv('NEO_STRATEGY_LAB_RESET_FLAG','/var/lib/neo-market/strategy_lab.reset'))
START_BALANCE=float(os.getenv('NEO_LAB_START_BALANCE','500'))
STRATEGY_START_BALANCES={'SCALPER':float(os.getenv('NEO_LAB_SCALPER_START_BALANCE','100'))}
TRADE_NOTIONAL=float(os.getenv('NEO_LAB_TRADE_NOTIONAL','150'))
POLL_SECONDS=float(os.getenv('NEO_LAB_POLL_SECONDS','2'))
ENTRY_REFRESH_SECONDS=float(os.getenv('NEO_LAB_ENTRY_REFRESH_SECONDS','2'))
STOP_LOSS=3.0
TAKE_PROFIT=10.0
TRAILING=4.0
MAX_HOLD_MIN=60.0
REENTRY_COOLDOWN_MIN=activity.REENTRY_SECONDS/60.0

# Realistic paper execution costs for Strategy Lab. These are applied equally to
# every strategy so comparisons stay fair. The main engine remains untouched.
GENERIC_DEX_FEE_BPS=float(os.getenv('NEO_LAB_GENERIC_DEX_FEE_BPS','30'))
BASE_SLIPPAGE_BPS=float(os.getenv('NEO_LAB_BASE_SLIPPAGE_BPS','10'))
LATENCY_BUFFER_BPS=float(os.getenv('NEO_LAB_LATENCY_BUFFER_BPS','10'))
NETWORK_FEE_SOL=float(os.getenv('NEO_LAB_NETWORK_FEE_SOL','0.0001'))
MAX_PRICE_IMPACT_PCT=float(os.getenv('NEO_LAB_MAX_PRICE_IMPACT_PCT','20'))

SESSION=requests.Session()
SESSION.headers.update({'user-agent':'NEO-Strategy-Lab/1.0','accept':'application/json'})

def now_ms(): return int(time.time()*1000)
def num(v,d=0.0):
    try:
        x=float(v)
        return x if math.isfinite(x) else d
    except Exception: return d

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
    tape=load_json(Path('/var/lib/neo-market/live_tape.json'),{})
    cutoff=now_ms()-60_000
    out={}
    for e in tape.get('events',[]):
        if int(e.get('ts',0))<cutoff: continue
        a=e.get('address')
        if not a: continue
        f=out.setdefault(a,{'trades':0,'buys':0,'sells':0,'buy_usd':0.0,'sell_usd':0.0,'wallets':set(),'max_sell':0.0})
        usd=num(e.get('usd_amount')); f['trades']+=1
        if e.get('wallet'): f['wallets'].add(e['wallet'])
        if e.get('direction')=='BUY': f['buys']+=1; f['buy_usd']+=usd
        else: f['sells']+=1; f['sell_usd']+=usd; f['max_sell']=max(f['max_sell'],usd)
    for f in out.values():
        f['unique_wallets']=len(f.pop('wallets')); f['ratio']=f['buy_usd']/max(f['sell_usd'],1)
    return out
def enrich(c,flows):
    tx=(c.get('txns') or {}).get('m5') or {}
    b=num(tx.get('buys')); s=num(tx.get('sells'))
    liq=num(c.get('liquidityUsd')); mc=num(c.get('marketCap') or c.get('fdv'))
    pc=c.get('priceChange') or {}
    vol1h=num((c.get('volume') or {}).get('h1'))
    return {
      'score':num(c.get('score')),'liq':liq,'m5':num(pc.get('m5')),'h1':num(pc.get('h1')),
      'bs':b/max(s,1),'lmc':liq/max(mc,1),'age':num(c.get('ageMinutes'),999999),
      'vol1h':vol1h,'vol_liq':vol1h/max(liq,1),
      'flow':flows.get(c.get('address'),{'trades':0,'buys':0,'sells':0,'buy_usd':0,'sell_usd':0,'unique_wallets':0,'ratio':0,'max_sell':0})
    }

STRATEGIES=[
 {'id':'ULTRA_PRECISION','name':'Ultra Precision','rule':lambda f: f['score']>=98 and f['liq']>=25000 and 3<=f['m5']<=18 and 1.1<=f['bs']<=3.0 and f['lmc']>=.15 and 8<=f['age']<=180},
 {'id':'PRECISION','name':'Precision','rule':lambda f: f['score']>=95 and f['liq']>=20000 and 2<=f['m5']<=22 and 1.0<=f['bs']<=3.2 and f['lmc']>=.12 and 5<=f['age']<=240},
 {'id':'MOMENTUM','name':'Momentum','rule':lambda f: f['score']>=90 and f['liq']>=15000 and 5<=f['m5']<=30 and f['bs']>=1.15 and f['lmc']>=.08 and 3<=f['age']<=300},
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
    for s in STRATEGIES:
        b=(raw.get('books') or {}).get(s['id']) or empty_book(s)
        b['id']=s['id']; b['name']=s['name']; books[s['id']]=b
    return {'started_at':now_ms() if reset_requested else (raw.get('started_at') or now_ms()),
            'updated_at':now_ms(),'status':'starting','books':books,
            'activity_version':raw.get('activity_version'),
            'activity_started_at':raw.get('activity_started_at')}

STATE={'started_at':now_ms(),'updated_at':now_ms(),'status':'starting',
       'books':{s['id']:empty_book(s) for s in STRATEGIES}}
if STATE.get('activity_version')!=activity.POLICY_VERSION:
    STATE['activity_version']=activity.POLICY_VERSION
    STATE['activity_started_at']=now_ms()
assert set(activity.RULES)=={s['id'] for s in STRATEGIES}, 'All 33 entries need a policy'

def dex_position_prices(positions):
    if not positions: return {}
    addresses=list(dict.fromkeys(p.get('address') for p in positions if p.get('address')))
    wanted={(p.get('address'),p.get('pairAddress')) for p in positions if p.get('address') and p.get('pairAddress')}
    out={}
    for i in range(0,len(addresses),30):
        batch=addresses[i:i+30]
        try:
            r=SESSION.get(DEX+'/tokens/v1/solana/'+','.join(batch),timeout=(1,3))
            r.raise_for_status();payload=r.json()
            rows=payload if isinstance(payload,list) else []
        except (requests.RequestException,ValueError):continue
        for p in rows:
            a=(p.get('baseToken') or {}).get('address')
            pair=p.get('pairAddress')
            if (a,pair) in wanted:
                price=num(p.get('priceUsd'))
                if price>0: out[(a,pair)]=p
    return out

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
           'execution_mode':'REALISTIC_COSTS_V1'}
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

def update_positions(flows):
    positions=[b['position'] for b in STATE['books'].values() if b.get('position')]
    prices=dex_position_prices(positions) if positions else {}
    for book in STATE['books'].values():
        pos=book.get('position')
        if not pos: continue
        pair_key=(pos.get('address'),pos.get('pairAddress'))
        coin=prices.get(pair_key)
        if not coin: continue
        price=num(coin.get('priceUsd'))
        entry=num(pos['entry_price']); peak=max(num(pos.get('peak_price'),entry),price)
        pct=(price-entry)/entry*100; hold=(now_ms()-int(pos['opened_at']))/60000
        f=flows.get(pos['address'],{})
        reason=None

        remaining_qty=num(pos.get('quantity'))
        live_quote=exit_execution(coin,remaining_qty)
        remaining_basis=num(pos.get('remaining_cost_basis_usd'),num(pos.get('notional_usd'))+num(pos.get('entry_network_fee_usd')))
        open_pnl=live_quote['net_proceeds_usd']-remaining_basis
        total_live_pnl=num(pos.get('partial_realized_pnl'))+open_pnl
        total_live_pct=total_live_pnl/max(num(pos.get('notional_usd')),1e-18)*100

        # Unified 3:10 NET exit framework across all 33 strategies.
        # Entry logic stays strategy-specific; exits are identical and include
        # DEX fee, price impact, slippage/latency and network cost.
        if total_live_pct<=-STOP_LOSS:
            reason='STOP_LOSS_3_NET'
        elif total_live_pct>=TAKE_PROFIT:
            reason='TAKE_PROFIT_10_NET'
        elif hold>=MAX_HOLD_MIN:
            reason='ABSOLUTE_MAX_HOLD_60'

        pos.update({'current_price':price,'peak_price':peak,'execution_exit_price':round(live_quote['fill_price'],12),
                    'pnl_pct':round(total_live_pct,3),'open_pnl_usd':round(open_pnl,4),
                    'estimated_exit_fee_usd':round(live_quote['dex_fee_usd']+live_quote['network_fee_usd'],6),
                    'estimated_exit_impact_pct':round(live_quote['impact_pct'],4),
                    'partial_realized_pnl':round(num(pos.get('partial_realized_pnl')),4),
                    'remaining_fraction':round(remaining_qty/max(num(pos.get('original_quantity'),remaining_qty),1e-18),4),
                    'updated_at':now_ms()})
        if reason: close_position(book,pos,coin,reason)
def maybe_open(feed,flows):
    now=now_ms()
    candidates=[]
    for c in feed:
        if activity.usable_feed_coin(c,now):
            candidates.append((c,enrich(c,flows)))
    for strategy in STRATEGIES:
        book=STATE['books'][strategy['id']]
        if book.get('position') or num(book.get('balance'))<activity.MIN_NOTIONAL_USD:
            continue
        eligible=[]
        checked=0
        blocked_cost=0
        blocked_price=0
        blocked_cooldown=0
        for coin,features in candidates:
            if not activity.RULES[strategy['id']].matches(features):
                continue
            validation=price_integrity.check(coin)
            if validation.get('status')!='pass':
                blocked_price+=1; continue
            address=coin['address']
            if activity.cooldown_remaining_ms(book,address,now)>0:
                blocked_cooldown+=1
                continue
            checked+=1
            proposed=activity.affordable_entry(
                coin,num(book['balance']),TRADE_NOTIONAL,entry_execution,exit_execution
            )
            if proposed is None:
                blocked_cost+=1
                continue
            eligible.append((proposed['initial_pnl_pct'],num(features.get('score')),
                             coin,features,proposed))
        book['entry_diagnostics']={
            'at':now,'matched_candidates':checked,'cost_rejected':blocked_cost,
            'cooldown_rejected':blocked_cooldown,'affordable_candidates':len(eligible),
            'price_verification_rejected':blocked_price,
        }
        if not eligible:
            continue
        _,_,coin,features,proposed=max(eligible,key=lambda item:(item[0],item[1]))
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
            'execution_mode':'REALISTIC_COSTS_V1',
            'price_crosscheck':price_integrity.check(coin),
            'entry_policy_version':activity.POLICY_VERSION,
            'entry_roundtrip_pnl_pct':round(proposed['initial_pnl_pct'],6),
            'entry_size_reduced':notional+0.02<min(TRADE_NOTIONAL,num(book['balance'])),
            'pnl_pct':round(proposed['initial_pnl_pct'],3),
            'open_pnl_usd':round(proposed['initial_pnl_usd'],4),
            'execution_exit_price':round(mark['fill_price'],12),
            'remaining_fraction':1.0,
        }
        book['position']=position
        book.setdefault('last_entry_by_address',{})[address]=stamp


def stats(book):
    start=num(book.get('starting_balance'),START_BALANCE)
    h=book.get('history') or []; wins=[t for t in h if num(t.get('pnl_usd'))>0]
    gp=sum(max(0,num(t.get('pnl_usd'))) for t in h); gl=-sum(min(0,num(t.get('pnl_usd'))) for t in h)
    unreal=0.0
    p=book.get('position')
    if p: unreal=num(p.get('open_pnl_usd'))
    equity=num(book.get('balance'))+unreal
    partial_count=sum(len(t.get('partial_exits') or []) for t in h)+len((p or {}).get('partial_exits') or [])
    locked_partial=sum(num(t.get('partial_realized_pnl')) for t in h)+num((p or {}).get('partial_realized_pnl'))
    return {'trades':len(h),'wins':len(wins),'losses':len(h)-len(wins),'win_rate':round(len(wins)/len(h)*100,1) if h else 0,
            'profit_factor':round(gp/gl,2) if gl>0 else None,
            'profit_factor_status':'finite' if gl>0 else ('infinite_no_losses' if gp>0 else 'undefined_no_results'),
            'realized_pnl':round(num(book.get('balance'))-start,2),
            'equity':round(equity,2),'return_pct':round((equity-start)/max(start,1e-18)*100,2),'open':bool(p),
            'partial_exits':partial_count,'partial_locked_pnl':round(locked_partial,2),
            'active_policy_trades':sum(t.get('entry_policy_version')==activity.POLICY_VERSION for t in h),
            'active_policy_wins':sum(t.get('entry_policy_version')==activity.POLICY_VERSION and num(t.get('pnl_usd'))>0 for t in h)}

def persist(status='online',error=None):
    STATE['status']=status; STATE['updated_at']=now_ms()
    STATE['stats']={k:stats(v) for k,v in STATE['books'].items()}
    STATE['data_integrity_note']='Историята съдържа непотвърдени цени, включително XFUN. Не е доказателство за реална доходност. Новите входове минават независима проверка.'
    STATE['activity_config']={**activity.policy_config(),'stop_loss_net_pct':STOP_LOSS,
                              'take_profit_net_pct':TAKE_PROFIT,'trade_limit_usd':TRADE_NOTIONAL}
    if error: STATE['error']=str(error)[:200]
    else: STATE.pop('error',None)
    published=merge_paired_snapshot(merge_astra_snapshot(STATE))
    atomic_write(published)
    atomic_write_path(COMPACT_PATH,compact_strategy_lab(published))

def main():
    global STATE
    STATE=load_state()
    if STATE.get('activity_version')!=activity.POLICY_VERSION:
        STATE['activity_version']=activity.POLICY_VERSION
        STATE['activity_started_at']=now_ms()
    last_entry=0
    while True:
        started=time.time()
        try:
            flows=flow_map()
            update_positions(flows)
            if time.time()-last_entry>=ENTRY_REFRESH_SECONDS:
                r=SESSION.get(API_URL,timeout=5); r.raise_for_status()
                maybe_open(r.json().get('feed') or [],flows); last_entry=time.time()
            persist('online')
        except Exception as e:
            persist('degraded',e)
        time.sleep(max(.25,POLL_SECONDS-(time.time()-started)))

if __name__=='__main__': main()
