"""Planning cannot authorize trades, destroy exit coverage, or churn leases."""
import unittest
from unittest.mock import patch

import live_tape as tape
import paper_market_feasibility as feasibility
import strategy_lab as lab
from tape_pool_scheduler import TapePoolScheduler
import entry_defense as _isolation_entry_defense
import live_tape as _isolation_live_tape
import tape_pool_scheduler as _isolation_tape

_DEFENSIVE_ISOLATION = []


def _defensive_pass(*_args, **_kwargs):
    return _isolation_entry_defense.pass_decision('TEST_GATE_ISOLATION')


def _path_less_layers():
    """Path-less layers for the module globals that otherwise build one from the env paths."""
    return (
        (_isolation_live_tape, '_POOL_SCHEDULER', _isolation_tape.TapePoolScheduler()),
    )


def setUpModule():
    """These tests isolate other entry gates. DEFENSIVE_ENTRY_LAYER_V1 (structural rug
    guard, pool loss memory, heat veto and its warm-up) has its own suite in
    tests/test_defensive_entry_layer.py, which proves every path consults it."""
    for target, name in ((_isolation_tape.TapePoolScheduler, 'defensive_entry_decision'),):
        isolation = patch.object(target, name, _defensive_pass)
        isolation.start()
        _DEFENSIVE_ISOLATION.append(isolation)
    # The layer's ticker registry stays in memory here. Run on its own (without
    # scripts/run_python_checks.py), this module must never write a ticker sidecar next
    # to the shell's NEO_STRATEGY_LAB_PATH / NEO_LIVE_TAPE_PATH (or the /var/lib/neo-market
    # defaults): strategy_lab.maybe_open observes through lab.DEFENSE and
    # live_tape.feed_snapshot through the module-level _POOL_SCHEDULER.
    for target, name, value in _path_less_layers():
        isolation = patch.object(target, name, value)
        isolation.start()
        _DEFENSIVE_ISOLATION.append(isolation)


def tearDownModule():
    while _DEFENSIVE_ISOLATION:
        _DEFENSIVE_ISOLATION.pop().stop()


NOW = 1_800_000_000_000


