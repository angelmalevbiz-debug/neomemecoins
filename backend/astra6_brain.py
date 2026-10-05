#!/usr/bin/env python3
"""Astra 6 Brain: isolated, quote-backed PAPER experiment. No signed orders.

A quote is not a fill. PnL includes AMM fees already in Jupiter outAmount,
explicit 10bps/leg assumed execution friction, a network/priority budget and
unrecovered token-account rent. Unknown/failed/stale quotes never create wins.
Existing engine and Strategy Lab books are read-only inputs.
"""
from __future__ import annotations
import concurrent.futures as cf
import compat_file_lock as fcntl
import json
import math
import os
import re
import signal
import statistics
import time
from collections import Counter, deque
from decimal import Decimal
from pathlib import Path
from typing import Any
import requests
import honest_quote_transport as quote_transport

ID = 'ASTRA_6_BRAIN'
NAME = 'Astra 6 Brain'
VERSION = 'ASTRA_QUOTE_PAPER_V1'
ROOT = Path(os.getenv('NEO_ASTRA_DATA_DIR', '/var/lib/neo-market'))
PATH = ROOT / 'astra_6_brain.json'
API = os.getenv('NEO_LOCAL_API', 'http://127.0.0.1:8788/state')
RPC = os.getenv('NEO_ASTRA_RPC_URL', 'https://api.mainnet-beta.solana.com')
USDC = 'EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v'
SOL = 'So11111111111111111111111111111111111111112'
ADDRESS = re.compile(r'^[1-9A-HJ-NP-Za-km-z]{32,44}$')
STOP = 2.0
MAX_POSITIONS = 3
MAX_NOTIONAL = 75.0
MIN_NOTIONAL = 25.0
MAX_COST = 1.25
MAX_IMPACT = 1.0
EXECUTION_BUFFER_BPS = 10
SLIPPAGE_BPS = 50
QUOTE_MAX_MS = 8_000
FEED_MAX_MS = 20_000
DAILY_LIMIT = 25.0
CONFIG = dict(version=VERSION, paper_only=True, stop_loss_net_pct=STOP,
    take_profit_min_pct=10, take_profit_max_pct=20, max_positions=MAX_POSITIONS,
    starting_balance_usd=500, min_notional_usd=MIN_NOTIONAL,
    max_notional_usd=MAX_NOTIONAL, max_entry_cost_pct=MAX_COST,
    assumed_execution_buffer_bps_per_leg=EXECUTION_BUFFER_BPS,
    slippage_tolerance_bps=SLIPPAGE_BPS, daily_loss_limit_usd=DAILY_LIMIT,
    decision_interval_seconds=0.5, quote_refresh_target_seconds=3,
    quote_max_age_seconds=QUOTE_MAX_MS/1000,
    execution_basis='LIVE_QUOTES_PLUS_EXPLICIT_ESTIMATES_NOT_ACTUAL_FILLS',
    numeraire='USDC; displayed as $ at assumed USDC/USD parity',
    network_fee_note='priority budget, not an observed transaction fee',
    rent_note='new token account rent reserved as cost; not assumed recovered',
    model_note='deterministic experimental signal, not a win probability')


def now_ms() -> int:
    return int(time.time()*1000)


def n(v: Any, d: float = 0.0) -> float:
    try:
        x = float(v)
        return x if math.isfinite(x) else d
    except (TypeError, ValueError, OverflowError):
        return d


