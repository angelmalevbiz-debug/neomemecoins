import copy
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

TMP=tempfile.TemporaryDirectory()
os.environ['NEO_STRATEGY_LAB_PATH']=str(Path(TMP.name)/'state.json')
os.environ['NEO_STRATEGY_LAB_RESET_FLAG']=str(Path(TMP.name)/'reset')
import lab_activity as a
import strategy_lab as lab
import promoted_entry_guard as promoted_guard
import entry_defense as _isolation_entry_defense
import strategy_lab as _isolation_lab

_DEFENSIVE_ISOLATION = []


def _defensive_pass(*_args, **_kwargs):
    return _isolation_entry_defense.pass_decision('TEST_GATE_ISOLATION')


def _path_less_layers():
    """Path-less layers for the module globals that otherwise build one from the env paths."""
    return (
        (_isolation_lab, 'DEFENSE', _isolation_entry_defense.DefensiveEntryLayer()),
    )


def setUpModule():
    """These tests isolate other entry gates. DEFENSIVE_ENTRY_LAYER_V1 (structural rug
    guard, pool loss memory, heat veto and its warm-up) has its own suite in
    tests/test_defensive_entry_layer.py, which proves every path consults it."""
    for target, name in ((_isolation_lab, 'defensive_entry_decision'),):
        isolation = patch.object(target, name, _defensive_pass)
        isolation.start()
        _DEFENSIVE_ISOLATION.append(isolation)
    # The layer's ticker registry stays in memory here. Run on its own (without
    # scripts/run_python_checks.py), this module must never write a ticker sidecar next
    # to the shell's NEO_STRATEGY_LAB_PATH / NEO_LIVE_TAPE_PATH (or the /var/lib/neo-market
    # defaults): strategy_lab.maybe_open observes through lab.DEFENSE and
    # live_tape.feed_snapshot through the module-level _POOL_SCHEDULER.
    for target, name, value in _path_less_layers():
        isolation = patch.object(target, name, value)
        isolation.start()
        _DEFENSIVE_ISOLATION.append(isolation)


def tearDownModule():
    while _DEFENSIVE_ISOLATION:
        _DEFENSIVE_ISOLATION.pop().stop()

ADDRESS='So11111111111111111111111111111111111111112'
PAIR='EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v'
NOW=1_800_000_000_000

def coin():
    return {'address':ADDRESS,'pairAddress':PAIR,'symbol':'TEST','name':'Test',
            'priceUsd':.01,'priceNative':.00008,'marketCap':1e6,'liquidityUsd':1e6,
            'quoteTokenAddress':lab.SOL_QUOTE_MINT,
            'dexId':'raydium','updatedAt':NOW,'score':100,'ageMinutes':50,
            'priceChange':{'m5':12,'h1':50},'volume':{'h1':1e6},
            'txns':{'m5':{'buys':60,'sells':20}}}

def flows():
    return {ADDRESS:{'trades':20,'buys':15,'sells':5,'buy_usd':1000,'sell_usd':100,
                     'unique_wallets':10,'ratio':10,'max_sell':30}}

