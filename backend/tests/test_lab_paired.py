import ast
import copy
import json
import math
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import lab_paired_policy as p
import lab_paired_costs as costs
import lab_paired_runner as r
import lab_paired_bridge as bridge

NOW=1791185000000
MINT='A'*43
PAIR='B'*43


def coin(now=NOW,price=.001):
    return {'address':MINT,'pairAddress':PAIR,'symbol':'TEST','name':'Test',
        'priceUsd':price,'priceNative':price/120,'quoteTokenAddress':r.SOL,
        'liquidityUsd':1_000_000,'marketCap':5_000_000,'fdv':5_000_000,
        'dexId':'raydium','score':99,'ageMinutes':60,'updatedAt':now,'received_at':now,
        'volume':{'h1':500000},'priceChange':{'m5':12,'h1':40},'txns':{'m5':{'buys':20,'sells':10}}}


def tape(now=NOW):
    return {'status':'online','updated_at':now,'events':[{'ts':now-i*1000,'address':MINT,'pairAddress':PAIR,
        'direction':'BUY' if i<6 else 'SELL','usd_amount':100.,'token_amount':100000.,
        'signature':'sig'+str(i),'wallet':'wallet'+str(i)} for i in range(8)]}


def weak(now=NOW,key='one'):
    return {'fresh':True,'trades':8,'unique_wallets':6,'buy_usd':20.,'sell_usd':300.,
        'net_buy_usd':-280.,'ratio':20/300,'latest_at':now,'evidence_key':key}


def pos(now=NOW):return {'opened_at':now-20000,'entry_flow_score':95.,'peak_net_pct':-1.}


