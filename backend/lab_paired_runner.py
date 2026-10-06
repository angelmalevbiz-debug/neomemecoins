#!/usr/bin/env python3
"""Independent matched paper experiments. No wallets, signing, swaps or live-state writes."""
import copy
import compat_file_lock as fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import signal
import tempfile
import threading
import time
from zoneinfo import ZoneInfo
from datetime import datetime
import requests
import lab_activity as activity
import lab_paired_costs as costs
import lab_paired_policy as policy
import pair_price_integrity as integrity

ROOT=Path(os.getenv('NEO_PAIRED_DIR','/var/lib/neo-lab-paired'))
API=os.getenv('NEO_PAIRED_FEED_URL','http://127.0.0.1:8788/state')
SOL='So11111111111111111111111111111111111111112'
STOP=threading.Event()


def now_ms(): return int(time.time()*1000)
def digest(value): return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
def day_key(now): return datetime.fromtimestamp(now/1000,ZoneInfo('Europe/Sofia')).date().isoformat()


def atomic(path,data):
    path.parent.mkdir(parents=True,exist_ok=True)
    encoded=json.dumps(data,ensure_ascii=False,allow_nan=False)
    fd,name=tempfile.mkstemp(prefix='.'+path.name+'.',dir=path.parent)
    try:
        with os.fdopen(fd,'w',encoding='utf-8') as f:
            f.write(encoded);f.flush();os.fsync(f.fileno())
        os.replace(name,path)
    finally:
        if os.path.exists(name): os.unlink(name)


def reference_check(coin,now):
    # Consume an existing exact-pool reference; never enqueue extra requests.
    mint=coin.get('address','');pair=coin.get('pairAddress','')
    if not activity.ADDRESS.fullmatch(mint) or not activity.ADDRESS.fullmatch(pair):
        return {'status':'blocked','reason':'invalid_identity'}
    path=Path('/var/lib/neo-market/price-crosscheck')/(mint+'-'+pair+'.json')
    try: return integrity.validate(coin,json.loads(path.read_text(encoding='utf-8')),now)
    except (OSError,ValueError,KeyError,TypeError): return {'status':'pending','reason':'reference_unavailable'}


def entry_size(coin,balance,cap):
    # Same size and cost model for all arms. No leverage, no target-price fills.
    size=math.floor(min(150.,balance-.1)*100)/100
    while size>=10:
        opening=costs.entry_execution(coin,size)
        mark=costs.exit_execution(coin,opening['quantity'])
        committed=opening['capital_committed_usd']
        initial=(mark['net_proceeds_usd']-committed)/size*100
        if math.isfinite(initial) and -cap<=initial<=0 and committed<=balance:
            return {'notional':size,'opening':opening,'mark':mark,'initial_pct':initial}
        next_size=math.floor(max(10.,size*.85)*100)/100
        if next_size>=size: break
        size=next_size
    return None


