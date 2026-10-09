"""Synthetic mechanics only; no claimed win rate or real fills."""
import copy
import os
from unittest.mock import patch

import test_paper_quality as quality
from audit_lab_outcomes import cohort_audit

active, lab, NOW = quality.active, quality.lab, quality.NOW


class SizedPolicyTests(quality.unittest.TestCase):
    def setUp(self):
        quality.QualityTests.setUp(self)
        self.c=quality.fixture.coin(liquidityUsd=1_000_000,marketCap=20_000_000)
        flag = patch.dict(os.environ, {active.SIZED_ENV:'1'})
        flag.start(); self.addCleanup(flag.stop)

    def flow(self, coin):
        flows = quality.strong_flows([coin])
        flows[(coin['address'],coin['pairAddress'])]['verified_flow'].update(buy_usd=1500,sell_usd=100)
        return flows

    def test_actual_entry_loop_fixed_250_for_each_book_with_proportional_frozen_exits(self):
        for sid, book in self.books.items():
            strategy = next(s for s in self.strategies if s['id']==sid)
            coin = quality.fixture.coin(list(self.books).index(sid),liquidityUsd=1_000_000,marketCap=20_000_000)
            with patch.object(lab,'STATE',{'books':{sid:book}}), patch.object(lab,'STRATEGIES',[strategy]):
                lab.maybe_open([coin],self.flow(coin))
            self.assertTrue(active.positions(book), (sid,book.get('entry_diagnostics')))
            p = active.positions(book)[0]
            self.assertEqual(p['notional_usd'],250)
            self.assertEqual(p['entry_policy_version'],active.SIZED_VERSION)
            self.assertEqual(p['exit_parameters'],active.exit_parameters(sid,250))
            self.assertEqual(p['stop_loss_net_pct'],5)
            self.assertEqual(p['exit_parameters']['stop_loss_net_usd'],12.5)
            self.assertLess(p['open_pnl_usd'],0)
            self.assertEqual(book['balance'],1000)
            self.assertFalse(p.get('capacity_test'))

    def test_soft_caps_removed_only_for_momentum_precision_without_erasing_losses(self):
        for sid,book in self.books.items():
            book['history']=[dict(entry_policy_version=active.CAPACITY_TEST_VERSION,
                opened_at=NOW-5000,closed_at=NOW-1000,pnl_usd=-2) for i in range(60)]
            before=copy.deepcopy(book)
            result=active.capacity(book,NOW)
            self.assertEqual(result['daily_net_usd'],-120)
            self.assertEqual(book,before)
            if sid in active.UNCAPPED_BOOKS:
                self.assertIsNone(result['blocked_reason'])
                for field in ('max_positions','daily_loss_limit_usd','target_orders_per_hour'):
                    self.assertIsNone(result[field])
                self.assertEqual(result['available_exposure_usd'],1000)
            else:
                self.assertEqual(result['blocked_reason'],'funded_active_daily_loss_limit')
                self.assertEqual(result['available_exposure_usd'],500)
            self.assertEqual(active.performance(book,NOW)['legacy_net_pnl_usd'],-120)

    def test_no_hidden_four_slot_cap_but_no_borrowing_or_stale_mark_bypass(self):
        book=self.books['MOMENTUM']; book['balance']=2500
        book['positions']=[dict(remaining_cost_basis_usd=250.02,quote_status='fresh',trade_no=i)
                           for i in range(5)]
        self.assertIsNone(active.capacity(book,NOW)['blocked_reason'])
        book['balance']=1500
        self.assertEqual(active.capacity(book,NOW)['blocked_reason'],'funded_active_cash_unavailable')
        book['positions'][0]['quote_status']='stale'
        self.assertEqual(active.capacity(book,NOW)['blocked_reason'],'funded_active_marks_unavailable')

    def test_old_lots_cash_funding_and_history_are_not_resized_or_rewritten(self):
        book=self.books['ULTRA_PRECISION']
        old=dict(strategy_id=book['id'],notional_usd=100,quantity=5,
                 opened_at=NOW-10000,entry_policy_version=active.QUALITY_VERSION,
                 exit_parameters=lab.active_paper.horizons.parameters(book['id']),
                 execution_mode=lab.EXECUTION_MODEL_VERSION)
        active.attach(book,old); before=copy.deepcopy(self.books)
        self.assertEqual(active.apply_horizon_exit_policy(self.books,NOW),[])
        self.assertEqual(active.apply_adaptive_exit_policy(self.books,NOW),[])
        self.assertEqual(self.books,before)
        self.assertEqual(active.performance(book,NOW)['legacy_open_positions'],1)

    def test_loss_run_pause_no_longer_stops_selected_books_but_bad_flow_still_does(self):
        book=self.books['MOMENTUM']; strategy=next(s for s in self.strategies if s['id']==book['id'])
        with patch.object(lab,'STATE',{'books':{book['id']:book}}), patch.object(lab,'STRATEGIES',[strategy]), \
             patch.object(lab,'promoted_pause_remaining_ms',return_value=120000):
            lab.maybe_open([self.c],quality.strong_flows([self.c]))
            self.assertEqual(active.positions(book),[])  # $500 buys insufficient for a $250 order.
            lab.maybe_open([self.c],self.flow(self.c))
        self.assertEqual(len(active.positions(book)),1,book.get('entry_diagnostics'))

    def test_sizing_requires_both_paper_modes_and_quality_flag(self):
        self.assertEqual(active.entry_notional(),250)
        for name,value in [('NEO_ENGINE_MODE','LIVE'),('NEO_EXECUTION_MODE','LIVE'),
                           (active.QUALITY_ENV,'0'),(active.ENV,'0'),(active.SIZED_ENV,'0')]:
            with patch.dict(os.environ,{name:value}):
                self.assertFalse(active.sized_enabled())
                self.assertFalse(active.soft_limits_removed(self.books['MOMENTUM']))
                self.assertEqual(active.entry_notional(),100)

    def test_full_cost_and_data_checks_remain_hard_and_cash_never_backoffs(self):
        for book in self.books.values():book['balance']=249
        lab.maybe_open([self.c],self.flow(self.c))
        self.assertTrue(all(not active.positions(b) for b in self.books.values()))
        for book in self.books.values():book['balance']=1000
        with patch.object(lab,'funded_defensive_decision',return_value={'allowed':False,'reasons':['heat_turnover_5m']}):
            lab.maybe_open([self.c],self.flow(self.c))
        self.assertTrue(all(not active.positions(b) for b in self.books.values()))
        with patch.object(lab.price_integrity,'check',return_value={'status':'pass','mint':'wrong','pair':'wrong'}):
            lab.maybe_open([self.c],self.flow(self.c))
        self.assertTrue(all(not active.positions(b) for b in self.books.values()))

    def test_proportional_protection_exit_gap_not_clamped_and_old_dollars_unchanged(self):
        p=dict(notional_usd=250,exit_parameters=active.exit_parameters('MOMENTUM'))
        active.observe_profit_protection(p,4,NOW)
        self.assertEqual(p['profit_protection']['peak_net_usd'],10)
        self.assertEqual(p['profit_protection']['floor_net_usd'],8)
        self.assertEqual(active.exit_reason(p,3,0),'ADAPTIVE_PROFIT_PROTECTION_NET_USD')
        self.assertEqual(active.exit_reason(p,-9,0),'ADAPTIVE_STOP_NET_USD')
        self.assertEqual(lab.active_paper.horizons.parameters('MOMENTUM',250)['stop_loss_net_usd'],5)

    def test_runner_clears_sizing_flag(self):
        self.assertNotIn(active.SIZED_ENV,quality.isolated_environment(self.temp.name,{active.SIZED_ENV:'1'}))

    def test_malformed_capital_or_exposure_cannot_create_credit(self):
        book=self.books['MOMENTUM']
        for value in (True,float('nan'),float('inf'),-1):
            book['balance']=value
            self.assertEqual(active.capacity(book,NOW)['blocked_reason'],'funded_active_capital_invalid')
            book['balance']=1000
            book['positions']=[dict(remaining_cost_basis_usd=value)]
            self.assertEqual(active.capacity(book,NOW)['blocked_reason'],'funded_active_capital_invalid')
            book['positions']=[]

    def test_same_fee_floor_can_pass_100_but_fail_250_due_to_impact(self):
        coin=quality.fixture.coin()  # Genuine model inputs; synthetic market.
        from paper_market_feasibility import modeled_roundtrip
        self.assertLess(-modeled_roundtrip(coin,100)['initial_pnl_pct'],1.5)
        self.assertGreater(-modeled_roundtrip(coin,250)['initial_pnl_pct'],1.5)
        lab.maybe_open([coin],self.flow(coin))
        self.assertTrue(all(not active.positions(b) for b in self.books.values()))

    def test_three_actual_full_entries_fit_903_cash_but_fourth_cannot_borrow(self):
        book=self.books['MOMENTUM']; book['balance']=903
        strategy=next(s for s in self.strategies if s['id']==book['id'])
        with patch.object(lab,'STATE',{'books':{book['id']:book}}), patch.object(lab,'STRATEGIES',[strategy]):
            for index in range(4):
                coin=quality.fixture.coin(index,liquidityUsd=1_000_000,marketCap=20_000_000)
                lab.maybe_open([coin],self.flow(coin))
        self.assertEqual(len(active.positions(book)),3)
        self.assertTrue(all(p['notional_usd']==250 for p in active.positions(book)))
        self.assertLess(active.capacity(book,NOW)['exposure_usd'],903)
        self.assertEqual(book['entry_diagnostics']['blocked_reason'],'funded_active_cash_unavailable')

    def test_scheduler_plans_at_250_instead_of_accepting_fee_floor_only(self):
        import tape_pool_scheduler as scheduler
        costly=quality.fixture.coin()
        with patch.object(scheduler.TapePoolScheduler,'defensive_entry_decision',
                          return_value=lab.entry_defense.pass_decision('TEST')), \
             patch.object(scheduler.winner_ensemble,'market_candidates',return_value=[]):
            _, report=scheduler.TapePoolScheduler().select({'feed':[costly]},now=NOW,max_tracked=1)
        self.assertEqual(report['estimated_feasible_market_candidates'],0)
        self.assertEqual(report['estimated_fixed_cost_over_budget'],1)


