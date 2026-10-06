"""Versioned paper-only hypotheses, with matched entries and independent exits."""
import math
from dataclasses import dataclass
from typing import Any

VERSION = 'LAB_PAIRED_EXITS_V2'
MODEL = 'ESTIMATED_PAPER_COSTS_NOT_LIVE_FILLS'
COOLDOWN_MS = 20 * 60_000
MAX_HOLD_MS = 60 * 60_000
ARMS = ('CONTROL', 'EARLY', 'PROTECT', 'LEGACY_NET')
ARM_LABELS = {
    'CONTROL': 'Контрол −3% / +10%',
    'EARLY': 'Ранен изход при слаб поток',
    'PROTECT': 'Ранен изход + защита на печалбата',
    'LEGACY_NET': 'Стар стил cost-aware −4% / +18% + trail / 7m',
}

@dataclass(frozen=True)
class Group:
    id: str
    name: str
    parent: str
    max_cost: float

GROUPS = (
    Group('FLOW_EXIT_AB_V1', 'Order Flow · matched exits V2', 'ORDER_FLOW', 2.75),
    Group('MICRO_BREAKOUT_V3', 'Micro Breakout · matched exits V2', 'MICRO_BREAKOUT_SAFE', 2.2),
    Group('ULTRA_PRECISION_V3', 'Ultra Precision · matched exits V2', 'ULTRA_PRECISION', 2.2),
    Group('EARLY_EXIT_AB_V2', 'Early Runner · matched exits V2', 'EARLY', 2.75),
    Group('MOMENTUM_EXIT_AB_V2', 'Momentum · matched exits V2', 'MOMENTUM', 2.75),
    Group('PRECISION_EXIT_AB_V2', 'Precision · matched exits V2', 'PRECISION', 2.75),
)

def number(value: Any, default=0.0):
    try:
        v=float(value)
        return v if math.isfinite(v) else default
    except (ValueError, TypeError, OverflowError): return default


def flow_window(tape, mint, pair, seconds, now):
    """Exact pool identity, deduplicated events, no future timestamps."""
    seen=set(); rows=[]
    for e in tape.get('events', []):
        ts=number(e.get('ts'))
        if e.get('address')!=mint or e.get('pairAddress')!=pair or not now-seconds*1000 <= ts <= now: continue
        if e.get('direction') not in ('BUY','SELL') or number(e.get('usd_amount'))<=0: continue
        identity=(e.get('signature'),mint,pair,e.get('direction'),ts,e.get('wallet'),e.get('usd_amount'),e.get('token_amount'))
        if identity in seen: continue
        seen.add(identity); rows.append(e)
    buys=[e for e in rows if e['direction']=='BUY']; sells=[e for e in rows if e['direction']=='SELL']
    b=sum(number(e['usd_amount']) for e in buys); s=sum(number(e['usd_amount']) for e in sells)
    latest=max((int(e['ts']) for e in rows), default=0)
    wallets=len({e['wallet'] for e in rows if e.get('wallet')})
    return {'seconds':seconds,'trades':len(rows),'buys':len(buys),'sells':len(sells),
            'buy_usd':b,'sell_usd':s,'net_buy_usd':b-s,'ratio':b/max(s,1),
            'unique_wallets':wallets,'max_sell':max((number(e['usd_amount']) for e in sells),default=0),
            'latest_at':latest,'evidence_key':str(latest)+':'+str(len(rows))+':'+str(round(b+s,4)),
            'fresh':tape.get('status')=='online' and 0<=now-number(tape.get('updated_at'))<=15_000 and latest>0 and 0<=now-latest<=15_000}


def features(coin, flow):
    tx=(coin.get('txns') or {}).get('m5') or {}; pc=coin.get('priceChange') or {}
    liq=number(coin.get('liquidityUsd')); cap=number(coin.get('marketCap') or coin.get('fdv'))
    volume=number((coin.get('volume') or {}).get('h1'))
    return {'score':number(coin.get('score')), 'liq':liq, 'm5':number(pc.get('m5'),math.nan),
            'h1':number(pc.get('h1'),math.nan), 'bs':number(tx.get('buys'))/max(number(tx.get('sells')),1),
            'lmc':liq/max(cap,1), 'age':number(coin.get('ageMinutes'),math.inf),
            'vol1h':volume,'vol_liq':volume/max(liq,1), 'flow':flow}


def entry_rejections(group, f):
    reasons=[]; flow=f['flow']
    if group.id=='MICRO_BREAKOUT_V3':
        if not 2<=f['age']<=720: reasons.append('age_cap')
        if not 3<=f['m5']<=18: reasons.append('overextended')
        if not flow.get('fresh') or flow['trades']<5 or flow['unique_wallets']<3 or flow['buy_usd']<150: reasons.append('flow_activity')
    if group.id=='ULTRA_PRECISION_V3':
        if f['m5']>18: reasons.append('overextended')
        # Missing/thin data is UNKNOWN, not negative. Never call it safe.
        observed=(flow.get('fresh') and flow['trades']>=3 and flow['unique_wallets']>=2 and flow['buy_usd']+flow['sell_usd']>=100)
        if observed and flow['net_buy_usd']<0 and flow['ratio']<1: reasons.append('negative_observed_flow')
    return reasons


