"""Bounded prospective same-entry exit comparison; no orders or cash mutations.

Recorded pool prices and unchanged modeled costs, NOT executable wallet fills.
Profiles are frozen before observations, never optimized on old winners.
"""
import copy
import hashlib
import json
import math
import random
import statistics
from collections import Counter, defaultdict

VERSION = 'PAPER_EXIT_RESEARCH_V1'
BASELINE = 'BASELINE_30_LOCK4'
PROFILES = {
    BASELINE: dict(target=30.0, stop=5.0, arm=4.0, giveback=.20, minimum=1.0),
    'QUICK_6_LOCK2': dict(target=6.0, stop=5.0, arm=2.0, giveback=.20, minimum=.75),
    'RUNNER_LOCK4': dict(target=None, stop=5.0, arm=4.0, giveback=.25, minimum=1.0),
}
MAX_ACTIVE = 64
MAX_COMPLETED = 256
TRACE_POINTS = 32
HORIZON_MS = 60*60_000
STALE_MS = 12_000
MIN_DELAY_MS = 2_000
MAX_MARK_GAP_MS = 60_000
MIN_PAIRS = 150
MIN_POOLS = 25
MIN_DAYS = 5
STRESS_FRACTION = .01  # additional 50 bps per leg, $1 on $100
QUALITY_ENTRIES = frozenset({'FUNDED_ACTIVE_PAPER_V4_QUALITY_100', 'FUNDED_ACTIVE_PAPER_V5_ADAPTIVE_100'})


def number(value, fallback=None):
    if isinstance(value, bool):
        return fallback
    try:
        out=float(value)
        return out if math.isfinite(out) else fallback
    except (TypeError, ValueError, OverflowError):
        return fallback


def config(model):
    return dict(version=VERSION, profiles=copy.deepcopy(PROFILES), cost_model=copy.deepcopy(model),
                fill_basis='NEXT_FRESH_CHANGED_POOL_MARK_AFTER_TRIGGER_AT_LEAST_2_SECONDS',
                horizon_ms=HORIZON_MS, stale_ms=STALE_MS, stress_fraction=STRESS_FRACTION,
                maximum_review_mark_gap_ms=MAX_MARK_GAP_MS,
                limits=dict(active=MAX_ACTIVE, completed=MAX_COMPLETED, trace_points=TRACE_POINTS),
                review_gate=dict(paired_entries=MIN_PAIRS, unique_pools=MIN_POOLS, utc_days=MIN_DAYS),
                automatic_promotion=False, financial_effect=False, wallet_execution=False)


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',', ':'),allow_nan=False).encode()).hexdigest()


def ensure(container, model, now):
    frozen=config(model)
    state=container.get('exit_research')
    if state is None:
        state=dict(version=VERSION, config=frozen, config_hash=digest(frozen), started_at=int(now),
                   episodes=[], capacity_refusals=0, trimmed_completed=0, observation_errors=0)
        container['exit_research']=state
    if (not isinstance(state,dict) or state.get('version')!=VERSION
            or state.get('config')!=frozen or state.get('config_hash')!=digest(frozen)
            or not isinstance(state.get('episodes'),list)):
        raise ValueError('Exit research state/config mismatch; refusing reset or mixed samples')
    seen=set()
    for e in state['episodes']:
        if (not isinstance(e,dict) or e.get('id') in seen or not isinstance(e.get('id'),str)
                or set(e.get('legs') or {})!=set(PROFILES)
                or any(not isinstance(l,dict) or l.get('status') not in
                       {'OPEN','EXIT_PENDING','CLOSED','CENSORED'} for l in e['legs'].values())):
            raise ValueError('Invalid exit research episodes; refusing reset')
        seen.add(e['id'])
    return state


def episode_id(position):
    return digest([position.get(k) for k in ('strategy_id','trade_no','opened_at','address','pairAddress',
                                            'entry_policy_version')])[:24]


def finished(episode):
    return all(leg['status'] in {'CLOSED','CENSORED'} for leg in episode['legs'].values())


