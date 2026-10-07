"""Broad market candidates still need proven flow; learning is sample-bounded."""
import unittest

import winner_ensemble as ensemble


class WinnerEnsembleLearningTests(unittest.TestCase):
    def closed_trade(self, index, pnl=-1, strategies=None, **fields):
        return {
            'id': f'paper-test:{index}',
            'closed_at': 1_790_000_000_000 + index * 1000,
            'exit_state': 'CLOSED',
            'entry_policy_version': ensemble.ENTRY_POLICY_VERSION,
            'strategy_matches': strategies if strategies is not None else ['VERIFIED_FLOW_MOMENTUM'],
            'pnl_usd': pnl,
            'exit_reason': 'STOP_LOSS_NET_TARGET',
            **fields,
        }

    def setUp(self):
        self.coin = {
            'address': 'A' * 44,
            'pairAddress': 'B' * 44,
            'score': 80,
            'liquidityUsd': 12_000,
            'marketCap': 100_000,
            'ageMinutes': 30,
            'priceChange': {'m5': 5, 'h1': 10},
            'txns': {'m5': {'buys': 10, 'sells': 5}},
            'volume': {'h1': 6_000},
        }
        self.flow = {
            'quality': 'COMPLETE', 'trades': 3,
            'buy_sell_usd_ratio': 2, 'buy_usd': 300, 'sell_usd': 150,
            'unique_wallets': 2, 'max_sell_usd': 100,
            'verified_flow': {
                'source': 'CONFIRMED_PUMPSWAP_WINDOW',
                'coverage_status': 'COMPLETE', 'window_ms': 30_000,
                'address': 'A' * 44, 'pairAddress': 'B' * 44,
                'trades': 3, 'unique_wallets': 2, 'buy_usd': 300, 'sell_usd': 150,
            },
        }

    def test_broad_market_candidate_waits_for_confirmed_flow(self):
        self.assertEqual(ensemble.market_candidates(self.coin), ['VERIFIED_FLOW_MOMENTUM'])
        self.assertEqual(ensemble.matches(self.coin), [])
        self.assertEqual(ensemble.matches(self.coin, self.flow), ['VERIFIED_FLOW_MOMENTUM'])

    def test_weak_flow_does_not_match_broad_strategy(self):
        weak = {**self.flow, 'unique_wallets': 1, 'buy_sell_usd_ratio': 1.0}
        self.assertEqual(ensemble.matches(self.coin, weak), [])

    def test_small_loss_sample_is_reported_but_not_throttled(self):
        history = [self.closed_trade(index) for index in range(11)]
        matches, learning = ensemble.apply_learning(['VERIFIED_FLOW_MOMENTUM'], history)
        self.assertEqual(matches, ['VERIFIED_FLOW_MOMENTUM'])
        self.assertEqual(learning['strategies']['VERIFIED_FLOW_MOMENTUM']['closed_trades'], 11)
        self.assertFalse(learning['strategies']['VERIFIED_FLOW_MOMENTUM']['throttled'])

    def test_repeated_net_losses_are_attributed_and_throttled_after_minimum_sample(self):
        history = [self.closed_trade(index) for index in range(12)]
        matches, learning = ensemble.apply_learning(['VERIFIED_FLOW_MOMENTUM'], history)
        self.assertEqual(matches, [])
        self.assertEqual(learning['throttled_strategies'], ['VERIFIED_FLOW_MOMENTUM'])
        self.assertEqual(learning['strategies']['VERIFIED_FLOW_MOMENTUM']['loss_reasons'], {
            'STOP_LOSS_NET_TARGET': 12,
        })
        self.assertEqual(learning['strategies']['VERIFIED_FLOW_MOMENTUM']['recovery_requirement'],
                         'REVIEWED_POLICY_OR_NEW_VALID_OVERLAPPING_OUTCOMES')

    def test_outcomes_from_another_policy_version_do_not_train_current_rules(self):
        history = [self.closed_trade(index, -100, entry_policy_version='WINNER_ENSEMBLE_VERIFIED_ENTRY_V2')
                   for index in range(20)]
        learning = ensemble.learning_snapshot(history)
        self.assertEqual(learning['closed_trades'], 0)
        self.assertEqual(learning['throttled_strategies'], [])
        self.assertEqual(learning['ignored_data']['other_policy'], 20)

    def test_duplicate_rule_names_do_not_inflate_rule_sample(self):
        learning = ensemble.learning_snapshot([self.closed_trade(
            0, strategies=['VERIFIED_FLOW_MOMENTUM'] * 12)])
        rule = learning['strategies']['VERIFIED_FLOW_MOMENTUM']
        self.assertEqual(rule['closed_trades'], 1)
        self.assertFalse(rule['throttled'])
        self.assertEqual(learning['ignored_data']['duplicate_strategy_match'], 11)

    def test_repeated_durable_close_id_is_counted_once(self):
        trade = self.closed_trade(0)
        learning = ensemble.learning_snapshot([dict(trade) for _ in range(12)])
        self.assertEqual(learning['closed_trades'], 1)
        self.assertEqual(learning['strategies']['VERIFIED_FLOW_MOMENTUM']['closed_trades'], 1)
        self.assertEqual(learning['ignored_data']['duplicate_close_id'], 11)
        self.assertEqual(learning['throttled_strategies'], [])

    def test_conflicting_copies_of_one_close_are_excluded(self):
        learning = ensemble.learning_snapshot([self.closed_trade(0, -1), self.closed_trade(0, 2)])
        self.assertEqual(learning['closed_trades'], 0)
        self.assertEqual(learning['ignored_data']['conflicting_close_ids'], 1)

    def test_unknown_pnl_never_counts_toward_minimum_sample(self):
        history = [self.closed_trade(index) for index in range(11)]
        invalid_pnls = [None, float('nan'), float('inf'), '-1', True, 10 ** 1000]
        history += [self.closed_trade(index + 11, pnl) for index, pnl in enumerate(invalid_pnls)]
        learning = ensemble.learning_snapshot(history)
        self.assertEqual(learning['closed_trades'], 11)
        self.assertEqual(learning['ignored_data']['unknown_pnl'], len(invalid_pnls))
        self.assertEqual(learning['throttled_strategies'], [])

    def test_malformed_records_and_rule_items_are_reported_without_crashing(self):
        history = [None, 'broken', self.closed_trade(1, strategies={}),
                   self.closed_trade(2, strategies=[{}, 'UNKNOWN', 'EARLY'])]
        learning = ensemble.learning_snapshot(history)
        self.assertEqual(learning['closed_trades'], 2)
        self.assertEqual(learning['strategies']['EARLY']['closed_trades'], 1)
        self.assertEqual(learning['strategies']['VERIFIED_FLOW_MOMENTUM']['closed_trades'], 0)
        self.assertEqual(learning['ignored_data']['invalid_record'], 2)
        self.assertEqual(learning['ignored_data']['invalid_strategy_matches'], 1)
        self.assertEqual(learning['ignored_data']['invalid_strategy_match'], 2)

    def test_missing_identity_and_unclosed_rows_are_not_outcomes(self):
        history = [self.closed_trade(0, id=None), self.closed_trade(1, id=''),
                   self.closed_trade(2, closed_at=None), self.closed_trade(3, closed_at=float('inf')),
                   self.closed_trade(4, closed_at=10 ** 1000), self.closed_trade(5, exit_state='OPEN')]
        learning = ensemble.learning_snapshot(history)
        self.assertEqual(learning['closed_trades'], 0)
        self.assertEqual(learning['ignored_data']['missing_close_id'], 2)
        self.assertEqual(learning['ignored_data']['invalid_closed_outcome'], 4)

    def test_statistics_use_latest_100_outcomes_by_close_time(self):
        history = [self.closed_trade(index, -1 if index < 50 else 1) for index in range(150)]
        learning = ensemble.learning_snapshot(history)
        rule = learning['strategies']['VERIFIED_FLOW_MOMENTUM']
        self.assertEqual(learning['closed_trades'], 100)
        self.assertEqual(learning['valid_policy_closed_trades'], 150)
        self.assertEqual(rule['closed_trades'], 100)
        self.assertEqual(rule['wins'], 100)
        self.assertEqual(rule['oldest_close_at'], self.closed_trade(50)['closed_at'])
        self.assertEqual(rule['latest_close_at'], self.closed_trade(149)['closed_at'])
        self.assertEqual(learning['throttled_strategies'], [])

    def test_each_rule_has_its_own_recent_100_close_window(self):
        history = [self.closed_trade(index, -1) for index in range(100)]
        history += [self.closed_trade(index, 1, strategies=['EARLY']) for index in range(100, 200)]
        learning = ensemble.learning_snapshot(history)
        self.assertEqual(learning['wins'], 100)
        self.assertEqual(learning['strategies']['VERIFIED_FLOW_MOMENTUM']['closed_trades'], 100)
        self.assertTrue(learning['strategies']['VERIFIED_FLOW_MOMENTUM']['throttled'])
        self.assertEqual(learning['strategies']['EARLY']['closed_trades'], 100)
        self.assertFalse(learning['strategies']['EARLY']['throttled'])

    def test_actual_overlapping_outcomes_are_available_to_held_rule(self):
        history = [self.closed_trade(index) for index in range(12)]
        history += [self.closed_trade(index, 1, strategies=['EARLY'],
                    strategy_matches_at_entry=['VERIFIED_FLOW_MOMENTUM', 'EARLY'])
                    for index in range(12, 24)]
        learning = ensemble.learning_snapshot(history)
        broad = learning['strategies']['VERIFIED_FLOW_MOMENTUM']
        self.assertEqual(broad['closed_trades'], 24)
        self.assertEqual(broad['wins'], 12)
        self.assertFalse(broad['throttled'])
        self.assertEqual(learning['strategies']['EARLY']['closed_trades'], 12)

    def test_breakeven_is_separate_from_loss_count(self):
        learning = ensemble.learning_snapshot([self.closed_trade(0, -1),
                    self.closed_trade(1, 0), self.closed_trade(2, 1)])
        self.assertEqual(learning['losses'], 1)
        self.assertEqual(learning['breakeven'], 1)
        self.assertEqual(learning['wins'], 1)
        self.assertEqual(learning['strategies']['VERIFIED_FLOW_MOMENTUM']['losses'], 1)


if __name__ == '__main__':
    unittest.main()
