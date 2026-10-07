"""Regression tests for the restored October 4 ORDER_FLOW_ADAPTIVE profile.

Offline fixtures only: quotes, flow, safety and prices are patched; no test
calls an external API or touches a real account. Numbering follows the owner's
required regression list (1-28); the remaining tests cover configuration
ownership and the default strategy staying unchanged.
"""
import copy
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

_TEMP = tempfile.TemporaryDirectory(prefix='neo-oct4-tests-')
os.environ['NEO_MARKET_STATE_PATH'] = str(Path(_TEMP.name) / 'state.json')
os.environ['NEO_MARKET_AUDIT_PATH'] = str(Path(_TEMP.name) / 'audit.jsonl')
os.environ['NEO_LIVE_TAPE_PATH'] = str(Path(_TEMP.name) / 'tape.json')
for key in ['NEO_SIGNAL_STRATEGY', 'NEO_TRADE_NOTIONAL_USD', 'NEO_MAX_DAILY_LOSS_USD',
            'NEO_STRICT_ENTRY_SCORE', 'NEO_STRICT_MIN_CONVICTION', 'NEO_STRICT_MIN_LIQUIDITY_USD',
            'NEO_STRICT_MAX_ENTRY_IMPACT_PCT', 'NEO_STRICT_MAX_ROUNDTRIP_COST_PCT']:
    os.environ.pop(key, None)
import market_monitor as m
import engine_exit_policy as exit_policy
import order_flow_adaptive_oct4 as oct4
import promoted_entry_guard as promoted_guard
import winner_ensemble

A, B, C = 'A' * 44, 'B' * 44, 'C' * 44


def decision_flow(now, **overrides):
    """A complete 60-second exact-pool window that passes every October 4 flow check."""
    flow = {'quality': 'COMPLETE', 'fresh': True, 'seconds': 60, 'decision_at': now,
            'trades': 5, 'buys': 4, 'sells': 1, 'buy_usd': 300.0, 'sell_usd': 100.0,
            'net_buy_usd': 200.0, 'buy_sell_usd_ratio': 3.0, 'unique_wallets': 4,
            'buyer_wallets': 3, 'seller_wallets': 1, 'wallet_buy_sell_ratio': 3.0,
            'repeat_buy_wallets': 1, 'whale_buy_usd': 0.0, 'whale_sell_usd': 0.0,
            'max_buy_usd': 120.0, 'max_sell_usd': 50.0,
            'verified_flow': {'source': promoted_guard.FLOW_SOURCE, 'coverage_status': 'COMPLETE',
                              'window_ms': promoted_guard.FLOW_WINDOW_MS, 'address': A,
                              'pairAddress': B, 'window_at': now, 'latest_event_at': now - 100,
                              'available_at': now - 50, 'trades': 4, 'unique_wallets': 3,
                              'buy_usd': 300.0, 'sell_usd': 100.0}}
    flow.update(overrides)
    return flow


def context_for(conviction):
    return {'conviction': float(conviction), 'fast_flow': {}, 'slow_flow': {}, **oct4.hold_mode(conviction)}


