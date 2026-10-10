"""Synthetic regressions, not evidence of live entries or future profit."""
import copy
import os
from unittest.mock import patch

import test_paper_250_policy as sized

active, lab, quality, NOW = sized.active, sized.lab, sized.quality, sized.NOW


class FlowSizeRepairTests(quality.unittest.TestCase):
    def setUp(self):
        sized.SizedPolicyTests.setUp(self)
        flags = patch.dict(os.environ, {active.FLOW_SIZE_ENV:'1', active.TICKER_WARNING_ENV:'1'})
        flags.start(); self.addCleanup(flags.stop)

    def flow(self, coin, **updates):
        flows = quality.strong_flows([coin])
        flows[(coin['address'],coin['pairAddress'])]['verified_flow'].update(
            {'buy_usd':400, 'sell_usd':150, **updates})
        return flows

    def test_restores_exact_pre_sizing_floors_without_reducing_order_or_cost_checks(self):
        expected = dict(minimum_confirmed_30s_trades=6,minimum_confirmed_30s_wallets=4,
            minimum_confirmed_30s_buy_usd=300,minimum_confirmed_30s_net_buy_usd=100,
            minimum_buy_sell_usd_ratio=1.5)
        self.assertEqual(active.quality_flow_requirements(),expected)
        self.assertEqual(active.entry_notional(),250)
        self.assertEqual(active.config()['quality_rules']['minimum_confirmed_30s_buy_usd'],300)
        for sid in active.RULES:
            self.assertEqual(active.entry_cost_limit(sid),1.5)
            self.assertEqual(active.reporting_version(sid),active.FLOW_SIZE_VERSION)
            self.assertEqual(active.ticker_warning_enabled(sid),sid in active.TICKER_WARNING_BOOKS)
        with patch.dict(os.environ,{active.FLOW_SIZE_ENV:'0'}):
            self.assertEqual(active.quality_flow_requirements()['minimum_confirmed_30s_buy_usd'],750)
            proof = {'verified_flow':self.flow(self.c)[(self.c['address'],self.c['pairAddress'])]['verified_flow']}
            self.assertEqual(active.quality_admission(self.c,proof,NOW,self.books)['reason'],
                             'quality_buy_flow_too_small')
            self.assertEqual(active.reporting_version('MOMENTUM'),active.TICKER_WARNING_VERSION)
            self.assertEqual(active.reporting_version('EARLY'),active.SIZED_VERSION)

    def test_actual_loop_each_book_opens_full_250_and_freezes_evidence_and_exits(self):
        for index,(sid,book) in enumerate(self.books.items()):
            strategy = next(s for s in self.strategies if s['id']==sid)
            coin = quality.fixture.coin(index,liquidityUsd=1_000_000,marketCap=20_000_000)
            before=copy.deepcopy(book)
            with patch.object(lab,'STATE',{'books':{sid:book}}), patch.object(lab,'STRATEGIES',[strategy]):
                lab.maybe_open([coin],self.flow(coin))
            positions=active.positions(book)
            self.assertEqual(len(positions),1,(sid,book.get('entry_diagnostics')))
            p=positions[0]
            self.assertEqual(p['notional_usd'],250)
            self.assertEqual(p['entry_policy_version'],active.FLOW_SIZE_VERSION)
            self.assertEqual(p['entry_quality_flow_requirements'],active.quality_flow_requirements())
            self.assertEqual(p['verified_entry_flow']['buy_usd'],400)
            self.assertEqual(p['exit_parameters'],active.exit_parameters(sid,250))
            self.assertEqual(p['entry_cost_cap_pct'],1.5)
            self.assertTrue(active.is_active_position(p))
            self.assertLess(p['open_pnl_usd'],0)
            self.assertEqual(active.performance(book,NOW)['orders_last_60m'],1)
            for field in ('balance','starting_balance','history','funding_events'):
                self.assertEqual(book.get(field),before.get(field))

    def test_weak_malformed_wrong_pool_or_stale_flow_never_fills(self):
        base=self.flow(self.c)
        identity=(self.c['address'],self.c['pairAddress'])
        for field,value in [('trades',5),('unique_wallets',3),('buy_usd',299),
                            ('sell_usd',301),('buy_usd',True),('trades',6.1),
                            ('buy_usd',float('nan')),('coverage_status','DEGRADED'),
                            ('pairAddress','wrong'),('latest_event_at',NOW-12_001)]:
            flows=copy.deepcopy(base); flows[identity]['verified_flow'][field]=value
            lab.maybe_open([self.c],flows)
            self.assertTrue(all(not active.positions(b) for b in self.books.values()),field)
        lab.maybe_open([self.c],{})
        self.assertTrue(all(not active.positions(b) for b in self.books.values()))

    def test_defense_price_safety_cost_and_real_cash_are_still_hard(self):
        for target,name,value in [
                (lab,'funded_defensive_decision',{'allowed':False,'reasons':['heat_crash_in_progress']}),
                (lab.rug_guard,'check',{'status':'unknown'}),
                (lab.price_integrity,'check',{'status':'pass','mint':'wrong','pair':'wrong'})]:
            with patch.object(target,name,return_value=value):
                lab.maybe_open([self.c],self.flow(self.c))
            self.assertTrue(all(not active.positions(b) for b in self.books.values()),name)
        costly=quality.fixture.coin()  # Impact at $250 fails the unchanged 1.5% cap.
        lab.maybe_open([costly],self.flow(costly))
        self.assertTrue(all(not active.positions(b) for b in self.books.values()))
        for book in self.books.values(): book['balance']=249
        lab.maybe_open([self.c],self.flow(self.c))
        self.assertTrue(all(not active.positions(b) for b in self.books.values()))

    def test_cohort_mutex_and_old_losing_pool_pause_are_not_relaxed(self):
        proof={'verified_flow':self.flow(self.c)[(self.c['address'],self.c['pairAddress'])]['verified_flow']}
        book=self.books['MOMENTUM']
        book['position']={'address':self.c['address'],'pairAddress':self.c['pairAddress']}
        self.assertEqual(active.quality_admission(self.c,proof,NOW,self.books)['reason'],'quality_correlated_position')
        book['position']=None
        book['history']=[dict(address=self.c['address'],closed_at=NOW-1000,pnl_usd=-1,
                             entry_policy_version=active.TICKER_WARNING_VERSION)]
        before=copy.deepcopy(self.books)
        self.assertEqual(active.quality_admission(self.c,proof,NOW,self.books)['reason'],'quality_pool_loss_pause')
        self.assertEqual(active.performance(book,NOW)['trades'],0)
        self.assertEqual(active.performance(book,NOW)['legacy_closed_trades'],1)
        self.assertEqual(self.books,before)

    def test_off_outside_sized_quality_paper_and_runner_clears_switch(self):
        for name,value in [('NEO_ENGINE_MODE','LIVE'),('NEO_EXECUTION_MODE','LIVE'),
                           (active.ENV,'0'),(active.QUALITY_ENV,'0'),(active.SIZED_ENV,'0'),
                           (active.FLOW_SIZE_ENV,'true')]:
            with patch.dict(os.environ,{name:value}):
                self.assertFalse(active.flow_size_decoupled())
        self.assertNotIn(active.FLOW_SIZE_ENV,quality.isolated_environment(self.temp.name,{active.FLOW_SIZE_ENV:'1'}))

    def test_provider_delay_does_not_revive_stale_flow_at_commit(self):
        def delayed(coin):
            self.clock=NOW+13_000
            return {'status':'pass','mint':coin['address'],'pair':coin['pairAddress']}
        with patch.object(lab.price_integrity,'check',side_effect=delayed):
            lab.maybe_open([self.c],self.flow(self.c))
        self.assertTrue(all(not active.positions(b) for b in self.books.values()))