def read(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return default


def write(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name+'.tmp')
    with tmp.open('w',encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, allow_nan=False)
        f.flush()
        os.fsync(f.fileno())
    tmp.replace(path)


def empty_book() -> dict:
    return dict(id=ID, name=NAME, starting_balance=500.0, balance=500.0,
        positions=[], position=None, history=[], trade_seq=0, created_at=now_ms(),
        known_mints=[], day=time.strftime('%Y-%m-%d', time.gmtime()),
        day_start_balance=500.0)


def reserved(book: dict) -> float:
    return sum(n(p['capital_committed_usd']) for p in book['positions'])


def equity(book: dict) -> float:
    return n(book['balance']) + sum(n(p.get('pnl_usd')) for p in book['positions'])


def available(book: dict) -> float:
    return max(0.0, n(book['balance'])-reserved(book))


def stats(book: dict) -> dict:
    h=book['history']; wins=[t for t in h if n(t['pnl_usd'])>0]
    gp=sum(max(0,n(t['pnl_usd'])) for t in h)
    gl=-sum(min(0,n(t['pnl_usd'])) for t in h)
    eq=equity(book); start=n(book['starting_balance'])
    return dict(trades=len(h), wins=len(wins), losses=len(h)-len(wins),
        win_rate=round(100*len(wins)/len(h),1) if h else 0,
        profit_factor=round(gp/gl,3) if gl else None,
        profit_factor_status='finite' if gl else ('infinite_no_losses' if gp else 'undefined_no_results'),
        realized_pnl=round(n(book['balance'])-start,4), equity=round(eq,4),
        return_pct=round((eq/start-1)*100,3), open=bool(book['positions']),
        open_positions=len(book['positions']), reserved_usd=round(reserved(book),4),
        available_usd=round(available(book),4),
        average_win_usd=round(gp/len(wins),4) if wins else 0,
        average_loss_usd=round(-gl/(len(h)-len(wins)),4) if len(h)>len(wins) else 0)


def cooldown(book: dict, mint: str, stamp: int) -> bool:
    recent=next((t for t in book['history'] if t['address']==mint),None)
    if not recent: return False
    delay=180_000 if n(recent['pnl_usd'])<0 else 30_000
    return stamp-int(recent['closed_at']) < delay


def flows(events: list, stamp: int) -> dict:
    out={}; seen=set()
    for e in events:
        if not stamp-60_000 <= int(e.get('ts') or 0) <= stamp+2000: continue
        if e.get('direction') not in ('BUY','SELL'): continue
        key=(e.get('signature'),e.get('address'),e.get('direction'),e.get('usd_amount'))
        if e.get('signature') and key in seen: continue
        seen.add(key)
        f=out.setdefault(e.get('address'),dict(buy=0.,sell=0.,buyers=set(),wallets=set(),trades=0,max_buy=0.))
        usd=max(0.,n(e.get('usd_amount'))); wallet=e.get('wallet')
        if usd<1: continue
        f['trades']+=1
        if wallet: f['wallets'].add(wallet)
        if e['direction']=='BUY':
            f['buy']+=usd; f['max_buy']=max(f['max_buy'],usd)
            if wallet: f['buyers'].add(wallet)
        else: f['sell']+=usd
    return out


def candidate(coin: dict, flow: dict, samples: list, stamp: int) -> tuple[dict|None,str]:
    if not ADDRESS.fullmatch(str(coin.get('address',''))) or not ADDRESS.fullmatch(str(coin.get('pairAddress',''))):
        return None,'Невалиден адрес'
    age=stamp-int(coin.get('updatedAt') or 0)
    if age<0 or age>FEED_MAX_MS: return None,'Стари пазарни данни'
    if n(coin.get('priceUsd'))<=0: return None,'Липсва цена'
    if n(coin.get('liquidityUsd'))<30000: return None,'Ликвидност под $30 000'
    if n(coin.get('score'))<80: return None,'Оценка под 80'
    if n(coin.get('ageMinutes'))<5: return None,'Прекалено нов pool'
    changes=coin.get('priceChange') or {}; m5=n(changes.get('m5')); h1=n(changes.get('h1'))
    if not -2<=m5<=30 or not -35<=h1<=200: return None,'Неподходящ тренд'
    buyers=len(flow.get('buyers',[])); wallets=len(flow.get('wallets',[]))
    buy=n(flow.get('buy')); sell=n(flow.get('sell')); ratio=buy/max(1,sell)
    if buyers<3 or wallets<3 or buy<100 or ratio<1.3: return None,'Недостатъчно потвърдени покупки'
    if n(flow.get('max_buy'))>buy*.85: return None,'Покупките зависят от един участник'
    prices=[x[1] for x in samples if stamp-x[0]<=90_000 and x[1]>0]
    volatility=statistics.pstdev([math.log(b/a)*100 for a,b in zip(prices,prices[1:])]) if len(prices)>3 else 0
    score=min(100.,n(coin.get('score'))*.40 + min(20.,buyers*2.) + min(20.,ratio*5.) + max(0.,min(15.,m5)) + (5 if h1>=0 else 0))
    target=20. if score>=88 and ratio>=2 else 15. if score>=76 else 10.
    return dict(score=round(score,2), target_pct=target, m5=m5, h1=h1,
                buyer_wallets=buyers, buy_sell_ratio=round(ratio,3),
                sampled_volatility_pct=round(volatility,4),
                setup='Пробив с покупки' if m5>=8 else 'Импулс с покупки'),''


class Busy(Exception): pass
class QuoteError(Exception): pass


def valid_route(data: dict, mint: str, pair: str, entry: bool) -> bool:
    # Base58 identities are case-sensitive. ALL token-facing route legs must
    # use the tracked pool, not just any one leg of a split route.
    key='outputMint' if entry else 'inputMint'
    legs=[x.get('swapInfo',{}) for x in data.get('routePlan',[]) if x.get('swapInfo',{}).get(key)==mint]
    return bool(legs) and all(x.get('ammKey')==pair for x in legs)


def usable_quote(q: dict, stamp: int) -> bool:
    return (0<=stamp-int(q.get('_at') or 0)<=QUOTE_MAX_MS
        and int(q.get('outAmount') or 0)>0
        and 0<int(q.get('otherAmountThreshold') or 0)<=int(q.get('outAmount') or 0))


class Client:
    def __init__(self):
        self.http=requests.Session()
        self.http.headers.update({'User-Agent':'NEO-Astra6-Paper/1.0'})
        self.meta={}; self.sol_usd=0.; self.sol_at=0
        self.fee_sol=.0001; self.rpc_at=0; self.rent={}
        self.requests=0; self.errors=0; self.last_quote_error={}

    def rpc(self, method: str, params: list):
        r=self.http.post(RPC,json={'jsonrpc':'2.0','id':1,'method':method,'params':params},timeout=5)
        r.raise_for_status(); d=r.json()
        if 'error' in d or 'result' not in d: raise QuoteError('RPC отказа проверката')
        return d['result']

    def mint(self, mint: str) -> dict:
        cached=self.meta.get(mint)
        if cached and now_ms()-cached['at']<300_000: return cached
        account=self.rpc('getAccountInfo',[mint,{'encoding':'jsonParsed','commitment':'confirmed'}])['value']
        if not account: raise QuoteError('Липсва mint акаунт')
        parsed=account.get('data',{}).get('parsed',{})
        info=parsed.get('info',{})
        if parsed.get('type')!='mint' or not info.get('isInitialized'): raise QuoteError('Невалиден mint')
        if info.get('freezeAuthority') or info.get('mintAuthority'): raise QuoteError('Активно право за замразяване или нови токени')
        safe={'metadataPointer','tokenMetadata'}
        if any(x.get('extension') not in safe for x in info.get('extensions',[])):
            raise QuoteError('Неподдържана token extension / възможна допълнителна такса')
        decimals=info.get('decimals')
        if not isinstance(decimals,int) or not 0<=decimals<=18: raise QuoteError('Непроверени decimals')
        if account['owner'] not in ('TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb','TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA'):
            raise QuoteError('Неподдържана token програма')
        size=182 if account['owner']=='TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb' else 165
        if size not in self.rent: self.rent[size]=int(self.rpc('getMinimumBalanceForRentExemption',[size]))/1e9
        m=dict(decimals=decimals,rent_sol=self.rent[size],at=now_ms())
        self.meta[mint]=m
        return m

    def quote(self, inp: str, out: str, raw: int, *, purpose: str='entry', force: bool=False) -> dict:
        # ASTRA's data directory only selects its ledger. Every account shares
        # the same transport, exit queue, cache and provider backoff files.
        started=now_ms()
        q=quote_transport.quote(inp,out,raw,purpose=purpose,
            slippage_bps=SLIPPAGE_BPS,min_received_at=started if force else None)
        if q is None:
            self.last_quote_error=quote_transport.last_error() or {'code':'QUOTE_UNAVAILABLE','at':now_ms()}
            self.errors+=1
            code=self.last_quote_error['code']
            if self.last_quote_error.get('http_ms') is not None or self.last_quote_error.get('http_status') or code in ('HTTP_ERROR','TIMEOUT','SCHEMA_MISMATCH'):
                self.requests+=1
            if code=='QUOTE_BUDGET_TIMEOUT': raise Busy(code)
            raise QuoteError(code)
        if not q.get('_cache_hit'): self.requests+=1
        q=dict(q)
        try:
            received=int(q['_received_at'])
            q['_at']=received # Never refresh the age of a reused observation.
            q['_request_ms']=int(q.get('_http_ms') or 0)
            q['_end_to_end_ms']=max(0,now_ms()-started)
            if q.get('inputMint')!=inp or q.get('outputMint')!=out or int(q.get('inAmount') or 0)!=raw:
                raise ValueError('QUOTE_IDENTITY_MISMATCH')
            if force and received<started: raise ValueError('STALE_EXIT_QUOTE')
            if not usable_quote(q,now_ms()) or q['_request_ms']>QUOTE_MAX_MS:
                raise ValueError('STALE_OR_INVALID_QUOTE')
        except (ValueError,TypeError,KeyError) as exc:
            self.errors+=1
            self.last_quote_error={'code':str(exc),'at':now_ms(),'purpose':purpose}
            raise QuoteError(self.last_quote_error['code']) from exc
        self.last_quote_error={}
        return q

    def warm(self):
        if not self.rpc_at or now_ms()-self.rpc_at>120_000:
            fees=self.rpc('getRecentPrioritizationFees',[])
            values=sorted(n(x.get('prioritizationFee')) for x in fees)
            # Conservative budget: full CU ceiling, P90 recent priority unit
            # price and a 100,000-lamport floor. Not a measured swap fee.
            unit=values[min(len(values)-1,int(.9*len(values)))] if values else 0
            self.fee_sol=max(100_000,5000+math.ceil(unit*1_400_000/1e6))/1e9
            self.rpc_at=now_ms()
        q=self.quote(SOL,USDC,1_000_000_000,purpose='background')
        self.sol_usd=int(q['outAmount'])/1e6
        self.sol_at=q['_at']
        if not 1<self.sol_usd<100000: raise QuoteError('Невалидна цена на SOL')

    def buy(self, coin: dict, amount: float):
        meta=self.mint(coin['address'])
        q=self.quote(USDC,coin['address'],int(Decimal(str(amount))*1_000_000),purpose='entry')
        if not valid_route(q,coin['address'],coin['pairAddress'],True): raise QuoteError('Входният route използва различен pool')
        return q,meta

    def sell(self, mint: str, pair: str, raw: int, purpose: str='exit'):
        q=self.quote(mint,USDC,raw,purpose=purpose,force=purpose=='exit')
        if not valid_route(q,mint,pair,False): raise QuoteError('Изходният route използва различен pool')
        return q


def simulated_raw(q: dict) -> int:
    # Explicit execution-friction assumption, distinct from slippage tolerance.
    return int(q['outAmount'])*(10000-EXECUTION_BUFFER_BPS)//10000


def proceeds(q: dict, fee_usd: float) -> dict:
    gross=int(q['outAmount'])/1e6
    buffer=gross*EXECUTION_BUFFER_BPS/10000
    return dict(expected_proceeds_usd=gross, execution_buffer_usd=buffer,
        network_fee_usd=fee_usd, net_proceeds_usd=max(0.,gross-buffer-fee_usd),
        conservative_net_usd=max(0.,int(q['otherAmountThreshold'])/1e6-fee_usd))


def construct_position(book: dict, proposal: dict, sell: dict, fee: float, stamp: int) -> dict:
    buy=proposal['quote']; coin=proposal['coin']; meta=proposal['meta']
    if not usable_quote(buy,stamp) or not usable_quote(sell,stamp): raise QuoteError('Проверка с изтекла котировка')
    if stamp-int(coin.get('updatedAt') or 0)>FEED_MAX_MS: raise QuoteError('Стар входен сигнал')
    amount=int(buy['inAmount'])/1e6
    raw=proposal['raw']; qty=raw/(10**meta['decimals'])
    if int(sell.get('inAmount',0))!=raw or not valid_route(buy,coin['address'],coin['pairAddress'],True) or not valid_route(sell,coin['address'],coin['pairAddress'],False):
        raise QuoteError('Несъответстващи количества или pool в проверката')
    basis=amount+fee+proposal['rent_usd']
    if len(book['positions'])>=MAX_POSITIONS or basis>available(book): raise QuoteError('Недостатъчен свободен капитал')
    if any(p['address']==coin['address'] for p in book['positions']): raise QuoteError('Токенът вече е отворен')
    mark=proceeds(sell,fee); pnl=mark['net_proceeds_usd']-basis
    pct=pnl/amount*100
    # Guard uses both expected-net cost and quoted slippage-bound scenario.
    if pct < -MAX_COST or pct>0.5: raise QuoteError('Разходите или отклонението на котировките не позволяват вход')
    # Additional entry-side slippage stress is a conservative scenario, NOT an observed fill.
    ratio=min(1.,int(buy['otherAmountThreshold'])/max(raw,1))
    stress_net=max(0.,(mark['conservative_net_usd']+fee)*ratio-fee)
    if (stress_net-basis)/amount*100 <= -STOP: raise QuoteError('Недостатъчен буфер до стопа')
    if abs(n(buy.get('priceImpactPct'))*100)>MAX_IMPACT: raise QuoteError('Прекалено ценово въздействие')
    return dict(id=f'{ID}:{book["trade_seq"]+1}',trade_no=book['trade_seq']+1,strategy_id=ID,
        symbol=coin.get('symbol','TOKEN'),name=coin.get('name',''),address=coin['address'],
        pairAddress=coin['pairAddress'],notional_usd=amount,capital_committed_usd=basis,
        token_raw_amount=raw,token_decimals=meta['decimals'],quantity=qty,
        entry_price=n(coin['priceUsd']),execution_entry_price=amount/qty,
        current_price=n(coin['priceUsd']),current_execution_price=mark['net_proceeds_usd']/qty,
        opened_at=stamp,updated_at=stamp,quote_at=sell['_at'],pnl_usd=pnl,pnl_pct=pct,
        entry_roundtrip_pnl_pct=pct,peak_net_pct=pct,stop_loss_net_pct=STOP,
        target_net_pct=proposal['signal']['target_pct'],signal=proposal['signal'],
        entry_network_fee_usd=fee,entry_rent_reserved_usd=proposal['rent_usd'],
        entry_execution_buffer_bps=EXECUTION_BUFFER_BPS,
        entry_quote=buy,entry_sell_check=sell,last_sell_quote=sell,
        execution_mode=VERSION,quote_status='Свежа котировка',pending_exit_reason=None,
        dex_url=coin.get('dexUrl'),costs=mark)


def apply_mark(book: dict, pos: dict, q: dict, fee: float, stamp: int) -> str|None:
    if not usable_quote(q,stamp): raise QuoteError('Не се използва стара котировка')
    if int(q.get('inAmount',0))!=int(pos['token_raw_amount']): raise QuoteError('Различно количество на изход')
    if not valid_route(q,pos['address'],pos['pairAddress'],False): raise QuoteError('Изход от различен pool')
    mark=proceeds(q,fee); pnl=mark['net_proceeds_usd']-pos['capital_committed_usd']
    pct=pnl/pos['notional_usd']*100
    pos.update(pnl_usd=pnl,pnl_pct=pct,updated_at=stamp,quote_at=q['_at'],
        peak_net_pct=max(n(pos.get('peak_net_pct'),pct),pct),last_sell_quote=q,
        current_execution_price=mark['net_proceeds_usd']/pos['quantity'],
        quote_status='Свежа котировка',costs=mark)
    reason=pos.get('pending_exit_reason')
    if pct<=-STOP: reason='STOP_LOSS_2_NET'
    elif pct>=pos['target_net_pct']: reason=f'TAKE_PROFIT_{int(pos["target_net_pct"])}_NET'
    elif pos['peak_net_pct']>=12 and pct<=max(10,pos['peak_net_pct']-2): reason='PROFIT_TRAIL_NET'
    elif stamp-pos['opened_at']>=45*60000: reason='MAX_HOLD_45_MIN'
    if reason:
        # Never clamp a loss to -2% or manufacture a price at the target.
        balance_before=book['balance']; book['balance']=round(balance_before+pnl,8)
        closed={**pos,'closed_at':stamp,'exit_reason':reason,
            'exit_price':pos['current_execution_price'],'execution_exit_price':pos['current_execution_price'],
            'exit_network_fee_usd':fee,'balance_before':balance_before,'balance_after':book['balance']}
        book['history'].insert(0,closed)
        book['positions']=[p for p in book['positions'] if p['id']!=pos['id']]
    return reason


class Brain:
    def __init__(self, path: Path = PATH, client=None):
        self.path=path
        if path.exists():
            stored=json.loads(path.read_text(encoding='utf-8')) # Corrupt state MUST NOT silently reset.
            self.book=stored['book']
        else: self.book=empty_book()
        self.client=client or Client(); self.executor=cf.ThreadPoolExecutor(max_workers=1)
        self.future=None; self.job=None; self.pending=None; self.cool={}
        self.samples={}; self.feed=[]; self.flow={}; self.last_feed=0; self.last_save=0
        self.diag=dict(scanned=0,qualified=0,quote_attempts=0,rejections={},message='Подготовка на котировките')
        self.running=True; self.stop_requested=False
        self.events=[]; self.api=requests.Session()
        self.last_attempt={}; self.size_trials={}

    def reject(self, text: str):
        self.diag['rejections'][text]=self.diag['rejections'].get(text,0)+1
        self.diag['message']=text

    def event(self,text):
        self.events.insert(0,dict(ts=now_ms(),text=text)); self.events=self.events[:30]

    def refresh(self):
        r=self.api.get(API,timeout=2); r.raise_for_status(); data=r.json()
        stamp=now_ms(); self.last_feed=stamp
        self.feed=data.get('feed',[])[:100]
        tape=read(ROOT/'live_tape.json',{}) or {}
        self.flow=flows(tape.get('events',[]),stamp)
        for c in self.feed:
            key=(c.get('address'),c.get('pairAddress')); ring=self.samples.setdefault(key,deque(maxlen=30))
            source=int(c.get('updatedAt') or 0)
            if not ring or source>ring[-1][0]: ring.append((source,n(c.get('priceUsd'))))
        self.diag['scanned']=len(self.feed)

    def submit(self, kind, data, fn, *args):
        self.job=(kind,data); self.future=self.executor.submit(fn,*args)
        if kind!='warm': self.diag['quote_attempts']+=1

    def finish(self):
        kind,data=self.job
        try:
            result=self.future.result(); stamp=now_ms()
            fee=self.client.fee_sol*self.client.sol_usd
            if kind=='buy':
                q,meta=result
                self.pending={**data,'quote':q,'meta':meta,'raw':simulated_raw(q),
                    'rent_usd':meta['rent_sol']*self.client.sol_usd if data['coin']['address'] not in self.book['known_mints'] else 0.}
            elif kind=='check':
                p=construct_position(self.book,data,result,fee,stamp)
                self.book['positions'].append(p); self.book['trade_seq']=p['trade_no']
                if p['address'] not in self.book['known_mints']: self.book['known_mints'].append(p['address'])
                self.event(f'Тестов вход {p["symbol"]}: ${p["notional_usd"]:.2f}; нетна цел {p["target_net_pct"]:.0f}%')
                self.pending=None; self.size_trials.pop(p['address'],None)
            elif kind=='mark':
                pos=next((p for p in self.book['positions'] if p['id']==data['id']),None)
                if pos:
                    reason=apply_mark(self.book,pos,result,fee,stamp)
                    if reason: self.event(f'Тестов изход {pos["symbol"]}: {pos["pnl_pct"]:+.2f}% нето; {reason}')
        except Busy as exc:
            self.diag['message']=str(exc) # Preserve the shared budget failure.
        except Exception as exc:
            text=str(exc) if isinstance(exc,QuoteError) else type(exc).__name__
            self.reject(text)
            if kind in ('buy','check'):
                address=data['coin']['address']
                step=self.size_trials.get(address,0)
                if isinstance(exc,QuoteError) and ('Разходите' in text or 'буфер до стопа' in text or 'въздействие' in text) and step<2:
                    self.size_trials[address]=step+1
                    self.cool[address]=now_ms()+5000
                else:
                    self.cool[address]=now_ms()+60_000
                    self.size_trials.pop(address,None)
                self.pending=None
            elif kind=='mark':
                data['quote_status']='Няма потвърдена котировка; резултатът е остарял'
        finally:
            self.future=None; self.job=None

    def schedule(self):
        stamp=now_ms()
        if self.future: return
        positions=self.book['positions']
        # Exits are serviced before discovery, buys and account lookups.
        due=[p for p in positions if stamp-int(p.get('quote_at',0))>=3000 and stamp-self.last_attempt.get(p['id'],0)>=1500]
        if due:
            pos=min(due,key=lambda p:p.get('quote_at',0))
            self.last_attempt[pos['id']]=stamp
            self.submit('mark',pos,self.client.sell,pos['address'],pos['pairAddress'],pos['token_raw_amount'])
            return
        if self.client.sol_usd<=0 or stamp-self.client.sol_at>120_000:
            if stamp-self.last_attempt.get('warm',0)<3000: return
            self.last_attempt['warm']=stamp; self.submit('warm',None,self.client.warm); return
        if self.pending:
            if stamp-self.pending['quote']['_at']>QUOTE_MAX_MS:
                self.pending=None; self.reject('Изтекла входна котировка'); return
            if stamp-self.last_attempt.get('check',0)<1500: return
            p=self.pending; self.last_attempt['check']=stamp
            self.submit('check',p,self.client.sell,p['coin']['address'],p['coin']['pairAddress'],p['raw'],'entry')
            return
        if any(stamp-int(p.get('quote_at',0))>QUOTE_MAX_MS for p in positions):
            self.diag['message']='Първо се обновяват отворените позиции'; return
        today=time.strftime('%Y-%m-%d',time.gmtime())
        if self.book['day']!=today:
            self.book['day']=today; self.book['day_start_balance']=self.book['balance']
        if equity(self.book)-self.book['day_start_balance']<=-DAILY_LIMIT:
            self.diag['message']='Дневният лимит на Astra е достигнат; без нови входове'; return
        if len(positions)>=MAX_POSITIONS:
            self.diag['message']='Следене на трите отворени позиции'; return
        eligible=[]; rejected=Counter()
        for coin in self.feed:
            a=coin.get('address'); key=(a,coin.get('pairAddress'))
            if any(p['address']==a for p in positions) or cooldown(self.book,a,stamp) or self.cool.get(a,0)>stamp: continue
            signal,why=candidate(coin,self.flow.get(a,{}),list(self.samples.get(key,[])),stamp)
            if signal: eligible.append((signal['score'],coin,signal))
            else: rejected[why]+=1
        self.diag['qualified']=len(eligible); self.diag['filter_rejections']=dict(rejected)
        if not eligible:
            self.diag['message']='Няма подходящ вход: '+('; '.join(f'{k}: {v}' for k,v in rejected.most_common(2)))
            return
        _,coin,signal=max(eligible,key=lambda x:x[0])
        if stamp-self.last_attempt.get('buy',0)<3000: return
        fee=self.client.fee_sol*self.client.sol_usd
        amount=min(MAX_NOTIONAL,max(0.,equity(self.book))*.15,available(self.book)-2.)
        amount=max(MIN_NOTIONAL,amount*(1.,.65,.35)[self.size_trials.get(coin['address'],0)]) if amount>=MIN_NOTIONAL else amount
        amount=math.floor(amount*100)/100
        if amount<MIN_NOTIONAL: self.diag['message']='Недостатъчен свободен капитал'; return
        self.last_attempt['buy']=stamp
        data=dict(coin=coin,signal=signal,amount=amount)
        self.submit('buy',data,self.client.buy,coin,amount)
        self.diag['message']=f'Проверка на вход и изход за {coin.get("symbol")}'

    def persist(self):
        stamp=now_ms()
        for p in self.book['positions']:
            p['quote_age_seconds']=round((stamp-int(p.get('quote_at',0)))/1000,1)
            if p['quote_age_seconds']>QUOTE_MAX_MS/1000: p['quote_status']='Остаряла котировка — не е текуща цена'
        self.book['position']=self.book['positions'][0] if self.book['positions'] else None
        out=dict(book=self.book,stats=stats(self.book),config=CONFIG,updated_at=stamp,
            status='online' if not self.stop_requested else 'stopped',diagnostics=self.diag,events=self.events,
            quote_requests=self.client.requests,sol_usd=self.client.sol_usd,
            quote_errors=self.client.errors,last_quote_error=self.client.last_quote_error,
            network_fee_budget_sol=self.client.fee_sol)
        write(self.path,out); self.last_save=stamp

    def run(self):
        self.persist()
        try:
            while not self.stop_requested:
                stamp=now_ms()
                if self.future and self.future.done(): self.finish()
                if stamp-self.last_feed>=3000:
                    try: self.refresh()
                    except Exception as e:
                        self.last_feed=stamp; self.reject('Пазарният източник не отговаря: '+type(e).__name__)
                self.schedule()
                if stamp-self.last_save>=1000: self.persist()
                time.sleep(.5)
        finally:
            self.executor.shutdown(wait=True,cancel_futures=True)
            if self.future and self.future.done(): self.finish()
            self.persist()


if __name__=='__main__':
    ROOT.mkdir(parents=True,exist_ok=True)
    singleton=(ROOT/'astra_6_brain.process.lock').open('a+')
    fcntl.flock(singleton.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
    brain=Brain()
    def stop(*_): brain.stop_requested=True
    signal.signal(signal.SIGTERM,stop); signal.signal(signal.SIGINT,stop)
    brain.run()
