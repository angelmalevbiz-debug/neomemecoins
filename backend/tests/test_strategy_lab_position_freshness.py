import unittest
from unittest.mock import patch

import strategy_lab as lab


NOW = 1_800_000_000_000
MINT = 'So11111111111111111111111111111111111111112'
PAIR = '8bjgcWvX8nU7m3p2zL4bC6aQ1dF5hK9sT2xY7eR3pLmN'


class MissingMarkFeed:
    def resolve(self, *_args):
        return None


class ExactMarkFeed:
    def resolve(self, _position, _prices, _now):
        return {'address': MINT, 'pairAddress': PAIR, 'priceUsd': 0.00101,
                'liquidityUsd': 50000, 'mark_received_at': NOW,
                'mark_source': 'DEXSCREENER_EXACT_POOL_API'}


def open_position():
    return {
        'symbol': 'MEME', 'address': MINT, 'pairAddress': PAIR,
        'opened_at': NOW - 15 * 60_000, 'updated_at': NOW - 20_000,
        'entry_price': 0.001, 'peak_price': 0.001, 'quantity': 50_000,
        'original_quantity': 50_000, 'notional_usd': 50,
        'remaining_cost_basis_usd': 50.02, 'entry_network_fee_usd': 0.02,
        'open_pnl_usd': -1.25, 'pnl_pct': -2.5,
    }


class StrategyLabPositionFreshnessTests(unittest.TestCase):
    def setUp(self):
        self.original_books = lab.STATE['books']
        self.book = lab.empty_book({'id': 'EARLY', 'name': 'Early Runner'})
        self.book['position'] = open_position()
        lab.STATE['books'] = {'EARLY': self.book}

    def tearDown(self):
        lab.STATE['books'] = self.original_books

    def test_missing_market_price_marks_stale_without_closing_or_reusing_as_current(self):
        with patch.object(lab, 'POSITION_MARK_FEED', MissingMarkFeed()), patch.object(lab, 'now_ms', return_value=NOW):
            lab.update_positions({}, [])
        position = self.book['position']
        self.assertIsNotNone(position)
        self.assertEqual(position['quote_status'], 'stale')
        self.assertEqual(position['open_pnl_usd'], -1.25)
        self.assertEqual(position['quote_unavailable_reason'], 'exact_pool_not_in_recent_entry_feed')
        with patch.object(lab, 'now_ms', return_value=NOW):
            metrics = lab.stats(self.book)
        self.assertTrue(metrics['valuation_stale'])
        self.assertEqual(metrics['mark_age_ms'], 20_000)

    def test_exact_pool_refresh_updates_mark_and_source(self):
        deterministic_exit = {'fill_price': 0.00101, 'net_proceeds_usd': 49.90,
                              'dex_fee_usd': .15, 'network_fee_usd': .02,
                              'impact_pct': .1, 'slippage_pct': .1, 'latency_pct': .1}
        with patch.object(lab, 'POSITION_MARK_FEED', ExactMarkFeed()), \
             patch.object(lab, 'now_ms', return_value=NOW), \
             patch.object(lab, 'exit_execution', return_value=deterministic_exit):
            lab.update_positions({}, [])
        position = self.book['position']
        self.assertIsNotNone(position)
        self.assertEqual(position['current_price'], 0.00101)
        self.assertEqual(position['mark_source'], 'DEXSCREENER_EXACT_POOL_API')
        self.assertEqual(position['quote_status'], 'fresh')


if __name__ == '__main__':
    unittest.main()