class ExperimentRunner:
    def __init__(self,root,now,reference=reference_check):
        self.root=Path(root); self.reference=reference
        if self.root.resolve()==Path('/var/lib/neo-market').resolve():
            raise ValueError('An independent experiment directory is required')
        self.path=self.root/'paired_state.json'
        if self.path.exists():
            # Corrupt state must halt, never silently start over.
            self.state=json.loads(self.path.read_text(encoding='utf-8'))
            if self.state.get('version')!=policy.VERSION or not isinstance(self.state.get('groups'),dict):
                raise ValueError('Unexpected experiment state; refusing reset')
            expected={g.id for g in policy.GROUPS}
            if set(self.state['groups'])!=expected: raise ValueError('Group mismatch; refusing reset')
            for group in self.state['groups'].values():
                if set(group['books'])!=set(policy.ARMS): raise ValueError('Arm mismatch')
                for book in group['books'].values():
                    if not isinstance(book['history'],list) or not math.isfinite(book['balance']): raise ValueError('Invalid book')
        else:
            groups={}
            for spec in policy.GROUPS:
                groups[spec.id]={'id':spec.id,'name':spec.name,'parent':spec.parent,'sequence':0,
                    'max_cost_pct':spec.max_cost,'episode':None,'completed':[], 'last_closed_by_mint':{},
                    'diagnostics':{},'day':day_key(now),'day_start_balances':{arm:500. for arm in policy.ARMS},
                    'books':{arm:{'id':spec.id+'_'+arm,'name':policy.ARM_LABELS[arm],
                        'starting_balance':500.,'balance':500.,'position':None,'history':[]} for arm in policy.ARMS}}
            self.state={'version':policy.VERSION,'started_at':now,'updated_at':now,'status':'starting',
                        'execution_basis':policy.MODEL,'paper_only':True,'groups':groups}

    def mark_stale(self,now):
        for group in self.state['groups'].values():
            for book in group['books'].values():
                p=book.get('position')
                if p:
                    p['quote_status']='stale';p.setdefault('stale_since',now)

    def update_positions(self,prices,tape,now):
        for group in self.state['groups'].values():
            for arm,book in group['books'].items():
                p=book.get('position')
                if p is None: continue
                coin=prices.get((p['address'],p['pairAddress']))
                if not coin or not 0<=now-policy.number(coin.get('received_at'))<=5_000 or policy.number(coin.get('priceUsd'))<=0:
                    p['quote_status']='stale';p.setdefault('stale_since',now);continue
                if coin.get('address')!=p['address'] or coin.get('pairAddress')!=p['pairAddress']:
                    p['quote_status']='stale';p.setdefault('stale_since',now);continue
                if coin.get('quoteTokenAddress')!=SOL or policy.number(coin.get('priceNative'))<=0 or policy.number(coin.get('liquidityUsd'))<=0:
                    p['quote_status']='stale';p.setdefault('stale_since',now);continue
                q=costs.exit_execution(coin,p['quantity'])
                pnl=q['net_proceeds_usd']-p['capital_committed_usd']; pct=pnl/p['notional_usd']*100
                if not math.isfinite(pct): p['quote_status']='stale';continue
                fast=policy.flow_window(tape,p['address'],p['pairAddress'],30,now)
                slow=policy.flow_window(tape,p['address'],p['pairAddress'],300,now)
                reason=policy.exit_decision(p,arm,pct,fast,slow,now)
                if p.get('stale_since') and now-p['stale_since']>=30_000:
                    reason=reason or 'STALE_DATA_RECOVERY'
                p.pop('stale_since',None)
                p.update(current_price=policy.number(coin['priceUsd']),execution_exit_price=q['fill_price'],
                    open_pnl_usd=pnl,pnl_usd=pnl,pnl_pct=pct,updated_at=now,quote_status='fresh',
                    estimated_exit_price_impact_pct=q['impact_pct'],current_exit_network_fee_usd=q['network_fee_usd'],
                    quote_received_at=coin['received_at'],provider_market_timestamp_available=False,
                    last_flow={'fast':fast,'slow':slow})
                if reason:
                    before=book['balance']; book['balance']=round(before+pnl,8)
                    trade={**copy.deepcopy(p),'closed_at':now,'exit_reason':reason,
                        'signal_pnl_pct':(policy.number(coin['priceUsd'])/p['entry_price']-1)*100,
                        'exit_price':policy.number(coin['priceUsd']),'exit_dex_fee_usd':q['dex_fee_usd'],
                        'exit_network_fee_usd':q['network_fee_usd'],'exit_price_impact_pct':q['impact_pct'],
                        'exit_slippage_pct':q['slippage_pct']+q['latency_pct'],
                        'exit_net_proceeds_usd':q['net_proceeds_usd'],'balance_before':before,'balance_after':book['balance']}
                    book['history'].append(trade);book['position']=None
            if group['episode'] and all(b['position'] is None for b in group['books'].values()):
                ep=group['episode'];outcomes={arm:next(t for t in reversed(book['history']) if t['episode_id']==ep['id']) for arm,book in group['books'].items()}
                control=outcomes['CONTROL']['pnl_usd']
                group['completed'].append({'episode_id':ep['id'],'address':ep['address'],'pair':ep['pair'],
                    'symbol':ep['symbol'],'opened_at':ep['opened_at'],'closed_at':now,
                    'pnl_usd':{arm:t['pnl_usd'] for arm,t in outcomes.items()},
                    'delta_vs_control_usd':{arm:t['pnl_usd']-control for arm,t in outcomes.items()},
                    'exit_reasons':{arm:t['exit_reason'] for arm,t in outcomes.items()}})
                group['last_closed_by_mint'][ep['address']]=now;group['episode']=None

    def maybe_open(self,feed,tape,now):
        for spec in policy.GROUPS:
            group=self.state['groups'][spec.id]; reasons={};group['diagnostics']={'checked_at':now,'rejections':reasons}
            def reject(reason): reasons[reason]=reasons.get(reason,0)+1
            if group['episode']: reject('paired_episode_open');continue
            if group['day']!=day_key(now):
                group['day']=day_key(now);group['day_start_balances']={a:b['balance'] for a,b in group['books'].items()}
            if any(b['balance']-group['day_start_balances'][a]<=-50 for a,b in group['books'].items()): reject('experiment_daily_limit');continue
            balance=min(b['balance'] for b in group['books'].values())
            if balance<10.1: reject('balance');continue
            eligible=[]
            for coin in feed:
                if not activity.usable_feed_coin(coin,now): reject('stale_or_invalid_feed');continue
                if coin.get('quoteTokenAddress')!=SOL or policy.number(coin.get('priceNative'))<=0: reject('network_price_unknown');continue
                flow=policy.flow_window(tape,coin['address'],coin['pairAddress'],60,now)
                f=policy.features(coin,flow)
                if not activity.RULES[spec.parent].matches(f): reject('parent_signal');continue
                if spec.parent=='ORDER_FLOW' and not flow.get('fresh'): reject('stale_flow_data');continue
                filters=policy.entry_rejections(spec,f)
                if filters:
                    for reason in filters:reject(reason)
                    continue
                last=group['last_closed_by_mint'].get(coin['address'],0)
                if last and now-last<policy.COOLDOWN_MS: reject('cooldown');continue
                check=self.reference(coin,now)
                if check.get('status')!='pass': reject(check.get('reason') or 'price_reference');continue
                proposed=entry_size(coin,balance,spec.max_cost)
                if proposed is None: reject('roundtrip_cost');continue
                eligible.append((proposed['initial_pct'],f['score'],coin['address'],coin,f,proposed,check))
            group['diagnostics']['eligible']=len(eligible)
            if not eligible: continue
            _,_,_,coin,f,proposal,check=max(eligible,key=lambda x:x[:3])
            group['sequence']+=1; eid=spec.id+':'+str(group['sequence'])+':'+str(now)
            q=proposal['opening'];notional=proposal['notional'];mark=proposal['mark']
            fast=policy.flow_window(tape,coin['address'],coin['pairAddress'],30,now)
            slow=policy.flow_window(tape,coin['address'],coin['pairAddress'],300,now)
            evidence={'coin':coin,'features':f,'entry':q,'initial_mark':mark,'at':now,'price_reference':check}
            evidence_hash=digest(evidence)
            p={'episode_id':eid,'entry_evidence_hash':evidence_hash,'entry_evidence':evidence,
                'address':coin['address'],'pairAddress':coin['pairAddress'],'symbol':coin.get('symbol','?'),
                'name':coin.get('name',''),'entry_price':policy.number(coin['priceUsd']),
                'execution_entry_price':q['fill_price'],'quantity':q['quantity'],'notional_usd':notional,
                'capital_committed_usd':q['capital_committed_usd'],'opened_at':now,'updated_at':now,
                'entry_flow_score':policy.conviction(fast,slow),'entry_features':f,
                'entry_roundtrip_pnl_pct':proposal['initial_pct'],'entry_dex_fee_usd':q['dex_fee_usd'],
                'entry_network_fee_usd':q['network_fee_usd'],'entry_price_impact_pct':q['impact_pct'],
                'entry_slippage_pct':q['slippage_pct']+q['latency_pct'],
                'pnl_pct':proposal['initial_pct'],'peak_net_pct':proposal['initial_pct'],
                'open_pnl_usd':mark['net_proceeds_usd']-q['capital_committed_usd'],
                'quote_status':'fresh','execution_mode':policy.MODEL,'entry_policy_version':policy.VERSION,
                'provider_market_timestamp_available':False,'score':f['score']}
            for arm,book in group['books'].items():
                book['position']={**copy.deepcopy(p),'strategy_id':book['id'],'arm':arm,'trade_no':group['sequence']}
            group['episode']={'id':eid,'address':coin['address'],'pair':coin['pairAddress'],
                'symbol':coin.get('symbol','?'),'opened_at':now,'entry_evidence_hash':evidence_hash}
            group['diagnostics']['opened_episode']=eid

    def snapshot(self,now):
        groups=[]
        for group in self.state['groups'].values():
            completed=group['completed']; comparisons={}
            for arm in policy.ARMS:
                if arm=='CONTROL': continue
                deltas=[e['delta_vs_control_usd'][arm] for e in completed]
                comparisons[arm]={'paired_episodes':len(deltas),'mean_delta_usd':sum(deltas)/len(deltas) if deltas else None,
                    'total_delta_usd':sum(deltas),'improved':sum(x>0 for x in deltas),'worse':sum(x<0 for x in deltas),
                    'equal':sum(x==0 for x in deltas),'independent_tokens':len({e['address'] for e in completed}),
                    'status':'collecting' if len(deltas)<100 else 'review_required_no_auto_promotion'}
            arms=[]
            for arm,book in group['books'].items():
                compact={k:v for k,v in (book.get('position') or {}).items() if k not in ('entry_evidence','last_flow')}
                arms.append({'arm':arm,'label':book['name'],'stats':policy.book_stats(book),
                    'position':compact or None,'last_exits':[{'symbol':t['symbol'],'pnl_usd':t['pnl_usd'],'pnl_pct':t['pnl_pct'],
                        'reason':t['exit_reason'],'closed_at':t['closed_at'],'episode_id':t['episode_id']} for t in book['history'][-10:][::-1]]})
            groups.append({'id':group['id'],'name':group['name'],'parent':group['parent'],'max_cost_pct':group['max_cost_pct'],
                'arms':arms,'comparisons':comparisons,'episode':group['episode'],'diagnostics':group['diagnostics'],
                'recent_pairs':completed[-10:][::-1]})
        return {'version':policy.VERSION,'started_at':self.state['started_at'],'updated_at':now,
            'status':self.state['status'],'error':self.state.get('error'),'paper_only':True,
            'execution_basis':policy.MODEL,'groups':groups,'cooldown_minutes':20,
            'data_note':'Оценени разходи, не Jupiter изпълнения. Сравнението е валидно само в една група. Няма доказана доходност.',
            'daily_loss_limit_per_arm_usd':50,'automatic_promotion':False}

    def persist(self,now):
        self.state['updated_at']=now
        atomic(self.path,self.state)
        atomic(self.root/'snapshot.json',self.snapshot(now))


