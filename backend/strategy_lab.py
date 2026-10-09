#!/usr/bin/env python3
import json, math, os, sys, threading, time
from dataclasses import asdict
from pathlib import Path
from typing import Any, Callable
import requests
from astra_lab_bridge import merge_astra_snapshot
from lab_paired_bridge import merge_paired_snapshot
from lab_portfolio_migration import promote_strategy_lab
import lab_activity as activity
import promoted_entry_guard as promoted_guard
import cost_first_established as cost_first
import entry_defense
import heat_veto
import pool_loss_memory
import lab_forward_tests as lab_forward
import lab_high_frequency as hf_lab
import funded_market_candidates as funded_candidates
import funded_active_paper as active_paper
import paper_exit_research as exit_research
import lab_capacity_test
import momentum_rush_brain as rush_brain
import engine_rug_guard as rug_guard
from lab_position_marks import POSITION_MARK_FEED
import paper_execution_quotes as paper_quotes
import pair_price_integrity as price_integrity
from lab_dashboard_projection import compact_strategy_lab
import lab_strategy_lifecycle as lifecycle
import paper_market_feasibility as market_feasibility
from shared_snapshot_io import read_shared_text

API_URL=os.getenv('NEO_LOCAL_API','http://127.0.0.1:8788/state')
DEX='https://api.dexscreener.com'
STATE_PATH=Path(os.getenv('NEO_STRATEGY_LAB_PATH','/var/lib/neo-market/strategy_lab.json'))
COMPACT_PATH=Path(os.getenv('NEO_STRATEGY_LAB_COMPACT_PATH',str(STATE_PATH.parent/'strategy_lab_compact.json')))
RESET_FLAG_PATH=Path(os.getenv('NEO_STRATEGY_LAB_RESET_FLAG','/var/lib/neo-market/strategy_lab.reset'))
# LAB_HIGH_FREQUENCY_V1 books keep their own journal and checkpoint here, never in strategy_lab.json.
HF_ROOT=Path(os.getenv(hf_lab.ENV_ROOT,str(STATE_PATH.parent/'strategy_lab_hf')))
LIVE_TAPE_PATH=Path(os.getenv('NEO_LIVE_TAPE_PATH','/var/lib/neo-market/live_tape.json'))
START_BALANCE=float(os.getenv('NEO_LAB_START_BALANCE','500'))
PROMOTED_STRATEGIES=('EARLY','MOMENTUM','PRECISION','ULTRA_PRECISION')
PROMOTED_TOTAL_CAPITAL=1000.0
PROMOTED_ALLOCATION=250.0
# PAPER entries may use up to half of each independent $250 strategy book.
# Net stop-loss sizing still bounds planned loss per book; actual gaps can exceed it.
PROMOTED_MAX_POSITION_FRACTION=0.5
PROMOTED_LOSS_COOLDOWN_SECONDS=1800
PROMOTED_LOSS_STREAK=3
PROMOTED_ROLLING_WINDOW=8
STRATEGY_START_BALANCES={
    'SCALPER':float(os.getenv('NEO_LAB_SCALPER_START_BALANCE','100')),
    **{strategy_id:PROMOTED_ALLOCATION for strategy_id in PROMOTED_STRATEGIES},
    **{strategy_id:cost_first.START_BALANCE_USD for strategy_id in cost_first.BOOK_IDS},
    **{strategy_id:lab_forward.START_BALANCE_USD for strategy_id in lab_forward.BOOK_IDS},
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

def lab_cost_cap_pct():
    """LAB_ACTIVE_V6: one admission cap (0.5 x net stop) for every Lab book."""
    return activity.admission_cost_cap_pct(STOP_LOSS)

# DEFENSIVE_ENTRY_LAYER_V1 of the Lab process: its own ticker registry (sidecar
# next to the Lab ledger) and pair history, fed with every entry refresh.
DEFENSE=None
DEFENSE_LOCK=threading.Lock()

def entry_defense_layer():
    global DEFENSE
    with DEFENSE_LOCK:
        if DEFENSE is None:
            path=entry_defense.registry_path_for(STATE_PATH)
            # A registry without current coverage merges main's and the tape's ticker memory.
            DEFENSE=entry_defense.DefensiveEntryLayer(
                registry_path=path,seed_paths=entry_defense.sibling_registry_paths(path))
            DEFENSE.funded_heat_seed=active_paper.seed_pair_history(
                DEFENSE.history,STATE_PATH.parent/'funded_heat_seed.json',now_ms())
        return DEFENSE

def defensive_entry_decision(coin,now,*,blocked_pools,heat_log_only=False):
    """Structural rug guard, the book's pool loss memory and the heat veto (log-only for
    the reserved surge/dip hypothesis arms); asked before flow, safety, price probes and fills."""
    return entry_defense_layer().evaluate(coin,now,blocked_pools=blocked_pools,heat_log_only=heat_log_only)

def funded_defensive_decision(book,coin,now,*,blocked_pools,heat_log_only=False):
    if active_paper.applies(book):
        return entry_defense_layer().evaluate(
            coin,now,blocked_pools=blocked_pools,heat_log_only=heat_log_only,
            structural_parameters=active_paper.structural_parameters(book['id']))
    return defensive_entry_decision(coin,now,blocked_pools=blocked_pools,heat_log_only=heat_log_only)

def heat_log_only_book(book_id):
    """HEAT_VETO_STACK_V1 records flags without blocking: heat_veto's reserved surge/dip
    hypothesis arms and, for LAB_FORWARD_TESTS_V1, their random controls (same universe)."""
    return book_id in heat_veto.LOG_ONLY_BOOK_IDS or book_id in lab_forward.HEAT_LOG_ONLY_BOOK_IDS

# LAB_FORWARD_TESTS_V1 memory of this Lab process: the LAB_A surge state of each
# pool's last two observations and its liquidity samples (prices, segments and
# gaps come from the defensive layer's PairHistory). Fed with every entry refresh.
FORWARD_MEMORY=lab_forward.ForwardFeedMemory()

def lab_forward_memory():
    return FORWARD_MEMORY

# LAB_FORWARD_SIGNAL_CARRY_V2: matched forward-test signals whose only blocker is the
# asynchronous price cross-check, retried on later refreshes (in memory, bounded).
FORWARD_SIGNAL_CARRY=lab_forward.SignalCarry()

def lab_forward_signal_carry():
    return FORWARD_SIGNAL_CARRY

def forward_cost_model():
    """This process's resolved Lab cost model, compared with the one in the forward books' config hash."""
    return {'execution_model':EXECUTION_MODEL_VERSION,'generic_dex_fee_bps':GENERIC_DEX_FEE_BPS,
            'base_slippage_bps':BASE_SLIPPAGE_BPS,'latency_buffer_bps':LATENCY_BUFFER_BPS,
            'network_fee_sol':NETWORK_FEE_SOL,'max_price_impact_pct':MAX_PRICE_IMPACT_PCT}

def forward_cooldown_remaining_ms(book,coin,now):
    """A forward book's re-entry wait on this pool: the Lab's per-token pause or the research 300 s pool cooldown."""
    return max(activity.cooldown_remaining_ms(book,coin['address'],now),
               lab_forward.pool_cooldown_remaining_ms(book,coin,now))

def cost_first_exit_reason(exits,net_pct,hold_minutes):
    """Net-basis exits of one COST_FIRST book; gaps below the stop are not clamped."""
    if net_pct<=-exits.stop_loss_net_pct: return exits.stop_reason
    if net_pct>=exits.take_profit_net_pct: return exits.take_profit_reason
    if hold_minutes>=exits.max_hold_minutes: return exits.max_hold_reason
    return None

# Realistic paper execution costs for Strategy Lab. These are applied equally to
# every strategy so comparisons stay fair. The main engine remains untouched.
GENERIC_DEX_FEE_BPS=float(os.getenv('NEO_LAB_GENERIC_DEX_FEE_BPS','30'))
BASE_SLIPPAGE_BPS=float(os.getenv('NEO_LAB_BASE_SLIPPAGE_BPS','10'))
LATENCY_BUFFER_BPS=float(os.getenv('NEO_LAB_LATENCY_BUFFER_BPS','10'))
NETWORK_FEE_SOL=float(os.getenv('NEO_LAB_NETWORK_FEE_SOL','0.0001'))
MAX_PRICE_IMPACT_PCT=float(os.getenv('NEO_LAB_MAX_PRICE_IMPACT_PCT','20'))
EXECUTION_MODEL_VERSION='DEX_SPOT_MODELED_COSTS_V3_VERIFIED_SOL_DENOMINATION'
SOL_QUOTE_MINT='So11111111111111111111111111111111111111112'
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
    if active_paper.applies(book):
        trades=[t for t in trades if active_paper.is_active_position(t)]
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

def quote_token_address(c):
    """Resolve feed/DEX quote identity without guessing a missing denomination."""
    normalized=c.get('quoteTokenAddress')
    raw_token=c.get('quoteToken')
    if raw_token is not None and not isinstance(raw_token,dict):
        return None
    raw=(raw_token or {}).get('address')
    if normalized is not None and raw is not None and normalized!=raw:
        return None
    return normalized if normalized is not None else raw

def sol_usd_from_coin(c):
    # priceNative is quoted in this pool's quote token, which may be a stablecoin
    # or another asset. Its USD ratio values SOL only for an identified SOL pool.
    if quote_token_address(c)!=SOL_QUOTE_MINT:
        return 0.0
    p=num(c.get('priceUsd')); n=num(c.get('priceNative'))
    value=p/n if p>0 and n>0 else 0.0
    return value if math.isfinite(value) and value>0 else 0.0

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
    sol_usd=sol_usd_from_coin(c)
    if sol_usd<=0:
        raise ValueError('network_price_unknown')
    liq=max(pair_liquidity_usd(c),1.0)
    impact=min(MAX_PRICE_IMPACT_PCT,(2.0*max(0.0,trade_value)/liq)*100.0)
    return {
      'impact_pct':impact,
      'slippage_pct':BASE_SLIPPAGE_BPS/100.0,
      'latency_pct':LATENCY_BUFFER_BPS/100.0,
      'dex_fee_bps':pumpswap_fee_bps(c),
      'network_fee_usd':NETWORK_FEE_SOL*sol_usd,
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

# LAB_FORWARD_TESTS_V1 booking: the shared model above (unchanged, frozen against
# lab_paired_costs.py) with a declared extra price penalty per leg (CALIB_V1),
# added to the model's impact + slippage + latency penalty exactly as the
# research harness adds extra_bps. extra_bps 0 returns the shared model's result.
def calibrated_entry_execution(c,notional,extra_bps):
    out=dict(entry_execution(c,notional))
    if not extra_bps:
        return out
    penalty=(out['impact_pct']+out['slippage_pct']+out['latency_pct']+extra_bps/100.0)/100.0
    out['fill_price']=out['market_price']*(1+penalty)
    out['quantity']=max(0.0,notional-out['dex_fee_usd'])/max(out['fill_price'],1e-18)
    out['calibration_extra_pct']=extra_bps/100.0
    return out

def calibrated_exit_execution(c,qty,extra_bps,drain_aware=False):
    """drain_aware (LAB_FORWARD_CLOSE_POLICY_V1): the impact is never below the constant-product
    x/(1+x) of the reported liquidity, and a reported liquidity of 0 sells for nothing."""
    out=dict(exit_execution(c,qty))
    impact=out['impact_pct']
    if drain_aware:
        impact=lab_forward.drain_aware_impact_pct(out['market_value_usd'],lab_forward.reported_liquidity_usd(c),impact)
    if not extra_bps and impact==out['impact_pct']:
        return out
    penalty=(impact+out['slippage_pct']+out['latency_pct']+extra_bps/100.0)/100.0
    fill=max(0.0,out['market_price']*(1-penalty))
    gross=max(0.0,qty*fill)
    dex_fee=gross*out['dex_fee_bps']/10000.0
    out.update({'impact_pct':impact,'fill_price':fill,'gross_proceeds_usd':gross,'dex_fee_usd':dex_fee,
                'net_proceeds_usd':max(0.0,gross-dex_fee-out['network_fee_usd']),
                'calibration_extra_pct':extra_bps/100.0})
    if drain_aware:
        out['exit_impact_model']=lab_forward.CLOSE_POLICY_VERSION
    return out

def forward_exit_execution(c,pos,qty=None):
    """Booked exit of a LAB_FORWARD_TESTS_V1 position: CALIB_V1 plus the drain-aware impact."""
    return calibrated_exit_execution(c,num(pos.get('quantity')) if qty is None else qty,
                                     lab_forward.position_calib_bps(pos),drain_aware=True)

# LAB_FORWARD_FILL_BASIS_V4: the booked model, re-run at the research fills of a close.
def research_fill_entry_execution(c,notional,extra_bps):
    return calibrated_entry_execution(c,notional,extra_bps)

def research_fill_exit_execution(c,qty,extra_bps):
    return calibrated_exit_execution(c,qty,extra_bps,drain_aware=True)

def complete_forward_research_fill(row):
    """Value a forward close's research-fill shadow once both legs are known (shadow fields only)."""
    return lab_forward.complete_research_fill(row,research_fill_entry_execution,research_fill_exit_execution)

def forward_fill_observations(key,prices,now,extra=()):
    """Observations of one exact pool this loop (shared feed, exact-pair refresh), oldest first."""
    coins=[coin for coin in (prices.get(key),*extra) if isinstance(coin,dict)]
    return sorted(coins,key=lambda coin:num(lab_forward.observation_ms(coin,now)))

def advance_forward_fill_legs(record,coins,now):
    """Advance the pending research-fill legs of a forward position or close with this loop's observations."""
    shadow=record.get('research_fill') if isinstance(record,dict) else None
    if not isinstance(shadow,dict):
        return False
    key=(record.get('address'),record.get('pairAddress'))
    changed=False
    for side in ('entry','exit'):
        leg=shadow.get(side)
        for coin in coins:
            changed=lab_forward.advance_fill_leg(leg,coin,now,key=key) or changed
        changed=lab_forward.advance_fill_leg(leg,None,now,key=key) or changed
    return changed

# Forward books whose whole history was scanned for research-fill shadows left
# pending (for example by a restart) in this process; later loops scan recent closes.
FORWARD_FILL_FULL_SCAN=set()

def advance_forward_research_fills(prices,now):
    """LAB_FORWARD_FILL_BASIS_V4: resolve and value the research fills of recent forward closes.

    Observations: the pool in the shared feed, else the exact-pair refresh (which
    this schedules for a closed pool that left the feed). Only shadow fields change.
    """
    for book_id in lab_forward.BOOK_IDS:
        book=STATE['books'].get(book_id)
        if not isinstance(book,dict):
            continue
        full=book_id not in FORWARD_FILL_FULL_SCAN
        rows=lab_forward.pending_fill_rows(book,now,full_scan=full)
        FORWARD_FILL_FULL_SCAN.add(book_id)
        for row in rows:
            key=(row.get('address'),row.get('pairAddress'))
            extra=()
            if key not in prices and not lab_forward.fill_legs_resolved(row):
                extra=(POSITION_MARK_FEED.resolve(row,prices,now),)
            advance_forward_fill_legs(row,forward_fill_observations(key,prices,now,extra),now)
            complete_forward_research_fill(row)

def load_json(path,default):
    try: return json.loads(read_shared_text(path,encoding='utf-8'))
    except Exception: return default

def atomic_write_path(path,data):
    """Write ``data`` as JSON atomically (fsync, then replace); returns the bytes written."""
    path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_name(f'{path.name}.{os.getpid()}.{threading.get_ident()}.tmp')
    # json.dumps runs the C encoder (json.dump streams through the pure-Python one): the
    # same bytes, several times faster on a large ledger rewritten every loop.
    text=json.dumps(data,ensure_ascii=False,allow_nan=False)
    with tmp.open('w',encoding='utf-8') as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
        size=os.fstat(handle.fileno()).st_size
    for attempt in range(8):
        try:
            tmp.replace(path)
            return size
        except PermissionError:
            if attempt==7:
                raise
            time.sleep(min(.4,.025*(2**attempt)))

def atomic_write(data):
    return atomic_write_path(STATE_PATH,data)

# LAB_PERSIST_METRICS_V1: size and duration of the last whole-ledger write (the Lab rewrites
# and fsyncs its ledger every loop; a growing ledger slows marks, exits and entries).
PERSIST_METRICS_VERSION='LAB_PERSIST_METRICS_V1'
PERSIST_METRICS={}

def record_persist_metrics(ledger_bytes,compact_bytes,seconds,at):
    PERSIST_METRICS.clear()
    PERSIST_METRICS.update({
        'version':PERSIST_METRICS_VERSION,'measured_at':int(at),
        'ledger_bytes':int(ledger_bytes) if isinstance(ledger_bytes,int) else None,
        'compact_bytes':int(compact_bytes) if isinstance(compact_bytes,int) else None,
        'write_seconds':round(max(0.0,seconds),3),'poll_seconds':POLL_SECONDS,
        # Writes alone take a share of the loop's period; near or above 1 the loop falls behind.
        'write_share_of_poll':round(max(0.0,seconds)/POLL_SECONDS,3) if POLL_SECONDS>0 else None,
        'basis':'the previous write (a write cannot publish its own size)'})

def flow_map():
    global FLOW_TAPE_DIAGNOSTICS
    tape=load_json(LIVE_TAPE_PATH,{})
    now=now_ms(); cutoff=now-60_000; verified_cutoff=now-promoted_guard.FLOW_WINDOW_MS
    tape_stamp=num(tape.get('updated_at'))
    tape_fresh=bool(tape_stamp>0 and 0<=now-tape_stamp<=promoted_guard.FLOW_MAX_AGE_MS)
    coverage=tape.get('pair_coverage') or {}
    out={}; verified_events=0
    for e in tape.get('events',[]):
        pair=str(e.get('pairAddress') or '')
        pair_state=coverage.get(pair,{})
        if pair_state.get('status')!='COMPLETE': continue
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
        pair_poll=num(pair_state.get('last_poll_at'))
        if (tape_fresh and pair_state.get('address')==a and pair_state.get('pairAddress')==pair
                and pair_poll>0 and 0<=now-pair_poll<=promoted_guard.FLOW_MAX_AGE_MS
                and event_time>=verified_cutoff):
            proof=f.setdefault('verified_flow',{
                'source':promoted_guard.FLOW_SOURCE,'coverage_status':'COMPLETE',
                'window_ms':promoted_guard.FLOW_WINDOW_MS,'address':a,'pairAddress':pair,
                'window_at':tape_stamp,'latest_event_at':0,'available_at':0,
                'trades':0,'unique_wallets':set(),'buy_usd':0.0,'sell_usd':0.0,
            })
            proof['trades']+=1
            proof['latest_event_at']=max(proof['latest_event_at'],event_time)
            proof['available_at']=max(proof['available_at'],available)
            proof['unique_wallets'].add(wallet)
            proof['buy_usd' if e.get('direction')=='BUY' else 'sell_usd']+=usd
    for f in out.values():
        f['unique_wallets']=len(f.pop('wallets')); f['ratio']=f['buy_usd']/max(f['sell_usd'],1)
        f['window_at']=now
        proof=f.get('verified_flow')
        if proof:
            proof['unique_wallets']=len(proof['unique_wallets'])
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
    features=activity.market_features(c,flow)
    exact=flows.get((address,pair)) or {}
    if exact.get('verified_flow'):
        features['verified_flow']=exact['verified_flow']
    return features

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
 {'id':'ULTRA_PRECISION','name':'Ultra Precision','rule':activity.RULES['ULTRA_PRECISION'].matches},
 {'id':'PRECISION','name':'Precision','rule':activity.RULES['PRECISION'].matches},
 {'id':'MOMENTUM','name':'Momentum','rule':activity.RULES['MOMENTUM'].matches},
 {'id':'MOMENTUM_RUSH_BRAIN','name':'Momentum Rush Brain','rule':lambda f: activity.RULES['MOMENTUM_RUSH_BRAIN'].matches(f)},
 {'id':'BREAKOUT','name':'Breakout','rule':lambda f: f['score']>=90 and f['liq']>=20000 and 15<f['m5']<=55 and f['bs']>=1.4 and f['lmc']>=.08 and 3<=f['age']<=300},
 {'id':'LIQUIDITY','name':'Liquidity First','rule':lambda f: f['score']>=85 and f['liq']>=40000 and -2<=f['m5']<=20 and f['bs']>=.9 and f['lmc']>=.12 and 5<=f['age']<=720},
 {'id':'ORDER_FLOW','name':'Order Flow','rule':lambda f: f['score']>=85 and f['liq']>=15000 and -5<=f['m5']<=25 and f['flow']['trades']>=3 and f['flow']['ratio']>=1.3 and f['flow']['unique_wallets']>=1 and f['flow']['max_sell']<max(750,f['flow']['buy_usd']*.8)},
 {'id':'EARLY','name':'Early Runner','rule':activity.RULES['EARLY'].matches},
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

 # COST_FIRST_ESTABLISHED_V1: one isolated TEST book pair on a cost-defined
 # universe (cost_first_established.py). Same universe, same evidence gates,
 # different exits; never promoted automatically, no outcome is assumed.
 {'id':cost_first.CONTROL_BOOK_ID,'name':cost_first.BOOK_NAMES[cost_first.CONTROL_BOOK_ID],'rule':activity.RULES[cost_first.CONTROL_BOOK_ID].matches},
 {'id':cost_first.SCALED_BOOK_ID,'name':cost_first.BOOK_NAMES[cost_first.SCALED_BOOK_ID],'rule':activity.RULES[cost_first.SCALED_BOOK_ID].matches},

 # LAB_FORWARD_TESTS_V1: two pre-registered research hypotheses and their random
 # controls (lab_forward_tests.py). Universe and signal are evaluated by that
 # module on each observation; heat is log-only; never promoted automatically.
 *({'id':book_id,'name':lab_forward.BOOK_NAMES[book_id],'rule':activity.RULES[book_id].matches}
   for book_id in lab_forward.BOOK_IDS),
]
def promoted_candidate_config():
    """Expose precisely the rules used at funded admission, never legacy lambdas."""
    return funded_candidates.candidate_config()


def empty_book(s):
    start=STRATEGY_START_BALANCES.get(s['id'],START_BALANCE)
    return {'id':s['id'],'name':s['name'],'starting_balance':start,'balance':start,
            'portfolio_group':'PROMOTED_PAPER' if s['id'] in PROMOTED_STRATEGIES else 'TEST',
            'allocation_usd':start,
            'max_position_fraction':(PROMOTED_MAX_POSITION_FRACTION if s['id'] in PROMOTED_STRATEGIES
                                     else activity.RUSH_MAX_BALANCE_FRACTION if s['id']==rush_brain.STRATEGY_ID else 1.0),
            'position':None,'history':[],'trade_seq':0,'last_entry_by_address':{},'created_at':now_ms()}

def registry_compatibility(books):
    """Retain custom ledgers without inventing their entry or exit policy."""
    registered={s['id'] for s in STRATEGIES}
    preserved=[]
    for key,book in books.items():
        if not isinstance(book,dict):
            raise ValueError(f'Cannot preserve unsupported Lab book schema: {key}')
        if key in registered:
            marker=book.get('runtime_compatibility')
            if isinstance(marker,dict) and marker.get('version')=='LAB_REGISTRY_COMPATIBILITY_V1':
                book.pop('runtime_compatibility',None)
            continue
        preserved.append(key)
        book['runtime_compatibility']={
            'version':'LAB_REGISTRY_COMPATIBILITY_V1','status':'preserved_inactive',
            'reason':'strategy_not_registered','entry_enabled':False,
            'position_management_enabled':False,
        }
    return {
        'version':'LAB_REGISTRY_COMPATIBILITY_V1',
        'status':'attention_required' if preserved else 'compatible',
        'preserved_strategy_ids':sorted(preserved),
        'preserved_open_position_ids':sorted(
            key for key in preserved if books[key].get('position') or books[key].get('positions')),
    }

def lifecycle_activity_versions():
    """Entry-policy versions whose closes count as retirement evidence, per own-policy book.

    The COST_FIRST pair stamps cost_first.ENTRY_POLICY_VERSION (V2 since the
    structural rug guard joined its universe) and every other TEST book the
    shared LAB_ACTIVE policy. LAB_STRATEGY_LIFECYCLE_V2 also counts the closes
    of the entry-only predecessor (COST_FIRST_ESTABLISHED_V1, LAB_ACTIVE_V6),
    so the defensive-layer bump never delays a retirement.
    """
    return {book_id:cost_first.LIFECYCLE_EVIDENCE_VERSIONS for book_id in cost_first.BOOK_IDS}

def review_strategy_lifecycle(books):
    now=now_ms()
    registered={s['id'] for s in STRATEGIES}
    # The four LAB_FORWARD_TESTS_V1 books are judged only by their pre-registered
    # kill rule (>= 50 closes, mean net50 < 0 and CI95 upper < 0), never by the
    # shared 12-close heuristic, which would stop a negative-expectancy control
    # before the comparison it exists for.
    review=lifecycle.apply_lifecycle(
        books,registered_ids=registered-set(lab_forward.BOOK_IDS),
        promoted_ids=set(PROMOTED_STRATEGIES),activity_version=activity.LIFECYCLE_EVIDENCE_VERSIONS,
        activity_versions=lifecycle_activity_versions(),
        execution_version=EXECUTION_MODEL_VERSION,now=now)
    return lab_forward.apply_kill_rules(books,review,registered_ids=registered,now=now)

def load_state():
    global RESET_REQUESTED
    reset_requested=RESET_FLAG_PATH.exists()
    # The HF books (strategy_lab_hf/) follow a requested Lab reset (archived, never deleted).
    RESET_REQUESTED=RESET_REQUESTED or reset_requested
    if reset_requested:
        raw={}
        try: RESET_FLAG_PATH.unlink()
        except Exception: pass
    else:
        raw=load_json(STATE_PATH,None) if STATE_PATH.exists() else {}
        if not isinstance(raw,dict):
            # An unreadable ledger must never become 34 fresh books on the next save.
            raise RuntimeError('Strategy Lab state is unreadable; refusing automatic reset')
    stored_books=raw.get('books') or {}
    # A server can contain a locally customized strategy such as Momentum Swarm.
    # Keep its full ledger in place; removing a registration must not erase it.
    books=dict(stored_books)
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
            b['allocation_usd']=num(b.get('starting_balance'),PROMOTED_ALLOCATION)
            b['max_position_fraction']=PROMOTED_MAX_POSITION_FRACTION
        elif s['id'] in PROMOTED_STRATEGIES:
            b['portfolio_group']='TEST'
            b['allocation_usd']=num(b.get('starting_balance'),START_BALANCE)
            b['max_position_fraction']=1.0
        else:
            b['portfolio_group']='TEST'
            b['allocation_usd']=b.get('allocation_usd',b.get('starting_balance',START_BALANCE))
            b['max_position_fraction']=activity.RUSH_MAX_BALANCE_FRACTION if s['id']==rush_brain.STRATEGY_ID else 1.0
        active_paper.synchronize_alias(b)
        books[s['id']]=b
    compatibility=registry_compatibility(books)
    # Legacy/draining cohorts must finish promotion and manage their exits;
    # authorization never turns an old TEST book into a funded trading book.
    funding=active_paper.apply_authorized_funding(books,now_ms()) if cohort_active or not stored_books else []
    exit_changes=active_paper.apply_capacity_exit_policy(books,now_ms())
    exit_changes+=active_paper.apply_adaptive_exit_policy(books,now_ms())
    setup=dict(raw.get('portfolio_setup') or {})
    if all(books[sid].get('starting_balance')==1000 for sid in PROMOTED_STRATEGIES):
        setup.update(total_allocated_capital_usd=4000.0,allocation_per_strategy_usd=1000.0)
        if not stored_books:
            setup.update(version='PROMOTED_PAPER_COHORT_V1',status='ACTIVE')
    return {'started_at':now_ms() if reset_requested else (raw.get('started_at') or now_ms()),
            'updated_at':now_ms(),'status':'starting','books':books,
            'stats':raw.get('stats') or {},'registry_compatibility':compatibility,
            'strategy_lifecycle':review_strategy_lifecycle(books),
            'activity_version':raw.get('activity_version'),
            'activity_started_at':raw.get('activity_started_at'),
            'portfolio_setup':setup or None, '_funding_requires_persist':bool(funding),
            '_exit_policy_requires_persist':bool(exit_changes),
            **({'exit_research':raw['exit_research']} if 'exit_research' in raw else {})}

STATE={'started_at':now_ms(),'updated_at':now_ms(),'status':'starting',
       'books':{s['id']:empty_book(s) for s in STRATEGIES}}
# Set once main() has loaded the durable ledger; shutdown persists only after that.
LOADED=False
# Whether this process found the Lab reset flag (load_state consumes the flag file).
RESET_REQUESTED=False
# LAB_HIGH_FREQUENCY_V1 container (built by main() when NEO_LAB_HF_ENABLED is '1', the default).
HF=None
# HF_MARK_FEED_V1: HF's own exact-pair mark feed (created with the first HF build).
HF_MARK_FEED=None
# HF build attempts of this process; a failed build is retried every HF_BUILD_RETRY_MS.
HF_BUILD_RETRY_MS=60_000
HF_BUILD={'attempts':0,'failures':0,'last_attempt_at':None,'last_error':None,'next_attempt_at':None,
          'built_at':None}
if STATE.get('activity_version')!=activity.POLICY_VERSION:
    STATE['activity_version']=activity.POLICY_VERSION
    STATE['activity_started_at']=now_ms()
assert set(activity.RULES)=={s['id'] for s in STRATEGIES}, 'Every Lab strategy needs an activity policy'

def close_position(book,pos,coin,reason):
    market_price=num(coin.get('priceUsd')); qty=num(pos.get('quantity'))
    # LAB_FORWARD_TESTS_V1 books book the exit leg with CALIB_V1 and the drain-aware
    # impact of LAB_FORWARD_CLOSE_POLICY_V1; every other book keeps the shared model.
    forward=lab_forward.is_forward_position(pos)
    quote=forward_exit_execution(coin,pos,qty) if forward else exit_execution(coin,qty)
    cost_basis=num(pos.get('remaining_cost_basis_usd'),num(pos.get('notional_usd'))+num(pos.get('entry_network_fee_usd')))
    final_pnl=quote['net_proceeds_usd']-cost_basis
    partial_pnl=num(pos.get('partial_realized_pnl'))
    total_pnl=partial_pnl+final_pnl
    original_notional=num(pos.get('notional_usd'))
    pct=total_pnl/max(original_notional,1e-18)*100
    # LAB_FORWARD_CONTROL_CONTINUITY_V1: a control's zero-capital measurement records its
    # P&L like any close but never moves the balance (it was entered past the funding).
    zero_capital=lab_forward.is_zero_capital_position(pos)
    if not zero_capital:
        book['balance']=round(num(book['balance'])+final_pnl,8)
    trade={**pos,'exit_price':market_price,'execution_exit_price':round(quote['fill_price'],12),
           'closed_at':now_ms(),'exit_reason':reason,'final_leg_pnl_usd':round(final_pnl,4),
           'pnl_usd':round(total_pnl,4),'pnl_pct':round(pct,3),'balance_after':round(book['balance'],4),
           'exit_dex_fee_usd':round(quote['dex_fee_usd'],6),'exit_network_fee_usd':round(quote['network_fee_usd'],6),
           'exit_price_impact_pct':round(quote['impact_pct'],4),
           'exit_slippage_pct':round(quote['slippage_pct']+quote['latency_pct'],4),
           'execution_mode':EXECUTION_MODEL_VERSION,
           'execution_source':'DEX_SPOT_WITH_MODELED_FRICTION'}
    if forward:
        # Uncalibrated model result, the calibration and drain costs, net50 (booked minus the
        # research stress) and the close kind (marked, drained or vanished).
        model=exit_execution(coin,num(pos.get('model_quantity')))
        capped=calibrated_exit_execution(coin,qty,lab_forward.position_calib_bps(pos))
        trade.update(lab_forward.close_record(pos,trade,model_net_proceeds_usd=model['net_proceeds_usd'],
                                              reason=reason,capped_net_proceeds_usd=capped['net_proceeds_usd'],
                                              booked_net_proceeds_usd=quote['net_proceeds_usd'],
                                              exit_liquidity_usd=lab_forward.reported_liquidity_usd(coin),
                                              exit_coin=coin,exit_at=trade['closed_at']))
        trade['balance_effect_usd']=0.0 if zero_capital else round(final_pnl,4)
        # LAB_FORWARD_FILL_BASIS_V4: a VANISHED close has no exit fill, so its shadow may be complete now.
        complete_forward_research_fill(trade)
    book['history'].insert(0,trade)
    if isinstance(book.get('positions'),list): active_paper.remove(book,pos)
    else: book['position']=None

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

# LAB_FORWARD_CLOSE_POLICY_V1: when this Lab process first failed to get a usable
# mark of a held forward-test pool (in memory: a restart first retries the exact-pair
# refresh for the confirm window). At most one entry per forward book.
FORWARD_UNPRICED_SINCE={}

def forward_position_key(pos):
    return (pos.get('strategy_id'),pos.get('trade_no'),pos.get('opened_at'))

def close_vanished_forward_position(book,pos,now,feed_alive):
    """Close a forward-test position whose exact pool gave no usable mark beyond max hold + 10 min
    at its last mark minus the research's 10% VANISH haircut (drain-aware booked exit)."""
    key=forward_position_key(pos)
    for stale in [k for k in FORWARD_UNPRICED_SINCE if k[0]==key[0] and k!=key]:
        FORWARD_UNPRICED_SINCE.pop(stale,None)
    since=FORWARD_UNPRICED_SINCE.setdefault(key,now)
    if not lab_forward.vanish_due(pos,now,since,feed_alive):
        return False
    coin=lab_forward.vanished_coin(pos)
    if coin is None or sol_usd_from_coin(coin)<=0:
        # No valuable last mark: keep the position open; the gate counts it as unpriced.
        pos['quote_unavailable_reason']='vanished_without_valuable_last_mark'
        return False
    FORWARD_UNPRICED_SINCE.pop(key,None)
    close_position(book,pos,coin,lab_forward.CLOSE_POLICY.vanish_reason)
    return True

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
    registered={s['id'] for s in STRATEGIES}
    managed_books=[book for key,book in STATE['books'].items() if key in registered]
    for book in [*managed_books,*exit_only_books]: active_paper.synchronize_alias(book)
    # LAB_FORWARD_CLOSE_POLICY_V1: a held pool may be declared vanished only while
    # the shared feed itself is alive (a feed outage is not a vanished pool).
    forward_feed_alive=lab_forward.feed_alive(feed,now_ms())
    for book,pos in [(b,p) for b in [*managed_books,*exit_only_books] for p in active_paper.positions(b)]:
        decision_at=now_ms()
        is_rush=pos.get('strategy_id',book.get('id'))==rush_brain.STRATEGY_ID
        is_active_position=active_paper.is_active_position(pos)
        forward=lab_forward.is_forward_position(pos)
        coin=POSITION_MARK_FEED.resolve(pos,prices,decision_at)
        if forward:
            # LAB_FORWARD_FILL_BASIS_V4: this loop's observations of the held pool decide where the
            # research would have filled the entry (shadow only; booking and exits are unchanged).
            advance_forward_fill_legs(pos,forward_fill_observations(
                (pos.get('address'),pos.get('pairAddress')),prices,decision_at,(coin,)),decision_at)
        mark_stamp=num((coin or {}).get('mark_received_at'),num((coin or {}).get('updatedAt')))
        rush_mark_fresh=(coin and coin.get('address')==pos.get('address')
                         and coin.get('pairAddress')==pos.get('pairAddress')
                         and num(coin.get('priceUsd'))>0 and mark_stamp>0
                         and 0<=decision_at-mark_stamp<=POSITION_STALE_AFTER_MS)
        if not coin or ((is_rush or is_active_position) and not rush_mark_fresh):
            age=max(0,now_ms()-int(num(pos.get('updated_at'))))
            pos['quote_status']='stale' if age>POSITION_STALE_AFTER_MS else 'refreshing'
            pos['quote_age_ms']=age
            pos['quote_unavailable_reason']='exact_pool_not_in_recent_entry_feed'
            if forward: close_vanished_forward_position(book,pos,decision_at,forward_feed_alive)
            continue
        if sol_usd_from_coin(coin)<=0:
            # Keep the complete held position and its last valuation while the
            # current network cost cannot be valued. Do not realize a free exit.
            pos['quote_status']='unavailable'
            pos['quote_age_ms']=max(0,decision_at-int(num(pos.get('mark_received_at'),num(pos.get('updated_at')))))
            pos['quote_unavailable_reason']='network_price_unknown'
            if forward: close_vanished_forward_position(book,pos,decision_at,forward_feed_alive)
            continue
        if forward:
            # A usable mark of the exact pool: reset the no-mark clock, keep it for a VANISHED valuation.
            # LAB_FORWARD_MARK_LIQUIDITY_V2: a mark that omits the pool liquidity is a reported 0 (the
            # shared feed and the exact-pair refresh normalize it alike): its price triggers the exits
            # and the drain-aware exit books the sale at 0, as for an explicit 0.
            FORWARD_UNPRICED_SINCE.pop(forward_position_key(pos),None)
            pos['last_mark']=lab_forward.mark_snapshot(coin,num(coin.get('mark_received_at'),decision_at),
                                                       pos.get('last_mark'))
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
        live_quote=(forward_exit_execution(coin,pos,remaining_qty) if forward
                    else exit_execution(coin,remaining_qty))
        remaining_basis=num(pos.get('remaining_cost_basis_usd'),num(pos.get('notional_usd'))+num(pos.get('entry_network_fee_usd')))
        open_pnl=live_quote['net_proceeds_usd']-remaining_basis
        total_live_pnl=num(pos.get('partial_realized_pnl'))+open_pnl
        total_live_pct=total_live_pnl/max(num(pos.get('notional_usd')),1e-18)*100
        # LAB_FORWARD_TESTS_V1: booked marks carry CALIB_V1 (and the drain-aware impact);
        # exits trigger on the uncalibrated model net of the model quantity, as the research's did.
        model_live_pct=None
        if forward:
            model_quote=exit_execution(coin,num(pos.get('model_quantity')))
            model_live_pct=(model_quote['net_proceeds_usd']-remaining_basis)/max(num(pos.get('notional_usd')),1e-18)*100

        # Rush's faster TEST exits use the same net cost model and 3% stop.
        # Standard books retain their 3/10/60 framework.
        peak_net=max(num(pos.get('peak_net_pct'),total_live_pct),total_live_pct)
        entry_liq=num(pos.get('entry_liquidity_usd'))
        current_liq=num(coin.get('liquidityUsd'),num((coin.get('liquidity') or {}).get('usd'),math.nan))
        cost_first_exits=cost_first.EXITS.get(pos.get('strategy_id',book.get('id')))
        if forward:
            # The exits this position was opened with (its stored exit_parameters).
            reason=lab_forward.exit_reason(pos,model_live_pct,hold)
        elif active_paper.is_active_position(pos):
            record_exit_research_mark(book,pos,coin,total_live_pnl,decision_at)
            active_paper.observe_profit_protection(pos,total_live_pct,
                num(coin.get('mark_received_at'),num(coin.get('updatedAt'))))
            reason=active_paper.exit_reason(pos,total_live_pct,hold)
        elif cost_first_exits is not None:
            reason=cost_first_exit_reason(cost_first_exits,total_live_pct,hold)
        elif total_live_pct<=-STOP_LOSS:
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
        if is_rush or is_active_position: pos['peak_net_pct']=peak_net
        if forward: pos['model_pnl_pct']=round(model_live_pct,3)
        if reason: close_position(book,pos,coin,reason)
    # LAB_FORWARD_FILL_BASIS_V4: research fills of recent forward closes (their exit legs
    # resolve after the close); booked results are never changed.
    advance_forward_research_fills(prices,now_ms())
    advance_exit_research(prices,now_ms())


def research_state(now):
    if not active_paper.quality_enabled():
        return None
    try:
        return exit_research.ensure(STATE,forward_cost_model(),now)
    except Exception as error:
        STATE['exit_research_error']=str(error)[:300]
        return None


def research_error(state,error):
    state['observation_errors']+=1
    state['error']=f'{type(error).__name__}: {error}'[:300]


def record_exit_research_mark(book,position,coin,net_usd,now,*,new_entry=False):
    # Measuring exits never grants entry permission, creates a tape seat, fetches
    # a quote or changes an existing position/cash/exit. Failures are published.
    if not active_paper.applies(book) or book.get('promotion_pending'):
        return
    state=research_state(now)
    if state is None:
        return
    try:
        episode=exit_research.start(state,position,now,new_entry=new_entry)
        if episode is not None:
            exit_research.observe(episode,coin,net_usd,now)
    except Exception as error:
        research_error(state,error)


def advance_exit_research(prices,now):
    # Continue shadow paths AFTER the real lot exits, using the shared exact-pool
    # feed only. Missing markets remain censored, never filled at old prices/zero.
    state=research_state(now)
    if state is None:
        return
    for episode in state['episodes']:
        if exit_research.finished(episode):
            continue
        exit_research.expire(episode,now)
        coin=prices.get((episode['address'],episode['pairAddress']))
        stamp=num((coin or {}).get('mark_received_at'),num((coin or {}).get('updatedAt')))
        if (not coin or not 0<=now-stamp<=POSITION_STALE_AFTER_MS
                or stamp<=num(episode.get('last_mark_at')) or sol_usd_from_coin(coin)<=0):
            continue
        try:
            quote=exit_execution(coin,episode['quantity'])
            net=episode['partial_realized_pnl']+quote['net_proceeds_usd']-episode['remaining_cost_basis_usd']
            exit_research.observe(episode,coin,net,now)
        except Exception as error:
            research_error(state,error)
def cost_feasibility_summary(rows,cap):
    """Fee-and-buffer planning floor of the matched candidates; never an admission."""
    return {
        'basis':'OPTIMISTIC_PAPER_FEE_AND_BUFFER_MODEL',
        'is_execution_quote':False,'profitability_proven':False,
        'excluded_costs':['price_impact','network_fees','rent'],
        'checked_market_candidates':len(rows),
        'fixed_cost_infeasible_candidates':sum(row['model_cost_feasible'] is False for row in rows),
        'maximum_roundtrip_cost_pct':cap,
        'minimum_model_roundtrip_cost_pct':min(
            (row['minimum_model_roundtrip_cost_pct'] for row in rows
             if row['minimum_model_roundtrip_cost_pct'] is not None),default=None),
        'best_candidates':sorted(rows,key=lambda row:num(row['minimum_model_roundtrip_cost_pct'],math.inf))[:5],
    }

def maybe_open(feed,flows):
    now=now_ms()
    cost_cap=lab_cost_cap_pct()
    STATE['strategy_lifecycle']=review_strategy_lifecycle(STATE['books'])
    by_pair={}
    for c in feed:
        if activity.usable_feed_coin(c,now):
            key=(c['address'],c['pairAddress'])
            if key not in by_pair or num(c.get('updatedAt'))>num(by_pair[key].get('updatedAt')):
                by_pair[key]=c
    candidates=[(c,enrich(c,flows)) for c in by_pair.values()]
    # Every entry refresh feeds the defensive layer's ticker registry and pair
    # history (idempotent for an already observed coin snapshot).
    defense=entry_defense_layer()
    defense.observe(feed,now)
    # LAB_FORWARD_TESTS_V1: the same refresh feeds the surge/liquidity memory, and
    # the LAB_B market regime of the current minute is computed (cached per minute).
    forward_memory=lab_forward_memory()
    forward_memory.observe(feed,now)
    forward_regime=forward_memory.regime(now,defense.history)
    # Continue gathering causal observations while the Rush book has a position;
    # another strategy's outcomes or number of polls do not affect this memory.
    rush_observations={
        (coin['address'],coin['pairAddress']):rush_brain.observe(coin,features,now)
        for coin,features in candidates
    }
    for strategy in STRATEGIES:
        book=STATE['books'][strategy['id']]
        active_paper.synchronize_alias(book)
        if lab_capacity_test.applies(book):
            lab_capacity_test.fill(book,candidates,now,sys.modules[__name__])
            continue
        is_active=active_paper.applies(book)
        active_capacity=active_paper.capacity(book,now) if is_active else None
        book_stop=active_paper.exit_parameters(strategy['id'])['stop_loss_net_pct'] if is_active else STOP_LOSS
        is_forward=strategy['id'] in lab_forward.BOOK_IDS
        forward_carry=lab_forward_signal_carry() if is_forward else None
        if is_forward:
            # LAB_FORWARD_SIGNAL_CARRY_V2: an episode more than 60 s past its run's first signal
            # was lost to a pending price check (a newer match never extends the window).
            forward_carry.expire(book,now)
        if not lifecycle.entry_enabled(book):
            if is_forward: forward_carry.clear(book,'book_stopped')
            book['entry_diagnostics']={
                'at':now,'blocked_reason':(lab_forward.RETIRED_REASON if is_forward
                                           else 'strategy_retired_observed_losses'),
                'balance_usd':round(num(book.get('balance')),4),
            }
            continue
        if is_active and active_capacity['blocked_reason']:
            book['entry_diagnostics']={'at':now,**active_capacity,'funded_active':active_paper.performance(book,now)}
            continue
        if book.get('position') and not is_active:
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
        if is_active:
            entry_limit=min(entry_limit,active_paper.MAX_NOTIONAL_USD,
                            balance*active_paper.MAX_POSITION_FRACTION,
                            active_capacity['available_exposure_usd'])
        min_notional=activity.entry_minimum_notional(strategy['id'])
        if is_active: min_notional=active_paper.MIN_NOTIONAL_USD
        # Admission cap: the Lab's one rule, 0.5 x net stop (<= 2.75%). A forward-test book
        # applies it to its own pre-registered stop and buys a fixed $200, never a smaller size.
        admission_cap=lab_forward.admission_cost_cap_pct(strategy['id']) if is_forward else cost_cap
        if is_active: admission_cap=active_paper.entry_cost_limit(strategy['id'])
        # The balance an entry must fit (the book's own, except a zero-capital control entry).
        funding_balance=active_capacity['available_exposure_usd'] if is_active else balance
        capital_mode=lab_forward.FUNDED
        if is_forward:
            entry_limit=min(lab_forward.NOTIONAL_USD,max(0.0,balance))
            min_notional=lab_forward.NOTIONAL_USD
            # The config hash covers the resolved cost model: fail closed if this process's
            # model differs from the hashed one (closes would pool across two cost models).
            mismatched=lab_forward.cost_model_mismatches(**forward_cost_model())
            if mismatched:
                forward_carry.clear(book,'book_stopped')
                book['entry_diagnostics']={
                    'at':now,'blocked_reason':lab_forward.COST_MODEL_MISMATCH_REASON,
                    'cost_model_mismatched_fields':mismatched,'balance_usd':round(balance,4),
                    'entry_policy_version':lab_forward.ENTRY_POLICY_VERSION,
                }
                continue
            if (balance<lab_forward.CASH.min_entry_balance_usd
                    and lab_forward.zero_capital_entry_allowed(strategy['id'],STATE['books'],
                                                               {s['id'] for s in STRATEGIES})):
                # LAB_FORWARD_CONTROL_CONTINUITY_V1: a control keeps measuring while its hypothesis
                # can enter; the fixed $200 is a zero-capital entry whose close never moves the balance.
                capital_mode=lab_forward.ZERO_CAPITAL
                funding_balance=lab_forward.CASH.min_entry_balance_usd
                entry_limit=lab_forward.NOTIONAL_USD
            elif balance<lab_forward.CASH.min_entry_balance_usd:
                # LAB_FORWARD_CASH_STATE_V1: the fixed $200 entry plus its network fee cannot be
                # funded; with no open position this book can never trade again.
                forward_carry.clear(book,'book_stopped')
                book['entry_diagnostics']={
                    'at':now,'matched_candidates':0,'cost_rejected':0,
                    'cooldown_rejected':0,'affordable_candidates':0,
                    'price_verification_rejected':0,
                    'blocked_reason':lab_forward.CASH_EXHAUSTED_REASON,
                    'balance_usd':round(balance,4),'risk_limited_notional_usd':round(entry_limit,4),
                    'min_entry_balance_usd':lab_forward.CASH.min_entry_balance_usd,
                    'entry_policy_version':lab_forward.ENTRY_POLICY_VERSION,
                }
                continue
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
        market_rejected=0
        blocked_cost=0
        blocked_network=0
        blocked_price=0
        price_crosscheck_pending=0
        blocked_cooldown=0
        flow_rejected=0
        brain_rejected=0
        temporal_warmup=0
        risk_rejected=0
        candidate_risk_rejected=0
        promoted_flow_rejected=0
        promoted_safety_rejected=0
        promoted_price_rejected=0
        promoted_cost_rejected=0
        promoted_block_reasons={}
        cost_examples=[]
        promoted_candidate_branches={}
        cost_first_rejections={}
        cost_first_universe=0
        cost_first_size_only=0
        rule=activity.RULES[strategy['id']]
        is_promoted=book.get('portfolio_group')=='PROMOTED_PAPER'
        is_cost_first=strategy['id'] in cost_first.BOOK_IDS
        # POOL_LOSS_MEMORY_V1 from this book's own closed history (read-only).
        blocked_pools=pool_loss_memory.index(book.get('history') or [],now)
        heat_log_only=heat_log_only_book(strategy['id'])
        defensive_summary=entry_defense.new_summary()
        defensive_summary['pool_loss_cooldown_pools']=len(blocked_pools)
        defensive_summary['heat_log_only']=heat_log_only
        defensive_rejected=0
        forward_diagnostics=lab_forward.new_diagnostics(strategy['id']) if is_forward else None
        forward_evaluations={}
        # LAB_FORWARD_SIGNAL_CARRY_V2: the last gate each matched forward signal reached,
        # and the observation it reached it on (LAB_FORWARD_FILL_BASIS_V4: the research-fill
        # entry leg of a signal run is decided at the run's first held observation).
        forward_stage={}
        forward_coins={}
        for coin,features in candidates:
            if is_active and any(p.get('address')==coin['address'] for p in active_paper.positions(book)):
                continue
            branches=(funded_candidates.matched_branches(strategy['id'],coin,features)
                      if is_promoted else [])
            if is_forward:
                # Research universe (PumpSwap/SOL, liquidity, fee tier) and the book's
                # signal at this observation; STRUCTURAL_RUG_GUARD_V1 and the loss memory
                # follow in the defensive layer, price identity and the cost cap below.
                forward_key=(coin['address'],coin['pairAddress'])
                evaluation=lab_forward.evaluate(strategy['id'],coin,now,forward_memory,defense.history)
                lab_forward.record_evaluation(forward_diagnostics,evaluation)
                matched=evaluation['matched']
                if matched:
                    # LAB_FORWARD_FILL_BASIS_V4: a fresh match of a pool whose signal is still held
                    # continues that signal run; the harness entered at the run's first signal,
                    # so the research-fill leg decided there is kept (and advanced by this
                    # observation) whether the run enters now or is held again. A run lives at
                    # most 60 s after its first signal (LAB_FORWARD_SIGNAL_CARRY_V2), so a match
                    # after that is the first signal of a new run.
                    continued=forward_carry.continued_evaluation(strategy['id'],forward_key,evaluation,now,
                                                                 coin=coin)
                    if continued is not evaluation:
                        evaluation=continued
                        forward_diagnostics['rematched_carried_signals']+=int(bool(continued['carry']['rematched']))
                elif not evaluation['universe_rejections']:
                    # A signal of this pool that waited only on the price cross-check is retried
                    # on its current observation, through every gate below; that observation
                    # also advances the research-fill leg decided at the run's first signal.
                    carried=forward_carry.carried_evaluation(strategy['id'],forward_key,now,coin=coin)
                    if carried is not None:
                        evaluation,matched=carried,True
                        forward_diagnostics['carried_signals_retried']+=1
                if matched:
                    forward_evaluations[forward_key]=evaluation
                    forward_coins[forward_key]=coin
                    forward_stage[forward_key]='defensive_entry'
                elif evaluation['universe_rejections']:
                    forward_carry.resolve(book,forward_key,'dropped_by_gate',evaluation['universe_rejections'][0])
            elif is_cost_first:
                # Physical cost universe plus STRUCTURAL_RUG_GUARD_V1. Confirmed
                # flow, fresh safety, price identity and the Lab cost cap are
                # still required below.
                universe_rejections=cost_first.rejections(
                    coin,cap_usd=entry_limit,minimum_notional_usd=min_notional,
                    now=now,ticker_registry=defense.registry)
                matched=not universe_rejections
                for reason in universe_rejections:
                    cost_first_rejections[reason]=cost_first_rejections.get(reason,0)+1
                # Passed every physical screen and failed only the size rule.
                cost_first_size_only+=int(universe_rejections==['size_below_minimum'])
                cost_first_universe+=int(matched)
            else:
                matched=bool(branches) if is_promoted else rule.matches(features)
            if not matched:
                if (not is_promoted and not is_cost_first and not is_forward
                        and rule.matches(features,require_flow=False)):
                    flow_rejected+=1
                else:
                    market_rejected+=1
                continue
            signal_candidates+=1
            # DEFENSIVE_ENTRY_LAYER_V1 for every book, before the planning
            # estimate, flow promotion, RugCheck, Jupiter price probes and fills.
            defensive=funded_defensive_decision(book,coin,now,blocked_pools=blocked_pools,
                                               heat_log_only=heat_log_only)
            # Two examples per book keep the 2-second Lab ledger write small.
            entry_defense.record(defensive_summary,defensive,coin,example_limit=2)
            if not defensive['allowed']:
                defensive_rejected+=1
                if is_forward: forward_stage[forward_key]=(defensive.get('reasons') or ['defensive_entry'])[0]
                continue
            # Transparent planning estimate for every book. It never grants
            # admission, replaces a price check, or assumes a future gain.
            feasibility=market_feasibility.execution_feasibility(
                coin,admission_cap,
                base_slippage_bps=BASE_SLIPPAGE_BPS,
                latency_buffer_bps=LATENCY_BUFFER_BPS,
                generic_dex_fee_bps=GENERIC_DEX_FEE_BPS)
            cost_examples.append({
                'symbol':coin.get('symbol'),'address':coin['address'],
                'pairAddress':coin['pairAddress'],**feasibility})
            if is_promoted:
                features={**features,'funded_candidate_branches':branches}
                for branch in branches:
                    promoted_candidate_branches[branch]=promoted_candidate_branches.get(branch,0)+1
            if is_forward: forward_stage[forward_key]='network_price_unknown'
            if sol_usd_from_coin(coin)<=0:
                blocked_network+=1
                continue
            risk=None
            if is_promoted or is_cost_first:
                flow_gate=promoted_guard.flow_admission(coin,features,now_ms())
                if not flow_gate['allow']:
                    promoted_flow_rejected+=1
                    reason=flow_gate['reason']
                    promoted_block_reasons[reason]=promoted_block_reasons.get(reason,0)+1
                    continue
                if is_active:
                    quality_gate=active_paper.quality_admission(coin,features,now_ms(),STATE['books'])
                    if not quality_gate['allow']:
                        reason=quality_gate['reason']
                        promoted_block_reasons[reason]=promoted_block_reasons.get(reason,0)+1
                        continue
                risk=rug_guard.check(coin)
                safety_gate=promoted_guard.risk_admission(coin,risk,now_ms(),rug_guard.TTL_MS)
                if not safety_gate['allow']:
                    promoted_safety_rejected+=1
                    reason=safety_gate['reason']
                    promoted_block_reasons[reason]=promoted_block_reasons.get(reason,0)+1
                    continue
            brain=None; candidate_limit=entry_limit
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
            if is_cost_first:
                # Liquidity-scaled size only shrinks the Lab's own entry limit.
                candidate_limit=cost_first.size_for(pair_liquidity_usd(coin),entry_limit)
                if candidate_limit<min_notional:
                    candidate_risk_rejected+=1
                    continue
            if is_forward: forward_stage[forward_key]='price_verification'
            validation=price_integrity.check(coin)
            if validation.get('status')=='review':
                validation=cached_jupiter_tiebreak(validation,coin,now=now)
                if validation.get('status')!='pass':
                    if is_forward and validation.get('status')=='review' and forward_cooldown_remaining_ms(book,coin,now)>0:
                        # LAB_FORWARD_SIGNAL_CARRY_V2: the cooldown (checked below) blocks this signal
                        # too, so it is not waiting only on the price check and is not carried. The
                        # research harness skips signal points inside its pool cooldown, so a held
                        # signal would also anchor the research fill (V3) where it never entered.
                        forward_stage[forward_key]='reentry_cooldown'
                        blocked_cooldown+=1
                        continue
                    if validation.get('status')=='review':
                        price_crosscheck_pending+=1
                        if is_forward:
                            # LAB_FORWARD_SIGNAL_CARRY_V2: the only blocker so far; carried below.
                            # Forward books never request the RugCheck + Jupiter probe: their random
                            # draws would feed arbitrary pools into the RugCheck, RPC and Jupiter
                            # budget main uses. They use the GeckoTerminal reference (and a tie-break
                            # another book's probe already cached) within the 60 s carry.
                            forward_stage[forward_key]='price_crosscheck_pending'
                        else:
                            schedule_jupiter_price_probe(coin)
                    else:
                        blocked_price+=1
                    continue
            if (validation.get('status')!='pass'
                    or ((is_promoted or is_cost_first or is_forward)
                        and (validation.get('mint')!=coin['address']
                             or validation.get('pair')!=coin['pairAddress']))
                    or (brain and (validation.get('mint')!=coin['address']
                                   or validation.get('pair')!=coin['pairAddress']))):
                if is_promoted or is_cost_first:
                    promoted_price_rejected+=1
                    promoted_block_reasons['promoted_price_identity_unverified']=promoted_block_reasons.get('promoted_price_identity_unverified',0)+1
                blocked_price+=1; continue
            address=coin['address']
            cooldown_ms=activity.cooldown_remaining_ms(book,address,now)
            if is_forward:
                # Research cooldown: 300 s after the book's last close on this pool.
                cooldown_ms=forward_cooldown_remaining_ms(book,coin,now)
                forward_stage[forward_key]='reentry_cooldown'
            if cooldown_ms>0:
                blocked_cooldown+=1
                continue
            checked+=1
            if is_forward: forward_stage[forward_key]='modeled_roundtrip_cost_limit'
            proposed=activity.affordable_entry(
                coin,funding_balance,candidate_limit,entry_execution,exit_execution,
                minimum_notional=min_notional,
                max_entry_cost_pct=admission_cap,
            )
            if proposed is None:
                blocked_cost+=1
                if is_promoted:
                    promoted_cost_rejected+=1
                    promoted_block_reasons['promoted_cost_headroom_insufficient']=promoted_block_reasons.get('promoted_cost_headroom_insufficient',0)+1
                continue
            if is_promoted or is_cost_first:
                cost_gate=promoted_guard.cost_admission(proposed['initial_pnl_pct'],book_stop)
                if not cost_gate['allow']:
                    blocked_cost+=1
                    promoted_cost_rejected+=1
                    reason=cost_gate['reason']
                    promoted_block_reasons[reason]=promoted_block_reasons.get(reason,0)+1
                    continue
            eligible.append((proposed['initial_pnl_pct'],num(features.get('score')),
                             coin,features,proposed,validation,brain,risk,candidate_limit,defensive))
            if is_forward: forward_stage[forward_key]='eligible'
        if is_forward:
            # LAB_FORWARD_SIGNAL_CARRY_V2: a signal whose only blocker was the pending price
            # cross-check (outside its cooldowns) is kept for a later refresh, a newer match
            # continuing its run; one refused by any other gate ends its episode; an
            # eligible one waits for the commit below.
            for forward_key,stage in forward_stage.items():
                if stage=='price_crosscheck_pending':
                    forward_carry.hold(book,forward_key,forward_evaluations[forward_key],now,
                                       coin=forward_coins.get(forward_key))
                    forward_diagnostics['price_crosscheck_pending_signals']+=1
                elif stage!='eligible':
                    forward_carry.resolve(book,forward_key,'dropped_by_gate',stage)
        book['entry_diagnostics']={
            'at':now,'signal_candidates':signal_candidates,
            'defensive_rejected':defensive_rejected,'defensive_entry':defensive_summary,
            'market_rejected_candidates':market_rejected,
            'matched_candidates':checked,'cost_rejected':blocked_cost,
            'network_cost_rejected':blocked_network,
            'cooldown_rejected':blocked_cooldown,'affordable_candidates':len(eligible),
            'price_verification_rejected':blocked_price,
            'price_crosscheck_pending':price_crosscheck_pending,
            'flow_missing_candidates':flow_rejected,
            'risk_limited_notional_usd':round(entry_limit,4),
            'entry_policy_version':(cost_first.ENTRY_POLICY_VERSION if is_cost_first
                                    else lab_forward.ENTRY_POLICY_VERSION if is_forward
                                    else promoted_guard.FUNDED_POLICY_VERSION if is_promoted
                                    else activity.POLICY_VERSION),
            'max_entry_roundtrip_cost_pct':admission_cap,
            # The cap is min(0.5 x net stop, this ceiling): the dashboard names the ceiling when it binds.
            'max_entry_roundtrip_cost_ceiling_pct':activity.MAX_ENTRY_COST_PCT,
            'stop_loss_net_pct':(cost_first.EXITS[strategy['id']].stop_loss_net_pct if is_cost_first
                                 else lab_forward.EXITS[strategy['id']].stop_loss_net_pct if is_forward
                                 else STOP_LOSS),
            'cost_infeasible_candidates':blocked_cost,
            'cost_feasibility':cost_feasibility_summary(cost_examples,admission_cap),
        }
        if is_forward:
            # One fixed size is checked against the cap; it is never reduced to fit.
            book['entry_diagnostics'].update({'entry_size_rule':'FIXED_NOTIONAL_NO_BACKOFF',
                                              'fixed_notional_usd':lab_forward.NOTIONAL_USD})
            if strategy['id']==lab_forward.LAB_B_ID:
                forward_diagnostics['regime']=forward_regime
            forward_diagnostics.update({
                'capital_mode':capital_mode,
                'signal_carry':{**lab_forward.signal_carry_counters(book,strategy['id']),
                                'pending_now':forward_carry.pending_count(strategy['id'])}})
            book['entry_diagnostics']['lab_forward']=forward_diagnostics
            if signal_candidates==0:
                book['entry_diagnostics']['blocked_reason']='no_market_signal'
            elif not eligible and blocked_cooldown and blocked_cooldown+defensive_rejected==signal_candidates:
                # Every signal the defensive layer allowed was in its pool or token cooldown.
                book['entry_diagnostics']['blocked_reason']='reentry_cooldown'
            elif not eligible and price_crosscheck_pending:
                # The signal is carried to the next refresh (LAB_FORWARD_SIGNAL_CARRY_V2).
                book['entry_diagnostics']['blocked_reason']=lab_forward.PRICE_CHECK_PENDING_REASON
        if is_cost_first:
            book['entry_diagnostics'].update({
                'cost_first':{
                    'version':cost_first.VERSION,
                    'universe_version':cost_first.UNIVERSE_VERSION,
                    'universe_candidates':cost_first_universe,
                    'universe_rejections':cost_first_rejections,
                    'flow_rejected':promoted_flow_rejected,
                    'safety_rejected':promoted_safety_rejected,
                    'price_rejected':promoted_price_rejected,
                    'size_rejected':candidate_risk_rejected,
                    'block_reasons':promoted_block_reasons,
                    'exits':asdict(cost_first.EXITS[strategy['id']]),
                    'automatic_promotion':cost_first.AUTOMATIC_PROMOTION,
                    'profitability_proven':False,
                },
                'evidence_guard_version':promoted_guard.VERSION,
            })
            if signal_candidates==0:
                book['entry_diagnostics']['blocked_reason']='no_market_signal'
            elif not eligible and promoted_block_reasons:
                book['entry_diagnostics']['blocked_reason']=next(iter(promoted_block_reasons))
            # Name the actual block when every universe candidate was refused
            # only for size or a same-address re-entry cooldown.
            universe_total=signal_candidates+cost_first_size_only
            size_total=cost_first_size_only+candidate_risk_rejected
            if (not eligible and universe_total>0
                    and size_total+blocked_cooldown==universe_total):
                book['entry_diagnostics']['blocked_reason']=(
                    'reentry_cooldown' if blocked_cooldown else 'cost_first_size_below_minimum')
        if book.get('portfolio_group')=='PROMOTED_PAPER':
            book['entry_diagnostics'].update({
                'promoted_policy_version':active_paper.reporting_version() if is_active else promoted_guard.FUNDED_POLICY_VERSION,
                'promoted_evidence_guard_version':promoted_guard.VERSION,
                'promoted_candidate_policy_source':'FUNDED_MARKET_BRANCHES',
                'promoted_candidate_policy_version':funded_candidates.VERSION,
                'promoted_candidate_branch_counts':promoted_candidate_branches,
                'promoted_flow_rejected':promoted_flow_rejected,
                'promoted_safety_rejected':promoted_safety_rejected,
                'promoted_price_rejected':promoted_price_rejected,
                'promoted_cost_rejected':promoted_cost_rejected,
                'promoted_block_reasons':promoted_block_reasons,
                'promoted_max_entry_roundtrip_cost_pct':admission_cap,
                'promoted_cost_feasibility':cost_feasibility_summary(
                    cost_examples,admission_cap),
                'profitability_proven':False,
            })
            if is_active:
                book['entry_diagnostics'].update({'funded_active':active_paper.performance(book,now),
                    'entry_policy_version':active_paper.reporting_version(),'stop_loss_net_pct':book_stop,
                    'max_entry_roundtrip_cost_pct':admission_cap})
            if signal_candidates==0:
                book['entry_diagnostics']['blocked_reason']='no_market_signal'
            elif not eligible and promoted_block_reasons:
                book['entry_diagnostics']['blocked_reason']=next(iter(promoted_block_reasons))
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
            if blocked_network:
                book['entry_diagnostics']['blocked_reason']='network_price_unknown'
            elif (not book['entry_diagnostics'].get('blocked_reason')
                    and checked>0 and blocked_cost==checked):
                # LAB_ACTIVE_V6: every checked candidate exceeded the cost cap.
                # This is reported, never hidden and never answered by a looser cap.
                book['entry_diagnostics']['blocked_reason']='modeled_roundtrip_cost_limit'
            if signal_candidates>0 and defensive_rejected==signal_candidates:
                # Every matched candidate was removed by the defensive layer:
                # name its most frequent highest-priority reason, not a later gate.
                book['entry_diagnostics']['blocked_reason']=(
                    entry_defense.primary_reason(defensive_summary) or 'defensive_entry')
            continue
        rank=(lambda item:(num((item[6] or {}).get('final_score')),item[0],item[1])) if strategy['id']==rush_brain.STRATEGY_ID else (lambda item:(item[0],item[1]))
        _,_,coin,features,proposed,validation,brain,risk,candidate_limit,defensive=max(eligible,key=rank)
        if is_promoted or is_cost_first:
            # Provider work may outlast the short evidence window. Its clock
            # cannot make a new safety receipt future-dated or revive old flow.
            commit_now=now_ms()
            final_gates=(promoted_guard.flow_admission(coin,features,commit_now),
                         promoted_guard.risk_admission(coin,risk,commit_now,rug_guard.TTL_MS),
                          promoted_guard.cost_admission(proposed['initial_pnl_pct'],book_stop))
            refusal=next((gate['reason'] for gate in final_gates if not gate['allow']),None)
            if refusal:
                book['entry_diagnostics']['blocked_reason']=refusal
                book['entry_diagnostics']['commit_recheck_rejected']=1
                continue
            if is_active:
                quality_gate=active_paper.quality_admission(coin,features,now_ms(),STATE['books'])
                if not quality_gate['allow']:
                    book['entry_diagnostics'].update(blocked_reason=quality_gate['reason'],commit_recheck_rejected=1)
                    continue
        # DEFENSIVE_ENTRY_LAYER_V1 again at commit, as the engine does: on the commit clock
        # and this book's closed history as it is now. The Lab has no newer observation than
        # this refresh, so a pool that turns hot after it is refused at the next refresh;
        # here a stale pair history, a moved 5-minute reference or a new loss refuses it.
        commit_at=now_ms()
        final_defensive=funded_defensive_decision(
            book,coin,commit_at,blocked_pools=pool_loss_memory.index(book.get('history') or [],commit_at),
            heat_log_only=heat_log_only)
        if not final_defensive['allowed']:
            defensive_summary['commit_recheck_blocked']=int(defensive_summary.get('commit_recheck_blocked') or 0)+1
            book['entry_diagnostics']['blocked_reason']=(final_defensive['reasons'] or ['defensive_entry'])[0]
            book['entry_diagnostics']['commit_recheck_rejected']=1
            if is_forward:
                forward_carry.resolve(book,(coin['address'],coin['pairAddress']),'dropped_by_gate','commit_recheck')
            continue
        defensive=final_defensive
        if brain:
            book['entry_diagnostics']['risk_limited_notional_usd']=round(candidate_limit,4)
        address=coin['address']; price=num(coin['priceUsd'])
        notional=proposed['notional']; opening=proposed['entry']; mark=proposed['mark']
        position_stop=(cost_first.EXITS[strategy['id']].stop_loss_net_pct if is_cost_first
                       else lab_forward.EXITS[strategy['id']].stop_loss_net_pct if is_forward else book_stop)
        qty=num(opening['quantity'])
        capital_basis=num(opening['capital_committed_usd'])
        book['trade_seq']=int(book.get('trade_seq',0))+1
        stamp=now_ms()
        position={
            'trade_no':book['trade_seq'],'strategy_id':strategy['id'],
            'symbol':coin.get('symbol'),'name':coin.get('name'),
            'address':address,'pairAddress':coin['pairAddress'],
            'dexId':coin.get('dexId'),'quoteTokenAddress':quote_token_address(coin),
            'entry_price':price,
            'execution_entry_price':round(opening['fill_price'],12),
            'current_price':price,'peak_price':price,'quantity':qty,'original_quantity':qty,
            'notional_usd':notional,'opened_at':stamp,'updated_at':stamp,
            'score':coin.get('score'),'entry_features':features,
            'partial_realized_pnl':0.0,'partial_exits':[],
            'remaining_cost_basis_usd':capital_basis,
            'entry_dex_fee_bps':round(opening['dex_fee_bps'],4),
            'entry_dex_fee_usd':round(opening['dex_fee_usd'],6),
            'entry_network_fee_usd':round(opening['network_fee_usd'],6),
            'entry_network_cost_basis':'IDENTIFIED_SOL_POOL_USD_NATIVE_RATIO',
            'entry_quote_token_address':quote_token_address(coin),
            'entry_price_impact_pct':round(opening['impact_pct'],4),
            'entry_slippage_pct':round(opening['slippage_pct']+opening['latency_pct'],4),
            'execution_mode':EXECUTION_MODEL_VERSION,
            'execution_source':'DEX_SPOT_WITH_MODELED_FRICTION',
            'quote_status':'fresh','mark_received_at':stamp,
            'mark_source':'SHARED_LIVE_FEED_EXACT_POOL','quote_age_ms':0,
            'price_crosscheck':validation,
            'entry_policy_version':(cost_first.ENTRY_POLICY_VERSION if is_cost_first
                                    else lab_forward.ENTRY_POLICY_VERSION if is_forward
                                     else active_paper.reporting_version() if is_active
                                    else promoted_guard.FUNDED_POLICY_VERSION if is_promoted
                                    else activity.POLICY_VERSION),
            'entry_roundtrip_pnl_pct':round(proposed['initial_pnl_pct'],6),
            # LAB_ACTIVE_V6 records: the cap this entry had to satisfy and the
            # gross move left before the net stop after modeled entry costs.
            'entry_cost_cap_pct':admission_cap,
            'stop_loss_net_pct':position_stop,
            'stop_headroom_pct':activity.stop_headroom_pct(position_stop,proposed['initial_pnl_pct']),
            'entry_size_reduced':notional+0.02<candidate_limit,
            # DEFENSIVE_ENTRY_LAYER_V1 decision this entry passed at commit (log-only heat flags included).
            'defensive_entry':entry_defense.compact(defensive),
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
        if book.get('portfolio_group')=='PROMOTED_PAPER':
            position['verified_entry_flow']=features.get('verified_flow')
            position['risk_guard']=risk
            position['entry_evidence_guard_version']=promoted_guard.VERSION
            position['entry_candidate_rule']=promoted_candidate_config()[strategy['id']]
            position['entry_matched_candidate_branches']=features['funded_candidate_branches']
        if is_cost_first:
            exits=cost_first.EXITS[strategy['id']]
            position.update({
                'verified_entry_flow':features.get('verified_flow'),'risk_guard':risk,
                'entry_evidence_guard_version':promoted_guard.VERSION,
                'promotion_eligible':False,
                'entry_universe_version':cost_first.UNIVERSE_VERSION,
                'entry_universe':cost_first.describe(coin,cap_usd=entry_limit,minimum_notional_usd=min_notional,
                                                     now=now,ticker_registry=defense.registry),
                'entry_size_rule':cost_first.config()['size_rule'],
                'entry_liquidity_usd':pair_liquidity_usd(coin),
                'exit_policy_label':exits.label,'exit_parameters':asdict(exits),
            })
        if is_forward:
            # The model leg (above) passed the cap; the booked leg adds CALIB_V1 per leg,
            # so the book holds fewer tokens and every mark sells with the same extra.
            calib=lab_forward.calib_extra_bps_per_leg(opening['dex_fee_bps'],pair_liquidity_usd(coin),notional)
            booked=calibrated_entry_execution(coin,notional,calib['total_bps'])
            booked_qty=num(booked['quantity'])
            booked_mark=calibrated_exit_execution(coin,booked_qty,calib['total_bps'],drain_aware=True)
            booked_pnl=booked_mark['net_proceeds_usd']-num(booked['capital_committed_usd'])
            position.update({
                'quantity':booked_qty,'original_quantity':booked_qty,
                'execution_entry_price':round(booked['fill_price'],12),
                'execution_exit_price':round(booked_mark['fill_price'],12),
                'remaining_cost_basis_usd':num(booked['capital_committed_usd']),
                'booked_entry_roundtrip_pnl_pct':round(booked_pnl/max(notional,1e-18)*100,6),
                'pnl_pct':round(booked_pnl/max(notional,1e-18)*100,3),
                'open_pnl_usd':round(booked_pnl,4),
                'model_pnl_pct':round(proposed['initial_pnl_pct'],3),
                'entry_liquidity_usd':pair_liquidity_usd(coin),
                'entry_fee_tier_bps':round(opening['dex_fee_bps'],4),
                **lab_forward.position_record(
                    strategy['id'],evaluation=forward_evaluations.get((address,coin['pairAddress'])) or {},
                    calib=calib,model_entry=opening,model_mark=proposed['initial_pnl_pct'],
                    defensive_flags=defensive.get('log_only_flags'),entry_coin=coin,entry_at=stamp,
                    capital_mode=capital_mode),
            })
        if is_active:
            position['exit_parameters']=active_paper.exit_parameters(strategy['id'])
            position['exit_policy_label']=position['exit_parameters']['version']
            position['quality_mode']=active_paper.quality_enabled()
            position['peak_net_pct']=proposed['initial_pnl_pct']
            active_paper.attach(book,position)
            record_exit_research_mark(book,position,coin,proposed['initial_pnl_usd'],stamp,new_entry=True)
            book['entry_diagnostics']['funded_active']=active_paper.performance(book,stamp)
        else:
            book['position']=position
        book.setdefault('last_entry_by_address',{})[address]=stamp
        if is_forward:
            # LAB_FORWARD_SIGNAL_CARRY_V2: this pool's episode entered; the book's other pending
            # signals are superseded (one position at a time, as in the research).
            forward_carry.resolve(book,(address,coin['pairAddress']),'entered')
            forward_carry.clear(book,'superseded')
            book['entry_diagnostics']['lab_forward']['signal_carry']={
                **lab_forward.signal_carry_counters(book,strategy['id']),
                'pending_now':forward_carry.pending_count(strategy['id'])}


ACTIVE_POLICY_VERSIONS=frozenset({activity.POLICY_VERSION,promoted_guard.FUNDED_POLICY_VERSION,
                                  cost_first.ENTRY_POLICY_VERSION,lab_forward.ENTRY_POLICY_VERSION,
                                  *active_paper.MANAGED_VERSIONS})

def stats(book):
    start=num(book.get('starting_balance'),START_BALANCE)
    h=book.get('history') or []; wins=[t for t in h if num(t.get('pnl_usd'))>0]
    gp=sum(max(0,num(t.get('pnl_usd'))) for t in h); gl=-sum(min(0,num(t.get('pnl_usd'))) for t in h)
    unreal=0.0
    open_rows=active_paper.positions(book)
    p=book.get('position')
    # A zero-capital control position (LAB_FORWARD_CONTROL_CONTINUITY_V1) never moves equity.
    unreal=sum(num(p.get('open_pnl_usd')) for p in open_rows if not lab_forward.is_zero_capital_position(p))
    zero_capital=[t for t in h if t.get('capital_mode')==lab_forward.ZERO_CAPITAL]
    marked_at=num((p or {}).get('mark_received_at'),num((p or {}).get('updated_at')))
    mark_age_ms=max(0,now_ms()-int(marked_at)) if p else 0
    valuation_stale=any(now_ms()-num(row.get('mark_received_at'),num(row.get('updated_at')))>POSITION_STALE_AFTER_MS
                        or row.get('quote_status') in {'stale','unavailable'} for row in open_rows)
    equity=num(book.get('balance'))+unreal
    partial_count=sum(len(t.get('partial_exits') or []) for t in h)+len((p or {}).get('partial_exits') or [])
    locked_partial=sum(num(t.get('partial_realized_pnl')) for t in h)+num((p or {}).get('partial_realized_pnl'))
    return {**({'funded_active':active_paper.performance(book,now_ms())} if active_paper.applies(book) else {}),
            'open_positions':len(open_rows),
            'trades':len(h),'wins':len(wins),'losses':len(h)-len(wins),'win_rate':round(len(wins)/len(h)*100,1) if h else 0,
            'profit_factor':round(gp/gl,2) if gl>0 else None,
            'profit_factor_status':'finite' if gl>0 else ('infinite_no_losses' if gp>0 else 'undefined_no_results'),
            'realized_pnl':round(num(book.get('balance'))-start,2),
            'unrealized_pnl':round(unreal,2),'total_pnl':round(equity-start,2),
            'equity':round(equity,2),'return_pct':round((equity-start)/max(start,1e-18)*100,2),'open':bool(open_rows),
            'valuation_stale':valuation_stale,'mark_age_ms':mark_age_ms,
            'partial_exits':partial_count,'partial_locked_pnl':round(locked_partial,2),
            'active_policy_trades':sum(t.get('entry_policy_version') in ACTIVE_POLICY_VERSIONS for t in h),
            'active_policy_wins':sum(t.get('entry_policy_version') in ACTIVE_POLICY_VERSIONS and num(t.get('pnl_usd'))>0 for t in h),
            'promoted_policy_trades':sum(t.get('entry_policy_version')==(active_paper.reporting_version() if active_paper.applies(book) else promoted_guard.FUNDED_POLICY_VERSION) for t in h),
            'promoted_policy_wins':sum(t.get('entry_policy_version')==(active_paper.reporting_version() if active_paper.applies(book) else promoted_guard.FUNDED_POLICY_VERSION) and num(t.get('pnl_usd'))>0 for t in h),
            # Closes counted in trades/wins but not in the balance (control measurement past its funding).
            'zero_capital_trades':len(zero_capital),
            'zero_capital_pnl_usd':round(sum(num(t.get('pnl_usd')) for t in zero_capital),2)}

def build_high_frequency():
    """LAB_HIGH_FREQUENCY_V1 container of this Lab process, or None when NEO_LAB_HF_ENABLED is not '1'.

    Its books, journal and checkpoint live in HF_ROOT (strategy_lab_hf/), never in STATE['books']
    or strategy_lab.json. The shared cost model and defensive layer are injected, with HF's own
    exact-pair mark feed (HF_MARK_FEED, never POSITION_MARK_FEED, so an HF refresh never queues
    ahead of a Lab book's); a Lab reset (the flag load_state consumed) archives the HF root's
    state as well, once.
    """
    global HF, HF_MARK_FEED, RESET_REQUESTED
    if not hf_lab.enabled():
        HF=None
        return None
    if HF_MARK_FEED is None:
        # HF_MARK_FEED_V1: HF's own exact-pair feed; the Lab books keep POSITION_MARK_FEED to themselves.
        HF_MARK_FEED=hf_lab.new_mark_feed()
    HF_BUILD['attempts']+=1
    HF_BUILD['last_attempt_at']=now_ms()
    container=hf_lab.HighFrequencyLab(HF_ROOT,cost=hf_lab.cost_functions(sys.modules[__name__]),
                                      defense=entry_defense_layer(),marks=HF_MARK_FEED,
                                      price_audit=price_integrity,clock=now_ms)
    try:
        container.load(reset=RESET_REQUESTED)
    finally:
        if container.reset_archive is not None:
            # The reset archive is made: a retried build must not archive the fresh root again.
            RESET_REQUESTED=False
    HF=container
    HF_BUILD.update(built_at=now_ms(),last_error=None,next_attempt_at=None)
    return container

def hf_build_failed(error):
    """A failed HF build: published (hf_error, the build status) and retried after HF_BUILD_RETRY_MS."""
    text=hf_error_text('build',error)
    STATE['hf_error']=text
    HF_BUILD.update(failures=HF_BUILD['failures']+1,last_error=text,next_attempt_at=now_ms()+HF_BUILD_RETRY_MS)

def hf_retry_build():
    """While HF is enabled but not built, retry the build once HF_BUILD_RETRY_MS has passed.

    A build failure (an unreadable journal file, a locked checkpoint) used to leave HF off for
    the life of the process, with nothing on the dashboard but 'Няма HF данни'."""
    if HF is not None or not hf_lab.enabled():
        return False
    due=HF_BUILD.get('next_attempt_at')
    # Only a failed build is retried (hf_build_failed schedules next_attempt_at).
    if due is None or now_ms()<due:
        return False
    try:
        build_high_frequency()
    except Exception as e:
        hf_build_failed(e)
        return False
    return HF is not None

def hf_build_status():
    """HF build attempts of this process (published in persistence.hf and activity_config)."""
    return {**HF_BUILD,'retry_ms':HF_BUILD_RETRY_MS}

def hf_feed_prices(feed):
    """{(mint, pool): coin} of this loop's shared feed (the marks HF.update reads)."""
    prices={}
    for coin in feed or []:
        address=coin.get('address'); pair=coin.get('pairAddress')
        if address and pair and num(coin.get('priceUsd'))>0:
            prices[(address,pair)]=coin
    return prices

def hf_error_text(stage,error):
    return f'{stage}: {type(error).__name__}: {error}'[:300]

def hf_update(feed,errors):
    """HF fills, marks and exits of this loop (after update_positions); returns its milliseconds."""
    if HF is None:
        return 0.0
    started=time.perf_counter()
    try:
        now=now_ms()
        HF.update(hf_feed_prices(feed),now,lab_forward.feed_alive(feed,now))
    except Exception as e:
        errors.append(hf_error_text('update',e))
    return (time.perf_counter()-started)*1000

def hf_refresh(feed,errors):
    """HF decisions of this refresh (after maybe_open); returns its milliseconds."""
    if HF is None:
        return 0.0
    started=time.perf_counter()
    try:
        HF.on_refresh(feed,now_ms())
    except Exception as e:
        errors.append(hf_error_text('on_refresh',e))
    return (time.perf_counter()-started)*1000

def hf_end_loop(milliseconds,errors):
    """The loop's one journal flush (HF_JOURNAL_V1), the time budget (HF_TIME_BUDGET_V1: > 250 ms of
    HF work in 3 consecutive loops: hf_degraded, exits continue) and STATE['hf_error'] of this loop,
    also counted in persistence.hf.errors; an HF error never stops the Lab."""
    if HF is None:
        return
    started=time.perf_counter()
    try:
        HF.flush_journal()
    except Exception as e:
        errors.append(hf_error_text('journal',e))
    milliseconds+=(time.perf_counter()-started)*1000
    try:
        HF.record_loop_time(milliseconds)
    except Exception as e:
        errors.append(hf_error_text('time_budget',e))
    try:
        HF.note_errors(errors)
    except Exception:
        pass
    if errors:
        STATE['hf_error']=' | '.join(errors)[:600]
    else:
        STATE.pop('hf_error',None)

def hf_persist_error(stage,error,now):
    """An HF failure inside persist(): appended to this loop's hf_error and counted in the HF metrics."""
    text=hf_error_text(stage,error)
    previous=STATE.get('hf_error')
    STATE['hf_error']=(f'{previous} | {text}' if previous else text)[:600]
    try:
        HF.note_error(text,now)
    except Exception:
        pass

def persist(status='online',error=None):
    STATE['status']=status; STATE['updated_at']=now_ms()
    STATE['registry_compatibility']=registry_compatibility(STATE['books'])
    STATE['strategy_lifecycle']=review_strategy_lifecycle(STATE['books'])
    registered={s['id'] for s in STRATEGIES}
    previous_stats=STATE.get('stats') or {}
    STATE['stats']={
        key:stats(book) if key in registered else {
            **(previous_stats.get(key) or {}),'runtime_status':'preserved_inactive',
            'entry_enabled':False,'position_management_enabled':False,
            'valuation_stale':bool(book.get('position') or book.get('positions')),
        }
        for key,book in STATE['books'].items()
    }
    research=research_state(now_ms())
    if research is not None:
        try:
            summary=STATE.get('exit_research_summary') or {}
            if now_ms()-num(summary.get('updated_at'))>=10_000:
                STATE['exit_research_summary']=exit_research.view(research,now_ms())
        except Exception as research_failure:
            research_error(research,research_failure)
            STATE['exit_research_error']=research['error']
    STATE['data_integrity_note']='Историята съдържа непотвърдени цени, включително XFUN. Не е доказателство за реална доходност. Новите входове минават независима проверка.'
    STATE['execution_basis']=EXECUTION_MODEL_VERSION
    STATE['execution_note']='DEX exact-pool spot marks with modeled fees, impact, slippage and latency; paper estimate only, no transaction is built, signed, or sent.'
    STATE['activity_config']={**activity.policy_config(STOP_LOSS),'stop_loss_net_pct':STOP_LOSS,
                              'take_profit_net_pct':TAKE_PROFIT,'trade_limit_usd':TRADE_NOTIONAL,
                              'rush_brain_version':rush_brain.VERSION,
                              'rush_research_target_win_rate_pct':rush_brain.TARGET_WIN_RATE_PCT,
                              'rush_target_is_guarantee':False,
                              'rush_max_memory_closed_trades':rush_brain.MAX_MEMORY_TRADES,
                              'rush_max_memory_scan_rows':rush_brain.MAX_MEMORY_SCAN_ROWS,
                              'rush_low_cap_max_balance_fraction':rush_brain.LOW_CAP_MAX_BALANCE_FRACTION,
                              'rush_low_cap_max_notional_usd':rush_brain.LOW_CAP_MAX_NOTIONAL_USD}
    STATE['activity_config']['promoted_entry_policy']=promoted_guard.funded_policy_config(
        STOP_LOSS,promoted_candidate_config())
    if active_paper.enabled():
        STATE['activity_config']['promoted_entry_policy'].update({
            'version':active_paper.reporting_version(),'candidate_policy_source':active_paper.reporting_version(),
            'per_strategy_parameters':active_paper.config(),'maximum_roundtrip_cost_pct':None})
    STATE['activity_config']['cost_first_established']=cost_first.config()
    STATE['activity_config']['funded_active_paper']=active_paper.config()
    if DEFENSE is not None:
        STATE['activity_config']['funded_active_paper']['history_seed']=getattr(DEFENSE,'funded_heat_seed',None)
    STATE['activity_config']['defensive_entry']=entry_defense.config()
    STATE['activity_config']['lab_forward_tests']=lab_forward.config()
    STATE['activity_config']['lab_forward_tests_state']={**FORWARD_MEMORY.status(),
                                                         'signal_carry':FORWARD_SIGNAL_CARRY.status()}
    # LAB_HIGH_FREQUENCY_V1: checkpoint (forced on stop), published definition, metrics and the
    # dashboard section. The HF view goes only into the compact projection: the full ledger
    # carries the HF definition and metrics, never HF trades.
    hf_view=None
    hf_metrics=None
    if HF is not None:
        hf_now=now_ms()
        try:
            HF.checkpoint_if_due(hf_now,force=status=='stopped')
        except Exception as e:
            # Published below (hf_error, persistence.hf.errors) even though the checkpoint failed.
            hf_persist_error('checkpoint',e,hf_now)
        try:
            STATE['activity_config']['lab_high_frequency']={**HF.config(),'running':bool(HF.loaded),
                                                            'build':hf_build_status()}
            hf_metrics={**HF.metrics(),'build':hf_build_status()}
            hf_view=HF.dashboard_view(hf_now)
        except Exception as e:
            hf_persist_error('persist',e,hf_now)
    else:
        hf_enabled=hf_lab.enabled()
        STATE['activity_config']['lab_high_frequency']={**hf_lab.config_view(enabled_flag=hf_enabled),
                                                        'running':False,'build':hf_build_status()}
        if hf_enabled and HF_BUILD['attempts']:
            # Enabled, attempted and not built (a failed build, retried every HF_BUILD_RETRY_MS): the
            # attempts and the last error stay visible in persistence.hf, with hf_error on GET /state.
            hf_metrics={'version':hf_lab.VERSION,'loaded':False,'running':False,'build':hf_build_status()}
    if DEFENSE is not None:
        STATE['activity_config']['defensive_entry_state']=DEFENSE.status()
        if status=='stopped':
            # Keep the Lab's ticker memory across a restart (atomic; never raises).
            DEFENSE.registry.flush()
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
        'total_allocated_capital_usd':sum(num(STATE['books'][key].get('allocation_usd'),PROMOTED_ALLOCATION)
                                        for key in PROMOTED_STRATEGIES),
        'allocation_per_strategy_usd':(num(STATE['books'][PROMOTED_STRATEGIES[0]].get('allocation_usd'),PROMOTED_ALLOCATION)
            if len({num(STATE['books'][key].get('allocation_usd'),PROMOTED_ALLOCATION)
                    for key in PROMOTED_STRATEGIES})==1 else None),
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
    STATE['persistence']=dict(PERSIST_METRICS)
    if hf_metrics is not None:
        STATE['persistence']['hf']=hf_metrics
    published=merge_paired_snapshot(merge_astra_snapshot(STATE))
    started=time.perf_counter()
    ledger_bytes=atomic_write(published)
    compact_source={**published,'high_frequency':hf_view} if hf_view is not None else published
    compact_bytes=atomic_write_path(COMPACT_PATH,compact_strategy_lab(compact_source))
    record_persist_metrics(ledger_bytes,compact_bytes,time.perf_counter()-started,now_ms())

def main():
    global STATE, LOADED
    STATE=load_state()
    LOADED=True
    if (STATE.get('portfolio_setup') or {}).get('status')=='DRAINING':
        try:
            promote_strategy_lab(STATE_PATH.parent)
            STATE=load_state()
        except Exception as e:
            setup=STATE.get('portfolio_setup') or {}
            setup['promotion_error']=str(e)[:200]
            STATE['portfolio_setup']=setup
    funding_changed=STATE.pop('_funding_requires_persist',False)
    exits_changed=STATE.pop('_exit_policy_requires_persist',False)
    if funding_changed or exits_changed:
        # Persist the audited credit before allowing any entry; a failed write
        # restarts fail-closed, never spends an unrecorded virtual contribution.
        persist('starting',('Owner-authorized PAPER capital contribution recorded' if funding_changed
                            else 'Owner-authorized PAPER exit policy change recorded'))
    if STATE.get('activity_version')!=activity.POLICY_VERSION:
        STATE['activity_version']=activity.POLICY_VERSION
        STATE['activity_started_at']=now_ms()
    try:
        # LAB_HIGH_FREQUENCY_V1 (NEO_LAB_HF_ENABLED, default '1'); a failure leaves the Lab running
        # without it and is retried every HF_BUILD_RETRY_MS (hf_retry_build in the loop).
        build_high_frequency()
    except Exception as e:
        hf_build_failed(e)
    last_entry=0
    feed=[]
    persistence_ready=True
    failures=0
    while True:
        started=time.time()
        try:
            # Restore a durable ledger before permitting another PAPER entry.
            # Failed projection writes leave the old published snapshot stale;
            # readers already reject stale evidence.
            if not persistence_ready:
                persist('degraded','Recovering a failed state write')
                persistence_ready=True
            flows=flow_map()
            refresh_due=time.time()-last_entry>=ENTRY_REFRESH_SECONDS
            if refresh_due:
                r=SESSION.get(API_URL,timeout=5); r.raise_for_status()
                feed=r.json().get('feed') or []
                last_entry=time.time()
            update_positions(flows,feed)
            hf_retry_build()
            hf_errors=[]
            hf_ms=hf_update(feed,hf_errors)
            if refresh_due:
                maybe_open(feed,flows)
                hf_ms+=hf_refresh(feed,hf_errors)
            hf_end_loop(hf_ms,hf_errors)
            persist('online')
            failures=0
        except Exception as e:
            failures+=1
            persistence_ready=False
            print(f'Strategy Lab retry: {type(e).__name__}: {e}',file=sys.stderr,flush=True)
            try:
                persist('degraded',e)
            except Exception as write_error:
                print(f'Strategy Lab state write failed: {type(write_error).__name__}: {write_error}',file=sys.stderr,flush=True)
        delay=min(60,2**min(failures,6)) if failures else POLL_SECONDS
        time.sleep(max(.25,delay-(time.time()-started)))

if __name__=='__main__': main()
