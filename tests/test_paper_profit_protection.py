"""Prospective exit mechanics on synthetic fixtures, NOT a profitable backtest."""
import copy
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import test_paper_quality as quality
from lab_dashboard_projection import compact_strategy_lab

active, lab, NOW = quality.active, quality.lab, quality.NOW


class ProtectionTests(unittest.TestCase):
    setUp = quality.QualityTests.setUp

    def position(self, notional=100):
        return dict(notional_usd=notional, peak_net_pct=90,
                    exit_parameters=active.quality_exit_parameters(notional))

    def observe(self, p, net, delta=0):
        active.observe_profit_protection(p, net, NOW+delta)
        return active.exit_reason(p, net, 1_000_000)

    def test_arm_and_unrounded_boundary(self):
        p=self.position()
        self.assertIsNone(self.observe(p,3.999999))
        self.assertFalse(p['profit_protection']['armed'])
        self.assertIsNone(self.observe(p,4,1))
        self.assertEqual(p['profit_protection']['floor_net_usd'],3)
        self.assertIsNone(self.observe(p,3.000001,2))
        self.assertEqual(self.observe(p,3,3),'ADAPTIVE_PROFIT_PROTECTION_NET_USD')

    def test_ten_to_eight_and_fifteen_to_twelve(self):
        for peak,floor in [(5,4),(10,8),(15,12),(25,20)]:
            p=self.position()
            self.assertIsNone(self.observe(p,peak))
            self.assertEqual(p['profit_protection']['floor_net_usd'],floor)
            self.assertIsNone(self.observe(p,floor+.01,1))
            self.assertEqual(self.observe(p,floor,2),'ADAPTIVE_PROFIT_PROTECTION_NET_USD')

    def test_peak_and_floor_only_rise_across_json_restart(self):
        p=self.position();self.observe(p,10)
        self.observe(p,9,1)
        restarted=json.loads(json.dumps(p));self.observe(restarted,14,2)
        self.assertEqual(restarted['profit_protection']['floor_net_usd'],11.2)
        self.assertEqual(self.observe(restarted,11,3),'ADAPTIVE_PROFIT_PROTECTION_NET_USD')

    def test_dollar_not_percentage_of_larger_notional(self):
        p=self.position(200)
        self.observe(p,5)  # $10 net
        self.assertEqual(p['profit_protection']['floor_net_usd'],8)
        self.assertEqual(self.observe(p,4,1),'ADAPTIVE_PROFIT_PROTECTION_NET_USD')
        self.assertEqual(active.exit_reason(p,-2.5,0),'ADAPTIVE_STOP_NET_USD')

    def test_no_timer_or_retroactive_historical_peak(self):
        p=self.position()
        self.assertIsNone(self.observe(p,-2))
        self.assertEqual(p['peak_net_pct'],90)
        self.assertFalse(p['profit_protection']['armed'])
        self.assertIsNone(active.exit_reason(p,2,1_000_000))

    def test_stop_and_target_have_priority_and_no_rounding(self):
        p=self.position();self.observe(p,15)
        self.assertEqual(active.exit_reason(p,-40,0),'ADAPTIVE_STOP_NET_USD')
        self.assertEqual(active.exit_reason(p,30,0),'ADAPTIVE_TAKE_PROFIT_NET_USD')
        q=self.position()
        self.assertIsNone(active.exit_reason(q,29.999999,0))
        self.assertIsNone(active.exit_reason(q,-4.999999,0))

    def test_bad_and_out_of_order_marks_cannot_change_floor(self):
        p=self.position();self.observe(p,10,10);saved=copy.deepcopy(p)
        for net,stamp in [(20,NOW+9),(float('nan'),NOW+11),(True,NOW+11),(20,0),(20,float('inf'))]:
            active.observe_profit_protection(p,net,stamp)
            self.assertEqual(p,saved)
        for value in (0,-1,True,float('nan')):
            with self.assertRaises(ValueError):active.quality_exit_parameters(value)

    def test_legacy_fixed_policy_never_arms_protection(self):
        for version in (active.QUALITY_EXIT_VERSION,active.CAPACITY_EXIT_VERSION):
            p={'notional_usd':100,'exit_parameters':{**active.capacity_exit_parameters(),'version':version}}
            self.observe(p,15);self.assertIsNone(self.observe(p,2,1))
            self.assertNotIn('profit_protection',p)

    def old_lots(self):
        for sid,b in self.books.items():
            b['history']=[dict(pnl_usd=-60,entry_policy_version=active.CAPACITY_TEST_VERSION,
                               opened_at=NOW-3000,closed_at=NOW-2000)]
            b['funding_events']=[{'amount_usd':750}]
            for n in range(4):
                active.attach(b,dict(strategy_id=sid,trade_no=n+1,notional_usd=100,
                    opened_at=NOW-1000,quantity=123,entry_price=.1,remaining_cost_basis_usd=100,
                    entry_policy_version=active.CAPACITY_TEST_VERSION,capacity_test=True,
                    execution_mode=lab.EXECUTION_MODEL_VERSION,peak_net_pct=15,open_pnl_usd=-2,
                    address=f'{sid}-{n}',pairAddress=f'pool-{sid}-{n}',
                    exit_parameters=active.capacity_exit_parameters()))

    def test_sixteen_existing_lots_audited_without_rewriting_capital_entry_or_history(self):
        self.old_lots();saved=copy.deepcopy(self.books)
        self.assertEqual(len(active.apply_adaptive_exit_policy(self.books,NOW)),16)
        amended={'exit_policy_changes','exit_parameters','exit_policy_label','profit_protection',
                 'stop_loss_net_pct','stop_headroom_pct'}
        for sid,b in self.books.items():
            for key in ('history','balance','starting_balance','funding_events','trade_seq'):
                self.assertEqual(b[key],saved[sid][key])
            for p,old in zip(active.positions(b),active.positions(saved[sid])):
                self.assertEqual({k:v for k,v in p.items() if k not in amended},
                                 {k:v for k,v in old.items() if k not in amended})
                event=p['exit_policy_changes'][-1]
                self.assertEqual(event['previous_parameters'],old['exit_parameters'])
                self.assertEqual(event['new_parameters'],p['exit_parameters'])
                active.observe_profit_protection(p,15,NOW-1)
                self.assertIsNone(p['profit_protection']['peak_net_usd'])
                self.assertIsNone(self.observe(p,-2,1))
                self.assertFalse(p['profit_protection']['armed'])
            self.assertEqual(active.capacity(b,NOW)['blocked_reason'],'funded_active_daily_loss_limit')
        after=copy.deepcopy(self.books)
        self.assertEqual(active.apply_adaptive_exit_policy(self.books,NOW+2),[])
        self.assertEqual(after,self.books)

    def test_unrecognized_live_other_and_pending_lots_unchanged(self):
        self.old_lots()
        self.books['EARLY']['positions'][0]['exit_parameters']['version']='UNKNOWN'
        self.books['EARLY']['positions'][1]['execution_mode']='LIVE'
        self.books['MOMENTUM']['portfolio_group']='TEST'
        self.books['PRECISION']['promotion_pending']=True
        before=copy.deepcopy(self.books)
        self.assertEqual(len(active.apply_adaptive_exit_policy(self.books,NOW)),6)
        for sid in ('MOMENTUM','PRECISION'):
            self.assertEqual(before[sid],self.books[sid])
        for p,old in zip(self.books['EARLY']['positions'][:2],before['EARLY']['positions'][:2]):
            self.assertEqual(p,old)

    def test_disabled_modes_do_not_amend(self):
        self.old_lots();before=copy.deepcopy(self.books)
        for flags in ({active.QUALITY_ENV:'0'},{active.ENV:'0'},
                      {'NEO_ENGINE_MODE':'LIVE'},{'NEO_EXECUTION_MODE':'LIVE'}):
            with patch.dict(os.environ,flags):
                self.assertEqual(active.apply_adaptive_exit_policy(self.books,NOW),[])
            self.assertEqual(before,self.books)

    def test_load_amendment_is_durable_and_keeps_peak_after_restart(self):
        self.old_lots()
        raw={'books':self.books,'portfolio_setup':{'version':'PROMOTED_PAPER_COHORT_V1','status':'ACTIVE'}}
        with tempfile.TemporaryDirectory(prefix='neo-profit-protection-') as tmp:
            path=Path(tmp)/'state.json';path.write_text(json.dumps(raw),encoding='utf-8')
            with patch.object(lab,'STATE_PATH',path),patch.object(lab,'RESET_FLAG_PATH',Path(tmp)/'reset'),\
                 patch.object(lab,'review_strategy_lifecycle',return_value={}):
                loaded=lab.load_state();self.assertTrue(loaded['_exit_policy_requires_persist'])
                p=active.positions(loaded['books']['EARLY'])[0]
                self.observe(p,10,1)
                path.write_text(json.dumps(loaded),encoding='utf-8')
                restarted=lab.load_state();self.assertFalse(restarted['_exit_policy_requires_persist'])
                p=active.positions(restarted['books']['EARLY'])[0]
                self.assertEqual(p['profit_protection']['floor_net_usd'],8)

    def mark(self, price, **overrides):
        self.clock+=5000
        coin={**self.c,'updatedAt':self.clock,'mark_received_at':self.clock,
              'priceUsd':price,'priceNative':price/150,**overrides}
        with patch.object(lab.POSITION_MARK_FEED,'resolve',return_value=coin):
            lab.update_positions({},[coin])

    def test_engine_actual_quotes_arm_and_retrace_to_closed_net_win(self):
        lab.maybe_open([self.c],quality.strong_flows([self.c]))
        b=next(b for b in self.books.values() if active.positions(b))
        self.mark(.0115)
        p=active.positions(b)[0];floor=p['profit_protection']['floor_net_usd']
        self.assertGreater(floor,8)
        self.mark(.0110)
        self.assertFalse(active.positions(b))
        trade=b['history'][0]
        self.assertEqual(trade['exit_reason'],'ADAPTIVE_PROFIT_PROTECTION_NET_USD')
        self.assertGreater(trade['pnl_usd'],5)
        self.assertLess(trade['pnl_usd'],floor)  # no invented threshold fill
        self.assertAlmostEqual(b['balance'],1000+trade['pnl_usd'],places=4)
        self.assertEqual(trade['profit_protection']['floor_net_usd'],floor)
        projected=compact_strategy_lab({'books':self.books})['books'][b['id']]['history'][0]
        self.assertEqual(projected['profit_protection'],trade['profit_protection'])

    def test_engine_freshness_identity_and_network_checks_precede_arming(self):
        lab.maybe_open([self.c],quality.strong_flows([self.c]))
        p=next(p for b in self.books.values() for p in active.positions(b))
        for overrides in ({'mark_received_at':NOW-60_000},{'pairAddress':'WRONG'},
                          {'mark_received_at':NOW+3_600_000},{'priceNative':None}):
            self.mark(.0115,**overrides)
            self.assertNotIn('profit_protection',p)
        self.mark(.0115)
        self.assertTrue(p['profit_protection']['armed'])
        view=compact_strategy_lab({'books':self.books})['books'][p['strategy_id']]
        self.assertEqual(view['position']['profit_protection'],view['positions'][0]['profit_protection'])

    def test_engine_gap_can_close_at_loss_after_a_green_peak(self):
        lab.maybe_open([self.c],quality.strong_flows([self.c]))
        b=next(b for b in self.books.values() if active.positions(b))
        self.mark(.0115);self.mark(.009)
        self.assertLess(b['history'][0]['pnl_usd'],-10)
        self.assertEqual(b['history'][0]['exit_reason'],'ADAPTIVE_STOP_NET_USD')

    def test_exit_cohort_metrics_do_not_reclassify_entry(self):
        from audit_lab_outcomes import audit
        trade=dict(entry_policy_version=active.CAPACITY_TEST_VERSION,exit_parameters=active.quality_exit_parameters(),
                   pnl_usd=8,profit_protection={'armed':True})
        result=audit({'books':{'EARLY':{'history':[trade]}}})
        self.assertEqual(result['by_entry_policy'][active.CAPACITY_TEST_VERSION]['wins'],1)
        self.assertEqual(result['by_exit_policy'][active.ADAPTIVE_EXIT_VERSION]['wins'],1)


if __name__=='__main__':
    unittest.main()
