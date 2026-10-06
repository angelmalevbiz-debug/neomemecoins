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

ADDRESS='So11111111111111111111111111111111111111112'
PAIR='EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v'
NOW=1_800_000_000_000

def coin():
    return {'address':ADDRESS,'pairAddress':PAIR,'symbol':'TEST','name':'Test',
            'priceUsd':.01,'priceNative':.00008,'marketCap':1e6,'liquidityUsd':1e6,
            'dexId':'raydium','updatedAt':NOW,'score':100,'ageMinutes':50,
            'priceChange':{'m5':12,'h1':50},'volume':{'h1':1e6},
            'txns':{'m5':{'buys':60,'sells':20}}}

def flows():
    return {ADDRESS:{'trades':20,'buys':15,'sells':5,'buy_usd':1000,'sell_usd':100,
                     'unique_wallets':10,'ratio':10,'max_sell':30}}

class ActivityTests(unittest.TestCase):
    def setUp(self):
        guard=patch.object(lab.price_integrity,'check',return_value={'status':'pass','version':'OFFLINE_FIXTURE'})
        guard.start();self.addCleanup(guard.stop)
        lab.STATE={'started_at':42,'books':{s['id']:lab.empty_book(s) for s in lab.STRATEGIES}}

    def test_all_33_rules_exist(self):
        self.assertEqual(len(a.RULES),33)
        self.assertEqual(set(a.RULES),{s['id'] for s in lab.STRATEGIES})
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
        lab.STATE['books']['SCALPER']['balance']=19.09
        c=coin();c['priceChange']['h1']=20
        with patch.object(lab,'now_ms',return_value=NOW):
            lab.maybe_open([c],flows())
        book=lab.STATE['books']['SCALPER']
        self.assertIsNotNone(book['position'])
        self.assertLessEqual(book['position']['notional_usd'],19.09*.25)
        self.assertGreaterEqual(book['position']['notional_usd'],a.SCALPER_MIN_NOTIONAL_USD)
        self.assertEqual(book['entry_diagnostics']['risk_limited_notional_usd'],4.7725)
        self.assertGreaterEqual(book['position']['entry_roundtrip_pnl_pct'],-a.MAX_ENTRY_COST_PCT)

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

    def test_unaffordable_model_fees_rejected(self):
        c=coin();c.update(dexId='pumpswap',marketCap=10000,liquidityUsd=10000)
        self.assertIsNone(a.affordable_entry(c,500,150,lab.entry_execution,lab.exit_execution))

    def test_no_history_balance_or_open_position_reset(self):
        for b in lab.STATE['books'].values():
            b['history']=[{'trade_no':7,'address':ADDRESS,'pnl_usd':1,'closed_at':NOW}]
            b['position']={'trade_no':8,'address':ADDRESS,'keep':'original'}
        before=copy.deepcopy(lab.STATE)
        with patch.object(lab,'now_ms',return_value=NOW):lab.maybe_open([coin()],flows())
        self.assertEqual(lab.STATE,before)

    def test_entry_records_cost_not_zero_and_preserves_book_balance(self):
        with patch.object(lab,'now_ms',return_value=NOW):lab.maybe_open([coin()],flows())
        opened=[b for b in lab.STATE['books'].values() if b['position']]
        self.assertGreater(len(opened),10)
        for b in opened:
            self.assertEqual(b['balance'],b['starting_balance'])
            self.assertEqual(b['position']['entry_policy_version'],a.POLICY_VERSION)
            self.assertLess(b['position']['open_pnl_usd'],0)
            self.assertGreaterEqual(b['position']['entry_roundtrip_pnl_pct'],-2.75)
        promoted=[lab.STATE['books'][key] for key in lab.PROMOTED_STRATEGIES]
        self.assertEqual(sum(book['starting_balance'] for book in promoted),1000)
        for book in promoted:
            self.assertEqual(book['portfolio_group'],'PROMOTED_PAPER')
            self.assertLessEqual(book['position']['notional_usd'],book['starting_balance']*.25)

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
            with patch.object(lab,'LIVE_TAPE_PATH',tape_path):observed=lab.flow_map()
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

    def test_net_stop_all_33_no_loss_clamping(self):
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

    def test_constants_preserved(self):
        self.assertEqual((lab.STOP_LOSS,lab.TAKE_PROFIT,lab.MAX_HOLD_MIN),(3,10,60))
        self.assertEqual(lab.TRADE_NOTIONAL,150)
        self.assertEqual(lab.STRATEGY_START_BALANCES['SCALPER'],100)

if __name__=='__main__':unittest.main()
