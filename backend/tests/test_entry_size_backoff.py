import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import entry_size_backoff as sizing


class QuoteSizeBackoffTests(unittest.TestCase):
    def test_halves_only_after_the_initial_size_and_stops_at_minimum(self):
        self.assertEqual(sizing.quote_notional_steps(75), [75.0, 37.5, 25.0])
        self.assertEqual(sizing.quote_notional_steps(26), [26.0, 25.0])

    def test_respects_global_attempt_bound_and_existing_smaller_risk_budget(self):
        self.assertEqual(sizing.quote_notional_steps(200), [200.0, 100.0, 50.0])
        self.assertEqual(sizing.quote_notional_steps(15), [15.0])

    def test_retries_only_impact_and_net_cost_rejections(self):
        self.assertTrue(sizing.may_retry_at_smaller_size(['impact', 'roundtrip_cost']))
        self.assertTrue(sizing.may_retry_at_smaller_size(['worst_case_cost']))
        self.assertFalse(sizing.may_retry_at_smaller_size(['quote_age']))
        self.assertFalse(sizing.may_retry_at_smaller_size(['rugcheck_critical']))
        self.assertFalse(sizing.may_retry_at_smaller_size([]))

    def test_uses_a_fresh_smaller_quote_only_after_cost_rejection(self):
        requested_sizes = []

        def check(size, _index):
            requested_sizes.append(size)
            if size > 40:
                return None, ['impact']
            return {'quote': 'fresh exact-size quote'}, []

        selected = sizing.first_quote_passing_costs(75, check)
        self.assertEqual(requested_sizes, [75.0, 37.5])
        self.assertEqual(selected, (37.5, {'quote': 'fresh exact-size quote'}, 2))

    def test_does_not_retry_data_or_safety_failures(self):
        requested_sizes = []

        def check(size, _index):
            requested_sizes.append(size)
            return None, ['quote_inconsistent']

        self.assertIsNone(sizing.first_quote_passing_costs(75, check))
        self.assertEqual(requested_sizes, [75.0])

    def test_rejects_invalid_size_inputs(self):
        self.assertEqual(sizing.quote_notional_steps(float('nan')), [])
        self.assertEqual(sizing.quote_notional_steps(0), [])
        self.assertEqual(sizing.quote_notional_steps(50, max_attempts=0), [])


if __name__ == '__main__':
    unittest.main()