def held_prices(session,runner):
    keys={(p['address'],p['pairAddress']) for g in runner.state['groups'].values()
          for b in g['books'].values() for p in [b.get('position')] if p}
    if not keys: return {}
    addresses=sorted({mint for mint,pair in keys})
    r=session.get('https://api.dexscreener.com/tokens/v1/solana/'+','.join(addresses),timeout=(1,3))
    r.raise_for_status(); rows=r.json();out={};stamp=now_ms()
    if not isinstance(rows,list): raise ValueError('Invalid market response')
    for raw in rows:
        if raw.get('chainId')!='solana':continue
        mint=(raw.get('baseToken') or {}).get('address');pair=raw.get('pairAddress')
        if (mint,pair) not in keys:continue
        out[(mint,pair)]={**raw,'address':mint,'pairAddress':pair,
            'quoteTokenAddress':(raw.get('quoteToken') or {}).get('address'),
            'liquidityUsd':policy.number((raw.get('liquidity') or {}).get('usd')),'received_at':stamp}
    return out


def main():
    ROOT.mkdir(parents=True,exist_ok=True)
    with (ROOT/'process.lock').open('a+') as handle:
        fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
        runner=ExperimentRunner(ROOT,now_ms());session=requests.Session()
        session.headers.update({'User-Agent':'NEO-Paired-Paper-Lab/1','Accept':'application/json'})
        for sig in (signal.SIGTERM,signal.SIGINT):signal.signal(sig,lambda *_:STOP.set())
        feed=[];last_feed=0
        while not STOP.is_set():
            started=time.monotonic();errors=[];now=now_ms()
            try:tape=json.loads(Path('/var/lib/neo-market/live_tape.json').read_text(encoding='utf-8'))
            except (OSError,ValueError):tape={};errors.append('tape_unavailable')
            try:runner.update_positions(held_prices(session,runner),tape,now_ms())
            except (requests.RequestException,ValueError,KeyError,TypeError) as exc:
                runner.mark_stale(now_ms());errors.append('price_'+type(exc).__name__)
            if not STOP.is_set() and now-last_feed>=5_000:
                try:
                    r=session.get(API,timeout=(1,3));r.raise_for_status();body=r.json();feed=body.get('feed') or [];last_feed=now
                    runner.maybe_open(feed,tape,now_ms())
                except (requests.RequestException,ValueError,KeyError,TypeError) as exc:errors.append('feed_'+type(exc).__name__)
            runner.state['status']='degraded' if errors else 'online'
            runner.state['error']=';'.join(errors) if errors else None
            runner.persist(now_ms())
            STOP.wait(max(.25,2-(time.monotonic()-started)))
        runner.state['status']='stopped';runner.persist(now_ms())

if __name__=='__main__':main()
