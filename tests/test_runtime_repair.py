"""Offline runtime tests. No network calls and no production account access."""
import json,tempfile,threading,time,unittest
from pathlib import Path
from unittest.mock import patch
import engine_runtime as r
import engine_execution as ex
import market_monitor as m

class Budget(unittest.TestCase):
 def test_full_allowance_retains_user_size(self):self.assertEqual(r.plan_notional(200,1000,100,0,5,.5,.25),200)
 def test_spent_limit_never_reopens(self):
  for pnl in [-100,-110]:self.assertEqual(r.plan_notional(200,900,100,pnl,5,.5,.06),0)
 def test_existing_user_remainder_permits_smaller_order(self):
  pnl=901.8761553-997.46197285
  size=r.plan_notional(200,901.8761553,100,pnl,5,.5,.06)
  self.assertGreater(size,10);self.assertLess(size,200)
  self.assertLessEqual(size*.055+.06,100+pnl)
 def test_fixed_costs_and_cash_reserved(self):
  size=r.plan_notional(200,100,100,0,5,.5,.26)
  self.assertLessEqual(size+.26,100)
 def test_nonfinite_budget_never_passes(self):
  for bad in [float('nan'),float('inf'),'bad']:self.assertEqual(r.plan_notional(200,bad,100,0,5,.5,.03),0)
 def test_latest_explicit_disabled_daily_cap_is_visible(self):
  self.assertEqual((m.STOP_LOSS_PCT,m.TAKE_PROFIT_PCT,m.TRADE_NOTIONAL_USD,m.MAX_DAILY_LOSS_USD),(5,10,200,0))

class Persistence(unittest.TestCase):
 def test_complete_json_replaces_whole_file(self):
  with tempfile.TemporaryDirectory() as t:
   p=Path(t)/'state.json';r.atomic_json(p,{'history':[1]});r.atomic_json(p,{'history':[1,2]})
   self.assertEqual(json.loads(p.read_text()),{'history':[1,2]});self.assertEqual(len(list(Path(t).iterdir())),1)
 def test_failed_serialization_preserves_old_state(self):
  with tempfile.TemporaryDirectory() as t:
   p=Path(t)/'state.json';r.atomic_json(p,{'balance':901})
   with self.assertRaises(ValueError):r.atomic_json(p,{'balance':float('nan')})
   self.assertEqual(json.loads(p.read_text())['balance'],901)
 def test_failed_replace_preserves_old_state(self):
  with tempfile.TemporaryDirectory() as t:
   p=Path(t)/'state.json';r.atomic_json(p,{'balance':901})
   with patch.object(r.os,'replace',side_effect=OSError('interrupted')):
    with self.assertRaises(OSError):r.atomic_json(p,{'balance':1000})
   self.assertEqual(json.loads(p.read_text())['balance'],901)
 def test_unreadable_state_cannot_reset_balance(self):
  with tempfile.TemporaryDirectory() as t:
   p=Path(t)/'state.json';p.write_text('{broken')
   with patch.object(m,'STATE_PATH',p):
    with self.assertRaisesRegex(RuntimeError,'refusing automatic reset'):m.State()
   self.assertEqual(p.read_text(),'{broken')

class Discovery(unittest.TestCase):
 def test_slow_discovery_does_not_block_prices(self):
  entered=threading.Event();release=threading.Event()
  def load():entered.set();release.wait(1);return ['token'],{'token':{}}
  c=r.DiscoveryCache(load)
  try:
   start=time.monotonic();self.assertEqual(c.get(),([],{}));self.assertLess(time.monotonic()-start,.2)
   self.assertTrue(entered.wait(.3));release.set();c.future.result(timeout=.3)
   self.assertEqual(c.get()[0],['token'])
  finally:release.set();c.stop()
 def test_refresh_failure_keeps_prior_list_but_expires(self):
  tick=[0];n=[0]
  def load():
   n[0]+=1
   if n[0]>1:raise RuntimeError('source down')
   return ['token'],{'token':{}}
  c=r.DiscoveryCache(load,clock=lambda:tick[0],refresh_seconds=30,max_age_seconds=300)
  try:
   c.get();c.future.result(timeout=.3);self.assertEqual(c.get()[0],['token'])
   tick[0]=31;self.assertEqual(c.get()[0],['token'])
   try:c.future.result(timeout=.3)
   except RuntimeError:pass
   self.assertEqual(c.get()[0],['token']);tick[0]=301;self.assertEqual(c.get(),([],{}))
  finally:c.stop()

class SaleRouting(unittest.TestCase):
 def q(self):return {'inputMint':'A'*44,'outputMint':ex.USDC,'inAmount':'100','outAmount':'200000000','otherAmountThreshold':'198000000','swapMode':'ExactIn','priceImpactPct':'0.001','routePlan':[{'swapInfo':{'inputMint':'A'*44,'outputMint':ex.USDC,'ammKey':'C'*44}}]}
 def test_same_mint_alternate_exit_is_not_stranded(self):
  with patch.object(ex.transport,'quote',return_value=self.q()):q=ex.exit_quote('A'*44,100,'B'*44)
  self.assertIsNotNone(q);self.assertFalse(q['route_matches_entry_pool']);self.assertEqual(q['token_input_raw'],100)
  self.assertAlmostEqual(q['expected_usdc'],199.8)
 def test_wrong_mint_still_blocked(self):
  q=self.q();q['inputMint']='X'*44
  with patch.object(ex.transport,'quote',return_value=q):self.assertIsNone(ex.exit_quote('A'*44,100,'B'*44))
 def test_wrong_quantity_still_blocked(self):
  q=self.q();q['inAmount']='999'
  with patch.object(ex.transport,'quote',return_value=q):self.assertIsNone(ex.exit_quote('A'*44,100,'B'*44))
 def test_buy_must_still_use_signal_pool(self):
  q=self.q();q.update(inputMint=ex.USDC,outputMint='A'*44,inAmount='200000000');q['routePlan'][0]['swapInfo'].update(inputMint=ex.USDC,outputMint='A'*44)
  with patch.object(ex.transport,'quote',return_value=q):self.assertIsNone(ex.entry_quote('A'*44,'B'*44,200))

if __name__=='__main__':unittest.main()
