import copy
import unittest

import promoted_entry_guard as guard


NOW = 1_800_000_000_000
COIN = {'address': 'mint', 'pairAddress': 'pool'}


def features():
    return {'verified_flow': {'address': 'mint', 'pairAddress': 'pool',
                     'source': guard.FLOW_SOURCE, 'coverage_status': 'COMPLETE',
                     'window_ms': guard.FLOW_WINDOW_MS,
                     'window_at': NOW, 'available_at': NOW-100, 'latest_event_at': NOW-200,
                     'trades': 4, 'unique_wallets': 3, 'buy_usd': 240, 'sell_usd': 100}}


class PromotedAdmissionTests(unittest.TestCase):
    def test_market_score_and_aggregate_tx_counts_cannot_replace_verified_flow(self):
        self.assertFalse(guard.flow_admission(COIN, {'score': 100, 'bs': 10}, NOW)['allow'])
        valid = features()
        coin = {**COIN, 'updatedAt': NOW}
        self.assertTrue(guard.flow_admission(coin, valid, NOW)['allow'])
        for field, value in [('source', 'SCANNER_COUNTS'), ('coverage_status', 'DEGRADED'),
                             ('address', 'other-mint'), ('pairAddress', 'other-pool')]:
            row = copy.deepcopy(valid)
            row['verified_flow'][field] = value
            self.assertFalse(guard.flow_admission(coin, row, NOW)['allow'], field)

    def test_receipt_does_not_refresh_stale_or_future_market_event(self):
        coin = {**COIN, 'updatedAt': NOW}
        for field, value in [('latest_event_at', NOW-12_001), ('latest_event_at', NOW+1),
                             ('available_at', NOW+1), ('window_at', NOW+1),
                             ('latest_event_at', None), ('available_at', True),
                             ('window_ms', guard.FLOW_WINDOW_MS + 1)]:
            row = features()
            row['verified_flow'][field] = value
            self.assertFalse(guard.flow_admission(coin, row, NOW)['allow'], field)
        for stamp in (NOW - guard.FLOW_MAX_AGE_MS - 1, NOW + 1):
            self.assertFalse(guard.flow_admission({**coin, 'updatedAt': stamp}, features(), NOW)['allow'])

    def test_pressure_uses_recorded_amounts_not_optimistic_ratio_field(self):
        for changes in ({'sell_usd': 300, 'ratio': 100}, {'unique_wallets': 1},
                        {'trades': 2}, {'buy_usd': float('nan')}, {'sell_usd': -1},
                        {'trades': 3.5}, {'unique_wallets': True}):
            row = features()
            row['verified_flow'].update(changes)
            self.assertFalse(guard.flow_admission({**COIN, 'updatedAt': NOW}, row, NOW)['allow'], changes)

    def test_safety_must_be_fresh_and_for_same_pool(self):
        risk = {'status': 'pass', 'mint': 'mint', 'pair': 'pool', 'checked_at': NOW-100}
        self.assertTrue(guard.risk_admission(COIN, risk, NOW, 60_000)['allow'])
        for changes in ({'pair': 'other'}, {'mint': 'other'}, {'status': 'review'},
                        {'provisional_early': True},
                        {'checked_at': NOW+1}, {'checked_at': NOW-60_001},
                        {'checked_at': None}):
            self.assertFalse(guard.risk_admission(COIN, {**risk, **changes}, NOW, 60_000)['allow'])

    def test_friction_cannot_consume_most_of_stop_budget(self):
        self.assertTrue(guard.cost_admission(-1.5, 3)['allow'])
        self.assertTrue(guard.cost_admission(-.6, 3)['allow'])
        for value in (-1.5001, -2.64, .01, None, float('nan'), True):
            self.assertFalse(guard.cost_admission(value, 3)['allow'], value)
        for stop in (0, -3, None, float('inf')):
            self.assertFalse(guard.cost_admission(-1, stop)['allow'])

    def test_policy_does_not_claim_edge_or_rewrite_ledger(self):
        config = guard.policy_config(3)
        self.assertEqual(config['maximum_roundtrip_cost_pct'], 1.5)
        self.assertEqual(config['flow_window_ms'], 30_000)
        self.assertFalse(config['profitability_proven'])
        self.assertTrue(config['historical_outcomes_unchanged'])