class Oct4EntryTests(unittest.TestCase):
    def setUp(self):
        m.activate_strategy(oct4.STRATEGY_ID)
        self.addCleanup(m.activate_strategy, m.DEFAULT_SIGNAL_STRATEGY)
        m.STATE_PATH.unlink(missing_ok=True)
        m.STATE = m.State()
        self.monitor = m.Monitor()
        self.addCleanup(self.monitor.stop)
        now = m.now_ms()
        self.coin = {'address': A, 'pairAddress': B, 'symbol': 'FIXTURE', 'name': 'Offline fixture',
                     'score': 90, 'liquidityUsd': 200000, 'marketCap': 1000000, 'priceUsd': .001,
                     'priceNative': .00001, 'ageMinutes': 60, 'priceChange': {'m5': 6, 'h1': -15},
                     'txns': {'m5': {'buys': 20, 'sells': 18}}, 'volume': {'h1': 50000},
                     'signals': [], 'updatedAt': now}
        self.flow = decision_flow(now)
        self.context = context_for(80)
        self.entry = {'token_raw_expected': 100000000, 'token_raw_floor': 99000000,
                      'input_usdc_raw': 200000000, 'token_raw_amount': 100000000,
                      'raw_quote': {'fixture': True}, 'price_impact_pct': 1.0,
                      'slippage_bps': 100, 'route': [], 'quoted_at': now}
        self.exit = {'expected_usdc': 198.0, 'floor_usdc': 196.8, 'provider_expected_usdc': 198.0,
                     'quoted_at': now, 'raw_quote': {'fixture': True}}
        self.safety = {'status': 'pass', 'mint': A, 'pair': B, 'checked_at': now,
                       'metrics': {'decimals': 6, 'token_account_rent_lamports': 1650000}}
        self.price = {'status': 'pass', 'version': 'TEST'}
        self.patches = [
            patch.object(m.STATE, 'live_flow', side_effect=lambda address, seconds=30, pair_address=None: self.flow),
            patch.object(self.monitor, 'market_context', side_effect=lambda coin, position=None: self.context),
            patch.object(m.paper_quotes, 'entry_quote', side_effect=lambda *args, **kwargs: self.entry),
            patch.object(m.paper_quotes, 'exit_quote', side_effect=lambda *args, **kwargs: self.exit),
            patch.object(m, 'append_audit'),
            patch.object(m.rug_guard, 'check', side_effect=lambda coin: self.safety),
            patch.object(m.price_integrity, 'check', side_effect=lambda coin: self.price),
        ]
        self.mocks = [p.start() for p in self.patches]
        self.addCleanup(lambda: [p.stop() for p in reversed(self.patches)])
        self.entry_quote = self.mocks[2]

    def open_once(self):
        self.monitor.maybe_open([self.coin])
        return m.STATE.entry_diagnostics

    def assert_rejected(self, reason):
        report = self.open_once()
        self.assertFalse(m.STATE.positions)
        self.assertIn(reason, report['rejections'], report)
        self.entry_quote.assert_not_called()
        return report

    def test_01_valid_historical_entry_passes(self):
        report = self.open_once()
        self.assertEqual(report['status'], 'opened')
        self.assertEqual(report['policy_version'], 'ORDER_FLOW_BALANCED_V4')
        self.assertEqual(report['signal_strategy'], 'ORDER_FLOW_ADAPTIVE')
        self.assertEqual(len(m.STATE.positions), 1)
        pos = m.STATE.positions[0]
        self.assertEqual(pos['strategy_id'], 'ORDER_FLOW_ADAPTIVE')
        self.assertEqual(pos['entry_policy_version'], 'ORDER_FLOW_BALANCED_V4')
        self.assertEqual(pos['exit_policy'], 'adaptive')
        self.assertEqual(pos['exit_policy_version'], exit_policy.ADAPTIVE_VERSION)
        self.assertEqual(pos['learning_mode'], 'ADAPTIVE_CONTEXT_HOLD')
        self.assertEqual(pos['entry_mode'], 'ORDER_FLOW_BALANCED_V4')
        self.assertEqual(pos['signal_evidence'], oct4.SIGNAL_EVIDENCE)
        self.assertEqual(pos['notional_usd'], 200)
        self.assertEqual(pos['entry_hold_mode'], 'STRONG')
        self.assertEqual(pos['adaptive_hold']['max_hold_minutes'], 30)
        self.assertIsNone(pos['take_profit_net_pct'])
        self.assertEqual(pos['entry_decision_flow']['seconds'], 60)
        self.assertEqual(pos['verified_entry_flow']['source'], promoted_guard.FLOW_SOURCE)
        self.assertEqual(pos['planned_stop_net_pct'], -5)
        self.assertIsNone(pos['hard_stop_net_pct'])
        self.assertLess(pos['entry_roundtrip_pnl_pct'], 0)
        self.assertEqual(pos['effective_entry_thresholds'], oct4.effective_entry_thresholds())
        self.assertEqual(pos['signal_source_commit'], oct4.SOURCE_COMMIT)

    def test_02_score_84_rejects(self):
        self.coin['score'] = 84
        report = self.assert_rejected('score')
        self.assertEqual(report['examples'][0]['metrics']['score'], 84)

    def test_03_liquidity_below_30k_rejects(self):
        self.coin['liquidityUsd'] = 29999
        self.assert_rejected('liquidity')

    def test_04_conviction_71_rejects(self):
        self.context = context_for(71)
        report = self.assert_rejected('conviction')
        self.assertEqual(report['examples'][0]['metrics']['conviction'], 71.0)

    def test_05_flow_ratio_below_1_30_rejects(self):
        self.flow['buy_sell_usd_ratio'] = 1.29
        report = self.assert_rejected('flow_ratio')
        self.assertEqual(report['examples'][0]['metrics']['flow_buy_sell_usd_ratio'], 1.29)

    def test_06_fewer_than_4_unique_wallets_rejects(self):
        self.flow['unique_wallets'] = 3
        self.assert_rejected('wallet_count')

    def test_07_large_sell_protection_rejects(self):
        self.flow['max_sell_usd'] = 300.0
        self.assert_rejected('large_sells')

    def test_07b_incomplete_decision_window_fails_closed(self):
        self.flow['quality'] = 'DEGRADED'
        self.assert_rejected('flow_quality')

    def test_07c_missing_confirmed_exact_pool_window_fails_closed(self):
        self.flow['verified_flow'] = None
        self.assert_rejected('promoted_verified_flow_unavailable')

    def test_20_same_token_reentry_cooldown_is_20_minutes(self):
        self.assertEqual(m.SAME_TOKEN_COOLDOWN_SECONDS, 1200)
        now = m.now_ms()
        m.STATE.history = [{'id': 'h1', 'address': A, 'pnl_usd': -1, 'closed_at': now - 19 * 60 * 1000}]
        report = self.open_once()
        self.assertFalse(m.STATE.positions)
        self.assertEqual(report['rejections'].get('cooldown'), 1)
        m.STATE.history = [{'id': 'h1', 'address': A, 'pnl_usd': -1, 'closed_at': now - 21 * 60 * 1000}]
        self.open_once()
        self.assertEqual(len(m.STATE.positions), 1)

    def test_21_only_one_open_position(self):
        self.assertEqual(m.MAX_POSITIONS, 1)
        m.STATE.positions = [{'id': 'existing', 'address': C, 'pairAddress': B, 'notional_usd': 200, 'quantity': 1}]
        report = self.open_once()
        self.assertEqual(report['status'], 'position_open')
        self.assertEqual(len(m.STATE.positions), 1)
        self.entry_quote.assert_not_called()

    def test_22_daily_loss_limit_100(self):
        self.assertEqual(m.MAX_DAILY_LOSS_USD, 100)
        m.STATE.demo_balance_usd = 900
        m.STATE.risk_day_start_balance_usd = 1000
        report = self.open_once()
        self.assertEqual(report['status'], 'daily_limit')
        self.assertFalse(m.STATE.positions)
        self.entry_quote.assert_not_called()

    def test_23_24_history_balance_and_sequence_survive_activation(self):
        m.STATE.demo_balance_usd = 975
        m.STATE.demo_session_id = 'KEEP-MY-SESSION'
        m.STATE.trade_seq = 7
        old = [{'id': 'historical', 'address': C, 'pnl_usd': -25, 'closed_at': 1,
                'strategy_id': winner_ensemble.VERSION}]
        m.STATE.history = copy.deepcopy(old)
        m.activate_strategy(m.DEFAULT_SIGNAL_STRATEGY)
        m.activate_strategy(oct4.STRATEGY_ID)
        self.open_once()
        self.assertEqual(m.STATE.demo_balance_usd, 975)
        self.assertEqual(m.STATE.demo_session_id, 'KEEP-MY-SESSION')
        self.assertEqual(m.STATE.history, old)
        self.assertEqual(m.STATE.trade_seq, 8)
        self.assertEqual(m.STATE.positions[0]['trade_no'], 8)

    def test_26_stale_quote_cannot_enter(self):
        # The modern quote adapter refuses a stale route before the policy's own
        # 10-second age check can run; either refusal must leave no position.
        self.entry['quoted_at'] = m.now_ms() - 11_000
        report = self.open_once()
        self.assertFalse(m.STATE.positions)
        self.assertTrue({'quote_age', 'quote_inconsistent'} & set(report['rejections']), report['rejections'])
        self.assertTrue(self.entry_quote.called)
        self.assertEqual(report['opened'], 0)

    def test_27_missing_safety_or_price_evidence_fails_closed(self):
        self.safety = {'status': 'pending', 'reasons': ['risk_check_pending']}
        report = self.open_once()
        self.assertFalse(m.STATE.positions)
        self.assertIn('risk_check_pending', report['rejections'])
        self.entry_quote.assert_not_called()
        now = m.now_ms()
        self.safety = {'status': 'pass', 'mint': A, 'pair': B, 'checked_at': now,
                       'metrics': {'decimals': 6, 'token_account_rent_lamports': 1650000}}
        self.price = {'status': 'review', 'reason': 'price_unavailable'}
        report = self.open_once()
        self.assertFalse(m.STATE.positions)
        self.assertIn('price_unavailable', report['rejections'])

    def test_cost_caps_are_strategy_owned_2_75_and_4_50(self):
        self.assertEqual((m.STRICT_MAX_ROUNDTRIP_COST_PCT, m.STRICT_MAX_WORST_CASE_COST_PCT), (2.75, 4.5))
        self.exit.update(expected_usdc=194.0, floor_usdc=192.0, provider_expected_usdc=194.0)
        report = self.open_once()
        self.assertFalse(m.STATE.positions)
        self.assertIn('roundtrip_cost', report['rejections'])
        # Flat notional: one quote per candidate, no smaller-size retry.
        self.assertEqual((report['quote_attempts'], report['size_retries']), (1, 0))
        self.assertEqual(self.entry_quote.call_args.args[2], 200)
        # The modern per-token quote-retry cooldown (15 s) still applies afterwards.
        self.assertIn('quote_retry_cooldown', self.open_once()['rejections'])
        self.monitor.entry_quote_retry_after.clear()
        self.exit.update(expected_usdc=195.5, floor_usdc=193.0, provider_expected_usdc=195.5)
        self.open_once()
        self.assertEqual(len(m.STATE.positions), 1)
        self.assertEqual(m.STATE.positions[0]['notional_usd'], 200)
        self.assertGreater(m.STATE.positions[0]['entry_roundtrip_pnl_pct'], -2.75)
        self.assertLess(m.STATE.positions[0]['entry_roundtrip_pnl_pct'], -1.5)

    def test_quote_budget_is_two_candidates_per_scan(self):
        self.assertEqual(m.MAX_QUOTED_CANDIDATES, 2)
        self.assertEqual(m.STATE.snapshot()['config']['max_quoted_candidates_per_scan'], 2)


