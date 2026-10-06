"""Offline contract tests for the explicitly requested active paper engine."""
import copy,json,os,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
T=tempfile.TemporaryDirectory()
os.environ['NEO_MARKET_STATE_PATH']=str(Path(T.name)/'state.json')
os.environ['NEO_MARKET_AUDIT_PATH']=str(Path(T.name)/'audit.jsonl')
os.environ['NEO_RISK_CACHE_DIR']=str(Path(T.name)/'risk')
import market_monitor as m
import engine_rug_guard as rg
import engine_execution as ex

A='A'*44; B='B'*44
class RiskTests(unittest.TestCase):
 def setUp(self):
  self.account={'owner':next(iter(rg.TOKEN_PROGRAMS)),'data':{'parsed':{'type':'mint','info':{'isInitialized':True,'supply':'1000000000','decimals':6,'mintAuthority':None,'freezeAuthority':None,'extensions':[]}}}}
  self.report={'mint':A,'rugged':False,'risks':[],'markets':[{'pubkey':B,'mintA':A,'mintB':'Q'*44,'liquidityA':'V'*44,'lp':{'lpLockedPct':100}}],'topHolders':[{'address':'C'*44,'owner':'D'*44,'pct':5},{'address':'V'*44,'owner':B,'pct':70}],'graphInsidersDetected':0}
 def assess(self): return rg.assess(A,B,self.account,self.report)
 def test_real_schema_and_pool_vault_exclusion(self):
  x=self.assess();self.assertEqual(x['status'],'pass');self.assertEqual(x['metrics']['reported_top10_pct'],5)
 def test_active_freeze_blocked(self):
  self.account['data']['parsed']['info']['freezeAuthority']='X';self.assertIn('freeze_authority',self.assess()['reasons'])
 def test_active_mint_blocked(self):
  self.account['data']['parsed']['info']['mintAuthority']='X';self.assertIn('mint_authority',self.assess()['reasons'])
 def test_missing_authority_not_treated_as_revoked(self):
  del self.account['data']['parsed']['info']['mintAuthority'];self.assertIn('mint_authority',self.assess()['reasons'])
 def test_token2022_dangerous_extensions(self):
  for name in ['permanentDelegate','transferHook','transferFeeConfig','nonTransferable','pausableConfig','unknown']:
   self.account['data']['parsed']['info']['extensions']=[{'extension':name}];self.assertEqual(self.assess()['status'],'blocked')
 def test_metadata_extensions_allowed(self):
  self.account['data']['parsed']['info']['extensions']=[{'extension':'metadataPointer'},{'extension':'tokenMetadata'}];self.assertEqual(self.assess()['status'],'pass')
 def test_report_critical_blocked(self):
  self.report['risks']=[{'name':'Critical','level':'danger'}];self.assertIn('rugcheck_critical',self.assess()['reasons'])
 def test_rugged_blocked(self):
  self.report['rugged']=True;self.assertEqual(self.assess()['status'],'blocked')
 def test_lp_unlocked_blocked(self):
  self.report['markets'][0]['lp']['lpLockedPct']=20;self.assertIn('lp_control_risk',self.assess()['reasons'])
 def test_owner_concentration_aggregates_accounts(self):
  self.report['topHolders']=[{'address':'E'*44,'owner':'D'*44,'pct':12},{'address':'F'*44,'owner':'D'*44,'pct':12}];self.assertIn('holder_concentration',self.assess()['reasons'])
 def test_report_for_wrong_mint_rejected(self):
  self.report['mint']=A.lower();self.assertEqual(self.assess()['status'],'blocked')
 def test_linked_insiders_blocked(self):
  self.report['graphInsidersDetected']=50;self.assertIn('reported_linked_insiders',self.assess()['reasons'])
 def test_missing_holder_data_does_not_pass(self):
  self.report.pop('topHolders');self.assertIn('holders_unverified',self.assess()['reasons'])

