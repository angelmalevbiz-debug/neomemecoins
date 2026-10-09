"""Explicit owner-requested PAPER load test, NOT strategy-signal admission.

Fill real-market model positions to exercise entries, marks, exits and costs.
Signal, flow direction, heat and loss/rate gates are shadow measurements.
Fresh exact-pool market, full safety, independent price, real modeled costs,
finite quantities, funded exposure and unique mints remain hard requirements.
No wallet execution, synthetic ticks, capital refill or profitability claim.
"""
import math

import funded_active_paper as active

VERSION=active.CAPACITY_TEST_VERSION
MAX_CANDIDATES=12
MIN_LIQUIDITY_USD=50_000.0
CURSORS={}


def applies(book):
    return active.capacity_test_enabled() and active.applies(book) and not book.get('promotion_pending')


def candidate(coin,now):
    return (isinstance(coin,dict) and coin.get('dexId')=='pumpswap'
            and coin.get('quoteTokenAddress')==active.SOL
            and isinstance(coin.get('address'),str) and bool(coin['address'])
            and isinstance(coin.get('pairAddress'),str) and bool(coin['pairAddress'])
            and active.finite(coin.get('priceUsd'))>0
            and active.finite(coin.get('priceNative'))>0
            and active.finite(coin.get('liquidityUsd'))>=MIN_LIQUIDITY_USD
            and 0<=now-active.finite(coin.get('updatedAt'),-math.inf)<=12_000)


def hard_capacity(book,now):
    cap=active.capacity(book,now)
    if cap['blocked_reason'] in {'funded_active_daily_loss_limit','funded_active_hourly_order_limit'}:
        # The load test may consume its finite contributed PAPER cash, but
        # never exceed four slots or 50% funded exposure even beyond a loss cap.
        cap['blocked_reason']=None
        if any(p.get('quote_status') in {'stale','unavailable'} for p in active.positions(book)):
            cap['blocked_reason']='funded_active_marks_unavailable'
        elif cap['open_positions']>=active.MAX_SLOTS:cap['blocked_reason']='funded_active_slots_full'
        elif cap['available_exposure_usd']<100:cap['blocked_reason']='funded_active_exposure_limit'
    return cap


def valid_execution(execution):
    fields=('fill_price','dex_fee_bps','dex_fee_usd','network_fee_usd','impact_pct','slippage_pct','latency_pct')
    return (isinstance(execution,dict) and all(math.isfinite(active.finite(execution.get(k),math.nan))
            and active.finite(execution.get(k),-1)>=0 for k in fields)
            and active.finite(execution.get('fill_price'))>0)


