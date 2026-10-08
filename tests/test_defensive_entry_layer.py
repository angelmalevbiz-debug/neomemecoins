"""DEFENSIVE_ENTRY_LAYER_V1: structural rug guard, heat veto, pool loss memory.

PAPER only and offline. Realistic fixtures come from the research scan log
(tests/fixtures/defensive_entry_obs_20261008.json, extracted read-only from
obs.sqlite3 table o; mint and pool abbreviated to 8 characters). Quotes, flow,
safety and prices are patched; no test calls an external API or touches a real
account. The integration tests prove that every entry path (main default,
ORDER_FLOW_ADAPTIVE, the COST_FIRST engine profile, Strategy Lab books, the
tape scheduler's seat groups and the training quote probe) consults the guard,
the heat veto and the loss memory before any quote, flow promotion or RugCheck.
Nothing here measures or claims profitability.
"""
import copy
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

_TEMP = tempfile.TemporaryDirectory(prefix='neo-defensive-entry-tests-')
for _name, _file in (('NEO_MARKET_STATE_PATH', 'state.json'), ('NEO_MARKET_AUDIT_PATH', 'audit.jsonl'),
                     ('NEO_LIVE_TAPE_PATH', 'tape.json'), ('NEO_STRATEGY_LAB_PATH', 'strategy_lab.json'),
                     ('NEO_STRATEGY_LAB_COMPACT_PATH', 'strategy_lab_compact.json'),
                     ('NEO_STRATEGY_LAB_RESET_FLAG', 'strategy_lab.reset')):
    os.environ.setdefault(_name, str(Path(_TEMP.name) / _file))
for _key in ('NEO_SIGNAL_STRATEGY', 'NEO_TRADE_NOTIONAL_USD', 'NEO_MAX_DAILY_LOSS_USD', 'NEO_STRICT_ENTRY_SCORE',
             'NEO_STRICT_MIN_CONVICTION', 'NEO_STRICT_MIN_LIQUIDITY_USD', 'NEO_STRICT_MAX_ENTRY_IMPACT_PCT',
             'NEO_STRICT_MAX_ROUNDTRIP_COST_PCT'):
    os.environ.pop(_key, None)

import cost_first_engine_profile as cfp
import cost_first_established as cost_first
import entry_defense
import heat_veto
import market_monitor as m
import order_flow_adaptive_oct4 as oct4
import paper_market_feasibility as feasibility
import pool_loss_memory
import promoted_entry_guard as promoted_guard
import strategy_lab as lab
import structural_rug_guard as guard
import tape_pool_scheduler as tape_scheduler
import winner_ensemble
from tape_pool_scheduler import TapePoolScheduler

FIXTURES = json.loads((Path(__file__).resolve().parent / 'fixtures' / 'defensive_entry_obs_20261008.json')
                      .read_text(encoding='utf-8'))
SOL = feasibility.SOL_QUOTE_MINT
MINUTE = 60_000
DAY = 86_400_000
# Valid base58 identities for engine paths (signal_data_rejections checks them).
MINT, PAIR = 'D' * 44, 'E' * 44
OTHER_MINT, OTHER_PAIR = 'F' * 44, 'G' * 44


def fixture(name):
    row = FIXTURES['fixtures'][name]
    return copy.deepcopy(row['coin']), row['observed_at']


def research_registry():
    """Ticker registry holding every research pool of the fixture tickers at its first sighting."""
    registry = guard.TickerRegistry()
    for row in FIXTURES['ticker_universe']:
        registry.observe_coin({'address': row['mint'], 'pairAddress': row['pair'], 'symbol': row['symbol']},
                              row['first_seen'])
    return registry


def established(now, **changes):
    """An established pool shaped like the TROLL fixture: passes the structural guard and,
    once warmed, the heat veto; matches the ensemble's COST_EFFICIENT_FLOW market rule."""
    coin = {'address': MINT, 'pairAddress': PAIR, 'symbol': 'TROLL', 'name': 'established fixture',
            'score': 95, 'liquidityUsd': 3_135_336.0, 'marketCap': 31_000_000.0,
            'priceUsd': 0.0398, 'priceNative': 0.0398 / 116.26, 'quoteTokenAddress': SOL,
            'ageMinutes': 43_200, 'pairCreatedAt': now - 30 * DAY,
            'priceChange': {'m5': 3.0, 'h1': 4.0, 'h6': -0.45, 'h24': -6.05},
            'txns': {'m5': {'buys': 30, 'sells': 20}}, 'volume': {'m5': 15_000.0, 'h1': 400_000.0},
            'signals': [], 'sources': ['pumpswap-address-catalog'], 'updatedAt': now}
    coin.update(changes)
    return coin


def warm(history, coin, now, *, seconds=400, step=100, price=None):
    """Feed contiguous observations of ``coin`` over the last ``seconds`` (gaps <= the 120 s limit)."""
    for back in range(seconds, 0, -step):
        stamp = now - back * 1000
        history.observe_coin(dict(coin, updatedAt=stamp, priceUsd=price or coin['priceUsd']), stamp)


def losses(mint, pair, now, *, count=2, spacing=35 * MINUTE, pnl=-4.0, first_age=60 * MINUTE):
    """Closed trades (newest first) on one pool, as a ledger stores them."""
    return [{'id': f'close-{index}', 'address': mint, 'pairAddress': pair, 'pnl_usd': pnl,
             'closed_at': now - first_age - index * spacing, 'entry_policy_version': 'ANY'}
            for index in range(count)]


# ------------------------------------------------------------------ structural guard

class StructuralRugGuardFixtureTests(unittest.TestCase):
    """Real research observations: the rug families block, genuinely established pools pass."""

    EXPECTED = {
        'USDP_8RCJrW94': ['rug_fake_market_cap', 'rug_ticker_reuse'],
        'IOF_FeCGDeqW': ['rug_young_pool', 'rug_fake_market_cap', 'rug_ticker_reuse'],
        'WOSE_GhBPuDpt': ['rug_young_pool', 'rug_fake_market_cap', 'rug_ticker_reuse'],
        'GOIF_D2pVedgH': ['rug_young_pool', 'rug_fake_market_cap', 'rug_ticker_reuse'],
        'GOIF_8ZMkMgWM': ['rug_fake_market_cap', 'rug_ticker_reuse'],
        'SARP_HiPe6mDS': ['rug_young_pool', 'rug_fake_market_cap', 'rug_ticker_reuse'],
        'SARP_AdvA19rg': ['rug_fake_market_cap', 'rug_ticker_reuse'],
        'DAWS_BTSpnpim': ['rug_fake_market_cap'],
        'SharkTank_CRGN2uGj': ['rug_lp_pullable', 'rug_young_pool', 'rug_ticker_reuse'],
        'knightcat_7puXhhDF': ['rug_lp_pullable', 'rug_young_pool'],
        'OGTRUMP_9rSmRH9w': ['rug_young_pool'],
        'ANSEM_FnzKY6x7': [], 'CATE_HMzvsEEm': [], 'TROLL_4w2cysot': [], 'neet_5wNu5Qhd': [],
    }

    def setUp(self):
        self.registry = research_registry()

    def test_every_fixture_family_gets_the_expected_reasons_in_order(self):
        self.assertEqual(set(self.EXPECTED), set(FIXTURES['fixtures']))
        for name, expected in self.EXPECTED.items():
            with self.subTest(name=name):
                coin, observed_at = fixture(name)
                result = guard.check(coin, observed_at, self.registry)
                self.assertEqual(result['reasons'], expected)
                self.assertEqual(result['blocked'], bool(expected))
                self.assertEqual(result['version'], 'STRUCTURAL_RUG_GUARD_V1')
                for key in ('version', 'blocked', 'reasons', 'liq_mcap', 'age_min', 'mcap'):
                    self.assertIn(key, result)

    def test_established_pools_pass_with_their_real_structure(self):
        for name in ('ANSEM_FnzKY6x7', 'CATE_HMzvsEEm', 'TROLL_4w2cysot', 'neet_5wNu5Qhd'):
            with self.subTest(name=name):
                coin, observed_at = fixture(name)
                result = guard.check(coin, observed_at, self.registry)
                self.assertFalse(result['blocked'])
                self.assertGreater(result['age_min'], 14 * 1440)
                self.assertLess(result['liq_mcap'], 1.0)
        # CATE shares its normalized ticker with younger pools ('CATE', 'CATE!'), but
        # an established pool (>= 14 days) is never judged by ticker reuse.
        cate, observed_at = fixture('CATE_HMzvsEEm')
        self.assertGreaterEqual(len(self.registry.reusers('cate', cate['address'], cate['pairAddress'],
                                                          observed_at)), 2)

    def test_fake_market_cap_family_values_match_the_research(self):
        for name in ('USDP_8RCJrW94', 'IOF_FeCGDeqW', 'WOSE_GhBPuDpt', 'GOIF_D2pVedgH', 'SARP_AdvA19rg'):
            coin, observed_at = fixture(name)
            result = guard.check(coin, observed_at, self.registry)
            self.assertGreaterEqual(result['mcap'], 20_000_000, name)
            self.assertLess(result['liq_mcap'], 0.01, name)
            self.assertLess(result['age_min'], 14 * 1440, name)

    def test_two_percent_threshold_catches_daws_like_pools_that_one_percent_misses(self):
        one_percent = guard.GuardParameters(fake_mcap_max_liq_to_mcap=0.01)
        for name in ('DAWS_BTSpnpim', 'GOIF_8ZMkMgWM'):
            with self.subTest(name=name):
                coin, observed_at = fixture(name)
                self.assertIn('rug_fake_market_cap', guard.check(coin, observed_at, self.registry)['reasons'])
                self.assertNotIn('rug_fake_market_cap',
                                 guard.check(coin, observed_at, self.registry, params=one_percent)['reasons'])
        self.assertEqual(guard.PARAMS.fake_mcap_max_liq_to_mcap, 0.02)

    def test_lp_pullable_and_young_pool_values(self):
        coin, observed_at = fixture('SharkTank_CRGN2uGj')
        result = guard.check(coin, observed_at, self.registry)
        self.assertGreaterEqual(result['liq_mcap'], 1.0)
        self.assertGreaterEqual(result['ticker_reused_by'], 30)
        coin, observed_at = fixture('OGTRUMP_9rSmRH9w')
        result = guard.check(coin, observed_at, self.registry)
        self.assertLess(result['age_min'], 15)
        self.assertEqual(result['ticker_reused_by'], 0)