def start(state, position, now, *, new_entry=False):
    eid=episode_id(position)
    old=next((e for e in state['episodes'] if e['id']==eid),None)
    if old is not None:
        return old
    if sum(not finished(e) for e in state['episodes'])>=MAX_ACTIVE:
        state['capacity_refusals']+=1
        return None
    values={k:number(position.get(k)) for k in ('notional_usd','quantity','remaining_cost_basis_usd','opened_at')}
    if (any(v is None or v<=0 for v in values.values()) or values['opened_at']>now
            or not all(isinstance(position.get(k),str) and position[k] for k in ('address','pairAddress','strategy_id'))):
        raise ValueError('Invalid research entry snapshot')
    eligible=(new_entry and position.get('quality_mode') is True
              and position.get('capacity_test') is not True
              and position.get('entry_policy_version') in QUALITY_ENTRIES
              and 0<=now-values['opened_at']<=STALE_MS)
    episode=dict(id=eid, strategy_id=position['strategy_id'], trade_no=position.get('trade_no'),
        symbol=str(position.get('symbol') or ''), address=position['address'], pairAddress=position['pairAddress'],
        entry_policy_version=position.get('entry_policy_version'), opened_at=values['opened_at'],
        started_at=int(now), origin='FULL_QUALITY_ENTRY' if eligible else 'RESUMED_OPEN_LOT',
        eligible_for_review=eligible, notional_usd=values['notional_usd'], quantity=values['quantity'],
        remaining_cost_basis_usd=values['remaining_cost_basis_usd'],
        partial_realized_pnl=number(position.get('partial_realized_pnl'),0),
        original_entry_cost_pct=position.get('entry_roundtrip_pnl_pct'),
        last_mark_at=None, last_price=None, observed_marks=0, last_net_usd=None,
        largest_mark_gap_ms=0, continuous_observation=True,
        trace=[], legs={name:dict(status='OPEN',peak_net_usd=None, trigger_at=None, trigger_price=None,
                                trigger_net_usd=None, reason=None) for name in PROFILES})
    state['episodes'].append(episode)
    # Only completed research rows can leave the bounded window, never live lots.
    done=[e for e in state['episodes'] if finished(e)]
    remove={e['id'] for e in done[:-MAX_COMPLETED]}
    if remove:
        state['episodes']=[e for e in state['episodes'] if e['id'] not in remove]
        state['trimmed_completed']+=len(remove)
    return episode


def observe(episode, coin, net_usd, now):
    """Observe one fresh exact market. Same-price later polls are not new fills."""
    if finished(episode):
        return False
    stamp=number(coin.get('mark_received_at'),number(coin.get('updatedAt')))
    price=number(coin.get('priceUsd'))
    net=number(net_usd)
    if (coin.get('address')!=episode['address'] or coin.get('pairAddress')!=episode['pairAddress']
            or stamp is None or price is None or price<=0 or net is None
            or not 0<=now-stamp<=STALE_MS or stamp<episode['started_at']
            or stamp<=number(episode.get('last_mark_at'),0)):
        return False
    if now-episode['started_at']>=HORIZON_MS:
        expire(episode,now)
        return False
    gap=stamp-number(episode.get('last_mark_at'),episode['started_at'])
    episode['largest_mark_gap_ms']=max(episode['largest_mark_gap_ms'],gap)
    episode['continuous_observation']=episode['continuous_observation'] and gap<=MAX_MARK_GAP_MS
    episode.update(last_mark_at=int(stamp),last_price=price,last_net_usd=net,
                   observed_marks=episode['observed_marks']+1)
    episode['trace']=[*episode['trace'],dict(at=int(stamp),price=price,net_usd=net)][-TRACE_POINTS:]
    for name,leg in episode['legs'].items():
        if leg['status'] in {'CLOSED','CENSORED'}:
            continue
        if leg['status']=='EXIT_PENDING':
            if stamp-leg['trigger_at']>=MIN_DELAY_MS and price!=leg['trigger_price']:
                leg.update(status='CLOSED',closed_at=int(stamp),net_usd=net,
                           stressed_net_usd=net-episode['notional_usd']*STRESS_FRACTION,
                           hold_minutes=(stamp-episode['started_at'])/60_000,
                           mark_gap_ms=stamp-leg['trigger_at'],fill_price_observation=price)
            continue
        profile=PROFILES[name]
        peak=max(number(leg['peak_net_usd'],net),net)
        leg['peak_net_usd']=peak
        floor=peak-max(profile['minimum'],peak*profile['giveback']) if peak>=profile['arm'] else None
        leg['floor_net_usd']=floor
        reason=('NET_STOP' if net<=-profile['stop'] else
                'NET_TARGET' if profile['target'] is not None and net>=profile['target'] else
                'NET_PROTECTION' if floor is not None and net<=floor else None)
        if reason:
            leg.update(status='EXIT_PENDING',trigger_at=int(stamp),trigger_price=price,
                       trigger_net_usd=net,reason=reason)
    return True


def expire(episode,now):
    if not finished(episode) and now-episode['started_at']>=HORIZON_MS:
        for leg in episode['legs'].values():
            if leg['status'] not in {'CLOSED','CENSORED'}:
                leg.update(status='CENSORED',censored_at=int(now),
                           censor_reason='HORIZON_WITHOUT_ALL_CHANGED_MARK_EXITS')


def interval_by_pool(rows, values):
    """Descriptive pool-block bootstrap; not a calibrated win probability."""
    grouped=defaultdict(list)
    for e,value in zip(rows,values):
        grouped[e['pairAddress']].append(value)
    blocks=list(grouped.values())
    if len(blocks)<MIN_POOLS:
        return None
    rng=random.Random(20261009)
    draws=[]
    for _ in range(500):
        sample=[v for _ in blocks for v in blocks[rng.randrange(len(blocks))]]
        draws.append(statistics.mean(sample))
    draws.sort()
    return [round(draws[12],6),round(draws[487],6)]


