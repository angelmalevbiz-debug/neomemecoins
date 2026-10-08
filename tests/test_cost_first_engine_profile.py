"""COST_FIRST_ESTABLISHED_PAPER_V1 engine profile (opt-in, one PAPER account).

Offline fixtures only: quotes, flow, safety and prices are patched; no test
calls an external API or touches a real account. Covers the cost-first
universe as the market screen, the unchanged engine gates, the size rule,
EXIT_IMPACT_EMERGENCY_V2 (sell anchor, fallback anchor, arm then confirm,
disarm, stop priority, fresh forced quote), per-position exit policies, the
/state config and the exact effective_config_hash of all three engine strategies.
"""
import copy
import importlib
import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

_TEMP = tempfile.TemporaryDirectory(prefix='neo-cost-first-engine-tests-')
os.environ['NEO_MARKET_STATE_PATH'] = str(Path(_TEMP.name) / 'state.json')
os.environ['NEO_MARKET_AUDIT_PATH'] = str(Path(_TEMP.name) / 'audit.jsonl')
os.environ['NEO_LIVE_TAPE_PATH'] = str(Path(_TEMP.name) / 'tape.json')
# Values that feed effective_config_hash; the pinned hashes below assume code defaults.
HASH_ENV = ('NEO_SIGNAL_STRATEGY', 'NEO_TRADE_NOTIONAL_USD', 'NEO_MAX_DAILY_LOSS_USD', 'NEO_MAX_POSITION_RISK_USD',
            'NEO_MAX_TOTAL_EXPOSURE_PCT', 'NEO_MAX_DRAWDOWN_PCT', 'NEO_STRICT_ENTRY_SCORE',
            'NEO_STRICT_MIN_CONVICTION', 'NEO_STRICT_MIN_LIQUIDITY_USD', 'NEO_STRICT_MAX_ENTRY_IMPACT_PCT',
            'NEO_STRICT_MAX_ROUNDTRIP_COST_PCT', 'NEO_STRICT_MAX_WORST_CASE_COST_PCT',
            'NEO_PAPER_EXECUTION_DELAY_MS', 'NEO_PAPER_MAX_SIGNAL_AGE_MS', 'NEO_JUPITER_SLIPPAGE_BPS')
# Same keys the ORDER_FLOW_ADAPTIVE suite clears before the engine is imported.
POPPED_ENV = ('NEO_SIGNAL_STRATEGY', 'NEO_TRADE_NOTIONAL_USD', 'NEO_MAX_DAILY_LOSS_USD',
              'NEO_STRICT_ENTRY_SCORE', 'NEO_STRICT_MIN_CONVICTION', 'NEO_STRICT_MIN_LIQUIDITY_USD',
              'NEO_STRICT_MAX_ENTRY_IMPACT_PCT', 'NEO_STRICT_MAX_ROUNDTRIP_COST_PCT')
_ENV_OVERRIDES_AT_IMPORT = [key for key in HASH_ENV if key not in POPPED_ENV and os.environ.get(key) is not None]
for key in POPPED_ENV:
    os.environ.pop(key, None)
import market_monitor as m
import cost_first_engine_profile as cfp
import cost_first_established as cost_first
import engine_exit_policy as exit_policy
import entry_defense
import structural_rug_guard
import order_flow_adaptive_oct4 as oct4
import promoted_entry_guard as promoted_guard
import winner_ensemble
import entry_defense as _isolation_entry_defense
import market_monitor as _isolation_engine

_DEFENSIVE_ISOLATION = []


def _defensive_pass(*_args, **_kwargs):
    return _isolation_entry_defense.pass_decision('TEST_GATE_ISOLATION')


def setUpModule():
    """These tests isolate other entry gates. DEFENSIVE_ENTRY_LAYER_V1 (structural rug
    guard, pool loss memory, heat veto and its warm-up) has its own suite in
    tests/test_defensive_entry_layer.py, which proves every path consults it."""
    for target, name in ((_isolation_engine.Monitor, 'defensive_entry_decision'),):
        isolation = patch.object(target, name, _defensive_pass)
        isolation.start()
        _DEFENSIVE_ISOLATION.append(isolation)


def tearDownModule():
    while _DEFENSIVE_ISOLATION:
        _DEFENSIVE_ISOLATION.pop().stop()

A, B, C = 'A' * 44, 'B' * 44, 'C' * 44
SOL = cost_first.SOL_QUOTE_MINT
TOKENS_PER_USD = 1_000_000  # fixture route: 1 token (6 decimals) per quoted USD

# effective_config_hash of the two existing strategies, computed on origin/main 69be225
# before this profile existed (code defaults, no NEO_* overrides). The cost-first profile
# left them unchanged; DEFENSIVE_ENTRY_LAYER_V1 changes both visibly.
DEFAULT_HASH_AT_69BE225 = 'fe08e29c142e0675cfbde9c6d4728a0a4429a64797fdec4ef7532ca6a183e62c'
ORDER_FLOW_ADAPTIVE_HASH_AT_69BE225 = '405669df3b585e9c69d2cf02706d530590c168c200156fdefaeae4f3666ecdc4'
# effective_config_hash of the three engine strategies with DEFENSIVE_ENTRY_LAYER_V1
# (PR #23 after its sixth review: heat window coverage, 40,000-entry registry with
# 24-hour coverage from discovered market rows only (DISCOVERED_MARKET_ROWS_V2),
# TICKER_COVERAGE_CLOCK_V1 (coverage stamped ahead of the clock never vouches),
# TICKER_REGISTRY_SEED_V4 (running seeds, sibling sightings while vouching),
# TICKER_REGISTRY_SIDECAR_READ_V1, a ticker required only under 14 days; the published
# first-deploy seed procedure as in docs/PAPER_RUNBOOK.md (PR #24 final review r4: journal
# read in place, the before-Stop seed, no seed across an outage over 60 min); code
# defaults, no NEO_* overrides; no position has run under these values yet). Positions
# record this hash as their audit identity, so an engine
# default, a profile field or a layer threshold may only change together with these pins
# (and docs/DEFENSIVE_ENTRY_LAYER.md).
DEFAULT_HASH_WITH_DEFENSIVE_ENTRY_LAYER_V1 = '580e3d25ebb845ff2ddd3e1e83928584bf75ca2208669f1be29adeb466b7b0b7'
ORDER_FLOW_ADAPTIVE_HASH_WITH_DEFENSIVE_ENTRY_LAYER_V1 = (
    'fcf5bd55bf7e5e8b99c1cc9f863236eb679536c31f42bca04db83e473f55d7ba')
COST_FIRST_HASH_WITH_DEFENSIVE_ENTRY_LAYER_V1 = '4ff33f9d0111846ff0d25e18f70574566a059a0832f7232bcbd1c7e887c97e10'


def universe_coin(now, **overrides):
    """A PumpSwap/SOL pool in the 30 bps tier with $400k liquidity (inside the universe)."""
    coin = {'address': A, 'pairAddress': B, 'symbol': 'ESTAB', 'name': 'Offline established fixture',
            'dexId': 'pumpswap', 'quoteTokenAddress': SOL, 'quoteToken': {'address': SOL},
            'score': 40, 'liquidityUsd': 400_000, 'marketCap': 10_000_000, 'priceUsd': .001,
            'priceNative': .00001, 'ageMinutes': 9000, 'priceChange': {'m5': 0.2, 'h1': -1.0},
            'txns': {'m5': {'buys': 20, 'sells': 18}}, 'volume': {'h1': 50000}, 'signals': [],
            'updatedAt': now, 'pairCreatedAt': now - 9000 * 60_000}
    coin.update(overrides)
    return coin