class PolicyTests(unittest.TestCase):
    def test_exact_pool_only(self):
        t=tape();t['events'][0]['pairAddress']='C'*43
        f=p.flow_window(t,MINT,PAIR,30,NOW);self.assertEqual(f['trades'],7)
    def test_dedup(self):
        t=tape();t['events']+=copy.deepcopy(t['events']);self.assertEqual(p.flow_window(t,MINT,PAIR,30,NOW)['trades'],8)
    def test_future_event_rejected(self):
        t=tape();t['events'][0]['ts']=NOW+1;self.assertEqual(p.flow_window(t,MINT,PAIR,30,NOW)['trades'],7)
    def test_future_provider_rejected(self):
        t=tape();t['updated_at']=NOW+1;self.assertFalse(p.flow_window(t,MINT,PAIR,30,NOW)['fresh'])
    def test_stale_provider(self):
        t=tape();t['updated_at']=NOW-16000;self.assertFalse(p.flow_window(t,MINT,PAIR,30,NOW)['fresh'])
    def test_invalid_direction_and_amount(self):
        t=tape();t['events'][0]['direction']='UNKNOWN';t['events'][1]['usd_amount']=-1
        self.assertEqual(p.flow_window(t,MINT,PAIR,30,NOW)['trades'],6)
    def test_control_only_bracket(self):
        x=pos();self.assertIsNone(p.exit_decision(x,'CONTROL',-1,weak(),weak(),NOW))
    def test_early_needs_new_evidence(self):
        x=pos();self.assertIsNone(p.exit_decision(x,'EARLY',-1,weak(),weak(),NOW))
        self.assertIsNone(p.exit_decision(x,'EARLY',-1,weak(),weak(),NOW+7000))
        self.assertEqual(p.exit_decision(x,'EARLY',-1,weak(NOW+7000,'two'),weak(NOW+7000,'two'),NOW+7000),'FLOW_DETERIORATION')
    def test_single_noise_tick_does_not_exit(self):
        x=pos();p.exit_decision(x,'EARLY',-1,weak(),weak(),NOW)
        f=weak();f.update(ratio=2,net_buy_usd=200)
        self.assertIsNone(p.exit_decision(x,'EARLY',-1,f,f,NOW+2000));self.assertNotIn('weak_since',x)
    def test_missing_flow_not_sell_pressure(self):
        self.assertIsNone(p.exit_decision(pos(),'EARLY',-1,{}, {},NOW))
    def test_stale_flow_not_pressure(self):
        f=weak();f['fresh']=False;self.assertIsNone(p.exit_decision(pos(),'EARLY',-1,f,f,NOW))
    def test_low_dollar_noise_not_pressure(self):
        f=weak();f.update(sell_usd=5,buy_usd=1)
        x=pos();p.exit_decision(x,'EARLY',-1,f,f,NOW);self.assertNotIn('weak_since',x)
    def test_pre_entry_events_not_pressure(self):
        f=weak(NOW-25000);x=pos();p.exit_decision(x,'EARLY',-1,f,f,NOW);self.assertNotIn('weak_since',x)
    def test_profit_floor_2(self):
        x=pos();self.assertIsNone(p.exit_decision(x,'PROTECT',2,{}, {},NOW))
        self.assertEqual(p.exit_decision(x,'PROTECT',.4,{}, {},NOW+1000),'NET_PROFIT_PROTECTION')
    def test_profit_floor_3(self):
        x=pos();p.exit_decision(x,'PROTECT',3,{}, {},NOW)
        self.assertEqual(p.exit_decision(x,'PROTECT',.9,{}, {},NOW+1000),'NET_PROFIT_PROTECTION')
    def test_profit_floor_5(self):
        x=pos();p.exit_decision(x,'PROTECT',5,{}, {},NOW)
        self.assertEqual(p.exit_decision(x,'PROTECT',2.4,{}, {},NOW+1000),'NET_PROFIT_PROTECTION')
    def test_early_does_not_enable_profit_trailing(self):
        x=pos();p.exit_decision(x,'EARLY',5,{}, {},NOW)
        self.assertIsNone(p.exit_decision(x,'EARLY',1,{}, {},NOW+1000))
    def test_large_gap_uses_hard_stop_no_clamp(self):
        self.assertEqual(p.exit_decision(pos(),'PROTECT',-20,{}, {},NOW),'STOP_LOSS_3_NET')
    def test_takeprofit(self):
        self.assertEqual(p.exit_decision(pos(),'CONTROL',15,{}, {},NOW),'TAKE_PROFIT_10_NET')
    def test_maxhold(self):
        x=pos();x['opened_at']=NOW-3600001
        self.assertEqual(p.exit_decision(x,'CONTROL',1,{}, {},NOW),'MAX_HOLD_60')
    def test_nan_not_mark(self):
        self.assertIsNone(p.exit_decision(pos(),'EARLY',math.nan,weak(),weak(),NOW))
    def test_micro_age(self):
        f=p.features(coin(),p.flow_window(tape(),MINT,PAIR,60,NOW));f['age']=8000
        self.assertIn('age_cap',p.entry_rejections(p.GROUPS[1],f))
    def test_micro_activity(self):
        f=p.features(coin(),p.flow_window(tape(),MINT,PAIR,60,NOW));f['flow']['trades']=1
        self.assertIn('flow_activity',p.entry_rejections(p.GROUPS[1],f))
    def test_ultra_negative_observed(self):
        f=p.features(coin(),weak());self.assertIn('negative_observed_flow',p.entry_rejections(p.GROUPS[2],f))
    def test_ultra_unknown_not_negative(self):
        f=p.features(coin(),{});self.assertNotIn('negative_observed_flow',p.entry_rejections(p.GROUPS[2],f))
    def test_ultra_overextended(self):
        f=p.features(coin(),{});f['m5']=21;self.assertIn('overextended',p.entry_rejections(p.GROUPS[2],f))