def view(state, now):
    """Compact descriptive report. Never promote or mutate financial books."""
    episodes=state['episodes']
    mature=[e for e in episodes if e['eligible_for_review'] and now-e['started_at']>=HORIZON_MS]
    paired=[e for e in mature if e['continuous_observation'] and
            all(leg['status']=='CLOSED' for leg in e['legs'].values())]
    days={int(e['opened_at'])//86_400_000 for e in paired}
    pools=Counter(e['pairAddress'] for e in paired)
    coverage=(len(paired)==len(mature) and state['capacity_refusals']==0 and state['observation_errors']==0)
    enough=len(paired)>=MIN_PAIRS and len(pools)>=MIN_POOLS and len(days)>=MIN_DAYS and coverage
    baseline_net=[e['legs'][BASELINE]['net_usd'] for e in paired]
    baseline_hold=[e['legs'][BASELINE]['hold_minutes'] for e in paired]
    rows=[]
    for name,profile in PROFILES.items():
        closed=[e['legs'][name] for e in episodes if e['legs'][name]['status']=='CLOSED']
        wins=[l['net_usd'] for l in closed if l['net_usd']>0]
        losses=[-l['net_usd'] for l in closed if l['net_usd']<0]
        nets=[e['legs'][name]['stressed_net_usd'] for e in paired]
        deltas=[e['legs'][name]['net_usd']-b for e,b in zip(paired,baseline_net)]
        ci=interval_by_pool(paired,nets) if enough else None
        delta_ci=interval_by_pool(paired,deltas) if enough else None
        holds=[e['legs'][name]['hold_minutes'] for e in paired]
        review=(name!=BASELINE and enough and ci[0]>0 and delta_ci[0]>0
                and max(pools.values())/len(paired)<=.20
                and statistics.median(holds)<statistics.median(baseline_hold)*.95)
        rows.append(dict(id=name,parameters=profile,observed_closes=len(closed),
            wins=sum(l['net_usd']>0 for l in closed),
            win_rate_pct=round(sum(l['net_usd']>0 for l in closed)/len(closed)*100,1) if closed else None,
            modeled_net_usd=round(sum(l['net_usd'] for l in closed),4),
            average_win_usd=round(statistics.mean(wins),4) if wins else None,
            average_loss_usd=round(statistics.mean(losses),4) if losses else None,
            profit_factor=round(sum(wins)/sum(losses),4) if losses else None,
            unresolved_last_mark_net_usd=round(sum(number(e['last_net_usd'],0) for e in episodes
                if e['legs'][name]['status']!='CLOSED'),4),
            unresolved_unpriced=sum(e['last_net_usd'] is None for e in episodes if e['legs'][name]['status']!='CLOSED'),
            median_hold_minutes=round(statistics.median(l['hold_minutes'] for l in closed),3) if closed else None,
            censored=sum(e['legs'][name]['status']=='CENSORED' for e in episodes),
            pending=sum(e['legs'][name]['status']=='EXIT_PENDING' for e in episodes),
            eligible_paired_closes=len(paired), stressed_mean_net_usd=round(statistics.mean(nets),6) if nets else None,
            stressed_pool_ci95=ci, improvement_pool_ci95=delta_ci,
            status='CANDIDATE_FOR_MANUAL_REVIEW_NOT_VALIDATED' if review else
                   'DESCRIPTIVE_NO_ACCEPTANCE' if enough else 'INSUFFICIENT_PROSPECTIVE_DATA'))
    return dict(version=VERSION,config_hash=state['config_hash'],started_at=state['started_at'],updated_at=int(now),
        status='ERROR' if state.get('error') else 'OBSERVING',error=state.get('error'),
        model_basis='OBSERVED_POOL_MODEL_NOT_EXECUTABLE_WALLET_QUOTES',financial_effect=False,
        automatic_promotion=False,profitability_proven=False,
        active_episodes=sum(not finished(e) for e in episodes),
        resumed_episodes=sum(e['origin']=='RESUMED_OPEN_LOT' for e in episodes),
        full_entry_episodes=sum(e['eligible_for_review'] for e in episodes),
        observations=sum(e['observed_marks'] for e in episodes),
        paired_mature_entries=len(paired),unique_pools=len(pools),utc_days=len(days),
        censored_or_unresolved_mature_entries=len(mature)-len(paired),coverage_complete=coverage,
        observed_gap_episodes=sum(not e['continuous_observation'] for e in episodes),
        capacity_refusals=state['capacity_refusals'],trimmed_completed=state['trimmed_completed'],
        review_gate=state['config']['review_gate'], profiles=rows,
        recent_episodes=[dict(id=e['id'],symbol=e['symbol'],strategy=e['strategy_id'],origin=e['origin'],
            marks=e['observed_marks'],last_net_usd=e['last_net_usd'],
            legs={name:dict(status=l['status'],net_usd=l.get('net_usd'),reason=l.get('reason'))
                  for name,l in e['legs'].items()}) for e in episodes[-5:]])