# The universe includes STRUCTURAL_RUG_GUARD_V1 (cost_first_established V2); the
# pure checks run at a fixed clock with an empty ticker registry that has watched
# the market for 48 h (TICKER_REGISTRY_V2_COVERAGE: 'no other mint seen' counts only
# after 24 h; tests/test_defensive_entry_layer.py covers that rule).
FIXED_NOW = 1_800_000_000_000


def covered_registry(now):
    registry = structural_rug_guard.TickerRegistry()
    for stamp in range(int(now) - 48 * 3_600_000, int(now) + 1, 1_800_000):
        registry.mark_observed(stamp)
    return registry


REGISTRY = covered_registry(FIXED_NOW)


def universe_rejections(coin, cap_usd):
    return cfp.universe_rejections(coin, cap_usd, now=FIXED_NOW, ticker_registry=REGISTRY)


def universe_metrics(coin, cap_usd):
    return cfp.universe_metrics(coin, cap_usd, now=FIXED_NOW, ticker_registry=REGISTRY)


def confirmed_flow(now, **overrides):
    verified = {'source': promoted_guard.FLOW_SOURCE, 'coverage_status': 'COMPLETE',
                'window_ms': promoted_guard.FLOW_WINDOW_MS, 'address': A, 'pairAddress': B,
                'window_at': now, 'latest_event_at': now - 100, 'available_at': now - 50,
                'trades': 4, 'unique_wallets': 3, 'buy_usd': 300.0, 'sell_usd': 100.0}
    verified.update(overrides)
    return {'quality': 'COMPLETE', 'fresh': True, 'seconds': 30, 'trades': 4, 'buys': 3, 'sells': 1,
            'buy_usd': 300.0, 'sell_usd': 100.0, 'verified_flow': verified}


class UniverseAndSizeRuleTests(unittest.TestCase):
    """The profile imports the merged cost-first definitions; it never restates them."""

    def test_universe_candidate_passes(self):
        coin = universe_coin(FIXED_NOW)
        self.assertEqual(universe_rejections(coin, 200.0), [])
        metrics = universe_metrics(coin, 200.0)
        self.assertEqual(metrics['fee_tier_bps'], 30.0)
        self.assertEqual(metrics['planned_notional_usd'], 200.0)
        self.assertLessEqual(metrics['fee_impact_roundtrip_pct'], 1.2)
        self.assertFalse(metrics['is_execution_quote'])

    def test_fee_tier_above_50_bps_rejects(self):
        coin = universe_coin(FIXED_NOW, marketCap=500_000)   # 5,000 SOL -> 100 bps
        self.assertEqual(universe_rejections(coin, 200.0), ['fee_tier_above_maximum'])
        self.assertEqual(universe_metrics(coin, 200.0)['fee_tier_bps'], 100.0)

    def test_liquidity_below_250k_rejects(self):
        self.assertEqual(universe_rejections(universe_coin(FIXED_NOW, liquidityUsd=249_999), 200.0),
                         ['liquidity_below_minimum'])

    def test_fee_plus_impact_above_1_2_pct_rejects(self):
        coin = universe_coin(FIXED_NOW, marketCap=6_000_000, liquidityUsd=250_000)   # 50 bps tier, thin for its size
        self.assertEqual(universe_rejections(coin, 250.0), ['fee_impact_roundtrip_above_maximum'])
        self.assertGreater(universe_metrics(coin, 250.0)['fee_impact_roundtrip_pct'], 1.2)

    def test_non_pumpswap_or_non_sol_or_unknown_cap_rejects(self):
        self.assertIn('dex_not_pumpswap', universe_rejections(universe_coin(FIXED_NOW, dexId='raydium'), 200.0))
        self.assertIn('quote_token_not_sol', universe_rejections(
            universe_coin(FIXED_NOW, quoteTokenAddress='USDC', quoteToken={'address': 'USDC'}), 200.0))
        self.assertEqual(universe_rejections(universe_coin(FIXED_NOW, marketCap=None, fdv=None), 200.0),
                         ['market_cap_unknown'])

    def test_size_rule_only_shrinks_the_engine_notional(self):
        self.assertEqual(cfp.requested_notional(universe_coin(FIXED_NOW), 200.0), 200.0)
        self.assertEqual(cfp.requested_notional(universe_coin(FIXED_NOW, liquidityUsd=300_000), 500.0), 300.0)
        self.assertEqual(cfp.requested_notional(universe_coin(FIXED_NOW, liquidityUsd=0), 500.0), 0.0)
        self.assertEqual(cfp.SIZE_POLICY, 'COST_FIRST_LIQUIDITY_SCALED_V1')


