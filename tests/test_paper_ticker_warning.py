"""Owner-scoped prospective PAPER mechanics, not profit or market evidence."""
import copy
import os
import unittest
from unittest.mock import patch

import test_funded_active_paper as fixture
from tape_pool_scheduler import TapePoolScheduler

active, lab, NOW = fixture.active, fixture.lab, fixture.NOW
REAL_FUNDED_DECISION = lab.funded_defensive_decision


class TickerWarningTests(unittest.TestCase):
    def setUp(self):
        fixture.ActivePolicyTests.setUp(self)
        flags = patch.dict(os.environ, {active.QUALITY_ENV:'1', active.SIZED_ENV:'1',
            active.TICKER_WARNING_ENV:'1', 'NEO_ENGINE_MODE':'PAPER', 'NEO_EXECUTION_MODE':'PAPER'})
        flags.start(); self.addCleanup(flags.stop)
        self.layer.registry.reusers.return_value = [('other-mint', 'other-pool')]
        self.c = fixture.coin(liquidityUsd=1_000_000, marketCap=20_000_000)

    def make_book(self, sid):
        book = lab.empty_book({'id':sid, 'name':sid})
        book.update(portfolio_group='PROMOTED_PAPER', balance=1000, starting_balance=1000)
        return book

    def flow(self):
        result = fixture.flows([self.c])
        result[(self.c['address'], self.c['pairAddress'])]['verified_flow'].update(
            trades=8, unique_wallets=5, buy_usd=1500, sell_usd=100)
        return result

    def decide(self, sid, coin=None, blocked=None):
        return REAL_FUNDED_DECISION(self.make_book(sid), coin or self.c, NOW, blocked_pools=blocked or {})

    def test_warning_scope_and_raw_structural_finding_are_preserved(self):
        for sid in active.RULES:
            decision = self.decide(sid)
            warning = sid in active.TICKER_WARNING_BOOKS
            self.assertEqual(decision['allowed'], warning, (sid, decision))
            self.assertEqual('rug_ticker_reuse' in decision['log_only_flags'], warning)
            self.assertEqual('rug_ticker_reuse' in decision['reasons'], not warning)
            self.assertTrue(decision['structural_rug_guard']['blocked'])
            self.assertEqual(decision['structural_rug_guard']['ticker_reused_by'], 1)
            self.assertEqual(active.reporting_version(sid),
                active.TICKER_WARNING_VERSION if warning else active.SIZED_VERSION)
        self.assertFalse(self.layer.evaluate(self.c, NOW)['allowed'])  # main/personal default
        other = self.make_book('MOMENTUM'); other['portfolio_group'] = 'OTHER'
        self.assertFalse(REAL_FUNDED_DECISION(other,self.c,NOW,blocked_pools={})['allowed'])

    def test_opt_in_requires_all_paper_flags_and_default_layer_does_not_inherit_it(self):
        for key in (active.ENV, active.QUALITY_ENV, active.SIZED_ENV, active.TICKER_WARNING_ENV):
            with patch.dict(os.environ, {key:'0'}):
                self.assertFalse(active.ticker_warning_enabled('MOMENTUM'))
        for key in ('NEO_ENGINE_MODE', 'NEO_EXECUTION_MODE'):
            with patch.dict(os.environ, {key:'LIVE'}):
                self.assertFalse(active.ticker_warning_enabled('MOMENTUM'))
        for value in (False, 'true', '1', 1):
            decision = self.layer.evaluate(self.c,NOW,ticker_reuse_log_only=value,
                structural_parameters=active.structural_parameters('MOMENTUM'))
            self.assertFalse(decision['allowed'], value)
        from run_python_checks import isolated_environment
        self.assertNotIn(active.TICKER_WARNING_ENV, isolated_environment('unused',
            {active.TICKER_WARNING_ENV:'1'}))

    def test_no_other_structural_veto_is_downgraded(self):
        cases = [('rug_lp_pullable', {**self.c,'marketCap':500_000}),
                 ('rug_young_pool', {**self.c,'pairCreatedAt':NOW-60_000}),
                 ('rug_fake_market_cap', {**self.c,'marketCap':100_000_000}),
                 ('rug_input_unknown', {**self.c,'address':None})]
        for reason, coin in cases:
            decision = self.decide('MOMENTUM', coin)
            self.assertFalse(decision['allowed'], decision)
            self.assertIn(reason, decision['reasons'])
        self.layer.registry.reusers.return_value = []
        self.layer.registry.coverage_ms.return_value = 30*60_000
        decision = self.decide('PRECISION')
        self.assertFalse(decision['allowed'])
        self.assertIn('rug_ticker_registry_warming', decision['reasons'])

    def test_heat_loss_memory_and_errors_remain_hard(self):
        heat = {'vetoed':True, 'log_only':False, 'reasons':['heat_buy_share_5m'], 'metrics':{}}
        with patch.object(lab.entry_defense.heat_veto,'evaluate',return_value=heat):
            decision = self.decide('MOMENTUM')
        self.assertFalse(decision['allowed'])
        self.assertEqual(decision['reasons'], ['heat_buy_share_5m'])
        losses = [dict(address=self.c['address'],pairAddress=self.c['pairAddress'],pnl_usd=-1,
                       closed_at=NOW-i*1000) for i in (1,2)]
        decision = self.decide('PRECISION', blocked=lab.pool_loss_memory.index(losses,NOW))
        self.assertFalse(decision['allowed'])
        self.assertIn('pool_loss_cooldown',decision['reasons'])
        self.layer.registry.reusers.side_effect = RuntimeError('fixture')
        self.assertEqual(self.decide('MOMENTUM')['reasons'],['defensive_entry_error'])

    def test_actual_loop_opens_only_scoped_books_with_frozen_250_warning_receipt(self):
        for sid in active.RULES:
            book = self.make_book(sid)
            book['history'] = [dict(entry_policy_version=active.SIZED_VERSION,pnl_usd=-2,
                address='old-mint',pairAddress='old-pool',closed_at=NOW-5000)]
            before = copy.deepcopy(book)
            with patch.object(lab,'STATE',{'books':{sid:book}}), \
                 patch.object(lab,'STRATEGIES',[{'id':sid,'name':sid}]), \
                 patch.object(lab,'funded_defensive_decision',side_effect=REAL_FUNDED_DECISION):
                lab.maybe_open([self.c],self.flow())
            self.assertEqual(bool(active.positions(book)),sid in active.TICKER_WARNING_BOOKS,
                             (sid,book.get('entry_diagnostics')))
            self.assertEqual(book['history'],before['history'])
            self.assertEqual(book['balance'],before['balance'])
            if active.positions(book):
                p=active.positions(book)[0]
                self.assertEqual(p['entry_policy_version'],active.TICKER_WARNING_VERSION)
                self.assertEqual(p['notional_usd'],250)
                self.assertEqual(p['exit_parameters'],active.exit_parameters(sid,250))
                self.assertEqual(p['defensive_entry']['log_only_flags'],['rug_ticker_reuse'])
                self.assertTrue(p['defensive_entry']['ticker_reuse_log_only'])
                self.assertEqual(active.performance(book,NOW)['trades'],0)
                self.assertEqual(active.performance(book,NOW)['legacy_closed_trades'],1)
                self.assertEqual(active.performance(book,NOW)['orders_last_60m'],1)

    def test_warning_does_not_bypass_flow_cost_cash_price_or_safety_in_actual_loop(self):
        cases = [('flow', {}, None, None),
                 ('price',self.flow(),lab.price_integrity,{'status':'pass','mint':'wrong','pair':'wrong'}),
                 ('safety',self.flow(),lab.rug_guard,{'status':'fail'}),
                 ('cost',self.flow(),None,None), ('cash',self.flow(),None,None)]
        for name, flow, target, value in cases:
            book=self.make_book('MOMENTUM')
            coin={**self.c,'liquidityUsd':100_000} if name=='cost' else self.c
            if name=='cash':book['balance']=249
            with patch.object(lab,'STATE',{'books':{'MOMENTUM':book}}), \
                 patch.object(lab,'STRATEGIES',[{'id':'MOMENTUM','name':'Momentum'}]), \
                 patch.object(lab,'funded_defensive_decision',side_effect=REAL_FUNDED_DECISION):
                if target:
                    with patch.object(target,'check',return_value=value):lab.maybe_open([coin],flow)
                else:lab.maybe_open([coin],flow)
            self.assertEqual(active.positions(book),[],(name,book.get('entry_diagnostics')))

    def test_commit_rechecks_heat_after_preflight_warning(self):
        book=self.make_book('MOMENTUM')
        cool={'vetoed':False,'log_only':False,'reasons':[],'metrics':{}}
        hot={'vetoed':True,'log_only':False,'reasons':['heat_return_5m_surge'],'metrics':{}}
        with patch.object(lab,'STATE',{'books':{'MOMENTUM':book}}), \
             patch.object(lab,'STRATEGIES',[{'id':'MOMENTUM','name':'Momentum'}]), \
             patch.object(lab,'funded_defensive_decision',side_effect=REAL_FUNDED_DECISION), \
             patch.object(lab.entry_defense.heat_veto,'evaluate',side_effect=[cool,hot]):
            lab.maybe_open([self.c],self.flow())
        self.assertEqual(active.positions(book),[])
        self.assertEqual(book['entry_diagnostics']['blocked_reason'],'heat_return_5m_surge')
        self.assertEqual(book['entry_diagnostics']['commit_recheck_rejected'],1)

    def test_scheduler_warning_is_not_reblocked_as_heat_and_early_only_is_not_exempt(self):
        scheduler=TapePoolScheduler();scheduler.defense=self.layer
        decision=scheduler.defensive_entry_decision(self.c,NOW)
        self.assertTrue(decision['allowed'],decision)
        self.assertIn('rug_ticker_reuse',decision['log_only_flags'])
        early_only={**self.c,'liquidityUsd':80_000,'marketCap':1_000_000}
        self.assertTrue(active.matches('EARLY',early_only))
        self.assertFalse(active.matches('MOMENTUM',early_only))
        self.assertFalse(scheduler.defensive_entry_decision(early_only,NOW)['allowed'])
        heat={'vetoed':False,'log_only':True,'reasons':['heat_return_5m_surge'],'metrics':{}}
        with patch.object(lab.entry_defense.heat_veto,'evaluate',return_value=heat):
            withheld=scheduler.defensive_entry_decision(self.c,NOW)
            self.assertFalse(withheld['allowed'])
            self.assertIn('rug_ticker_reuse',withheld['log_only_flags'])
            leased=scheduler.defensive_entry_decision(self.c,NOW,leased=True)
            self.assertTrue(leased['allowed'])
            self.assertIn('rug_ticker_reuse',leased['log_only_flags'])

    def test_cohort_reporting_and_default_fallback_do_not_reclassify_history(self):
        book=self.make_book('MOMENTUM')
        book['history']=[dict(entry_policy_version=v,pnl_usd=p,closed_at=NOW-1)
            for v,p in ((active.SIZED_VERSION,-2),(active.TICKER_WARNING_VERSION,3))]
        before=copy.deepcopy(book)
        self.assertEqual(active.current_trades(book),[book['history'][1]])
        self.assertEqual(active.performance(book,NOW)['net_pnl_usd'],3)
        with patch.dict(os.environ,{active.TICKER_WARNING_ENV:'0'}):
            self.assertEqual(active.current_trades(book),[book['history'][0]])
            self.assertFalse(self.decide('MOMENTUM')['allowed'])
        self.assertEqual(book,before)


if __name__=='__main__':
    unittest.main()
