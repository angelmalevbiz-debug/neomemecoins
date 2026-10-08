import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('pages_probe',
    Path(__file__).resolve().parents[1] / 'scripts' / 'check_pages_backend.py')
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


class Response:
    def __init__(self, status, state=None):
        self.status_code = status
        self.headers = {'Access-Control-Allow-Origin': probe.ORIGIN,
                        'Access-Control-Allow-Headers': 'Authorization, Content-Type'}
        self.state = state

    def json(self):
        return self.state


class ProbeTests(unittest.TestCase):
    def run_probe(self, version, *, paper=True, training=True):
        state = {'config': {'entry_policy_version': version, 'paper_only': paper}}
        if training:
            state['paper_training'] = {}
        with patch.object(probe.requests, 'Session') as session:
            session.return_value.__enter__.return_value.request.side_effect = [
                Response(204), Response(200), Response(401), Response(200, state)]
            report = probe.check()
        return report

    def test_running_and_opt_in_labels_are_declared_supported(self):
        # The shared PAPER account publishes the strategy-lock label; the opt-in
        # ORDER_FLOW_ADAPTIVE profile publishes ORDER_FLOW_BALANCED_V4.
        self.assertIn('WINNER_ENSEMBLE_VERIFIED_ENTRY_V4', probe.SUPPORTED_ENTRY_POLICIES)
        self.assertIn('ORDER_FLOW_BALANCED_V4', probe.SUPPORTED_ENTRY_POLICIES)
        # DEFENSIVE_ENTRY_LAYER_V1 labels (2026-10-08) published by updated backends.
        import winner_ensemble
        import order_flow_adaptive_oct4 as oct4
        self.assertIn(winner_ensemble.ENTRY_POLICY_VERSION, probe.SUPPORTED_ENTRY_POLICIES)
        self.assertIn(oct4.ENTRY_POLICY_VERSION, probe.SUPPORTED_ENTRY_POLICIES)

    def test_current_ensemble_and_original_validated_policy_are_supported(self):
        for version in probe.SUPPORTED_ENTRY_POLICIES:
            report = self.run_probe(version)
            self.assertTrue(report['repaired_shared_backend_ready'])
            self.assertTrue(report['user_gateway_cors_ready'])
            self.assertEqual(report['authenticated_dashboard'], 'NOT_CHECKED')

    def test_unknown_policy_live_or_absent_training_still_fails(self):
        self.assertFalse(self.run_probe('unknown')['repaired_shared_backend_ready'])
        self.assertFalse(self.run_probe('WINNER_ENSEMBLE_ENTRY_V1', paper=False)['repaired_shared_backend_ready'])
        self.assertFalse(self.run_probe('WINNER_ENSEMBLE_ENTRY_V1', training=False)['repaired_shared_backend_ready'])