class CostFirstEntryTests(unittest.TestCase):
    def setUp(self):
        m.activate_strategy(cfp.STRATEGY_ID)
        self.addCleanup(m.activate_strategy, m.DEFAULT_SIGNAL_STRATEGY)
        m.STATE_PATH.unlink(missing_ok=True)
        m.STATE = m.State()
        self.monitor = m.Monitor()
        now = m.now_ms()
        # A fresh, non-persistent defensive layer per test: no ticker memory
        # leaks between tests through the sidecar next to the shared state path;
        # its registry has watched the market for 48 h (no coverage warm-up here).
        self.monitor._entry_defense = entry_defense.DefensiveEntryLayer(registry=covered_registry(now))
        self.addCleanup(self.monitor.stop)
        self.coin = universe_coin(now)
        self.flow = confirmed_flow(now)
        self.buy_impact, self.sell_impact, self.roundtrip_loss = 0.4, 0.7, 0.008
        self.entry_notionals = []
        self.safety = {'status': 'pass', 'mint': A, 'pair': B, 'checked_at': now,
                       'metrics': {'decimals': 6, 'token_account_rent_lamports': 1650000}}
        self.price = {'status': 'pass', 'version': 'TEST'}

        def entry_quote(mint, pair, notional):
            self.entry_notionals.append(float(notional))
            raw = int(round(float(notional) * TOKENS_PER_USD))
            return {'token_raw_expected': raw, 'token_raw_floor': int(raw * .99), 'token_raw_amount': raw,
                    'input_usdc_raw': int(round(float(notional) * 1e6)), 'price_impact_pct': self.buy_impact,
                    'raw_quote': {'fixture': True, 'priceImpactPct': str(self.buy_impact / 100)},
                    'slippage_bps': 100, 'route': [], 'quoted_at': m.paper_quotes.stamp()}

        def exit_quote(mint, raw, pair=None, purpose='exit', force=False):
            expected = raw / TOKENS_PER_USD * (1 - self.roundtrip_loss)
            return {'expected_usdc': expected, 'floor_usdc': expected * .99, 'provider_expected_usdc': expected,
                    'price_impact_pct': self.sell_impact, 'quoted_at': m.paper_quotes.stamp(),
                    'raw_quote': {'fixture': True, 'priceImpactPct': str(self.sell_impact / 100)}}

        self.patches = [
            patch.object(m.STATE, 'live_flow', side_effect=lambda address, seconds=30, pair_address=None: self.flow),
            patch.object(self.monitor, 'market_context', side_effect=lambda coin, position=None: {'conviction': 50}),
            patch.object(m.paper_quotes, 'entry_quote', side_effect=entry_quote),
            patch.object(m.paper_quotes, 'exit_quote', side_effect=exit_quote),
            patch.object(m, 'append_audit'),
            patch.object(m.rug_guard, 'check', side_effect=lambda coin: self.safety),
            patch.object(m.price_integrity, 'check', side_effect=lambda coin: self.price),
        ]
        for p in self.patches:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in reversed(self.patches)])

    def open_once(self, feed=None):
        self.monitor.maybe_open(feed or [self.coin])
        return m.STATE.entry_diagnostics

    def assert_rejected_before_quote(self, reason):
        report = self.open_once()
        self.assertFalse(m.STATE.positions)
        self.assertIn(reason, report['rejections'], report)
        self.assertEqual(self.entry_notionals, [])
        return report

    def test_valid_universe_entry_opens_with_profile_identity(self):
        report = self.open_once()
        self.assertEqual(report['status'], 'opened', report)
        self.assertEqual(report['policy_version'], 'COST_FIRST_ESTABLISHED_ENTRY_V2')
        self.assertEqual(report['signal_strategy'], 'COST_FIRST_ESTABLISHED_PAPER_V1')
        pos = m.STATE.positions[0]
        self.assertEqual(pos['strategy_id'], cfp.STRATEGY_ID)
        self.assertEqual(pos['entry_policy_version'], 'COST_FIRST_ESTABLISHED_ENTRY_V2')
        self.assertEqual(pos['entry_mode'], 'COST_FIRST_ESTABLISHED_ENTRY_V2')
        self.assertEqual(pos['exit_policy'], 'cost_first')
        self.assertEqual(pos['exit_policy_version'], 'COST_FIRST_NET_EXIT_V1')
        self.assertEqual(pos['signal_evidence'], cfp.SIGNAL_EVIDENCE)
        self.assertEqual(pos['learning_mode'], cfp.LEARNING_MODE)
        self.assertEqual(pos['strategy_matches'], [cfp.STRATEGY_ID])
        self.assertEqual(pos['notional_usd'], 200.0)
        self.assertEqual(pos['size_rule_notional_usd'], 200.0)
        self.assertEqual(pos['take_profit_net_pct'], 10.0)
        self.assertEqual(pos['planned_stop_net_pct'], -5.0)
        self.assertAlmostEqual(pos['entry_sell_impact_pct'], 0.7)
        self.assertAlmostEqual(pos['entry_price_impact_pct'], 0.4)
        self.assertEqual(pos['exit_impact_emergency_version'], 'EXIT_IMPACT_EMERGENCY_V2')
        self.assertEqual(pos['cost_first_universe']['fee_tier_bps'], 30.0)
        self.assertEqual(pos['verified_entry_flow']['source'], promoted_guard.FLOW_SOURCE)
        self.assertEqual(pos['effective_config_hash'], m.effective_config_hash())
        self.assertEqual(pos['signal_source_commit'], cost_first.VERSION)

    def test_fee_tier_rejection_is_recorded_with_metrics(self):
        self.coin['marketCap'] = 500_000   # 5,000 SOL -> 100 bps
        report = self.assert_rejected_before_quote('fee_tier_above_maximum')
        example = report['examples'][0]
        self.assertEqual(example['reasons'], ['fee_tier_above_maximum'])
        self.assertEqual(example['metrics']['fee_tier_bps'], 100.0)
        self.assertEqual(example['metrics']['max_fee_tier_bps'], 50.0)

    def test_liquidity_rejection_is_recorded_with_metrics(self):
        self.coin['liquidityUsd'] = 200_000
        report = self.assert_rejected_before_quote('liquidity_below_minimum')
        self.assertEqual(report['examples'][0]['metrics']['liquidity_usd'], 200_000)
        self.assertEqual(report['examples'][0]['metrics']['min_liquidity_usd'], 250_000)

    def test_cost_rejection_is_recorded_with_metrics(self):
        self.coin.update(marketCap=6_000_000, liquidityUsd=250_000)
        with patch.object(m, 'TRADE_NOTIONAL_USD', 250.0):
            report = self.assert_rejected_before_quote('fee_impact_roundtrip_above_maximum')
        metrics = report['examples'][0]['metrics']
        self.assertGreater(metrics['fee_impact_roundtrip_pct'], metrics['max_fee_impact_roundtrip_pct'])

    def test_ensemble_market_rule_is_not_the_screen(self):
        # Score 40 fails every ensemble rule; the cost-first universe does not gate on score.
        self.assertFalse(winner_ensemble.market_candidates(self.coin))
        self.assertTrue(m.market_candidate(self.coin, m.now_ms(), self.monitor.defense.registry))
        # The universe's structural rug guard fails closed without a ticker registry.
        self.assertFalse(m.market_candidate(self.coin, m.now_ms()))
        self.assertEqual(self.open_once()['status'], 'opened')

    def test_confirmed_exact_pool_flow_is_still_required(self):
        self.flow = {'quality': 'UNKNOWN', 'verified_flow': None}
        self.assert_rejected_before_quote('promoted_verified_flow_unavailable')
        self.flow = confirmed_flow(m.now_ms(), sell_usd=290.0)   # buy/sell 300/290 < 1.2x
        self.assert_rejected_before_quote('promoted_buy_pressure_unconfirmed')

    def test_rug_guard_and_price_integrity_still_required(self):
        self.safety = dict(self.safety, status='fail', reasons=['mint_authority'])
        self.assert_rejected_before_quote('mint_authority')
        self.safety = dict(self.safety, status='pass', reasons=[])
        self.price = {'status': 'fail', 'reason': 'price_source_disagreement'}
        self.assert_rejected_before_quote('price_source_disagreement')

    def test_engine_quote_cost_caps_still_enforced_with_size_backoff(self):
        self.assertEqual((m.STRICT_MAX_ROUNDTRIP_COST_PCT, m.STRICT_MAX_WORST_CASE_COST_PCT,
                          m.STRICT_MAX_ENTRY_IMPACT_PCT), (1.5, 2.5, 1.75))
        self.roundtrip_loss = 0.02   # about -2.1% expected round trip > 1.5% cap
        report = self.open_once()
        self.assertFalse(m.STATE.positions)
        self.assertIn('roundtrip_cost', report['rejections'])
        self.assertEqual(self.entry_notionals[0], 200.0)
        self.assertLess(min(self.entry_notionals), 200.0, 'size backoff retries smaller sizes as in the default')

    def test_engine_impact_cap_still_enforced(self):
        self.buy_impact = 1.8
        report = self.open_once()
        self.assertFalse(m.STATE.positions)
        self.assertIn('impact', report['rejections'])

    def test_notional_goes_through_daily_budget_sizing(self):
        with patch.object(m.STATE, 'risk_day_pnl', return_value=-95.0):
            report = self.open_once()
        self.assertEqual(report['status'], 'opened', report)
        pos = m.STATE.positions[0]
        self.assertEqual(pos['size_rule_notional_usd'], 200.0)
        self.assertLess(pos['notional_usd'], 100.0)
        self.assertTrue(pos['size_limited_by_daily_budget'])

    def test_size_rule_shrinks_a_larger_engine_notional(self):
        self.coin['liquidityUsd'] = 300_000
        with patch.object(m, 'TRADE_NOTIONAL_USD', 500.0), patch.object(m, 'MAX_POSITION_RISK_USD', 1000.0):
            report = self.open_once()
        self.assertEqual(report['status'], 'opened', report)
        self.assertEqual(m.STATE.positions[0]['size_rule_notional_usd'], 300.0)
        self.assertEqual(m.STATE.positions[0]['notional_usd'], 300.0)
        # The size rule, not the daily budget, set 300 < the 500 engine notional.
        self.assertFalse(m.STATE.positions[0]['size_limited_by_daily_budget'])

    def test_universe_is_rechecked_at_commit(self):
        drained = dict(self.coin, liquidityUsd=200_000)
        m.STATE.feed = [drained]
        report = self.open_once([self.coin])
        self.assertFalse(m.STATE.positions)
        self.assertIn('liquidity_below_minimum', report['rejections'])
        self.assertTrue(self.entry_notionals, 'the quote ran; the commit-time recheck refused')

    def test_losses_do_not_throttle_the_fixed_hypothesis(self):
        m.STATE.history = [{'id': f'loss-{i}', 'address': C, 'strategy_id': cfp.STRATEGY_ID,
                            'strategy_matches': [cfp.STRATEGY_ID], 'pnl_usd': -5.0, 'closed_at': 1}
                           for i in range(20)]
        self.assertEqual(self.open_once()['status'], 'opened')


