"""Frozen legacy Lab cost model. ESTIMATES ONLY: no swap submission, no Jupiter calls.
Parity with the original functions is verified by tests.
"""
import math, os

GENERIC_DEX_FEE_BPS=float(os.getenv('NEO_LAB_GENERIC_DEX_FEE_BPS','30'))

BASE_SLIPPAGE_BPS=float(os.getenv('NEO_LAB_BASE_SLIPPAGE_BPS','10'))

LATENCY_BUFFER_BPS=float(os.getenv('NEO_LAB_LATENCY_BUFFER_BPS','10'))

NETWORK_FEE_SOL=float(os.getenv('NEO_LAB_NETWORK_FEE_SOL','0.0001'))

MAX_PRICE_IMPACT_PCT=float(os.getenv('NEO_LAB_MAX_PRICE_IMPACT_PCT','20'))

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
