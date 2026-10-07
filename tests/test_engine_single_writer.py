"""One engine per PAPER ledger: gateway port stability, auth cache and the engine write lock.

Regression for the 2026-10-08 incident where a slow health probe made the gateway
start a second engine for the same account on a new port; both wrote one ledger.
Offline only: processes, health probes and Supabase are patched.
"""
import importlib
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

_TEMP = tempfile.TemporaryDirectory(prefix='neo-single-writer-')
os.environ['NEO_MARKET_STATE_PATH'] = str(Path(_TEMP.name) / 'state.json')
os.environ['NEO_MARKET_AUDIT_PATH'] = str(Path(_TEMP.name) / 'audit.jsonl')
os.environ['NEO_LIVE_TAPE_PATH'] = str(Path(_TEMP.name) / 'tape.json')
os.environ.pop('NEO_SIGNAL_STRATEGY', None)
import market_monitor as m


class AliveProcess:
    returncode = None

    def poll(self):
        return None


class GatewayPortStability(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.env = patch.dict(os.environ, {'NEO_USER_STATE_PATH': str(root / 'accounts.json'),
                                           'NEO_USER_ENGINE_ROOT': str(root / 'users')})
        self.env.start()
        self.addCleanup(self.env.stop)
        import user_gateway
        self.gateway = importlib.reload(user_gateway)
        self.addCleanup(self.gateway.ENGINE_PROCESSES.clear)
        sleep = patch.object(self.gateway.time, 'sleep')
        sleep.start()
        self.addCleanup(sleep.stop)

    def test_busy_configured_port_does_not_churn_to_another_port(self):
        account = {'engine_port': 18804}
        with patch.object(self.gateway, 'engine_health', return_value=False), \
             patch.object(self.gateway, 'port_open', return_value=True), \
             patch.object(self.gateway, 'allocate_port') as allocate, \
             patch.object(self.gateway.subprocess, 'Popen') as popen:
            with self.assertRaisesRegex(RuntimeError, 'occupied but not healthy'):
                self.gateway.start_engine({'id': 'test'}, account)
        self.assertEqual(account['engine_port'], 18804)
        allocate.assert_not_called()
        popen.assert_not_called()

    def test_slow_engine_is_reused_after_a_retry(self):
        account = {'engine_port': 18804}
        with patch.object(self.gateway, 'engine_health', side_effect=[False, False, True]), \
             patch.object(self.gateway, 'port_open', return_value=True), \
             patch.object(self.gateway.subprocess, 'Popen') as popen:
            self.assertEqual(self.gateway.start_engine({'id': 'test'}, account), 18804)
        popen.assert_not_called()

    def test_tracked_live_engine_is_never_duplicated_even_if_port_looks_free(self):
        self.gateway.ENGINE_PROCESSES['test'] = AliveProcess()
        account = {'engine_port': 18804}
        with patch.object(self.gateway, 'engine_health', return_value=False), \
             patch.object(self.gateway, 'port_open', return_value=False), \
             patch.object(self.gateway, 'allocate_port') as allocate, \
             patch.object(self.gateway.subprocess, 'Popen') as popen:
            with self.assertRaisesRegex(RuntimeError, 'no second engine'):
                self.gateway.start_engine({'id': 'test'}, account)
        allocate.assert_not_called()
        popen.assert_not_called()

    def test_dead_engine_restarts_on_its_own_port(self):
        account = {'engine_port': 18804}
        with patch.object(self.gateway, 'engine_health', side_effect=[False, True]), \
             patch.object(self.gateway, 'port_open', return_value=False), \
             patch.object(self.gateway, 'allocate_port') as allocate, \
             patch.object(self.gateway, 'bootstrap_if_needed'), \
             patch.object(self.gateway.subprocess, 'Popen', return_value=AliveProcess()) as popen:
            self.assertEqual(self.gateway.start_engine({'id': 'test'}, account), 18804)
        allocate.assert_not_called()
        self.assertEqual(popen.call_args.kwargs['env']['NEO_MONITOR_PORT'], '18804')

    def test_verified_user_is_cached_between_polls(self):
        class Response:
            status_code = 200

            def json(self):
                return {'id': 'user-1', 'email': 'paper@example.test'}

        with patch.object(self.gateway, 'SUPABASE_PUBLISHABLE_KEY', 'test-key'), \
             patch.object(self.gateway.SESSION, 'get', return_value=Response()) as get:
            first = self.gateway.verify_user({'Authorization': 'Bearer test-token'})
            second = self.gateway.verify_user({'Authorization': 'Bearer test-token'})
            other = self.gateway.verify_user({'Authorization': 'Bearer other-token'})
        self.assertEqual((first['id'], second['id'], other['id']), ('user-1', 'user-1', 'user-1'))
        self.assertEqual(get.call_count, 2)

    def test_rejected_token_is_never_cached(self):
        class Response:
            status_code = 401

            def json(self):
                return {}

        with patch.object(self.gateway, 'SUPABASE_PUBLISHABLE_KEY', 'test-key'), \
             patch.object(self.gateway.SESSION, 'get', return_value=Response()) as get:
            self.assertIsNone(self.gateway.verify_user({'Authorization': 'Bearer bad'}))
            self.assertIsNone(self.gateway.verify_user({'Authorization': 'Bearer bad'}))
        self.assertEqual(get.call_count, 2)

    def test_gateway_save_never_reverts_an_operator_strategy_edit(self):
        import json
        self.gateway.STORE['accounts']['test'] = {'user_id': 'test', 'engine_port': 18804, 'balance': 990.0}
        self.gateway.save_store()
        on_disk = json.loads(self.gateway.STORE_PATH.read_text(encoding='utf-8'))
        on_disk['accounts']['test'].update(signal_strategy='ORDER_FLOW_ADAPTIVE', signal_strategy_set_at=123)
        self.gateway.STORE_PATH.write_text(json.dumps(on_disk), encoding='utf-8')
        # The gateway's shutdown save runs without any request having refreshed STORE.
        self.gateway.STORE['accounts']['test']['balance'] = 989.5
        self.gateway.save_store()
        saved = json.loads(self.gateway.STORE_PATH.read_text(encoding='utf-8'))['accounts']['test']
        self.assertEqual(saved['signal_strategy'], 'ORDER_FLOW_ADAPTIVE')
        self.assertEqual(saved['signal_strategy_set_at'], 123)
        self.assertEqual(saved['balance'], 989.5)
        self.assertEqual(saved['engine_port'], 18804)

    def test_transient_registry_read_error_keeps_known_strategy(self):
        self.gateway.STORE['accounts']['test'] = {'user_id': 'test', 'signal_strategy': 'ORDER_FLOW_ADAPTIVE'}
        with patch.object(self.gateway, 'load_store', side_effect=RuntimeError('sharing violation')):
            self.gateway.refresh_account_strategies()
        self.assertEqual(self.gateway.STORE['accounts']['test']['signal_strategy'], 'ORDER_FLOW_ADAPTIVE')


class EngineWriteLock(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='neo-engine-lock-')
        self.addCleanup(self.temp.cleanup)
        self.lock_path = Path(self.temp.name) / 'state.json.engine.lock'
        self.addCleanup(lambda: m.ENGINE_LOCK_HANDLE and not m.ENGINE_LOCK_HANDLE.closed and m.ENGINE_LOCK_HANDLE.close())

    def test_second_engine_on_the_same_ledger_refuses_to_start(self):
        first = m.acquire_engine_lock(self.lock_path)
        code = ('import sys; sys.path.insert(0, sys.argv[2]); import market_monitor as m\n'
                'try:\n    m.acquire_engine_lock(sys.argv[1])\nexcept RuntimeError as exc:\n'
                '    print("REFUSED", exc); raise SystemExit(3)\nprint("ACQUIRED")')
        env = {**os.environ, 'PYTHONUTF8': '1'}
        backend = str(Path(__file__).resolve().parents[1] / 'backend')
        result = subprocess.run([sys.executable, '-c', code, str(self.lock_path), backend],
                                env=env, capture_output=True, timeout=60)
        self.assertEqual(result.returncode, 3, result.stdout + result.stderr)
        self.assertIn(b'REFUSED', result.stdout)
        first.close()
        result = subprocess.run([sys.executable, '-c', code, str(self.lock_path), backend],
                                env=env, capture_output=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(b'ACQUIRED', result.stdout)

    def test_lock_does_not_touch_the_ledger(self):
        state = Path(self.temp.name) / 'state.json'
        state.write_text('{"history": [{"id": "keep"}]}', encoding='utf-8')
        handle = m.acquire_engine_lock(self.lock_path)
        handle.close()
        self.assertEqual(state.read_text(encoding='utf-8'), '{"history": [{"id": "keep"}]}')


if __name__ == '__main__':
    unittest.main()