class StructuralRugGuardRuleTests(unittest.TestCase):
    NOW = 1_800_000_000_000

    def coin(self, **changes):
        coin = {'address': MINT, 'pairAddress': PAIR, 'symbol': 'BASE', 'liquidityUsd': 500_000,
                'marketCap': 5_000_000, 'pairCreatedAt': self.NOW - 20 * DAY}
        coin.update(changes)
        return coin

    def test_inputs_fail_closed_and_stop(self):
        registry = guard.TickerRegistry()
        cases = [{'liquidityUsd': None}, {'liquidityUsd': float('nan')}, {'liquidityUsd': 0},
                 {'marketCap': None, 'fdv': None}, {'marketCap': 0, 'fdv': 0}, {'marketCap': -1, 'fdv': None},
                 {'pairCreatedAt': None}, {'pairCreatedAt': self.NOW}, {'pairCreatedAt': self.NOW + 1},
                 {'pairCreatedAt': 'x'}, {'symbol': None}, {'symbol': '\U0001f438\U0001f438'}, {'address': ''},
                 {'pairAddress': None}, {'liquidityUsd': True}]
        for change in cases:
            with self.subTest(change=change):
                result = guard.check(self.coin(**change), self.NOW, registry)
                self.assertTrue(result['blocked'])
                self.assertEqual(result['reasons'], ['rug_input_unknown'])
        # A missing ticker registry is an unknown input too.
        self.assertEqual(guard.check(self.coin(), self.NOW, None)['reasons'], ['rug_input_unknown'])
        self.assertFalse(guard.check(self.coin(), self.NOW, registry)['blocked'])

    def test_market_cap_falls_back_to_fdv_and_liquidity_to_the_dex_shape(self):
        registry = guard.TickerRegistry()
        result = guard.check(self.coin(marketCap=0, fdv=400_000), self.NOW, registry)
        self.assertEqual(result['mcap'], 400_000)
        self.assertEqual(result['reasons'], ['rug_lp_pullable'])
        result = guard.check(self.coin(liquidityUsd=None, liquidity={'usd': 250_000}), self.NOW, registry)
        self.assertEqual(result['liquidity_usd'], 250_000)
        self.assertFalse(result['blocked'])

    def test_boundaries(self):
        registry = guard.TickerRegistry()
        check = lambda **c: guard.check(self.coin(**c), self.NOW, registry)['reasons']
        self.assertEqual(check(liquidityUsd=5_000_000), ['rug_lp_pullable'])            # liq/mcap == 1.0
        self.assertEqual(check(liquidityUsd=4_999_999), [])
        self.assertEqual(check(pairCreatedAt=self.NOW - 720 * MINUTE), [])
        self.assertEqual(check(pairCreatedAt=self.NOW - 720 * MINUTE + 1), ['rug_young_pool'])
        fake = dict(marketCap=20_000_000, liquidityUsd=399_999)                          # 1.99999%
        self.assertEqual(check(**fake, pairCreatedAt=self.NOW - 14 * DAY + 1), ['rug_fake_market_cap'])
        self.assertEqual(check(**fake, pairCreatedAt=self.NOW - 14 * DAY), [])
        self.assertEqual(check(marketCap=20_000_000, liquidityUsd=400_000, pairCreatedAt=self.NOW - DAY), [])
        self.assertEqual(check(marketCap=19_999_999, liquidityUsd=100_000, pairCreatedAt=self.NOW - DAY), [])

    def test_ticker_reuse_needs_a_different_mint_seen_at_or_before_now(self):
        registry = guard.TickerRegistry()
        coin = self.coin(symbol='D O T F', pairCreatedAt=self.NOW - 3 * DAY)
        # The token's own second pool (same mint) is not a reuse.
        registry.observe_coin({'address': MINT, 'pairAddress': OTHER_PAIR, 'symbol': 'DOTF'}, self.NOW - DAY)
        self.assertEqual(guard.check(coin, self.NOW, registry)['reasons'], [])
        # Another mint first seen after the decision is invisible (past-only).
        registry.observe_coin({'address': OTHER_MINT, 'pairAddress': 'H' * 44, 'symbol': 'dotf!'}, self.NOW + 1)
        self.assertEqual(guard.check(coin, self.NOW, registry)['reasons'], [])
        self.assertEqual(guard.check(coin, self.NOW + 1, registry)['reasons'], ['rug_ticker_reuse'])
        # Established tokens (>= 14 days) are not judged by ticker reuse.
        old = dict(coin, pairCreatedAt=self.NOW - 14 * DAY)
        self.assertEqual(guard.check(old, self.NOW + 1, registry)['reasons'], [])
        self.assertEqual(guard.normalize_ticker('CATE!'), 'cate')
        self.assertEqual(guard.normalize_ticker('D O T F'), 'dotf')

    def test_config_is_published_and_distinct_from_rugcheck(self):
        config = guard.config()
        self.assertEqual(config['version'], 'STRUCTURAL_RUG_GUARD_V1')
        self.assertEqual(config['reasons_in_order'], list(guard.REASONS))
        self.assertTrue(config['fail_closed'])
        self.assertIn('RUG_GUARD_V2', config['distinct_from'])
        self.assertEqual(config['parameters']['young_pool_max_age_minutes'], 720.0)
        self.assertEqual(m.rug_guard.VERSION, 'RUG_GUARD_V2')


class TickerRegistryPersistenceTests(unittest.TestCase):
    NOW = 1_800_000_000_000

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='neo-ticker-registry-')
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'state.ticker_registry.json'
        self.clock = self.NOW

    def registry(self, **kwargs):
        return guard.TickerRegistry(self.path, clock=lambda: self.clock, **kwargs)

    def coin(self, mint, pair, symbol):
        return {'address': mint, 'pairAddress': pair, 'symbol': symbol}

    def test_restart_keeps_memory_through_an_atomic_sidecar(self):
        first = self.registry()
        self.assertEqual(first.load_status, 'MISSING_STARTED_EMPTY')
        first.observe([self.coin(OTHER_MINT, OTHER_PAIR, 'WOSE')], self.NOW - DAY)
        self.assertTrue(self.path.exists())
        self.assertEqual(first.status()['saves'], 1)
        self.assertEqual([p.name for p in self.path.parent.iterdir()], [self.path.name], 'no temp file left')
        data = json.loads(self.path.read_text(encoding='utf-8'))
        self.assertEqual(data['version'], 'TICKER_REGISTRY_V1')
        self.assertEqual(data['entries'], [[OTHER_MINT, OTHER_PAIR, 'wose', self.NOW - DAY, self.NOW - DAY]])
        second = self.registry()
        self.assertEqual(second.load_status, 'LOADED')
        self.assertEqual(second.reusers('wose', MINT, PAIR, self.NOW), [(OTHER_MINT, OTHER_PAIR)])
        coin = {'address': MINT, 'pairAddress': PAIR, 'symbol': 'WOSE', 'liquidityUsd': 1_400_000,
                'marketCap': 5_000_000, 'pairCreatedAt': self.NOW - 2 * DAY}
        self.assertEqual(guard.check(coin, self.NOW, second)['reasons'], ['rug_ticker_reuse'])

    def test_corrupt_or_unsupported_file_starts_empty_and_is_replaced(self):
        for content in ('{not json', json.dumps({'version': 'OTHER', 'entries': []}), json.dumps([1, 2]),
                        json.dumps({'version': 'TICKER_REGISTRY_V1', 'entries': 'x'})):
            with self.subTest(content=content[:20]):
                self.path.write_text(content, encoding='utf-8')
                registry = self.registry()
                self.assertEqual(registry.load_status, 'CORRUPT_STARTED_EMPTY')
                self.assertEqual(len(registry), 0)
                registry.observe([self.coin(MINT, PAIR, 'X')], self.NOW)
                self.assertEqual(json.loads(self.path.read_text(encoding='utf-8'))['version'], 'TICKER_REGISTRY_V1')

    def test_invalid_rows_are_skipped_and_stale_rows_pruned_on_load(self):
        rows = [[MINT, PAIR, 'good', self.NOW - DAY, self.NOW - DAY],
                [OTHER_MINT, OTHER_PAIR, 'old', self.NOW - 30 * DAY, self.NOW - 15 * DAY],
                ['', PAIR, 'bad', 1, 2], [MINT, PAIR, 'NotNormalized', 1, 2], [MINT, PAIR, 'x', 5, 1], 'junk']
        self.path.write_text(json.dumps({'version': 'TICKER_REGISTRY_V1', 'entries': rows}), encoding='utf-8')
        registry = self.registry()
        self.assertEqual(registry.load_status, 'LOADED')
        self.assertEqual(registry.load_skipped_rows, 4)
        self.assertEqual(len(registry), 1, 'last seen 15 days ago is pruned (14-day retention)')
        self.assertEqual(registry.reusers('good', 'Z' * 44, 'Y' * 44, self.NOW), [(MINT, PAIR)])

    def test_pruning_retention_and_entry_bound(self):
        registry = guard.TickerRegistry(max_entries=20, prune_interval_ms=0)
        registry.observe([self.coin(f'M{index:02d}' + 'A' * 40, f'P{index:02d}' + 'B' * 40, f'T{index}')
                          for index in range(30)], self.NOW)
        self.assertLessEqual(len(registry), 20)
        registry.observe([self.coin(MINT, PAIR, 'fresh')], self.NOW + 15 * DAY)
        self.assertEqual(len(registry), 1, 'entries unseen for 14 days are pruned')
        self.assertEqual(registry.status()['retention_days'], 14.0)

    def test_failed_atomic_replace_keeps_the_previous_file_and_never_raises(self):
        registry = self.registry()
        registry.observe([self.coin(MINT, PAIR, 'KEEP')], self.NOW)
        before = self.path.read_text(encoding='utf-8')
        registry.observe_coin(self.coin(OTHER_MINT, OTHER_PAIR, 'NEW'), self.NOW + 1)
        with patch.object(guard.os, 'replace', side_effect=OSError('disk')):
            self.assertFalse(registry.save(self.NOW + 2))
        self.assertEqual(registry.status()['save_error'], 'OSError')
        self.assertEqual(self.path.read_text(encoding='utf-8'), before)
        self.assertEqual([p.name for p in self.path.parent.iterdir()], [self.path.name])
        self.assertTrue(registry.flush())
        self.assertIn('new', self.path.read_text(encoding='utf-8'))

    def test_saves_are_throttled(self):
        registry = self.registry(save_interval_ms=60_000)
        registry.observe([self.coin(MINT, PAIR, 'A')], self.NOW)
        registry.observe([self.coin(OTHER_MINT, OTHER_PAIR, 'B')], self.NOW + 1_000)
        self.assertEqual(registry.status()['saves'], 1)
        registry.observe([self.coin(OTHER_MINT, OTHER_PAIR, 'B')], self.NOW + 61_000)
        self.assertEqual(registry.status()['saves'], 2)

    def test_engine_sidecar_lives_next_to_the_account_state_and_survives_restart(self):
        state_path = Path(self.tmp.name) / 'engine' / 'state.json'
        with patch.object(m, 'STATE_PATH', state_path):
            monitor = m.Monitor()
            monitor.observe_entry_defense([established(self.NOW)], self.NOW)
            monitor.stop()
            sidecar = state_path.with_name('state.ticker_registry.json')
            self.assertTrue(sidecar.exists())
            restarted = m.Monitor()
            self.assertEqual(restarted.defense.registry.load_status, 'LOADED')
            self.assertEqual(len(restarted.defense.registry), 1)
            restarted.stop()
        self.assertEqual(entry_defense.registry_path_for(Path('x') / 'strategy_lab.json').name,
                         'strategy_lab.ticker_registry.json')


# ------------------------------------------------------------------ heat veto

