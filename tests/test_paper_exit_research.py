"""Synthetic safety/mechanics fixtures, never a profitability backtest."""
import copy
import json
import unittest
from unittest.mock import patch

import test_paper_quality as quality
import paper_exit_research as research
from lab_dashboard_projection import compact_strategy_lab

lab,active,NOW=quality.lab,quality.active,quality.NOW


def position(n=1, **changes):
    return dict(strategy_id='EARLY',trade_no=n,opened_at=NOW,address=f'mint{n}',pairAddress=f'pool{n}',
        symbol='COIN',entry_policy_version=active.QUALITY_VERSION,quality_mode=True,
        notional_usd=100,quantity=10,remaining_cost_basis_usd=100.01,partial_realized_pnl=0,
        **changes)


def coin(p,at,price=1):
    return dict(address=p['address'],pairAddress=p['pairAddress'],updatedAt=at,
                priceUsd=price,priceNative=price/150)


class ResearchTests(unittest.TestCase):
    def setUp(self):
        self.container={'books':{'EARLY':{'balance':1000,'history':[]}}}
        self.state=research.ensure(self.container,lab.forward_cost_model(),NOW)
        self.p=position()
        self.e=research.start(self.state,self.p,NOW,new_entry=True)

    def observe(self,net,delta,price=1):
        return research.observe(self.e,coin(self.p,NOW+delta,price),net,NOW+delta)

    def test_frozen_config_and_state_identity_survive_json_restart(self):
        self.observe(5,1000)
        restart=json.loads(json.dumps(self.container))
        state=research.ensure(restart,lab.forward_cost_model(),NOW+2000)
        self.assertEqual(state,self.state)
        self.assertEqual(research.start(state,self.p,NOW+2000),state['episodes'][0])
        self.assertEqual(len(state['episodes']),1)

    def test_model_or_profile_mismatch_never_resets_research(self):
        saved=copy.deepcopy(self.container)
        model={**lab.forward_cost_model(),'network_fee_sol':9}
        with self.assertRaises(ValueError):research.ensure(self.container,model,NOW)
        self.assertEqual(self.container,saved)
        self.state['config']['profiles']['QUICK_6_LOCK2']['target']=99
        with self.assertRaises(ValueError):research.ensure(self.container,lab.forward_cost_model(),NOW)

    def test_unknown_or_corrupt_state_is_not_reset(self):
        for state in ({'version':'UNKNOWN'}, {'version':research.VERSION,'episodes':None}):
            root={'exit_research':state};saved=copy.deepcopy(root)
            with self.assertRaises(ValueError):research.ensure(root,lab.forward_cost_model(),NOW)
            self.assertEqual(root,saved)
        self.e['legs'][research.BASELINE]['status']='FAKE_WIN'
        with self.assertRaises(ValueError):research.ensure(self.container,lab.forward_cost_model(),NOW)

    def test_resumed_lots_and_capacity_entries_are_excluded_from_review(self):
        for n,new,p in [(2,False,position(2)),(3,True,{**position(3),'capacity_test':True}),
                       (4,True,{**position(4),'entry_policy_version':active.CAPACITY_TEST_VERSION})]:
            e=research.start(self.state,p,NOW,new_entry=new)
            self.assertFalse(e['eligible_for_review'])
            self.assertEqual(e['origin'],'RESUMED_OPEN_LOT')
        self.assertTrue(self.e['eligible_for_review'])

    def test_financial_books_and_input_position_never_mutated(self):
        books=copy.deepcopy(self.container['books']);p=copy.deepcopy(self.p)
        self.observe(7,1000);self.observe(5,3000,2)
        research.view(self.state,NOW+research.HORIZON_MS)
        self.assertEqual(self.container['books'],books)
        self.assertEqual(self.p,p)

    def test_target_is_a_trigger_then_next_changed_fresh_mark_books_actual_net(self):
        self.observe(6,1000)
        leg=self.e['legs']['QUICK_6_LOCK2']
        self.assertEqual(leg['status'],'EXIT_PENDING')
        self.observe(5,2500,2)  # less than 2 s
        self.assertEqual(leg['status'],'EXIT_PENDING')
        self.observe(-7,3500,2)
        self.assertEqual(leg['status'],'CLOSED')
        self.assertEqual(leg['net_usd'],-7)
        self.assertEqual(leg['stressed_net_usd'],-8)
        self.assertEqual(leg['reason'],'NET_TARGET')

    def test_same_price_polls_cannot_fill_or_duplicate_observation(self):
        self.observe(6,1000)
        self.assertFalse(self.observe(100,1000,2))
        self.observe(6,4000)
        self.assertEqual(self.e['legs']['QUICK_6_LOCK2']['status'],'EXIT_PENDING')
        self.assertEqual(self.e['observed_marks'],2)

    def test_runner_has_no_upper_target_while_baseline_does(self):
        self.observe(31,1000)
        self.assertEqual(self.e['legs'][research.BASELINE]['status'],'EXIT_PENDING')
        self.assertEqual(self.e['legs']['RUNNER_LOCK4']['status'],'OPEN')
        self.observe(28,4000,2)
        self.assertEqual(self.e['legs'][research.BASELINE]['net_usd'],28)
        self.assertEqual(self.e['legs']['RUNNER_LOCK4']['status'],'OPEN')

    def test_quick_trail_can_exit_without_reaching_target(self):
        self.observe(3,1000)
        self.assertEqual(self.e['legs']['QUICK_6_LOCK2']['floor_net_usd'],2.25)
        self.observe(2,4000,2)
        self.assertEqual(self.e['legs']['QUICK_6_LOCK2']['reason'],'NET_PROTECTION')
        self.observe(1,7000,3)
        self.assertEqual(self.e['legs']['QUICK_6_LOCK2']['net_usd'],1)

    def test_missing_stale_future_wrong_pool_and_nonfinite_marks_ignored(self):
        base=coin(self.p,NOW+1000)
        for c,net in [({**base,'pairAddress':'wrong'},10),({**base,'address':'wrong'},10),
                ({**base,'updatedAt':NOW-60_000},10),({**base,'updatedAt':NOW+60_000},10),
                ({**base,'priceUsd':0},10),(base,float('nan')),(base,True)]:
            self.assertFalse(research.observe(self.e,c,net,NOW+1000))
        self.assertEqual(self.e['observed_marks'],0)

    def test_expiry_censors_no_zero_fill_or_forced_cash_exit(self):
        self.observe(6,1000)
        research.expire(self.e,NOW+research.HORIZON_MS)
        for leg in self.e['legs'].values():
            self.assertEqual(leg['status'],'CENSORED')
            self.assertNotIn('net_usd',leg)
        result=research.view(self.state,NOW+research.HORIZON_MS)
        self.assertEqual(result['censored_or_unresolved_mature_entries'],1)
        self.assertFalse(result['coverage_complete'])

    def test_observation_gap_blocks_review_instead_of_hiding_missing_path(self):
        self.observe(6,1000);self.observe(8,90_000,2)
        self.assertFalse(self.e['continuous_observation'])
        self.assertEqual(self.e['largest_mark_gap_ms'],89_000)
        for leg in self.e['legs'].values():leg.update(status='CLOSED',net_usd=8,stressed_net_usd=7,hold_minutes=2)
        result=research.view(self.state,NOW+research.HORIZON_MS)
        self.assertEqual(result['paired_mature_entries'],0)
        self.assertFalse(result['coverage_complete'])

    def test_pending_exit_state_survives_restart(self):
        self.observe(6,1000)
        restarted=json.loads(json.dumps(self.container))['exit_research']
        e=restarted['episodes'][0]
        research.observe(e,coin(self.p,NOW+4000,2),5,NOW+4000)
        self.assertEqual(e['legs']['QUICK_6_LOCK2']['net_usd'],5)

    def test_trace_and_active_episode_limits_are_bounded(self):
        for delta in range(1000,50_000,1000):self.observe(0,delta)
        self.assertEqual(len(self.e['trace']),research.TRACE_POINTS)
        with patch.object(research,'MAX_ACTIVE',2):
            research.start(self.state,position(2),NOW)
            self.assertIsNone(research.start(self.state,position(3),NOW))
        self.assertEqual(len(self.state['episodes']),2)
        self.assertEqual(self.state['capacity_refusals'],1)

    def test_only_completed_research_rows_can_be_trimmed(self):
        for n in (2,3,4):
            e=research.start(self.state,position(n),NOW)
            research.expire(e,NOW+research.HORIZON_MS)
        with patch.object(research,'MAX_COMPLETED',1):research.start(self.state,position(5),NOW)
        self.assertIn(self.e,self.state['episodes'])
        self.assertEqual(self.state['trimmed_completed'],2)

    def test_invalid_entry_snapshot_is_not_invented(self):
        for field,value in [('quantity',0),('notional_usd',True),('remaining_cost_basis_usd',float('nan')),
                            ('opened_at',NOW+1),('pairAddress','')]:
            p={**position(2),field:value}
            with self.assertRaises(ValueError):research.start(self.state,p,NOW)

    def test_zero_closes_has_unknown_wr_and_no_promotion(self):
        view=research.view(self.state,NOW)
        for row in view['profiles']:
            self.assertIsNone(row['win_rate_pct'])
            self.assertEqual(row['status'],'INSUFFICIENT_PROSPECTIVE_DATA')
        self.assertFalse(view['automatic_promotion'])
        self.assertFalse(view['financial_effect'])

    def test_maturity_and_resumed_rows_prevent_cherry_picked_early_acceptance(self):
        for leg in self.e['legs'].values():leg.update(status='CLOSED',net_usd=8,stressed_net_usd=7,hold_minutes=2)
        view=research.view(self.state,NOW+research.HORIZON_MS-1)
        self.assertEqual(view['paired_mature_entries'],0)
        self.assertEqual(view['profiles'][0]['observed_closes'],1)
        view=research.view(self.state,NOW+research.HORIZON_MS)
        self.assertEqual(view['paired_mature_entries'],1)
        self.assertEqual(view['profiles'][0]['status'],'INSUFFICIENT_PROSPECTIVE_DATA')

    def test_review_is_manual_even_for_synthetic_favorable_mature_comparison(self):
        for n in range(2,31):
            e=research.start(self.state,position(n),NOW,new_entry=True)
            for name,leg in e['legs'].items():
                net=5 if name==research.BASELINE else 8
                leg.update(status='CLOSED',net_usd=net,stressed_net_usd=net-1,
                           hold_minutes=4 if name==research.BASELINE else 2)
        for name,leg in self.e['legs'].items():
            net=5 if name==research.BASELINE else 8
            leg.update(status='CLOSED',net_usd=net,stressed_net_usd=net-1,
                       hold_minutes=4 if name==research.BASELINE else 2)
        with patch.multiple(research,MIN_PAIRS=30,MIN_DAYS=1):
            view=research.view(self.state,NOW+research.HORIZON_MS)
        self.assertEqual(view['profiles'][1]['status'],'CANDIDATE_FOR_MANUAL_REVIEW_NOT_VALIDATED')
        self.assertFalse(view['automatic_promotion'])
        self.assertFalse(view['profitability_proven'])

    def test_capacity_or_error_blocks_review_for_entire_window(self):
        self.state['capacity_refusals']=1
        self.assertFalse(research.view(self.state,NOW)['coverage_complete'])
        self.state['capacity_refusals']=0;self.state['observation_errors']=1
        self.assertFalse(research.view(self.state,NOW)['coverage_complete'])