class Oct4HoldModeTests(unittest.TestCase):
    def test_08_runner_mode_at_conviction_85(self):
        self.assertEqual(oct4.hold_mode(85), {'mode': 'RUNNER', 'max_hold_minutes': 60.0, 'target_pct': None,
                                              'trail_arm_pct': 15.0, 'trail_pct': 7.0})
        self.assertEqual(oct4.hold_mode(100)['mode'], 'RUNNER')

    def test_09_strong_mode_at_conviction_72(self):
        self.assertEqual(oct4.hold_mode(72), {'mode': 'STRONG', 'max_hold_minutes': 30.0, 'target_pct': None,
                                              'trail_arm_pct': 12.0, 'trail_pct': 6.0})
        self.assertEqual(oct4.hold_mode(84.9)['mode'], 'STRONG')

    def test_10_normal_mode_at_conviction_58(self):
        self.assertEqual(oct4.hold_mode(58), {'mode': 'NORMAL', 'max_hold_minutes': 15.0, 'target_pct': 20.0,
                                              'trail_arm_pct': 9.0, 'trail_pct': 5.0})
        self.assertEqual(oct4.hold_mode(71.9)['mode'], 'NORMAL')

    def test_11_cautious_mode_at_conviction_45(self):
        self.assertEqual(oct4.hold_mode(45), {'mode': 'CAUTIOUS', 'max_hold_minutes': 8.0, 'target_pct': 14.0,
                                              'trail_arm_pct': 7.0, 'trail_pct': 4.0})
        self.assertEqual(oct4.hold_mode(57.9)['mode'], 'CAUTIOUS')

    def test_12_weak_mode_below_45(self):
        self.assertEqual(oct4.hold_mode(44.9), {'mode': 'WEAK', 'max_hold_minutes': 4.0, 'target_pct': 8.0,
                                                'trail_arm_pct': 5.0, 'trail_pct': 3.0})
        self.assertEqual(oct4.hold_mode(0)['mode'], 'WEAK')

    def test_conviction_table_matches_october_4(self):
        strong = oct4.conviction_score(fast_ratio=3.0, slow_ratio=2.0, unique_wallets=10, repeat_buy_wallets=3,
                                       whale_buy_usd=1000, whale_sell_usd=0, m5=5, h1=50, market_ratio=1.5,
                                       liquidity_ratio=1.0, neo_score=95)
        boundary = oct4.conviction_score(fast_ratio=1.4, slow_ratio=1.4, unique_wallets=5, repeat_buy_wallets=0,
                                         whale_buy_usd=0, whale_sell_usd=0, m5=5, h1=10, market_ratio=1.1,
                                         liquidity_ratio=0.95, neo_score=90)
        weak = oct4.conviction_score(fast_ratio=0.5, slow_ratio=0.5, unique_wallets=1, repeat_buy_wallets=0,
                                     whale_buy_usd=0, whale_sell_usd=1000, m5=-6, h1=-20, market_ratio=0.5,
                                     liquidity_ratio=0.7, neo_score=80)
        self.assertEqual((strong, boundary, weak), (100.0, 85.0, 0.0))
        self.assertEqual(oct4.hold_mode(boundary)['mode'], 'RUNNER')
        self.assertEqual(oct4.hold_mode(boundary - 4)['mode'], 'STRONG')

    def test_engine_market_context_uses_the_versioned_table(self):
        m.STATE_PATH.unlink(missing_ok=True)
        m.STATE = m.State()
        monitor = m.Monitor()
        self.addCleanup(monitor.stop)
        fast = {'buy_sell_usd_ratio': 3.0}
        slow = {'buy_sell_usd_ratio': 2.0, 'unique_wallets': 10, 'repeat_buy_wallets': 3,
                'whale_buy_usd': 1000, 'whale_sell_usd': 0}
        coin = {'address': A, 'pairAddress': B, 'score': 95, 'liquidityUsd': 100000,
                'priceChange': {'m5': 5, 'h1': 50}, 'txns': {'m5': {'buys': 15, 'sells': 10}}}
        with patch.object(m.STATE, 'live_flow',
                          side_effect=lambda address, seconds=30, pair_address=None: fast if seconds == 30 else slow):
            context = monitor.market_context(coin)
        self.assertEqual(context['conviction'], 100.0)
        self.assertEqual(context['mode'], 'RUNNER')
        self.assertEqual(context['max_hold_minutes'], 60.0)
        self.assertIsNone(context['target_pct'])
        self.assertEqual((context['trail_arm_pct'], context['trail_pct']), (15.0, 7.0))


