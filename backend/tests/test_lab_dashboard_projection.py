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

    def test_projects_funded_cohort_separately_from_testing_books(self):
        state = compact_strategy_lab({
            'portfolio_setup': {'version': 'PROMOTED_PAPER_COHORT_V1', 'total_allocated_capital_usd': 1000},
            'books': {'EARLY': {
                'portfolio_group': 'PROMOTED_PAPER', 'allocation_usd': 250,
                'max_position_fraction': .25, 'starting_balance': 250, 'balance': 250,
            }},
        })
        self.assertEqual(state['portfolio_setup']['total_allocated_capital_usd'], 1000)
        self.assertEqual(state['books']['EARLY']['portfolio_group'], 'PROMOTED_PAPER')
        self.assertEqual(state['books']['EARLY']['allocation_usd'], 250)
        self.assertEqual(state['books']['EARLY']['max_position_fraction'], .25)

    def test_projects_legacy_exit_book_without_internal_position_fields(self):
        state = compact_strategy_lab({'portfolio_setup': {
            'version': 'PROMOTED_PAPER_COHORT_V1',
            'legacy_draining_books': {'EARLY': {
                'id': 'LEGACY_EARLY', 'strategy_id': 'EARLY', 'name': 'Early legacy',
                'starting_balance': 500, 'balance': 500,
                'position': {'symbol': 'OLD', 'address': 'mint', 'pairAddress': 'pair',
                             'notional_usd': 50, 'open_pnl_usd': -1, 'entry_features': {'wallets': ['private']}}
            }}
        }})
        legacy = state['portfolio_setup']['legacy_draining_books']['EARLY']
        self.assertEqual(state['portfolio_setup']['legacy_open_position_count'], 1)
        self.assertEqual(legacy['position']['symbol'], 'OLD')
        self.assertNotIn('entry_features', legacy['position'])


if __name__ == '__main__':
    unittest.main()