class EngineResearchTests(unittest.TestCase):
    setUp=quality.QualityTests.setUp

    def open(self):
        lab.maybe_open([self.c],quality.strong_flows([self.c]))
        return next(b for b in self.books.values() if active.positions(b))

    def mark(self, price):
        self.clock+=5000
        c={**self.c,'updatedAt':self.clock,'mark_received_at':self.clock,
           'priceUsd':price,'priceNative':price/150}
        with patch.object(lab.POSITION_MARK_FEED,'resolve',return_value=c):lab.update_positions({},[c])
        return c

    def test_actual_quality_entry_is_registered_with_full_entry_provenance(self):
        b=self.open();e=lab.STATE['exit_research']['episodes'][0]
        self.assertTrue(e['eligible_for_review'])
        self.assertEqual(e['quantity'],active.positions(b)[0]['quantity'])
        self.assertEqual(e['entry_policy_version'],active.QUALITY_VERSION)
        self.assertEqual(len(b['history']),0)

    def test_shadow_observations_continue_after_actual_exit_without_changing_cash(self):
        b=self.open();self.mark(.0115);self.mark(.011)
        self.assertFalse(active.positions(b));saved=copy.deepcopy(self.books)
        e=lab.STATE['exit_research']['episodes'][0];before=e['observed_marks']
        c=self.mark(.0108)
        self.assertGreater(e['observed_marks'],before)
        self.assertEqual(self.books,saved)
        self.assertEqual(e['legs']['QUICK_6_LOCK2']['status'],'CLOSED')
        expected=lab.exit_execution(c,e['quantity'])['net_proceeds_usd']-e['remaining_cost_basis_usd']
        # Each leg uses its own subsequent fresh quote, not a fixed target.
        self.assertEqual(e['legs'][research.BASELINE]['net_usd'],expected)

    def test_research_failure_does_not_block_actual_stop_or_reset_data(self):
        b=self.open();lab.STATE['exit_research']['config_hash']='CORRUPT'
        saved=copy.deepcopy(lab.STATE['exit_research'])
        self.mark(.009)
        self.assertFalse(active.positions(b))
        self.assertLess(b['history'][0]['pnl_usd'],-10)
        self.assertEqual(lab.STATE['exit_research'],saved)
        self.assertIn('mismatch',lab.STATE['exit_research_error'])

    def test_disabled_and_other_books_do_not_create_research(self):
        with patch.dict(quality.os.environ,{active.QUALITY_ENV:'0'}):
            lab.record_exit_research_mark(self.books['EARLY'],position(),coin(position(),NOW),0,NOW)
        self.assertNotIn('exit_research',lab.STATE)
        b={**self.books['EARLY'],'portfolio_group':'TEST'}
        lab.record_exit_research_mark(b,position(),coin(position(),NOW),0,NOW)
        self.assertNotIn('exit_research',lab.STATE)

    def test_snapshot_projection_carries_summary_not_large_raw_traces(self):
        self.open();self.mark(.0115)
        lab.STATE['exit_research_summary']=research.view(lab.STATE['exit_research'],self.clock)
        view=compact_strategy_lab(lab.STATE)
        self.assertEqual(view['exit_research'],lab.STATE['exit_research_summary'])
        self.assertNotIn('episodes',view['exit_research'])
        self.assertLess(len(json.dumps(view['exit_research'])),16_000)


if __name__=='__main__':unittest.main()