class Oct4ExitLadderTests(unittest.TestCase):
    def reason(self, conviction, net, peak, hold, fast=None):
        context = {**context_for(conviction), 'fast_flow': fast or {}}
        return exit_policy.exit_reason({}, context, net_pct=net, peak_net_pct=peak, hold_minutes=hold,
                                       stop_pct=5.0, take_profit_pct=10.0, policy='adaptive')

    def test_13_stop_loss_takes_priority(self):
        sell_wave = {'trades': 9, 'sells': 9, 'sell_usd': 9000.0, 'buy_usd': 0.0}
        self.assertEqual(self.reason(95, -5.0, 10.0, 1, fast=sell_wave), 'STOP_LOSS_NET_TARGET')
        self.assertEqual(self.reason(10, -5.0, 0.0, 130), 'STOP_LOSS_NET_TARGET')

    def test_14_runner_and_strong_are_not_closed_at_plus_18(self):
        self.assertIsNone(self.reason(90, 18.0, 18.0, 5))
        self.assertIsNone(self.reason(75, 18.0, 18.0, 5))
        self.assertIsNone(self.reason(60, 18.0, 18.0, 5))
        self.assertEqual(self.reason(60, 20.0, 20.0, 5), 'ADAPTIVE_TP_20')
        self.assertEqual(self.reason(50, 14.0, 14.0, 5), 'ADAPTIVE_TP_14')
        self.assertEqual(self.reason(40, 8.0, 8.0, 1), 'ADAPTIVE_TP_8')

    def test_15_strong_sell_flow_causes_orderflow_exit(self):
        fast = {'trades': 4, 'sells': 3, 'sell_usd': 500.0, 'buy_usd': 100.0}
        self.assertEqual(self.reason(80, 1.0, 1.0, 2, fast=fast), 'ORDERFLOW_EXIT')
        self.assertIsNone(self.reason(80, 3.0, 3.0, 2, fast=fast))
        self.assertIsNone(self.reason(80, 1.0, 1.0, 2, fast={**fast, 'sells': 2}))

    def test_16_conviction_collapse_causes_conviction_exit(self):
        self.assertEqual(self.reason(34, -1.0, 2.0, 3), 'CONVICTION_EXIT')
        self.assertIsNone(self.reason(34, 1.0, 2.0, 3))
        self.assertIsNone(self.reason(35, -1.0, 2.0, 3))

    def test_17_adaptive_trailing_exit(self):
        self.assertEqual(self.reason(75, 7.5, 15.0, 10), 'ADAPTIVE_TRAILING')
        self.assertIsNone(self.reason(75, 9.0, 15.0, 10))
        self.assertIsNone(self.reason(75, 5.0, 11.0, 10))

    def test_18_adaptive_max_hold(self):
        self.assertEqual(self.reason(60, 1.0, 1.0, 15.0), 'ADAPTIVE_MAX_HOLD')
        self.assertIsNone(self.reason(60, 1.0, 1.0, 14.9))
        self.assertIsNone(self.reason(75, 1.0, 1.0, 31.0))

    def test_19_absolute_max_hold_120_minutes(self):
        self.assertEqual(self.reason(90, 1.0, 1.0, 120.0), 'ABSOLUTE_MAX_HOLD')
        self.assertIsNone(self.reason(90, 1.0, 1.0, 119.9))

    def test_19b_conviction_profit_lock_precedes_trailing(self):
        self.assertEqual(self.reason(49, 2.5, 10.0, 5), 'CONVICTION_PROFIT_LOCK')
        # At conviction 50 the lock no longer applies; the NORMAL trail (arm 9, 5%) decides.
        self.assertEqual(self.reason(50, 2.5, 10.0, 5), 'ADAPTIVE_TRAILING')
        self.assertIsNone(self.reason(50, 6.0, 10.0, 5))


