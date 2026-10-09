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
                'quote_status': 'stale', 'quote_age_ms': 22_000,
                'mark_source': 'DEXSCREENER_EXACT_POOL_API',
            }}}
        })

        position = state['books']['SCALPER']['position']
        self.assertEqual(position['pairAddress'], 'pair')
        self.assertEqual(position['current_price'], 0.021)
        self.assertEqual(position['entry_dex_fee_usd'], 0.3)
        self.assertEqual(position['quote_status'], 'stale')
        self.assertEqual(position['quote_age_ms'], 22_000)
        self.assertEqual(position['mark_source'], 'DEXSCREENER_EXACT_POOL_API')

    def test_execution_note_and_basis_are_projected(self):
        projected = compact_strategy_lab({
            'execution_basis': 'DEX_SPOT_MODELED_COSTS_V2',
            'execution_note': 'paper estimate only',
        })
        self.assertEqual(projected['execution_basis'], 'DEX_SPOT_MODELED_COSTS_V2')
        self.assertEqual(projected['execution_note'], 'paper estimate only')

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

    def test_projects_funded_entry_blockers_and_prospective_policy(self):
        state = compact_strategy_lab({
            'activity_config': {'promoted_entry_policy': {
                'version': 'PROMOTED_EVIDENCE_COST_V1', 'profitability_proven': False}},
            'books': {'EARLY': {'portfolio_group': 'PROMOTED_PAPER', 'entry_diagnostics': {
                'promoted_policy_version': 'PROMOTED_EVIDENCE_COST_V1',
                'promoted_flow_rejected': 2, 'promoted_safety_rejected': 1,
                'promoted_max_entry_roundtrip_cost_pct': 1.5,
                'blocked_reason': 'promoted_verified_flow_unavailable'}}},
        })
        entry = state['books']['EARLY']['entry_diagnostics']
        self.assertEqual(entry['promoted_flow_rejected'], 2)
        self.assertEqual(entry['promoted_safety_rejected'], 1)
        self.assertEqual(entry['promoted_max_entry_roundtrip_cost_pct'], 1.5)
        self.assertFalse(state['activity_config']['promoted_entry_policy']['profitability_proven'])

    def test_projects_lab_active_v6_cost_cap_headroom_and_cost_first_diagnostics(self):
        state = compact_strategy_lab({'books': {
            'TREND': {
                'portfolio_group': 'TEST',
                'position': {'symbol': 'T', 'entry_roundtrip_pnl_pct': -1.1,
                             'entry_cost_cap_pct': 1.5, 'stop_loss_net_pct': 3.0,
                             'stop_headroom_pct': 1.9, 'entry_features': {'private': 1}},
                'history': [{'trade_no': 1, 'entry_cost_cap_pct': 1.5, 'stop_headroom_pct': 1.7,
                             'entry_policy_version': 'LAB_ACTIVE_V6_STOP_BUDGET_COST_CAP'}],
                'entry_diagnostics': {
                    'signal_candidates': 3, 'cost_rejected': 3, 'cost_infeasible_candidates': 3,
                    'max_entry_roundtrip_cost_pct': 1.5, 'stop_loss_net_pct': 3.0,
                    'entry_policy_version': 'LAB_ACTIVE_V6_STOP_BUDGET_COST_CAP',
                    'blocked_reason': 'modeled_roundtrip_cost_limit',
                    'cost_feasibility': {'checked_market_candidates': 3,
                                         'fixed_cost_infeasible_candidates': 3,
                                         'minimum_model_roundtrip_cost_pct': 2.7,
                                         'maximum_roundtrip_cost_pct': 1.5}}},
            'COST_FIRST_SCALED': {'portfolio_group': 'TEST', 'entry_diagnostics': {
                'cost_first': {'universe_candidates': 2, 'universe_rejections': {'fee_tier_above_maximum': 9}},
                'evidence_guard_version': 'PROMOTED_EVIDENCE_COST_V1'}},
        }})
        trend = state['books']['TREND']
        self.assertEqual(trend['position']['stop_headroom_pct'], 1.9)
        self.assertEqual(trend['position']['entry_cost_cap_pct'], 1.5)
        self.assertNotIn('entry_features', trend['position'])
        self.assertEqual(trend['history'][0]['stop_headroom_pct'], 1.7)
        diagnostics = trend['entry_diagnostics']
        self.assertEqual(diagnostics['cost_infeasible_candidates'], 3)
        self.assertEqual(diagnostics['max_entry_roundtrip_cost_pct'], 1.5)
        self.assertEqual(diagnostics['blocked_reason'], 'modeled_roundtrip_cost_limit')
        self.assertEqual(diagnostics['cost_feasibility']['minimum_model_roundtrip_cost_pct'], 2.7)
        scaled = state['books']['COST_FIRST_SCALED']['entry_diagnostics']
        self.assertEqual(scaled['cost_first']['universe_rejections'], {'fee_tier_above_maximum': 9})
        self.assertEqual(scaled['evidence_guard_version'], 'PROMOTED_EVIDENCE_COST_V1')

    def test_test_books_publish_scalar_cost_feasibility_and_funded_books_keep_candidates(self):
        summary = {'basis': 'OPTIMISTIC_PAPER_FEE_AND_BUFFER_MODEL', 'is_execution_quote': False,
                   'profitability_proven': False, 'excluded_costs': ['price_impact'],
                   'checked_market_candidates': 40, 'fixed_cost_infeasible_candidates': 40,
                   'maximum_roundtrip_cost_pct': 1.5, 'minimum_model_roundtrip_cost_pct': 2.4,
                   'best_candidates': [{'symbol': 'C%d' % index, 'model_cost_feasible': False}
                                       for index in range(5)]}
        source = {'books': {
            'TREND': {'portfolio_group': 'TEST', 'entry_diagnostics': {'cost_feasibility': summary}},
            'COST_FIRST_CONTROL': {'portfolio_group': 'TEST',
                                   'entry_diagnostics': {'cost_feasibility': summary}},
            'EARLY': {'portfolio_group': 'PROMOTED_PAPER', 'entry_diagnostics': {
                'cost_feasibility': summary, 'promoted_cost_feasibility': summary}},
        }}
        state = compact_strategy_lab(source)
        for key in ('TREND', 'COST_FIRST_CONTROL'):
            projected = state['books'][key]['entry_diagnostics']['cost_feasibility']
            self.assertNotIn('best_candidates', projected)
            self.assertEqual(projected, {k: v for k, v in summary.items() if k != 'best_candidates'})
        early = state['books']['EARLY']['entry_diagnostics']
        self.assertEqual(len(early['cost_feasibility']['best_candidates']), 5)
        self.assertEqual(len(early['promoted_cost_feasibility']['best_candidates']), 5)
        # The projection never mutates the source state.
        self.assertEqual(len(source['books']['TREND']['entry_diagnostics']['cost_feasibility']['best_candidates']), 5)

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

    def test_passes_the_high_frequency_view_through_unchanged(self):
        # LAB_HIGH_FREQUENCY_V1: the HF container builds its own bounded view (<= 16 KB);
        # the projection passes it through and never invents one.
        view = {'version': 'LAB_HIGH_FREQUENCY_V1', 'badge': 'ЕКСПЕРИМЕНТ · ОЧАКВА СЕ ЗАГУБА',
                'books': [{'id': 'HF_RND_E95', 'status': 'active', 'last_closes': [{'pnl_usd': -0.7}]}]}
        state = compact_strategy_lab({'books': {}, 'high_frequency': view})
        self.assertEqual(state['high_frequency'], view)
        self.assertNotIn('high_frequency', compact_strategy_lab({'books': {}}))
        self.assertNotIn('high_frequency', compact_strategy_lab({'books': {}, 'high_frequency': ['bad']}))


if __name__ == '__main__':
    unittest.main()
