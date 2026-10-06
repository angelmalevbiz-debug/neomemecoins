import importlib
import http.client
import json
import os
import tempfile
import threading
import unittest
from contextlib import contextmanager
from http.server import ThreadingHTTPServer
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

    @contextmanager
    def gateway_server(self):
        server = ThreadingHTTPServer(('127.0.0.1', 0), self.gateway.Handler)
        thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': 0.01}, daemon=True)
        thread.start()
        try:
            yield server.server_address
        finally:
            server.shutdown()
            thread.join(timeout=2)
            server.server_close()

    def cors_response(self, address, method, origin):
        connection = http.client.HTTPConnection(*address, timeout=2)
        try:
            headers = {'Origin': origin} if origin is not None else {}
            connection.request(method, '/user/health', headers=headers)
            response = connection.getresponse()
            body = response.read()
            self.assertEqual(response.status, 204 if method == 'OPTIONS' else 200)
            if method == 'GET':
                self.assertTrue(json.loads(body)['ok'])
            return dict(response.getheaders())
        finally:
            connection.close()

    def test_pages_and_local_origins_allowed_on_preflight_and_response(self):
        origins = (
            'https://angelmalevbiz-debug.github.io',
            'https://angelmalev9-creator.github.io',
            'http://localhost:5173',
            'http://127.0.0.1:5173',
        )
        with self.gateway_server() as address:
            for origin in origins:
                for method in ('OPTIONS', 'GET'):
                    with self.subTest(origin=origin, method=method):
                        headers = self.cors_response(address, method, origin)
                        self.assertEqual(headers['Access-Control-Allow-Origin'], origin)
                        self.assertEqual(headers['Vary'], 'Origin')

    def test_unknown_origin_is_never_reflected_and_default_is_new_pages(self):
        with self.gateway_server() as address:
            for origin in ('https://evil.example',
                           'https://angelmalevbiz-debug.github.io.evil.example', None):
                for method in ('OPTIONS', 'GET'):
                    with self.subTest(origin=origin, method=method):
                        headers = self.cors_response(address, method, origin)
                        self.assertEqual(headers['Access-Control-Allow-Origin'],
                                         'https://angelmalevbiz-debug.github.io')
                        self.assertNotEqual(headers['Access-Control-Allow-Origin'], origin)

    def test_private_state_cors_includes_authentication_and_upstream_errors(self):
        origin = 'https://angelmalevbiz-debug.github.io'
        with self.gateway_server() as address:
            def request_state():
                connection = http.client.HTTPConnection(*address, timeout=2)
                try:
                    connection.request('GET', '/user/state', headers={'Origin': origin})
                    response = connection.getresponse()
                    body = json.loads(response.read())
                    self.assertEqual(response.getheader('Access-Control-Allow-Origin'), origin)
                    self.assertEqual(response.getheader('Vary'), 'Origin')
                    return response.status, body
                finally:
                    connection.close()

            status, body = request_state()
            self.assertEqual(status, 401)
            self.assertEqual(body['error'], 'unauthorized')
            with patch.object(self.gateway, 'verify_user', return_value={'id': 'test-user'}):
                with patch.object(self.gateway, 'proxy_user_engine', return_value={'history': []}):
                    status, body = request_state()
                    self.assertEqual(status, 200)
                    self.assertEqual(body['history'], [])
                with patch.object(self.gateway, 'proxy_user_engine', side_effect=RuntimeError('engine unavailable')):
                    status, body = request_state()
                    self.assertEqual(status, 502)
                    self.assertEqual(body['error'], 'gateway_error')

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

    def test_target_paper_profile_requires_exact_email_and_stays_paper_only(self):
        account={'user_id':'target'}
        with patch.object(self.gateway,'TARGET_PAPER_EMAIL','target@example.com'):
            self.assertTrue(self.gateway.apply_target_profile({'id':'target','email':'target@example.com'},account))
            self.assertEqual(account['paper_profile'],self.gateway.TARGET_PAPER_PROFILE)
            other={'user_id':'other'}
            self.assertFalse(self.gateway.apply_target_profile({'id':'other','email':'other@example.com'},other))
            self.assertNotIn('paper_profile',other)
        env={'NEO_ENGINE_MODE':'PAPER','NEO_EXECUTION_MODE':'PAPER'}
        env.update(self.gateway.TARGET_PAPER_ENV)
        self.assertEqual(env['NEO_ENGINE_MODE'],'PAPER')
        self.assertEqual(env['NEO_EXECUTION_MODE'],'PAPER')
        self.assertNotIn('NEO_ENGINE_MODE',self.gateway.TARGET_PAPER_ENV)
        self.assertNotIn('NEO_EXECUTION_MODE',self.gateway.TARGET_PAPER_ENV)

    def test_target_paper_profile_does_not_match_email_case_or_prefix_by_accident(self):
        with patch.object(self.gateway,'TARGET_PAPER_EMAIL','target@example.com'):
            self.assertTrue(self.gateway.is_target_user({'email':'TARGET@example.com'}))
            self.assertFalse(self.gateway.is_target_user({'email':'x-target@example.com'}))
            self.assertFalse(self.gateway.is_target_user({'email':'target@example.com.evil'}))

    def test_corrupt_registry_refuses_silent_reset(self):
        p=Path(self.gateway.STORE_PATH)
        p.write_text('{broken', encoding='utf-8')
        with self.assertRaisesRegex(RuntimeError, 'refusing silent'):
            self.gateway.load_store()


if __name__ == '__main__':
    unittest.main()