class Oct4EngineExitTests(unittest.TestCase):
    def setUp(self):
        m.activate_strategy(oct4.STRATEGY_ID)
        self.addCleanup(m.activate_strategy, m.DEFAULT_SIGNAL_STRATEGY)
        self.temp = tempfile.TemporaryDirectory(prefix='neo-oct4-exit-')
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.clock = [1_000_000]
        patches = [patch.object(m, 'STATE_PATH', root / 'state.json'),
                   patch.object(m, 'AUDIT_PATH', root / 'audit.jsonl'),
                   patch.object(m, 'LIVE_TAPE_PATH', root / 'tape.json'),
                   patch.object(m, 'now_ms', side_effect=lambda: self.clock[0]),
                   patch.object(m, 'sol_usd_market_price', return_value=100),
                   patch.object(m.pumpswap_stop, 'prime_positions')]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        m.STATE = m.State()
        self.monitor = m.Monitor()
        self.addCleanup(self.monitor.stop)
        self.coin = {'address': A, 'pairAddress': B, 'symbol': 'FIXTURE', 'priceUsd': 2, 'priceNative': .02,
                     'liquidityUsd': 200000, 'marketCap': 1000000, 'ageMinutes': 60, 'score': 90,
                     'priceChange': {'m5': 5, 'h1': 8}, 'txns': {'m5': {'buys': 10, 'sells': 3}},
                     'signals': [], 'updatedAt': self.clock[0]}
        self.pos = {'id': 'position-1', 'address': A, 'pairAddress': B, 'session_id': m.STATE.demo_session_id,
                    'entry_price': 2, 'current_price': 2, 'quantity': 100, 'notional_usd': 200,
                    'original_notional_usd': 200, 'capital_committed_usd': 200.23,
                    'entry_network_fee_usd': .03, 'entry_account_reserve_usd': .2,
                    'entry_liquidity_usd': 200000, 'opened_at': self.clock[0], 'updated_at': self.clock[0],
                    'jupiter_token_raw_amount': 100000000, 'execution_mode': 'JUPITER_QUOTE_V2',
                    'coin_snapshot': copy.deepcopy(self.coin), 'exit_policy': 'adaptive',
                    'strategy_id': oct4.STRATEGY_ID, 'entry_policy_version': oct4.ENTRY_POLICY_VERSION}
        self.quote = {'net_proceeds_usd': 200, 'gross_proceeds_usd': 200.03, 'network_fee_usd': .03,
                      'dex_fee_usd': 0, 'fill_price': 2, 'impact_pct': .2, 'slippage_pct': .1, 'latency_pct': 0,
                      'quoted_at': self.clock[0], 'from_cache': False, 'execution_source': 'OFFLINE_FIXTURE'}

    def position(self):
        m.STATE.positions = [copy.deepcopy(self.pos)]
        m.STATE.trade_seq = 1
        return m.STATE.positions[0]

    def test_13b_engine_stop_is_an_uncapped_net_loss(self):
        self.position()
        self.quote['net_proceeds_usd'] = 160
        with patch.object(m.paper_quotes, 'position_mark', return_value=self.quote), \
             patch.object(self.monitor, 'market_context', return_value=context_for(95)):
            self.monitor.update_positions({A: self.coin})
        self.assertFalse(m.STATE.positions)
        closed = m.STATE.history[0]
        self.assertEqual(closed['exit_reason'], 'STOP_LOSS_NET_TARGET')
        self.assertAlmostEqual(closed['pnl_usd'], -40.23)
        self.assertFalse(closed['paper_stop_capped'])
        self.assertEqual(closed['exit_policy_version'], exit_policy.ADAPTIVE_VERSION)

    def test_16b_engine_books_conviction_exit_with_adaptive_version(self):
        self.position()
        with patch.object(m.paper_quotes, 'position_mark', return_value=self.quote), \
             patch.object(self.monitor, 'market_context', return_value=context_for(20)):
            self.monitor.update_positions({A: self.coin})
        self.assertFalse(m.STATE.positions)
        closed = m.STATE.history[0]
        self.assertEqual(closed['exit_reason'], 'CONVICTION_EXIT')
        self.assertEqual(closed['exit_policy_version'], exit_policy.ADAPTIVE_VERSION)
        self.assertEqual(closed['strategy_id'], 'ORDER_FLOW_ADAPTIVE')
        self.assertEqual(closed['market_context']['mode'], 'WEAK')

    def test_14b_engine_keeps_a_runner_open_at_plus_18(self):
        self.position()
        self.quote['net_proceeds_usd'] = 236.23
        with patch.object(m.paper_quotes, 'position_mark', return_value=self.quote), \
             patch.object(self.monitor, 'market_context', return_value=context_for(90)):
            self.monitor.update_positions({A: self.coin})
        self.assertEqual(len(m.STATE.positions), 1)
        self.assertFalse(m.STATE.history)
        self.assertAlmostEqual(m.STATE.positions[0]['pnl_pct'], 18.0)
        self.assertEqual(m.STATE.positions[0]['exit_state'], 'OPEN')

    def test_25_wrong_pool_observation_cannot_mark_the_held_position(self):
        self.position()
        wrong_pool = dict(self.coin, pairAddress=C, priceUsd=1, updatedAt=self.clock[0])
        with patch.object(m.paper_quotes, 'position_mark', return_value=self.quote) as mark, \
             patch.object(self.monitor, 'market_context', return_value=context_for(80)):
            self.monitor.update_positions({A: wrong_pool})
        observed = mark.call_args.args[1]
        self.assertEqual(observed['pairAddress'], B)
        self.assertEqual(observed['priceUsd'], 2)
        self.assertEqual(len(m.STATE.positions), 1)
        self.assertEqual(m.STATE.positions[0]['current_price'], 2)