def fill(book,candidates,now,engine):
    """Bounded normal event-loop step; safety/price checks use existing caches.

    Rotating bounded checks prevent one pending price/safety candidate from
    starving every free slot. No thread, direct journal write or network budget
    increase; the established provider queues retain their rate limits.
    """
    sid=book['id'];book['name']=next(s['name'] for s in engine.STRATEGIES if s['id']==sid)+' · ТЕСТ ЗАПЪЛВАНЕ'
    diagnostics={'at':now,'entry_policy_version':VERSION,'capacity_test':True,
                 'signal_candidates':0,'opened_this_refresh':0,'hard_rejections':{},'shadow_examples':[],
                 'strategy_validation':False,'profitability_proven':False}
    book['entry_diagnostics']=diagnostics
    def reject(reason):
        counts=diagnostics['hard_rejections'];counts[reason]=counts.get(reason,0)+1
    cap=hard_capacity(book,now)
    if cap['blocked_reason']:
        diagnostics.update(blocked_reason=cap['blocked_reason'],funded_active=active.performance(book,now))
        return
    rows=[(c,f) for c,f in candidates if candidate(c,now)]
    # Observe all bounded identities over successive refreshes, not the first
    # high-score pool forever. Across books the same pool is allowed; within a
    # book each mint is unique. Sixteen positions are NOT sixteen independent bets.
    rows.sort(key=lambda row:(row[0]['address'],row[0]['pairAddress']))
    offset=CURSORS.get(sid,0)%max(len(rows),1)
    selection=(rows[offset:]+rows[:offset])[:MAX_CANDIDATES]
    CURSORS[sid]=offset+MAX_CANDIDATES
    for coin,features in selection:
        stamp=engine.now_ms();cap=hard_capacity(book,stamp)
        if cap['blocked_reason']:break
        if any(p.get('address')==coin['address'] for p in active.positions(book)):continue
        diagnostics['signal_candidates']+=1
        if not candidate(coin,stamp):reject('capacity_test_market_stale');continue
        # Full safety and independently checked exact-pool price remain hard.
        risk=engine.rug_guard.check(coin)
        safety=engine.promoted_guard.risk_admission(coin,risk,stamp,engine.rug_guard.TTL_MS)
        if not safety['allow']:reject(safety['reason']);continue
        validation=engine.price_integrity.check(coin)
        if validation.get('status')=='review':
            validation=engine.cached_jupiter_tiebreak(validation,coin,now=stamp)
            if validation.get('status')=='review':engine.schedule_jupiter_price_probe(coin)
        if (validation.get('status')!='pass' or validation.get('mint')!=coin['address']
                or validation.get('pair')!=coin['pairAddress']):
            reject('promoted_price_identity_unverified');continue
        if engine.sol_usd_from_coin(coin)<=0:reject('network_price_unknown');continue
        opening=engine.entry_execution(coin,100.0)
        if not valid_execution(opening):reject('capacity_test_invalid_execution');continue
        qty=active.finite(opening.get('quantity'),math.nan)
        basis=active.finite(opening.get('capital_committed_usd'),math.nan)
        if not math.isfinite(qty) or qty<=0 or not math.isfinite(basis) or basis<100:
            reject('capacity_test_invalid_execution');continue
        if basis>cap['available_exposure_usd']:reject('funded_active_exposure_limit');continue
        mark=engine.exit_execution(coin,qty)
        if not valid_execution(mark):reject('capacity_test_invalid_execution');continue
        proceeds=active.finite(mark.get('net_proceeds_usd'),math.nan)
        if not math.isfinite(proceeds) or proceeds<0:
            reject('capacity_test_invalid_execution');continue
        pnl=proceeds-basis;pct=pnl # $100 notional: dollars equal percent.
        if not -100.1<=pct<=0:reject('capacity_test_invalid_execution');continue
        original_defense=engine.funded_defensive_decision(book,coin,stamp,
            blocked_pools=engine.pool_loss_memory.index(book.get('history') or [],stamp))
        shadow={'strategy_market_signal':active.matches(sid,coin),
                'flow_admission':engine.promoted_guard.flow_admission(coin,features,stamp),
                'defensive_entry':engine.entry_defense.compact(original_defense),
                'strategy_roundtrip_cost_admitted':-pct<=active.RULES[sid]['cost'],
                'strategy_cost_limit_pct':active.RULES[sid]['cost'],
                'recent_loss_pause_ms':engine.promoted_pause_remaining_ms(book,stamp),
                'address_cooldown_ms':engine.activity.cooldown_remaining_ms(book,coin['address'],stamp),
                'capacity_risk_block':active.capacity(book,stamp)['blocked_reason']}
        # Recheck the live clock after provider/quote preparation. No entry is
        # admitted on a stale snapshot or expired cached safety result.
        commit=engine.now_ms()
        if not candidate(coin,commit):reject('capacity_test_market_stale');continue
        if not engine.promoted_guard.risk_admission(coin,risk,commit,engine.rug_guard.TTL_MS)['allow']:
            reject('promoted_safety_unavailable');continue
        fresh_validation=engine.price_integrity.check(coin)
        if fresh_validation.get('status')=='review':
            fresh_validation=engine.cached_jupiter_tiebreak(fresh_validation,coin,now=commit)
        if (fresh_validation.get('status')!='pass' or fresh_validation.get('mint')!=coin['address']
                or fresh_validation.get('pair')!=coin['pairAddress']):
            reject('promoted_price_identity_unverified');continue
        cap=hard_capacity(book,commit)
        if cap['blocked_reason'] or basis>cap['available_exposure_usd']:
            reject(cap['blocked_reason'] or 'funded_active_exposure_limit');continue
        book['trade_seq']=int(book.get('trade_seq',0))+1
        exits=active.capacity_exit_parameters(100.0)
        position={
            'trade_no':book['trade_seq'],'strategy_id':sid,'symbol':coin.get('symbol'),'name':coin.get('name'),
            'address':coin['address'],'pairAddress':coin['pairAddress'],'dexId':coin['dexId'],
            'quoteTokenAddress':active.SOL,'entry_quote_token_address':active.SOL,
            'entry_price':coin['priceUsd'],'execution_entry_price':opening['fill_price'],
            'current_price':coin['priceUsd'],'peak_price':coin['priceUsd'],'quantity':qty,'original_quantity':qty,
            'notional_usd':100.0,'opened_at':commit,'updated_at':commit,'score':coin.get('score'),
            'entry_features':features,'partial_realized_pnl':0.0,'partial_exits':[],
            'remaining_cost_basis_usd':basis,'entry_dex_fee_bps':opening['dex_fee_bps'],
            'entry_dex_fee_usd':opening['dex_fee_usd'],'entry_network_fee_usd':opening['network_fee_usd'],
            'entry_price_impact_pct':opening['impact_pct'],
            'entry_slippage_pct':opening['slippage_pct']+opening['latency_pct'],
            'execution_mode':engine.EXECUTION_MODEL_VERSION,'execution_source':'DEX_SPOT_WITH_MODELED_FRICTION',
            'entry_policy_version':VERSION,'capacity_test':True,'strategy_validation':False,
            'capacity_test_shadow':shadow,'risk_guard':risk,'price_crosscheck':fresh_validation,
            'verified_entry_flow':features.get('verified_flow'),
            'quote_status':'fresh','quote_age_ms':commit-coin['updatedAt'],'mark_received_at':coin['updatedAt'],
            'mark_source':'SHARED_LIVE_FEED_EXACT_POOL','entry_roundtrip_pnl_pct':pct,
            'entry_cost_cap_pct':None,'stop_loss_net_pct':exits['stop_loss_net_pct'],
            'stop_headroom_pct':engine.activity.stop_headroom_pct(exits['stop_loss_net_pct'],pct),
            'exit_parameters':exits,'exit_policy_label':active.CAPACITY_EXIT_VERSION,'peak_net_pct':pct,
            'open_pnl_usd':round(pnl,4),'pnl_pct':round(pct,3),
            'estimated_exit_fee_usd':mark['dex_fee_usd']+mark['network_fee_usd'],
            'estimated_exit_impact_pct':mark['impact_pct'],'defensive_entry':engine.entry_defense.compact(original_defense),
        }
        active.attach(book,position)
        book.setdefault('last_entry_by_address',{})[coin['address']]=commit
        diagnostics['opened_this_refresh']+=1
        if len(diagnostics['shadow_examples'])<4:
            diagnostics['shadow_examples'].append({'symbol':coin.get('symbol'),'address':coin['address'],
                                                  'pairAddress':coin['pairAddress'],**shadow})
    final=hard_capacity(book,engine.now_ms())
    diagnostics.update(funded_active=active.performance(book,engine.now_ms()),
        blocked_reason=(final['blocked_reason'] or (next(iter(diagnostics['hard_rejections']))
                        if diagnostics['hard_rejections'] else 'capacity_test_no_priced_market')))