class CostFirstExitTests(unittest.TestCase):
    def setUp(self):
        m.activate_strategy(cfp.STRATEGY_ID)
        self.addCleanup(m.activate_strategy, m.DEFAULT_SIGNAL_STRATEGY)
        self.temp = tempfile.TemporaryDirectory(prefix='neo-cost-first-exit-')
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
        self.coin = universe_coin(self.clock[0], priceUsd=2, priceNative=.02)
        self.pos = {'id': 'position-1', 'address': A, 'pairAddress': B, 'session_id': m.STATE.demo_session_id,
                    'entry_price': 2, 'current_price': 2, 'quantity': 100, 'notional_usd': 200,
                    'original_notional_usd': 200, 'capital_committed_usd': 200.23,
                    'entry_network_fee_usd': .03, 'entry_account_reserve_usd': .2,
                    'entry_liquidity_usd': 400000, 'opened_at': self.clock[0], 'updated_at': self.clock[0],
                    'jupiter_token_raw_amount': 100000000, 'execution_mode': 'JUPITER_QUOTE_V2',
                    'coin_snapshot': copy.deepcopy(self.coin), 'exit_policy': 'cost_first',
                    'exit_policy_version': cfp.EXIT_POLICY_VERSION, 'strategy_id': cfp.STRATEGY_ID,
                    'entry_policy_version': cfp.ENTRY_POLICY_VERSION, 'entry_price_impact_pct': 0.6,
                    'entry_sell_impact_pct': 1.0,
                    'preflight_sell_quote': {'priceImpactPct': '0.01'}}
        self.mark = self.quote(impact=0.3, from_cache=True)
        self.confirm = self.quote(impact=1.7)
        self.calls = []
        self.forced_at = []
        self.confirm_age_ms = 0

        def position_mark(position, coin, network, force=False):
            self.calls.append(force)
            if force:
                self.forced_at.append(self.clock[0])
                return (None if self.confirm is None
                        else dict(self.confirm, quoted_at=self.clock[0] - self.confirm_age_ms))
            return dict(self.mark, quoted_at=self.clock[0])

        p = patch.object(m.paper_quotes, 'position_mark', side_effect=position_mark)
        p.start()
        self.addCleanup(p.stop)

    def quote(self, *, impact, net=200.0, from_cache=False, exact_pool=True):
        return {'net_proceeds_usd': net, 'gross_proceeds_usd': net + .03, 'network_fee_usd': .03,
                'dex_fee_usd': 0, 'fill_price': 2, 'impact_pct': impact, 'slippage_pct': .1, 'latency_pct': 0,
                'quoted_at': self.clock[0], 'from_cache': from_cache, 'execution_source': 'OFFLINE_FIXTURE',
                'route_matches_entry_pool': exact_pool, 'route': [{'ammKey': B if exact_pool else C}]}

    def position(self, **overrides):
        position = copy.deepcopy(self.pos)
        position.update(overrides)
        m.STATE.positions = [position]
        m.STATE.trade_seq = 1
        return position

    def tick(self, ms=0):
        self.clock[0] += ms
        self.coin['updatedAt'] = self.clock[0]
        self.monitor.update_positions({A: self.coin})

    def state_v2(self):
        return m.STATE.positions[0].get('exit_impact_emergency_v2') or {}

    def test_threshold_anchors_on_entry_preflight_sell_impact(self):
        rule = cfp.emergency_threshold(self.pos)
        self.assertEqual((rule['anchor'], rule['anchor_source'], rule['threshold_pct']),
                         (cfp.ANCHOR_SELL, 'entry_sell_impact_pct', 1.5))
        raw_only = dict(self.pos, entry_sell_impact_pct=None, preflight_sell_quote={'priceImpactPct': '0.012'})
        rule = cfp.emergency_threshold(raw_only)
        self.assertEqual((rule['anchor'], rule['anchor_source']), (cfp.ANCHOR_SELL, 'preflight_sell_quote'))
        self.assertAlmostEqual(rule['threshold_pct'], 1.7)
        floor = cfp.emergency_threshold(dict(self.pos, entry_sell_impact_pct=0.1))
        self.assertEqual(floor['threshold_pct'], 0.75)

    def test_sell_anchor_ignores_v1_quote_noise(self):
        # 1.3% clears V1 (max(0.75, 0.6 buy + 0.5) = 1.1) but not V2 (1.0 sell + 0.5 = 1.5).
        self.position()
        self.mark = self.quote(impact=1.3)
        self.tick()
        self.assertEqual(len(m.STATE.positions), 1)
        self.assertFalse(self.state_v2().get('armed'))
        self.assertNotIn(True, self.calls)
        # The same mark on a position opened under the default fixed policy fires V1.
        self.position(exit_policy='fixed', exit_policy_version=exit_policy.VERSION)
        self.tick()
        self.assertFalse(m.STATE.positions)
        closed = m.STATE.history[0]
        self.assertEqual(closed['exit_reason'], 'EXIT_IMPACT_EMERGENCY')
        self.assertEqual(closed['exit_policy_version'], exit_policy.VERSION)
        self.assertFalse(closed['exit_impact_emergency']['rule_changed'])

    def test_fallback_anchor_is_the_buy_impact_and_is_recorded(self):
        self.position(entry_sell_impact_pct=None, preflight_sell_quote=None)
        self.mark = self.quote(impact=1.2)
        self.tick()
        state = self.state_v2()
        self.assertTrue(state['armed'])
        self.assertEqual(state['anchor'], cfp.ANCHOR_BUY_FALLBACK)
        self.assertEqual(state['anchor_source'], 'entry_price_impact_pct')
        self.assertAlmostEqual(state['threshold_pct'], 1.1)

    def test_arm_then_confirm_exits_with_the_confirming_quote(self):
        self.position()
        self.mark = self.quote(impact=1.6, from_cache=True)
        self.tick()
        self.assertEqual(len(m.STATE.positions), 1, 'the first trigger only arms')
        armed = self.state_v2()
        self.assertTrue(armed['armed'])
        self.assertEqual(armed['armed_at'], self.clock[0])
        self.assertEqual(armed['trigger_quote']['impact_pct'], 1.6)
        self.assertEqual(m.STATE.positions[0]['exit_state'], 'OPEN')
        self.tick(1000)   # still inside the 2 s confirmation delay: no forced quote, no exit
        self.assertEqual(len(m.STATE.positions), 1)
        self.assertNotIn(True, self.calls)
        self.tick(1500)
        self.assertEqual(self.calls[-1], True, 'a fresh forced quote decides')
        self.assertFalse(m.STATE.positions)
        closed = m.STATE.history[0]
        self.assertEqual(closed['exit_reason'], 'EXIT_IMPACT_EMERGENCY')
        self.assertEqual(closed['exit_policy_version'], 'COST_FIRST_NET_EXIT_V1')
        evidence = closed['exit_impact_emergency']
        self.assertEqual(evidence['rule_version'], 'EXIT_IMPACT_EMERGENCY_V2')
        self.assertTrue(evidence['rule_changed'])
        self.assertEqual(evidence['threshold_pct'], 1.5)
        self.assertEqual(evidence['threshold_anchor'], cfp.ANCHOR_SELL)
        self.assertEqual(evidence['trigger_quote']['impact_pct'], 1.6)
        self.assertEqual(evidence['confirming_quote']['impact_pct'], 1.7)
        self.assertFalse(evidence['confirming_quote']['from_cache'])
        self.assertTrue(evidence['confirming_quote_meets_threshold'])
        self.assertEqual(evidence['booked_quote'], 'confirming')
        self.assertEqual(evidence['booked_impact_pct'], 1.7)
        self.assertGreaterEqual(evidence['confirm_delay_ms'], 2000)
        self.assertEqual(evidence['entry_preflight']['buy_impact_pct'], 0.6)
        self.assertEqual(evidence['liquidity']['exit_to_entry_ratio'], 1.0)
        self.assertEqual(closed['jupiter_exit_quote_at'], self.clock[0])

    def test_arm_then_non_confirmation_disarms_and_keeps_the_position(self):
        self.position()
        self.mark = self.quote(impact=1.6)
        self.tick()
        self.confirm = self.quote(impact=1.2)
        self.mark = self.quote(impact=0.4)
        self.tick(2500)
        self.assertEqual(len(m.STATE.positions), 1)
        self.assertFalse(m.STATE.history)
        state = self.state_v2()
        self.assertFalse(state['armed'])
        self.assertEqual(state['disarm_count'], 1)
        self.assertEqual(state['last_disarm']['reason'], 'confirm_below_threshold')
        self.assertEqual(state['last_disarm']['confirm_quote']['impact_pct'], 1.2)
        self.assertEqual(state['last_disarm']['trigger_impact_pct'], 1.6)
        self.assertEqual(m.STATE.positions[0]['exit_state'], 'OPEN')
        # Inside the re-arm cooldown a trigger is only counted; after it, a trigger
        # arms again and never exits on the first sight.
        self.mark = self.quote(impact=1.6)
        self.tick(500)
        self.assertFalse(self.state_v2()['armed'])
        self.assertEqual(self.state_v2()['trigger_blocks'], {'trigger_in_rearm_cooldown': 1})
        self.tick(cfp.EIE_V2.rearm_cooldown_ms)
        self.assertTrue(self.state_v2()['armed'])
        self.assertEqual(self.state_v2()['arm_count'], 2)
        self.assertEqual(len(m.STATE.positions), 1)

    def test_fresh_forced_exact_pool_quote_is_required(self):
        cases = {'confirm_quote_cached': self.quote(impact=1.9, from_cache=True),
                 'confirm_quote_not_exact_pool': self.quote(impact=1.9, exact_pool=False),
                 'confirm_quote_unavailable': None}
        for code, confirm in cases.items():
            with self.subTest(code=code):
                m.STATE.history = []
                self.position()
                self.mark = self.quote(impact=1.6)
                self.tick()
                self.confirm = confirm
                self.tick(2500)
                self.assertEqual(len(m.STATE.positions), 1)
                self.assertFalse(m.STATE.history)
                self.assertEqual(self.state_v2()['last_disarm']['reason'], code)

    def test_confirm_quote_problem_rejects_stale_and_early_quotes(self):
        good = self.quote(impact=1.9)
        good['quoted_at'] = 5000
        problem = lambda quote, now: cfp.confirm_quote_problem(quote, armed_at=2000, now=now,
                                                               max_age_ms=10_000, threshold_pct=1.5)
        self.assertIsNone(problem(good, 5000))
        self.assertEqual(problem(dict(good, quoted_at=3000), 5000), 'confirm_quote_before_delay')
        self.assertEqual(problem(good, 16_000), 'confirm_quote_stale')
        self.assertEqual(problem(dict(good, impact_pct=None), 5000), 'confirm_quote_invalid')

    def test_off_pool_mark_never_arms_or_takes_a_forced_quote(self):
        # Review probe: an off-pool best route at/above the threshold re-armed every
        # 0.5 s and took a forced exit-priority quote every 2 s, although confirmation
        # requires the exact entry pool (40 ticks -> 8 forced quotes, no exit).
        self.position()
        self.mark = self.quote(impact=1.9, exact_pool=False)
        for _ in range(40):
            self.tick(500)
        self.assertEqual(len(m.STATE.positions), 1)
        self.assertFalse(m.STATE.history)
        self.assertNotIn(True, self.calls, 'no forced quote for a mark that can never confirm')
        state = self.state_v2()
        self.assertFalse(state['armed'])
        self.assertEqual(state['arm_count'], 0)
        self.assertEqual(state['trigger_blocks'], {'trigger_not_exact_pool': 40})
        self.assertEqual(state['last_trigger_block']['reason'], 'trigger_not_exact_pool')
        self.assertEqual(state['last_trigger_block']['impact_pct'], 1.9)
        # The same impact on the exact entry pool arms and is confirmed as before.
        self.mark = self.quote(impact=1.9)
        self.tick(500)
        self.assertTrue(self.state_v2()['armed'])
        self.tick(cfp.EIE_V2.confirm_delay_ms)
        self.assertEqual(m.STATE.history[0]['exit_reason'], 'EXIT_IMPACT_EMERGENCY')
        self.assertEqual(self.forced_at, [self.clock[0]])

    def test_oscillating_trigger_spends_a_bounded_number_of_forced_quotes(self):
        params = cfp.EIE_V2
        self.position()
        self.confirm = self.quote(impact=1.2)   # never confirms
        high, low = self.quote(impact=1.6), self.quote(impact=0.4)
        start = self.clock[0]
        for i in range(1200):   # 10 minutes of 0.5 s position ticks around the 1.5% threshold
            self.mark = high if i % 2 == 0 else low
            self.tick(500)
        self.assertEqual(len(m.STATE.positions), 1)
        self.assertFalse(m.STATE.history)
        # At most max_confirms_per_window forced quotes per rolling window, each one
        # separated by at least the confirmation delay plus the re-arm cooldown.
        self.assertLessEqual(len(self.forced_at), 2 * params.max_confirms_per_window)
        first_window = [t for t in self.forced_at if t - self.forced_at[0] < params.confirm_window_ms]
        self.assertEqual(len(first_window), params.max_confirms_per_window)
        for earlier, later in zip(self.forced_at, self.forced_at[1:]):
            self.assertGreaterEqual(later - earlier, params.rearm_cooldown_ms + params.confirm_delay_ms)
        for t in self.forced_at:
            in_window = [u for u in self.forced_at if 0 <= t - u < params.confirm_window_ms]
            self.assertLessEqual(len(in_window), params.max_confirms_per_window)
        state = self.state_v2()
        self.assertEqual(state['disarm_count'], len(self.forced_at))
        self.assertEqual(state['confirm_count'], len(self.forced_at))
        self.assertEqual(state['arm_count'], len(self.forced_at) + int(bool(state['armed'])))
        self.assertGreater(state['trigger_blocks']['trigger_in_rearm_cooldown'], 0)
        self.assertGreater(state['trigger_blocks']['trigger_confirm_budget_exhausted'], 0)
        self.assertLessEqual(len(state['confirm_attempts_at']), params.max_confirms_per_window)
        self.assertLess(len(self.forced_at), (self.clock[0] - start) / 2000 / 10)

    def test_net_geometry_runs_on_any_fresh_confirm_quote(self):
        stop, target = dict(net=188.0), dict(net=221.0)   # about -6.1% / +10.4% net
        cases = [('off-pool stop', self.quote(impact=1.9, exact_pool=False, **stop), 'STOP_LOSS_NET_TARGET'),
                 ('cached stop', self.quote(impact=1.9, from_cache=True, **stop), 'STOP_LOSS_NET_TARGET'),
                 ('below-threshold stop', self.quote(impact=1.2, **stop), 'STOP_LOSS_NET_TARGET'),
                 ('off-pool target', self.quote(impact=1.9, exact_pool=False, **target), 'TAKE_PROFIT_10_NET')]
        for label, confirm, expected in cases:
            with self.subTest(label):
                m.STATE.history = []
                self.position()
                self.mark = self.quote(impact=1.6)
                self.tick()
                self.assertTrue(self.state_v2()['armed'])
                self.confirm = confirm
                self.tick(2500)
                self.assertFalse(m.STATE.positions)
                closed = m.STATE.history[0]
                self.assertEqual(closed['exit_reason'], expected)
                self.assertEqual(closed['exit_impact_emergency_v2']['superseded_by'], expected)
                self.assertNotIn('exit_impact_emergency', closed)
        # A stale or missing confirmation quote is not a mark: it only disarms.
        for code, age, confirm in (('confirm_quote_stale', 11_000, self.quote(impact=1.9, **stop)),
                                   ('confirm_quote_unavailable', 0, None)):
            with self.subTest(code):
                m.STATE.history = []
                self.confirm_age_ms = 0
                self.position()
                self.mark = self.quote(impact=1.6)
                self.tick()
                self.confirm, self.confirm_age_ms = confirm, age
                self.tick(2500)
                self.assertEqual(len(m.STATE.positions), 1)
                self.assertFalse(m.STATE.history)
                self.assertEqual(self.state_v2()['last_disarm']['reason'], code)

    def test_unknown_exit_policy_falls_back_to_the_fixed_geometry(self):
        # Older code raised 'unknown exit policy' inside the position loop; the
        # defensive mapping uses the fixed net geometry and logs the name once.
        exit_policy._FALLBACK_LOGGED.discard('FUTURE_POLICY_PROBE')
        decide = lambda **kw: exit_policy.exit_reason({}, {}, policy='FUTURE_POLICY_PROBE', peak_net_pct=0, **kw)
        with self.assertLogs(exit_policy.LOG, level='WARNING') as logs:
            self.assertEqual(decide(net_pct=-6, hold_minutes=1), 'STOP_LOSS_NET_TARGET')
            self.assertEqual(decide(net_pct=11, hold_minutes=1), 'TAKE_PROFIT_10_NET')
            self.assertEqual(decide(net_pct=0, hold_minutes=61), 'MAX_HOLD_60')
            self.assertIsNone(decide(net_pct=0, hold_minutes=1))
        self.assertEqual(len(logs.records), 1)
        self.assertIn('FUTURE_POLICY_PROBE', logs.output[0])
        self.assertEqual(exit_policy.geometry_policy('adaptive'), 'adaptive')
        self.assertEqual(exit_policy.geometry_policy('fixed'), 'fixed')
        # In the engine the position is managed instead of crashing the position loop.
        self.position(exit_policy='FUTURE_POLICY_PROBE')
        self.mark = self.quote(impact=0.3, net=221.0)
        self.tick()
        self.assertFalse(m.STATE.positions)
        self.assertEqual(m.STATE.history[0]['exit_reason'], 'TAKE_PROFIT_10_NET')

    def test_stop_loss_has_priority_on_the_arming_mark(self):
        self.position()
        self.mark = self.quote(impact=2.5, net=188.0)   # about -6.1% net
        self.tick()
        self.assertFalse(m.STATE.positions)
        closed = m.STATE.history[0]
        self.assertEqual(closed['exit_reason'], 'STOP_LOSS_NET_TARGET')
        self.assertNotIn('exit_impact_emergency', closed)
        self.assertLess(closed['pnl_usd'], -10)   # never clamped to the stop

    def test_stop_loss_has_priority_on_the_confirming_quote(self):
        self.position()
        self.mark = self.quote(impact=1.6)
        self.tick()
        self.confirm = self.quote(impact=2.5, net=188.0)
        self.tick(2500)
        self.assertFalse(m.STATE.positions)
        closed = m.STATE.history[0]
        self.assertEqual(closed['exit_reason'], 'STOP_LOSS_NET_TARGET')
        self.assertEqual(closed['exit_impact_emergency_v2']['superseded_by'], 'STOP_LOSS_NET_TARGET')
        self.assertNotIn('exit_impact_emergency', closed)

    def test_fixed_net_geometry_take_profit_and_max_hold(self):
        self.position()
        self.mark = self.quote(impact=0.3, net=221.0)   # about +10.4% net
        self.tick()
        closed = m.STATE.history[0]
        self.assertEqual(closed['exit_reason'], 'TAKE_PROFIT_10_NET')
        self.assertEqual(closed['exit_policy_version'], 'COST_FIRST_NET_EXIT_V1')
        m.STATE.history = []
        self.position()
        self.mark = self.quote(impact=0.3)
        self.tick(61 * 60 * 1000)
        self.assertEqual(m.STATE.history[0]['exit_reason'], 'MAX_HOLD_60')

    def test_liquidity_emergency_unchanged(self):
        self.position()
        self.coin['liquidityUsd'] = 300_000   # below 80% of the $400k entry liquidity
        self.mark = self.quote(impact=0.3)
        self.tick()
        self.assertEqual(m.STATE.history[0]['exit_reason'], 'LIQUIDITY_EMERGENCY')

    def test_positions_keep_their_own_exit_policy_after_a_switch(self):
        # Default engine active: a cost_first position still uses V2 (arms, does not exit).
        m.activate_strategy(m.DEFAULT_SIGNAL_STRATEGY)
        self.position()
        self.mark = self.quote(impact=1.6)
        self.tick()
        self.assertEqual(len(m.STATE.positions), 1)
        self.assertTrue(self.state_v2()['armed'])
        self.assertEqual(m.STATE.positions[0]['exit_policy_version'], 'COST_FIRST_NET_EXIT_V1')
        # Cost-first engine active: an adaptive position keeps its adaptive ladder and version.
        m.activate_strategy(cfp.STRATEGY_ID)
        self.position(exit_policy='adaptive', exit_policy_version=exit_policy.ADAPTIVE_VERSION,
                      adaptive_hold=oct4.hold_mode(90), entry_conviction=90.0)
        self.mark = self.quote(impact=0.3)
        with patch.object(self.monitor, 'market_context',
                          return_value={'conviction': 20.0, 'fast_flow': {'quality': 'COMPLETE'},
                                        **oct4.hold_mode(20)}):
            self.mark = self.quote(impact=0.3, net=199.0)
            self.tick()
        closed = m.STATE.history[0]
        self.assertEqual(closed['exit_reason'], 'CONVICTION_EXIT')
        self.assertEqual(closed['exit_policy_version'], exit_policy.ADAPTIVE_VERSION)


