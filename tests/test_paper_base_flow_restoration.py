"""Synthetic regressions of admission mechanics, never evidence of profit."""
import copy
import os
from unittest.mock import patch

import test_paper_flow_size_repair as repaired

active, lab, quality, NOW = repaired.active, repaired.lab, repaired.quality, repaired.NOW


class BaseFlowRestorationTests(quality.unittest.TestCase):
    def setUp(self):
        repaired.FlowSizeRepairTests.setUp(self)
        flag = patch.dict(os.environ, {active.BASE_FLOW_ENV:'1'})
        flag.start(); self.addCleanup(flag.stop)

    def flow(self, coin, **updates):
        return repaired.FlowSizeRepairTests.flow(self, coin,
            **{'trades':3, 'unique_wallets':2, **updates})

    def test_restores_baseline_flow_and_versions_it_separately(self):
        limits = active.quality_flow_requirements()
        self.assertEqual(limits, dict(minimum_confirmed_30s_trades=3,
            minimum_confirmed_30s_wallets=2, minimum_confirmed_30s_buy_usd=0,
            minimum_confirmed_30s_net_buy_usd=0, minimum_buy_sell_usd_ratio=1.2))
        self.assertEqual(lab.promoted_guard.MIN_FLOW_TRADES, 3)
        self.assertEqual(lab.promoted_guard.MIN_FLOW_WALLETS, 2)
        self.assertTrue(active.config()['base_flow_repair']['enabled'])
        self.assertFalse(active.config()['base_flow_repair']['capacity_test_bypass_restored'])
        self.assertFalse(active.capacity_test_enabled())
        for sid in active.RULES:
            self.assertEqual(active.reporting_version(sid), active.BASE_FLOW_VERSION)
            self.assertEqual(active.entry_notional(), 250)
            self.assertEqual(active.entry_cost_limit(sid), 1.5)
            self.assertEqual(active.ticker_warning_enabled(sid), sid in active.TICKER_WARNING_BOOKS)
        proof = {'verified_flow':self.flow(self.c)[(self.c['address'],self.c['pairAddress'])]['verified_flow']}
        with patch.dict(os.environ, {active.BASE_FLOW_ENV:'0'}):
            self.assertEqual(active.reporting_version(), active.FLOW_SIZE_VERSION)
            self.assertEqual(active.quality_flow_requirements()['minimum_confirmed_30s_trades'], 6)
            self.assertFalse(active.quality_admission(self.c, proof, NOW, self.books)['allow'])

    def test_real_entry_loop_separates_baseline_signal_from_full_250_cost_for_each_book(self):
        for index, (sid, book) in enumerate(self.books.items()):
            strategy = next(s for s in self.strategies if s['id']==sid)
            coin = quality.fixture.coin(index, liquidityUsd=1_000_000, marketCap=20_000_000)
            before = copy.deepcopy(book)
            with patch.object(lab,'STATE',{'books':{sid:book}}), patch.object(lab,'STRATEGIES',[strategy]):
                lab.maybe_open([coin],self.flow(coin,buy_usd=12,sell_usd=9))
            self.assertEqual(len(active.positions(book)), 1, (sid,book.get('entry_diagnostics')))
            p = active.positions(book)[0]
            self.assertEqual(p['notional_usd'],250)
            self.assertEqual(p['entry_policy_version'],active.BASE_FLOW_VERSION)
            self.assertEqual(p['entry_quality_flow_requirements'],active.quality_flow_requirements())
            self.assertEqual(p['verified_entry_flow']['trades'],3)
            self.assertEqual(p['verified_entry_flow']['unique_wallets'],2)
            self.assertEqual(p['entry_cost_cap_pct'],1.5)
            self.assertEqual(p['exit_parameters'],active.exit_parameters(sid,250))
            self.assertFalse(p.get('capacity_test'))
            self.assertTrue(active.is_active_position(p))
            self.assertEqual(active.performance(book,NOW)['orders_last_60m'],1)
            self.assertTrue(active.performance(book,NOW)['base_flow_restored'])
            self.assertLess(p['open_pnl_usd'],0)
            for field in ('balance','starting_balance','history','funding_events'):
                self.assertEqual(book.get(field),before.get(field))

    def test_weak_forged_incomplete_or_stale_flow_never_fills(self):
        for field,value in [('trades',2),('unique_wallets',1),('trades',3.1),
                ('unique_wallets',True),('buy_usd',0),('sell_usd',350),
                ('buy_usd',float('nan')),('window_ms',60_000),('coverage_status','DEGRADED'),
                ('source','DEXSCREENER_ESTIMATE'),('address','wrong'),('pairAddress','wrong'),
                ('latest_event_at',NOW-12_001),('window_at',NOW+1),('available_at',NOW+1)]:
            flows = self.flow(self.c, **{field:value})
            lab.maybe_open([self.c],flows)
            self.assertTrue(all(not active.positions(b) for b in self.books.values()),field)
        lab.maybe_open([self.c],{})
        self.assertTrue(all(not active.positions(b) for b in self.books.values()))

    def test_defense_safety_price_cost_and_cash_still_block(self):
        repaired.FlowSizeRepairTests.test_defense_price_safety_cost_and_real_cash_are_still_hard(self)

    def test_cohort_mutex_and_old_loss_pause_still_apply(self):
        repaired.FlowSizeRepairTests.test_cohort_mutex_and_old_losing_pool_pause_are_not_relaxed(self)

    def test_provider_delay_does_not_revive_old_proof(self):
        repaired.FlowSizeRepairTests.test_provider_delay_does_not_revive_stale_flow_at_commit(self)

    def test_paper_only_literal_opt_in_and_runner_isolation(self):
        for name,value in [('NEO_ENGINE_MODE','LIVE'),('NEO_EXECUTION_MODE','LIVE'),
                (active.ENV,'0'),(active.QUALITY_ENV,'0'),(active.SIZED_ENV,'0'),
                (active.FLOW_SIZE_ENV,'0'),(active.BASE_FLOW_ENV,'true')]:
            with patch.dict(os.environ,{name:value}):
                self.assertFalse(active.base_flow_restored(),name)
        self.assertNotIn(active.BASE_FLOW_ENV,
            quality.isolated_environment(self.temp.name,{active.BASE_FLOW_ENV:'1'}))

    def test_existing_v8_lot_and_history_not_rewritten_or_counted_as_new_v9(self):
        book = self.books['MOMENTUM']
        old = dict(strategy_id=book['id'],notional_usd=250,quantity=5,opened_at=NOW-10_000,
            entry_policy_version=active.FLOW_SIZE_VERSION,
            exit_parameters=active.exit_parameters(book['id'],250),
            execution_mode=lab.EXECUTION_MODEL_VERSION,
            entry_quality_flow_requirements={'minimum_confirmed_30s_trades':6,
                'minimum_confirmed_30s_wallets':4})
        active.attach(book,old)
        book['history']=[dict(entry_policy_version=active.FLOW_SIZE_VERSION,
            opened_at=NOW-20_000,closed_at=NOW-1000,pnl_usd=2)]
        before = copy.deepcopy(self.books)
        self.assertEqual(active.apply_horizon_exit_policy(self.books,NOW),[])
        self.assertEqual(active.apply_adaptive_exit_policy(self.books,NOW),[])
        self.assertEqual(self.books,before)
        stats = active.performance(book,NOW)
        self.assertEqual(stats['trades'],0)
        self.assertEqual(stats['orders_last_60m'],0)
        self.assertEqual(stats['legacy_open_positions'],1)
        self.assertEqual(stats['legacy_closed_trades'],1)