class ExitTests(unittest.TestCase):
 def setUp(self):
  m.STATE_PATH.unlink(missing_ok=True)
  m.STATE=m.State();self.mon=m.Monitor();m.STATE.demo_balance_usd=1000;m.STATE.demo_session_id='KEEP'
  self.coin={'address':A,'pairAddress':B,'priceUsd':2,'priceNative':.02,'liquidityUsd':100000,'updatedAt':m.now_ms()}
  self.pos={'id':'open-1','address':A,'pairAddress':B,'symbol':'OFFLINE','entry_price':2,'current_price':2,'quantity':100,'notional_usd':200,'entry_network_fee_usd':.03,'entry_account_reserve_usd':.2,'entry_liquidity_usd':100000,'opened_at':m.now_ms(),'jupiter_token_raw_amount':100000000,'execution_mode':'JUPITER_QUOTE_V2','coin_snapshot':copy.deepcopy(self.coin)}
  m.STATE.positions=[copy.deepcopy(self.pos)]
  self.q={'net_proceeds_usd':200,'gross_proceeds_usd':200.03,'network_fee_usd':.03,'dex_fee_usd':0,'fill_price':2,'impact_pct':.2,'slippage_pct':.1,'latency_pct':0,'quoted_at':m.now_ms(),'from_cache':False,'execution_source':'OFFLINE_FIXTURE'}
  self.mark=patch.object(m.paper_quotes,'position_mark',return_value=self.q).start();self.audit=patch.object(m,'append_audit').start()
  self.addCleanup(patch.stopall)
  self.addCleanup(self.mon.stop)
 def test_fixed_net_contract(self):
  self.assertEqual((m.STOP_LOSS_PCT,m.TAKE_PROFIT_PCT,m.TRADE_NOTIONAL_USD,m.MAX_DAILY_LOSS_USD),(5,10,200,100))
 def test_profit_requires_ten_net_not_chart(self):
  self.coin['priceUsd']=20;self.mon.update_positions({A:self.coin});self.assertEqual(len(m.STATE.positions),1)
 def test_net_ten_closes(self):
  self.q['net_proceeds_usd']=221;self.mon.update_positions({A:self.coin});self.assertFalse(m.STATE.positions);self.assertEqual(m.STATE.history[0]['exit_reason'],'TAKE_PROFIT_10_NET')
 def test_stop_gap_books_observed_proceeds_without_cap(self):
  self.q['net_proceeds_usd']=185;self.mon.update_positions({A:self.coin});closed=m.STATE.history[0]
  self.assertEqual(closed['exit_reason'],'STOP_LOSS_NET_TARGET')
  self.assertAlmostEqual(closed['pnl_pct'],-7.615)
  self.assertAlmostEqual(m.STATE.demo_balance_usd,984.77)
  self.assertFalse(closed['paper_stop_capped'])
  self.assertEqual(closed['exit_net_proceeds_usd'],185)
 def test_executable_mark_above_four_percent_loss_does_not_stop(self):
  self.q['net_proceeds_usd']=195.0;self.mon.update_positions({A:self.coin});self.assertEqual(len(m.STATE.positions),1)
 def test_stop_buffer_before_five(self):
  self.q['net_proceeds_usd']=190.8;self.mon.update_positions({A:self.coin});self.assertEqual(len(m.STATE.positions),1)
 def test_missing_quote_keeps_pending_stop_no_fake_close(self):
  m.STATE.positions[0]['pending_exit_reason']='STOP_LOSS_5_NET_TARGET';self.mark.return_value=None
  self.mon.update_positions({A:self.coin});self.assertEqual(m.STATE.demo_balance_usd,1000);self.assertEqual(m.STATE.positions[0]['pending_exit_reason'],'STOP_LOSS_5_NET_TARGET');self.assertFalse(m.STATE.history)
 def test_legacy_chart_trigger_cannot_close_before_net_stop(self):
  m.STATE.positions[0]['stop_signal_trigger_pct']=-3.5
  self.coin['priceUsd']=1.929
  self.q['net_proceeds_usd']=196.0
  self.mon.update_positions({A:self.coin})
  self.assertEqual(len(m.STATE.positions),1)
  self.assertFalse(m.STATE.history)
  self.assertEqual(m.STATE.positions[0]['legacy_chart_stop_ignored'],-3.5)

 def test_liquidity_collapse_emergency(self):
  self.coin['liquidityUsd']=10000;self.mon.update_positions({A:self.coin});self.assertEqual(m.STATE.history[0]['exit_reason'],'LIQUIDITY_EMERGENCY')
 def test_no_reset_and_ledger_reconciles(self):
  m.STATE.history=[{'id':'old','pnl_usd':5}];self.q['net_proceeds_usd']=221;self.mon.update_positions({A:self.coin})
  self.assertEqual(m.STATE.demo_session_id,'KEEP');self.assertEqual(m.STATE.history[-1],{'id':'old','pnl_usd':5});self.assertAlmostEqual(m.STATE.demo_balance_usd,1020.77)
 def test_reset_race_cannot_book_old_quote(self):
  def reset(*a,**kw): m.STATE.demo_session_id='NEW';m.STATE.positions=[];return self.q
  self.mark.side_effect=reset;self.q['net_proceeds_usd']=221;self.mon.update_positions({A:self.coin});self.assertEqual(m.STATE.demo_balance_usd,1000);self.assertFalse(m.STATE.history)
 def test_http_not_under_state_lock(self):
  def observed(*a,**kw): self.assertFalse(m.STATE.lock._is_owned());return self.q
  self.mark.side_effect=observed;self.mon.fast_position_check()

class QuoteTests(unittest.TestCase):
 def q(self): return {'inputMint':ex.USDC,'outputMint':A,'inAmount':'200000000','outAmount':'100000000','otherAmountThreshold':'99000000','swapMode':'ExactIn','priceImpactPct':'0.01','routePlan':[{'swapInfo':{'inputMint':ex.USDC,'outputMint':A,'ammKey':B}}]}
 def test_case_sensitive_pool_check(self):
  self.assertTrue(ex.same_token_pool(self.q(),A,B));self.assertFalse(ex.same_token_pool(self.q(),A,B.lower()))
 def test_mixed_wrong_pool_rejected(self):
  q=self.q();q['routePlan'].append({'swapInfo':{'inputMint':ex.USDC,'outputMint':A,'ammKey':'C'*44}});self.assertFalse(ex.same_token_pool(q,A,B))
 def test_wrong_raw_amount_rejected(self):
  self.assertFalse(ex.valid(self.q(),ex.USDC,A,123,ex.stamp()))
 def test_stale_quote_rejected(self):
  self.assertFalse(ex.valid(self.q(),ex.USDC,A,200000000,ex.stamp()-15000))
 def test_buffer_not_slippage_floor(self):
  with patch.object(ex.transport,'quote',return_value=self.q()):
   q=ex.entry_quote(A,B,200)
  self.assertEqual(q['token_raw_amount'],99900000);self.assertEqual(q['token_raw_expected'],100000000);self.assertEqual(q['token_raw_floor'],99000000)
 def test_no_stale_cache_on_failure(self):
  ex._CACHE[(A,B,1)]={'quoted_at':ex.stamp()-99999,'net_proceeds_usd':999}
  with patch.object(ex,'exit_quote',return_value=None):
   self.assertIsNone(ex.position_mark({'address':A,'pairAddress':B,'jupiter_token_raw_amount':1},{},.03))

if __name__=='__main__': unittest.main(verbosity=2)
