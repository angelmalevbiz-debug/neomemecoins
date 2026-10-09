"""Synthetic regression fixtures are not a backtest or live profit evidence."""
import copy
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'backend'))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
import funded_active_paper as active
import strategy_lab as lab
import test_funded_active_paper as fixture
from audit_lab_outcomes import audit, summarize
from run_python_checks import isolated_environment

NOW = fixture.NOW


def strong_flows(rows):
    result = fixture.flows(rows)
    for value in result.values():
        value['verified_flow'].update(trades=8, unique_wallets=5)
    return result


class QualityTests(unittest.TestCase):
    def setUp(self):
        self.clock = NOW
        self.temp = tempfile.TemporaryDirectory(prefix='neo-quality-tests-')
        self.addCleanup(self.temp.cleanup)
        self.strategies = [s for s in lab.STRATEGIES if s['id'] in active.RULES]
        self.books = {s['id']:lab.empty_book(s) for s in self.strategies}
        for book in self.books.values():
            book.update(balance=1000, starting_balance=1000, history=[])
        self.c = fixture.coin()
        for p in (
            patch.dict(os.environ, {active.ENV:'1', active.QUALITY_ENV:'1',
                active.CAPACITY_TEST_ENV:'1', 'NEO_ENGINE_MODE':'PAPER','NEO_EXECUTION_MODE':'PAPER'}),
            patch.object(lab,'STATE',{'books':self.books}),
            patch.object(lab,'STRATEGIES',self.strategies),
            patch.object(lab,'now_ms',side_effect=lambda:self.clock),
            patch.object(lab,'DEFENSE',lab.entry_defense.DefensiveEntryLayer(
                registry_path=Path(self.temp.name)/'tickers.json')),
            patch.object(lab,'funded_defensive_decision',return_value={
                'allowed':True,'reasons':[],'log_only_flags':[]}),
            patch.object(lab.rug_guard,'check',side_effect=lambda c:{'status':'pass',
                'mint':c['address'],'pair':c['pairAddress'],'checked_at':self.clock}),
            patch.object(lab.price_integrity,'check',side_effect=lambda c:{'status':'pass',
                'mint':c['address'],'pair':c['pairAddress']}),
        ):
            p.start(); self.addCleanup(p.stop)

    def features(self):
        return {'verified_flow':strong_flows([self.c])[(self.c['address'],self.c['pairAddress'])]['verified_flow']}

    def test_quality_wins_over_inherited_capacity_flag_and_never_runs_live(self):
        self.assertTrue(active.quality_enabled())
        self.assertFalse(active.capacity_test_enabled())
        for name in ('NEO_ENGINE_MODE','NEO_EXECUTION_MODE'):
            with patch.dict(os.environ,{name:'LIVE'}):
                self.assertFalse(active.quality_enabled())
                self.assertFalse(active.capacity_test_enabled())

    def test_no_market_signal_or_unconfirmed_flow_does_not_force_fill(self):
        for row in [self.c, {**self.c,'priceChange':{'m5':-2,'h1':-2}}]:
            lab.maybe_open([row],{})
        self.assertTrue(all(not active.positions(b) for b in self.books.values()))

    def test_flow_strength_scales_to_order_size_and_rejects_malformed_values(self):
        self.assertTrue(active.quality_admission(self.c,self.features(),NOW,self.books)['allow'])
        for key,value in [('trades',5),('trades',6.5),('unique_wallets',3),('buy_usd',299),
                          ('sell_usd',450),('buy_usd',float('nan')),('unique_wallets',True)]:
            features=self.features();features['verified_flow'][key]=value
            self.assertEqual(active.quality_admission(self.c,features,NOW,self.books)['reason'],
                             'quality_buy_flow_too_small')

    def test_actual_loop_opens_only_one_copy_across_four_books(self):
        before=copy.deepcopy(self.books)
        lab.maybe_open([self.c],strong_flows([self.c]))
        positions=[p for b in self.books.values() for p in active.positions(b)]
        self.assertEqual(len(positions),1)
        p=positions[0]
        self.assertEqual(p['entry_policy_version'],active.QUALITY_VERSION)
        self.assertEqual(p['notional_usd'],100)
        self.assertEqual(p['exit_parameters'],active.exit_parameters(p['strategy_id']))
        self.assertLess(p['open_pnl_usd'],0)
        self.assertEqual(p['entry_cost_cap_pct'],1.5)
        for sid,b in self.books.items():
            for key in ('balance','starting_balance','history'):
                self.assertEqual(b[key],before[sid][key])

    def test_full_defense_and_safety_are_hard_not_shadow(self):
        for target,method,result in [(lab,'funded_defensive_decision',{'allowed':False,'reasons':['heat_turnover_5m']}),
                (lab.rug_guard,'check',{'status':'unknown'})]:
            with patch.object(target,method,return_value=result):
                lab.maybe_open([self.c],strong_flows([self.c]))
            self.assertTrue(all(not active.positions(b) for b in self.books.values()))

    def test_price_identity_and_cost_limit_cannot_be_bypassed(self):
        with patch.object(lab.price_integrity,'check',return_value={'status':'pass','mint':'wrong','pair':'wrong'}):
            lab.maybe_open([self.c],strong_flows([self.c]))
        self.assertTrue(all(not active.positions(b) for b in self.books.values()))
        original=lab.exit_execution
        def costly(*args,**kwargs):
            value=original(*args,**kwargs);value['net_proceeds_usd']-=5
            return value
        with patch.object(lab,'exit_execution',side_effect=costly):
            lab.maybe_open([self.c],strong_flows([self.c]))
        self.assertTrue(all(not active.positions(b) for b in self.books.values()))

    def test_daily_loss_from_old_test_is_not_reset_for_new_policy(self):
        for b in self.books.values():
            b['history']=[{'entry_policy_version':active.CAPACITY_TEST_VERSION,
                'pnl_usd':-60,'closed_at':NOW-1000,'opened_at':NOW-5000}]
        lab.maybe_open([self.c],strong_flows([self.c]))
        for b in self.books.values():
            self.assertFalse(active.positions(b))
            self.assertEqual(b['entry_diagnostics']['blocked_reason'],'funded_active_daily_loss_limit')
            stats=active.performance(b,NOW)
            self.assertEqual(stats['trades'],0)
            self.assertEqual(stats['legacy_closed_trades'],1)
            self.assertEqual(stats['legacy_net_pnl_usd'],-60)

    def test_mint_mutex_and_cohort_loss_pause_include_other_book_and_old_policy(self):
        b=self.books['MOMENTUM']
        b['position']={'address':self.c['address'],'pairAddress':'different'}
        self.assertEqual(active.quality_admission(self.c,self.features(),NOW,self.books)['reason'],
                         'quality_correlated_position')
        b['position']=None
        b['history']=[{'pairAddress':self.c['pairAddress'],'closed_at':NOW-1000,'pnl_usd':-1}]
        self.assertEqual(active.quality_admission(self.c,self.features(),NOW,self.books)['reason'],
                         'quality_pool_loss_pause')
        self.assertTrue(active.quality_admission(self.c,self.features(),NOW+30*60_000,self.books)['allow'])

    def test_commit_rechecks_quality_after_provider_work(self):
        original=active.quality_admission
        calls=[0]
        def changed(*args,**kwargs):
            calls[0]+=1
            return original(*args,**kwargs) if calls[0]%2 else {
                'allow':False,'reason':'quality_correlated_position'}
        with patch.object(active,'quality_admission',side_effect=changed):
            lab.maybe_open([self.c],strong_flows([self.c]))
        self.assertTrue(all(not active.positions(b) for b in self.books.values()))

    def test_new_dollar_exits_do_not_treat_green_two_percent_as_closed_win(self):
        p={'notional_usd':100,'exit_parameters':active.quality_exit_parameters(),'peak_net_pct':4}
        for net in (2,4,29.999,-4.999):
            self.assertIsNone(active.exit_reason(p,net,240))
        self.assertEqual(active.exit_reason(p,30,1),'ADAPTIVE_TAKE_PROFIT_NET_USD')
        self.assertEqual(active.exit_reason(p,-5,1),'ADAPTIVE_STOP_NET_USD')
        self.assertEqual(active.exit_reason(p,-9,1),'ADAPTIVE_STOP_NET_USD')

    def test_actual_gap_close_books_full_loss_not_the_new_stop_threshold(self):
        lab.maybe_open([self.c],strong_flows([self.c]))
        b=next(b for b in self.books.values() if active.positions(b))
        self.clock=NOW+5000
        # Real engine mark/close logic, synthetic fixture price (not live data).
        fallen={**self.c,'updatedAt':self.clock,'priceUsd':.009,'priceNative':.009/150}
        with patch.object(lab.POSITION_MARK_FEED,'resolve',return_value=fallen):
            lab.update_positions({},[fallen])
        self.assertFalse(active.positions(b))
        self.assertEqual(b['history'][0]['exit_reason'],'ADAPTIVE_STOP_NET_USD')
        self.assertLess(b['history'][0]['pnl_usd'],-10)
        self.assertAlmostEqual(b['balance'],1000+b['history'][0]['pnl_usd'],places=4)

    def test_new_policy_does_not_change_any_old_lot_exit_or_ledger(self):
        b=self.books['EARLY']
        p={'notional_usd':100,'strategy_id':'EARLY','capacity_test':True,
            'entry_policy_version':active.CAPACITY_TEST_VERSION,
            'exit_parameters':active.capacity_exit_parameters(),'quantity':123,'opened_at':NOW-900_000}
        active.attach(b,p);before=copy.deepcopy(self.books)
        self.assertEqual(active.apply_capacity_exit_policy(self.books,NOW),[])
        self.assertEqual(self.books,before)
        self.assertIsNone(active.exit_reason(p,-8,100))
        self.assertEqual(active.exit_reason(p,-11,100),'CAPACITY_TEST_STOP_NET_USD')

    def test_test_runner_clears_quality_flag(self):
        self.assertNotIn(active.QUALITY_ENV,isolated_environment(self.temp.name,{active.QUALITY_ENV:'1'}))


class OutcomeAuditTests(unittest.TestCase):
    def test_no_winners_does_not_invent_a_win_rate_or_profit_factor(self):
        self.assertIsNone(summarize([])['win_rate_pct'])
        self.assertEqual(summarize([{'pnl_usd':-7}])['profit_factor'],0)
        self.assertEqual(summarize([{'pnl_usd':2},{'pnl_usd':-7}])['win_rate_pct'],50)

    def test_open_marks_aliases_and_cohorts_stay_separate(self):
        p={'trade_no':1,'opened_at':1,'address':'mint','pairAddress':'pool','open_pnl_usd':2}
        old={'entry_policy_version':'OLD','pnl_usd':-7}
        snapshot={'books':{'EARLY':{'position':p,'positions':[copy.deepcopy(p)],'history':[old]}}}
        before=copy.deepcopy(snapshot);result=audit(snapshot)
        self.assertEqual(result['open_position_count'],1)
        self.assertEqual(result['primary_total']['wins'],0)
        self.assertEqual(result['primary_total']['net_usd'],-7)
        self.assertEqual(result['capacity_test']['closes'],0)
        self.assertEqual(snapshot,before)
