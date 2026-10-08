"""Per-account strategy selection in the user gateway (offline; no engine is spawned)."""
import importlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class FakeProcess:
    returncode = None

    def poll(self):
        return None


class GatewayAccountStrategy(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.registry = root / 'accounts.json'
        self.registry.write_text(json.dumps({'accounts': {
            'user-a': {'user_id': 'user-a', 'signal_strategy': 'ORDER_FLOW_ADAPTIVE', 'balance': 1000.0,
                       'history': [], 'positions': [], 'trade_seq': 0},
            'user-b': {'user_id': 'user-b', 'balance': 987.5, 'history': [{'id': 'kept', 'pnl_usd': -12.5}],
                       'positions': [], 'trade_seq': 1},
            'user-c': {'user_id': 'user-c', 'signal_strategy': 'NOT_A_STRATEGY'},
        }}), encoding='utf-8')
        # A gateway-wide variable must never reach a personal engine.
        self.env = patch.dict(os.environ, {'NEO_USER_STATE_PATH': str(self.registry),
                                           'NEO_USER_ENGINE_ROOT': str(root / 'users'),
                                           'NEO_SIGNAL_STRATEGY': 'ORDER_FLOW_ADAPTIVE'})
        self.env.start()
        self.addCleanup(self.env.stop)
        import user_gateway
        self.gateway = importlib.reload(user_gateway)
        self.spawned = []

        def popen(args, cwd=None, env=None, **kwargs):
            self.spawned.append(env)
            return FakeProcess()

        patches = [patch.object(self.gateway.subprocess, 'Popen', side_effect=popen),
                   patch.object(self.gateway, 'port_open', return_value=False),
                   patch.object(self.gateway, 'engine_health', return_value=True),
                   patch.object(self.gateway, 'central_state', return_value={'stats': {}, 'events': []})]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(self.gateway.ENGINE_PROCESSES.clear)

    def start(self, user_id):
        return self.gateway.ensure_engine({'id': user_id, 'created_at': None})

    def saved_accounts(self):
        return json.loads(self.registry.read_text(encoding='utf-8'))['accounts']

    def test_registry_choice_reaches_only_that_account_engine(self):
        self.start('user-a')
        self.start('user-b')
        self.assertEqual(self.spawned[0]['NEO_SIGNAL_STRATEGY'], 'ORDER_FLOW_ADAPTIVE')
        self.assertNotIn('NEO_SIGNAL_STRATEGY', self.spawned[1])
        self.assertEqual(self.spawned[1]['NEO_ENGINE_MODE'], 'PAPER')
        saved = self.saved_accounts()
        self.assertEqual(saved['user-a']['signal_strategy'], 'ORDER_FLOW_ADAPTIVE')
        self.assertNotIn('signal_strategy', saved['user-b'])
        self.assertEqual(saved['user-b']['balance'], 987.5)
        self.assertEqual(saved['user-b']['history'], [{'id': 'kept', 'pnl_usd': -12.5}])
        self.assertNotEqual(saved['user-a']['engine_port'], saved['user-b']['engine_port'])

    def test_personal_engines_get_main_state_path_for_their_ticker_registry_seed(self):
        # TICKER_REGISTRY_SEED_V4: a personal engine's NEO_MARKET_STATE_PATH is its own
        # account; main's state path (the gateway's own) is passed separately.
        main_state = str(Path(self.temp.name) / 'state.json')
        with patch.dict(os.environ, {'NEO_MARKET_STATE_PATH': main_state}):
            os.environ.pop('NEO_MAIN_MARKET_STATE_PATH', None)
            self.start('user-b')
        env = self.spawned[-1]
        self.assertEqual(env['NEO_MAIN_MARKET_STATE_PATH'], main_state)
        self.assertEqual(env['NEO_MARKET_STATE_PATH'], str(self.gateway.engine_state_path('user-b')))
        self.assertNotEqual(env['NEO_MARKET_STATE_PATH'], main_state)
        with patch.dict(os.environ, {}):
            os.environ.pop('NEO_MARKET_STATE_PATH', None)
            os.environ.pop('NEO_MAIN_MARKET_STATE_PATH', None)
            self.gateway.ENGINE_PROCESSES.clear()
            self.start('user-a')
        self.assertNotIn('NEO_MAIN_MARKET_STATE_PATH', self.spawned[-1])

    def test_unknown_strategy_fails_closed_without_starting_an_engine(self):
        with self.assertRaisesRegex(RuntimeError, 'unsupported'):
            self.start('user-c')
        self.assertEqual(self.spawned, [])

    def test_registry_edit_is_adopted_before_the_next_spawn(self):
        data = json.loads(self.registry.read_text(encoding='utf-8'))
        data['accounts']['user-b']['signal_strategy'] = 'ORDER_FLOW_ADAPTIVE'
        self.registry.write_text(json.dumps(data), encoding='utf-8')
        self.start('user-b')
        self.assertEqual(self.spawned[-1]['NEO_SIGNAL_STRATEGY'], 'ORDER_FLOW_ADAPTIVE')
        self.assertEqual(self.saved_accounts()['user-b']['signal_strategy'], 'ORDER_FLOW_ADAPTIVE')
        self.assertEqual(self.saved_accounts()['user-b']['balance'], 987.5)

    def test_account_scope_reports_requested_versus_running_strategy(self):
        class Response:
            def __init__(self, payload):
                self.payload = payload

            def raise_for_status(self):
                return None

            def json(self):
                return json.loads(json.dumps(self.payload))

        with patch.object(self.gateway, 'ensure_engine', return_value=18800), \
             patch.object(self.gateway.SESSION, 'get',
                          return_value=Response({'config': {'signal_strategy': 'WINNER_ENSEMBLE_PAPER_V1'}})):
            stale = self.gateway.proxy_user_engine({'id': 'user-a'}, 'GET', '/state')
            matching = self.gateway.proxy_user_engine({'id': 'user-b'}, 'GET', '/state')
        self.assertEqual(stale['account_scope']['requested_strategy'], 'ORDER_FLOW_ADAPTIVE')
        self.assertEqual(stale['account_scope']['strategy'], 'WINNER_ENSEMBLE_PAPER_V1')
        self.assertFalse(stale['account_scope']['strategy_matches_request'])
        self.assertEqual(matching['account_scope']['requested_strategy'], 'WINNER_ENSEMBLE_PAPER_V1')
        self.assertTrue(matching['account_scope']['strategy_matches_request'])


class SetAccountStrategyCli(unittest.TestCase):
    """scripts/paper_runtime.py set-account-strategy touches one field of one account."""

    def setUp(self):
        import importlib.util
        script = Path(__file__).resolve().parents[1] / 'scripts' / 'paper_runtime.py'
        spec = importlib.util.spec_from_file_location('paper_runtime_cli', script)
        self.cli = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.cli)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.registry = Path(self.temp.name) / 'user_accounts.json'
        self.original = {'accounts': {
            '3aa07b45-0000-4000-8000-000000000001': {'user_id': '3aa07b45-0000-4000-8000-000000000001',
                                                     'engine_port': 18800, 'balance': 993.57, 'trade_seq': 1,
                                                     'history': [{'id': 'keep', 'pnl_usd': -6.43}], 'positions': []},
            '3aa07b45-0000-4000-8000-000000000002': {'user_id': '3aa07b45-0000-4000-8000-000000000002',
                                                     'engine_port': 18801, 'balance': 999.04, 'trade_seq': 1,
                                                     'history': [], 'positions': []},
        }}
        self.registry.write_text(json.dumps(self.original), encoding='utf-8')

    def accounts(self):
        return json.loads(self.registry.read_text(encoding='utf-8'))['accounts']

    def test_sets_and_clears_only_the_strategy_field(self):
        result = self.cli.set_account_strategy(self.registry, '3aa07b45-0000-4000-8000-000000000001',
                                               'ORDER_FLOW_ADAPTIVE')
        self.assertEqual(result['signal_strategy_after'], 'ORDER_FLOW_ADAPTIVE')
        self.assertEqual(result['ledger_files_touched'], 0)
        saved = self.accounts()
        first, second = saved['3aa07b45-0000-4000-8000-000000000001'], saved['3aa07b45-0000-4000-8000-000000000002']
        self.assertEqual(first['signal_strategy'], 'ORDER_FLOW_ADAPTIVE')
        self.assertNotIn('signal_strategy', second)
        for key in ('engine_port', 'balance', 'trade_seq', 'history', 'positions'):
            self.assertEqual(first[key], self.original['accounts']['3aa07b45-0000-4000-8000-000000000001'][key])
            self.assertEqual(second[key], self.original['accounts']['3aa07b45-0000-4000-8000-000000000002'][key])
        self.cli.set_account_strategy(self.registry, '3aa07b45-0000-4000-8000-000000000001', 'default')
        self.assertNotIn('signal_strategy', self.accounts()['3aa07b45-0000-4000-8000-000000000001'])

    def test_short_ambiguous_or_unknown_requests_change_nothing(self):
        for user, strategy in (('3aa07b45', 'ORDER_FLOW_ADAPTIVE'), ('3aa0', 'ORDER_FLOW_ADAPTIVE'),
                               ('3aa07b45-0000-4000-8000-00000000000', 'ORDER_FLOW_ADAPTIVE'),
                               ('3aa07b45-0000-4000-8000-000000000001', 'BOGUS'),
                               ('ffffffff-0000-4000-8000-000000000009', 'ORDER_FLOW_ADAPTIVE')):
            with self.subTest(user=user, strategy=strategy), self.assertRaises(SystemExit):
                self.cli.set_account_strategy(self.registry, user, strategy)
        self.assertEqual(self.accounts(), self.original['accounts'])


if __name__ == '__main__':
    unittest.main()