class HeatVetoRuleTests(unittest.TestCase):
    """Rule by rule on the real 16-minute TROLL series (one sample per >= 10 s)."""

    def setUp(self):
        self.series = FIXTURES['troll_series']
        self.base, _ = fixture('TROLL_4w2cysot')
        self.now = self.series[-1]['t'] + 1_000
        self.history = heat_veto.PairHistory()
        for row in self.series:
            self.history.observe_coin(dict(self.base, priceUsd=row['priceUsd'], sources=row['sources'],
                                           updatedAt=row['t']), row['t'])
        self.coin = dict(self.base, priceUsd=self.series[-1]['priceUsd'], updatedAt=self.series[-1]['t'])

    def evaluate(self, coin=None, **kwargs):
        return heat_veto.evaluate(coin or self.coin, self.now, self.history, **kwargs)

    def test_the_real_established_series_passes(self):
        result = self.evaluate()
        self.assertEqual(result['reasons'], [], result['metrics'])
        self.assertFalse(result['vetoed'])
        self.assertGreaterEqual(result['metrics']['history_span_s'], 900)
        self.assertLess(abs(result['metrics']['return_5m_pct']), 3)
        self.assertEqual(result['version'], 'HEAT_VETO_STACK_V1')

    def test_a_5m_return_surge(self):
        reference = heat_veto._price_at_or_before(self.history.view(self.coin)['samples'],
                                                  self.history.view(self.coin)['since'], self.now - 300_000)
        surge = dict(self.coin, priceUsd=reference * 1.03)
        self.assertIn('heat_return_5m_surge', self.evaluate(surge)['reasons'])
        calm = dict(self.coin, priceUsd=reference * 1.0299)
        self.assertNotIn('heat_return_5m_surge', self.evaluate(calm)['reasons'])

    def test_b_buy_share(self):
        self.assertIn('heat_buy_share_5m', self.evaluate(dict(self.coin, txns={'m5': {'buys': 70, 'sells': 30}}))['reasons'])
        self.assertNotIn('heat_buy_share_5m', self.evaluate(dict(self.coin, txns={'m5': {'buys': 69, 'sells': 31}}))['reasons'])
        self.assertIsNone(self.evaluate(dict(self.coin, txns={'m5': {'buys': 0, 'sells': 0}}))['metrics']['buy_share_5m'])

    def test_c_volume_acceleration(self):
        hot = dict(self.coin, volume={'m5': 1.3 * 12_000 / 12, 'h1': 12_000})
        self.assertIn('heat_volume_acceleration', self.evaluate(hot)['reasons'])
        cool = dict(self.coin, volume={'m5': 1.29 * 12_000 / 12, 'h1': 12_000})
        self.assertNotIn('heat_volume_acceleration', self.evaluate(cool)['reasons'])
        self.assertIsNone(self.evaluate(dict(self.coin, volume={'m5': 10, 'h1': 0}))['metrics']['volume_acceleration'])

    def test_d_extended_move(self):
        for change in ({'h6': 200, 'h24': 0}, {'h6': 0, 'h24': 150}):
            self.assertIn('heat_extended_move', self.evaluate(dict(self.coin, priceChange=change))['reasons'])
        self.assertNotIn('heat_extended_move',
                         self.evaluate(dict(self.coin, priceChange={'h6': 199.9, 'h24': 149.9}))['reasons'])

    def test_e_paid_profile_in_a_high_fee_pool(self):
        paid, _ = fixture('OGTRUMP_9rSmRH9w')
        self.assertEqual(feasibility.pumpswap_fee_bps(paid), 115.0)
        self.assertEqual(paid['sources'], ['latest'])
        result = heat_veto.evaluate(paid, paid['updatedAt'], heat_veto.PairHistory())
        self.assertIn('heat_paid_profile_high_fee', result['reasons'])
        self.assertIn('heat_history_warming', result['reasons'])
        # A 'latest' sighting inside the last 60 min counts; an older one does not.
        high_fee = dict(self.coin, marketCap=500_000)                    # ~4,300 SOL -> 105 bps
        self.assertGreaterEqual(feasibility.pumpswap_fee_bps(high_fee), 100)
        self.history.observe_coin(dict(high_fee, sources=['latest'], updatedAt=self.now - 500), self.now - 500)
        self.assertIn('heat_paid_profile_high_fee', self.evaluate(high_fee)['reasons'])
        later = self.now + 3_601_000
        self.assertNotIn('heat_paid_profile_high_fee',
                         heat_veto.evaluate(high_fee, later, self.history)['reasons'])
        # The same paid profile in the 30 bps tier is not vetoed by (e).
        self.assertNotIn('heat_paid_profile_high_fee', self.evaluate(dict(self.coin, sources=['latest']))['reasons'])

    def test_f_crash_in_progress(self):
        # Branch 1: price at or below 75% of the 15-minute high (the 5-min return alone is mild).
        history = heat_veto.PairHistory()
        for back, price in ((800, 1.0), (700, 1.0), (600, 0.9), (500, 0.85), (400, 0.8), (300, 0.8),
                            (200, 0.8), (100, 0.8)):
            stamp = self.now - back * 1000
            history.observe_coin(dict(self.coin, priceUsd=price, updatedAt=stamp), stamp)
        crashed = heat_veto.evaluate(dict(self.coin, priceUsd=0.75), self.now, history)
        self.assertIn('heat_crash_in_progress', crashed['reasons'])
        self.assertGreater(crashed['metrics']['return_5m_pct'], -20)
        self.assertNotIn('heat_crash_in_progress',
                         heat_veto.evaluate(dict(self.coin, priceUsd=0.76), self.now, history)['reasons'])
        # Branch 2: a 5-minute return of -20% or worse on the real flat TROLL series.
        reference = heat_veto._price_at_or_before(self.history.view(self.coin)['samples'],
                                                  self.history.view(self.coin)['since'], self.now - 300_000)
        high = max(row['priceUsd'] for row in self.series if row['t'] > self.now - 900_000)
        self.assertLess(reference * 0.80, high)
        self.assertIn('heat_crash_in_progress', self.evaluate(dict(self.coin, priceUsd=reference * 0.80))['reasons'])

    def test_g_turnover(self):
        liquidity = self.coin['liquidityUsd']
        hot = dict(self.coin, volume={'m5': liquidity * 0.095, 'h1': liquidity * 10})
        self.assertIn('heat_turnover_5m', self.evaluate(hot)['reasons'])
        cool = dict(self.coin, volume={'m5': liquidity * 0.094, 'h1': liquidity * 10})
        self.assertNotIn('heat_turnover_5m', self.evaluate(cool)['reasons'])

    def test_warming_gaps_staleness_and_unknown_price_are_vetoes(self):
        fresh = heat_veto.PairHistory()
        warm(fresh, self.coin, self.now, seconds=200, step=50)
        self.assertEqual(heat_veto.evaluate(self.coin, self.now, fresh)['reasons'][:1], ['heat_history_warming'])
        warm(fresh, self.coin, self.now - 1_000, seconds=500, step=100)  # older stamps are ignored
        self.assertIn('heat_history_warming', heat_veto.evaluate(self.coin, self.now, fresh)['reasons'])
        # A gap longer than 120 s restarts the contiguous segment.
        gapped = heat_veto.PairHistory()
        for back in (900, 800, 700, 100, 50):
            stamp = self.now - back * 1000
            gapped.observe_coin(dict(self.coin, updatedAt=stamp), stamp)
        self.assertIn('heat_history_warming', heat_veto.evaluate(self.coin, self.now, gapped)['reasons'])
        # A pair not re-observed within 120 s has no current history.
        self.assertIn('heat_history_warming',
                      heat_veto.evaluate(self.coin, self.now + 121_000, self.history)['reasons'])
        result = self.evaluate(dict(self.coin, priceUsd=0))
        self.assertIn('heat_input_unknown', result['reasons'])
        self.assertTrue(result['vetoed'])

    def test_log_only_records_flags_without_blocking(self):
        hot = dict(self.coin, txns={'m5': {'buys': 90, 'sells': 10}})
        result = self.evaluate(hot, log_only=True)
        self.assertFalse(result['vetoed'])
        self.assertTrue(result['log_only'])
        self.assertEqual(result['reasons'], ['heat_buy_share_5m'])
        warming = heat_veto.evaluate(self.coin, self.now, heat_veto.PairHistory(), log_only=True)
        self.assertFalse(warming['vetoed'])
        self.assertIn('heat_history_warming', warming['reasons'])
        self.assertEqual(heat_veto.LOG_ONLY_BOOK_IDS, {'LAB_A_SURGE_EST_GUARD', 'LAB_B_DIP_MKTDIP_GUARD'})

    def test_history_is_bounded_and_deduplicated(self):
        history = heat_veto.PairHistory(heat_veto.HistoryParameters(max_pairs=3, max_samples_per_pair=5))
        for index in range(5):
            history.observe_coin({'address': f'm{index}', 'pairAddress': f'p{index}', 'priceUsd': 1,
                                  'updatedAt': self.now}, self.now)
        self.assertEqual(len(history), 3)
        coin = {'address': 'mx', 'pairAddress': 'px', 'priceUsd': 1, 'updatedAt': self.now}
        self.assertTrue(history.observe_coin(coin, self.now))
        self.assertFalse(history.observe_coin(coin, self.now), 'same observation twice')
        for step in range(1, 20):
            history.observe_coin(dict(coin, priceUsd=1 + step, updatedAt=self.now + step * 1000), self.now + step * 1000)
        self.assertEqual(len(history.view(coin)['samples']), 5)
        history.prune(self.now + 3_700_000)
        self.assertEqual(len(history), 0)

    def observe_series(self, history, coin, points):
        """``points``: (seconds before self.now, price) every 20 s, gaps where the pair is absent."""
        for back, price in points:
            stamp = self.now - back * 1000
            history.observe_coin(dict(coin, priceUsd=price, updatedAt=stamp), stamp)

    def test_f_crash_during_a_feed_gap_longer_than_120_s(self):
        # Seen at 1.0 from t-14 to t-9 min, absent 3 min, back at 0.70 and flat for 6 min:
        # the research window (P.window('price', 900)) spans the gap and flags the crash.
        coin = dict(self.coin, address=MINT, pairAddress=PAIR)
        history = heat_veto.PairHistory()
        before = [(back, 1.0) for back in range(14 * 60, 9 * 60 - 1, -20)]
        after = [(back, 0.70) for back in range(6 * 60, 0, -20)]
        self.observe_series(history, coin, before + after)
        view = history.view(coin)
        self.assertEqual(len(view['gaps']), 1, 'one absence longer than 120 s')
        self.assertGreater(view['since'], self.now - 7 * MINUTE, 'the contiguous segment restarted')
        result = heat_veto.evaluate(dict(coin, priceUsd=0.70), self.now, history)
        self.assertIn('heat_crash_in_progress', result['reasons'], result['metrics'])
        self.assertEqual(result['metrics']['fraction_of_15m_high'], 0.7)
        self.assertNotIn('heat_history_warming', result['reasons'], 'six minutes of current segment')
        # The 5-minute return stays on the current segment (flat): only (f)'s high spans the gap.
        self.assertEqual(result['metrics']['return_5m_pct'], 0.0)

    def test_f_a_price_from_a_segment_that_ended_before_the_window_does_not_count(self):
        # Seen at 2.0 until t-40 min, absent until t-10 min, then flat at 1.0: the 2.0
        # sample is the last one at or before the window start, but its segment ended
        # 25 minutes before the window, so (as in the research) it is no 15-min high.
        coin = dict(self.coin, address=MINT, pairAddress=PAIR)
        history = heat_veto.PairHistory()
        self.observe_series(history, coin, [(back, 2.0) for back in range(45 * 60, 40 * 60 - 1, -20)]
                            + [(back, 1.0) for back in range(10 * 60, 0, -20)])
        result = heat_veto.evaluate(dict(coin, priceUsd=1.0), self.now, history)
        self.assertNotIn('heat_crash_in_progress', result['reasons'], result['metrics'])
        self.assertEqual(result['metrics']['fraction_of_15m_high'], 1.0)
        # A segment still observed after the window start keeps the price in effect at the
        # start: unchanged prices are stored only as 30 s heartbeats, so the last 1.0 sample
        # (t-930 s) lies before the window although the pair showed 1.0 until t-910 s.
        history = heat_veto.PairHistory()
        self.observe_series(history, coin, [(back, 1.0) for back in range(1200, 905, -10)]
                            + [(back, 0.7) for back in range(890, 0, -10)])
        samples = history.view(coin)['samples']
        self.assertEqual([s[1] for s in samples if s[0] > self.now - 900_000 and s[1] == 1.0], [])
        self.assertEqual([s for s in samples if s[0] <= self.now - 900_000][-1][1], 1.0)
        self.assertEqual(history.view(coin)['gaps'], [])
        self.assertIn('heat_crash_in_progress',
                      heat_veto.evaluate(dict(coin, priceUsd=0.7), self.now, history)['reasons'])

    def test_malformed_inputs_never_raise_and_leave_the_rule_unevaluated(self):
        malformed = [{'txns': {'m5': 5}}, {'txns': {'m5': [1, 2]}}, {'txns': 7}, {'txns': {'m5': {'buys': 'x'}}},
                     {'volume': 'hot'}, {'volume': {'m5': [1], 'h1': None}}, {'priceChange': [200]},
                     {'priceChange': {'h6': 'NaN'}}, {'symbol': 42}, {'sources': 'latest'}, {'liquidityUsd': 'x'},
                     {'marketCap': {'usd': 1}}, {'pairCreatedAt': 'yesterday'}, {'updatedAt': 'now'}]
        for change in malformed:
            with self.subTest(change=change):
                result = self.evaluate(dict(self.coin, **change))
                self.assertEqual(result['version'], 'HEAT_VETO_STACK_V1')
        self.assertIsNone(self.evaluate(dict(self.coin, txns={'m5': 5}))['metrics']['buy_share_5m'])
        self.assertIsNone(self.evaluate(dict(self.coin, txns={'m5': [90, 10]}))['metrics']['buy_share_5m'])
        for coin in (None, [], 'x', 5):
            with self.subTest(coin=coin):
                self.assertTrue(heat_veto.evaluate(coin, self.now, self.history)['vetoed'])
                self.assertFalse(self.history.observe_coin(coin, self.now))