class CostFirstConfigTests(unittest.TestCase):
    def setUp(self):
        m.activate_strategy(cfp.STRATEGY_ID)
        self.addCleanup(m.activate_strategy, m.DEFAULT_SIGNAL_STRATEGY)
        m.STATE_PATH.unlink(missing_ok=True)
        m.STATE = m.State()

    def test_state_config_publishes_profile_and_engine_owned_limits(self):
        config = m.STATE.snapshot()['config']
        expected = {'signal_strategy': cfp.STRATEGY_ID, 'entry_policy_version': 'COST_FIRST_ESTABLISHED_ENTRY_V2',
                    'exit_policy': 'cost_first', 'exit_policy_version': 'COST_FIRST_NET_EXIT_V1',
                    'exit_impact_emergency_version': 'EXIT_IMPACT_EMERGENCY_V2',
                    'learning_mode': cfp.LEARNING_MODE, 'ensemble_strategies': [cfp.STRATEGY_ID],
                    'max_positions': 8, 'max_daily_loss_usd': 100.0, 'max_drawdown_pct': 20.0,
                    'max_total_exposure_pct': 100.0, 'same_token_cooldown_seconds': 1200, 'stop_loss_pct': 5.0,
                    'take_profit_pct': 10.0, 'max_hold_minutes': 60, 'trade_notional_usd': 200.0,
                    'strict_max_roundtrip_cost_pct': 1.5, 'strict_max_worst_case_cost_pct': 2.5,
                    'strict_max_entry_impact_pct': 1.75, 'min_liquidity_usd': 250_000.0, 'paper_only': True,
                    'quote_size_backoff_version': m.entry_size_backoff.VERSION, 'entry_score': None}
        for key, value in expected.items():
            self.assertEqual(config[key], value, key)
        universe = config['universe_parameters']
        self.assertEqual((universe['max_fee_tier_bps'], universe['min_liquidity_usd'],
                          universe['max_fee_impact_roundtrip_pct'], universe['liquidity_size_fraction']),
                         (50.0, 250_000.0, 1.2, 0.001))
        self.assertEqual(config['size_rule']['notional_cap_usd'], 200.0)
        exits = config['exit_parameters']
        self.assertEqual((exits['stop_loss_net_pct'], exits['take_profit_net_pct'], exits['max_hold_minutes']),
                         (-5.0, 10.0, 60))
        self.assertEqual(exits['exit_impact_emergency']['confirm_delay_ms'], 2000)
        self.assertEqual((exits['exit_impact_emergency']['rearm_cooldown_ms'],
                          exits['exit_impact_emergency']['max_confirms_per_window'],
                          exits['exit_impact_emergency']['confirm_window_ms']), (15_000, 3, 300_000))
        self.assertEqual(exits['exit_impact_emergency']['trigger_block_codes'], list(cfp.TRIGGER_BLOCKS))
        ownership = config['config_ownership']
        for key in ('max_positions', 'max_daily_loss_usd', 'stop_loss_pct', 'same_token_cooldown_seconds',
                    'strict_max_roundtrip_cost_pct'):
            self.assertTrue(ownership[key].startswith('engine-owned'), key)
        self.assertIn(cfp.STRATEGY_ID, ownership['universe_parameters'])
        self.assertIn('EXIT_IMPACT_EMERGENCY_V2', ownership['exit_impact_emergency'])
        profile = config['strategy_profile']
        self.assertEqual(profile['profile_version'], cfp.PROFILE_VERSION)
        self.assertFalse(profile['profitability_proven'])
        self.assertIn(cfp.STRATEGY_ID, config['effective_entry_thresholds'])
        self.assertEqual(config['effective_config_hash'], m.effective_config_hash())
        self.assertEqual(m.STATE.entry_diagnostics['policy_version'], 'COST_FIRST_ESTABLISHED_ENTRY_V2')

    def test_effective_config_hash_differs_and_includes_the_profile(self):
        cost_first_hash = m.effective_config_hash()
        m.activate_strategy(m.DEFAULT_SIGNAL_STRATEGY)
        default = m.effective_config_hash()
        m.activate_strategy(oct4.STRATEGY_ID)
        adaptive = m.effective_config_hash()
        m.activate_strategy(cfp.STRATEGY_ID)
        self.assertEqual(len({cost_first_hash, default, adaptive}), 3)
        self.assertEqual(m.effective_config_hash(), cost_first_hash)
        with patch.object(cfp, 'EIE_VERSION', 'EXIT_IMPACT_EMERGENCY_V3_PROBE'):
            self.assertNotEqual(m.effective_config_hash(), cost_first_hash, 'the profile is part of the hash')

    def test_default_and_order_flow_adaptive_hashes_change_visibly_with_the_defensive_layer(self):
        # DEFENSIVE_ENTRY_LAYER_V1, the V5 entry policies and the V2 score model are
        # behaviour changes, so neither pre-layer hash may survive (2026-10-08); the
        # new values are pinned exactly so any later config change fails here.
        if _ENV_OVERRIDES_AT_IMPORT:
            self.skipTest(f'environment overrides change the engine hash: {_ENV_OVERRIDES_AT_IMPORT}')
        for strategy, old, pinned in (
                (m.DEFAULT_SIGNAL_STRATEGY, DEFAULT_HASH_AT_69BE225, DEFAULT_HASH_WITH_DEFENSIVE_ENTRY_LAYER_V1),
                (oct4.STRATEGY_ID, ORDER_FLOW_ADAPTIVE_HASH_AT_69BE225,
                 ORDER_FLOW_ADAPTIVE_HASH_WITH_DEFENSIVE_ENTRY_LAYER_V1),
                (cfp.STRATEGY_ID, None, COST_FIRST_HASH_WITH_DEFENSIVE_ENTRY_LAYER_V1)):
            with self.subTest(strategy=strategy):
                m.activate_strategy(strategy)
                current = m.effective_config_hash()
                self.assertEqual(current, pinned)
                self.assertNotEqual(current, old)
                with patch.object(entry_defense, 'VERSION', 'DEFENSIVE_ENTRY_LAYER_PROBE'):
                    self.assertNotEqual(m.effective_config_hash(), current, 'the layer is part of the hash')
                with patch.object(m, 'SCORE_VERSION', 'NEO_MARKET_SCORE_PROBE'):
                    self.assertNotEqual(m.effective_config_hash(), current, 'the score model is part of the hash')

    def test_engine_defaults_restored_and_unknown_fails_closed(self):
        self.assertEqual((m.MAX_POSITIONS, m.SCAN_SECONDS, m.TRADE_NOTIONAL_USD, m.MAX_DAILY_LOSS_USD,
                          m.MAX_QUOTED_CANDIDATES), (8, 3, 200.0, 100.0, 6))
        self.assertIsNone(m.ADAPTIVE_PROFILE)
        m.activate_strategy(oct4.STRATEGY_ID)
        self.assertFalse(m.COST_FIRST_ACTIVE)
        m.activate_strategy(cfp.STRATEGY_ID)
        self.assertTrue(m.COST_FIRST_ACTIVE)
        self.assertEqual((m.MAX_POSITIONS, m.MAX_QUOTED_CANDIDATES, m.STRICT_MAX_ROUNDTRIP_COST_PCT), (8, 6, 1.5))
        m.activate_strategy(m.DEFAULT_SIGNAL_STRATEGY)
        self.assertFalse(m.COST_FIRST_ACTIVE)
        self.assertEqual(m.POSITION_EXIT_POLICY, 'fixed')
        self.assertIsNone(m.State().snapshot()['config'].get('universe_parameters'))
        for bad in ('COST_FIRST_ESTABLISHED_PAPER_V2', 'cost_first_established_paper_v1', 'COST_FIRST_CONTROL'):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                m.activate_strategy(bad)
        self.assertEqual(m.SIGNAL_STRATEGY, winner_ensemble.VERSION)

    def test_environment_selects_the_profile_at_import_and_unknown_fails(self):
        script = ('import market_monitor as m; print(m.SIGNAL_STRATEGY, m.ENTRY_POLICY_VERSION, '
                  'm.POSITION_EXIT_POLICY, m.exit_policy_version())')
        import subprocess
        import sys
        backend = str(Path(__file__).resolve().parents[1] / 'backend')
        env = dict(os.environ, PYTHONPATH=backend, NEO_SIGNAL_STRATEGY=cfp.STRATEGY_ID, PYTHONUTF8='1')
        out = subprocess.run([sys.executable, '-c', script], env=env, capture_output=True, text=True, timeout=120)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(out.stdout.split(), [cfp.STRATEGY_ID, 'COST_FIRST_ESTABLISHED_ENTRY_V2', 'cost_first',
                                              'COST_FIRST_NET_EXIT_V1'])
        env['NEO_SIGNAL_STRATEGY'] = 'COST_FIRST_UNKNOWN'
        out = subprocess.run([sys.executable, '-c', script], env=env, capture_output=True, text=True, timeout=120)
        self.assertNotEqual(out.returncode, 0)
        self.assertIn('Unsupported NEO_SIGNAL_STRATEGY', out.stderr)


