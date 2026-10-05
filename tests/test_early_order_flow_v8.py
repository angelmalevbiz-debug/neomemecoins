import unittest
import gold_order_flow as g


def coin(**kw):
    base={
        'score':70,'liquidityUsd':8000,'ageMinutes':20,
        'priceChange':{'m5':4.0},
    }
    base.update(kw)
    return base

def flow(**kw):
    base={
        'trades':1,'buy_sell_usd_ratio':1.2,'buy_usd':25.0,'sell_usd':5.0,
        'unique_wallets':1,'max_sell_usd':5.0,
    }
    base.update(kw)
    return base

class EarlyOrderFlowTests(unittest.TestCase):
    def test_first_real_impulse_can_enter_ultra_early(self):
        self.assertEqual(g.entry_mode(coin(),flow(),{'conviction':40}),'ULTRA_EARLY')

    def test_ultra_early_does_not_wait_for_mature_score(self):
        c=coin(score=58,liquidityUsd=6000,ageMinutes=1,priceChange={'m5':1})
        self.assertEqual(g.entry_mode(c,flow(buy_usd=20,sell_usd=2),{'conviction':30}),'ULTRA_EARLY')

    def test_two_trade_early_setup_enters(self):
        c=coin(score=74,liquidityUsd=12000,ageMinutes=100,priceChange={'m5':7})
        f=flow(trades=2,buy_sell_usd_ratio=1.3,buy_usd=80,sell_usd=30,unique_wallets=2)
        self.assertEqual(g.entry_mode(c,f,{'conviction':50}),'EARLY')

    def test_later_strong_flow_has_momentum_fallback(self):
        c=coin(score=82,liquidityUsd=20000,ageMinutes=500,priceChange={'m5':8})
        f=flow(trades=4,buy_sell_usd_ratio=1.5,buy_usd=120,sell_usd=50,unique_wallets=3)
        self.assertEqual(g.entry_mode(c,f,{'conviction':65}),'MOMENTUM')

    def test_thin_liquidity_stays_blocked(self):
        self.assertIsNone(g.entry_mode(coin(liquidityUsd=3000),flow(),{'conviction':90}))

    def test_sell_pressure_stays_blocked(self):
        f=flow(buy_sell_usd_ratio=.8,buy_usd=20,sell_usd=60,max_sell_usd=60)
        self.assertIsNone(g.entry_mode(coin(),f,{'conviction':80}))

if __name__=='__main__':
    unittest.main(verbosity=2)
