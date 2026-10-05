"""Deterministic main engine regressions; all account/audit/network paths isolated."""
import copy
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

IMPORT_TEMP = tempfile.TemporaryDirectory(prefix='neo-v9-import-')
for name, filename in [('NEO_MARKET_STATE_PATH','state.json'),('NEO_MARKET_AUDIT_PATH','audit.jsonl'),('NEO_LIVE_TAPE_PATH','tape.json')]:
    os.environ[name] = str(Path(IMPORT_TEMP.name)/filename)
import market_monitor as m
import gold_order_flow as flow_policy
import engine_entry_policy as entry_policy
import engine_exit_policy as exit_policy

A, B, C = 'A'*44, 'B'*44, 'C'*44

class MainPaperRepair(unittest.TestCase):
    def test_non_utf8_locale_restart_retains_multilingual_history(self):
        # A real child with UTF-8 mode disabled reproduces Windows cp1251 and
        # POSIX C-locale reads. Atomic save is UTF-8 regardless of user locale.
        m.STATE.history=[{'id':'unicode','symbol':'Койн 🌟 中文','pnl_usd':-1,
                          'session_id':m.STATE.demo_session_id}]
        m.STATE.demo_balance_usd=999
        m.STATE.save()
        code=("import market_monitor as m; s=m.State(load_state=True); "
              "assert s.history[0]['symbol']=='\\u041a\\u043e\\u0439\\u043d \\U0001f31f \\u4e2d\\u6587'; "
              "assert s.demo_balance_usd==999; print('UTF8 restart PASS')")
        env={**os.environ,'PYTHONUTF8':'0','PYTHONCOERCECLOCALE':'0','LC_ALL':'C',
             'NEO_MARKET_STATE_PATH':str(m.STATE_PATH),'NEO_MARKET_AUDIT_PATH':str(m.AUDIT_PATH),
             'PYTHONPATH':str(Path(__file__).resolve().parents[1]/'backend')}
        result=subprocess.run([sys.executable,'-X','utf8=0','-c',code],env=env,
                              capture_output=True,timeout=15)
        self.assertEqual(result.returncode,0,result.stderr.decode('utf-8',errors='replace'))
        self.assertIn(b'UTF8 restart PASS',result.stdout)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='neo-v9-account-')
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.clock = [1_000_000]
        patches = [patch.object(m,'STATE_PATH',root/'state.json'), patch.object(m,'AUDIT_PATH',root/'audit.jsonl'),
                   patch.object(m,'LIVE_TAPE_PATH',root/'tape.json'),patch.object(m,'now_ms',side_effect=lambda:self.clock[0]),
                   patch.object(m,'sol_usd_market_price',return_value=100),patch.object(m.pumpswap_stop,'prime_positions')]
        for p in patches: p.start(); self.addCleanup(p.stop)
        m.STATE = m.State()
        self.monitor = m.Monitor()
        self.addCleanup(self.monitor.stop)
        self.coin = {'address':A,'pairAddress':B,'symbol':'FIXTURE','priceUsd':2,'priceNative':.02,
            'liquidityUsd':200000,'marketCap':1000000,'ageMinutes':60,'score':90,
            'priceChange':{'m5':5,'h1':8},'txns':{'m5':{'buys':10,'sells':3}},'signals':[],
            'updatedAt':self.clock[0]}
        self.flow = {'quality':'COMPLETE','trades':4,'buy_sell_usd_ratio':3,'unique_wallets':3,
            'buyer_wallets':3,'buy_usd':300,'sell_usd':100,'max_sell_usd':50}
        self.context = {'conviction':80,'mode':'STRONG'}
        self.pos = {'id':'position-1','address':A,'pairAddress':B,'session_id':m.STATE.demo_session_id,
            'entry_price':2,'current_price':2,'quantity':100,'notional_usd':200,'original_notional_usd':200,
            'capital_committed_usd':200.23,'entry_network_fee_usd':.03,'entry_account_reserve_usd':.2,
            'entry_liquidity_usd':200000,'opened_at':self.clock[0],'updated_at':self.clock[0],
            'jupiter_token_raw_amount':100000000,'execution_mode':'JUPITER_QUOTE_V2','coin_snapshot':copy.deepcopy(self.coin)}
        self.quote = {'net_proceeds_usd':200,'gross_proceeds_usd':200.03,'network_fee_usd':.03,
            'dex_fee_usd':0,'fill_price':2,'impact_pct':.2,'slippage_pct':.1,'latency_pct':0,
            'quoted_at':self.clock[0],'from_cache':False,'execution_source':'OFFLINE_FIXTURE'}

    def position(self):
        m.STATE.positions=[copy.deepcopy(self.pos)]
        m.STATE.trade_seq=1
        return m.STATE.positions[0]

    def entry_patches(self):
        def prepared(address,pair,notional):
            return ({'token_raw_expected':int(notional/2*1e6),'token_raw_amount':int(notional/2*1e6),
                'input_usdc_raw':int(notional*1e6),'price_impact_pct':.2,'quoted_at':self.clock[0],
                'raw_quote':{'fixture':True}}, {'expected_usdc':notional-1,'floor_usdc':notional-2})
        patches=[patch.object(m.STATE,'live_flow',return_value=self.flow),
            patch.object(self.monitor,'market_context',return_value=self.context),
            patch.object(m.rug_guard,'check',return_value={'status':'pass','metrics':{'decimals':6,'token_account_rent_lamports':1650000}}),
            patch.object(m.price_integrity,'check',return_value={'status':'pass'}),
            patch.object(m.paper_quotes,'prepare_entry',side_effect=prepared)]
        for p in patches: p.start(); self.addCleanup(p.stop)

    def test_old_trigger_is_constant_and_repair_ignores_it(self):
        for cost in (0,-.01,-1,-3,-100,-1e9):
            self.assertEqual(-max(.5,5-abs(min(0,cost))-4.5),-.5)
        self.position()['stop_signal_trigger_pct']=-.5
        self.coin['priceUsd']=1.98
        with patch.object(m.paper_quotes,'position_mark',return_value=self.quote):
            self.monitor.update_positions({A:self.coin})
        self.assertEqual(len(m.STATE.positions),1)
        self.assertFalse(m.STATE.history)

    def test_gap_200_to_160_is_minus_40_not_minus_10(self):
        q,pnl,pct,capped=m.enforce_paper_stop_cap({'net_proceeds_usd':160,'fill_price':1.6},200,0,100)
        self.assertEqual((pnl,pct,capped),(-40,-20,False))
        self.assertEqual(q['fill_price'],1.6)
        self.position()
        self.quote['net_proceeds_usd']=160
        with patch.object(m.paper_quotes,'position_mark',return_value=self.quote):
            self.monitor.update_positions({A:self.coin})
        self.assertAlmostEqual(m.STATE.history[0]['pnl_usd'],-40.23)
        self.assertAlmostEqual(m.STATE.demo_balance_usd,959.77)
        self.assertFalse(m.STATE.history[0]['paper_stop_capped'])

    def test_entry_fee_allocated_once_partial_legs_one_completed_trade(self):
        position=self.position()
        first=dict(self.quote,token_input_raw=50000000,net_proceeds_usd=110)
        remaining=self.monitor.book_paper_exit(position,first,'PARTIAL',self.coin)
        self.assertEqual(remaining['jupiter_token_raw_amount'],50000000)
        self.assertFalse(m.STATE.history)
        self.assertAlmostEqual(m.STATE.demo_balance_usd,1009.885)
        self.monitor.book_paper_exit(remaining,dict(self.quote,token_input_raw=50000000,net_proceeds_usd=95),'FINAL',self.coin)
        self.assertEqual(len(m.STATE.history),1)
        self.assertAlmostEqual(m.STATE.history[0]['pnl_usd'],4.77)
        self.assertAlmostEqual(m.STATE.demo_balance_usd,1004.77)
        self.assertEqual(m.STATE.snapshot()['stats']['closed_trades'],1)
        self.monitor.book_paper_exit(remaining,self.quote,'DUPLICATE',self.coin)
        self.assertAlmostEqual(m.STATE.demo_balance_usd,1004.77)

    def test_exit_wrong_raw_quantity_cannot_book(self):
        with self.assertRaisesRegex(ValueError,'quantity'):
            self.monitor.book_paper_exit(self.position(),dict(self.quote,token_input_raw=100000001),'BAD',self.coin)
        self.assertEqual(m.STATE.demo_balance_usd,1000)
        self.assertFalse(m.STATE.history)

    def test_main_actual_entry_exit_and_restart(self):
        self.entry_patches()
        self.monitor.maybe_open([self.coin])
        self.assertEqual(len(m.STATE.positions),1)
        self.assertEqual(m.State().positions[0]['id'],m.STATE.positions[0]['id'])
        opened=m.STATE.positions[0]
        self.quote['net_proceeds_usd']=opened['notional_usd']*1.12
        with patch.object(m.paper_quotes,'position_mark',return_value=self.quote):
            self.monitor.update_positions({A:self.coin})
        restored=m.State()
        self.assertFalse(restored.positions)
        self.assertEqual(len(restored.history),1)
        self.assertEqual(restored.demo_balance_usd,m.STATE.demo_balance_usd)
        self.assertEqual([json.loads(x)['event'] for x in m.AUDIT_PATH.read_text().splitlines()],['ENTRY','EXIT'])

    def test_final_flow_revalidated_after_quote(self):
        self.entry_patches()
        with patch.object(m.STATE,'live_flow',side_effect=[self.flow,dict(self.flow,quality='DEGRADED')]):
            self.monitor.maybe_open([self.coin])
        self.assertFalse(m.STATE.positions)
        self.assertIn('flow_quality',m.STATE.entry_diagnostics['rejections'])

    def test_unknown_flow_and_pending_rug_never_open(self):
        self.entry_patches()
        with patch.object(m.STATE,'live_flow',return_value=dict(self.flow,quality='UNKNOWN')):
            self.monitor.maybe_open([self.coin])
        self.assertFalse(m.STATE.positions)
        with patch.object(m.rug_guard,'check',return_value={'status':'pending','reasons':['risk_check_pending']}):
            self.monitor.maybe_open([self.coin])
        self.assertFalse(m.STATE.positions)
        self.assertIn('risk_check_pending',m.STATE.entry_diagnostics['rejections'])

    def test_full_loss_risk_and_total_exposure_bound_quote_size(self):
        self.entry_patches()
        with patch.object(m,'MAX_POSITION_RISK_USD',25),patch.object(m.paper_quotes,'prepare_entry',return_value=None) as request:
            self.monitor.maybe_open([self.coin])
            self.assertLessEqual(request.call_args.args[2]+.225,25)
        self.position()
        with patch.object(m,'MAX_TOTAL_EXPOSURE_PCT',20),patch.object(m.paper_quotes,'prepare_entry') as request:
            self.monitor.maybe_open([dict(self.coin,address=C)])
            request.assert_not_called()
            self.assertIn('risk_budget_unavailable',m.STATE.entry_diagnostics['rejections'])

    def test_unavailable_liquidation_and_drawdown_block_new_risk(self):
        self.entry_patches()
        self.position()['valuation_status']='unavailable'
        self.monitor.maybe_open([dict(self.coin,address=C)])
        self.assertIn('liquidation_unavailable',m.STATE.entry_diagnostics['rejections'])
        m.STATE.positions=[]
        m.STATE.demo_balance_usd=900
        with patch.object(m,'MAX_DRAWDOWN_PCT',5): self.monitor.maybe_open([self.coin])
        self.assertIn('drawdown_limit',m.STATE.entry_diagnostics['rejections'])
        self.assertFalse(m.STATE.positions)

    def test_duplicate_or_nonfinite_saved_state_refuses_reset(self):
        m.STATE.save()
        saved=json.loads(m.STATE_PATH.read_text())
        for bad in (dict(saved,positions=[self.pos,self.pos]),dict(saved,demo_balance_usd=float('nan'))):
            m.STATE_PATH.write_text(json.dumps(bad))
            with self.assertRaisesRegex(RuntimeError,'refusing automatic reset'): m.State()

    def test_module_import_is_inert_even_for_unreadable_account(self):
        m.STATE_PATH.write_text('{unreadable-existing-account')
        env=dict(os.environ,NEO_MARKET_STATE_PATH=str(m.STATE_PATH),NEO_MARKET_AUDIT_PATH=str(m.AUDIT_PATH))
        env['PYTHONPATH']=str(Path(m.__file__).parent)
        result=subprocess.run([sys.executable,'-c',"import market_monitor as m; assert m.STATE.positions == []; print('inert import')"],env=env,capture_output=True,text=True,timeout=10)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(m.STATE_PATH.read_text(),'{unreadable-existing-account')
        self.assertFalse(m.AUDIT_PATH.exists())

    def test_main_live_mode_refused_before_loading_or_starting(self):
        with patch.dict(os.environ,{'NEO_ENGINE_MODE':'LIVE'}),patch.object(m.STATE,'load') as load,patch.object(m.training_bridge,'start') as start:
            with self.assertRaisesRegex(ValueError,'PAPER'): m.main()
            load.assert_not_called(); start.assert_not_called()

    def test_pinned_pair_survives_other_scanner_pool_and_missing_feed(self):
        self.position()
        held=dict(self.coin,priceUsd=1.8)
        m.STATE.position_market[f'{A}:{B}']=held
        m.STATE.feed=[dict(self.coin,pairAddress=C,priceUsd=500)]
        with patch.object(m.paper_quotes,'position_mark',return_value=self.quote) as mark:
            self.monitor.fast_position_check()
            self.assertEqual(mark.call_args.args[1]['pairAddress'],B)
            self.assertEqual(m.STATE.positions[0]['current_price'],1.8)
            m.STATE.feed=[]
            self.monitor.fast_position_check()
            self.assertEqual(mark.call_args.args[1]['priceUsd'],1.8)

    def test_pause_does_not_stop_exits(self):
        self.position()
        m.STATE.running=False
        self.quote['net_proceeds_usd']=160
        with patch.object(m.paper_quotes,'position_mark',return_value=self.quote): self.monitor.fast_position_check()
        self.assertFalse(m.STATE.positions)
        self.assertEqual(len(m.STATE.history),1)

    def test_no_route_preserves_risk_then_bounded_retry(self):
        self.position()['pending_exit_reason']='STOP_LOSS_NET_TARGET'
        with patch.object(m.paper_quotes,'position_mark',return_value=None) as mark:
            for attempt in range(6):
                self.monitor.update_positions({A:self.coin})
                next_retry=m.STATE.positions[0]['next_exit_retry_at']
                self.monitor.update_positions({A:self.coin})
                self.assertEqual(mark.call_count,attempt+1)
                self.clock[0]=next_retry
        self.assertEqual(m.STATE.positions[0]['exit_state'],'UNSELLABLE')
        self.assertGreater(m.STATE.positions[0]['conservative_risk_usd'],200)
        self.assertEqual(m.STATE.demo_balance_usd,1000)
        self.assertFalse(m.State().history)

    def test_full_history_over_300_and_breakeven_denominator(self):
        m.STATE.history=[{'id':str(i),'session_id':m.STATE.demo_session_id,'pnl_usd':(1 if i%2 else 0),'pnl_pct':(1 if i%2 else 0)} for i in range(351)]
        m.STATE.trade_seq=351
        m.STATE.save()
        m.STATE=m.State()
        stats=m.STATE.snapshot()['stats']
        self.assertEqual(len(m.STATE.history),351)
        self.assertEqual(stats['closed_trades'],351)
        self.assertEqual(stats['wins'],175)
        self.assertEqual(stats['metrics']['lifetime']['breakeven'],176)
        self.assertIsNone(stats['metrics']['lifetime']['profit_factor'])

    def test_failed_state_commit_rolls_back_memory_and_disk(self):
        m.STATE.save()
        old=m.STATE_PATH.read_bytes()
        with patch.object(m.runtime,'atomic_json',side_effect=OSError('interrupted')):
            with self.assertRaises(OSError):
                m.STATE.commit('ENTRY',self.pos,positions=[self.pos],trade_seq=1)
        self.assertEqual(m.STATE.positions,[])
        self.assertEqual(m.STATE.trade_seq,0)
        self.assertEqual(m.STATE_PATH.read_bytes(),old)

    def test_audit_failure_is_durable_and_retry_after_restart(self):
        with patch.object(m,'append_audit',side_effect=OSError('disk full')):
            m.STATE.commit('ENTRY',self.pos,positions=[self.pos],trade_seq=1)
        m.STATE=m.State()
        self.assertEqual(len(m.STATE.pending_audit),1)
        self.assertEqual(len(m.STATE.positions),1)
        m.STATE.save()
        self.assertFalse(m.STATE.pending_audit)
        self.assertEqual(len(m.AUDIT_PATH.read_text().splitlines()),1)

    def test_restart_after_audit_before_outbox_clear_deduplicates(self):
        real=m.runtime.atomic_json
        calls=[0]
        def crash(path,data):
            calls[0]+=1
            if calls[0]==2: raise OSError('after audit append')
            real(path,data)
        with patch.object(m.runtime,'atomic_json',side_effect=crash):
            m.STATE.commit('ENTRY',self.pos,positions=[self.pos],trade_seq=1)
        m.STATE=m.State()
        m.STATE.save()
        self.assertEqual(len(m.AUDIT_PATH.read_text().splitlines()),1)
        self.assertEqual(len(m.STATE.positions),1)

    def test_explicit_reset_archives_then_resets_all_metrics(self):
        self.position()
        m.STATE.demo_balance_usd=900
        m.STATE.risk_day_start_balance_usd=1000
        m.STATE.history=[{'id':'old','pnl_usd':-100}]
        archive=Path(m.STATE.reset_with_archive())
        old=json.loads((archive/'state.json').read_text())
        self.assertEqual(old['demo_balance_usd'],900)
        self.assertEqual(len(old['positions']),1)
        self.assertEqual(m.STATE.demo_balance_usd,1000)
        self.assertEqual(m.STATE.risk_day_pnl(),0)
        self.assertFalse(m.STATE.history)
        self.assertFalse(m.STATE.positions)
        self.assertTrue((archive/'manifest.json').exists())

    def test_adaptive_candidate_controls_real_exit_path(self):
        self.position()['exit_policy']='adaptive'
        with patch.object(m.paper_quotes,'position_mark',return_value=self.quote),patch.object(self.monitor,'market_context',return_value={'conviction':20}):
            self.monitor.update_positions({A:self.coin})
        self.assertEqual(m.STATE.history[0]['exit_reason'],'CONVICTION_EXIT')
        self.assertEqual(m.STATE.history[0]['exit_policy_version'],exit_policy.ADAPTIVE_VERSION)

    def test_late_and_future_event_not_visible(self):
        rows=[{'address':A,'pairAddress':B,'ts':self.clock[0]-1000,'available_at':self.clock[0]-500,'usd_amount':50,'direction':'BUY','wallet':'one'},
              {'address':A,'pairAddress':B,'ts':self.clock[0]-2000,'available_at':self.clock[0]+1,'usd_amount':999,'direction':'BUY'},
              {'address':A,'pairAddress':B,'ts':self.clock[0]+1,'available_at':self.clock[0]-1,'usd_amount':999,'direction':'BUY'}]
        tape={'events':rows,'pair_coverage':{B:{'status':'COMPLETE','complete_since_ms':self.clock[0]-300000}}}
        with patch.object(m,'read_live_tape',return_value=tape): result=m.STATE.live_flow(A,30,B)
        self.assertEqual(result['trades'],1)
        self.assertEqual(result['buy_usd'],50)
        self.assertEqual(result['quality'],'COMPLETE')

    def test_uncertain_valuations_degrade_instead_of_counting_no_sellers(self):
        row={'event_id':'sig:pool:0','address':A,'pairAddress':B,'ts':self.clock[0]-1000,
             'available_at':self.clock[0]-500,'usd_amount':50,'direction':'SELL','wallet':'one',
             'quality_flags':['QUOTE_ASSET_USD_REFERENCE_ESTIMATE']}
        tape={'events':[row],'pair_coverage':{B:{'status':'COMPLETE','complete_since_ms':self.clock[0]-300000}}}
        with patch.object(m,'read_live_tape',return_value=tape): result=m.STATE.live_flow(A,30,B)
        self.assertEqual(result['quality'],'DEGRADED')
        self.assertFalse(result['fresh'])

    def test_enabled_bridge_refreshes_shared_evidence_and_context(self):
        self.position()
        with patch.object(m.training_bridge,'enabled',return_value=True),patch.object(m.training_bridge,'observe') as observe,\
             patch.object(m.rug_guard,'check',return_value={'status':'pass','checked_at':self.clock[0]}),\
             patch.object(m.price_integrity,'check',return_value={'status':'pass'}),\
             patch.object(self.monitor,'market_context',return_value=self.context),\
             patch.object(m.paper_quotes,'position_mark',return_value=self.quote):
            self.monitor.update_positions({A:self.coin})
        self.assertEqual(observe.call_args.kwargs['context']['conviction'],80)
        self.assertEqual(observe.call_args.kwargs['safety']['checked_at'],self.clock[0])
        self.assertEqual(observe.call_args.kwargs['quotes']['mark']['net_proceeds_usd'],200)

class EffectiveThresholds(unittest.TestCase):
    def test_each_permitted_threshold_changes_actual_mode(self):
        coin={'score':60,'liquidityUsd':4500,'ageMinutes':1,'priceChange':{'m5':5}}
        flow={'trades':1,'unique_wallets':1,'buy_usd':25,'sell_usd':1,'max_sell_usd':1,'buy_sell_usd_ratio':2}
        context={'conviction':35}
        self.assertEqual(flow_policy.entry_mode(coin,flow,context),'ULTRA_EARLY')
        for thresholds in (flow_policy.EntryThresholds(61,4000,30),flow_policy.EntryThresholds(58,4501,30),flow_policy.EntryThresholds(58,4000,36)):
            with self.subTest(thresholds=thresholds): self.assertIsNone(flow_policy.entry_mode(coin,flow,context,thresholds))

    def test_invalid_config_rejected(self):
        for args in ((float('nan'),4000,30),(float('inf'),4000,30),(-1,4000,30),(101,4000,30),(58,0,30),(58,4000,101),(True,4000,30)):
            with self.subTest(args=args),self.assertRaises(ValueError): flow_policy.EntryThresholds(*args)

if __name__=='__main__': unittest.main()