# ------------------------------------------------------------------ pool loss memory

class PoolLossMemoryTests(unittest.TestCase):
    NOW = 1_800_000_000_000

    def test_two_consecutive_losses_block_for_six_hours_after_the_last(self):
        history = losses(MINT, PAIR, self.NOW, first_age=10 * MINUTE)
        before = copy.deepcopy(history)
        result = pool_loss_memory.check({'address': MINT, 'pairAddress': PAIR}, self.NOW, history=history)
        self.assertTrue(result['blocked'])
        self.assertEqual(result['reasons'], ['pool_loss_cooldown'])
        self.assertEqual(result['consecutive_losses'], 2)
        self.assertEqual(result['blocked_until'], self.NOW - 10 * MINUTE + 6 * 3_600_000)
        self.assertEqual(history, before, 'read-only')
        later = self.NOW - 10 * MINUTE + 6 * 3_600_000
        self.assertFalse(pool_loss_memory.check({'address': MINT, 'pairAddress': PAIR}, later,
                                                history=history)['blocked'])

    def test_streak_rules(self):
        coin = {'address': MINT, 'pairAddress': PAIR}
        one = losses(MINT, PAIR, self.NOW, count=1)
        self.assertFalse(pool_loss_memory.check(coin, self.NOW, history=one)['blocked'])
        win_between = [one[0], {**one[0], 'pnl_usd': 2.0, 'closed_at': one[0]['closed_at'] - 1},
                       {**one[0], 'closed_at': one[0]['closed_at'] - 2}]
        self.assertFalse(pool_loss_memory.check(coin, self.NOW, history=win_between)['blocked'])
        breakeven = [one[0], {**one[0], 'pnl_usd': 0.0, 'closed_at': one[0]['closed_at'] - 1},
                     {**one[0], 'closed_at': one[0]['closed_at'] - 2}]
        self.assertFalse(pool_loss_memory.check(coin, self.NOW, history=breakeven)['blocked'])
        unknown_between = [one[0], {**one[0], 'pnl_usd': None, 'closed_at': one[0]['closed_at'] - 1},
                           {**one[0], 'closed_at': one[0]['closed_at'] - 2}]
        self.assertTrue(pool_loss_memory.check(coin, self.NOW, history=unknown_between)['blocked'])
        other_pools = losses(MINT, OTHER_PAIR, self.NOW, count=1) + losses(OTHER_MINT, PAIR, self.NOW, count=1)
        self.assertFalse(pool_loss_memory.check(coin, self.NOW, history=one + other_pools[1:])['blocked'])
        self.assertFalse(pool_loss_memory.check(coin, self.NOW, history=[{'junk': 1}, None, 'x'])['blocked'])

    def test_rebuy_every_35_minutes_while_falling_is_stopped_after_two_losses(self):
        """The forensic swordinu pattern: re-buys every 30-40 minutes into one falling pool."""
        history, entered = [], []
        clock = self.NOW
        for attempt in range(8):
            if not pool_loss_memory.check({'address': MINT, 'pairAddress': PAIR}, clock, history=history)['blocked']:
                entered.append(attempt)
                history.insert(0, {'address': MINT, 'pairAddress': PAIR, 'pnl_usd': -3.0, 'closed_at': clock + 5 * MINUTE})
            clock += 35 * MINUTE
        self.assertEqual(entered, [0, 1])

    def test_memory_is_one_ledger_only(self):
        # No cross-ledger union exists: one ledger's index never contains another's pools.
        self.assertFalse(hasattr(pool_loss_memory, 'merge'))
        config = pool_loss_memory.config()
        self.assertFalse(config['cross_ledger_union'])
        self.assertEqual(config['cooldown_hours'], 6.0)
        self.assertEqual(config['scope'], 'same (mint, pool) within one account or one Lab book')


# ------------------------------------------------------------------ combined layer

class DefensiveLayerTests(unittest.TestCase):
    NOW = 1_800_000_000_000

    def test_reasons_order_and_allowed(self):
        layer = entry_defense.DefensiveEntryLayer()
        coin = established(self.NOW)
        self.assertEqual(layer.evaluate(coin, self.NOW)['reasons'], ['heat_history_warming'])
        warm(layer.history, coin, self.NOW)
        decision = layer.evaluate(coin, self.NOW, closed_history=[])
        self.assertTrue(decision['allowed'], decision)
        blocked = pool_loss_memory.index(losses(MINT, PAIR, self.NOW), self.NOW)
        young = dict(coin, pairCreatedAt=self.NOW - 30 * MINUTE)
        decision = layer.evaluate(young, self.NOW, blocked_pools=blocked)
        self.assertEqual(decision['reasons'], ['rug_young_pool', 'pool_loss_cooldown'])
        self.assertFalse(decision['allowed'])
        hot = dict(coin, txns={'m5': {'buys': 95, 'sells': 5}})
        self.assertEqual(layer.evaluate(hot, self.NOW)['reasons'], ['heat_buy_share_5m'])
        logged = layer.evaluate(hot, self.NOW, heat_log_only=True)
        self.assertTrue(logged['allowed'])
        self.assertEqual(logged['log_only_flags'], ['heat_buy_share_5m'])
        summary = entry_defense.new_summary()
        for decision in (layer.evaluate(young, self.NOW, blocked_pools=blocked), logged):
            entry_defense.record(summary, decision, young)
        self.assertEqual((summary['checked'], summary['blocked']), (2, 1))
        self.assertEqual(summary['primary_rejections'], {'rug_young_pool': 1})
        self.assertEqual(entry_defense.primary_reason(summary), 'rug_young_pool')
        self.assertEqual(summary['log_only_flags'], {'heat_buy_share_5m': 1})
        compact = entry_defense.compact(logged)
        self.assertEqual(compact['versions'], {'defensive_entry': 'DEFENSIVE_ENTRY_LAYER_V1',
                                               'structural_rug_guard': 'STRUCTURAL_RUG_GUARD_V1',
                                               'heat_veto': 'HEAT_VETO_STACK_V1',
                                               'pool_loss_memory': 'POOL_LOSS_MEMORY_V1'})

    def test_config_is_complete(self):
        config = entry_defense.config()
        self.assertEqual(config['version'], 'DEFENSIVE_ENTRY_LAYER_V1')
        self.assertFalse(config['exits_changed'])
        self.assertFalse(config['profitability_proven'])
        self.assertEqual(config['heat_veto']['parameters']['turnover_5m'], 0.095)
        self.assertEqual(config['pool_loss_memory']['consecutive_losses'], 2)
        self.assertEqual(config['reasons_in_order'][-1], 'defensive_entry_error')

    def test_an_unexpected_error_blocks_the_candidate_and_never_escapes(self):
        layer = entry_defense.DefensiveEntryLayer()
        coin = established(self.NOW)
        warm(layer.history, coin, self.NOW)
        with patch.object(heat_veto, 'evaluate', side_effect=RuntimeError('boom')):
            decision = layer.evaluate(coin, self.NOW, blocked_pools={})
        self.assertFalse(decision['allowed'])
        self.assertEqual(decision['reasons'], ['defensive_entry_error'])
        self.assertEqual(decision['error'], 'RuntimeError')
        self.assertEqual(layer.status()['evaluate_errors'], 1)
        self.assertEqual(layer.status()['last_error'], 'evaluate:RuntimeError')
        summary = entry_defense.new_summary()
        entry_defense.record(summary, decision, coin)
        self.assertEqual(summary['rejections'], {'defensive_entry_error': 1})
        self.assertEqual(entry_defense.primary_reason(summary), 'defensive_entry_error')
        self.assertEqual(entry_defense.compact(decision)['error'], 'RuntimeError')
        self.assertEqual(entry_defense.metrics(decision)['error'], 'RuntimeError')
        # A failing observation is counted, never raised; the history stays short (warming).
        fresh = entry_defense.DefensiveEntryLayer()
        with patch.object(fresh.history, 'observe', side_effect=ValueError('bad feed')):
            fresh.observe([coin], self.NOW)
        fresh.observe(None, self.NOW)
        fresh.observe(5, self.NOW)
        self.assertEqual(fresh.status()['observe_errors'], 2)
        self.assertEqual(fresh.evaluate(coin, self.NOW)['reasons'], ['heat_history_warming'])
        # The real layer tolerates a malformed txns.m5 without the error path.
        for txns in ({'m5': 5}, {'m5': [1, 2]}, 7):
            with self.subTest(txns=txns):
                decision = layer.evaluate(dict(coin, txns=txns), self.NOW, blocked_pools={})
                self.assertTrue(decision['allowed'], decision)
        self.assertEqual(layer.status()['evaluate_errors'], 1)

    def test_every_reason_has_engine_and_lab_dashboard_labels(self):
        import engine_entry_policy
        source = (Path(__file__).resolve().parents[1] / 'src' / 'lib' / 'labStrategyView.ts').read_text(encoding='utf-8')
        block = source[source.index('const reasons: Record<string, string> = {'):]
        block = block[:block.index('};')]
        for reason in entry_defense.REASONS_IN_ORDER + ('defensive_entry',):
            with self.subTest(reason=reason):
                self.assertIn(f'\n  {reason}: ', block.replace('\r\n', '\n'))
                if reason != 'defensive_entry':
                    self.assertIn(reason, engine_entry_policy.LABELS)


# ------------------------------------------------------------------ score companion

