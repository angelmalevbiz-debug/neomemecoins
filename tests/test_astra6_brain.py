import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import astra6_brain as a

MINT='HKZDfZnkHZxd9agRDNPyDv4iT6LmAurJnpRtj9wpump'
PAIR='867dKvaCcyRrUDP66bqNXbujXfjaxsNB9wTpc4CmBdAD'
STAMP=1_800_000_000_000

def quote(inp,out,amount,result,entry=False,at=STAMP,pair=PAIR):
    return {'inputMint':inp,'outputMint':out,'inAmount':str(amount),'outAmount':str(result),
        'otherAmountThreshold':str(result*995//1000),'priceImpactPct':'0.001',
        'routePlan':[{'swapInfo':{'ammKey':pair,'inputMint':inp,'outputMint':out}}],
        '_at':at,'contextSlot':123}

def coin(**kw):
    c=dict(address=MINT,pairAddress=PAIR,priceUsd=1.,updatedAt=STAMP,
        liquidityUsd=100000,score=95,ageMinutes=60,symbol='TEST',priceChange={'m5':8,'h1':20})
    c.update(kw);return c

def proposal():
    b=quote(a.USDC,MINT,75_000_000,75_000_000,True)
    raw=a.simulated_raw(b)
    return dict(coin=coin(),quote=b,meta={'decimals':6,'rent_sol':.0015},raw=raw,
        rent_usd=.18,signal={'target_pct':15.})

def opened():
    book=a.empty_book(); p=proposal(); q=quote(MINT,a.USDC,p['raw'],74_750_000)
    pos=a.construct_position(book,p,q,.012,STAMP)
    book['positions'].append(pos); book['trade_seq']=1
    return book,pos

class AstraTests(unittest.TestCase):
    def test_quote_expected_not_slippage_floor_as_fill(self):
        q=quote(MINT,a.USDC,100,100_000_000)
        r=a.proceeds(q,.012)
        self.assertAlmostEqual(r['net_proceeds_usd'],99.888)
        self.assertAlmostEqual(r['conservative_net_usd'],99.488)
        self.assertEqual(r['expected_proceeds_usd'],100)
    def test_raw_quantity_integer_and_decimals(self):
        b,p=opened();self.assertEqual(p['token_raw_amount'],74_925_000)
        self.assertEqual(p['quantity'],74.925)
    def test_entry_cost_includes_rent_network_both_legs(self):
        b,p=opened();self.assertAlmostEqual(p['capital_committed_usd'],75.192)
        self.assertAlmostEqual(p['pnl_usd'],74.75*.999-.012-75.192)
        self.assertLess(a.equity(b),500)
    def test_buy_does_not_change_realized_balance(self):
        b,p=opened();self.assertEqual(b['balance'],500)
        self.assertAlmostEqual(a.available(b),500-75.192)
    def test_stop_gap_is_not_clamped(self):
        b,p=opened();q=quote(MINT,a.USDC,p['token_raw_amount'],68_000_000)
        reason=a.apply_mark(b,p,q,.012,STAMP)
        self.assertEqual(reason,'STOP_LOSS_2_NET')
        self.assertLess(b['history'][0]['pnl_pct'],-9)
        self.assertAlmostEqual(b['balance'],500+68*.999-.012-75.192)
    def test_target_15_is_net(self):
        b,p=opened();q=quote(MINT,a.USDC,p['token_raw_amount'],87_000_000)
        self.assertEqual(a.apply_mark(b,p,q,.012,STAMP),'TAKE_PROFIT_15_NET')
    def test_profit_target_not_from_chart(self):
        b,p=opened();p['current_price']=1000
        q=quote(MINT,a.USDC,p['token_raw_amount'],76_000_000)
        self.assertIsNone(a.apply_mark(b,p,q,.012,STAMP))
    def test_no_pnl_mutation_on_stale_quote(self):
        b,p=opened();before=copy.deepcopy(b)
        q=quote(MINT,a.USDC,p['token_raw_amount'],87_000_000,at=STAMP-9000)
        with self.assertRaises(a.QuoteError):a.apply_mark(b,p,q,.012,STAMP)
        self.assertEqual(b,before)
    def test_quantity_mismatch_cannot_close(self):
        b,p=opened();q=quote(MINT,a.USDC,1,87_000_000)
        with self.assertRaises(a.QuoteError):a.apply_mark(b,p,q,.012,STAMP)
        self.assertEqual(len(b['positions']),1)
    def test_pending_stop_does_not_disappear(self):
        b,p=opened();p['pending_exit_reason']='STOP_LOSS_2_NET'
        q=quote(MINT,a.USDC,p['token_raw_amount'],75_000_000)
        self.assertEqual(a.apply_mark(b,p,q,.012,STAMP),'STOP_LOSS_2_NET')
    def test_costly_entry_rejected(self):
        p=proposal();q=quote(MINT,a.USDC,p['raw'],72_000_000)
        with self.assertRaises(a.QuoteError):a.construct_position(a.empty_book(),p,q,.012,STAMP)
    def test_stale_buy_rejected(self):
        p=proposal();p['quote']['_at']=STAMP-9000
        with self.assertRaises(a.QuoteError):a.construct_position(a.empty_book(),p,quote(MINT,a.USDC,p['raw'],74_750_000),.012,STAMP)
    def test_cash_reserve_cannot_be_reused(self):
        b,p=opened();b['balance']=100
        pp=proposal(); pp['coin']['address']='So11111111111111111111111111111111111111112'
        with self.assertRaises(a.QuoteError):a.construct_position(b,pp,quote(MINT,a.USDC,pp['raw'],74_750_000),.012,STAMP)
    def test_pool_ids_case_sensitive(self):
        q=quote(a.USDC,MINT,100,100,True)
        self.assertTrue(a.valid_route(q,MINT,PAIR,True))
        self.assertFalse(a.valid_route(q,MINT,PAIR.lower(),True))
    def test_split_to_wrong_pool_rejected(self):
        q=quote(a.USDC,MINT,100,100,True)
        q['routePlan'].append({'swapInfo':{'ammKey':'WRONG','outputMint':MINT}})
        self.assertFalse(a.valid_route(q,MINT,PAIR,True))
    def test_signal_target_in_user_range(self):
        f={'buyers':{'a','b','c','d'},'wallets':{'a','b','c','d'},'buy':400,'sell':100,'max_buy':150}
        sig,why=a.candidate(coin(),f,[],STAMP)
        self.assertIsNotNone(sig);self.assertIn(sig['target_pct'],[10,15,20])
    def test_old_feed_and_dust_not_entries(self):
        for c in [coin(updatedAt=STAMP-25000),coin(liquidityUsd=50)]:
            self.assertIsNone(a.candidate(c,{},[],STAMP)[0])
    def test_single_wallet_not_entry(self):
        f={'buyers':{'a'},'wallets':{'a'},'buy':1000,'sell':0,'max_buy':1000}
        self.assertIsNone(a.candidate(coin(),f,[],STAMP)[0])
    def test_cooldown_loss_vs_profit(self):
        b=a.empty_book();b['history']=[dict(address=MINT,closed_at=STAMP-60_000,pnl_usd=-1)]
        self.assertTrue(a.cooldown(b,MINT,STAMP));b['history'][0]['pnl_usd']=1
        self.assertFalse(a.cooldown(b,MINT,STAMP))
    def test_ledger_closed_pnl_reconciles(self):
        b,p=opened();a.apply_mark(b,p,quote(MINT,a.USDC,p['token_raw_amount'],87_000_000),.012,STAMP)
        self.assertAlmostEqual(b['balance'],b['starting_balance']+sum(t['pnl_usd'] for t in b['history']))
    def test_corrupt_file_must_not_reset(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'state.json';path.write_text('broken')
            with self.assertRaises(json.JSONDecodeError):a.Brain(path)
    def test_resume_preserves_capital_positions_history(self):
        b,p=opened()
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'state.json';a.write(path,{'book':b})
            brain=a.Brain(path); self.assertEqual(brain.book,b)
            brain.executor.shutdown()
    def test_constants(self):
        self.assertEqual(a.STOP,2);self.assertEqual(a.MAX_POSITIONS,3)
        self.assertTrue(a.CONFIG['paper_only'])

if __name__=='__main__': unittest.main(verbosity=2)
