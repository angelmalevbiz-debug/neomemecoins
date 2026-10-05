"""Offline regression tests. Quotes are fixtures; no test calls external APIs."""
import copy
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

_TEMP = tempfile.TemporaryDirectory(prefix='neo-engine-entry-tests-')
os.environ['NEO_MARKET_STATE_PATH'] = str(Path(_TEMP.name) / 'state.json')
os.environ['NEO_MARKET_AUDIT_PATH'] = str(Path(_TEMP.name) / 'audit.jsonl')
os.environ['NEO_LIVE_TAPE_PATH'] = str(Path(_TEMP.name) / 'tape.json')
for key in ['NEO_TRADE_NOTIONAL_USD', 'NEO_MAX_DAILY_LOSS_USD', 'NEO_STRICT_ENTRY_SCORE',
            'NEO_STRICT_MIN_CONVICTION', 'NEO_STRICT_MIN_LIQUIDITY_USD',
            'NEO_STRICT_MAX_ENTRY_IMPACT_PCT', 'NEO_STRICT_MAX_ROUNDTRIP_COST_PCT']:
    os.environ.pop(key, None)
import market_monitor as m
import engine_entry_policy as policy


class EngineEntryTests(unittest.TestCase):
    def setUp(self):
        m.STATE_PATH.unlink(missing_ok=True)
        m.STATE = m.State()
        self.monitor = m.Monitor()
        self.coin = {'address': 'A'*44, 'pairAddress': 'B'*44, 'symbol': 'FIXTURE',
                     'name': 'Offline fixture', 'score': 90, 'liquidityUsd': 200000,
                     'marketCap': 1000000, 'priceUsd': .001, 'priceNative': .00001,
                     'ageMinutes': 60, 'priceChange': {'m5': 6, 'h1': -15},
                     'txns': {'m5': {'buys': 20, 'sells': 18}}, 'volume': {'h1': 50000},
                     'signals': [], 'updatedAt': m.now_ms()}
        self.flow = {'quality': 'COMPLETE', 'trades': 4, 'buys': 3, 'sells': 1, 'buy_sell_usd_ratio': 2.0,
                     'unique_wallets': 4, 'buyer_wallets': 3, 'wallet_buy_sell_ratio': 3,
                     'max_sell_usd': 50, 'buy_usd': 300}
        self.context = {'conviction': 80, 'mode': 'STRONG'}
        self.entry = {'token_raw_expected': 100000000, 'token_raw_floor': 99000000,
                      'input_usdc_raw': 200000000,'token_raw_amount': 100000000,'raw_quote': {'fixture': True}, 'price_impact_pct': 1.0,
                      'slippage_bps': 100, 'route': [], 'quoted_at': m.now_ms()}
        self.exit = {'expected_usdc': 198.0, 'floor_usdc': 196.8, 'provider_expected_usdc':198.0, 'quoted_at':m.now_ms(), 'raw_quote':{'fixture':True}}
        self.patches = [patch.object(m.STATE, 'live_flow', return_value=self.flow),
                        patch.object(self.monitor, 'market_context', return_value=self.context),
                        patch.object(m.paper_quotes, 'entry_quote', return_value=self.entry),
                        patch.object(m.paper_quotes, 'exit_quote', return_value=self.exit),
                        patch.object(m, 'append_audit'),
                        patch.object(m.rug_guard,'check',return_value={'status':'pass','metrics':{'decimals':6,'token_account_rent_lamports':1650000}}),
                        patch.object(m.price_integrity,'check',return_value={'status':'pass','version':'TEST'})]
        self.mocks = [p.start() for p in self.patches]
        self.addCleanup(lambda: [p.stop() for p in reversed(self.patches)])
        self.addCleanup(self.monitor.stop)

    def test_critical_rug_blocks_before_quote(self):
        self.mocks[5].return_value={'status':'blocked','reasons':['rugcheck_critical']}
        self.monitor.maybe_open([self.coin])
        self.mocks[2].assert_not_called()
        self.assertFalse(m.STATE.positions)

    def test_unavailable_risk_does_not_invent_clearance(self):
        self.mocks[5].return_value={'status':'pending','reasons':['risk_check_pending']}
        self.monitor.maybe_open([self.coin])
        self.mocks[2].assert_not_called()
        self.assertFalse(m.STATE.positions)

    def test_remaining_daily_risk_budget_sizes_down_before_request(self):
        m.STATE.demo_balance_usd=905
        m.STATE.risk_day_start_balance_usd=1000
        with patch.object(m,'MAX_DAILY_LOSS_USD',100), patch.object(m.paper_quotes,'prepare_entry',return_value=None) as quote:
            self.monitor.maybe_open([self.coin])
        self.assertTrue(quote.called)
        size=quote.call_args.args[2]
        self.assertLess(size,200)
        self.assertGreaterEqual(size,10)
        self.assertLessEqual(size*.055+.225,5.01)


    def test_preserve_user_risk_settings(self):
        self.assertEqual(m.TRADE_NOTIONAL_USD, 200)
        self.assertEqual(m.MAX_DAILY_LOSS_USD, 0)
        self.assertEqual(m.STOP_LOSS_PCT, 5)
        self.assertEqual(m.MAX_POSITIONS, 8)
        self.assertEqual(m.TAKE_PROFIT_PCT, 10)

    def test_valid_formerly_overfiltered_entry_reaches_quote_and_opens(self):
        self.monitor.maybe_open([self.coin])
        self.assertEqual(len(m.STATE.positions), 1)
        pos = m.STATE.positions[0]
        self.assertEqual(pos['notional_usd'], 200)
        self.assertEqual(pos['entry_policy_version'], policy.POLICY_VERSION)
        self.assertEqual(m.STATE.entry_diagnostics['status'], 'opened')
        self.assertLess(pos['entry_roundtrip_pnl_pct'], 0)
        self.assertIsNone(pos['hard_stop_net_pct'])
        self.assertEqual(pos['planned_stop_net_pct'], -5)

    def test_no_balance_session_history_reset(self):
        m.STATE.demo_balance_usd = 975
        m.STATE.demo_session_id = 'KEEP-MY-SESSION'
        old = [{'id': 'historical', 'address': 'C'*44, 'pnl_usd': -25, 'closed_at': 1}]
        m.STATE.history = copy.deepcopy(old)
        self.monitor.maybe_open([self.coin])
        self.assertEqual(m.STATE.demo_balance_usd, 975)
        self.assertEqual(m.STATE.demo_session_id, 'KEEP-MY-SESSION')
        self.assertEqual(m.STATE.history, old)

    def test_daily_loss_blocks_new_entry(self):
        m.STATE.demo_balance_usd = 900
        m.STATE.risk_day_start_balance_usd = 1000
        with patch.object(m,'MAX_DAILY_LOSS_USD',100):
            self.monitor.maybe_open([self.coin])
        self.assertFalse(m.STATE.positions)
        self.mocks[2].assert_not_called()
        self.assertEqual(m.STATE.entry_diagnostics['status'], 'daily_limit')

    def test_existing_position_is_preserved_when_second_position_opens(self):
        original = {'id': 'existing', 'address': 'D'*44, 'notional_usd': 200, 'quantity': 123}
        m.STATE.positions = [copy.deepcopy(original)]
        self.monitor.maybe_open([self.coin])
        self.assertEqual(m.STATE.positions[0], original)
        self.assertEqual(len(m.STATE.positions), 2)
        self.assertTrue(self.mocks[2].called)

    def test_no_quote_does_not_invent_a_fill(self):
        self.mocks[2].return_value = None
        self.monitor.maybe_open([self.coin])
        self.assertFalse(m.STATE.positions)
        self.assertEqual(m.STATE.entry_diagnostics['rejections']['quote_inconsistent'], 1)

    def test_no_sell_route_does_not_open(self):
        self.mocks[3].return_value = None
        self.monitor.maybe_open([self.coin])
        self.assertFalse(m.STATE.positions)
        self.assertEqual(m.STATE.entry_diagnostics['rejections']['quote_inconsistent'], 1)

    def test_costs_above_cap_are_still_rejected(self):
        self.exit.update(expected_usdc=190.0, floor_usdc=188.0)
        self.monitor.maybe_open([self.coin])
        self.assertFalse(m.STATE.positions)
        self.assertIn('roundtrip_cost', m.STATE.entry_diagnostics['rejections'])

    def test_impact_cap_is_enforced(self):
        self.entry['price_impact_pct'] = 2.01
        self.monitor.maybe_open([self.coin])
        self.assertFalse(m.STATE.positions)
        self.assertIn('impact', m.STATE.entry_diagnostics['rejections'])

    def test_stale_quote_is_not_used(self):
        self.entry['quoted_at'] -= 20000
        self.monitor.maybe_open([self.coin])
        self.assertFalse(m.STATE.positions)
        self.assertIn('quote_inconsistent', m.STATE.entry_diagnostics['rejections'])

    def test_effective_early_signal_rejects_below_configured_liquidity_and_score(self):
        for key,val in [('liquidityUsd',3999),('score',57.9)]:
            self.monitor.maybe_open([{**self.coin,key:val}])
            self.assertFalse(m.STATE.positions)
            self.assertIn('gold_signal',m.STATE.entry_diagnostics['rejections'])

    def test_original_gold_requires_observed_wallet(self):
        self.flow['unique_wallets']=0
        self.monitor.maybe_open([self.coin])
        self.assertFalse(m.STATE.positions)
        self.assertIn('gold_signal',m.STATE.entry_diagnostics['rejections'])

    def test_independent_price_failure_blocks_before_quote(self):
        self.mocks[6].return_value={'status':'blocked','reason':'price_source_disagreement'}
        self.monitor.maybe_open([self.coin])
        self.mocks[2].assert_not_called()
        self.assertFalse(m.STATE.positions)

    def test_stale_feed_is_explained(self):
        self.coin['updatedAt'] -= 60000
        self.monitor.maybe_open([self.coin])
        self.assertFalse(m.STATE.positions)
        self.assertIn('stale_feed', m.STATE.entry_diagnostics['rejections'])
        self.assertIn('Откази', m.STATE.entry_diagnostics['message'])

    def test_quote_attempts_are_bounded(self):
        self.mocks[2].return_value = None
        self.monitor.maybe_open([dict(self.coin, address=x*44) for x in 'ACDEFGH'])
        self.assertEqual(self.mocks[2].call_count, policy.MAX_QUOTED_CANDIDATES)
        self.assertEqual(m.STATE.entry_diagnostics['signal_passed'], 7)

    def test_reported_thresholds_match_real_policy(self):
        snapshot = m.STATE.snapshot()
        self.assertEqual(snapshot['config']['entry_score'], 58)
        self.assertEqual(snapshot['config']['min_liquidity_usd'], 4000)
        self.assertEqual(snapshot['config']['entry_policy_version'], policy.POLICY_VERSION)
        self.assertIn('entry_diagnostics', snapshot)

if __name__ == '__main__':
    unittest.main(verbosity=2)