class ScoreBandTests(unittest.TestCase):
    def pair(self, liquidity, cap):
        # Volume scales with liquidity so only the liq/MC component differs between the cases.
        return {'liquidity': {'usd': liquidity}, 'marketCap': cap, 'volume': {'h1': liquidity * .5},
                'priceChange': {'m5': 0, 'h1': 0}, 'txns': {'m5': {'buys': 10, 'sells': 10}},
                # 3.5 days: clear of score_pair's 4,320-minute age boundary between calls.
                'pairCreatedAt': m.now_ms() - 3 * DAY - 12 * 60 * MINUTE}

    def titles(self, liquidity, cap):
        score, _risk, _posture, signals = m.score_pair(self.pair(liquidity, cap), {})
        return score, [(s['kind'], s['title']) for s in signals]

    def test_good_liquidity_bonus_only_inside_the_band_and_lp_risk_above_one(self):
        good, good_titles = self.titles(60_000, 200_000)          # 0.30
        neutral, neutral_titles = self.titles(140_000, 200_000)   # 0.70
        risky, risky_titles = self.titles(300_000, 200_000)       # 1.50
        self.assertIn(('positive', 'Добро liquidity/MC'), good_titles)
        self.assertNotIn(('positive', 'Добро liquidity/MC'), neutral_titles)
        self.assertIn(('risk', 'Ликвидност >= MC (LP риск)'), risky_titles)
        # Liquidity and volume tiers are equal for 0.70 and 1.50: only the liq/MC component differs.
        self.assertEqual(round(neutral - risky, 1), 10.0)
        self.assertGreater(good, risky)
        self.assertEqual(m.SCORE_VERSION, 'NEO_MARKET_SCORE_V2_LIQ_MC_BAND')

    def test_lp_pullable_research_pool_no_longer_earns_the_bonus(self):
        coin, _ = fixture('SharkTank_CRGN2uGj')
        pair = {'liquidity': {'usd': coin['liquidityUsd']}, 'marketCap': coin['marketCap'],
                'volume': coin['volume'], 'priceChange': coin['priceChange'], 'txns': coin['txns'],
                'pairCreatedAt': coin['pairCreatedAt']}
        _score, _risk, _posture, signals = m.score_pair(pair, {})
        titles = [s['title'] for s in signals]
        self.assertNotIn('Добро liquidity/MC', titles)
        made = m.make_coin(MINT, {**pair, 'pairAddress': PAIR, 'priceUsd': 1, 'baseToken': {'symbol': 'X'}}, {})
        self.assertEqual(made['scoreVersion'], m.SCORE_VERSION)


class ExitContextScoreTests(unittest.TestCase):
    """NEO_MARKET_SCORE_V2 is an entry change only: held ORDER_FLOW_ADAPTIVE positions keep
    the exit context (conviction, hold mode, max hold, target, trail) of the V1 score."""

    FAST = {'quality': 'COMPLETE', 'buy_sell_usd_ratio': 1.5}
    SLOW = {'buy_sell_usd_ratio': 1.2, 'unique_wallets': 6, 'repeat_buy_wallets': 1,
            'whale_buy_usd': 0, 'whale_sell_usd': 0}

    def setUp(self):
        self.monitor = m.Monitor()
        self.addCleanup(self.monitor.stop)
        flows = {30: self.FAST, 300: self.SLOW}
        p = patch.object(m.STATE, 'live_flow', side_effect=lambda address, seconds, pair='': dict(flows[seconds]))
        p.start()
        self.addCleanup(p.stop)

    @staticmethod
    def pair(ratio):
        cap = 1_000_000
        return {'liquidity': {'usd': ratio * cap}, 'marketCap': cap, 'volume': {'h1': 60_000},
                'priceChange': {'m5': 2, 'h1': 5}, 'txns': {'m5': {'buys': 70, 'sells': 50}},
                # 3.5 days: clear of score_pair's 4,320-minute age boundary between calls.
                'pairCreatedAt': m.now_ms() - 3 * DAY - 12 * 60 * MINUTE, 'pairAddress': PAIR, 'priceUsd': 1,
                'baseToken': {'symbol': 'X'}}

    @staticmethod
    def pre_change_score(pair):
        """The score_pair of origin/main (+9 for any liq/MC >= 0.15, no LP-risk term)."""
        with patch.object(m, 'SCORE_GOOD_LIQ_MC_RANGE', (0.15, float('inf'))), \
             patch.object(m, 'SCORE_LP_RISK_LIQ_MC', float('inf')):
            return m.score_pair(pair, {})[0]

    def test_adaptive_exit_context_is_unchanged_at_liq_mc_0_7_and_1_1(self):
        sensitive = False
        for ratio in (0.7, 1.1):
            with self.subTest(liq_mc=ratio):
                pair = self.pair(ratio)
                coin = m.make_coin(MINT, pair, {})
                before = self.pre_change_score(pair)
                self.assertEqual(coin['scoreV1'], before)
                self.assertLess(coin['score'], before, 'V2 removes the bonus in this band')
                # The coin the pre-change engine saw: 'score' was the V1 score, no scoreV1.
                old_coin = {key: value for key, value in coin.items() if key != 'scoreV1'}
                old_coin['score'] = before
                for opened_under in (m.PREVIOUS_SCORE_VERSION, m.SCORE_VERSION, None):
                    position = {'address': MINT, 'pairAddress': PAIR, 'exit_policy': 'adaptive',
                                'entry_liquidity_usd': coin['liquidityUsd'], 'score_version': opened_under}
                    now_context = self.monitor.market_context(coin, position)
                    old_context = self.monitor.market_context(old_coin, position)
                    for field in ('conviction', 'mode', 'max_hold_minutes', 'target_pct', 'trail_arm_pct', 'trail_pct'):
                        self.assertEqual(now_context[field], old_context[field], (field, opened_under))
                    self.assertNotIn('exit_basis_conviction', now_context)
                    self.assertEqual(m.adaptive_exit_context(position, now_context),
                                     m.adaptive_exit_context(position, old_context))
                # Entries use V2; the hold mode a new position keeps is the V1-basis one.
                entry = self.monitor.market_context(coin)
                old_entry = self.monitor.market_context(old_coin)
                self.assertEqual(entry['exit_basis_conviction'], old_entry['conviction'])
                if entry['conviction'] != old_entry['conviction']:
                    sensitive = True
                    # What feeding V2 to exits would have done (the reviewed regression).
                    v2_fed = self.monitor.market_context(dict(coin, scoreV1=coin['score']), {'address': MINT, 'x': 1})
                    self.assertNotEqual(v2_fed['conviction'], old_entry['conviction'])
        self.assertTrue(sensitive, 'the fixture must make V1 and V2 convictions differ')

    def test_an_observation_recorded_before_v2_keeps_its_own_score(self):
        old = {'address': MINT, 'pairAddress': PAIR, 'score': 97.0}
        self.assertEqual(m.exit_context_score(old), 97.0)
        self.assertEqual(m.exit_context_score(dict(old, scoreV1=100.0)), 100.0)
        self.assertEqual(m.exit_context_score(dict(old, scoreV1='bad')), 97.0)
        self.assertEqual(m.exit_context_score({}), 0.0)
        self.assertEqual(m.EXIT_CONTEXT_SCORE_VERSION, 'NEO_MARKET_SCORE_V1')
        config = m.STATE.snapshot()['config']
        self.assertEqual(config['exit_context_score_version'], 'NEO_MARKET_SCORE_V1')


# ------------------------------------------------------------------ engine entry paths

class EngineHarness(unittest.TestCase):
    """The real engine path with fixture quotes, flow, safety and price (offline)."""
    STRATEGY = winner_ensemble.VERSION

    def setUp(self):
        m.activate_strategy(self.STRATEGY)
        self.addCleanup(m.activate_strategy, m.DEFAULT_SIGNAL_STRATEGY)
        m.STATE_PATH.unlink(missing_ok=True)
        m.STATE = m.State()
        self.monitor = m.Monitor()
        self.monitor._entry_defense = entry_defense.DefensiveEntryLayer()   # non-persistent per test
        self.addCleanup(self.monitor.stop)
        self.now = m.now_ms()
        self.coin = established(self.now)
        self.calls = {'quote': 0, 'rugcheck': 0, 'price': 0, 'flow_admission': 0}
        self.flow = {'quality': 'COMPLETE', 'fresh': True, 'trades': 4, 'buys': 3, 'sells': 1,
                     'buy_sell_usd_ratio': 2.0, 'unique_wallets': 4, 'buyer_wallets': 3,
                     'wallet_buy_sell_ratio': 3, 'max_sell_usd': 50, 'buy_usd': 300, 'sell_usd': 50,
                     'verified_flow': {'source': promoted_guard.FLOW_SOURCE, 'coverage_status': 'COMPLETE',
                                       'window_ms': promoted_guard.FLOW_WINDOW_MS, 'address': MINT,
                                       'pairAddress': PAIR, 'window_at': self.now,
                                       'latest_event_at': self.now - 100, 'available_at': self.now - 50,
                                       'trades': 4, 'unique_wallets': 3, 'buy_usd': 300, 'sell_usd': 50}}
        real_flow_admission = promoted_guard.flow_admission

        def flow_admission(*args, **kwargs):
            self.calls['flow_admission'] += 1
            return real_flow_admission(*args, **kwargs)

        def entry_quote(mint, pair, notional):
            self.calls['quote'] += 1
            raw = int(round(float(notional) * 1_000_000))
            return {'token_raw_expected': raw, 'token_raw_floor': int(raw * .99), 'token_raw_amount': raw,
                    'input_usdc_raw': int(round(float(notional) * 1e6)), 'price_impact_pct': 0.4,
                    'raw_quote': {'fixture': True}, 'slippage_bps': 100, 'route': [],
                    'quoted_at': m.paper_quotes.stamp()}

        def exit_quote(mint, raw, pair=None, purpose='exit', force=False):
            expected = raw / 1_000_000 * 0.992
            return {'expected_usdc': expected, 'floor_usdc': expected * .99, 'provider_expected_usdc': expected,
                    'price_impact_pct': 0.5, 'quoted_at': m.paper_quotes.stamp(), 'raw_quote': {'fixture': True}}

        def rugcheck(coin):
            self.calls['rugcheck'] += 1
            return {'status': 'pass', 'mint': coin.get('address'), 'pair': coin.get('pairAddress'),
                    'checked_at': m.now_ms(), 'metrics': {'decimals': 6, 'token_account_rent_lamports': 1650000}}

        def price(coin):
            self.calls['price'] += 1
            return {'status': 'pass', 'version': 'TEST'}

        for p in (patch.object(m.STATE, 'live_flow', side_effect=lambda *a, **k: self.flow),
                  patch.object(self.monitor, 'market_context', return_value={'conviction': 80, 'mode': 'STRONG'}),
                  patch.object(m.paper_quotes, 'entry_quote', side_effect=entry_quote),
                  patch.object(m.paper_quotes, 'exit_quote', side_effect=exit_quote),
                  patch.object(m, 'append_audit'),
                  patch.object(m.rug_guard, 'check', side_effect=rugcheck),
                  patch.object(m.price_integrity, 'check', side_effect=price),
                  patch.object(m.promoted_guard, 'flow_admission', side_effect=flow_admission)):
            p.start()
            self.addCleanup(p.stop)

    def warm(self, coin=None, **kwargs):
        warm(self.monitor.defense.history, coin or self.coin, self.now, **kwargs)

    def open_once(self, coin=None):
        self.monitor.maybe_open([coin or self.coin])
        return m.STATE.entry_diagnostics

    def assert_blocked_before_any_quote(self, reason, coin=None, *, by_layer=True):
        """``by_layer`` False: the cost-first universe (which embeds the structural guard) refused it."""
        report = self.open_once(coin)
        self.assertFalse(m.STATE.positions)
        self.assertIn(reason, report['rejections'], report['rejections'])
        self.assertEqual(self.calls, {'quote': 0, 'rugcheck': 0, 'price': 0, 'flow_admission': 0})
        if by_layer:
            self.assertGreaterEqual(report['defensive_entry']['blocked'], 1)
            self.assertIn(reason, report['defensive_entry']['rejections'])
        return report