class CostTests(unittest.TestCase):
    def test_frozen_execution_math_matches_legacy_ast(self):
        root=Path(__file__).resolve().parents[1]
        a=ast.parse((root/'strategy_lab.py').read_text(encoding='utf-8'));b=ast.parse((root/'lab_paired_costs.py').read_text(encoding='utf-8'))
        funcs={n.name:ast.dump(n,include_attributes=False) for n in a.body if isinstance(n,ast.FunctionDef)}
        for node in b.body:
            # The active Lab now validates quote identity and refuses unknown
            # network costs. The paired runner already admits explicit SOL only;
            # its frozen math remains comparable for that verified universe.
            if isinstance(node,ast.FunctionDef) and node.name not in {'sol_usd_from_coin','execution_friction'}:
                self.assertEqual(funcs[node.name],ast.dump(node,include_attributes=False))
    def test_verified_sol_fills_match_frozen_paired_costs(self):
        import strategy_lab as lab
        for dex in ('raydium','pumpswap'):
            for size in (10,50,150):
                c=coin();c['dexId']=dex
                self.assertEqual(lab.entry_execution(c,size),costs.entry_execution(c,size))
                qty=costs.entry_execution(c,size)['quantity']
                self.assertEqual(lab.exit_execution(c,qty),costs.exit_execution(c,qty))
    def test_price_flat_loses_fees(self):
        q=costs.entry_execution(coin(),150);e=costs.exit_execution(coin(),q['quantity'])
        self.assertLess(e['net_proceeds_usd'],q['capital_committed_usd'])
    def test_cost_cap_and_balance(self):
        q=r.entry_size(coin(),100,2.2);self.assertIsNotNone(q)
        self.assertGreaterEqual(q['initial_pct'],-2.2);self.assertLessEqual(q['opening']['capital_committed_usd'],100)
    def test_reject_cost_floor(self):
        c=coin();c['dexId']='pumpswap';c['marketCap']=10000;c['fdv']=10000
        self.assertIsNone(r.entry_size(c,500,2.2))
    def test_no_balance_no_trade(self):self.assertIsNone(r.entry_size(coin(),3,2.2))


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.runner=r.ExperimentRunner(self.root,NOW,reference=lambda *_:{'status':'pass'})
    def tearDown(self):self.tmp.cleanup()
    def open(self):self.runner.maybe_open([coin()],tape(),NOW)
    def mark(self,price,now):self.runner.update_positions({(MINT,PAIR):coin(now,price)},tape(now),now)
    def test_nine_matched_entries(self):
        self.open()
        for g in self.runner.state['groups'].values():
            positions=[b['position'] for b in g['books'].values()];self.assertEqual(len(positions),3)
            for field in ('opened_at','quantity','notional_usd','entry_evidence_hash','episode_id','execution_entry_price'):
                self.assertEqual(len({x[field] for x in positions}),1)
    def test_groups_do_not_share_mutable_objects(self):
        self.open();g=self.runner.state['groups'][p.GROUPS[0].id];g['books']['EARLY']['position']['entry_features']['score']=0
        self.assertEqual(g['books']['CONTROL']['position']['entry_features']['score'],99)
    def test_pending_pairs_not_scored(self):
        self.open();self.mark(.00106,NOW+2000);self.mark(.00102,NOW+4000)
        for g in self.runner.state['groups'].values():
            self.assertIsNone(g['books']['PROTECT']['position']);self.assertIsNotNone(g['books']['CONTROL']['position'])
            self.assertEqual(g['completed'],[])
    def test_completed_pairs_and_ledger(self):
        self.open();self.mark(.00106,NOW+2000);self.mark(.00102,NOW+4000);self.mark(.0012,NOW+6000)
        for g in self.runner.state['groups'].values():
            self.assertEqual(len(g['completed']),1);self.assertIsNone(g['episode'])
            for b in g['books'].values():
                self.assertEqual(len(b['history']),1);self.assertAlmostEqual(b['balance']-500,b['history'][0]['pnl_usd'],places=6)
                self.assertIn('signal_pnl_pct',b['history'][0])
    def test_loss_overshoot_not_clamped(self):
        self.open();self.mark(.0008,NOW+2000)
        for g in self.runner.state['groups'].values():
            self.assertLess(g['books']['CONTROL']['history'][0]['pnl_pct'],-20)
            self.assertEqual(p.book_stats(g['books']['CONTROL'])['stop_overshoots'],1)
    def test_stale_price_does_not_close(self):
        self.open();self.runner.update_positions({},tape(),NOW+2000)
        for g in self.runner.state['groups'].values():
            self.assertTrue(g['books']['CONTROL']['position']);self.assertEqual(g['books']['CONTROL']['history'],[])
            self.assertTrue(p.book_stats(g['books']['CONTROL'])['valuation_stale'])
    def test_wrong_pair_does_not_close(self):
        self.open();c=coin(NOW+2000,.0008);c['pairAddress']='C'*43
        self.runner.update_positions({(MINT,PAIR):c},tape(),NOW+2000)
        self.assertIsNotNone(self.runner.state['groups'][p.GROUPS[0].id]['books']['CONTROL']['position'])
    def test_stale_recovery_at_observed_price(self):
        self.open();self.runner.update_positions({},tape(),NOW+2000);self.mark(.00101,NOW+35000)
        t=self.runner.state['groups'][p.GROUPS[0].id]['books']['CONTROL']['history'][0]
        self.assertEqual(t['exit_reason'],'STALE_DATA_RECOVERY');self.assertEqual(t['exit_price'],.00101)
    def test_unknown_quote_currency_rejected(self):
        c=coin();c['quoteTokenAddress']='USDC';self.runner.maybe_open([c],tape(),NOW)
        self.assertTrue(all(g['episode'] is None for g in self.runner.state['groups'].values()))
    def test_no_reference_no_entry(self):
        self.runner.reference=lambda *_:{'status':'pending'};self.open()
        self.assertTrue(all(g['episode'] is None for g in self.runner.state['groups'].values()))
    def test_cooldown_shared_until_all_close(self):
        self.open();self.mark(.0012,NOW+2000);self.runner.maybe_open([coin(NOW+3000)],tape(NOW+3000),NOW+3000)
        self.assertTrue(all(g['sequence']==1 for g in self.runner.state['groups'].values()))
    def test_roundtrip_reject_does_not_lower_threshold(self):
        c=coin();c['dexId']='pumpswap';c['marketCap']=10000;c['fdv']=10000
        self.runner.maybe_open([c],tape(),NOW)
        self.assertIsNone(self.runner.state['groups']['MICRO_BREAKOUT_V3']['episode'])
    def test_restart_retains_positions_and_histories(self):
        self.open();self.runner.persist(NOW);other=r.ExperimentRunner(self.root,NOW+2000)
        self.assertEqual(other.state,self.runner.state)
    def test_corrupt_state_refuses_reset(self):
        (self.root/'paired_state.json').write_text('{broken')
        with self.assertRaises(ValueError):r.ExperimentRunner(self.root,NOW)
    def test_live_data_directory_forbidden(self):
        with self.assertRaises(ValueError):r.ExperimentRunner('/var/lib/neo-market',NOW)
    def test_daily_limit_does_not_close_existing(self):
        self.open()
        for g in self.runner.state['groups'].values():g['books']['CONTROL']['balance']=400
        self.runner.maybe_open([coin()],tape(),NOW+1000)
        self.assertTrue(all(g['books']['CONTROL']['position'] for g in self.runner.state['groups'].values()))
    def test_snapshot_is_finite_and_no_auto_promotion(self):
        self.open();snapshot=self.runner.snapshot(NOW)
        json.dumps(snapshot,allow_nan=False);self.assertFalse(snapshot['automatic_promotion'])
        self.assertEqual(snapshot['execution_basis'],p.MODEL)
    def test_bridge_does_not_change_legacy_state(self):
        self.runner.persist(NOW);state={'books':{'OLD':{'balance':123,'history':[{'pnl':-20}]}},'stats':{'OLD':{}}}
        before=copy.deepcopy(state)
        with patch.dict(os.environ,{'NEO_PAIRED_DIR':str(self.root)}):out=bridge.merge_paired_snapshot(state)
        self.assertEqual(state,before);self.assertEqual(out['books'],before['books']);self.assertIn('paired',out)
    def test_stale_bridge_exposed(self):
        self.runner.persist(NOW)
        with patch.dict(os.environ,{'NEO_PAIRED_DIR':str(self.root)}),patch('lab_paired_bridge.time.time',return_value=NOW/1000+60):
            self.assertEqual(bridge.merge_paired_snapshot({})['paired']['status'],'stale')
    def test_read_only_inputs(self):
        c=coin();t=tape();before=(copy.deepcopy(c),copy.deepcopy(t));self.runner.maybe_open([c],t,NOW)
        self.assertEqual((c,t),before)

if __name__=='__main__':unittest.main()
