"""Synthetic holding-policy mechanics, never evidence of profitable returns."""
import copy
import json
import os
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

import test_paper_quality as quality
import paper_horizon_exits as horizons

active,lab,NOW=quality.active,quality.lab,quality.NOW


class HorizonTests(unittest.TestCase):
    setUp=quality.QualityTests.setUp

    def position(self,sid='EARLY',**changes):
        return dict(strategy_id=sid,trade_no=1,opened_at=NOW-120*60_000,
            notional_usd=100,quantity=123,remaining_cost_basis_usd=100.01,
            entry_price=.01,entry_policy_version=active.CAPACITY_TEST_VERSION,
            execution_mode=lab.EXECUTION_MODEL_VERSION,capacity_test=True,
            address=quality.fixture.MINT,pairAddress=quality.fixture.PAIR,
            exit_parameters=active.quality_exit_parameters(),peak_net_pct=99,**changes)

    def new_position(self,sid='EARLY'):
        return {**self.position(sid),'exit_parameters':horizons.parameters(sid)}

    def test_fixed_distinct_profiles_are_not_fitted_to_shadow_outcomes(self):
        for sid,minutes,target,arm in [('EARLY',60,6,2),('MOMENTUM',15,6,2),
                ('PRECISION',240,15,4),('ULTRA_PRECISION',720,30,4)]:
            p=horizons.parameters(sid)
            self.assertEqual((p['max_hold_minutes'],p['take_profit_net_usd'],
                p['profit_protection_arm_net_usd']),(minutes,target,arm))
            self.assertEqual(p['stop_loss_net_usd'],5)
            self.assertFalse(p['profitability_proven'])
            self.assertEqual(active.exit_parameters(sid),p)

    def test_invalid_strategy_or_size_never_defaults_to_a_holding_profile(self):
        with self.assertRaises(ValueError):horizons.parameters('UNKNOWN')
        for value in (0,-1,True,float('nan'),float('inf')):
            with self.assertRaises(ValueError):horizons.parameters('EARLY',value)

    def test_timeout_is_exact_and_applies_to_wins_flat_and_losses(self):
        for sid in horizons.PROFILES:
            p=self.new_position(sid);minutes=p['exit_parameters']['max_hold_minutes']
            for net in (-4,0,1):
                self.assertIsNone(active.exit_reason(p,net,minutes-.0001))
                self.assertEqual(active.exit_reason(p,net,minutes),f'PAPER_HORIZON_MAX_HOLD_{minutes}')

    def test_stop_target_and_profit_protection_take_priority_over_timeout(self):
        p=self.new_position()
        self.assertEqual(active.exit_reason(p,-20,100),'ADAPTIVE_STOP_NET_USD')
        self.assertEqual(active.exit_reason(p,8,100),'ADAPTIVE_TAKE_PROFIT_NET_USD')
        active.observe_profit_protection(p,5,NOW)
        self.assertEqual(active.exit_reason(p,3,100),'ADAPTIVE_PROFIT_PROTECTION_NET_USD')

    def test_quick_trail_arms_at_two_and_never_lowers_floor(self):
        p=self.new_position()
        active.observe_profit_protection(p,1.999,NOW)
        self.assertFalse(p['profit_protection']['armed'])
        active.observe_profit_protection(p,2,NOW+1)
        self.assertEqual(p['profit_protection']['floor_net_usd'],1.25)
        active.observe_profit_protection(p,5,NOW+2)
        active.observe_profit_protection(p,3,NOW+3)
        self.assertEqual(p['profit_protection']['floor_net_usd'],4)

    def test_dollar_limits_scale_with_notional_not_account_balance(self):
        p={**self.new_position(),'notional_usd':200,'exit_parameters':horizons.parameters('EARLY',200)}
        self.assertEqual(active.exit_reason(p,3,0),'ADAPTIVE_TAKE_PROFIT_NET_USD')
        self.assertEqual(active.exit_reason(p,-2.5,0),'ADAPTIVE_STOP_NET_USD')

    def test_amendment_preserves_financial_entries_history_and_original_clock(self):
        for sid,b in self.books.items():
            b['history']=[dict(pnl_usd=-60,closed_at=NOW-500,entry_policy_version=active.CAPACITY_TEST_VERSION)]
            b['funding_events']=[{'amount_usd':750}]
            active.attach(b,self.position(sid))
        before=copy.deepcopy(self.books)
        self.assertEqual(len(active.apply_horizon_exit_policy(self.books,NOW)),4)
        amended={'exit_parameters','exit_policy_label','exit_policy_changes','profit_protection',
                 'stop_loss_net_pct','stop_headroom_pct'}
        for sid,b in self.books.items():
            p=active.positions(b)[0];old=active.positions(before[sid])[0]
            self.assertEqual({k:v for k,v in p.items() if k not in amended},
                             {k:v for k,v in old.items() if k not in amended})
            for field in ('balance','starting_balance','history','funding_events','trade_seq'):
                self.assertEqual(b.get(field),before[sid].get(field))
            self.assertEqual(p['exit_policy_changes'][-1]['original_opened_at'],old['opened_at'])
            self.assertIsNone(p['profit_protection']['peak_net_usd'])  # not historical 99%
            self.assertEqual(active.capacity(b,NOW)['blocked_reason'],'funded_active_daily_loss_limit')
        saved=copy.deepcopy(self.books)
        self.assertEqual(active.apply_horizon_exit_policy(self.books,NOW+1),[])
        self.assertEqual(saved,self.books)

    def test_observed_adaptive_peak_and_floor_survive_amendment_and_json_restart(self):
        p=self.position();active.observe_profit_protection(p,5,NOW-100)
        active.attach(self.books['EARLY'],p)
        active.apply_horizon_exit_policy(self.books,NOW)
        self.assertEqual(p['profit_protection']['peak_net_usd'],5)
        self.assertEqual(p['profit_protection']['floor_net_usd'],4)
        q=json.loads(json.dumps(p));active.observe_profit_protection(q,3,NOW+1)
        self.assertEqual(q['profit_protection']['floor_net_usd'],4)
        self.assertEqual(active.exit_reason(q,3,120),'ADAPTIVE_PROFIT_PROTECTION_NET_USD')
        self.assertTrue(p['exit_policy_changes'][-1]['observed_protection_carried'])

    def test_future_or_unversioned_peak_cannot_be_carried(self):
        for state in ({'version':active.ADAPTIVE_EXIT_VERSION,'activated_at':NOW,'last_mark_at':NOW+1,'peak_net_usd':90},
                      {'version':'UNKNOWN','activated_at':NOW-1,'last_mark_at':NOW,'peak_net_usd':90}):
            p=self.position(profit_protection=state)
            b={**self.books['EARLY'],'position':p,'positions':[p]}
            active.apply_horizon_exit_policy({'EARLY':b},NOW)
            self.assertIsNone(p['profit_protection']['peak_net_usd'])

    def test_unknown_live_pending_and_other_books_and_disabled_modes_untouched(self):
        for sid,b in self.books.items():active.attach(b,self.position(sid))
        self.books['EARLY']['positions'][0]['exit_parameters']['version']='UNKNOWN'
        self.books['MOMENTUM']['positions'][0]['execution_mode']='LIVE'
        self.books['PRECISION']['promotion_pending']=True
        self.books['ULTRA_PRECISION']['portfolio_group']='TEST'
        saved=copy.deepcopy(self.books)
        self.assertEqual(active.apply_horizon_exit_policy(self.books,NOW),[])
        self.assertEqual(self.books,saved)
        for flags in ({active.QUALITY_ENV:'0'},{'NEO_ENGINE_MODE':'LIVE'},{'NEO_EXECUTION_MODE':'LIVE'}):
            with patch.dict(os.environ,flags):
                self.assertEqual(active.apply_horizon_exit_policy({'EARLY':self.books['EARLY']},NOW),[])

    def open_for(self,sid):
        with patch.object(lab,'STRATEGIES',[s for s in self.strategies if s['id']==sid]):
            lab.maybe_open([self.c],quality.strong_flows([self.c]))
        p=active.positions(self.books[sid])[0]
        self.assertEqual(p['exit_parameters'],horizons.parameters(sid))
        return p

    def mark(self,price,**changes):
        c={**self.c,'updatedAt':self.clock,'mark_received_at':self.clock,
           'priceUsd':price,'priceNative':price/150,**changes}
        with patch.object(lab.POSITION_MARK_FEED,'resolve',return_value=c):lab.update_positions({},[c])

    def test_overdue_quick_real_engine_books_quote_not_target_and_does_not_force_reentry(self):
        p=self.open_for('EARLY');opened=p['opened_at'];basis=p['remaining_cost_basis_usd']
        self.clock=NOW+60*60_000
        self.mark(.0103)
        b=self.books['EARLY'];t=b['history'][0]
        self.assertEqual(t['exit_reason'],'PAPER_HORIZON_MAX_HOLD_60')
        self.assertEqual(t['opened_at'],opened)
        self.assertEqual(t['remaining_cost_basis_usd'],basis)
        self.assertGreater(t['pnl_usd'],0);self.assertLess(t['pnl_usd'],6)
        self.assertAlmostEqual(b['balance'],1000+t['pnl_usd'],places=4)
        lab.maybe_open([],{});self.assertFalse(active.positions(b))

    def test_timeout_loss_is_real_loss_and_stale_wrong_future_network_quotes_cannot_exit(self):
        self.open_for('MOMENTUM');self.clock=NOW+60*60_000
        for changes in ({'mark_received_at':NOW},{'mark_received_at':self.clock+60_000},
                {'pairAddress':'WRONG'},{'priceNative':None}):
            self.mark(.0099,**changes)
            self.assertTrue(active.positions(self.books['MOMENTUM']))
        self.mark(.0099)
        t=self.books['MOMENTUM']['history'][0]
        self.assertEqual(t['exit_reason'],'PAPER_HORIZON_MAX_HOLD_15')
        self.assertLess(t['pnl_usd'],0)

    def test_new_momentum_scalp_has_distinct_receipt_and_real_engine_fifteen_minute_timeout(self):
        p=self.open_for('MOMENTUM')
        self.assertEqual(p['notional_usd'],100)
        self.assertEqual(p['exit_parameters']['version'],horizons.SCALP_VERSION)
        self.assertEqual(p['exit_parameters']['holding_profile'],'SCALP')
        self.clock=NOW+14*60_000;self.mark(.0102)
        self.assertTrue(active.positions(self.books['MOMENTUM']))
        self.clock=NOW+15*60_000;self.mark(.0102)
        self.assertFalse(active.positions(self.books['MOMENTUM']))
        t=self.books['MOMENTUM']['history'][0]
        self.assertEqual(t['exit_reason'],'PAPER_HORIZON_MAX_HOLD_15')
        self.assertLess(t['pnl_usd'],6)  # quote result, never booked at a target

    def test_existing_horizon_momentum_and_holder_are_not_rewritten_or_rearmed(self):
        for sid,minutes,profile in [('MOMENTUM',60,'QUICK'),('ULTRA_PRECISION',720,'HOLDER')]:
            p=self.new_position(sid)
            p['exit_parameters'].update(version=horizons.VERSION,max_hold_minutes=minutes,holding_profile=profile)
            active.observe_profit_protection(p,5,NOW-1)
            active.attach(self.books[sid],p)
        before=copy.deepcopy(self.books)
        self.assertEqual(active.apply_horizon_exit_policy(self.books,NOW),[])
        self.assertEqual(self.books,before)
        p=active.positions(self.books['MOMENTUM'])[0]
        self.assertIsNone(active.exit_reason(p,4.5,15))
        self.assertEqual(active.exit_reason(p,4.5,60),'PAPER_HORIZON_MAX_HOLD_60')
        active.observe_profit_protection(p,6,NOW)
        self.assertAlmostEqual(p['profit_protection']['floor_net_usd'],4.8)

    def test_daily_pause_retains_all_managed_losses_and_open_marks_across_utc_day(self):
        b=self.books['MOMENTUM']
        b['history']=[dict(entry_policy_version=active.CAPACITY_TEST_VERSION,pnl_usd=-60,closed_at=NOW)]
        cap=active.capacity(b,NOW)
        self.assertEqual(cap['blocked_reason'],'funded_active_daily_loss_limit')
        self.assertEqual(cap['daily_window_ends_at'],(NOW//86_400_000+1)*86_400_000)
        self.assertEqual(cap['daily_window_basis'],'UTC_CALENDAR_DAY_INCLUDING_OPEN_MARKS')
        self.assertFalse(cap['next_day_guarantees_entry'])
        next_day=cap['daily_window_ends_at']
        self.assertIsNone(active.capacity(b,next_day)['blocked_reason'])
        active.attach(b,self.new_position('MOMENTUM'))
        active.positions(b)[0].update(open_pnl_usd=-55,quote_status='fresh')
        self.assertEqual(active.capacity(b,next_day)['blocked_reason'],'funded_active_daily_loss_limit')

    def test_long_profile_is_not_sold_at_one_hour(self):
        self.open_for('ULTRA_PRECISION');self.clock=NOW+60*60_000
        self.mark(.0101)
        self.assertTrue(active.positions(self.books['ULTRA_PRECISION']))

    def test_load_persists_once_and_restart_never_renews_original_clock(self):
        active.attach(self.books['EARLY'],self.position())
        raw={'books':self.books,'portfolio_setup':{'version':'PROMOTED_PAPER_COHORT_V1','status':'ACTIVE'}}
        with tempfile.TemporaryDirectory(prefix='neo-horizon-') as tmp:
            path=Path(tmp)/'lab.json';path.write_text(json.dumps(raw),encoding='utf-8')
            with patch.object(lab,'STATE_PATH',path),patch.object(lab,'RESET_FLAG_PATH',Path(tmp)/'reset'),\
                 patch.object(lab,'review_strategy_lifecycle',return_value={}):
                loaded=lab.load_state();self.assertTrue(loaded['_exit_policy_requires_persist'])
                path.write_text(json.dumps(loaded),encoding='utf-8')
                restarted=lab.load_state();self.assertFalse(restarted['_exit_policy_requires_persist'])
                p=active.positions(restarted['books']['EARLY'])[0]
                self.assertEqual(p['opened_at'],NOW-120*60_000)
                self.assertEqual(active.exit_reason(p,1,120),'PAPER_HORIZON_MAX_HOLD_60')


if __name__=='__main__':unittest.main()
