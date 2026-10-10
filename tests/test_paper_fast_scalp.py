"""Synthetic state-machine checks, NOT profitable backtest or live evidence."""
import copy
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import test_funded_active_paper as fixture
import paper_fast_scalp as fast
import heat_veto
from lab_dashboard_projection import compact_strategy_lab

lab, NOW = fixture.lab, fixture.NOW


class FastScalpTests(unittest.TestCase):
    def setUp(self):
        env = patch.dict(os.environ, {fast.ENV:'1','NEO_ENGINE_MODE':'PAPER','NEO_EXECUTION_MODE':'PAPER'})
        env.start(); self.addCleanup(env.stop)
        self.temp = tempfile.TemporaryDirectory(prefix='neo-fast-scalp-tests-')
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)/'fast.json'
        self.now = NOW
        self.history = heat_veto.PairHistory()
        self.defense = SimpleNamespace(history=self.history, evaluate=Mock(return_value={
            'allowed':True,'reasons':[],'log_only_flags':[], 'version':'synthetic-fixture'}))
        self.marks = SimpleNamespace(resolve=lambda p,coins,now:coins.get((p['address'],p['pairAddress'])))
        self.risk = Mock(side_effect=lambda c:dict(status='pass',mint=c['address'],pair=c['pairAddress'],checked_at=self.now))
        self.price = Mock(side_effect=lambda c:dict(status='pass',mint=c['address'],pair=c['pairAddress'],reference_received_at=self.now))
        self.engine = self.new_engine()
        self.coin = fixture.coin(priceUsd=.0102, priceNative=.0102/150, marketCap=20_000_000,
                                 txns={'m5':{'buys':60,'sells':40}})
        self.observe(self.coin, NOW-900_000, .01)
        self.observe(self.coin, NOW-60_000, .01)
        self.observe(self.coin, NOW, .0102)

    def new_engine(self, **updates):
        kwargs=dict(write=lab.atomic_write_path,entry=lab.entry_execution,exit=lab.exit_execution,
            defense=self.defense,marks=self.marks,risk=self.risk,price=self.price,clock=lambda:self.now,
            cost_model=lab.forward_cost_model())
        kwargs.update(updates)
        return fast.FastScalpLab(self.path, **kwargs)

    def observe(self, coin, stamp, price):
        self.history.observe_coin({**coin,'priceUsd':price,'updatedAt':stamp}, stamp)

    def flow(self, coin=None, **changes):
        c = self.coin if coin is None else coin
        proof=fixture.flows([c])[(c['address'],c['pairAddress'])]['verified_flow']
        proof.update(window_at=self.now,available_at=self.now-50,latest_event_at=self.now-100)
        proof.update(changes)
        return {(c['address'],c['pairAddress']):{'verified_flow':proof}}

    def tick(self, advance=0, price=None, flow=None):
        self.now += advance
        self.coin['updatedAt'] = self.now
        if price is not None:
            self.coin['priceUsd'] = price
            self.coin['priceNative'] = price/150
        self.observe(self.coin, self.now, self.coin['priceUsd'])
        self.engine.step([self.coin],self.flow() if flow is None else flow)

    def opened(self):
        self.tick()
        self.assertEqual(len(self.engine.state['positions']),0)
        self.assertIsNotNone(self.engine.state['pending_entry'])
        self.tick(2000)
        self.assertEqual(len(self.engine.state['positions']),1,self.engine.state['diagnostics'])
        return self.engine.state['positions'][0]

    def test_entry_is_full_size_confirmed_delayed_and_isolated(self):
        before=copy.deepcopy(lab.STATE)
        p=self.opened()
        self.assertEqual(p['notional_usd'],250)
        self.assertEqual(p['entry_policy_version'],fast.VERSION)
        self.assertGreaterEqual(p['opened_at']-p['entry_signal_at'],2000)
        self.assertLessEqual(p['entry_roundtrip_cost_pct'],1.5)
        self.assertEqual(p['risk_guard']['status'],'pass')
        self.assertFalse(p['micro_signal']['future_return_predicted'])
        self.assertLess(p['open_pnl_usd'],0)
        self.assertEqual(self.engine.state['balance'],1000)
        self.assertLess(self.engine.free_cash(),750)
        self.assertEqual(lab.STATE,before)
        self.assertIsNone(self.engine.view()['win_rate_pct'])
        args=self.defense.evaluate.call_args.kwargs
        self.assertIs(args['heat_log_only'],False)
        self.assertIs(args['ticker_reuse_log_only'],False)

    def test_incomplete_wrong_pool_stale_or_nonfinite_flow_never_opens(self):
        cases=[dict(source='estimate'),dict(pairAddress='wrong'),dict(coverage_status='DEGRADED'),
               dict(window_ms=60_000),dict(trades=2),dict(unique_wallets=True),
               dict(buy_usd=float('nan')),dict(buy_usd=0),dict(sell_usd=1000)]
        for changes in cases:
            self.tick(1, flow=self.flow(**changes))
            self.assertFalse(self.engine.state['positions'])
            self.assertIsNone(self.engine.state['pending_entry'])
        self.engine.step([self.coin],self.flow(latest_event_at=self.now-12_001))
        self.assertFalse(self.engine.state['positions'])
        self.risk.assert_not_called()

    def test_pending_does_not_fill_at_first_or_stale_observation(self):
        self.tick()
        self.now += 2000
        self.engine.step([self.coin],self.flow())  # same old price observation
        self.assertFalse(self.engine.state['positions'])
        self.risk.assert_not_called()

    def test_cost_signal_and_cash_are_hard_before_provider_work(self):
        self.tick(price=.01005)
        self.assertIsNone(self.engine.state['pending_entry'])
        self.tick(price=.0102)
        self.engine.state['pending_entry']=None
        with patch.object(self.engine,'entry',side_effect=lambda c,n:{**lab.entry_execution(c,n),'quantity':1}):
            self.tick()
            self.assertIsNone(self.engine.state['pending_entry'])
        with patch.object(self.engine,'free_cash',return_value=250):
            self.tick()
            self.assertIsNone(self.engine.state['pending_entry'])
        self.risk.assert_not_called()

    def test_heat_structural_and_loss_veto_are_not_bypassed(self):
        for reason in ['heat_history_warming','heat_crash_in_progress','rug_ticker_reuse','pool_loss_cooldown']:
            self.defense.evaluate.return_value={'allowed':False,'reasons':[reason]}
            self.tick(1)
            self.assertIsNone(self.engine.state['pending_entry'])
            self.assertEqual(self.engine.state['diagnostics']['blocked_reason'],reason)
        self.risk.assert_not_called()

    def test_delayed_provider_expires_flow_before_commit(self):
        self.tick()
        def slow(c):
            self.now += 13_000
            return dict(status='pass',mint=c['address'],pair=c['pairAddress'],reference_received_at=self.now)
        self.price.side_effect=slow
        self.tick(2000)
        self.assertFalse(self.engine.state['positions'])
        self.assertIn('stale',self.engine.state['diagnostics']['blocked_reason'])

    def test_wrong_or_future_risk_and_price_are_refused(self):
        for target, result in [(self.risk,{'status':'pass','mint':'wrong'}),
                (self.price,{'status':'pass','mint':'wrong'}),
                (self.risk,dict(status='pass',mint=self.coin['address'],pair=self.coin['pairAddress'],checked_at=NOW+99_000)),
                (self.price,dict(status='pass',mint=self.coin['address'],pair=self.coin['pairAddress'],reference_received_at=NOW+99_000))]:
            self.tick()
            old=target.side_effect
            target.side_effect=None; target.return_value=result
            self.tick(2000)
            self.assertFalse(self.engine.state['positions'])
            target.side_effect=old
            self.engine.state['pending_entry']=None

    def test_net_target_executes_on_subsequent_price_and_books_gap_not_threshold(self):
        p=self.opened(); entry_clock=p['opened_at']
        self.tick(2000,price=.0107)
        self.assertEqual(p['pending_exit']['reason'],'FAST_TARGET_NET')
        self.assertFalse(self.engine.state['history'])
        self.tick(2000,price=.0103)  # reversal before modeled fill
        t=self.engine.state['history'][0]
        self.assertLess(t['pnl_usd'],5)
        self.assertEqual(t['opened_at'],entry_clock)
        self.assertEqual(self.engine.state['balance'],1000+t['pnl_usd'])
        self.assertEqual(t['pnl_usd'],lab.exit_execution(self.coin,p['quantity'])['net_proceeds_usd']-p['capital_committed_usd'])

    def test_profit_floor_does_not_fall_and_restart_keeps_original_clock(self):
        self.opened()
        self.tick(2000,price=.01045)
        p=self.engine.state['positions'][0]
        self.assertIsNotNone(p['profit_floor_usd'])
        before=copy.deepcopy(p)
        self.engine=self.new_engine()
        self.assertEqual(self.engine.state['positions'][0],before)
        self.tick(2000,price=.01046)
        self.assertGreaterEqual(self.engine.state['positions'][0]['profit_floor_usd'],before['profit_floor_usd'])

    def test_timeout_and_stop_continue_when_entries_disabled_and_marks_missing(self):
        p=self.opened()
        opened=p['opened_at']
        with patch.dict(os.environ,{fast.ENV:'0'}):
            self.now=opened+fast.HOLD_MS
            self.engine.step([],{},refresh=False)
            self.assertEqual(len(self.engine.state['positions']),1)
            self.assertEqual(p['quote_status'],'stale')
            self.tick(price=.0102)
            self.assertEqual(p['pending_exit']['reason'],'FAST_MAX_HOLD_5M')
            self.engine=self.new_engine()  # restart with pending sale
            self.assertEqual(self.engine.state['positions'][0]['opened_at'],opened)
            self.tick(2000,price=.009)
            t=self.engine.state['history'][0]
            self.assertLess(t['pnl_usd'],-7.5)  # no stop-price clamping
            self.assertEqual(t['reason'],'FAST_MAX_HOLD_5M')
            self.assertFalse(self.engine.state['positions'])

    def test_write_failure_reloads_durable_state_without_duplicate_fill_or_credit(self):
        self.tick()
        original=self.engine.write
        self.engine.write=Mock(side_effect=OSError('synthetic failed write'))
        with self.assertRaises(OSError): self.tick(2000)
        self.assertEqual(self.engine.view()['status'],'degraded')
        self.engine.write=original
        self.tick(2000)
        self.assertEqual(len(self.engine.state['positions']),1)
        self.assertEqual(self.engine.state['trade_seq'],1)
        self.assertEqual(self.engine.state['balance'],1000)
        self.assertEqual(self.engine.state['funding_event']['amount_usd'],1000)

    def test_corrupt_or_mismatched_ledger_never_resets(self):
        state=copy.deepcopy(self.engine.state)
        for changes in [{'balance':999},{'config_hash':'wrong'},{'starting_balance':100}]:
            lab.atomic_write_path(self.path,{**state,**changes})
            before=self.path.read_bytes()
            with self.assertRaises(ValueError): self.new_engine()
            self.assertEqual(self.path.read_bytes(),before)

    def test_paper_flags_literal_and_snapshot_projection_is_separate(self):
        for key in ['NEO_ENGINE_MODE','NEO_EXECUTION_MODE',fast.ENV]:
            with patch.dict(os.environ,{key:'LIVE'}):
                self.assertFalse(fast.enabled())
        before=copy.deepcopy(self.engine.state)
        with patch.dict(os.environ,{'NEO_ENGINE_MODE':'LIVE'}):
            self.tick()
        self.assertEqual(self.engine.state,before)
        snap=self.engine.view()
        projected=compact_strategy_lab({'books':{},'fast_scalp':snap})
        self.assertEqual(projected['fast_scalp'],snap)
        self.assertEqual(projected['books'],{})

    def test_only_one_candidate_provider_budget_and_no_duplicate_pool(self):
        self.opened()
        calls=self.risk.call_count
        self.tick(2000)
        self.assertEqual(len(self.engine.state['positions']),1)
        self.assertEqual(self.risk.call_count,calls)
        self.assertEqual(self.engine.state['diagnostics']['blocked_reason'],'fast_pool_already_open')

    def test_real_heat_layer_can_admit_micro_pulse_but_not_a_surge(self):
        # Synthetic continuous 15min history and ticker registry, not a real fill.
        c={**self.coin,'sources':['search'],'priceChange':{'m5':2,'h1':5,'h6':10,'h24':10},
           'volume':{'m5':1000,'h1':60_000}}
        defense=lab.entry_defense.DefensiveEntryLayer(clock=lambda:self.now)
        for stamp in range(NOW-3_600_000,NOW+1,30_000):
            price=.01 if stamp <= NOW-60_000 else .0102
            defense.observe([{**c,'updatedAt':stamp,'priceUsd':price}],stamp)
        self.engine.defense=defense
        result=self.engine.defense_check(c,NOW)
        self.assertTrue(result['allowed'],result)
        plan,reason=self.engine.plan(c,self.flow()[(c['address'],c['pairAddress'])],NOW)
        self.assertIsNone(reason)
        self.assertGreater(plan['signal']['observed_return_pct'],plan['friction'])
        c['priceUsd']=.0104
        result=self.engine.defense_check(c,NOW)
        self.assertFalse(result['allowed'])
        self.assertIn('heat_return_5m_surge',result['reasons'])

    def test_out_of_order_wrong_pool_and_future_marks_cannot_arm_or_close(self):
        p=self.opened()
        before=copy.deepcopy(p)
        for change in [dict(updatedAt=p['opened_at']-1),dict(pairAddress='wrong'),
                       dict(updatedAt=self.now+99_000)]:
            bad={**self.coin,'priceUsd':.1,**change}
            self.engine.marks=SimpleNamespace(resolve=lambda *args:bad)
            self.engine.step([self.coin],{})
            self.assertEqual(p['peak_net_usd'],before['peak_net_usd'])
            self.assertIsNone(p['pending_exit'])

    def test_losing_pool_pause_and_cash_reservations_survive_restart(self):
        p=self.opened()
        self.tick(2000,price=.0095)
        self.assertEqual(p['pending_exit']['reason'],'FAST_STOP_NET')
        self.tick(2000)
        self.assertLess(self.engine.state['balance'],1000)
        self.engine=self.new_engine()
        self.assertEqual(self.engine.blocked_pool(self.coin,self.now),'fast_pool_cooldown')
        before=self.engine.state['balance']
        self.assertEqual(self.engine.state['funding_event']['amount_usd'],1000)
        self.assertEqual(self.new_engine().state['balance'],before)

    def test_cost_model_mismatch_and_feature_failure_do_not_mutate_funded_books(self):
        before=copy.deepcopy(lab.STATE)
        with self.assertRaises(ValueError): self.new_engine(cost_model={})
        with patch.object(lab,'STATE_PATH',Path(self.temp.name)/'lab.json'),patch.object(lab,'FAST_SCALP',None),\
             patch.object(lab.fast_scalp,'FastScalpLab',side_effect=ValueError('bad experiment ledger')):
            lab.fast_scalp_cycle([],{},refresh=True)
        self.assertEqual(lab.STATE['books'],before['books'])
        self.assertEqual(lab.STATE['fast_scalp']['status'],'degraded')
        lab.STATE.clear(); lab.STATE.update(before)

    def test_v1_config_hash_is_exactly_the_deployed_ledger_hash(self):
        self.assertEqual(fast.PREVIOUS_CONFIG_HASH,
                         '5030707e5d2c0fadef64e1102c65e3bdc796fa134c390c2948bf38177a7bb3fd')

    def test_cost_first_branch_can_admit_short_flow_without_slow_momentum(self):
        self.coin.update(priceChange={'m5':-.2,'h1':-15}, txns={'m5':{'buys':6,'sells':18}})
        self.assertFalse(fast.active.matches('MOMENTUM',self.coin))
        self.assertTrue(fast.market_candidate(self.coin))
        hint=fast.discovery_estimate(self.coin)
        self.assertTrue(hint['candidate']); self.assertFalse(hint['is_entry_authorization'])
        p=self.opened()
        self.assertEqual(p['entry_policy_version'],fast.VERSION)
        self.assertEqual(p['notional_usd'],250)
        self.assertGreater(p['micro_signal']['observed_return_pct'],p['entry_roundtrip_cost_pct'])

    def test_new_branch_still_requires_actual_flow_and_observed_cost_cover(self):
        self.coin.update(priceChange={'m5':-.2,'h1':-15},txns={'m5':{'buys':6,'sells':18}})
        self.tick(flow={})
        self.assertIsNone(self.engine.state['pending_entry'])
        self.assertEqual(self.engine.state['diagnostics']['blocked_reason'],'promoted_verified_flow_unavailable')
        self.tick(price=.01005)
        self.assertIsNone(self.engine.state['pending_entry'])
        self.assertEqual(self.engine.state['diagnostics']['blocked_reason'],'fast_move_does_not_cover_cost')
        self.risk.assert_not_called()

    def test_pending_cost_first_candidate_must_still_be_in_universe_at_commit(self):
        self.coin.update(priceChange={'m5':-.2,'h1':-15},txns={'m5':{'buys':6,'sells':18}})
        self.tick(); self.assertIsNotNone(self.engine.state['pending_entry'])
        self.coin['liquidityUsd']=249_999
        self.tick(2000)
        self.assertFalse(self.engine.state['positions'])
        self.assertIsNone(self.engine.state['pending_entry'])
        self.assertEqual(self.engine.state['diagnostics']['blocked_reason'],'fast_market_outside_universe')
        self.risk.assert_not_called()

    def test_virtual_contribution_cannot_be_reclassified_as_real_profit(self):
        for field in ('profit','real_money','transfer_from_funded_books'):
            state=copy.deepcopy(self.engine.state)
            state['funding_event'][field]=True
            lab.atomic_write_path(self.path,state)
            before=self.path.read_bytes()
            with self.assertRaises(ValueError): self.new_engine()
            self.assertEqual(self.path.read_bytes(),before)

    def test_discovery_uses_full_250_cost_and_unknown_or_expensive_is_not_eligible(self):
        for changed in [dict(priceNative=0),dict(liquidityUsd=10_000),
                        dict(marketCap=100_000),dict(quoteTokenAddress='wrong')]:
            hint=fast.discovery_estimate({**self.coin,**changed})
            self.assertFalse(hint['candidate'],hint)
            self.assertFalse(hint['is_entry_authorization'])
        hint=fast.discovery_estimate(self.coin)
        expected=-fast.feasibility.modeled_roundtrip(self.coin,250)['initial_pnl_pct']
        self.assertAlmostEqual(hint['roundtrip_cost_pct'],expected)
        self.assertEqual(hint['planned_notional_usd'],250)

    def test_mark_only_ticks_preserve_last_admission_diagnostics(self):
        self.tick(flow={})
        prior=copy.deepcopy(self.engine.state['diagnostics'])
        self.now+=1000
        self.engine.step([self.coin],{},refresh=False)
        self.assertEqual(self.engine.state['diagnostics'],prior)
        self.assertEqual(self.new_engine().state['diagnostics'],prior)
        self.assertGreater(self.engine.view()['updated_at'],prior['at'])

    def legacy_ledger(self):
        state=copy.deepcopy(self.engine.state)
        state.update(version=fast.PREVIOUS_VERSION,config_hash=fast.PREVIOUS_CONFIG_HASH)
        for row in state['positions']+state['history']:
            row.update(entry_policy_version=fast.PREVIOUS_VERSION,config_hash=fast.PREVIOUS_CONFIG_HASH,
                       exit_parameters=fast.config(fast.PREVIOUS_VERSION))
        lab.atomic_write_path(self.path,state)
        return state

    def test_upgrade_preserves_open_lot_cash_funding_clock_and_frozen_exits(self):
        self.opened()
        before=self.legacy_ledger()
        self.engine=self.new_engine()
        for field in ('positions','history','balance','trade_seq','created_at','funding_event'):
            self.assertEqual(self.engine.state[field],before[field],field)
        self.assertEqual(self.engine.state['version'],fast.VERSION)
        self.assertEqual(len(self.engine.state['policy_changes']),1)
        self.assertEqual(self.engine.state['policy_changes'][0]['funding_change_usd'],0)
        self.assertEqual(self.new_engine().state['policy_changes'],self.engine.state['policy_changes'])

    def test_upgrade_preserves_closed_outcomes_and_does_not_credit_losses(self):
        self.opened(); self.tick(2000,price=.0095); self.tick(2000)
        before=self.legacy_ledger()
        self.assertLess(before['balance'],1000)
        self.engine=self.new_engine()
        self.assertEqual(self.engine.state['history'],before['history'])
        self.assertEqual(self.engine.state['balance'],before['balance'])
        self.assertEqual(self.engine.state['funding_event'],before['funding_event'])

    def test_migrated_v1_position_can_close_with_its_original_exit_receipt(self):
        self.opened(); before=self.legacy_ledger(); self.engine=self.new_engine()
        self.tick(2000,price=.0095); self.tick(2000)
        trade=self.engine.state['history'][0]
        self.assertEqual(trade['entry_policy_version'],fast.PREVIOUS_VERSION)
        self.assertEqual(trade['exit_parameters'],before['positions'][0]['exit_parameters'])
        self.assertEqual(trade['opened_at'],before['positions'][0]['opened_at'])
        self.assertLess(trade['pnl_usd'],0)
        self.assertEqual(self.new_engine().state['balance'],1000+trade['pnl_usd'])

    def test_old_pending_intent_is_invalidated_not_counted_as_a_fill_on_upgrade(self):
        self.tick(); self.assertIsNotNone(self.engine.state['pending_entry'])
        self.legacy_ledger(); self.engine=self.new_engine()
        self.assertIsNone(self.engine.state['pending_entry'])
        self.assertFalse(self.engine.state['positions']); self.assertFalse(self.engine.state['history'])
        self.assertEqual(self.engine.state['trade_seq'],0)

    def test_fast_candidates_get_bounded_discovery_without_slow_rules(self):
        coins=[fixture.coin(i,marketCap=20_000_000,priceChange={'m5':-.2,'h1':-15},
                           txns={'m5':{'buys':6,'sells':18}}) for i in range(5)]
        scheduler=fixture.TapePoolScheduler()
        with patch.object(scheduler,'defensive_entry_decision',side_effect=lambda *a,**k:
                          lab.entry_defense.pass_decision('SYNTHETIC_ISOLATION')):
            selected,report=scheduler.select({'feed':coins},now=NOW,max_tracked=2)
            self.assertEqual(report['fast_scalp']['candidate_pools'],5)
            self.assertEqual(report['fast_scalp']['selected_pools'],2)
            self.assertFalse(report['fast_scalp']['is_entry_authorization'])
            first={c['pairAddress'] for c in selected}
            selected,_=scheduler.select({'feed':coins},now=NOW+20_000,max_tracked=2)
            self.assertEqual({c['pairAddress'] for c in selected},first)
            selected,_=scheduler.select({'feed':coins},now=NOW+60_000,max_tracked=2)
            self.assertFalse(first & {c['pairAddress'] for c in selected})
        self.risk.assert_not_called()

    def test_discovery_disabled_live_or_structurally_blocked_keeps_old_behavior(self):
        c={**self.coin,'priceChange':{'m5':-.2,'h1':-15}}
        for env in [{fast.ENV:'0'},{'NEO_ENGINE_MODE':'LIVE'},{'NEO_EXECUTION_MODE':'LIVE'}]:
            with patch.dict(os.environ,env),patch.object(fixture.TapePoolScheduler,'defensive_entry_decision',
                    side_effect=lambda *a,**k:lab.entry_defense.pass_decision('SYNTHETIC_ISOLATION')):
                _,report=fixture.TapePoolScheduler().select({'feed':[c]},now=NOW,max_tracked=1)
                self.assertFalse(report['fast_scalp']['enabled'])
                self.assertEqual(report['fast_scalp']['candidate_pools'],0)
        with patch.object(fixture.TapePoolScheduler,'defensive_entry_decision',
                return_value={'allowed':False,'reasons':['rug_lp_pullable']}):
            selected,report=fixture.TapePoolScheduler().select({'feed':[c]},now=NOW,max_tracked=1)
            self.assertFalse(selected)
            self.assertEqual(report['fast_scalp']['candidate_pools'],0)


if __name__=='__main__': unittest.main()