class FakeProcess:
    returncode = None

    def poll(self):
        return None


class GatewayAndCliAcceptTheProfile(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.registry = root / 'accounts.json'
        self.registry.write_text(json.dumps({'accounts': {
            'user-a': {'user_id': 'user-a', 'signal_strategy': cfp.STRATEGY_ID, 'balance': 1000.0,
                       'history': [], 'positions': [], 'trade_seq': 0},
            'user-b': {'user_id': 'user-b', 'balance': 990.0, 'history': [], 'positions': [], 'trade_seq': 0},
            'user-c': {'user_id': 'user-c', 'signal_strategy': 'COST_FIRST_ESTABLISHED_PAPER_V2'},
        }}), encoding='utf-8')
        self.env = patch.dict(os.environ, {'NEO_USER_STATE_PATH': str(self.registry),
                                           'NEO_USER_ENGINE_ROOT': str(root / 'users')})
        self.env.start()
        self.addCleanup(self.env.stop)
        import user_gateway
        self.gateway = importlib.reload(user_gateway)
        self.spawned = []

        def popen(args, cwd=None, env=None, **kwargs):
            self.spawned.append(env)
            return FakeProcess()

        for p in (patch.object(self.gateway.subprocess, 'Popen', side_effect=popen),
                  patch.object(self.gateway, 'port_open', return_value=False),
                  patch.object(self.gateway, 'engine_health', return_value=True),
                  patch.object(self.gateway, 'central_state', return_value={'stats': {}, 'events': []})):
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(self.gateway.ENGINE_PROCESSES.clear)

    def test_gateway_starts_only_that_account_with_the_profile(self):
        self.assertIn(cfp.STRATEGY_ID, self.gateway.SUPPORTED_SIGNAL_STRATEGIES)
        self.gateway.ensure_engine({'id': 'user-a', 'created_at': None})
        self.gateway.ensure_engine({'id': 'user-b', 'created_at': None})
        self.assertEqual(self.spawned[0]['NEO_SIGNAL_STRATEGY'], cfp.STRATEGY_ID)
        self.assertNotIn('NEO_SIGNAL_STRATEGY', self.spawned[1])

    def test_gateway_unknown_id_fails_closed(self):
        with self.assertRaisesRegex(RuntimeError, 'unsupported'):
            self.gateway.ensure_engine({'id': 'user-c', 'created_at': None})
        self.assertEqual(self.spawned, [])

    def test_cli_sets_the_profile_and_rejects_unknown_ids(self):
        script = Path(__file__).resolve().parents[1] / 'scripts' / 'paper_runtime.py'
        spec = importlib.util.spec_from_file_location('paper_runtime_cli_cost_first', script)
        cli = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cli)
        registry = Path(self.temp.name) / 'cli_accounts.json'
        uid = '3aa07b45-0000-4000-8000-000000000001'
        original = {'accounts': {uid: {'user_id': uid, 'engine_port': 18800, 'balance': 993.57, 'trade_seq': 1,
                                       'history': [{'id': 'keep', 'pnl_usd': -6.43}], 'positions': []}}}
        registry.write_text(json.dumps(original), encoding='utf-8')
        result = cli.set_account_strategy(registry, uid, cfp.STRATEGY_ID)
        self.assertEqual(result['signal_strategy_after'], cfp.STRATEGY_ID)
        self.assertEqual(result['ledger_files_touched'], 0)
        saved = json.loads(registry.read_text(encoding='utf-8'))['accounts'][uid]
        for key in ('engine_port', 'balance', 'trade_seq', 'history', 'positions'):
            self.assertEqual(saved[key], original['accounts'][uid][key])
        before = registry.read_text(encoding='utf-8')
        for bad in ('COST_FIRST_ESTABLISHED_PAPER_V2', 'COST_FIRST_CONTROL', 'cost_first'):
            with self.subTest(bad=bad), self.assertRaises(SystemExit):
                cli.set_account_strategy(registry, uid, bad)
        self.assertEqual(registry.read_text(encoding='utf-8'), before)


if __name__ == '__main__':
    unittest.main()
