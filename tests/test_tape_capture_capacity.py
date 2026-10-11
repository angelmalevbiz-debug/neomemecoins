import json
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class TapeCaptureCapacityTests(unittest.TestCase):
    def test_launcher_expands_observation_without_increasing_rpc_body_budget(self):
        script = (ROOT / 'scripts' / 'start_local_paper.ps1').read_text(encoding='utf-8')
        seats = re.search(r"NEO_TAPE_MAX_PAIRS='(\d+)'", script)
        bodies = re.search(r"NEO_TAPE_TX_PER_POLL='(\d+)'", script)
        self.assertIsNotNone(seats)
        self.assertIsNotNone(bodies)
        self.assertEqual(int(seats.group(1)), 12)
        self.assertEqual(int(bodies.group(1)), 48)

    def test_strategy_lock_records_observation_capacity_not_entry_authorization(self):
        lock = json.loads((ROOT / 'strategy-lock.json').read_text(encoding='utf-8'))
        policy = lock['tape_seat_policy']
        self.assertEqual(policy['configured_seat_budget'], 12)
        self.assertEqual(policy['transaction_body_budget_per_poll'], 48)
        self.assertTrue(policy['seat_or_body_budget_changed'])
        self.assertFalse(policy['changes_entry_exit_cost_or_ledgers'])
        self.assertFalse(policy['is_entry_authorization'])
        self.assertFalse(policy['profitability_proven'])


if __name__ == '__main__':
    unittest.main()
