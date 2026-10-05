"""Offline evidence tests: original signal parity, price poisoning, timing, no lookahead."""
import ast,copy,hashlib,random,sys,time,unittest
from pathlib import Path
from unittest.mock import patch
import gold_order_flow as gold
import engine_execution as ex
import pair_price_integrity as pi
import market_monitor as m

A='A'*44;B='B'*44
class GoldParityTests(unittest.TestCase):
 def test_frozen_baseline_ast_hash_and_20000_inputs(self):
  text=(Path(__file__).parent/'fixtures/gold_entry_expression.txt').read_text()
  node=ast.parse(text,mode='eval').body
  # Python 3.13 added show_empty, and 3.14 omits empty fields by default.
  # Preserve the explicit empty-field representation used by Python 3.12.
  dump_options={'show_empty':True} if sys.version_info>=(3,13) else {}
  baseline_dump=ast.dump(node,**dump_options)
  self.assertEqual(hashlib.sha256(baseline_dump.encode()).hexdigest(),'e71bf5406c0a7b31396dd8d1e51bd73d79b46fd96cc9540b7e2ad9b58ea9e906')
  fn=compile(ast.Expression(node),'<original-GOLD>','eval');rnd=random.Random(21)
  for _ in range(20000):
   score=rnd.choice([0,78,84.99,85,90,100]);liquidity=rnd.choice([1000,9999,10000,14999,15000,50000])
   change_m5=rnd.choice([-6,-5,0,25,26]);flow={'trades':rnd.choice([0,2,3,9]),'buy_sell_usd_ratio':rnd.choice([1.29,1.3,3]),'unique_wallets':rnd.choice([0,1,5]),'max_sell_usd':rnd.choice([100,749.9,750,850]),'buy_usd':rnd.choice([10,500,1000,10000])}
   conviction=rnd.choice([35,74.9,75,100])
   expected=bool(eval(fn,{'max':max},locals()) and conviction>=75)
   oracle=(score>=85 and liquidity>=10000 and -5<=change_m5<=25 and flow['trades']>=3 and flow['buy_sell_usd_ratio']>=1.3 and flow['unique_wallets']>=1 and flow['max_sell_usd']<max(750.,flow['buy_usd']*.8) and conviction>=75)
   self.assertEqual(oracle,expected)
  self.assertEqual(gold.SOURCE_COMMIT,'EARLY_ORDER_FLOW_REPAIR_2026_10_05')
 def test_gold_tag_contains_known_nonphysical_stop_clamp(self):
  text=(Path(__file__).parent/'fixtures/gold_stop_clamp.txt').read_text()
  self.assertIn('pnl_pct = -STOP_LOSS_PCT',text)
  current=(Path(m.__file__)).read_text()
  self.assertNotIn('pnl_pct = -STOP_LOSS_PCT',current)
 def test_latest_user_risk_uses_dynamic_five_net_budget(self):
  self.assertEqual((m.STOP_LOSS_PCT,m.TAKE_PROFIT_PCT,m.TRADE_NOTIONAL_USD,m.MAX_DAILY_LOSS_USD),(5,10,200,0))

class PriceTests(unittest.TestCase):
 def setUp(self):
  self.t=pi.now();self.coin={'address':A,'pairAddress':B,'priceUsd':.0008407}
  self.ref={'mint':A,'pair':B,'price_usd':.000008388,'received_at':self.t}
 def test_actual_xfun_hundredfold_fixture_blocked(self):
  r=pi.validate(self.coin,self.ref,self.t);self.assertEqual(r['status'],'blocked');self.assertGreater(r['divergence_pct'],9900)
 def test_correct_same_pool_price_passes(self):
  self.coin['priceUsd']=.0000083;self.assertEqual(pi.validate(self.coin,self.ref,self.t)['status'],'pass')
 def test_wrong_mint_blocked(self):
  self.ref['mint']=B;self.assertEqual(pi.validate(self.coin,self.ref,self.t)['status'],'blocked')
 def test_pool_case_not_folded(self):
  self.ref['pair']=B.lower();self.assertEqual(pi.validate(self.coin,self.ref,self.t)['status'],'blocked')
 def test_old_reference_never_passes(self):
  self.coin['priceUsd']=self.ref['price_usd'];self.assertNotEqual(pi.validate(self.coin,self.ref,self.t+31000)['status'],'pass')
 def test_missing_price_not_implicitly_valid(self):
  self.ref['price_usd']=None;self.assertNotEqual(pi.validate(self.coin,self.ref,self.t)['status'],'pass')

class TimingTests(unittest.TestCase):
 def setUp(self):
  self.t=ex.stamp();self.first={'token_raw_amount':100000,'input_usdc_raw':200000000,'quoted_at':self.t-6000,'context_slot':100}
  self.final={**self.first,'quoted_at':self.t,'context_slot':112}
  self.sell={'quoted_at':self.t-2100,'provider_expected_usdc':198}
 def test_valid_preflight_and_fresh_final(self):self.assertTrue(ex.consistent_preflight(self.first,self.sell,self.final,self.t))
 def test_price_changed_after_preflight_rejected(self):
  self.final['token_raw_amount']=85000;self.assertFalse(ex.consistent_preflight(self.first,self.sell,self.final,self.t))
 def test_positive_roundtrip_is_not_free_profit(self):
  self.sell['provider_expected_usdc']=206;self.assertFalse(ex.consistent_preflight(self.first,self.sell,self.final,self.t))
 def test_old_buy_cannot_be_booked_after_new_sell(self):
  self.final['quoted_at']=self.t-3000;self.assertFalse(ex.consistent_preflight(self.first,self.sell,self.final,self.t))
 def test_full_raw_evidence_and_final_quantity(self):
  first={**self.first,'raw_quote':{'outAmount':'100000'},'token_raw_expected':100000}
  final={**self.final,'raw_quote':{'outAmount':'100010'},'token_raw_expected':100010}
  sale={**self.sell,'expected_usdc':197.8,'floor_usdc':196,'raw_quote':{'outAmount':'198000000'}}
  with patch.object(ex,'entry_quote',side_effect=[first,final]),patch.object(ex,'exit_quote',return_value=sale):
   q,preview=ex.prepare_entry(A,B,200)
  self.assertEqual(q['raw_quote']['outAmount'],'100010');self.assertEqual(q['preflight_buy_quote']['outAmount'],'100000');self.assertTrue(preview['is_preflight_estimate'])
 def test_queue_wait_not_mistaken_for_quote_age(self):
  data={'inputMint':ex.USDC,'outputMint':A,'inAmount':'1','outAmount':'2','otherAmountThreshold':'1','swapMode':'ExactIn','routePlan':[{}],'priceImpactPct':'0','_received_at':self.t,'_requested_at':self.t-6000,'_queue_ms':5900,'_http_ms':100}
  self.assertTrue(ex.valid(data,ex.USDC,A,1,self.t-6000))
  data['_received_at']=self.t-5000;self.assertFalse(ex.valid(data,ex.USDC,A,1,self.t-6000))

if __name__=='__main__':unittest.main()