def conviction(fast, slow):
    # An evidence score, not a probability of profit.
    result=50.0
    for flow,weight in ((fast,25),(slow,20)):
        if not flow.get('fresh') or flow.get('trades',0)<3: continue
        ratio=number(flow.get('ratio'))
        result+=weight if ratio>=2 else weight*.5 if ratio>=1.3 else -weight if ratio<.8 else -weight*.5 if ratio<1 else 0
    return max(0.,min(100.,result))


def exit_decision(position, arm, net_pct, fast, slow, now):
    """Mutates only this experiment position. Always fill at the observed model price."""
    if not math.isfinite(net_pct): return None
    peak=max(number(position.get('peak_net_pct'), net_pct),net_pct)
    position['peak_net_pct']=peak

    # Cost-aware reconstruction of the pre-unification exit shape. This is a
    # research arm only: matched entry, same observed mark, no target-price fill.
    if arm=='LEGACY_NET':
        if net_pct<=-4: return 'STOP_LOSS_4_NET'
        if net_pct>=18: return 'TAKE_PROFIT_18_NET'
        if now-position['opened_at']>=7*60_000: return 'MAX_HOLD_7'
        if peak>=10 and net_pct<4: return 'NET_PROFIT_PROTECT_4'
        if peak>=6 and net_pct<=peak-4: return 'TRAILING_STOP_4_NET'
        return None

    if net_pct<=-3: return 'STOP_LOSS_3_NET'
    if net_pct>=10: return 'TAKE_PROFIT_10_NET'
    if now-position['opened_at']>=MAX_HOLD_MS: return 'MAX_HOLD_60'
    if arm=='CONTROL': return None
    if arm=='PROTECT' and peak>=2:
        floor=.5 if peak<3 else 1.0 if peak<5 else max(2.5,peak-2)
        position['profit_floor_net_pct']=floor
        if net_pct<=floor: return 'NET_PROFIT_PROTECTION'
    adequate=(fast.get('fresh') and slow.get('fresh') and fast.get('trades',0)>=3
              and slow.get('trades',0)>=5 and fast.get('unique_wallets',0)>=2
              and slow.get('unique_wallets',0)>=3 and fast.get('buy_usd',0)+fast.get('sell_usd',0)>=100
              and slow.get('buy_usd',0)+slow.get('sell_usd',0)>=200
              and fast.get('latest_at',0)>position['opened_at'])
    score=conviction(fast,slow); position['current_flow_score']=score
    weak=adequate and fast.get('net_buy_usd',0)<0 and slow.get('net_buy_usd',0)<0 and (
        fast.get('ratio',1)<.7 and slow.get('ratio',1)<.9 or
        score<45 and number(position.get('entry_flow_score'))-score>=25 and fast.get('ratio',1)<.8)
    if not weak or now-position['opened_at']<15_000:
        position.pop('weak_since',None);position.pop('weak_evidence',None);position.pop('weak_updates',None)
        return None
    if 'weak_since' not in position:
        position.update(weak_since=now,weak_evidence=fast.get('evidence_key'),weak_updates=1)
    elif fast.get('evidence_key')!=position.get('weak_evidence'):
        position['weak_updates']=position.get('weak_updates',1)+1
        position['weak_evidence']=fast.get('evidence_key')
    if now-position['weak_since']>=6_000 and position.get('weak_updates',0)>=2: return 'FLOW_DETERIORATION'
    return None


def book_stats(book):
    h=book['history']; wins=[t for t in h if t['pnl_usd']>0]; losses=[t for t in h if t['pnl_usd']<0]
    gp=sum(t['pnl_usd'] for t in wins); gl=-sum(t['pnl_usd'] for t in losses)
    pnl=sum(t['pnl_usd'] for t in h); p=book.get('position'); unreal=number((p or {}).get('open_pnl_usd'))
    equity=book['balance']+unreal
    return {'trades':len(h),'wins':len(wins),'losses':len(losses),'breakeven':len(h)-len(wins)-len(losses),
            'win_rate':len(wins)/len(h)*100 if h else 0.,'profit_factor':gp/gl if gl else None,
            'gross_profit':gp,'gross_loss':gl,'realized_pnl':pnl,'equity':equity,
            'return_pct':(equity/book['starting_balance']-1)*100,'open':bool(p),'valuation_stale':bool(p and p.get('quote_status')!='fresh'),
            'mean_pnl_usd':pnl/len(h) if h else None,
            'mean_roundtrip_cost_pct':-sum(t['entry_roundtrip_pnl_pct'] for t in h)/len(h) if h else None,
            'average_hold_seconds':sum((t['closed_at']-t['opened_at'])/1000 for t in h)/len(h) if h else None,
            'stop_overshoots':sum(t['exit_reason']=='STOP_LOSS_3_NET' and t['pnl_pct'] < -3.5 for t in h)}
