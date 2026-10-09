"""Synthetic load-test regression fixtures are not live trade/profit evidence."""
import copy
import os
import sys
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

    def test_exits_close_test_positions_after_flag_off_and_keep_fees_and_shadow(self):
        lab.maybe_open(self.rows,{})
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


if __name__=='__main__':unittest.main()
