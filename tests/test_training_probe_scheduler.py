"""Background probe admission must not relabel stale cached passes as fresh."""
import copy
import os
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

IMPORT_TEMP = tempfile.TemporaryDirectory(prefix='neo-probe-import-')
for name, filename in [('NEO_MARKET_STATE_PATH', 'state.json'),
                       ('NEO_MARKET_AUDIT_PATH', 'audit.jsonl'),
                       ('NEO_LIVE_TAPE_PATH', 'tape.json')]:
    os.environ[name] = str(Path(IMPORT_TEMP.name) / filename)
import market_monitor as market
from test_training_quote_probe import inputs, NOW, MINT, PAIR


class TrainingProbeSchedulerTests(unittest.TestCase):
    def setUp(self):
        self.coin, self.flow, self.safety, self.price = inputs()
        self.monitor = market.Monitor()
        state = SimpleNamespace(lock=threading.Lock(), running=True, positions=[])
        for target, name, kwargs in [
            (market, 'STATE', {'new': state}),
            (market, 'now_ms', {'return_value': NOW}),
            (market.training_bridge, 'enabled', {'return_value': True}),
            (market.training_bridge, 'note_quote_probe', {}),
            (market.price_integrity, 'check', {'return_value': self.price}),
            (market.rug_guard, 'check', {'return_value': self.safety}),
            (market.threading, 'Thread', {}),
            (market, 'collect_exact_pool_quotes', {}),
        ]:
            p = patch.object(target, name, **kwargs)
            value = p.start()
            self.addCleanup(p.stop)
            if name == 'note_quote_probe': self.note = value
            if name == 'Thread': self.worker = value
            if name == 'collect_exact_pool_quotes': self.collector = value
        state.live_flow = lambda *_: copy.deepcopy(self.flow)

    def assert_blocked(self, reason):
        before = copy.deepcopy((self.safety, self.price))
        self.monitor.schedule_training_quote_probe([self.coin])
        self.worker.assert_not_called()
        self.collector.assert_not_called()
        self.assertFalse(self.monitor.training_probe_inflight)
        self.assertEqual(self.monitor.training_probe_last_attempt_at, 0)
        self.note.assert_called_once_with('WAITING_FOR_FRESH_PREFLIGHT', reason=reason, at=NOW)
        self.assertEqual((self.safety, self.price), before)

    def test_cached_risk_pass_older_than_collector_window_cannot_start_probe(self):
        # The risk adapter caches passes for 60s; the collector admits only 30s.
        self.safety['checked_at'] = NOW - 30_001
        self.assert_blocked('safety_not_fresh_pass')
        self.assertEqual(self.monitor.training_probe_retry_after[(MINT, PAIR)],
                         NOW + market.TRAINING_PREFLIGHT_RETRY_MS)

    def test_future_or_missing_risk_timestamp_cannot_start_probe(self):
        for stamp in (NOW + 1, None):
            with self.subTest(stamp=stamp):
                self.monitor.training_probe_retry_after.clear()
                self.note.reset_mock()
                self.safety['checked_at'] = stamp
                self.assert_blocked('safety_not_fresh_pass')

    def test_independent_price_pass_requires_fresh_reference_and_positive_price(self):
        for key, value in [('reference_received_at', NOW - 30_001),
                           ('reference_received_at', NOW + 1),
                           ('reference_received_at', None), ('reference_price', 0),
                           ('reference_price', float('nan')), ('mint', 'A' * 44),
                           ('pair', 'A' * 44)]:
            with self.subTest(key=key, value=value):
                self.monitor.training_probe_retry_after.clear()
                self.note.reset_mock()
                self.price.clear()
                self.price.update(inputs()[3])
                self.price[key] = value
                self.assert_blocked('independent_price_not_fresh_pass')

    def test_missing_cost_proof_and_wrong_risk_identity_spend_no_attempt(self):
        for mutation, reason in [
            (lambda s: s['metrics'].pop('token_account_rent_lamports'), 'rent_or_sol_cost_unknown'),
            (lambda s: s['metrics'].update(sol_usd=None), 'rent_or_sol_cost_unknown'),
            (lambda s: s.update(mint='A' * 44), 'safety_not_fresh_pass'),
            (lambda s: s.update(pair='A' * 44), 'safety_not_fresh_pass'),
            (lambda s: s.update(provisional_early=True), 'safety_not_fresh_pass'),
        ]:
            with self.subTest(reason=reason):
                self.monitor.training_probe_retry_after.clear()
                self.note.reset_mock()
                self.safety.clear()
                self.safety.update(inputs()[2])
                mutation(self.safety)
                self.assert_blocked(reason)

    def test_boundary_fresh_exact_pool_proof_starts_one_background_worker(self):
        self.safety['checked_at'] = NOW - 30_000
        self.price['reference_received_at'] = NOW - 30_000
        self.monitor.schedule_training_quote_probe([self.coin])
        self.worker.assert_called_once()
        self.worker.return_value.start.assert_called_once_with()
        self.note.assert_called_once_with('PREFLIGHT_PASSED', attempted=True, at=NOW)
        self.assertTrue(self.monitor.training_probe_inflight)
        self.assertEqual(self.monitor.training_probe_last_attempt_at, NOW)
        self.collector.assert_not_called()

    def test_proof_expiring_while_waiting_for_admission_is_rechecked(self):
        self.safety['checked_at'] = NOW - 30_000
        # A cache read was valid, but the lock admission occurs a millisecond later.
        with patch.object(market, 'now_ms', side_effect=[NOW, NOW, NOW + 1, NOW + 1]):
            self.monitor.schedule_training_quote_probe([self.coin])
        self.worker.assert_not_called()
        self.note.assert_called_once_with('WAITING_FOR_FRESH_PREFLIGHT',
                                         reason='safety_not_fresh_pass', at=NOW + 1)
        self.assertFalse(self.monitor.training_probe_inflight)

    def test_missing_verified_flow_reports_wait_without_probing_risk_or_prices(self):
        self.flow['quality'] = 'UNKNOWN'
        self.monitor.schedule_training_quote_probe([self.coin])
        self.worker.assert_not_called()
        market.rug_guard.check.assert_not_called()
        market.price_integrity.check.assert_not_called()
        self.note.assert_called_once_with('WAITING_FOR_QUALIFIED_FLOW', at=NOW)

    def test_preflight_retry_cooldown_is_reported_as_a_cooldown(self):
        self.monitor.training_probe_retry_after[(MINT, PAIR)] = NOW + 1
        self.monitor.schedule_training_quote_probe([self.coin])
        self.worker.assert_not_called()
        market.rug_guard.check.assert_not_called()
        market.price_integrity.check.assert_not_called()
        self.note.assert_called_once_with('WAITING_FOR_FRESH_PREFLIGHT',
                                         reason='preflight_retry_cooldown', at=NOW)


if __name__ == '__main__':
    unittest.main()