class CohortAuditTests(quality.unittest.TestCase):
    def test_fee_erosion_is_not_mislabeled_as_fee_only_or_alternative_fills(self):
        rows=[dict(entry_price=1,exit_price=1.01,notional_usd=100,pnl_usd=-1,
                   entry_policy_version='OLD',exit_reason='TIMEOUT',capacity_test=True,
                   capacity_test_shadow={'strategy_market_signal':False},entry_dex_fee_usd=.5,
                   entry_network_fee_usd=.01,exit_dex_fee_usd=.5,exit_network_fee_usd=.01)]
        before=copy.deepcopy(rows); result=cohort_audit(rows)
        self.assertEqual(result['forced_without_market_signal'],1)
        self.assertEqual(result['recorded_dex_and_network_fees_usd'],1.02)
        self.assertEqual(result['gross_green_but_net_nonpositive'],1)
        self.assertEqual(result['gross_quote_move_benchmark_usd'],1)
        self.assertEqual(result['by_entry_policy']['OLD']['net_usd'],-1)
        self.assertEqual(rows,before)

    def test_partial_exits_missing_prices_and_missing_fees_are_not_invented(self):
        result=cohort_audit([dict(pnl_usd=1),dict(entry_price=1,exit_price=2,notional_usd=100,
                                                pnl_usd=20,partial_exits=[{}])])
        self.assertEqual(result['comparable_unpartial_closes'],0)
        self.assertEqual(result['fee_complete_closes'],0)
