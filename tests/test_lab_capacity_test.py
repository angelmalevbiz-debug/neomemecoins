"""Synthetic load-test regression fixtures are not live trade/profit evidence."""
import copy
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
import strategy_lab as lab
import funded_active_paper as active
import lab_capacity_test as capacity
from lab_dashboard_projection import compact_strategy_lab

NOW=1_800_000_000_000


def coin(index=1,**changes):
    return {'address':'BrUimx7KncgRNTggAdZdaX2s5XUqQyR6XMRmEg4mpum'+str(index),
        'pairAddress':'8t34p7n94man8wcmFdHedYkJaWEhA9nKGJMLZyZUz'+str(index),
        'symbol':'UNIT'+str(index),'priceUsd':.01,'priceNative':.01/150,
        'quoteTokenAddress':active.SOL,'dexId':'pumpswap','updatedAt':NOW,
        'liquidityUsd':400_000,'marketCap':10_000_000,'ageMinutes':10_000,
        'priceChange':{'m5':-.5,'h1':-1},'volume':{'h1':60_000,'m5':4000},
        'txns':{'m5':{'buys':10,'sells':20}},**changes}


class CapacityTestTests(unittest.TestCase):
    def setUp(self):
        self.clock=NOW
        self.strategies=[s for s in lab.STRATEGIES if s['id'] in active.RULES]
        self.books={s['id']:lab.empty_book(s) for s in self.strategies}
        for book in self.books.values():
            book.update(starting_balance=1000,balance=950,allocation_usd=1000,
                history=[{'trade_no':1,'entry_policy_version':active.VERSION,'pnl_usd':-50,
                          'opened_at':NOW-120_000,'closed_at':NOW-60_000}],trade_seq=1)
        self.rows=[coin(i) for i in range(1,6)]
        self.old=copy.deepcopy(self.books)
        self.shadow={'allowed':False,'reasons':['heat_buy_share_5m','pool_loss_cooldown'],
                     'log_only_flags':[]}
        for p in (
            patch.dict(os.environ,{active.ENV:'1',active.CAPACITY_TEST_ENV:'1','NEO_ENGINE_MODE':'PAPER'}),
            patch.object(lab,'STATE',{'books':self.books}),patch.object(lab,'STRATEGIES',self.strategies),
            patch.object(lab,'now_ms',side_effect=lambda:self.clock),
            patch.object(lab.rug_guard,'check',side_effect=lambda c:{'status':'pass',
                'mint':c['address'],'pair':c['pairAddress'],'checked_at':self.clock}),
            patch.object(lab.price_integrity,'check',side_effect=lambda c:{'status':'pass',
                'mint':c['address'],'pair':c['pairAddress']}),
            patch.object(lab,'funded_defensive_decision',return_value=self.shadow),
            patch.object(lab,'promoted_pause_remaining_ms',return_value=120_000),
        ):
            p.start();self.addCleanup(p.stop)
        capacity.CURSORS.clear()

    def test_fills_all_sixteen_slots_in_one_refresh_without_signals_or_flow(self):
        lab.maybe_open(self.rows,{})
        self.assertEqual(sum(len(active.positions(b)) for b in self.books.values()),16)
        for sid,b in self.books.items():
            positions=active.positions(b)
            self.assertEqual(len({p['address'] for p in positions}),4)
            self.assertTrue(all(p['notional_usd']==100 for p in positions))
            self.assertLess(sum(p['remaining_cost_basis_usd'] for p in positions),475)
            self.assertEqual(b['balance'],950)
            self.assertEqual(b['history'],self.old[sid]['history'])
            self.assertIn('ТЕСТ ЗАПЪЛВАНЕ',b['name'])
            for p in positions:
                self.assertEqual(p['entry_policy_version'],capacity.VERSION)
                self.assertTrue(p['capacity_test'])
                self.assertFalse(p['strategy_validation'])
                self.assertFalse(p['capacity_test_shadow']['strategy_market_signal'])
                self.assertFalse(p['capacity_test_shadow']['flow_admission']['allow'])
                self.assertFalse(p['capacity_test_shadow']['defensive_entry']['allowed'])
                self.assertLess(p['open_pnl_usd'],0)
                self.assertGreater(p['entry_dex_fee_usd'],0)
                self.assertGreater(p['entry_network_fee_usd'],0)
            self.assertEqual(b['entry_diagnostics']['blocked_reason'],'funded_active_slots_full')

    def test_repeated_poll_does_not_overfill_or_duplicate(self):
        for _ in range(3):lab.maybe_open(self.rows,{})
        self.assertTrue(all(len(active.positions(b))==4 and b['trade_seq']==5 for b in self.books.values()))

    def test_failed_safety_or_price_never_becomes_a_position(self):
        for p in (patch.object(lab.rug_guard,'check',return_value={'status':'fail'}),
                  patch.object(lab.price_integrity,'check',return_value={'status':'blocked'}),
                  patch.object(lab.price_integrity,'check',return_value={'status':'pass','mint':'wrong','pair':'wrong'})):
            with p:lab.maybe_open(self.rows,{})
            self.assertTrue(all(not active.positions(b) for b in self.books.values()))

    def test_stale_missing_price_wrong_quote_and_unknown_liquidity_cannot_fill(self):
        for bad in (coin(updatedAt=NOW-12_001),coin(priceUsd=0),coin(priceNative=0),
                    coin(liquidityUsd=float('nan')),coin(quoteTokenAddress='WRONG'),coin(dexId='other')):
            lab.maybe_open([bad],{})
            self.assertTrue(all(not active.positions(b) for b in self.books.values()))

    def test_live_or_unset_execution_mode_cannot_enable_capacity_testing(self):
        for mode in ('LIVE',''):
            with patch.dict(os.environ,{'NEO_ENGINE_MODE':mode}):
                self.assertFalse(active.capacity_test_enabled())
                self.assertFalse(capacity.applies(self.books['EARLY']))
        with patch.dict(os.environ,{'NEO_EXECUTION_MODE':'LIVE'}):
            self.assertFalse(active.capacity_test_enabled())
        other={**self.books['EARLY'],'id':'OTHER'}
        self.assertFalse(capacity.applies(other))
        self.assertFalse(capacity.applies({**self.books['EARLY'],'portfolio_group':'TEST'}))
        self.assertFalse(capacity.applies({**self.books['EARLY'],'promotion_pending':True}))

    def test_bad_execution_costs_cannot_fill_or_break_ledger_serialization(self):
        with patch.object(lab,'entry_execution',return_value={'fill_price':float('nan'),'quantity':100}):
            lab.maybe_open(self.rows,{})
        self.assertTrue(all(not active.positions(b) for b in self.books.values()))

    def test_expired_market_during_preparation_cannot_commit(self):
        def delay(*args,**kwargs):
            self.clock=NOW+13_000
            return self.shadow
        with patch.object(lab,'funded_defensive_decision',side_effect=delay):
            lab.maybe_open(self.rows,{})
        self.assertTrue(all(not active.positions(b) for b in self.books.values()))

    def test_exposure_counts_preexisting_positions_and_never_overfills_them(self):
        b=self.books['EARLY'];legacy={'address':'old','pairAddress':'oldpool','notional_usd':400,
                'remaining_cost_basis_usd':400,'open_pnl_usd':0,'quote_status':'fresh'}
        b['position']=legacy
        lab.maybe_open(self.rows,{})
        self.assertEqual(active.positions(b),[legacy])
        self.assertEqual(b['trade_seq'],1)

    def test_cash_exposure_and_unpriced_held_lots_still_stop_load_test(self):
        b=self.books['EARLY'];b['balance']=199
        self.assertEqual(capacity.hard_capacity(b,NOW)['blocked_reason'],'funded_active_exposure_limit')
        b['balance']=950;b['position']={'notional_usd':100,'quote_status':'stale'}
        self.assertEqual(capacity.hard_capacity(b,NOW)['blocked_reason'],'funded_active_marks_unavailable')

    def test_daily_and_hourly_limits_are_shadow_not_an_infinite_cash_refill(self):
        b=self.books['EARLY']
        b['history']=[{'entry_policy_version':capacity.VERSION,'opened_at':NOW-2000,
                       'closed_at':NOW-1000,'pnl_usd':-2} for _ in range(60)]
        self.assertEqual(active.capacity(b,NOW)['blocked_reason'],'funded_active_daily_loss_limit')
        self.assertIsNone(capacity.hard_capacity(b,NOW)['blocked_reason'])
        lab.maybe_open(self.rows,{})
        self.assertEqual(len(active.positions(b)),4)
        self.assertEqual(b['balance'],950)

    def test_metrics_and_projection_distinguish_test_from_prior_strategy_results(self):
        lab.maybe_open(self.rows,{})
        view=compact_strategy_lab({'books':self.books})
        b=self.books['EARLY'];metrics=active.performance(b,NOW)
        self.assertEqual(metrics['version'],capacity.VERSION)
        self.assertEqual(metrics['trades'],0)
        self.assertEqual(metrics['orders_last_60m'],4)
        self.assertEqual(metrics['strategy_policy_closed_trades'],1)
        self.assertTrue(metrics['risk_limits_shadow_only'])
        self.assertTrue(view['books']['EARLY']['positions'][0]['capacity_test'])
        self.assertEqual(view['books']['EARLY']['position']['capacity_test_shadow'],b['position']['capacity_test_shadow'])

    def test_legacy_exits_close_after_flag_off_and_keep_fees_and_shadow(self):
        lab.maybe_open(self.rows,{})
        for b in self.books.values():
            for p in active.positions(b):
                p['exit_parameters']={**active.exit_parameters(b['id']),'version':capacity.VERSION}
        self.clock+=240_001
        marks=[{**c,'updatedAt':self.clock,'mark_received_at':self.clock} for c in self.rows]
        with patch.dict(os.environ,{active.CAPACITY_TEST_ENV:'0',active.ENV:'0'}),\
             patch.object(lab.POSITION_MARK_FEED,'resolve',side_effect=lambda p,by_pair,now:by_pair[(p['address'],p['pairAddress'])]):
            lab.update_positions({},marks)
        for b in self.books.values():
            self.assertEqual(active.positions(b),[])
            trades=[t for t in b['history'] if t.get('entry_policy_version')==capacity.VERSION]
            self.assertEqual(len(trades),4)
            self.assertTrue(all(t['exit_reason']=='FUNDED_ACTIVE_MAX_HOLD_4' for t in trades))
            self.assertTrue(all(t['pnl_usd']<0 and t['capacity_test_shadow'] for t in trades))
            self.assertLess(b['balance'],950)
            view=compact_strategy_lab({'books':{'EARLY':b}})['books']['EARLY']
            self.assertTrue(view['history'][0]['capacity_test'])

    def test_new_test_entries_freeze_dollar_target_and_stop_without_timer_or_trailing(self):
        lab.maybe_open(self.rows,{})
        for b in self.books.values():
            for p in active.positions(b):
                exits=p['exit_parameters']
                self.assertEqual(exits['version'],active.CAPACITY_EXIT_VERSION)
                self.assertEqual(exits['take_profit_net_usd'],30)
                self.assertEqual(exits['stop_loss_net_usd'],10)
                self.assertEqual(p['stop_loss_net_pct'],10)
                self.assertIsNone(exits['max_hold_minutes'])
                self.assertIsNone(exits['profit_trail_arm_net_pct'])
            self.assertEqual(active.performance(b,NOW)['exit_policy'],active.capacity_exit_parameters())

    def test_dollar_exit_boundaries_ignore_age_peak_and_small_positive_pnl(self):
        p={'notional_usd':100,'peak_net_pct':80,'exit_parameters':active.capacity_exit_parameters()}
        for net in (-9.999,0,2,4,6,15,29.999):
            self.assertIsNone(active.exit_reason(p,net,60*24*7))
        self.assertEqual(active.exit_reason(p,30,0),'CAPACITY_TEST_TAKE_PROFIT_NET_USD')
        self.assertEqual(active.exit_reason(p,-10,0),'CAPACITY_TEST_STOP_NET_USD')
        self.assertEqual(active.exit_reason(p,-70,0),'CAPACITY_TEST_STOP_NET_USD')

    def test_dollar_target_is_not_a_percent_of_the_notional_or_account_balance(self):
        p={'notional_usd':200,'exit_parameters':active.capacity_exit_parameters(200)}
        self.assertIsNone(active.exit_reason(p,14.999,1))
        self.assertEqual(active.exit_reason(p,15,1),'CAPACITY_TEST_TAKE_PROFIT_NET_USD')
        self.assertEqual(active.exit_reason(p,-5,1),'CAPACITY_TEST_STOP_NET_USD')
        for invalid in (0,-1,float('nan'),True):
            with self.assertRaises(ValueError):active.capacity_exit_parameters(invalid)
        p['notional_usd']=-1
        self.assertIsNone(active.exit_reason(p,999,1))

    def test_existing_test_exit_change_is_audited_idempotent_and_preserves_entries_and_history(self):
        lab.maybe_open(self.rows,{})
        for b in self.books.values():
            for p in active.positions(b):
                p['exit_parameters']={**active.exit_parameters(b['id']),'version':capacity.VERSION}
        before=copy.deepcopy(self.books)
        changes=active.apply_capacity_exit_policy(self.books,NOW+123)
        self.assertEqual(len(changes),16)
        changed_fields={'exit_parameters','exit_policy_label','stop_loss_net_pct',
                        'stop_headroom_pct','exit_policy_changes'}
        for sid,b in self.books.items():
            self.assertEqual(b['history'],before[sid]['history'])
            self.assertEqual(b['balance'],before[sid]['balance'])
            self.assertEqual(b['trade_seq'],before[sid]['trade_seq'])
            for p,old in zip(active.positions(b),active.positions(before[sid])):
                self.assertEqual({k:v for k,v in p.items() if k not in changed_fields},
                                 {k:v for k,v in old.items() if k not in changed_fields})
                event=p['exit_policy_changes'][0]
                self.assertEqual(event['at'],NOW+123)
                self.assertEqual(event['previous_parameters'],old['exit_parameters'])
                self.assertEqual(event['new_parameters'],p['exit_parameters'])
        after=copy.deepcopy(self.books)
        self.assertEqual(active.apply_capacity_exit_policy(self.books,NOW+999),[])
        self.assertEqual(self.books,after)
        view=compact_strategy_lab({'books':self.books})['books']['EARLY']
        self.assertEqual(view['position']['exit_policy_changes'],view['positions'][0]['exit_policy_changes'])

    def test_exit_migration_does_not_touch_closed_rows_ordinary_lots_or_disabled_modes(self):
        lab.maybe_open(self.rows,{})
        for b in self.books.values():
            for p in active.positions(b):
                p['exit_parameters']={**active.exit_parameters(b['id']),'version':capacity.VERSION}
        before=copy.deepcopy(self.books)
        for flags in ({active.CAPACITY_TEST_ENV:'0'},{'NEO_ENGINE_MODE':'LIVE'},
                      {'NEO_EXECUTION_MODE':'LIVE'}):
            with patch.dict(os.environ,flags):
                self.assertEqual(active.apply_capacity_exit_policy(self.books,NOW),[])
            self.assertEqual(self.books,before)
        self.books['EARLY']['positions'][0]['entry_policy_version']=active.VERSION
        self.books['EARLY']['positions'][1]['capacity_test']=False
        self.books['MOMENTUM']['portfolio_group']='TEST'
        self.books['PRECISION']['promotion_pending']=True
        saved=copy.deepcopy(self.books)
        changes=active.apply_capacity_exit_policy(self.books,NOW)
        self.assertEqual(len(changes),6)
        self.assertEqual(self.books['MOMENTUM'],saved['MOMENTUM'])
        self.assertEqual(self.books['PRECISION'],saved['PRECISION'])
        for b in self.books.values():self.assertEqual(b['history'],saved[b['id']]['history'])

    def test_startup_load_requests_durable_exit_change_and_restart_does_not_repeat_it(self):
        lab.maybe_open(self.rows,{})
        for b in self.books.values():
            for p in active.positions(b):
                p['exit_parameters']={**active.exit_parameters(b['id']),'version':capacity.VERSION}
        saved=copy.deepcopy(self.books)
        raw={'books':self.books,'portfolio_setup':{'version':'PROMOTED_PAPER_COHORT_V1','status':'ACTIVE'}}
        with tempfile.TemporaryDirectory(prefix='neo-dollar-exit-test-') as tmp:
            path=Path(tmp)/'state.json'
            path.write_text(json.dumps(raw),encoding='utf-8')
            with patch.object(lab,'STATE_PATH',path),patch.object(lab,'RESET_FLAG_PATH',Path(tmp)/'reset'),\
                 patch.object(lab,'review_strategy_lifecycle',return_value={}):
                loaded=lab.load_state()
                self.assertTrue(loaded['_exit_policy_requires_persist'])
                self.assertFalse(loaded['_funding_requires_persist'])
                for sid,b in loaded['books'].items():
                    self.assertEqual(b['history'],saved[sid]['history'])
                    self.assertEqual(b['balance'],saved[sid]['balance'])
                    self.assertEqual(len(active.positions(b)),4)
                    self.assertTrue(all(len(p['exit_policy_changes'])==1 for p in active.positions(b)))
                path.write_text(json.dumps(loaded),encoding='utf-8')
                restarted=lab.load_state()
                self.assertFalse(restarted['_exit_policy_requires_persist'])
                self.assertEqual(restarted['books'],loaded['books'])

    def test_failed_startup_persist_prevents_decisions_on_an_unrecorded_exit_change(self):
        loaded={'books':self.books,'portfolio_setup':{'status':'ACTIVE'},
                '_exit_policy_requires_persist':True,'_funding_requires_persist':False}
        with patch.object(lab,'LOADED',False),patch.object(lab,'load_state',return_value=loaded),\
             patch.object(lab,'persist',side_effect=OSError('fixture write failure')),\
             patch.object(lab,'update_positions') as manage,patch.object(lab,'maybe_open') as enter,\
             patch.object(lab,'build_high_frequency') as hf:
            with self.assertRaises(OSError):lab.main()
        manage.assert_not_called();enter.assert_not_called();hf.assert_not_called()

    def test_fresh_marks_do_not_close_new_policy_at_four_minutes_or_on_a_small_gain(self):
        lab.maybe_open(self.rows,{})
        identities=[[p['trade_no'] for p in active.positions(b)] for b in self.books.values()]
        self.clock+=60*60*1000
        marks=[{**c,'updatedAt':self.clock,'mark_received_at':self.clock,
                'priceUsd':.011,'priceNative':.011/150} for c in self.rows]
        with patch.dict(os.environ,{active.CAPACITY_TEST_ENV:'0',active.ENV:'0'}),\
             patch.object(lab.POSITION_MARK_FEED,'resolve',side_effect=lambda p,by_pair,now:by_pair[(p['address'],p['pairAddress'])]):
            lab.update_positions({},marks)
        self.assertEqual([[p['trade_no'] for p in active.positions(b)] for b in self.books.values()],identities)
        self.assertTrue(all(len(b['history'])==1 for b in self.books.values()))
        self.assertTrue(all(0<p['open_pnl_usd']<30 for b in self.books.values() for p in active.positions(b)))

    def test_gross_thirty_percent_is_not_thirty_dollars_net_and_only_actual_target_closes(self):
        lab.maybe_open(self.rows,{})
        self.clock+=10_000
        def marks(price):
            return [{**c,'priceUsd':price,'priceNative':price/150,
                     'updatedAt':self.clock,'mark_received_at':self.clock} for c in self.rows]
        with patch.object(lab.POSITION_MARK_FEED,'resolve',side_effect=lambda p,by_pair,now:by_pair[(p['address'],p['pairAddress'])]):
            lab.update_positions({},marks(.013))
            self.assertTrue(all(len(active.positions(b))==4 for b in self.books.values()))
            self.assertTrue(all(p['open_pnl_usd']<30 for b in self.books.values() for p in active.positions(b)))
            lab.update_positions({},marks(.014))
        for b in self.books.values():
            self.assertEqual(active.positions(b),[])
            trades=[t for t in b['history'] if t.get('capacity_test')]
            self.assertEqual(len(trades),4)
            self.assertTrue(all(t['pnl_usd']>=30 and t['exit_dex_fee_usd']>0 for t in trades))
            self.assertTrue(all(t['exit_reason']=='CAPACITY_TEST_TAKE_PROFIT_NET_USD' for t in trades))

    def test_new_stop_keeps_working_after_flag_off_and_does_not_clamp_gap_losses(self):
        lab.maybe_open(self.rows,{})
        self.clock+=10_000
        marks=[{**c,'priceUsd':.008,'priceNative':.008/150,
                'updatedAt':self.clock,'mark_received_at':self.clock} for c in self.rows]
        with patch.dict(os.environ,{active.CAPACITY_TEST_ENV:'0',active.ENV:'0'}),\
             patch.object(lab.POSITION_MARK_FEED,'resolve',side_effect=lambda p,by_pair,now:by_pair[(p['address'],p['pairAddress'])]):
            lab.update_positions({},marks)
        for b in self.books.values():
            self.assertEqual(active.positions(b),[])
            trades=[t for t in b['history'] if t.get('capacity_test')]
            self.assertEqual(len(trades),4)
            self.assertTrue(all(t['pnl_usd']<-10 and t['exit_reason']=='CAPACITY_TEST_STOP_NET_USD' for t in trades))

    def test_stale_or_wrong_pool_target_price_cannot_close(self):
        lab.maybe_open(self.rows,{})
        self.clock+=60_000
        with patch.object(lab.POSITION_MARK_FEED,'resolve',side_effect=lambda p,by_pair,now:
                          {**self.rows[0],'address':p['address'],'pairAddress':p['pairAddress'],
                           'priceUsd':1,'mark_received_at':NOW}):
            lab.update_positions({},[])
        self.assertTrue(all(len(active.positions(b))==4 for b in self.books.values()))
        with patch.object(lab.POSITION_MARK_FEED,'resolve',side_effect=lambda p,by_pair,now:
                          {**self.rows[0],'priceUsd':1,'pairAddress':'wrong','mark_received_at':self.clock}):
            lab.update_positions({},[])
        self.assertTrue(all(len(active.positions(b))==4 for b in self.books.values()))


if __name__=='__main__':unittest.main()