def coin(name, *, cap=10_000_000, liquidity=1_500_000, activity=60):
    return {'address': name + '-mint', 'pairAddress': name + '-pair',
            'symbol': name, 'dexId': 'pumpswap',
            'quoteTokenAddress': feasibility.SOL_QUOTE_MINT,
            'priceUsd': .001, 'priceNative': .001 / 115,
            'score': 98, 'marketCap': cap, 'liquidityUsd': liquidity,
            'ageMinutes': 30, 'updatedAt': NOW,
            'priceChange': {'m5': 5, 'h1': 10},
            'txns': {'m5': {'buys': activity * 2 // 3, 'sells': activity // 3}},
            'volume': {'h1': liquidity * .5}}


class ModelFeasibilityTests(unittest.TestCase):
    def test_small_cap_fixed_fee_cannot_fit_budget_even_at_zero_impact(self):
        candidate = coin('expensive', cap=100_000, liquidity=15_000)
        result = feasibility.execution_feasibility(candidate)
        self.assertFalse(result['model_cost_feasible'])
        self.assertGreater(result['minimum_model_roundtrip_cost_pct'], 1.5)
        self.assertFalse(result['is_execution_quote'])
        self.assertFalse(result['profitability_proven'])
        for notional in (10, 25, 62.5):
            full = feasibility.modeled_roundtrip(candidate, notional)
            self.assertGreater(-full['initial_pnl_pct'], result['minimum_model_roundtrip_cost_pct'])

    def test_low_model_fee_remains_estimate_requiring_actual_costs(self):
        result = feasibility.execution_feasibility(coin('lower'))
        self.assertTrue(result['model_cost_feasible'])
        self.assertEqual(result['reason'], 'variable_costs_and_quotes_required')
        self.assertFalse(result['is_execution_quote'])

    def test_unknown_or_non_sol_ratio_is_not_fabricated_sol_price(self):
        for quote in (None, 'USDC', {'wrong': 'schema'}):
            candidate = coin('unknown')
            candidate['quoteTokenAddress'] = quote
            result = feasibility.execution_feasibility(candidate)
            self.assertIsNone(result['model_cost_feasible'])
            self.assertIsNone(result['minimum_model_roundtrip_cost_pct'])
            self.assertEqual(feasibility.modeled_roundtrip(candidate, 25)['status'], 'unavailable')

    def test_shared_estimate_matches_unchanged_lab_actual_model_at_tier_edges(self):
        for threshold, _ in feasibility.PUMP_FEE_TIERS:
            for offset in (-.1, 0, .1):
                candidate = coin('tier', cap=(threshold + offset) * 115)
                self.assertAlmostEqual(feasibility.pumpswap_fee_bps(candidate), lab.pumpswap_fee_bps(candidate))
                for notional in (10, 62.5):
                    estimate = feasibility.modeled_roundtrip(candidate, notional)
                    entry = lab.entry_execution(candidate, notional)
                    mark = lab.exit_execution(candidate, entry['quantity'])
                    self.assertAlmostEqual(estimate['quantity'], entry['quantity'])
                    self.assertAlmostEqual(estimate['net_proceeds_usd'], mark['net_proceeds_usd'])
                    self.assertAlmostEqual(estimate['initial_pnl_pct'],
                                           100 * (mark['net_proceeds_usd'] - entry['capital_committed_usd']) / notional)


class SchedulerTests(unittest.TestCase):
    def test_estimated_feasible_pool_precedes_unaffordable_high_score_pool(self):
        costly = coin('costly', cap=100_000, liquidity=15_000)
        feasible = coin('feasible', activity=900)
        scheduler = TapePoolScheduler()
        selected, report = scheduler.select({'feed': [costly, feasible]}, now=NOW, max_tracked=1)
        self.assertEqual([row['symbol'] for row in selected], ['feasible'])
        self.assertEqual(report['estimated_fixed_cost_over_budget'], 1)
        self.assertEqual(report['selected_exploration_pools'], 0)

    def test_duplicates_are_removed_before_budget_and_all_exit_pins_survive(self):
        first, second, entry = coin('first'), coin('second', cap=100_000), coin('entry')
        first_position = {'address': first['address'], 'pairAddress': first['pairAddress'],
                          'coin_snapshot': first}
        second_position = {'address': second['address'], 'pairAddress': second['pairAddress'],
                           'dexId': 'pumpswap', 'symbol': 'second'}
        state = {'feed': [entry, entry, first, second], 'positions': [first_position],
                 'strategy_lab': {'books': {'duplicate': {'position': first_position},
                                           'retired': {'position': second_position}}}}
        selected, report = TapePoolScheduler().select(state, now=NOW, max_tracked=1)
        self.assertEqual({row['pairAddress'] for row in selected}, {'first-pair', 'second-pair'})
        self.assertEqual(len(selected), 2)
        self.assertEqual(report['pinned_exit_pools'], 2)
        self.assertEqual(report['entry_capacity'], 0)

    def test_lab_pin_absent_from_market_uses_recorded_dex_identity(self):
        state = {'feed': [], 'strategy_lab': {'books': {
            'retired': {'position': {'address': 'mint', 'pairAddress': 'pool', 'dexId': 'pumpswap'}}}}}
        selected, report = TapePoolScheduler().select(state, now=NOW, max_tracked=1)
        self.assertEqual(selected[0]['pairAddress'], 'pool')
        self.assertEqual(report['pinned_exit_pools'], 1)

    def test_unsupported_held_pool_is_reported_without_claimed_pumpswap_coverage(self):
        position = {'address': 'mint', 'pairAddress': 'ray-pool', 'dexId': 'raydium'}
        selected, report = TapePoolScheduler().select({'positions': [position]}, now=NOW, max_tracked=4)
        self.assertEqual(selected, [])
        self.assertEqual(report['unsupported_held_pools'], 1)

    def test_lease_prevents_churn_then_rotates_fairly_after_full_flow_window(self):
        first, second = coin('A'), coin('B')
        scheduler = TapePoolScheduler()
        state = {'feed': [first, second]}
        selected, _ = scheduler.select(state, now=NOW, max_tracked=1)
        self.assertEqual(selected[0]['symbol'], 'A')
        second['score'] = 100
        selected, _ = scheduler.select(state, now=NOW + 59_999, max_tracked=1)
        self.assertEqual(selected[0]['symbol'], 'A')
        selected, _ = scheduler.select(state, now=NOW + 60_000, max_tracked=1)
        self.assertEqual(selected[0]['symbol'], 'B')
        selected, _ = scheduler.select(state, now=NOW + 120_000, max_tracked=1)
        self.assertEqual(selected[0]['symbol'], 'A')

    def test_infeasible_model_does_not_create_zero_tape_bootstrap(self):
        candidates = [coin('A', cap=100_000, liquidity=15_000),
                      coin('B', cap=150_000, liquidity=20_000)]
        selected, report = TapePoolScheduler().select({'feed': candidates}, now=NOW, max_tracked=1)
        self.assertEqual(len(selected), 1)
        self.assertEqual(report['selected_exploration_pools'], 1)
        self.assertEqual(report['estimated_feasible_market_candidates'], 0)
        self.assertTrue(report['cost_estimates_are_planning_hints'])

    def test_new_affordable_candidate_can_replace_exploration_before_lease_expiry(self):
        expensive = coin('A', cap=100_000, liquidity=15_000)
        affordable = coin('B')
        scheduler = TapePoolScheduler()
        selected, _ = scheduler.select({'feed': [expensive]}, now=NOW, max_tracked=1)
        self.assertEqual(selected[0]['symbol'], 'A')
        selected, _ = scheduler.select({'feed': [expensive, affordable]}, now=NOW + 2_000, max_tracked=1)
        self.assertEqual(selected[0]['symbol'], 'B')

    def test_feed_snapshot_exposes_scheduling_without_sending_any_trade(self):
        state = {'feed': [coin('single')]}
        class Response:
            def raise_for_status(self):
                pass
            def json(self):
                return state
        with patch.object(tape.SESSION, 'get', return_value=Response()), \
                patch.object(tape, '_POOL_SCHEDULER', TapePoolScheduler()), \
                patch.object(tape, 'shared_quote_reference', return_value=None), \
                patch.object(tape, 'now_ms', return_value=NOW):
            rows = tape.feed_snapshot()
        self.assertEqual(len(rows), 1)
        self.assertEqual(tape.STATUS['entry_scheduling']['selected_entry_pools'], 1)
        self.assertFalse(tape.STATUS['entry_scheduling']['model_is_execution_quote'])


if __name__ == '__main__':
    unittest.main()
