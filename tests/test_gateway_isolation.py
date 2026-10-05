import importlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class GatewayIsolation(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {'NEO_USER_STATE_PATH': str(Path(self.temp.name)/'accounts.json')})
        self.env.start()
        import user_gateway
        self.gateway = importlib.reload(user_gateway)

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def test_new_personal_book_does_not_copy_shared_trades_or_capital(self):
        raw = {'history': [{'opened_at': 9999999999999, 'pnl_usd': 90}],
               'positions': [{'opened_at': 9999999999999, 'quantity': 10}]}
        s = self.gateway.build_bootstrap_state({'id':'test'}, {}, raw)
        self.assertEqual(s['history'], [])
        self.assertEqual(s['positions'], [])
        self.assertEqual(s['demo_balance_usd'], 1000)

    def test_legacy_history_is_not_truncated_to_300(self):
        history = [{'id':str(i), 'pnl_usd':-1} for i in range(400)]
        s = self.gateway.build_bootstrap_state({'id':'test'}, {'history':history}, {})
        self.assertEqual(len(s['history']), 400)
        self.assertEqual(s['demo_balance_usd'], 600)

    def test_corrupt_registry_refuses_silent_reset(self):
        p=Path(self.gateway.STORE_PATH)
        p.write_text('{broken', encoding='utf-8')
        with self.assertRaisesRegex(RuntimeError, 'refusing silent'):
            self.gateway.load_store()


if __name__ == '__main__':
    unittest.main()