class ActivityTests(unittest.TestCase):
    def setUp(self):
        guard=patch.object(lab.price_integrity,'check',side_effect=lambda c:{
            'status':'pass','version':'OFFLINE_FIXTURE','mint':c['address'],'pair':c['pairAddress']})
        guard.start();self.addCleanup(guard.stop)
        lab.rush_brain._SAMPLE_BY_PAIR.clear()
        lab.STATE={'started_at':42,'books':{s['id']:lab.empty_book(s) for s in lab.STRATEGIES}}

    def test_all_36_rules_exist(self):
        # 34 original books plus the COST_FIRST_ESTABLISHED_V1 pair.
        self.assertEqual(len(a.RULES),36)
        self.assertEqual(set(a.RULES),{s['id'] for s in lab.STRATEGIES})
        self.assertTrue(set(lab.cost_first.BOOK_IDS)<=set(a.RULES))
        for rule in a.RULES.values():self.assertGreaterEqual(rule.liquidity,10000)

    def test_every_strategy_can_match_its_family(self):
        for key,rule in a.RULES.items():
            f={'score':100,'liq':max(rule.liquidity,100000),'m5':sum(rule.move)/2,
               'bs':max(2,rule.buy_sell),'lmc':max(.3,rule.liquidity_cap),
               'age':max(10,rule.age[0]),'h1':max(20,rule.hour[0]),
               'vol_liq':max(1,rule.volume_liquidity[0]),'flow':flows()[ADDRESS]}
            self.assertTrue(rule.matches(f),key)

    def test_no_low_liquidity_or_missing_prices(self):
        c=coin();self.assertTrue(a.usable_feed_coin(c,NOW))
        for k,v in [('liquidityUsd',9999),('priceUsd',0),('priceUsd',float('nan')),('address','https://x.com/foo')]:
            self.assertFalse(a.usable_feed_coin({**c,k:v},NOW),k)
        self.assertFalse(a.usable_feed_coin(c,NOW+20001))

    def test_cooldown_from_exit(self):
        b={'last_entry_by_address':{ADDRESS:1},'history':[{'address':ADDRESS,'closed_at':NOW-59000,'pnl_usd':10}]}
        self.assertEqual(a.cooldown_remaining_ms(b,ADDRESS,NOW),1000)
        self.assertEqual(a.cooldown_remaining_ms(b,ADDRESS,NOW+1000),0)
        b['history'][0]['pnl_usd']=-1
        self.assertEqual(a.cooldown_remaining_ms(b,ADDRESS,NOW),121000)
        self.assertEqual(a.cooldown_remaining_ms(b,ADDRESS,NOW+121000),0)

    def test_scalper_has_longer_cooldown_after_a_loss(self):
        b={'id':'SCALPER','history':[{'address':ADDRESS,'closed_at':NOW-3599000,'pnl_usd':-1}]}
        self.assertEqual(a.cooldown_remaining_ms(b,ADDRESS,NOW),1000)
        b['history'][0]['pnl_usd']=1
        self.assertEqual(a.cooldown_remaining_ms(b,ADDRESS,NOW),0)

    def test_promoted_book_uses_shared_rule_but_still_requires_flow_evidence(self):
        c=coin();c['score']=94
        self.assertTrue(a.RULES['PRECISION'].matches(a.market_features(c,flows()[ADDRESS])))
        with patch.object(lab,'now_ms',return_value=NOW):lab.maybe_open([c],flows())
        book=lab.STATE['books']['PRECISION']
        self.assertIsNone(book['position'])
        self.assertEqual(book['entry_diagnostics']['signal_candidates'],1)
        self.assertEqual(book['entry_diagnostics']['blocked_reason'],'promoted_verified_flow_unavailable')

    def test_promoted_entries_require_fresh_exact_flow_and_completed_safety(self):
        c=coin()
        verified={'source':promoted_guard.FLOW_SOURCE,'coverage_status':'COMPLETE',
            'window_ms':promoted_guard.FLOW_WINDOW_MS,'address':ADDRESS,'pairAddress':PAIR,
            'window_at':NOW,'latest_event_at':NOW-100,'available_at':NOW-50,
            'trades':4,'unique_wallets':3,'buy_usd':500,'sell_usd':100}
        flow_map={(ADDRESS,PAIR):{**flows()[ADDRESS],'verified_flow':verified}}
        risk={'status':'pass','mint':ADDRESS,'pair':PAIR,'checked_at':NOW}
        with patch.object(lab,'now_ms',return_value=NOW), patch.object(lab.rug_guard,'check',return_value=risk):
            lab.maybe_open([c],flow_map)
        for key in lab.PROMOTED_STRATEGIES:
            book=lab.STATE['books'][key]
            self.assertIsNotNone(book['position'],key)
            self.assertEqual(book['position']['entry_policy_version'],promoted_guard.FUNDED_POLICY_VERSION)
            self.assertEqual(book['position']['verified_entry_flow']['address'],ADDRESS)
            self.assertGreaterEqual(book['position']['entry_roundtrip_pnl_pct'],-1.5)

    def test_promoted_entries_wait_when_flow_proof_is_stale_or_absent(self):
        c=coin()
        stale={'source':promoted_guard.FLOW_SOURCE,'coverage_status':'COMPLETE',
            'window_ms':promoted_guard.FLOW_WINDOW_MS,'address':ADDRESS,'pairAddress':PAIR,
            'window_at':NOW,'latest_event_at':NOW-12_001,'available_at':NOW-12_000,
            'trades':4,'unique_wallets':3,'buy_usd':500,'sell_usd':100}
        with patch.object(lab,'now_ms',return_value=NOW), patch.object(lab.rug_guard,'check') as safety:
            lab.maybe_open([c],{(ADDRESS,PAIR):{**flows()[ADDRESS],'verified_flow':stale}})
        book=lab.STATE['books']['EARLY']
        self.assertIsNone(book['position'])
        self.assertGreater(book['entry_diagnostics']['promoted_flow_rejected'],0)
        safety.assert_not_called()

    def test_promoted_book_pauses_after_recent_loss_streak(self):
        book=lab.STATE['books']['EARLY']
        book['history']=[
            {'closed_at':NOW-1000,'pnl_usd':-1},
            {'closed_at':NOW-2000,'pnl_usd':-1},
            {'closed_at':NOW-3000,'pnl_usd':-1},
        ]
        with patch.object(lab,'now_ms',return_value=NOW):lab.maybe_open([coin()],flows())
        self.assertIsNone(book['position'])
        self.assertEqual(book['entry_diagnostics']['blocked_reason'],'promoted_recent_loss_cooldown')
        self.assertGreater(book['entry_diagnostics']['promoted_cooldown_remaining_ms'],0)

    def test_promoted_guard_ignores_noneligible_research_rows(self):
        book={'history':[
            {'closed_at':NOW-1000,'pnl_usd':9,'promotion_eligible':False},
            {'closed_at':NOW-2000,'pnl_usd':-1},
            {'closed_at':NOW-3000,'pnl_usd':-1},
            {'closed_at':NOW-4000,'pnl_usd':-1},
        ]}
        self.assertGreater(lab.promoted_pause_remaining_ms(book,NOW),0)
        book['history'][1]['pnl_usd']=1
        self.assertEqual(lab.promoted_pause_remaining_ms(book,NOW),0)

    def test_scalper_requires_verified_flow_and_positive_trend(self):
        rule=a.RULES['SCALPER']
        f={'score':95,'liq':50000,'m5':4,'bs':1.2,'lmc':.1,'age':60,
           'h1':8,'vol_liq':2,'flow':copy.deepcopy(flows()[ADDRESS])}
        self.assertTrue(rule.matches(f))
        for key,value in [('trades',0),('ratio',1.1),('buy_usd',50),('unique_wallets',1),('max_sell',300)]:
            bad=copy.deepcopy(f);bad['flow'][key]=value
            self.assertFalse(rule.matches(bad),key)
        for key,value in [('m5',-0.1),('h1',-0.1),('liq',24999),('age',241)]:
            bad=copy.deepcopy(f);bad[key]=value
            self.assertFalse(rule.matches(bad),key)

    def test_scalper_risk_is_capped_at_25_percent_and_pauses_below_minimum(self):
        self.assertEqual(a.entry_notional_limit('SCALPER',100,150),25)
        self.assertEqual(a.entry_notional_limit('SCALPER',40,150),10)
        self.assertEqual(a.entry_notional_limit('SCALPER',19.09,150),4.7725)
        self.assertEqual(a.entry_notional_limit('SCALPER',7.99,150),0)
        self.assertEqual(a.entry_minimum_notional('SCALPER'),2)
        self.assertEqual(a.entry_notional_limit('PRECISION',19.09,150),19.09)

    def test_scalper_sizes_down_and_keeps_cost_checks_at_low_balance(self):
        # LAB_ACTIVE_V6: a $4.77 risk-capped entry pays ~1.52% (fixed network
        # fees dominate), above the 1.5% cap, so it is reported as cost-infeasible
        # instead of being forced. At a balance whose cap fits, it still sizes down.
        lab.STATE['books']['SCALPER']['balance']=19.09
        c=coin();c['priceChange']['h1']=20
        with patch.object(lab,'now_ms',return_value=NOW):
            lab.maybe_open([c],flows())
        book=lab.STATE['books']['SCALPER']
        self.assertIsNone(book['position'])
        self.assertEqual(book['entry_diagnostics']['risk_limited_notional_usd'],4.7725)
        self.assertEqual(book['entry_diagnostics']['cost_rejected'],1)
        self.assertEqual(book['entry_diagnostics']['cost_infeasible_candidates'],1)
        self.assertEqual(book['entry_diagnostics']['blocked_reason'],'modeled_roundtrip_cost_limit')
        self.assertEqual(book['entry_diagnostics']['max_entry_roundtrip_cost_pct'],1.5)
        book['balance']=40
        with patch.object(lab,'now_ms',return_value=NOW):
            lab.maybe_open([c],flows())
        self.assertIsNotNone(book['position'])
        self.assertLessEqual(book['position']['notional_usd'],40*.25)
        self.assertGreaterEqual(book['position']['notional_usd'],a.SCALPER_MIN_NOTIONAL_USD)
        self.assertGreaterEqual(book['position']['entry_roundtrip_pnl_pct'],-a.admission_cost_cap_pct(lab.STOP_LOSS))
        self.assertEqual(book['position']['entry_cost_cap_pct'],1.5)
        self.assertGreaterEqual(book['position']['stop_headroom_pct'],1.5)

    def test_scalper_stops_below_risk_sized_minimum(self):
        lab.STATE['books']['SCALPER']['balance']=7.99
        c=coin();c['priceChange']['h1']=20
        with patch.object(lab,'now_ms',return_value=NOW):
            lab.maybe_open([c],flows())
        book=lab.STATE['books']['SCALPER']
        self.assertIsNone(book['position'])
        self.assertEqual(book['entry_diagnostics']['blocked_reason'],'scalper_risk_cap_below_minimum')
        self.assertEqual(book['entry_diagnostics']['risk_limited_notional_usd'],0)

    def test_budget_includes_network(self):
        for balance in [9.99,10,20,100,500]:
            q=a.affordable_entry(coin(),balance,150,lab.entry_execution,lab.exit_execution)
            if q:
                self.assertLessEqual(q['entry']['capital_committed_usd'],balance+1e-9)
                self.assertLessEqual(q['notional'],150)
                self.assertGreaterEqual(q['initial_pnl_pct'],-a.MAX_ENTRY_COST_PCT)
                self.assertLess(q['initial_pnl_usd'],0)
        self.assertIsNone(a.affordable_entry(coin(),9.99,150,lab.entry_execution,lab.exit_execution))

    def test_downsizes_instead_of_waiving_costs(self):
        c=coin();c['liquidityUsd']=20000
        q=a.affordable_entry(c,500,150,lab.entry_execution,lab.exit_execution)
        self.assertIsNotNone(q)
        self.assertLess(q['notional'],150)
        self.assertGreaterEqual(q['initial_pnl_pct'],-2.75)
        tight=a.affordable_entry(c,500,150,lab.entry_execution,lab.exit_execution,
                                 max_entry_cost_pct=a.admission_cost_cap_pct(lab.STOP_LOSS))
        self.assertIsNotNone(tight)
        self.assertLess(tight['notional'],q['notional'])
        self.assertGreaterEqual(tight['initial_pnl_pct'],-1.5)

    def test_tighter_promoted_cost_cap_rejects_without_waiving_costs(self):
        c=coin()
        ordinary=a.affordable_entry(c,500,150,lab.entry_execution,lab.exit_execution)
        self.assertIsNotNone(ordinary)
        self.assertLess(ordinary['initial_pnl_pct'],-.5)
        self.assertIsNone(a.affordable_entry(c,500,150,lab.entry_execution,lab.exit_execution,
                                             max_entry_cost_pct=.5))
        self.assertIsNone(a.affordable_entry(c,500,150,lab.entry_execution,lab.exit_execution,
                                             max_entry_cost_pct=2.76))

    def test_unaffordable_model_fees_rejected(self):
        c=coin();c.update(dexId='pumpswap',marketCap=10000,liquidityUsd=10000)
        self.assertIsNone(a.affordable_entry(c,500,150,lab.entry_execution,lab.exit_execution))

    def test_no_history_balance_or_open_position_reset(self):
        for b in lab.STATE['books'].values():
            b['history']=[{'trade_no':7,'address':ADDRESS,'pnl_usd':1,'closed_at':NOW}]
            b['position']={'trade_no':8,'address':ADDRESS,'keep':'original'}
        before=copy.deepcopy(lab.STATE)
        with patch.object(lab,'now_ms',return_value=NOW):lab.maybe_open([coin()],flows())
        after=copy.deepcopy(lab.STATE)
        lifecycle=after.pop('strategy_lifecycle')
        self.assertEqual(lifecycle['version'],lab.lifecycle.VERSION)
        self.assertEqual(lifecycle['retired_strategy_ids'],[])
        self.assertEqual(lifecycle['retired_open_position_ids'],[])
        self.assertEqual(set(lifecycle['active_registered_strategy_ids']),set(before['books']))
        for key,b in after['books'].items():
            if key in lab.PROMOTED_STRATEGIES:
                self.assertNotIn('strategy_lifecycle',b)
            else:
                marker=b.pop('strategy_lifecycle')
                self.assertEqual(marker['version'],lab.lifecycle.VERSION)
                self.assertEqual(marker['status'],'active')
                self.assertTrue(marker['entry_enabled'])
                self.assertTrue(marker['position_management_enabled'])
        # Only the lifecycle annotations may differ: money, history, positions,
        # sequence numbers and every other persisted field stay exactly equal.
        self.assertEqual(after,before)

    def test_entry_records_cost_not_zero_and_preserves_book_balance(self):
        with patch.object(lab,'now_ms',return_value=NOW):lab.maybe_open([coin()],flows())
        opened=[b for b in lab.STATE['books'].values() if b['position']]
        self.assertGreater(len(opened),10)
        for b in opened:
            self.assertEqual(b['balance'],b['starting_balance'])
            self.assertEqual(b['position']['entry_policy_version'],a.POLICY_VERSION)
            self.assertEqual(b['position']['entry_policy_version'],'LAB_ACTIVE_V7_DEFENSIVE_ENTRY')
            self.assertLess(b['position']['open_pnl_usd'],0)
            # LAB_ACTIVE_V6: TEST books share the funded 0.5 x stop cost cap.
            self.assertGreaterEqual(b['position']['entry_roundtrip_pnl_pct'],-1.5)
            self.assertEqual(b['position']['entry_cost_cap_pct'],1.5)
            self.assertEqual(b['position']['stop_loss_net_pct'],3.0)
            self.assertAlmostEqual(b['position']['stop_headroom_pct'],
                                   3.0+b['position']['entry_roundtrip_pnl_pct'],places=5)
        promoted=[lab.STATE['books'][key] for key in lab.PROMOTED_STRATEGIES]
        self.assertEqual(sum(book['starting_balance'] for book in promoted),1000)
        for book in promoted:
            self.assertEqual(book['portfolio_group'],'PROMOTED_PAPER')
            self.assertIsNone(book['position'])

    def test_promotion_drain_blocks_new_entries_without_disabling_position_exits(self):
        book=lab.STATE['books']['EARLY']
        book['promotion_pending']=True
        with patch.object(lab,'now_ms',return_value=NOW):lab.maybe_open([coin()],flows())
        self.assertIsNone(book['position'])
        self.assertEqual(book['entry_diagnostics']['blocked_reason'],'promotion_waiting_for_existing_position_exit')

    def test_legacy_promotion_position_keeps_using_shared_feed_for_exit(self):
        legacy={
            'id':'LEGACY_EARLY','strategy_id':'EARLY','name':'Early legacy',
            'starting_balance':500,'balance':500,'history':[],'trade_seq':1,
            'position':{'trade_no':1,'strategy_id':'EARLY','address':ADDRESS,'pairAddress':PAIR,
                        'entry_price':.01,'current_price':.01,'quantity':10000,'original_quantity':10000,
                        'notional_usd':100,'remaining_cost_basis_usd':100,'opened_at':NOW-1000,
                        'partial_realized_pnl':0,'entry_network_fee_usd':0}
        }
        lab.STATE['portfolio_setup']={'legacy_draining_books':{'EARLY':legacy}}
        quote={'fill_price':.0094,'net_proceeds_usd':94,'dex_fee_usd':0,'network_fee_usd':0,
               'impact_pct':0,'slippage_pct':0,'latency_pct':0}
        with patch.object(lab,'exit_execution',return_value=quote),patch.object(lab,'now_ms',return_value=NOW):
            lab.update_positions({},[coin()])
        self.assertIsNone(legacy['position'])
        self.assertEqual(legacy['history'][0]['pnl_usd'],-6)
        self.assertEqual(lab.STATE['books']['EARLY']['balance'],250)

    def test_flow_map_reads_the_configured_shared_tape(self):
        with tempfile.TemporaryDirectory() as folder:
            tape_path=Path(folder)/'live_tape.json'
            stamp=lab.now_ms()
            base={'ts':stamp,'event_time':stamp,'observed_at':stamp+100,'available_at':stamp+200,
                  'confirmed_swap':True,'quality_flags':[],'pairAddress':PAIR,'address':ADDRESS}
            tape_path.write_text(__import__('json').dumps({'pair_coverage':{PAIR:{'status':'COMPLETE'}},'events':[
                {**base,'direction':'BUY','usd_amount':240,'wallet':'buyer'},
                {**base,'direction':'SELL','usd_amount':40,'wallet':'seller'},
            ]}),encoding='utf-8')
            with patch.object(lab,'LIVE_TAPE_PATH',tape_path),patch.object(lab,'now_ms',return_value=stamp+250):
                observed=lab.flow_map()
        self.assertEqual(observed[ADDRESS]['trades'],2)
        self.assertEqual(observed[ADDRESS]['ratio'],6)
        self.assertEqual(observed[ADDRESS]['unique_wallets'],2)

    def test_flow_map_rejects_incomplete_pairs_and_bad_event_provenance(self):
        with tempfile.TemporaryDirectory() as folder:
            tape_path=Path(folder)/'live_tape.json';stamp=lab.now_ms()
            base={'ts':stamp,'event_time':stamp,'observed_at':stamp+100,'available_at':stamp+200,
                  'confirmed_swap':True,'quality_flags':[],'pairAddress':PAIR,'address':ADDRESS,
                  'direction':'BUY','usd_amount':240,'wallet':'buyer'}
            incomplete_pair='7pQvPNLa7s8kN9uUq47XkbKzA9a7JwzsHjFeNFK2M4eH'
            events=[base,
                {**base,'wallet':'partial','pairAddress':incomplete_pair},
                {**base,'wallet':'unverified','quality_flags':['QUOTE_USD_UNKNOWN']},
                {**base,'wallet':'future','available_at':stamp+60000},
                {**base,'wallet':'not-swap','confirmed_swap':False}]
            tape_path.write_text(__import__('json').dumps({'pair_coverage':{
                PAIR:{'status':'COMPLETE'},incomplete_pair:{'status':'DEGRADED'}},'events':events}),encoding='utf-8')
            with patch.object(lab,'LIVE_TAPE_PATH',tape_path),patch.object(lab,'now_ms',return_value=stamp+250):
                observed=lab.flow_map()
        self.assertEqual(observed[ADDRESS]['trades'],1)
        self.assertEqual(observed[ADDRESS]['unique_wallets'],1)

    def test_scalper_shows_when_coverage_blocks_an_otherwise_valid_signal(self):
        with tempfile.TemporaryDirectory() as folder:
            tape_path=Path(folder)/'live_tape.json'
            tape_path.write_text(__import__('json').dumps({
                'status':'degraded','coverage':0,'backlog':238,
                'pair_coverage':{PAIR:{'status':'DEGRADED'}},'events':[]}),encoding='utf-8')
            with patch.object(lab,'LIVE_TAPE_PATH',tape_path),patch.object(lab,'now_ms',return_value=NOW):
                observed=lab.flow_map()
                c=coin();c['priceChange']['h1']=20
                lab.maybe_open([c],observed)
        diagnostics=lab.STATE['books']['SCALPER']['entry_diagnostics']
        self.assertIsNone(lab.STATE['books']['SCALPER']['position'])
        self.assertEqual(diagnostics['blocked_reason'],'verified_flow_unavailable')
        self.assertEqual(diagnostics['flow_tape_coverage_pct'],0)
        self.assertEqual(diagnostics['flow_tape_status'],'degraded')
        self.assertGreater(diagnostics['flow_rejected_candidates'],0)

    def test_position_management_uses_the_shared_feed_without_extra_price_api(self):
        book=lab.STATE['books']['PRECISION']
        book['position']={'trade_no':1,'address':ADDRESS,'pairAddress':PAIR,'entry_price':.01,
                          'current_price':.01,'quantity':10000,'original_quantity':10000,
                          'notional_usd':100,'remaining_cost_basis_usd':100,
                          'opened_at':NOW,'partial_realized_pnl':0,'entry_network_fee_usd':0}
        quote={'fill_price':.0101,'net_proceeds_usd':100,'dex_fee_usd':0,'network_fee_usd':0,
               'impact_pct':0,'slippage_pct':0,'latency_pct':0}
        with patch.object(lab,'exit_execution',return_value=quote), patch.object(lab.SESSION,'get',side_effect=AssertionError('must reuse shared feed')) as request, patch.object(lab,'now_ms',return_value=NOW):
            lab.update_positions({},[coin()])
        request.assert_not_called()
        self.assertEqual(book['position']['current_price'],coin()['priceUsd'])

    def test_net_stop_all_36_no_loss_clamping(self):
        c=coin()
        for b in lab.STATE['books'].values():
            b['position']={'trade_no':1,'address':ADDRESS,'pairAddress':PAIR,'entry_price':1,
                           'current_price':1,'quantity':100,'original_quantity':100,
                           'notional_usd':100,'remaining_cost_basis_usd':100,
                           'opened_at':NOW-1000,'partial_realized_pnl':0,'entry_network_fee_usd':0}
        quote={'fill_price':.94,'net_proceeds_usd':94,'dex_fee_usd':0,'network_fee_usd':0,
               'impact_pct':0,'slippage_pct':0,'latency_pct':0}
        with patch.object(lab,'exit_execution',return_value=quote),patch.object(lab,'now_ms',return_value=NOW):
            lab.update_positions({},[c])
        for b in lab.STATE['books'].values():
            self.assertIsNone(b['position'])
            self.assertEqual(b['history'][0]['exit_reason'],'STOP_LOSS_3_NET')
            self.assertEqual(b['history'][0]['pnl_pct'],-6)
            self.assertEqual(b['balance'],b['starting_balance']-6)

    def test_momentum_rush_opens_broader_low_cap_setup_without_promoting_it(self):
        c=coin()
        # Liquidity 60k keeps the modeled round trip of a $60 low-cap entry
        # inside the V6 1.5% cap; score 84 keeps the shared MOMENTUM rule off.
        c.update(score=84,marketCap=40000,liquidityUsd=60000,ageMinutes=12)
        c['priceChange']={'m5':8,'h1':30}
        c['volume']={'h1':20000}
        c['txns']={'m5':{'buys':40,'sells':10}}
        flow={ADDRESS:{'trades':8,'buys':6,'sells':2,'buy_usd':800,'sell_usd':100,
                       'unique_wallets':6,'ratio':4,'max_sell':50}}
        features=a.market_features(c,flow[ADDRESS])
        self.assertFalse(a.RULES['MOMENTUM'].matches(features))
        with patch.object(lab.rug_guard,'check',return_value={
                'status':'pass','mint':ADDRESS,'pair':PAIR,'checked_at':NOW-1}), \
             patch.object(lab.price_integrity,'check',return_value={
                'status':'pass','mint':ADDRESS,'pair':PAIR}):
            for stamp,price in ((NOW-16000,.01),(NOW-8000,.01005),(NOW,.0102)):
                with patch.object(lab,'now_ms',return_value=stamp):
                    lab.maybe_open([{**c,'updatedAt':stamp,'priceUsd':price}],flow)
        book=lab.STATE['books']['MOMENTUM_RUSH_BRAIN']
        self.assertEqual(book['portfolio_group'],'TEST')
        self.assertIsNotNone(book['position'])
        self.assertLessEqual(book['position']['notional_usd'],60)
        self.assertEqual(book['position']['momentum_rush_brain']['target_win_rate_pct'],80.0)
        self.assertFalse(book['position']['momentum_rush_brain']['target_is_guarantee'])

    def test_momentum_rush_rejects_bearish_low_cap_flow(self):
        c=coin()
        c.update(score=86,marketCap=40000,liquidityUsd=14000,ageMinutes=15)
        c['priceChange']={'m5':3,'h1':10}
        c['txns']={'m5':{'buys':16,'sells':9}}
        flow={'trades':6,'buys':2,'sells':4,'buy_usd':100,'sell_usd':450,
              'unique_wallets':4,'ratio':.22,'max_sell':1800}
        meta=lab.rush_brain.evaluate(lab.STATE['books']['MOMENTUM_RUSH_BRAIN'],c,a.market_features(c,flow),NOW)
        self.assertFalse(meta['allow'])
        self.assertTrue(meta['verified_flow_bearish'])
        self.assertTrue(meta['dump_risk'])

    def test_constants_preserved(self):
        self.assertEqual((lab.STOP_LOSS,lab.TAKE_PROFIT,lab.MAX_HOLD_MIN),(3,10,60))
        self.assertEqual(lab.TRADE_NOTIONAL,150)
        self.assertEqual(lab.lab_cost_cap_pct(),promoted_guard.max_entry_cost_pct(lab.STOP_LOSS))
        self.assertEqual(lab.lab_cost_cap_pct(),1.5)
        self.assertEqual(a.MAX_ENTRY_COST_PCT,2.75)
        self.assertEqual(lab.STRATEGY_START_BALANCES['SCALPER'],100)

if __name__=='__main__':unittest.main()