class DefaultEnginePathTests(EngineHarness):
    def test_established_warmed_pool_without_losses_opens_and_records_the_layer(self):
        self.warm()
        report = self.open_once()
        self.assertEqual(report['status'], 'opened', report['rejections'])
        position = m.STATE.positions[0]
        self.assertEqual(position['entry_policy_version'], 'WINNER_ENSEMBLE_VERIFIED_ENTRY_V5')
        self.assertEqual(position['strategy_matches'], ['COST_EFFICIENT_FLOW'])
        self.assertTrue(position['defensive_entry']['allowed'])
        self.assertEqual(position['defensive_entry']['versions']['structural_rug_guard'], 'STRUCTURAL_RUG_GUARD_V1')
        self.assertEqual(position['defensive_entry']['versions']['heat_veto'], 'HEAT_VETO_STACK_V1')
        self.assertEqual(position['defensive_entry']['versions']['pool_loss_memory'], 'POOL_LOSS_MEMORY_V1')
        self.assertEqual(position['score_version'], m.SCORE_VERSION)
        self.assertEqual(position['exit_context_score_version'], 'NEO_MARKET_SCORE_V1')
        self.assertEqual(report['defensive_entry']['checked'], 1)
        self.assertEqual(self.calls['quote'] > 0, True)

    def test_daws_like_fake_market_cap_is_blocked_before_quotes(self):
        coin, _ = fixture('DAWS_BTSpnpim')
        daws = established(self.now, symbol=coin['symbol'], marketCap=coin['marketCap'],
                           liquidityUsd=coin['liquidityUsd'], pairCreatedAt=self.now - 4 * DAY,
                           volume={'m5': 1_000.0, 'h1': 20_000.0})
        self.assertIn('COST_EFFICIENT_FLOW', winner_ensemble.market_candidates(daws))
        self.warm(daws)
        report = self.assert_blocked_before_any_quote('rug_fake_market_cap', daws)
        self.assertEqual(report['examples'][0]['reasons'], ['rug_fake_market_cap'])

    def test_young_pool_matching_an_ensemble_rule_is_blocked_before_quotes(self):
        young = established(self.now, ageMinutes=60, pairCreatedAt=self.now - 60 * MINUTE,
                            marketCap=1_000_000, liquidityUsd=200_000, priceChange={'m5': 6, 'h1': -15},
                            volume={'m5': 1_000.0, 'h1': 50_000.0})
        self.assertIn('VERIFIED_FLOW_MOMENTUM', winner_ensemble.market_candidates(young))
        self.warm(young)
        self.assert_blocked_before_any_quote('rug_young_pool', young)

    def test_heat_warm_up_is_a_veto_before_quotes(self):
        self.assert_blocked_before_any_quote('heat_history_warming')

    def test_heat_surge_is_blocked_before_quotes(self):
        self.warm(price=self.coin['priceUsd'] / 1.04)
        report = self.assert_blocked_before_any_quote('heat_return_5m_surge')
        self.assertGreaterEqual(report['examples'][0]['metrics']['heat']['return_5m_pct'], 3.0)

    def test_two_losses_on_the_pool_block_it_while_the_cooldown_alone_would_not(self):
        self.warm()
        m.STATE.history = losses(MINT, PAIR, self.now, first_age=30 * MINUTE)   # past the 20-min cooldown
        report = self.assert_blocked_before_any_quote('pool_loss_cooldown')
        self.assertEqual(report['defensive_entry']['pool_loss_cooldown_pools'], 1)

    def test_rugcheck_prewarm_requests_nothing_for_a_blocked_pool(self):
        # Prewarm only considers pools with ageMinutes <= 360, all of which the 12 h
        # young-pool rule blocks in reality; 'passing' carries a 60-minute ageMinutes with
        # a 30-day pairCreatedAt only to show that an unblocked pool is still prewarmed.
        young = established(self.now, address=OTHER_MINT, pairAddress=OTHER_PAIR, symbol='YNG',
                            ageMinutes=60, pairCreatedAt=self.now - 60 * MINUTE)
        passing = dict(self.coin, ageMinutes=60)
        self.warm(young)
        self.warm(passing)
        self.monitor.prewarm_entry_checks([young])
        self.assertEqual((self.calls['price'], self.calls['rugcheck']), (0, 0), 'structural block')
        self.monitor.prewarm_entry_checks([dict(passing, txns={'m5': {'buys': 95, 'sells': 5}})])
        self.assertEqual((self.calls['price'], self.calls['rugcheck']), (0, 0), 'heat veto')
        m.STATE.history = losses(MINT, PAIR, self.now, first_age=30 * MINUTE)
        self.monitor.prewarm_entry_checks([passing])
        self.assertEqual((self.calls['price'], self.calls['rugcheck']), (0, 0), 'loss memory')
        m.STATE.history = []
        self.monitor.prewarm_entry_checks([passing, young])
        self.assertEqual((self.calls['price'], self.calls['rugcheck']), (1, 1), 'only the passing pool')
        self.assertEqual(self.calls['quote'], 0)


class OrderFlowAdaptivePathTests(EngineHarness):
    STRATEGY = oct4.STRATEGY_ID

    def test_layer_runs_after_the_market_screen_and_before_the_oct4_signal_flow_and_quotes(self):
        signal_calls = []
        with patch.object(m.oct4, 'market_rejections', return_value=[]), \
             patch.object(m.oct4, 'signal_rejections', side_effect=lambda *a, **k: signal_calls.append(1) or ['conviction']):
            self.assert_blocked_before_any_quote('heat_history_warming')
            self.assertEqual(signal_calls, [])
            # A fresh layer, warmed with five minutes of contiguous history.
            self.monitor._entry_defense = entry_defense.DefensiveEntryLayer()
            self.warm()
            young = dict(self.coin, pairCreatedAt=self.now - 30 * MINUTE)
            self.assert_blocked_before_any_quote('rug_young_pool', young)
            m.STATE.history = losses(MINT, PAIR, self.now, first_age=30 * MINUTE)
            self.assert_blocked_before_any_quote('pool_loss_cooldown')
            m.STATE.history = []
            report = self.open_once()
            self.assertEqual(signal_calls, [1], 'a passing pool reaches the October 4 checks')
            self.assertIn('conviction', report['rejections'])
        self.assertEqual(m.ENTRY_POLICY_VERSION, 'ORDER_FLOW_BALANCED_V5')
        self.assertEqual(oct4.DECISION_FILTER_VERSION, 'ORDER_FLOW_BALANCED_V4')

    def test_a_new_position_keeps_the_v1_basis_hold_mode_for_blind_flow_exits(self):
        # Entry gates read the V2 conviction (70: NORMAL); the hold mode the blind-flow exit
        # fallback keeps is the V1-basis one of the same flow (80: STRONG).
        self.warm()
        self.monitor.market_context.return_value = {
            'conviction': 70.0, 'exit_basis_conviction': 80.0, 'mode': 'NORMAL', 'max_hold_minutes': 15,
            'target_pct': 20, 'trail_arm_pct': 9, 'trail_pct': 5}
        with patch.object(m.oct4, 'market_rejections', return_value=[]), \
             patch.object(m.oct4, 'signal_rejections', return_value=[]):
            report = self.open_once()
        self.assertEqual(report['status'], 'opened', report['rejections'])
        position = m.STATE.positions[0]
        self.assertEqual(position['entry_conviction'], 70.0)
        self.assertEqual(position['entry_hold_mode'], 'NORMAL')
        self.assertEqual(position['adaptive_hold'], oct4.hold_mode(80.0))
        self.assertEqual(position['adaptive_hold']['mode'], 'STRONG')
        self.assertEqual(position['exit_context_score_version'], 'NEO_MARKET_SCORE_V1')
        self.assertEqual(position['exit_policy_version'], 'GOLD_ADAPTIVE_NET_CANDIDATE_V1')


class CostFirstProfilePathTests(EngineHarness):
    STRATEGY = cfp.STRATEGY_ID

    def universe_coin(self, **changes):
        coin = established(self.now, dexId='pumpswap', quoteToken={'address': SOL}, score=40,
                           liquidityUsd=400_000.0, marketCap=10_000_000.0, priceUsd=.001,
                           priceNative=.00001, volume={'m5': 1_000.0, 'h1': 50_000.0})
        coin.update(changes)
        return coin

    def test_fake_market_cap_rug_family_never_enters_the_universe(self):
        # WOSE (GhBPuDpt) as the cost-first account met it: $235M "market cap" on $1.4M
        # liquidity, 30 bps tier, 410 minutes old, the ticker already used by other mints.
        wose, _ = fixture('WOSE_GhBPuDpt')
        coin = self.universe_coin(symbol=wose['symbol'], marketCap=wose['marketCap'],
                                  liquidityUsd=wose['liquidityUsd'], pairCreatedAt=self.now - 410 * MINUTE)
        self.monitor.defense.registry.observe_coin(
            {'address': OTHER_MINT, 'pairAddress': OTHER_PAIR, 'symbol': 'WOSE'}, self.now - DAY)
        self.assertEqual(cost_first.fee_tier_bps(coin), 30.0)
        report = self.assert_blocked_before_any_quote('rug_fake_market_cap', coin, by_layer=False)
        self.assertEqual(report['examples'][0]['reasons'],
                         ['rug_young_pool', 'rug_fake_market_cap', 'rug_ticker_reuse'])
        self.assertFalse(m.market_candidate(coin, self.now, self.monitor.defense.registry))

    def test_universe_member_still_passes_heat_and_loss_memory_before_quotes(self):
        coin = self.universe_coin()
        self.assertEqual(cfp.universe_rejections(coin, m.TRADE_NOTIONAL_USD, now=self.now,
                                                 ticker_registry=self.monitor.defense.registry), [])
        self.assert_blocked_before_any_quote('heat_history_warming', coin)
        self.monitor._entry_defense = entry_defense.DefensiveEntryLayer()
        self.warm(coin)
        m.STATE.history = losses(MINT, PAIR, self.now, first_age=30 * MINUTE)
        self.assert_blocked_before_any_quote('pool_loss_cooldown', coin)
        m.STATE.history = []
        report = self.open_once(coin)
        self.assertEqual(report['status'], 'opened', report['rejections'])
        position = m.STATE.positions[0]
        self.assertEqual(position['entry_policy_version'], 'COST_FIRST_ESTABLISHED_ENTRY_V2')
        self.assertEqual(position['strategy_profile_version'], 'COST_FIRST_ENGINE_PROFILE_V2_DEFENSIVE_ENTRY')
        self.assertFalse(position['cost_first_universe']['structural_rug_guard']['blocked'])
        self.assertTrue(position['defensive_entry']['allowed'])


