import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))

import early_scout as e


class EarlyScoutTests(unittest.TestCase):
    def setUp(self):
        for p in (
            mock.patch.object(e, 'ENABLED', True),
            mock.patch.object(e, 'MIN_MARKET_CAP', 5000.0),
            mock.patch.object(e, 'MAX_MARKET_CAP', 60000.0),
            mock.patch.object(e, 'MAX_AGE_MINUTES', 45.0),
            mock.patch.object(e, 'MIN_SCORE', 0.72),
            mock.patch.object(e, 'MIN_CONFIRMATIONS', 5),
        ):
            p.start(); self.addCleanup(p.stop)

    @staticmethod
    def strong_case():
        now = 100_000
        prices = [1.0,1.002,1.004,1.006,1.008,1.011,1.014,1.018,1.024,1.032,1.043,1.058]
        points=[]
        for i, price in enumerate(prices):
            points.append({'ts':now-(11-i)*6000,'price':price,'liquidity':12000+i*80,'volumeH1':5000+i*120})
        coin={'marketCap':30000,'liquidityUsd':13000,'ageMinutes':12,'priceUsd':1.058,
              'priceChange':{'m5':8},'volume':{'h1':6500}}
        fast={'seconds':30,'quality':'COMPLETE','trades':12,'buys':10,'sells':2,
              'buy_sell_usd_ratio':4.0,'buyer_wallets':7,'unique_wallets':8,
              'repeat_buy_wallets':2,'whale_buy_usd':900,'whale_sell_usd':0}
        slow={'seconds':300,'quality':'COMPLETE','trades':24,'buys':17,'sells':7,'buy_sell_usd_ratio':2.0}
        return now, coin, points, fast, slow

    def test_detects_pre_spike_acceleration(self):
        now, coin, points, fast, slow = self.strong_case()
        result=e.evaluate(coin,points,fast,slow,now_ms=now)
        self.assertTrue(result['allow'])
        self.assertGreaterEqual(result['score'],0.72)
        self.assertGreaterEqual(result['confirmations'],5)
        self.assertTrue(result['confirmation_flags']['price_accelerating'])
        self.assertTrue(result['confirmation_flags']['trade_rate_accelerating'])

    def test_already_vertical_move_is_rejected(self):
        now, coin, points, fast, slow = self.strong_case()
        coin=dict(coin, priceChange={'m5':31})
        result=e.evaluate(coin,points,fast,slow,now_ms=now)
        self.assertFalse(result['allow'])
        self.assertIn('already_overextended',result['vetoes'])

    def test_sell_pressure_is_rejected(self):
        now, coin, points, fast, slow = self.strong_case()
        fast=dict(fast,buy_sell_usd_ratio=0.5,buys=2,sells=9,whale_sell_usd=1200,whale_buy_usd=0)
        result=e.evaluate(coin,points,fast,slow,now_ms=now)
        self.assertFalse(result['allow'])
        self.assertIn('sell_flow_dominant',result['vetoes'])
        self.assertIn('whale_sell_pressure',result['vetoes'])

    def test_market_cap_band_is_enforced(self):
        now, coin, points, fast, slow = self.strong_case()
        coin=dict(coin, marketCap=120000)
        result=e.evaluate(coin,points,fast,slow,now_ms=now)
        self.assertFalse(result['allow'])
        self.assertIn('market_cap_outside_early_band',result['vetoes'])

    def test_insufficient_micro_history_does_not_guess(self):
        now, coin, points, fast, slow = self.strong_case()
        result=e.evaluate(coin,points[-2:],fast,slow,now_ms=now)
        self.assertFalse(result['allow'])
        self.assertIn('insufficient_micro_history',result['vetoes'])

    def test_disabled_scout_never_creates_entry_signal(self):
        now, coin, points, fast, slow = self.strong_case()
        with mock.patch.object(e,'ENABLED',False):
            result=e.evaluate(coin,points,fast,slow,now_ms=now)
        self.assertFalse(result['allow'])
        self.assertFalse(result['enabled'])


if __name__ == '__main__':
    unittest.main()
