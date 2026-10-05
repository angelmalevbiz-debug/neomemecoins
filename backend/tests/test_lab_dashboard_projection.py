import unittest

from lab_dashboard_projection import compact_strategy_lab


class StrategyLabDashboardProjection(unittest.TestCase):
    def test_keeps_recent_closed_trades_and_executable_pair_for_review(self):
        trades = [
            {
                'trade_no': index,
                'symbol': f'T{index}',
                'address': f'mint-{index}',
                'pairAddress': f'pair-{index}',
                'opened_at': index * 100,
                'closed_at': index * 100 + 50,
                'execution_entry_price': 0.01,
                'execution_exit_price': 0.011,
                'entry_dex_fee_usd': 0.3,
                'exit_dex_fee_usd': 0.31,
                'entry_network_fee_usd': 0.02,
                'exit_network_fee_usd': 0.02,
                'entry_price_impact_pct': 0.5,
                'exit_price_impact_pct': 0.6,
                'pnl_usd': 1.0,
                'exit_reason': 'TAKE_PROFIT_10_NET',
                'large_internal_field': 'must not be projected',
            }
            for index in reversed(range(35))
        ]
        state = compact_strategy_lab({'books': {'MOMENTUM': {'history': trades}}})

        projected = state['books']['MOMENTUM']['history']
        self.assertEqual(len(projected), 30)
        self.assertEqual(projected[0]['trade_no'], 34)
        self.assertEqual(projected[-1]['pairAddress'], 'pair-5')
        self.assertEqual(projected[0]['entry_network_fee_usd'], 0.02)
        self.assertEqual(projected[0]['exit_reason'], 'TAKE_PROFIT_10_NET')
        self.assertNotIn('large_internal_field', projected[0])

    def test_keeps_open_position_pair_and_costs(self):
        state = compact_strategy_lab({
            'books': {'SCALPER': {'position': {
                'symbol': 'MEME', 'address': 'mint', 'pairAddress': 'pair',
                'execution_entry_price': 0.02, 'current_price': 0.021,
                'entry_dex_fee_usd': 0.3, 'entry_network_fee_usd': 0.01,
            }}}
        })

        position = state['books']['SCALPER']['position']
        self.assertEqual(position['pairAddress'], 'pair')
        self.assertEqual(position['current_price'], 0.021)
        self.assertEqual(position['entry_dex_fee_usd'], 0.3)


if __name__ == '__main__':
    unittest.main()