class TrainingProbePathTests(unittest.TestCase):
    NOW = 1_800_000_000_000

    def setUp(self):
        self.monitor = m.Monitor()
        self.monitor._entry_defense = entry_defense.DefensiveEntryLayer()
        self.addCleanup(self.monitor.stop)
        self.coin = established(self.NOW, score=60, liquidityUsd=100_000.0, marketCap=1_000_000.0,
                                volume={'m5': 1_000.0, 'h1': 40_000.0})
        self.flow = {'fresh': True, 'quality': 'COMPLETE', 'latest_at': self.NOW,
                     'coverage': {'status': 'COMPLETE', 'address': MINT, 'pairAddress': PAIR}, 'trades': 3,
                     'unique_wallets': 2, 'buy_usd': 25, 'buy_sell_usd_ratio': 1.1}
        state = SimpleNamespace(lock=threading.RLock(), running=True, positions=[], history=[],
                                feed=[self.coin], live_flow=lambda *_: copy.deepcopy(self.flow))
        self.state = state
        self.safety = {'status': 'pass', 'mint': MINT, 'pair': PAIR, 'checked_at': self.NOW,
                       'metrics': {'decimals': 9, 'token_account_rent_lamports': 2_039_280, 'sol_usd': 200}}
        self.price = {'status': 'pass', 'mint': MINT, 'pair': PAIR, 'reference_price': 1,
                      'reference_received_at': self.NOW}
        mocks = {}
        for target, name, kwargs in [
                (m, 'STATE', {'new': state}), (m, 'now_ms', {'return_value': self.NOW}),
                (m.training_bridge, 'enabled', {'return_value': True}), (m.training_bridge, 'note_quote_probe', {}),
                (m.training_bridge, 'observe', {}),
                (m.price_integrity, 'check', {'return_value': self.price}),
                (m.rug_guard, 'check', {'return_value': self.safety}), (m.threading, 'Thread', {}),
                (m, 'collect_exact_pool_quotes', {'return_value': (None, 'blocked')})]:
            p = patch.object(target, name, **kwargs)
            mocks[name] = p.start()
            self.addCleanup(p.stop)
        self.mocks = mocks

    def test_blocked_candidate_spends_no_price_check_rugcheck_or_probe(self):
        cases = [('heat_history_warming', self.coin, []),
                 ('rug_young_pool', dict(self.coin, pairCreatedAt=self.NOW - 30 * MINUTE), []),
                 ('pool_loss_cooldown', self.coin, losses(MINT, PAIR, self.NOW, first_age=30 * MINUTE))]
        for reason, coin, history in cases:
            with self.subTest(reason=reason):
                if reason == 'pool_loss_cooldown':
                    warm(self.monitor.defense.history, coin, self.NOW)
                self.state.history = history
                self.monitor.training_probe_last_attempt_at = 0
                self.mocks['note_quote_probe'].reset_mock()
                self.monitor.schedule_training_quote_probe([coin])
                self.mocks['Thread'].assert_not_called()
                self.mocks['note_quote_probe'].assert_called_once_with(
                    'WAITING_FOR_FRESH_PREFLIGHT', reason=reason, at=self.NOW)
        self.assertEqual(m.price_integrity.check.call_count, 0)
        self.assertEqual(m.rug_guard.check.call_count, 0)

    def test_passing_candidate_reaches_preflight_and_the_worker_rechecks_before_quotes(self):
        warm(self.monitor.defense.history, self.coin, self.NOW)
        self.monitor.schedule_training_quote_probe([self.coin])
        self.assertEqual(m.rug_guard.check.call_count, 1)
        self.mocks['Thread'].assert_called_once()
        # The worker re-asks the layer on the current observation before any quote.
        self.state.history = losses(MINT, PAIR, self.NOW, first_age=30 * MINUTE)
        self.monitor.run_training_quote_probe(self.coin, self.flow, self.safety, self.price)
        self.mocks['collect_exact_pool_quotes'].assert_not_called()
        self.mocks['note_quote_probe'].assert_called_with('SKIPPED', reason='pool_loss_cooldown', at=self.NOW)
        self.state.history = []
        self.monitor.run_training_quote_probe(self.coin, self.flow, self.safety, self.price)
        self.mocks['collect_exact_pool_quotes'].assert_called_once()


# ------------------------------------------------------------------ Strategy Lab

class LabPathTests(unittest.TestCase):
    NOW = 1_800_000_000_000

    def setUp(self):
        lab.rush_brain._SAMPLE_BY_PAIR.clear()
        self.books = {s['id']: lab.empty_book(s) for s in lab.STRATEGIES}
        self.layer = entry_defense.DefensiveEntryLayer()
        self.calls = {'rugcheck': 0, 'price': 0, 'probe': 0}

        def rugcheck(coin):
            self.calls['rugcheck'] += 1
            return {'status': 'pass', 'mint': coin['address'], 'pair': coin['pairAddress'], 'checked_at': self.NOW}

        def price(coin):
            self.calls['price'] += 1
            return {'status': 'pass', 'mint': coin['address'], 'pair': coin['pairAddress']}

        for p in (patch.object(lab, 'STATE', {'started_at': 42, 'books': self.books}),
                  patch.object(lab, 'DEFENSE', self.layer),
                  patch.object(lab, 'now_ms', return_value=self.NOW),
                  patch.object(lab.rug_guard, 'check', side_effect=rugcheck),
                  patch.object(lab.price_integrity, 'check', side_effect=price),
                  patch.object(lab, 'schedule_jupiter_price_probe',
                               side_effect=lambda coin: self.calls.__setitem__('probe', self.calls['probe'] + 1))):
            p.start()
            self.addCleanup(p.stop)

    def lab_coin(self, **changes):
        coin = established(self.NOW, liquidityUsd=3_100_000.0, marketCap=31_000_000.0,
                           volume={'m5': 15_000.0, 'h1': 400_000.0})
        coin.update(changes)
        return coin

    def test_every_matching_book_consults_the_layer_before_flow_safety_and_price(self):
        # The young LP-pullable pool most TEST rules match (research SharkTank shape).
        coin = self.lab_coin(ageMinutes=50, pairCreatedAt=self.NOW - 50 * MINUTE, liquidityUsd=1_000_000.0,
                             marketCap=1_000_000.0, priceChange={'m5': 12, 'h1': 50}, dexId='raydium',
                             txns={'m5': {'buys': 60, 'sells': 20}}, volume={'h1': 1e6})
        lab.maybe_open([coin], {})
        matched = [book for book in self.books.values()
                   if (book.get('entry_diagnostics') or {}).get('signal_candidates')]
        self.assertGreater(len(matched), 5)
        for book in matched:
            diagnostics = book['entry_diagnostics']
            with self.subTest(book=book['id']):
                self.assertIsNone(book['position'])
                self.assertEqual(diagnostics['defensive_rejected'], diagnostics['signal_candidates'])
                self.assertEqual(diagnostics['defensive_entry']['checked'], diagnostics['signal_candidates'])
                self.assertEqual(diagnostics['blocked_reason'], 'rug_lp_pullable')
                self.assertEqual(diagnostics['defensive_entry']['rejections']['rug_young_pool'], 1)
        self.assertEqual(self.calls, {'rugcheck': 0, 'price': 0, 'probe': 0})

    def test_cost_first_books_screen_the_rug_family_in_the_universe(self):
        usdp, _ = fixture('USDP_8RCJrW94')
        coin = self.lab_coin(dexId='pumpswap', symbol='USDP', marketCap=usdp['marketCap'],
                             liquidityUsd=usdp['liquidityUsd'], pairCreatedAt=self.NOW - 2 * DAY,
                             priceUsd=.1, priceNative=.1 / 116.0)
        lab.maybe_open([coin], {})
        for book_id in cost_first.BOOK_IDS:
            diagnostics = self.books[book_id]['entry_diagnostics']
            self.assertEqual(diagnostics['cost_first']['universe_rejections'], {'rug_fake_market_cap': 1})
            self.assertEqual(diagnostics['signal_candidates'], 0)
            self.assertIsNone(self.books[book_id]['position'])
        self.assertEqual(self.calls['rugcheck'], 0)

    def test_heat_warm_up_blocks_enforcing_books_while_a_log_only_arm_records_flags(self):
        coin = self.lab_coin()
        self.assertTrue(lab.activity.RULES['DEEP_LIQ_MOMENTUM'].matches(lab.enrich(coin, {})))
        with patch.object(heat_veto, 'LOG_ONLY_BOOK_IDS', frozenset({'DEEP_LIQ_MOMENTUM'})):
            lab.maybe_open([coin], {})
        logged = self.books['DEEP_LIQ_MOMENTUM']
        self.assertIsNotNone(logged['position'], logged['entry_diagnostics'])
        self.assertEqual(logged['position']['defensive_entry']['log_only_flags'], ['heat_history_warming'])
        self.assertTrue(logged['entry_diagnostics']['defensive_entry']['heat_log_only'])
        enforcing = [book for key, book in self.books.items() if key != 'DEEP_LIQ_MOMENTUM'
                     and (book.get('entry_diagnostics') or {}).get('signal_candidates')]
        for book in enforcing:
            with self.subTest(book=book['id']):
                self.assertIsNone(book['position'])
                self.assertEqual(book['entry_diagnostics']['blocked_reason'], 'heat_history_warming')

    def test_loss_memory_is_scoped_to_each_book(self):
        coin = self.lab_coin()
        warm(self.layer.history, coin, self.NOW)
        book = self.books['DEEP_LIQ_MOMENTUM']
        # Two losses on this pool in ANOTHER book do not block this book...
        self.books['BREAKOUT']['history'] = losses(MINT, PAIR, self.NOW, first_age=30 * MINUTE)
        lab.maybe_open([coin], {})
        self.assertIsNotNone(book['position'], book['entry_diagnostics'])
        self.assertTrue(book['position']['defensive_entry']['allowed'])
        self.assertEqual(book['position']['defensive_entry']['pool_loss_memory']['reasons'], [])
        # ...while the same two losses in its own history do (6 h after the last loss).
        book['position'] = None
        book['history'] = losses(MINT, PAIR, self.NOW, first_age=30 * MINUTE)
        book['last_entry_by_address'] = {}
        lab.maybe_open([coin], {})
        self.assertIsNone(book['position'])
        self.assertEqual(book['entry_diagnostics']['blocked_reason'], 'pool_loss_cooldown')
        self.assertEqual(book['entry_diagnostics']['defensive_entry']['pool_loss_cooldown_pools'], 1)


# ------------------------------------------------------------------ tape scheduler