class Oct4ConfigTests(unittest.TestCase):
    def setUp(self):
        m.activate_strategy(oct4.STRATEGY_ID)
        self.addCleanup(m.activate_strategy, m.DEFAULT_SIGNAL_STRATEGY)
        m.STATE_PATH.unlink(missing_ok=True)
        m.STATE = m.State()

    def test_50_state_exposes_strategy_identity_and_ownership(self):
        config = m.STATE.snapshot()['config']
        expected = {
            'signal_strategy': 'ORDER_FLOW_ADAPTIVE', 'entry_policy_version': 'ORDER_FLOW_BALANCED_V4',
            'exit_policy': 'adaptive', 'exit_policy_version': exit_policy.ADAPTIVE_VERSION,
            'learning_mode': 'ADAPTIVE_CONTEXT_HOLD', 'scan_seconds': 15, 'position_scan_seconds': 2.0,
            'max_positions': 1, 'trade_notional_usd': 200.0, 'max_daily_loss_usd': 100.0,
            'stop_loss_pct': 5.0, 'same_token_cooldown_seconds': 1200, 'entry_score': 85.0,
            'min_liquidity_usd': 30000.0, 'strict_entry_score': 85.0, 'strict_min_conviction': 72.0,
            'strict_min_liquidity_usd': 30000.0, 'strict_max_entry_impact_pct': 2.0,
            'strict_max_roundtrip_cost_pct': 2.75, 'strict_max_worst_case_cost_pct': 4.5,
            'max_quoted_candidates_per_scan': 2, 'paper_only': True,
            'ensemble_strategies': ['ORDER_FLOW_ADAPTIVE'], 'signal_source_commit': oct4.SOURCE_COMMIT,
        }
        for key, value in expected.items():
            self.assertEqual(config[key], value, key)
        profile = config['strategy_profile']
        self.assertEqual(profile['restore_version'], oct4.RESTORE_VERSION)
        self.assertEqual([mode['mode'] for mode in profile['hold_modes']],
                         ['RUNNER', 'STRONG', 'NORMAL', 'CAUTIOUS', 'WEAK'])
        self.assertEqual(profile['absolute_max_hold_minutes'], 120)
        self.assertFalse(profile['profitability_proven'])
        self.assertIn('ORDER_FLOW_ADAPTIVE', config['effective_entry_thresholds'])
        self.assertEqual(config['config_ownership']['scan_seconds'], 'strategy_profile:ORDER_FLOW_ADAPTIVE')
        self.assertEqual(config['config_ownership']['environment_overrides_honored'], 'none for strategy values')
        self.assertEqual(m.STATE.entry_diagnostics['policy_version'], 'ORDER_FLOW_BALANCED_V4')

    def test_51_effective_config_hash_distinguishes_strategies(self):
        adaptive = m.effective_config_hash()
        m.activate_strategy(m.DEFAULT_SIGNAL_STRATEGY)
        default = m.effective_config_hash()
        m.activate_strategy(oct4.STRATEGY_ID)
        self.assertNotEqual(adaptive, default)
        self.assertEqual(m.effective_config_hash(), adaptive)

    def test_52_environment_cannot_tune_the_profile(self):
        with patch.dict(os.environ, {'NEO_STRICT_ENTRY_SCORE': '10', 'NEO_STRICT_MAX_ROUNDTRIP_COST_PCT': '9',
                                     'NEO_SCAN_SECONDS': '1', 'NEO_TRADE_NOTIONAL_USD': '5'}):
            m.activate_strategy(oct4.STRATEGY_ID)
        self.assertEqual((m.STRICT_ENTRY_SCORE, m.STRICT_MAX_ROUNDTRIP_COST_PCT, m.SCAN_SECONDS,
                          m.TRADE_NOTIONAL_USD), (85.0, 2.75, 15, 200.0))

    def test_53_default_strategy_is_unchanged_and_unknown_fails_closed(self):
        m.activate_strategy(m.DEFAULT_SIGNAL_STRATEGY)
        self.assertEqual((m.SIGNAL_STRATEGY, m.ENTRY_POLICY_VERSION, m.POSITION_EXIT_POLICY),
                         (winner_ensemble.VERSION, winner_ensemble.ENTRY_POLICY_VERSION, 'fixed'))
        self.assertEqual((m.MAX_POSITIONS, m.SCAN_SECONDS, m.TRADE_NOTIONAL_USD, m.MAX_DAILY_LOSS_USD,
                          m.STRICT_MAX_ROUNDTRIP_COST_PCT, m.STRICT_MAX_WORST_CASE_COST_PCT,
                          m.MAX_QUOTED_CANDIDATES), (8, 3, 200.0, 100.0, 1.5, 2.5, 6))
        config = m.State().snapshot()['config']
        self.assertEqual(config['signal_strategy'], winner_ensemble.VERSION)
        self.assertEqual(config['exit_policy'], 'fixed')
        self.assertEqual(config['ensemble_strategies'], list(winner_ensemble.STRATEGIES))
        self.assertIsNone(config['strategy_profile'])
        self.assertIn('scan_seconds', config['config_ownership'])
        with self.assertRaises(ValueError):
            m.activate_strategy('SOMETHING_ELSE')
        self.assertEqual(m.SIGNAL_STRATEGY, winner_ensemble.VERSION)

    def test_54_profile_values_match_the_october_4_record(self):
        profile = oct4.PROFILE
        self.assertEqual((profile.scan_seconds, profile.position_scan_seconds, profile.max_positions,
                          profile.trade_notional_usd, profile.max_daily_loss_usd, profile.stop_loss_pct,
                          profile.same_token_cooldown_seconds), (15, 2.0, 1, 200.0, 100.0, 5.0, 1200))
        self.assertEqual((profile.strict_entry_score, profile.strict_min_conviction,
                          profile.strict_min_liquidity_usd, profile.strict_max_entry_impact_pct,
                          profile.strict_max_roundtrip_cost_pct, profile.strict_max_worst_case_cost_pct),
                         (85.0, 72.0, 30000.0, 2.0, 2.75, 4.5))
        self.assertEqual((profile.max_feed_age_ms, profile.max_entry_quote_age_ms, profile.max_quoted_candidates),
                         (30_000, 10_000, 2))
        self.assertEqual((profile.legacy_take_profit_pct, profile.legacy_trailing_pct,
                          profile.legacy_max_hold_minutes), (18.0, 4.0, 7))
        for bad in ({'max_positions': 2}, {'strict_max_roundtrip_cost_pct': 6.0},
                    {'strict_max_worst_case_cost_pct': 2.0}, {'strict_entry_score': 101}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                oct4.Profile(**bad)


if __name__ == '__main__':
    unittest.main()