class TapeSchedulerPathTests(unittest.TestCase):
    """A seat serves every ledger: only the structural guard withholds one (V5)."""
    NOW = 1_800_000_000_000

    def coin(self, name, **changes):
        coin = established(self.NOW, address=(name[0] * 44), pairAddress=(name[1] * 44), symbol=name,
                           dexId='pumpswap', liquidityUsd=1_500_000.0, marketCap=10_000_000.0,
                           priceUsd=.001, priceNative=.001 / 115, ageMinutes=30, score=98,
                           priceChange={'m5': 5, 'h1': 10}, txns={'m5': {'buys': 40, 'sells': 20}},
                           volume={'m5': 10_000.0, 'h1': 750_000.0})
        coin.update(changes)
        return coin

    def test_structurally_blocked_pools_get_no_seat_and_pins_are_untouched(self):
        scheduler = TapePoolScheduler()
        safe = self.coin('Safe')
        young = self.coin('Young', pairCreatedAt=self.NOW - 30 * MINUTE)
        fake = self.coin('Wake', symbol='WOSE', marketCap=235_000_000.0, liquidityUsd=1_380_000.0,
                         pairCreatedAt=self.NOW - 410 * MINUTE, ageMinutes=100_000, score=0,
                         priceChange={'m5': -40, 'h1': -60}, volume={'m5': 1_000.0, 'h1': 0})
        lost = self.coin('Lost')
        for coin in (safe, young, fake, lost):
            warm(scheduler.defense.history, coin, self.NOW)
        held = {'address': young['address'], 'pairAddress': young['pairAddress'],
                'coin_snapshot': {'dexId': 'pumpswap', 'symbol': 'Young', 'priceUsd': 1}}
        # Two losses on 'Lost' in one Lab book: that book's own entry path blocks it, but
        # the seat serves every other ledger too, so the pool keeps its seat.
        state = {'feed': [safe, young, fake, lost], 'positions': [held],
                 'history': [], 'strategy_lab': {'books': {'MOMENTUM': {'history': losses(
                     lost['address'], lost['pairAddress'], self.NOW, first_age=30 * MINUTE)}}}}
        selected, report = scheduler.select(state, now=self.NOW, max_tracked=4)
        symbols = [row['symbol'] for row in selected]
        self.assertEqual(symbols[0], 'Young', 'the held young pool stays pinned')
        self.assertEqual(sorted(symbols[1:]), ['Lost', 'Safe'], 'only the fake-cap pool is withheld')
        defensive = report['defensive_entry']
        # Pinned pools are never screened; the fake-cap pool is withheld structurally.
        self.assertEqual(defensive['checked'], 3)
        self.assertEqual(defensive['blocked_pools_in_feed'], 1)
        self.assertEqual(defensive['rejections'], {'rug_young_pool': 1, 'rug_fake_market_cap': 1})
        self.assertNotIn('pool_loss_cooldown', defensive['rejections'])
        self.assertEqual(defensive['pool_loss_memory_scope'], 'NOT_APPLIED_AT_SEATS_EACH_LEDGER_AT_ITS_OWN_ENTRY')
        self.assertEqual(defensive['heat_veto_mode'], 'LOG_ONLY_AT_SEATS_ENFORCED_BY_EACH_ENGINE')
        # Without the pin the young pool is screened like any candidate.
        selected, report = scheduler.select(dict(state, positions=[]), now=self.NOW, max_tracked=4)
        self.assertEqual(sorted(row['symbol'] for row in selected), ['Lost', 'Safe'])
        self.assertEqual(report['defensive_entry']['blocked_pools_in_feed'], 2)
        self.assertEqual(report['policy_version'], 'STABLE_COST_AWARE_TAPE_DISCOVERY_V5_DEFENSIVE_ENTRY')
        self.assertTrue(defensive['pinned_exit_pools_exempt'])
        # The fake-cap pool sits in the 30 bps tier but never reaches the cost-first seat group.
        self.assertEqual(cost_first.fee_tier_bps(fake), 30.0)
        self.assertEqual(sorted(row['symbol'] for row in report['cost_first']['examples']), ['Lost', 'Safe'])
        self.assertFalse(hasattr(tape_scheduler, 'visible_pool_loss_index'), 'no cross-ledger loss union')

    def test_losses_in_any_ledger_never_withhold_the_seat(self):
        # Main's own history and every Lab book's history: each entry path applies its
        # own ledger's memory before quoting; the shared seat is not withheld.
        scheduler = TapePoolScheduler()
        coin = self.coin('Safe')
        warm(scheduler.defense.history, coin, self.NOW)
        book_losses = {f'BOOK_{index}': {'history': losses(coin['address'], coin['pairAddress'], self.NOW)}
                       for index in range(15)}
        state = {'feed': [coin], 'history': losses(coin['address'], coin['pairAddress'], self.NOW),
                 'strategy_lab': {'books': book_losses}}
        selected, report = scheduler.select(state, now=self.NOW, max_tracked=2)
        self.assertEqual([row['symbol'] for row in selected], ['Safe'])
        self.assertEqual(report['defensive_entry']['blocked'], 0)
        self.assertNotIn('pool_loss_cooldown_pools', report['defensive_entry'])

    def test_heat_is_log_only_at_seats(self):
        # After a tape-only restart the scheduler's own history is empty: seats are still
        # given at once (the engines enforce their own warm-up), the flag is counted.
        scheduler = TapePoolScheduler()
        coin = self.coin('Safe')
        selected, report = scheduler.select({'feed': [coin]}, now=self.NOW, max_tracked=2)
        self.assertEqual([row['symbol'] for row in selected], ['Safe'])
        self.assertEqual(report['defensive_entry']['blocked'], 0)
        self.assertEqual(report['defensive_entry']['log_only_flags'], {'heat_history_warming': 1})
        self.assertTrue(report['defensive_entry']['heat_log_only'])

    def test_a_hot_poll_never_drops_a_running_lease(self):
        # One entry seat, two pools. The seated pool flickers hot for one 2 s poll (buy
        # share and turnover above their thresholds): it keeps its lease, so its tape
        # coverage is not reset; the engines refuse the entry while it is hot.
        scheduler = TapePoolScheduler()
        first, second = self.coin('Aaaa'), self.coin('Bbbb')
        for coin in (first, second):
            warm(scheduler.defense.history, coin, self.NOW)
        selected, _ = scheduler.select({'feed': [first, second]}, now=self.NOW, max_tracked=1)
        seated = selected[0]['symbol']
        hot = {'Aaaa': first, 'Bbbb': second}[seated]
        other = second if hot is first else first
        flicker = dict(hot, txns={'m5': {'buys': 95, 'sells': 5}}, volume={'m5': 300_000.0, 'h1': 750_000.0},
                       updatedAt=self.NOW + 2_000)
        decision = entry_defense.DefensiveEntryLayer(history=scheduler.defense.history,
                                                     registry=scheduler.defense.registry).evaluate(
            flicker, self.NOW + 2_000, blocked_pools={})
        self.assertIn('heat_buy_share_5m', decision['reasons'], 'an engine would refuse this entry now')
        selected, report = scheduler.select({'feed': [flicker, dict(other, updatedAt=self.NOW + 2_000)]},
                                            now=self.NOW + 2_000, max_tracked=1)
        self.assertEqual([row['symbol'] for row in selected], [seated])
        self.assertEqual(report['defensive_entry']['blocked'], 0)
        self.assertGreaterEqual(report['defensive_entry']['log_only_flags'].get('heat_buy_share_5m', 0), 1)

    def test_live_tape_registry_sidecar_lives_next_to_the_tape_file_and_survives_a_restart(self):
        import live_tape
        self.assertEqual(live_tape._POOL_SCHEDULER.defense.registry.path, live_tape.scheduler_registry_path())
        self.assertEqual(live_tape.scheduler_registry_path().name, f'{live_tape.OUT.stem}.ticker_registry.json')
        self.assertEqual(live_tape.scheduler_registry_path().parent, live_tape.OUT.parent)
        with tempfile.TemporaryDirectory() as tmp:
            path = live_tape.scheduler_registry_path(Path(tmp) / 'live_tape.json')
            self.assertEqual(path, Path(tmp) / 'live_tape.ticker_registry.json')
            first = TapePoolScheduler(registry_path=path)
            original = self.coin('Wose', symbol='WOSE', pairCreatedAt=self.NOW - 10 * DAY)
            first.select({'feed': [original]}, now=self.NOW, max_tracked=2)
            self.assertTrue(path.exists(), 'saved on the first observation')
            # A restarted tape process remembers the ticker: a relaunch under another mint
            # is a reuse and gets no seat.
            restarted = TapePoolScheduler(registry_path=path)
            self.assertEqual(restarted.defense.registry.status()['load_status'], 'LOADED')
            self.assertEqual(len(restarted.defense.registry), 1)
            relaunch = self.coin('Xyse', symbol='WOSE', pairCreatedAt=self.NOW - 2 * DAY)
            selected, report = restarted.select({'feed': [relaunch]}, now=self.NOW + MINUTE, max_tracked=2)
            self.assertEqual(selected, [])
            self.assertEqual(report['defensive_entry']['rejections'], {'rug_ticker_reuse': 1})


# ------------------------------------------------------------------ versions and replay

class VersionAndReplayTests(unittest.TestCase):
    def test_version_strings(self):
        self.assertEqual(entry_defense.VERSION, 'DEFENSIVE_ENTRY_LAYER_V1')
        self.assertEqual(guard.VERSION, 'STRUCTURAL_RUG_GUARD_V1')
        self.assertEqual(heat_veto.VERSION, 'HEAT_VETO_STACK_V1')
        self.assertEqual(pool_loss_memory.VERSION, 'POOL_LOSS_MEMORY_V1')
        self.assertEqual(winner_ensemble.ENTRY_POLICY_VERSION, 'WINNER_ENSEMBLE_VERIFIED_ENTRY_V5')
        self.assertEqual(winner_ensemble.LEARNING_POLICY_VERSIONS,
                         ('WINNER_ENSEMBLE_VERIFIED_ENTRY_V5', 'WINNER_ENSEMBLE_VERIFIED_ENTRY_V4'))
        self.assertEqual(oct4.ENTRY_POLICY_VERSION, 'ORDER_FLOW_BALANCED_V5')
        self.assertEqual(cfp.ENTRY_POLICY_VERSION, 'COST_FIRST_ESTABLISHED_ENTRY_V2')
        self.assertEqual(cfp.PROFILE_VERSION, 'COST_FIRST_ENGINE_PROFILE_V2_DEFENSIVE_ENTRY')
        self.assertEqual(cost_first.UNIVERSE_VERSION, 'COST_FIRST_UNIVERSE_V2_STRUCTURAL_RUG_GUARD')
        self.assertEqual(cost_first.ENTRY_POLICY_VERSION, 'COST_FIRST_ESTABLISHED_V2')
        self.assertEqual(lab.activity.POLICY_VERSION, 'LAB_ACTIVE_V7_DEFENSIVE_ENTRY')
        self.assertEqual(promoted_guard.FUNDED_POLICY_VERSION, 'PROMOTED_MARKET_BRANCHES_EVIDENCE_COST_V5')
        self.assertEqual(tape_scheduler.POLICY_VERSION, 'STABLE_COST_AWARE_TAPE_DISCOVERY_V5_DEFENSIVE_ENTRY')
        self.assertEqual(m.SCORE_VERSION, 'NEO_MARKET_SCORE_V2_LIQ_MC_BAND')

    def test_v4_learning_evidence_still_holds_a_throttled_rule(self):
        history = [{'id': f't{index}', 'closed_at': 1_800_000_000_000 - index, 'pnl_usd': -2.0,
                    'entry_policy_version': 'WINNER_ENSEMBLE_VERIFIED_ENTRY_V4',
                    'strategy_matches_at_entry': ['COST_EFFICIENT_FLOW']} for index in range(12)]
        snapshot = winner_ensemble.learning_snapshot(history)
        self.assertIn('COST_EFFICIENT_FLOW', snapshot['throttled_strategies'])

    def test_every_strategy_hash_includes_the_layer(self):
        for strategy in m.SUPPORTED_SIGNAL_STRATEGIES:
            with self.subTest(strategy=strategy):
                m.activate_strategy(strategy)
                current = m.effective_config_hash()
                with patch.object(guard, 'VERSION', 'STRUCTURAL_RUG_GUARD_PROBE'):
                    self.assertNotEqual(m.effective_config_hash(), current)
        m.activate_strategy(m.DEFAULT_SIGNAL_STRATEGY)
        config = m.STATE.snapshot()['config']
        self.assertEqual(config['defensive_entry'], entry_defense.VERSIONS)
        self.assertEqual(config['score_version'], m.SCORE_VERSION)

    def test_replay_labels_the_recorded_policy_and_refuses_unknown_modes(self):
        from main_replay import MainReplay
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                MainReplay(Path(tmp) / 'bad', entry_defense='off')
            with MainReplay(Path(tmp) / 'recorded') as replay:
                decision = replay.monitor.defensive_entry_decision(established(1), 1)
                self.assertTrue(decision['allowed'])
                self.assertEqual(decision['mode'], 'REPLAY_RECORDED_POLICY_PREDATES_DEFENSIVE_ENTRY_LAYER_V1')
                result = replay.replay([])
            self.assertEqual(result['entry_defense']['mode'], 'recorded_policy')
            self.assertFalse(result['entry_defense']['applied'])
            with MainReplay(Path(tmp) / 'apply', entry_defense='apply') as replay:
                self.assertFalse(replay.monitor.defensive_entry_decision(established(1), 1)['allowed'])
                result = replay.replay([])
            self.assertEqual(result['entry_defense']['label'], 'COUNTERFACTUAL_LAYER_ON_REPLAYED_ROWS_ONLY')


if __name__ == '__main__':
    unittest.main()
