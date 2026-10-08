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
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

_TEMP = tempfile.TemporaryDirectory(prefix='neo-defensive-entry-tests-')
# Always temporary (never setdefault): EngineHarness deletes and rewrites the
# engine state file, so a shell that points NEO_MARKET_STATE_PATH at a real
# PAPER account must never reach it. setUpModule also pins the module globals
# of engines imported earlier in a discover run to these paths.
_PATHS = (('NEO_MARKET_STATE_PATH', 'state.json'), ('NEO_MARKET_AUDIT_PATH', 'audit.jsonl'),
          ('NEO_LIVE_TAPE_PATH', 'tape.json'), ('NEO_STRATEGY_LAB_PATH', 'strategy_lab.json'),
          ('NEO_STRATEGY_LAB_COMPACT_PATH', 'strategy_lab_compact.json'),
          ('NEO_STRATEGY_LAB_RESET_FLAG', 'strategy_lab.reset'), ('NEO_RISK_CACHE_DIR', 'risk'),
          ('NEO_PRICE_CHECK_DIR', 'price-check'), ('NEO_TRAINING_ROOT', 'training'),
          ('NEO_TAPE_DB_PATH', 'tape.sqlite3'))
for _name, _file in _PATHS:
    os.environ[_name] = str(Path(_TEMP.name) / _file)
for _key in ('NEO_MAIN_MARKET_STATE_PATH', 'NEO_SIGNAL_STRATEGY', 'NEO_TRADE_NOTIONAL_USD', 'NEO_MAX_DAILY_LOSS_USD',
             'NEO_STRICT_ENTRY_SCORE',
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
import paper_training as training
import pool_loss_memory
import promoted_entry_guard as promoted_guard
import strategy_lab as lab
import structural_rug_guard as guard
import tape_pool_scheduler as tape_scheduler
import winner_ensemble
from tape_pool_scheduler import TapePoolScheduler

_MODULE_PATCHES = []


def setUpModule():
    """Pin the ledger paths of engines imported earlier in this process (a discover
    run may import market_monitor or strategy_lab first) to this module's temp dir."""
    for target, name, file in ((m, 'STATE_PATH', 'state.json'), (m, 'AUDIT_PATH', 'audit.jsonl'),
                               (lab, 'STATE_PATH', 'strategy_lab.json'),
                               (lab, 'COMPACT_PATH', 'strategy_lab_compact.json'),
                               (lab, 'RESET_FLAG_PATH', 'strategy_lab.reset')):
        pinned = patch.object(target, name, Path(_TEMP.name) / file)
        pinned.start()
        _MODULE_PATCHES.append(pinned)


def tearDownModule():
    while _MODULE_PATCHES:
        _MODULE_PATCHES.pop().stop()


def assert_temporary(path):
    """Fail before any write or delete outside this module's temporary directory."""
    if Path(_TEMP.name).resolve() not in Path(path).resolve().parents:
        raise AssertionError(f'refusing to touch a non-test path: {path}')


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


def cover(registry, since, until, step=30 * MINUTE):
    """Record continuous market observations from ``since`` to ``until`` (coverage only, no sightings)."""
    stamp = since
    while stamp < until:
        registry.mark_observed(stamp)
        stamp += step
    registry.mark_observed(until)
    return registry


def covered_registry(now, hours=48):
    """An empty registry that has watched the market for ``hours`` without a gap up to ``now``."""
    return cover(guard.TickerRegistry(), now - hours * 60 * MINUTE, now)


def research_registry(until=None):
    """Ticker registry holding every research pool of the fixture tickers at its first sighting.

    It is treated as covered from 25 h before the scan log to ``until`` (the
    log's end when None): these tests check the rules on real sightings; the
    24-hour coverage rule (rug_ticker_registry_warming) has its own tests. A
    check at an observation time must use a registry covered up to that time:
    coverage stamped more than 5 min after the decision clock never vouches
    (TICKER_COVERAGE_CLOCK_V1)."""
    registry = guard.TickerRegistry()
    for row in FIXTURES['ticker_universe']:
        registry.observe_coin({'address': row['mint'], 'pairAddress': row['pair'], 'symbol': row['symbol']},
                              row['first_seen'])
    first = min(row['first_seen'] for row in FIXTURES['ticker_universe'])
    last = max(row['observed_at'] for row in FIXTURES['fixtures'].values()) if until is None else until
    return cover(registry, first - 25 * 60 * MINUTE, last)


def research_check(coin, observed_at, **kwargs):
    """guard.check of one research observation against the research registry as of its time."""
    return guard.check(coin, observed_at, research_registry(until=observed_at), **kwargs)


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


def warm(history, coin, now, *, seconds=960, step=100, price=None):
    """Feed contiguous observations of ``coin`` over the last ``seconds`` (gaps <= the 120 s limit).

    The default 16 minutes covers the 5-minute return and the 15-minute crash
    window; a pool at a fee tier >= 100 bps also needs 60 minutes (paid-profile
    lookback) unless it is already known paid."""
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
        # Just above the 2% fake-cap line ($20.2M / $20.3M, 97 h / 135 h): only the
        # ticker-reuse rule blocks these family pools, so it needs a non-empty registry.
        'USDF_EpugLBw1': ['rug_ticker_reuse'], 'DOTF_5GaCcKLd': ['rug_ticker_reuse'],
        'ANSEM_FnzKY6x7': [], 'CATE_HMzvsEEm': [], 'TROLL_4w2cysot': [], 'neet_5wNu5Qhd': [],
    }

    def setUp(self):
        self.registry = research_registry()

    def test_every_fixture_family_gets_the_expected_reasons_in_order(self):
        self.assertEqual(set(self.EXPECTED), set(FIXTURES['fixtures']))
        for name, expected in self.EXPECTED.items():
            with self.subTest(name=name):
                coin, observed_at = fixture(name)
                result = research_check(coin, observed_at)
                self.assertEqual(result['reasons'], expected)
                self.assertEqual(result['blocked'], bool(expected))
                self.assertEqual(result['version'], 'STRUCTURAL_RUG_GUARD_V1')
                for key in ('version', 'blocked', 'reasons', 'liq_mcap', 'age_min', 'mcap'):
                    self.assertIn(key, result)

    def test_established_pools_pass_with_their_real_structure(self):
        for name in ('ANSEM_FnzKY6x7', 'CATE_HMzvsEEm', 'TROLL_4w2cysot', 'neet_5wNu5Qhd'):
            with self.subTest(name=name):
                coin, observed_at = fixture(name)
                result = research_check(coin, observed_at)
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
            result = research_check(coin, observed_at)
            self.assertGreaterEqual(result['mcap'], 20_000_000, name)
            self.assertLess(result['liq_mcap'], 0.01, name)
            self.assertLess(result['age_min'], 14 * 1440, name)

    def test_two_percent_threshold_catches_daws_like_pools_that_one_percent_misses(self):
        one_percent = guard.GuardParameters(fake_mcap_max_liq_to_mcap=0.01)
        for name in ('DAWS_BTSpnpim', 'GOIF_8ZMkMgWM'):
            with self.subTest(name=name):
                coin, observed_at = fixture(name)
                self.assertIn('rug_fake_market_cap', research_check(coin, observed_at)['reasons'])
                self.assertNotIn('rug_fake_market_cap',
                                 research_check(coin, observed_at, params=one_percent)['reasons'])
        self.assertEqual(guard.PARAMS.fake_mcap_max_liq_to_mcap, 0.02)

    def test_lp_pullable_and_young_pool_values(self):
        coin, observed_at = fixture('SharkTank_CRGN2uGj')
        result = research_check(coin, observed_at)
        self.assertGreaterEqual(result['liq_mcap'], 1.0)
        self.assertGreaterEqual(result['ticker_reused_by'], 30)
        coin, observed_at = fixture('OGTRUMP_9rSmRH9w')
        result = research_check(coin, observed_at)
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
                 {'pairCreatedAt': 'x'}, {'address': ''},
                 {'pairAddress': None}, {'liquidityUsd': True}]
        for change in cases:
            with self.subTest(change=change):
                result = guard.check(self.coin(**change), self.NOW, registry)
                self.assertTrue(result['blocked'])
                self.assertEqual(result['reasons'], ['rug_input_unknown'])
        # A pool under 14 days needs its ticker (rules 5 and 6); the feed's placeholder
        # for a missing symbol is a missing ticker.
        for symbol in (None, '\U0001f438\U0001f438', 'TOKEN', 'Token!', '$', ''):
            with self.subTest(symbol=symbol):
                result = guard.check(self.coin(symbol=symbol, pairCreatedAt=self.NOW - 3 * DAY), self.NOW, registry)
                self.assertEqual(result['reasons'], ['rug_input_unknown'])
        # A missing ticker registry is an unknown input too.
        self.assertEqual(guard.check(self.coin(), self.NOW, None)['reasons'], ['rug_input_unknown'])
        self.assertFalse(guard.check(self.coin(), self.NOW, registry)['blocked'])

    def test_an_established_pool_is_judged_without_a_usable_ticker(self):
        # The reviewed case: a 30-day-old pool ($3M liquidity, $31M market cap) on a covered
        # registry passed as 'TROLL' and was rug_input_unknown as 'TOKEN', an emoji, '$' or None.
        registry = covered_registry(self.NOW)
        sane = dict(liquidityUsd=3_000_000, marketCap=31_000_000, pairCreatedAt=self.NOW - 30 * DAY)
        self.assertEqual(guard.check(self.coin(symbol='TROLL', **sane), self.NOW, registry)['reasons'], [])
        for symbol in ('TOKEN', 'Token', '\U0001f438', '$', None, ''):
            with self.subTest(symbol=symbol):
                result = guard.check(self.coin(symbol=symbol, **sane), self.NOW, registry)
                self.assertEqual((result['reasons'], result['blocked'], result['ticker']), ([], False, None))
                # Rules 2-4 still apply without a ticker.
                lp = guard.check(self.coin(symbol=symbol, **dict(sane, liquidityUsd=31_000_000)), self.NOW, registry)
                self.assertEqual(lp['reasons'], ['rug_lp_pullable'])
        # Boundary: from exactly 14 days no ticker is read; one millisecond younger needs one.
        self.assertEqual(guard.check(self.coin(symbol='TOKEN', pairCreatedAt=self.NOW - 14 * DAY), self.NOW,
                                     registry)['reasons'], [])
        self.assertEqual(guard.check(self.coin(symbol='TOKEN', pairCreatedAt=self.NOW - 14 * DAY + 1), self.NOW,
                                     registry)['reasons'], ['rug_input_unknown'])
        # Placeholders and empty tickers are still never registered.
        self.assertFalse(registry.observe_coin(self.coin(symbol='TOKEN', **sane), self.NOW))
        self.assertFalse(registry.observe_coin(self.coin(symbol='\U0001f438', **sane), self.NOW))
        self.assertEqual(len(registry), 0)
        self.assertIn('pair age < 14 days', guard.config()['ticker_input_rule'])

    def test_market_cap_falls_back_to_fdv_and_liquidity_to_the_dex_shape(self):
        registry = guard.TickerRegistry()
        result = guard.check(self.coin(marketCap=0, fdv=400_000), self.NOW, registry)
        self.assertEqual(result['mcap'], 400_000)
        self.assertEqual(result['reasons'], ['rug_lp_pullable'])
        result = guard.check(self.coin(liquidityUsd=None, liquidity={'usd': 250_000}), self.NOW, registry)
        self.assertEqual(result['liquidity_usd'], 250_000)
        self.assertFalse(result['blocked'])

    def test_boundaries(self):
        registry = covered_registry(self.NOW)
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
        registry = covered_registry(self.NOW)
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
        self.assertEqual(data['version'], 'TICKER_REGISTRY_V2_COVERAGE')
        self.assertEqual(data['entries'], [[OTHER_MINT, OTHER_PAIR, 'wose', self.NOW - DAY, self.NOW - DAY]])
        self.assertEqual((data['covered_since'], data['observed_until']), (self.NOW - DAY, self.NOW - DAY))
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
                for stale in self.path.parent.glob('*.corrupt-*'):
                    stale.unlink()
                registry = self.registry()
                self.assertEqual(registry.load_status, 'CORRUPT_STARTED_EMPTY')
                self.assertEqual(len(registry), 0)
                # The corrupt bytes are kept in a copy no service reads before a save replaces them.
                backup = self.path.with_name(registry.status()['sidecar_read']['corrupt_backup'])
                self.assertEqual(backup.name, f'{self.path.name}.corrupt-{self.NOW}')
                self.assertEqual(backup.read_text(encoding='utf-8'), content)
                registry.observe([self.coin(MINT, PAIR, 'X')], self.NOW)
                self.assertEqual(json.loads(self.path.read_text(encoding='utf-8'))['version'],
                                 'TICKER_REGISTRY_V2_COVERAGE')

    def own_sidecar_with_memory(self, *, pools=40, hours=48):
        """Main's sidecar: ``pools`` sightings and ``hours`` of coverage up to 2 minutes ago."""
        main = self.registry()
        for index in range(pools):
            main.observe_coin(self.coin(f'M{index:03d}' + 'A' * 40, f'P{index:03d}' + 'B' * 40, f'OWN{index}'),
                              self.NOW - 10 * 60 * MINUTE)
        cover(main, self.NOW - hours * 60 * MINUTE, self.NOW - 2 * MINUTE)
        self.assertTrue(main.flush())
        return self.path.read_bytes()

    def lab_sidecar_current(self, hours=30):
        """A sibling (the Lab) whose own coverage is current: one sighting, ``hours`` of coverage."""
        path = Path(self.tmp.name) / 'strategy_lab.ticker_registry.json'
        lab = guard.TickerRegistry(path, clock=lambda: self.clock)
        lab.observe_coin(self.coin('L' * 44, 'K' * 44, 'LABT'), self.NOW - 60 * MINUTE)
        cover(lab, self.NOW - hours * 60 * MINUTE, self.NOW - MINUTE)
        self.assertTrue(lab.flush())
        return path

    def test_an_unreadable_own_sidecar_is_never_overwritten_and_merged_once_readable(self):
        # Review finding: an I/O error (a Windows sharing violation) was treated as corruption,
        # the intact sidecar was replaced at the first observe and the registry reported the
        # sibling's coverage. Now it is kept, no coverage is adopted until it is read, and it is
        # merged when a later read succeeds.
        own = self.own_sidecar_with_memory()
        lab_path = self.lab_sidecar_current()
        original = Path.read_text
        blocked = {'on': True}

        def flaky(path, *args, **kwargs):
            if blocked['on'] and Path(path) == self.path:
                raise PermissionError(32, 'The process cannot access the file')
            return original(path, *args, **kwargs)

        with patch.object(Path, 'read_text', flaky):
            registry = self.registry(seed_paths=(lab_path,))
            status = registry.status()
            self.assertEqual(status['load_status'], 'UNREADABLE_NOT_OVERWRITTEN')
            self.assertTrue(status['sidecar_read']['kept_not_overwritten'])
            self.assertEqual(status['seed']['sources'], {'strategy_lab.ticker_registry.json': 'SEEDED'})
            self.assertEqual(len(registry), 1, 'sibling sightings still merge')
            self.assertIsNone(status['coverage']['adopted_from'], "no sibling coverage while its own is unknown")
            self.assertTrue(status['coverage']['warming'])
            relaunch = {'address': MINT, 'pairAddress': PAIR, 'symbol': 'FRESH', 'liquidityUsd': 400_000,
                        'marketCap': 4_000_000, 'pairCreatedAt': self.NOW - 3 * DAY}
            self.assertEqual(guard.check(relaunch, self.NOW, registry)['reasons'], ['rug_ticker_registry_warming'])
            registry.observe([self.coin('N' * 44, 'O' * 44, 'NEWT')], self.NOW)
            self.assertEqual(self.path.read_bytes(), own, 'the intact sidecar is never overwritten')
            self.assertEqual(registry.status()['save_error'], 'OWN_SIDECAR_KEPT_UNREAD')
            self.assertEqual(registry.status()['sidecar_read']['reread_attempts'], 1)
            # A clean-stop flush reads it once more and, still unreadable, leaves it untouched.
            self.assertFalse(registry.flush())
            self.assertEqual(registry.status()['sidecar_read']['reread_attempts'], 2)
            self.assertEqual(self.path.read_bytes(), own)
            # Still unreadable 30 s later: no re-read yet (60 s interval); after 60 s it is tried again.
            registry.observe([], self.NOW + 30_000)
            self.assertEqual(registry.status()['sidecar_read']['reread_attempts'], 2)
            registry.observe([self.coin('N' * 44, 'O' * 44, 'NEWT')], self.NOW + 61_000)
            self.assertEqual(registry.status()['sidecar_read']['reread_attempts'], 3)
            self.assertEqual(self.path.read_bytes(), own)
            # The sharing violation clears: the next re-read merges its memory and coverage.
            blocked['on'] = False
            self.clock = self.NOW + 122_000
            registry.observe([self.coin('N' * 44, 'O' * 44, 'NEWT')], self.NOW + 122_000)
        status = registry.status()
        self.assertEqual(status['load_status'], 'LOADED_AFTER_REREAD')
        self.assertFalse(status['sidecar_read']['kept_not_overwritten'])
        self.assertEqual(len(registry), 42, 'own 40 + the Lab sighting + the new pool')
        self.assertEqual(status['coverage']['covered_since'], self.NOW - 48 * 60 * MINUTE)
        self.assertFalse(status['coverage']['warming'])
        saved = json.loads(self.path.read_text(encoding='utf-8'))
        self.assertEqual(len(saved['entries']), 42, 'saved only after the merge, nothing lost')
        self.assertEqual(saved['covered_since'], self.NOW - 48 * 60 * MINUTE)

    def test_a_transient_read_error_is_retried_and_loads_normally(self):
        own = self.own_sidecar_with_memory(pools=3)
        original = Path.read_text
        failures = {'left': 1}

        def once(path, *args, **kwargs):
            if Path(path) == self.path and failures['left']:
                failures['left'] -= 1
                raise PermissionError(32, 'The process cannot access the file')
            return original(path, *args, **kwargs)

        with patch.object(Path, 'read_text', once):
            registry = self.registry()
        self.assertEqual((registry.load_status, len(registry)), ('LOADED', 3))
        self.assertGreater(registry.coverage_ms(self.NOW), 24 * 60 * MINUTE)
        self.assertEqual(self.path.read_bytes(), own)

    def test_a_clean_stop_merges_a_kept_sidecar_that_became_readable(self):
        self.own_sidecar_with_memory(pools=5)
        original = Path.read_text

        def locked(path, *args, **kwargs):
            if Path(path) == self.path:
                raise PermissionError(13, 'Access is denied')
            return original(path, *args, **kwargs)

        with patch.object(Path, 'read_text', locked):
            registry = self.registry()
            registry.observe([self.coin('N' * 44, 'O' * 44, 'NEWT')], self.NOW)
        self.assertEqual(registry.load_status, 'UNREADABLE_NOT_OVERWRITTEN')
        self.assertTrue(registry.flush(), 'readable again at the stop: merged, then saved')
        self.assertEqual(registry.load_status, 'LOADED_AFTER_REREAD')
        self.assertEqual(len(json.loads(self.path.read_text(encoding='utf-8'))['entries']), 6)

    def test_a_corrupt_sidecar_whose_copy_fails_is_kept_until_it_can_be_copied(self):
        self.path.write_text('{corrupt', encoding='utf-8')
        with patch.object(guard.shutil, 'copyfile', side_effect=OSError('disk')):
            registry = self.registry()
            self.assertEqual(registry.load_status, 'CORRUPT_NOT_OVERWRITTEN')
            registry.observe([self.coin(MINT, PAIR, 'X')], self.NOW)
            self.assertEqual(self.path.read_text(encoding='utf-8'), '{corrupt')
        registry.observe([self.coin(MINT, PAIR, 'X')], self.NOW + 61_000)
        status = registry.status()
        self.assertEqual(status['load_status'], 'CORRUPT_STARTED_EMPTY')
        self.assertEqual(self.path.with_name(status['sidecar_read']['corrupt_backup']).read_text(encoding='utf-8'),
                         '{corrupt')
        self.assertEqual(json.loads(self.path.read_text(encoding='utf-8'))['entries'][0][2], 'x')

    def test_a_running_registry_seeds_again_when_a_gap_restarts_its_coverage(self):
        # Review finding: a tape whose polls stalled for over 60 min while main kept scanning
        # restarted its coverage and withheld every seat of a pool under 14 days for 24 h,
        # although main's registry vouched; only a tape restart cleared it.
        main_sidecar = Path(self.tmp.name) / 'state.ticker_registry.json'
        tape_sidecar = Path(self.tmp.name) / 'live_tape.ticker_registry.json'
        self.clock = self.NOW
        scheduler = TapePoolScheduler(registry_path=tape_sidecar, registry_seed_paths=(main_sidecar,))
        registry = scheduler.defense.registry
        cover(registry, self.NOW - 30 * 60 * MINUTE, self.NOW - 70 * MINUTE)     # stalled for 70 min
        main = guard.TickerRegistry(main_sidecar, clock=lambda: self.clock)
        main.observe_coin(self.coin(OTHER_MINT, OTHER_PAIR, 'DOTF'), self.NOW - 20 * 60 * MINUTE)
        cover(main, self.NOW - 30 * 60 * MINUTE, self.NOW - MINUTE)
        self.assertTrue(main.flush())
        pool = established(self.NOW, address='C' * 44, pairAddress='H' * 44, symbol='CALM',
                           pairCreatedAt=self.NOW - 3 * DAY, dexId='pumpswap')
        relaunch = established(self.NOW, address='J' * 44, pairAddress='K' * 44, symbol='DOTF',
                               pairCreatedAt=self.NOW - 2 * DAY, dexId='pumpswap')
        selected, report = scheduler.select({'feed': [pool, relaunch]}, now=self.NOW, max_tracked=2)
        status = registry.status()
        self.assertEqual(status['seed']['running_reseeds'], 1)
        coverage = registry.coverage_status(self.NOW)       # the scheduler's registry runs on the wall clock
        self.assertEqual((coverage['resets'], coverage['adopted_from']), (1, 'state.ticker_registry.json'))
        self.assertEqual(coverage['covered_since'], self.NOW - 30 * 60 * MINUTE)
        self.assertFalse(coverage['warming'])
        self.assertNotIn('rug_ticker_registry_warming', report['defensive_entry']['rejections'])
        self.assertEqual(report['defensive_entry']['rejections'].get('rug_ticker_reuse'), 1,
                         "main's sightings arrived with the seed")
        self.assertIn('CALM', [row['symbol'] for row in selected])

    def test_a_warming_running_registry_seeds_at_most_every_5_minutes(self):
        # Started before any sibling vouched; the Lab's sidecar starts vouching later.
        lab_path = Path(self.tmp.name) / 'strategy_lab.ticker_registry.json'
        registry = self.registry(seed_paths=(lab_path,))
        self.assertEqual(registry.status()['seed']['sources'], {'strategy_lab.ticker_registry.json': 'MISSING'})
        feed = [dict(self.coin(MINT, PAIR, 'MAIN'), updatedAt=self.NOW)]
        registry.observe(feed, self.NOW)
        self.assertTrue(registry.status()['coverage']['warming'])
        self.lab_sidecar_current()
        later = self.NOW + 2 * MINUTE
        registry.observe([dict(feed[0], updatedAt=later)], later)
        self.assertEqual(registry.status()['seed']['running_reseeds'], 0, 'not before 5 minutes')
        self.assertTrue(registry.status()['coverage']['warming'])
        later = self.NOW + 5 * MINUTE
        registry.observe([dict(feed[0], updatedAt=later)], later)
        status = registry.status()
        self.assertEqual(status['seed']['running_reseeds'], 1)
        self.assertEqual(status['seed']['sources'], {'strategy_lab.ticker_registry.json': 'SEEDED'})
        self.assertEqual(status['coverage']['adopted_from'], 'strategy_lab.ticker_registry.json')
        self.assertFalse(status['coverage']['warming'])
        # A registry that vouches never seeds again.
        registry.observe([dict(feed[0], updatedAt=later + 10 * MINUTE)], later + 10 * MINUTE)
        self.assertEqual(registry.status()['seed']['running_reseeds'], 1)

    # ---- TICKER_COVERAGE_CLOCK_V1 (review finding: a future observed_until failed open) ----
    def relaunch(self, now, mint='J' * 44, pool='K' * 44, symbol='DOTF'):
        """A 3-day-old pool just above the 2% fake-cap line: only the ticker rules judge it."""
        return established(now, address=mint, pairAddress=pool, symbol=symbol, liquidityUsd=404_000.0,
                           marketCap=20_100_000.0, pairCreatedAt=now - 3 * DAY)

    def main_sidecar(self, *, hours=30, until=None, extra=()):
        """Main's sidecar: current coverage of ``hours`` ending 1 min before the clock."""
        path = Path(self.tmp.name) / 'state.ticker_registry.json'
        until = self.clock - MINUTE if until is None else until
        main = guard.TickerRegistry(path, clock=lambda: self.clock)
        main.observe_coin(self.coin('Q' * 44, 'R' * 44, 'MAIN'), until - 2 * self.HOUR_MS)
        for mint, pool, symbol, seen in extra:
            main.observe_coin(self.coin(mint, pool, symbol), seen)
        cover(main, until - hours * self.HOUR_MS, until)
        self.assertTrue(main.flush())
        return path, main

    HOUR_MS = 60 * MINUTE

    def future_sidecar(self, path, stamp):
        """A sidecar written by a test run with a fixed future clock (covered_since = observed_until)."""
        writer = guard.TickerRegistry(path, clock=lambda: stamp)
        writer.observe([dict(self.coin('S' * 44, 'T' * 44, 'TROLL'), updatedAt=stamp)], stamp)
        writer.flush()
        data = json.loads(path.read_text(encoding='utf-8'))
        self.assertEqual((data['covered_since'], data['observed_until']), (stamp, stamp))
        return path

    def test_an_own_sidecar_stamped_in_the_future_never_vouches_and_seeds_instead(self):
        # The finding: the Lab's sidecar carried covered_since = observed_until = 1.8e12 (January
        # 2027, written by a test with a fixed clock) while main's was current with 30 h. The Lab
        # vouched through a 5 h outage (coverage 35 h, resets 0) and allowed a 3-day-old pool.
        self.clock = self.NOW - 90 * DAY
        lab_path = self.future_sidecar(Path(self.tmp.name) / 'strategy_lab.ticker_registry.json', self.NOW)
        main_path, main = self.main_sidecar()
        lab = guard.TickerRegistry(lab_path, clock=lambda: self.clock, seed_paths=(main_path,))
        status = lab.status()
        self.assertEqual(status['load_status'], 'LOADED')
        self.assertEqual(len(lab), 2, "its own sightings still load, main's are merged")
        coverage = status['coverage']
        self.assertEqual((coverage['future_drops'], coverage['last_future_ahead_minutes']),
                         (1, round(90 * DAY / MINUTE, 1)))
        self.assertEqual(coverage['clock_version'], 'TICKER_COVERAGE_CLOCK_V1')
        # Seeded from main, which honestly vouches: main's span, never the January 2027 stamp.
        self.assertEqual((coverage['adopted_from'], coverage['covered_since'], coverage['observed_until']),
                         ('state.ticker_registry.json', self.clock - MINUTE - 30 * self.HOUR_MS, self.clock - MINUTE))
        self.assertFalse(coverage['warming'])
        # A 5 h outage with no observation: the gap is seen and coverage restarts (main lapsed too).
        later = self.clock + 5 * self.HOUR_MS
        pool = self.relaunch(later)
        lab.observe([dict(self.coin('U' * 44, 'V' * 44, 'OTHR'), updatedAt=later)], later)
        coverage = lab.coverage_status(later)
        self.assertEqual((coverage['resets'], coverage['warming']), (1, True))
        self.assertGreater(coverage['last_gap_minutes'], 5 * 60)
        self.assertEqual(guard.check(pool, later, lab)['reasons'], ['rug_ticker_registry_warming'])
        lab.flush()
        saved = json.loads(lab_path.read_text(encoding='utf-8'))
        self.assertLessEqual(saved['observed_until'], later, 'the future stamp is never saved again')

    def test_coverage_stamped_ahead_of_the_clock_is_never_current_and_is_dropped_at_the_next_scan(self):
        registry = self.registry()
        cover(registry, self.NOW - 30 * self.HOUR_MS, self.NOW)
        self.assertTrue(registry.coverage_current(self.NOW - 4 * MINUTE), 'within the 5-minute tolerance')
        self.assertFalse(registry.coverage_current(self.NOW - 6 * MINUTE))
        self.assertEqual(registry.coverage_ms(self.NOW - self.HOUR_MS), 0.0)
        # A backward wall-clock step of 2 h: the next scan drops the coverage it never observed
        # and starts again (a gap can no longer hide behind the future stamp).
        back = self.NOW - 2 * self.HOUR_MS
        registry.observe([dict(self.coin(MINT, PAIR, 'MAIN'), updatedAt=back)], back)
        coverage = registry.coverage_status(back)
        self.assertEqual((coverage['covered_since'], coverage['observed_until']), (back, back))
        self.assertEqual((coverage['future_drops'], coverage['warming']), (1, True))
        self.assertEqual(guard.check(self.relaunch(back), back, registry)['reasons'], ['rug_ticker_registry_warming'])
        # Without the scan clock (the offline builder's replay) the stamps alone are compared.
        replayed = guard.TickerRegistry()
        replayed.mark_observed(self.NOW)
        replayed.mark_observed(self.NOW - self.HOUR_MS)
        self.assertEqual(replayed.coverage_status(self.NOW)['future_drops'], 0)

    def test_a_sibling_stamped_in_the_future_is_merged_but_never_adopted(self):
        self.clock = self.NOW - 90 * DAY
        tape_path = self.future_sidecar(Path(self.tmp.name) / 'live_tape.ticker_registry.json', self.NOW)
        fresh = self.registry(seed_paths=(tape_path,))
        status = fresh.status()
        self.assertEqual(status['seed']['sources'], {'live_tape.ticker_registry.json': 'SEEDED_COVERAGE_AHEAD_IGNORED'})
        self.assertEqual(len(fresh), 1, 'its sightings still count (they can only block more)')
        self.assertIsNone(status['coverage']['adopted_from'])
        self.assertTrue(status['coverage']['warming'])
        self.assertEqual(guard.check(self.relaunch(self.clock), self.clock, fresh)['reasons'],
                         ['rug_ticker_registry_warming'])

    # ---- TICKER_REGISTRY_SEED_V4 (review finding: main's later sightings never reached the Lab) ----
    def test_a_vouching_registry_merges_its_siblings_sightings_every_5_minutes(self):
        main_path, main = self.main_sidecar()
        lab = guard.TickerRegistry(Path(self.tmp.name) / 'strategy_lab.ticker_registry.json',
                                   clock=lambda: self.clock, seed_paths=(main_path,))
        self.assertEqual(lab.status()['coverage']['adopted_from'], 'state.ticker_registry.json')
        adopted = lab.coverage_status(self.clock)['covered_since']
        # Main sees a DOTF relaunch sibling among the Gecko new pools the bounded feed cuts:
        # the Lab and the tape never see that row themselves. Main's sidecar now also carries
        # a longer span (40 h) than the one the Lab adopted at its start.
        rewritten = guard.TickerRegistry(None, clock=lambda: self.clock)
        rewritten.observe_coin(self.coin(OTHER_MINT, OTHER_PAIR, 'DOTF'), self.clock)
        cover(rewritten, self.clock - 40 * self.HOUR_MS, self.clock)
        rewritten.path = main_path
        self.assertTrue(rewritten.save(self.clock))
        trimmed = [dict(self.coin('U' * 44, 'V' * 44, 'OTHR'), updatedAt=self.clock + MINUTE)]
        lab.observe(trimmed, self.clock + MINUTE)
        pool = self.relaunch(self.clock + MINUTE)
        self.assertEqual(lab.status()['seed']['sighting_merges'], 0, 'not before 5 minutes')
        self.assertEqual(guard.check(pool, self.clock + MINUTE, lab)['reasons'], [])
        later = self.clock + 5 * MINUTE
        lab.observe([dict(trimmed[0], updatedAt=later)], later)
        status = lab.status()
        self.assertEqual((status['seed']['sighting_merges'], status['seed']['sighting_merge_new_pools']), (1, 1))
        self.assertEqual(status['seed']['sighting_merge_sources'], {'state.ticker_registry.json': 'MERGED'})
        self.assertEqual(guard.check(self.relaunch(later), later, lab)['reasons'], ['rug_ticker_reuse'])
        # Sightings only: the coverage stays the Lab's own (main's earlier start is not adopted).
        self.assertEqual(lab.coverage_status(later)['covered_since'], adopted)
        self.assertEqual(status['seed']['running_reseeds'], 0)
        # At most every 5 minutes.
        lab.observe([dict(trimmed[0], updatedAt=later + 4 * MINUTE)], later + 4 * MINUTE)
        self.assertEqual(lab.status()['seed']['sighting_merges'], 1)
        lab.observe([dict(trimmed[0], updatedAt=later + 5 * MINUTE)], later + 5 * MINUTE)
        self.assertEqual(lab.status()['seed']['sighting_merges'], 2)

    def test_invalid_rows_are_skipped_and_stale_rows_pruned_on_load(self):
        rows = [[MINT, PAIR, 'good', self.NOW - DAY, self.NOW - DAY],
                [OTHER_MINT, OTHER_PAIR, 'old', self.NOW - 30 * DAY, self.NOW - 15 * DAY],
                ['', PAIR, 'bad', 1, 2], [MINT, PAIR, 'NotNormalized', 1, 2], [MINT, PAIR, 'x', 5, 1], 'junk',
                # A placeholder registered before the placeholder rule is dropped.
                ['P' * 44, 'Q' * 44, 'token', self.NOW - DAY, self.NOW - DAY]]
        # A legacy V1 sidecar (no coverage) loads its sightings; coverage starts again.
        self.path.write_text(json.dumps({'version': 'TICKER_REGISTRY_V1', 'entries': rows}), encoding='utf-8')
        registry = self.registry()
        self.assertEqual(registry.load_status, 'LOADED')
        self.assertEqual(registry.status()['loaded_version'], 'TICKER_REGISTRY_V1')
        self.assertEqual(registry.coverage_ms(self.NOW), 0.0)
        self.assertEqual(registry.load_skipped_rows, 5)
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

    def test_temporaries_abandoned_by_a_hard_stop_are_reclaimed(self):
        """Final review r2: a TerminateProcess inside save() skips its finally and leaves the mkstemp
        temporary; nothing reclaimed it. Only this sidecar's own stale temporaries go."""
        folder = self.path.parent
        registry = self.registry()
        registry.observe([self.coin(MINT, PAIR, 'KEEP')], self.NOW)
        old = time.time() - guard.SIDECAR_TEMP_STALE_SECONDS - 60

        def leave(name, *, stale=True):
            path = folder / name
            self.assertIn(Path(self.tmp.name).resolve(), path.resolve().parents)
            path.write_text('{"partial', encoding='utf-8')
            if stale:
                os.utime(path, (old, old))
            return path

        abandoned = leave(f'.{self.path.name}.ab3_x9zq.tmp')
        writing = leave(f'.{self.path.name}.kq81zz0a.tmp', stale=False)          # a save still in progress
        others = [leave('.strategy_lab.ticker_registry.json.ab3_x9zq.tmp'),     # another service's sidecar
                  leave(f'.{self.path.name}.TOOLONG123.tmp'), leave(f'{self.path.name}.ab3_x9zq.tmp'),
                  leave(f'.{self.path.name}.ab3_x9zq.bak'), leave('state.json'), leave('observations.jsonl')]
        (folder / f'.{self.path.name}.dir12345.tmp').mkdir()
        restarted = self.registry()
        self.assertFalse(abandoned.exists(), 'reclaimed when the registry starts')
        self.assertTrue(writing.exists(), 'a temporary younger than 10 min may belong to a live write')
        self.assertTrue(all(path.exists() for path in others))
        self.assertTrue((folder / f'.{self.path.name}.dir12345.tmp').is_dir())
        self.assertEqual(restarted.status()['stale_temps'],
                         {'version': guard.SIDECAR_TEMP_SWEEP_VERSION, 'removed': 1})
        self.assertEqual(restarted.status()['load_status'], 'LOADED')
        # The temporary the restart's own kill left is too young at start; a later save reclaims it
        # once it is 10 min old (at most one sweep per 10 min), and the save still succeeds.
        os.utime(writing, (old, old))
        restarted.observe_coin(self.coin(OTHER_MINT, OTHER_PAIR, 'NEW'), self.NOW + 1)
        self.assertTrue(restarted.save(self.NOW + 2))
        self.assertTrue(writing.exists(), 'the next sweep is not due yet')
        restarted._next_temp_sweep = 0.0
        restarted.observe_coin(self.coin(OTHER_MINT, OTHER_PAIR, 'NEWER'), self.NOW + 3)
        self.assertTrue(restarted.save(self.NOW + 4))
        self.assertFalse(writing.exists())
        self.assertEqual(restarted.status()['stale_temps']['removed'], 2)
        self.assertIn('newer', self.path.read_text(encoding='utf-8'))
        self.assertTrue(all(path.exists() for path in others))
        # A missing folder or an unlink that fails never raises.
        self.assertEqual(guard.remove_stale_sidecar_temps(folder / 'missing' / self.path.name), [])
        stale = leave(f'.{self.path.name}.zzzzzzzz.tmp')
        with patch.object(guard.os, 'unlink', side_effect=PermissionError('held')):
            self.assertEqual(guard.remove_stale_sidecar_temps(self.path), [])
        self.assertTrue(stale.exists())
        self.assertEqual(guard.remove_stale_sidecar_temps(None), [])

    def test_saves_are_throttled(self):
        registry = self.registry(save_interval_ms=60_000)
        registry.observe([self.coin(MINT, PAIR, 'A')], self.NOW)
        registry.observe([self.coin(OTHER_MINT, OTHER_PAIR, 'B')], self.NOW + 1_000)
        self.assertEqual(registry.status()['saves'], 1)
        registry.observe([self.coin(OTHER_MINT, OTHER_PAIR, 'B')], self.NOW + 61_000)
        self.assertEqual(registry.status()['saves'], 2)

    def test_engine_sidecar_lives_next_to_the_account_state_and_survives_restart(self):
        # The engine's registry runs on the wall clock (structural_rug_guard.time) and prunes
        # on load, so the sighting is stamped by that clock (a fixed future stamp would be
        # pruned once the wall clock passes it by 14 days, review finding).
        now = int(guard.time.time() * 1000)
        state_path = Path(self.tmp.name) / 'engine' / 'state.json'
        with patch.object(m, 'STATE_PATH', state_path):
            monitor = m.Monitor()
            monitor.observe_entry_defense([established(now)], now)
            monitor.stop()
            sidecar = state_path.with_name('state.ticker_registry.json')
            self.assertTrue(sidecar.exists())
            restarted = m.Monitor()
            self.assertEqual(restarted.defense.registry.load_status, 'LOADED')
            self.assertEqual(len(restarted.defense.registry), 1)
            restarted.stop()
        self.assertEqual(entry_defense.registry_path_for(Path('x') / 'strategy_lab.json').name,
                         'strategy_lab.ticker_registry.json')

    def test_entry_bound_covers_14_days_of_research_traffic_and_publishes_its_horizon(self):
        registry = guard.TickerRegistry()
        self.assertEqual(registry.max_entries, 40_000)
        research_pairs_per_day = 2_042 / 22.8 * 24          # distinct pairs in the scan log
        self.assertGreater(registry.max_entries, 14 * research_pairs_per_day)
        status = registry.status()
        self.assertEqual((status['cap_evictions'], status['cap_limited_horizon_days']), (0, None))
        self.assertEqual(guard.config()['registry']['max_entries'], 40_000)
        # A cap that fills before the retention publishes the effective horizon, and a
        # sibling it evicted is forgotten although it was seen less than 14 days ago.
        small = guard.TickerRegistry(max_entries=20, prune_interval_ms=0)
        hour = 3_600_000
        for index in range(30):
            small.observe([self.coin(f'M{index:02d}' + 'A' * 40, f'P{index:02d}' + 'B' * 40, f'T{index}')],
                          self.NOW + index * hour)
        status = small.status()
        self.assertEqual(status['cap_evictions'], 12)
        self.assertEqual(status['cap_limited_horizon_days'], 0.75)          # 18 hours
        self.assertEqual(status['cap_evicted_at'], self.NOW + 29 * hour)
        self.assertEqual(status['oldest_last_seen_days'], round(17 / 24, 2))
        self.assertEqual(small.reusers('t0', MINT, PAIR, self.NOW + 30 * hour), [])

    def sibling_sidecars(self):
        """The Lab's sidecar knows every USDF mint, the tape's every DOTF mint (research sightings).

        The Lab has watched the market for 30 h up to 5 minutes ago (current
        coverage); the tape's last observation is 3 h old (lapsed coverage)."""
        lab_sidecar = Path(self.tmp.name) / 'strategy_lab.ticker_registry.json'
        tape_sidecar = Path(self.tmp.name) / 'live_tape.ticker_registry.json'
        for path, ticker, until in ((lab_sidecar, 'usdf', self.clock - 5 * MINUTE),
                                    (tape_sidecar, 'dotf', self.clock - 180 * MINUTE)):
            sibling = guard.TickerRegistry(path, clock=lambda: self.clock)
            for row in FIXTURES['ticker_universe']:
                if row['ticker'] == ticker:
                    sibling.observe_coin({'address': row['mint'], 'pairAddress': row['pair'],
                                          'symbol': row['symbol']}, row['first_seen'])
            cover(sibling, until - 30 * 60 * MINUTE, until)
            self.assertTrue(sibling.flush())
        return lab_sidecar, tape_sidecar

    def test_a_registry_without_current_coverage_is_seeded_read_only_from_sibling_sidecars(self):
        self.clock = 1_791_440_000_000                      # just after both research rows
        lab_sidecar, tape_sidecar = self.sibling_sidecars()
        seeds = (lab_sidecar, tape_sidecar)
        before = [path.read_bytes() for path in seeds]
        # An empty registry (the first deploy) never passes the threshold-edge family pools
        # (USDF 2.00%, DOTF 2.01% liquidity/market cap, 97 h and 135 h old): 'no other mint
        # seen' means nothing yet, so the ticker rule fails closed.
        for name in ('USDF_EpugLBw1', 'DOTF_5GaCcKLd'):
            coin, observed_at = fixture(name)
            with self.subTest(name=name):
                self.assertGreaterEqual(guard.check(coin, observed_at, guard.TickerRegistry())['liq_mcap'], 0.02)
                self.assertEqual(cost_first.rejections(coin, cap_usd=200.0, now=observed_at,
                                                       ticker_registry=guard.TickerRegistry()),
                                 ['rug_ticker_registry_warming'])
                self.assertEqual(cost_first.physical_rejections(coin, cap_usd=200.0), [])
        # A new engine registry (no sidecar yet) is seeded from both siblings and blocks them;
        # it adopts the Lab's current coverage (30 h), not the tape's lapsed one.
        fresh = self.registry(seed_paths=seeds + (self.path,))
        self.assertEqual(fresh.load_status, 'MISSING_STARTED_EMPTY')
        status = fresh.status()
        self.assertEqual(status['seed']['sources'], {'strategy_lab.ticker_registry.json': 'SEEDED',
                                                     'live_tape.ticker_registry.json': 'SEEDED'})
        self.assertEqual(status['seed']['entries'], 11)
        self.assertEqual(status['coverage']['adopted_from'], 'strategy_lab.ticker_registry.json')
        self.assertEqual(status['coverage']['covered_since'], self.clock - 5 * MINUTE - 30 * 60 * MINUTE)
        self.assertFalse(status['coverage']['warming'])
        for name in ('USDF_EpugLBw1', 'DOTF_5GaCcKLd'):
            coin, observed_at = fixture(name)
            with self.subTest(name=name):
                self.assertEqual(cost_first.rejections(coin, cap_usd=200.0, now=observed_at, ticker_registry=fresh),
                                 ['rug_ticker_reuse'])
        self.assertEqual([path.read_bytes() for path in seeds], before, 'seed files are never written')
        self.assertTrue(fresh.flush(), 'the seeded memory and coverage are saved in its own sidecar')
        # A registry whose own coverage is current after loading its sidecar is not seeded again.
        loaded = self.registry(seed_paths=seeds)
        self.assertEqual((loaded.load_status, loaded.status()['seed']['sources'], len(loaded)), ('LOADED', {}, 11))
        self.assertGreater(loaded.coverage_ms(self.clock), 24 * 60 * MINUTE)
        # Three hours later every coverage has lapsed: the siblings are merged again
        # (sightings only add), nothing is adopted and the registry warms.
        self.clock += 180 * MINUTE
        stale = self.registry(seed_paths=seeds)
        self.assertEqual(stale.status()['seed']['sources'], {'strategy_lab.ticker_registry.json': 'SEEDED',
                                                             'live_tape.ticker_registry.json': 'SEEDED'})
        self.assertEqual((len(stale), stale.coverage_ms(self.clock)), (11, 0.0))
        self.assertTrue(stale.status()['coverage']['warming'])
        # Missing or corrupt seeds start empty and never raise.
        corrupt = Path(self.tmp.name) / 'corrupt.ticker_registry.json'
        corrupt.write_text('{oops', encoding='utf-8')
        other = guard.TickerRegistry(Path(self.tmp.name) / 'other.json', clock=lambda: self.clock,
                                     seed_paths=(Path(self.tmp.name) / 'missing.json', corrupt))
        self.assertEqual(other.status()['seed'], {'version': 'TICKER_REGISTRY_SEED_V4', 'entries': 0,
                                                 'sources': {'missing.json': 'MISSING',
                                                             'corrupt.ticker_registry.json': 'CORRUPT'},
                                                 'running_reseeds': 0, 'last_seed_at': self.clock,
                                                 # A seed counts as the first sighting merge.
                                                 'sighting_merges': 0, 'sighting_merge_new_pools': 0,
                                                 'sighting_merge_sources': {},
                                                 'last_sighting_merge_at': self.clock})
        self.assertEqual(len(other), 0)
        self.assertEqual(corrupt.read_text(encoding='utf-8'), '{oops', 'a corrupt seed is never copied or written')

    def test_a_short_current_sidecar_left_before_the_seed_still_adopts_a_sibling(self):
        # The services ran 40 min before the first-deploy seed, then were stopped and main's
        # sidecar was rebuilt: the Lab's own sidecar is current but only 40 min long, so at its
        # restart it still merges main's and adopts its 30 h, instead of warming for 24 h.
        main_sidecar = Path(self.tmp.name) / 'state.ticker_registry.json'
        lab_sidecar = Path(self.tmp.name) / 'strategy_lab.ticker_registry.json'
        seeded = guard.TickerRegistry(main_sidecar, clock=lambda: self.clock)
        seeded.observe_coin(self.coin(OTHER_MINT, OTHER_PAIR, 'DOTF'), self.clock - 20 * 60 * MINUTE)
        cover(seeded, self.clock - 30 * 60 * MINUTE, self.clock - 10 * MINUTE)
        self.assertTrue(seeded.flush())
        short = guard.TickerRegistry(lab_sidecar, clock=lambda: self.clock)
        short.observe_coin(self.coin('Q' * 44, 'R' * 44, 'OWN'), self.clock - 30 * MINUTE)
        cover(short, self.clock - 45 * MINUTE, self.clock - 5 * MINUTE)
        self.assertTrue(short.flush())
        lab = guard.TickerRegistry(lab_sidecar, clock=lambda: self.clock, seed_paths=(main_sidecar,))
        status = lab.status()
        self.assertEqual(status['seed']['sources'], {'state.ticker_registry.json': 'SEEDED'})
        self.assertEqual((status['coverage']['covered_since'], status['coverage']['observed_until']),
                         (self.clock - 30 * 60 * MINUTE, self.clock - 5 * MINUTE))
        self.assertEqual(status['coverage']['adopted_from'], 'state.ticker_registry.json')
        self.assertFalse(status['coverage']['warming'])
        self.assertEqual(len(lab), 2)
        # Without a current sibling the short sidecar keeps its own span; a lapsed sibling
        # still adds its sightings.
        lab_sidecar.unlink()
        short = guard.TickerRegistry(lab_sidecar, clock=lambda: self.clock)
        cover(short, self.clock - 45 * MINUTE, self.clock - 5 * MINUTE)
        self.assertTrue(short.flush())
        lapsed = Path(self.tmp.name) / 'live_tape.ticker_registry.json'
        lapsed_registry = guard.TickerRegistry(lapsed, clock=lambda: self.clock)
        lapsed_registry.observe_coin(self.coin(OTHER_MINT, 'S' * 44, 'DOTF'), self.clock - 5 * 60 * MINUTE)
        cover(lapsed_registry, self.clock - 30 * 60 * MINUTE, self.clock - 3 * 60 * MINUTE)
        self.assertTrue(lapsed_registry.flush())
        lab = guard.TickerRegistry(lab_sidecar, clock=lambda: self.clock, seed_paths=(lapsed,))
        coverage = lab.status()['coverage']
        self.assertEqual((coverage['covered_since'], coverage['observed_until'], coverage['adopted_from']),
                         (self.clock - 45 * MINUTE, self.clock - 5 * MINUTE, None))
        self.assertTrue(coverage['warming'])
        self.assertEqual(len(lab), 1, 'the lapsed sibling still adds its sighting')
        # A sidecar that already vouches (current, >= 24 h) is not seeded.
        lab_sidecar.unlink()
        vouching = guard.TickerRegistry(lab_sidecar, clock=lambda: self.clock)
        cover(vouching, self.clock - 25 * 60 * MINUTE, self.clock)
        self.assertTrue(vouching.flush())
        loaded = guard.TickerRegistry(lab_sidecar, clock=lambda: self.clock, seed_paths=(main_sidecar, lapsed))
        self.assertEqual(loaded.status()['seed']['sources'], {})

    def test_every_service_seeds_from_the_other_services_sidecars(self):
        root = Path(self.tmp.name)
        env = {'NEO_MARKET_STATE_PATH': str(root / 'users' / 'u1' / 'state.json'),
               'NEO_STRATEGY_LAB_PATH': str(root / 'strategy_lab.json'),
               'NEO_LIVE_TAPE_PATH': str(root / 'live_tape.json')}
        engine = entry_defense.registry_path_for(env['NEO_MARKET_STATE_PATH'])
        lab_sidecar = root / 'strategy_lab.ticker_registry.json'
        tape_sidecar = root / 'live_tape.ticker_registry.json'
        self.assertEqual(entry_defense.sibling_registry_paths(engine, env), (lab_sidecar, tape_sidecar))
        self.assertEqual(entry_defense.sibling_registry_paths(tape_sidecar, env), (engine, lab_sidecar))
        self.assertEqual(entry_defense.sibling_registry_paths(engine, {}), ())
        # A personal engine also reads main's sidecar (the gateway passes main's state path);
        # main itself never lists its own sidecar twice.
        main_state = root / 'state.json'
        main_sidecar = entry_defense.registry_path_for(main_state)
        personal_env = dict(env, NEO_MAIN_MARKET_STATE_PATH=str(main_state))
        self.assertEqual(entry_defense.sibling_registry_paths(engine, personal_env),
                         (main_sidecar, lab_sidecar, tape_sidecar))
        main_env = dict(personal_env, NEO_MARKET_STATE_PATH=str(main_state))
        self.assertEqual(entry_defense.sibling_registry_paths(main_sidecar, main_env), (lab_sidecar, tape_sidecar))
        # The services wire their own sidecar and the siblings named by the environment.
        import live_tape
        monitor = m.Monitor()
        own = entry_defense.registry_path_for(m.STATE_PATH)
        self.assertEqual(monitor.defense.registry.seed_paths, entry_defense.sibling_registry_paths(own))
        monitor.stop()
        with patch.object(lab, 'DEFENSE', None):
            layer = lab.entry_defense_layer()
            own = entry_defense.registry_path_for(lab.STATE_PATH)
            self.assertEqual(layer.registry.path, own)
            self.assertEqual(layer.registry.seed_paths, entry_defense.sibling_registry_paths(own))
        # live_tape builds its scheduler at import (the environment of that moment).
        tape_registry = live_tape._POOL_SCHEDULER.defense.registry
        self.assertEqual(tape_registry.path, live_tape.scheduler_registry_path())
        self.assertTrue(tape_registry.seed_paths)
        self.assertNotIn(tape_registry.path, tape_registry.seed_paths)
        self.assertTrue(all(path.name.endswith('.ticker_registry.json') for path in tape_registry.seed_paths))
        with patch.dict(os.environ, env):
            scheduler = tape_scheduler.TapePoolScheduler(
                registry_path=tape_sidecar, registry_seed_paths=entry_defense.sibling_registry_paths(tape_sidecar))
        self.assertEqual(scheduler.defense.registry.seed_paths, (engine, lab_sidecar))


class TickerRegistryCoverageTests(unittest.TestCase):
    """rug_ticker_registry_warming: 'no other mint seen' counts only after 24 h of
    continuous observation (TICKER_REGISTRY_V2_COVERAGE)."""
    NOW = 1_800_000_000_000
    HOUR = 60 * MINUTE

    def young(self, symbol='NEWT', **changes):
        coin = {'address': MINT, 'pairAddress': PAIR, 'symbol': symbol, 'liquidityUsd': 500_000,
                'marketCap': 5_000_000, 'pairCreatedAt': self.NOW - 3 * DAY}
        coin.update(changes)
        return coin

    def test_a_relaunch_just_above_the_fake_cap_line_waits_for_24_hours_of_coverage(self):
        # The reviewed gap: with an empty registry the USDF/DOTF family pools (2.00% / 2.01%
        # liquidity/market cap) passed the guard and the cost-first universe.
        coin, at = fixture('DOTF_5GaCcKLd')
        empty = guard.TickerRegistry()
        result = guard.check(coin, at, empty)
        self.assertEqual((result['reasons'], result['registry_coverage_h']), (['rug_ticker_registry_warming'], 0.0))
        self.assertEqual(cost_first.rejections(coin, cap_usd=200.0, now=at, ticker_registry=empty),
                         ['rug_ticker_registry_warming'])
        short = cover(guard.TickerRegistry(), at - 24 * self.HOUR + MINUTE, at)
        self.assertEqual(guard.check(coin, at, short)['reasons'], ['rug_ticker_registry_warming'])
        self.assertEqual(guard.check(coin, at, short)['registry_coverage_h'], 23.98)
        full = cover(guard.TickerRegistry(), at - 24 * self.HOUR, at)
        self.assertEqual(guard.check(coin, at, full)['reasons'], [], 'no sibling seen in 24 h of watching')
        # A sibling already seen is a reuse whatever the coverage; an established pool
        # (>= 14 days) is never judged by tickers, so it never waits.
        empty.observe_coin({'address': OTHER_MINT, 'pairAddress': OTHER_PAIR, 'symbol': 'DOTF'}, at - DAY)
        self.assertEqual(guard.check(coin, at, empty)['reasons'], ['rug_ticker_reuse'])
        ansem, ansem_at = fixture('ANSEM_FnzKY6x7')
        self.assertEqual(guard.check(ansem, ansem_at, guard.TickerRegistry())['reasons'], [])
        config = guard.config()
        self.assertEqual(config['reasons_in_order'][-1], 'rug_ticker_registry_warming')
        self.assertEqual(config['parameters']['ticker_registry_min_coverage_minutes'], 1440.0)
        self.assertEqual(config['registry']['coverage']['max_gap_minutes'], 60)
        self.assertEqual(config['registry_version'], 'TICKER_REGISTRY_V2_COVERAGE')

    def test_a_gap_longer_than_60_minutes_restarts_coverage(self):
        registry = cover(guard.TickerRegistry(), self.NOW - 30 * self.HOUR, self.NOW - 2 * self.HOUR)
        registry.mark_observed(self.NOW - self.HOUR)                              # exactly 60 min: kept
        self.assertEqual(registry.coverage_ms(self.NOW - self.HOUR), 29 * self.HOUR)
        registry.mark_observed(self.NOW + MINUTE)                                 # 61 min later
        coverage = registry.coverage_status(self.NOW + MINUTE)
        self.assertEqual((coverage['covered_since'], coverage['resets'], coverage['last_gap_minutes']),
                         (self.NOW + MINUTE, 1, 61.0))
        self.assertEqual(registry.coverage_ms(self.NOW + MINUTE), 0.0)
        self.assertEqual(guard.check(self.young(), self.NOW + MINUTE, registry)['reasons'],
                         ['rug_ticker_registry_warming'])

    def test_coverage_lapses_when_the_registry_stops_observing(self):
        registry = cover(guard.TickerRegistry(), self.NOW - 30 * self.HOUR, self.NOW)
        self.assertEqual(guard.check(self.young(), self.NOW + 60 * MINUTE, registry)['reasons'], [])
        self.assertEqual(registry.coverage_ms(self.NOW + 61 * MINUTE), 0.0)
        self.assertEqual(guard.check(self.young(), self.NOW + 61 * MINUTE, registry)['reasons'],
                         ['rug_ticker_registry_warming'])

    def test_a_stale_published_feed_or_an_empty_scan_never_extends_coverage(self):
        registry = guard.TickerRegistry()
        stale = dict(self.young(), updatedAt=self.NOW - 2 * self.HOUR)
        for minutes in (0, 30, 60, 90):
            registry.observe([stale], self.NOW + minutes * MINUTE)
        self.assertEqual(registry.coverage_status(self.NOW)['observed_until'], self.NOW - 2 * self.HOUR)
        self.assertEqual(registry.coverage_ms(self.NOW + 90 * MINUTE), 0.0)
        registry.observe([], self.NOW + 95 * MINUTE)
        self.assertEqual(registry.coverage_status(self.NOW)['observed_until'], self.NOW - 2 * self.HOUR)
        # A fresh observation starts coverage again (the gap exceeded 60 min).
        registry.observe([dict(stale, updatedAt=self.NOW + 100 * MINUTE)], self.NOW + 100 * MINUTE)
        self.assertEqual(registry.coverage_status(self.NOW)['covered_since'], self.NOW + 100 * MINUTE)
        # A stamp more than 5 s after the scan clock is skew: the scan clock counts.
        self.assertEqual(guard.observation_time({'updatedAt': self.NOW + 6_000}, self.NOW), self.NOW)
        self.assertEqual(guard.observation_time({'updatedAt': self.NOW + 4_000}, self.NOW), self.NOW)
        self.assertEqual(guard.observation_time({}, self.NOW), self.NOW)

    def test_a_held_only_feed_does_not_extend_coverage(self):
        # The reviewed probe: discovery down, the pairs endpoint up, one position open. A
        # registry that saw only the held coin every minute for 30 h reported 30 h of coverage.
        registry = guard.TickerRegistry()
        held = dict(self.young(symbol='HELD'), sources=['open-position'])
        pinned = dict(held, sources=['open-position-pinned-pair'])
        start = self.NOW - 30 * self.HOUR
        scans = 0
        for minute in range(0, 30 * 60 + 1, 5):
            stamp = start + minute * MINUTE
            registry.observe([dict(held, updatedAt=stamp), dict(pinned, updatedAt=stamp)], stamp)
            scans += 1
        coverage = registry.coverage_status(self.NOW)
        self.assertEqual((coverage['covered_since'], coverage['observed_until'], coverage['coverage_hours']),
                         (None, None, 0.0))
        self.assertTrue(coverage['warming'])
        self.assertEqual((coverage['held_only_scans'], coverage['basis']), (scans, 'DISCOVERED_MARKET_ROWS_V2'))
        # The held coin is still a real sighting.
        self.assertEqual(len(registry), 1)
        sibling = self.young(symbol='SIBL', address=OTHER_MINT, pairAddress=OTHER_PAIR)
        self.assertEqual(guard.check(sibling, self.NOW, registry)['reasons'], ['rug_ticker_registry_warming'])
        # A discovered row marks coverage at its own time; held rows in the same scan never set it.
        market = dict(self.young(symbol='MKT', address='H' * 44, pairAddress='J' * 44),
                      sources=['latest'], updatedAt=self.NOW - MINUTE)
        registry.observe([market, dict(held, updatedAt=self.NOW)], self.NOW)
        self.assertEqual(registry.coverage_status(self.NOW)['observed_until'], self.NOW - MINUTE)
        # A held coin that discovery also returned carries a discovery source and counts.
        registry.observe([dict(held, sources=['open-position', 'gecko-new-pools'], updatedAt=self.NOW + MINUTE)],
                         self.NOW + MINUTE)
        self.assertEqual(registry.coverage_status(self.NOW)['observed_until'], self.NOW + MINUTE)
        # The Lab and the tape observe through the layer with the same rule.
        layer = entry_defense.DefensiveEntryLayer(registry=guard.TickerRegistry())
        for minute in range(0, 120, 5):
            layer.observe([dict(held, updatedAt=start + minute * MINUTE)], start + minute * MINUTE)
        self.assertIsNone(layer.status()['ticker_registry']['coverage']['observed_until'])
        self.assertEqual(layer.status()['ticker_registry']['coverage']['held_only_scans'], 24)
        # Rows without a source list (no production row) count as market rows.
        for sources, expected in ((None, True), ([], True), (['latest'], True), ('open-position', False),
                                  (['open-position'], False), (['open-position-pinned-pair', 'open-position'], False),
                                  (['open-position', 'pumpswap-address-catalog'], True)):
            with self.subTest(sources=sources):
                coin = {'symbol': 'X'} if sources is None else {'symbol': 'X', 'sources': sources}
                self.assertIs(guard.market_observation(coin), expected)
        config = guard.config()['registry']['coverage']
        self.assertEqual((config['basis_version'], config['held_position_sources']),
                         ('DISCOVERED_MARKET_ROWS_V2', ['open-position', 'open-position-pinned-pair']))

    def test_a_main_scan_that_only_refreshes_a_held_position_never_extends_coverage(self):
        # scan_once with the discovery lists down (no addresses after the cache expired) and
        # the pairs endpoint answering for the one open position.
        assert_temporary(m.STATE_PATH)
        m.STATE_PATH.unlink(missing_ok=True)
        m.STATE = m.State()
        monitor = m.Monitor()
        self.addCleanup(monitor.stop)
        registry = guard.TickerRegistry()
        monitor._entry_defense = entry_defense.DefensiveEntryLayer(registry=registry)
        now = m.now_ms()
        m.STATE.positions = [{'id': 'held', 'address': MINT, 'pairAddress': PAIR, 'symbol': 'HELD'}]

        def pair(mint, pool, symbol):
            return {'chainId': 'solana', 'dexId': 'pumpswap', 'pairAddress': pool,
                    'baseToken': {'address': mint, 'symbol': symbol}, 'quoteToken': {'address': SOL},
                    'priceUsd': '0.04', 'priceNative': '0.0003', 'liquidity': {'usd': 500_000},
                    'marketCap': 5_000_000, 'pairCreatedAt': now - 3 * DAY}
        held_pair, other_pair = pair(MINT, PAIR, 'HELD'), pair(OTHER_MINT, OTHER_PAIR, 'OTHR')
        common = (patch.object(m, 'gecko_new_pumpswap_pairs', return_value=[]),
                  patch.object(monitor, 'maybe_open'), patch.object(monitor, 'prewarm_entry_checks'),
                  patch.object(m.training_bridge, 'enabled', return_value=False))
        for p in common:
            p.start()
            self.addCleanup(p.stop)
        with patch.object(monitor.discovery, 'get', return_value=([], {})), \
                patch.object(m, 'fetch_pairs', return_value=[held_pair]):
            monitor.scan_once()
        self.assertEqual(m.STATE.status, 'monitoring')
        self.assertEqual([coin['sources'] for coin in m.STATE.feed], [['open-position']])
        coverage = registry.coverage_status(now)
        self.assertIsNone(coverage['observed_until'])
        self.assertEqual((coverage['held_only_scans'], len(registry)), (1, 1))
        # Discovery back: a discovered market row marks coverage again.
        meta = {OTHER_MINT: {'sources': ['latest'], 'icon': '', 'header': '', 'description': '',
                             'links': [], 'boost_amount': 0}}
        with patch.object(monitor.discovery, 'get', return_value=([OTHER_MINT], meta)), \
                patch.object(m, 'fetch_pairs', return_value=[held_pair, other_pair]):
            monitor.scan_once()
        self.assertEqual(m.STATE.status, 'monitoring')
        self.assertIsNotNone(registry.coverage_status(m.now_ms())['observed_until'])
        self.assertEqual(len(registry), 2)

    def test_a_paused_engine_publishes_its_registry_status_on_every_scan(self):
        # Review finding: maybe_open returns at once while STATE.running is false, and
        # entry_diagnostics (re-initialised as 'starting') was the only place of the layer
        # status, so the paused cost-first account never showed its ticker coverage.
        assert_temporary(m.STATE_PATH)
        m.STATE_PATH.unlink(missing_ok=True)
        m.STATE = m.State()
        m.STATE.running = False
        monitor = m.Monitor()
        self.addCleanup(monitor.stop)
        registry = guard.TickerRegistry()
        monitor._entry_defense = entry_defense.DefensiveEntryLayer(registry=registry)
        now = m.now_ms()
        cover(registry, now - 30 * self.HOUR, now - MINUTE)
        self.assertIsNone(m.STATE.snapshot()['defensive_entry_layer'], 'nothing before the first scan')
        pair = {'chainId': 'solana', 'dexId': 'pumpswap', 'pairAddress': OTHER_PAIR,
                'baseToken': {'address': OTHER_MINT, 'symbol': 'OTHR'}, 'quoteToken': {'address': SOL},
                'priceUsd': '0.04', 'priceNative': '0.0003', 'liquidity': {'usd': 500_000},
                'marketCap': 5_000_000, 'pairCreatedAt': now - 30 * DAY}
        meta = {OTHER_MINT: {'sources': ['latest'], 'icon': '', 'header': '', 'description': '',
                             'links': [], 'boost_amount': 0}}
        with patch.object(m, 'gecko_new_pumpswap_pairs', return_value=[]), \
                patch.object(monitor, 'prewarm_entry_checks'), \
                patch.object(m.training_bridge, 'enabled', return_value=False), \
                patch.object(monitor.discovery, 'get', return_value=([OTHER_MINT], meta)), \
                patch.object(m, 'fetch_pairs', return_value=[pair]):
            monitor.scan_once()
        snapshot = m.STATE.snapshot()
        self.assertFalse(snapshot['running'])
        self.assertEqual(snapshot['entry_diagnostics']['status'], 'starting', 'no entry was evaluated')
        layer = snapshot['defensive_entry_layer']
        self.assertEqual(layer['version'], 'DEFENSIVE_ENTRY_LAYER_V1')
        coverage = layer['ticker_registry']['coverage']
        self.assertFalse(coverage['warming'])
        self.assertGreaterEqual(coverage['coverage_hours'], 29.9)
        self.assertEqual(layer['ticker_registry']['entries'], 1)
        # In memory only: the ledger file never carries it.
        self.assertNotIn('defensive_entry_layer', json.loads(m.STATE_PATH.read_text(encoding='utf-8')))

    def test_coverage_survives_a_restart_and_a_cap_eviction_moves_its_start(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'state.ticker_registry.json'
            first = guard.TickerRegistry(path, clock=lambda: self.NOW)
            cover(first, self.NOW - 30 * self.HOUR, self.NOW)
            self.assertTrue(first.flush())
            restarted = guard.TickerRegistry(path, clock=lambda: self.NOW + 10 * MINUTE)
            self.assertEqual(restarted.loaded_version, 'TICKER_REGISTRY_V2_COVERAGE')
            self.assertEqual(restarted.coverage_ms(self.NOW + 10 * MINUTE), 30 * self.HOUR + 10 * MINUTE)
            self.assertFalse(restarted.status()['coverage']['warming'])
        # The 40,000-entry cap evicted sightings up to hour 11: memory is complete only after them.
        small = guard.TickerRegistry(max_entries=20, prune_interval_ms=0)
        for index in range(30):
            small.observe([{'address': f'M{index:02d}' + 'A' * 40, 'pairAddress': f'P{index:02d}' + 'B' * 40,
                            'symbol': f'T{index}'}], self.NOW + index * self.HOUR)
        self.assertEqual(small.status()['cap_evictions'], 12)
        self.assertEqual(small.coverage_status(self.NOW + 29 * self.HOUR)['covered_since'], self.NOW + 11 * self.HOUR)
        self.assertEqual(small.coverage_ms(self.NOW + 29 * self.HOUR), 18 * self.HOUR)

    def test_the_missing_symbol_placeholder_is_a_missing_ticker(self):
        made = m.make_coin(MINT, {'pairAddress': PAIR, 'priceUsd': 1, 'baseToken': {},
                                  'liquidity': {'usd': 500_000}, 'marketCap': 5_000_000,
                                  'pairCreatedAt': self.NOW - 3 * DAY}, {})
        self.assertEqual(made['symbol'], 'TOKEN')
        registry = covered_registry(self.NOW)
        self.assertEqual(guard.check(made, self.NOW, registry)['reasons'], ['rug_input_unknown'])
        self.assertEqual(guard.check(self.young(symbol=None), self.NOW, registry)['reasons'], ['rug_input_unknown'])
        # An established pool (>= 14 days) is never judged by tickers, so it needs none.
        self.assertEqual(guard.check(dict(made, pairCreatedAt=self.NOW - 30 * DAY), self.NOW, registry)['reasons'], [])
        # Placeholders are never registered, so two unnamed mints are not each other's reuse.
        self.assertFalse(registry.observe_coin(made, self.NOW))
        self.assertEqual(len(registry), 0)
        self.assertEqual(guard.ticker_of({'symbol': 'TOKEN'}), '')
        self.assertEqual(guard.ticker_of({'symbol': 'TOKENS'}), 'tokens')
        self.assertEqual(guard.config()['unknown_ticker_placeholders'], ['token'])


class TickerRegistrySeedBuilderTests(unittest.TestCase):
    """scripts/build_ticker_registry_seed.py: first-deploy seed from a training journal copy."""
    T0 = 1_800_000_000_000

    @classmethod
    def setUpClass(cls):
        import importlib.util
        path = Path(__file__).resolve().parents[1] / 'scripts' / 'build_ticker_registry_seed.py'
        spec = importlib.util.spec_from_file_location('build_ticker_registry_seed', path)
        cls.tool = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.tool)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='neo-seed-builder-')
        self.addCleanup(self.tmp.cleanup)
        self.journal = Path(self.tmp.name) / 'observations.jsonl'
        self.out = Path(self.tmp.name) / 'state.ticker_registry.json'

    def write_journal(self, stamps, *, extra=()):
        lines = []
        for index, stamp in enumerate(stamps):
            coin = {'address': f'M{index % 5}' + 'A' * 42, 'pairAddress': f'P{index % 5}' + 'B' * 42,
                    'symbol': f'T{index % 5}', 'updatedAt': stamp}
            lines.append(json.dumps({'observed_at': stamp, 'available_at': stamp, 'coin': coin}))
        # A relaunch family: two other mints of DOTF seen early on.
        lines.append(json.dumps({'observed_at': stamps[1], 'coin': {'address': OTHER_MINT, 'pairAddress': OTHER_PAIR,
                                                                    'symbol': 'D.O.T.F', 'updatedAt': stamps[1]}}))
        lines.extend(extra)
        self.journal.write_text('\n'.join(lines) + '\n', encoding='utf-8')
        return self.journal.read_bytes()

    def run_tool(self, *flags, clock=None):
        from contextlib import redirect_stderr, redirect_stdout
        import io
        buffer = io.StringIO()
        with redirect_stdout(buffer), redirect_stderr(io.StringIO()):
            code = self.tool.main(['--journal', str(self.journal), '--out', str(self.out), *flags], clock=clock)
        return code, json.loads(buffer.getvalue())

    def held_rows(self, start, end, *, step=10 * MINUTE):
        """Rows of one held coin only (discovery down): scan rows and position-mark rows."""
        rows = []
        stamp = start
        while stamp <= end:
            coin = {'address': MINT, 'pairAddress': PAIR, 'symbol': 'HELD', 'updatedAt': stamp,
                    'sources': ['open-position']}
            rows.append(json.dumps({'observed_at': stamp, 'coin': coin}))
            rows.append(json.dumps({'observed_at': stamp, 'rejection_reasons': ['exit_quote'],
                                    'coin': dict(coin, sources=['open-position-pinned-pair'])}))
            # A valid mark of a held coin whose snapshot still carries its discovery source.
            rows.append(json.dumps({'observed_at': stamp, 'rejection_reasons': [],
                                    'source': {'execution_evidence': {'mark': {'quoted_at': stamp}}},
                                    'coin': dict(coin, sources=['latest'])}))
            stamp += step
        return rows

    def old_sidecar(self, *, covered_since, observed_until, pools=(('Q' * 44, 'R' * 44, 'OLDT'),)):
        """A sidecar left by an earlier attempt (main ran before the seed, or a rolled-back deploy)."""
        registry = guard.TickerRegistry(self.out, clock=lambda: observed_until)
        for mint, pool, symbol in pools:
            registry.observe_coin({'address': mint, 'pairAddress': pool, 'symbol': symbol}, covered_since)
        cover(registry, covered_since, observed_until)
        self.assertTrue(registry.flush())
        return self.out.read_bytes()

    def test_held_position_rows_never_extend_the_seed_coverage(self):
        # The journal's market rows end; for 3 h only the held coin is journalled
        # (scan rows under 'open-position' and its position-mark rows).
        stamps = [self.T0 + index * 10 * MINUTE for index in range(26 * 6 + 1)]            # 26 h
        last_market = stamps[-1]
        held = self.held_rows(last_market + 10 * MINUTE, last_market + 3 * guard.HOUR_MS)
        self.write_journal(stamps, extra=held)
        code, summary = self.run_tool(clock=lambda: last_market + 3 * guard.HOUR_MS + MINUTE)
        # Written (the sightings count) but not vouching at the end: exit 2.
        self.assertEqual((code, summary['written'], summary['vouches_at_end']), (2, True, False))
        self.assertIn('warning', summary)
        self.assertEqual((summary['market_rows'], summary['held_position_rows']), (len(stamps) + 1, len(held)))
        self.assertEqual(summary['coverage']['observed_until'], last_market)
        self.assertEqual(summary['last_observed_at'], last_market + 3 * guard.HOUR_MS)
        # At the journal's end the market coverage lapsed (3 h > 60 min): no 24 h vouching.
        self.assertEqual(summary['coverage']['coverage_hours'], 0.0)
        self.assertTrue(summary['coverage']['warming'])
        self.assertEqual(summary['coverage_basis'], 'DISCOVERED_MARKET_ROWS_V2')
        loaded = guard.TickerRegistry(self.out, clock=lambda: summary['last_observed_at'])
        self.assertEqual(len(loaded), 7, 'the held coin is still a sighting')
        self.assertEqual(loaded.coverage_ms(summary['last_observed_at']), 0.0)

    def test_replace_stale_recovers_a_sidecar_left_by_an_earlier_attempt(self):
        stamps = [self.T0 + index * 10 * MINUTE for index in range(26 * 6 + 1)]            # 26 h
        self.write_journal(stamps)
        last = stamps[-1]
        now = last + 20 * MINUTE
        # The services ran 40 min before the seed: main's sidecar is current but only 40 min long.
        old = self.old_sidecar(covered_since=last - 30 * MINUTE, observed_until=last + 10 * MINUTE)
        with self.assertRaises(SystemExit):
            self.run_tool(clock=lambda: now)
        self.assertEqual(self.out.read_bytes(), old)
        code, summary = self.run_tool('--replace-stale', clock=lambda: now)
        self.assertEqual(code, 0)
        replaced = summary['replaced']
        self.assertEqual((replaced['status'], replaced['coverage_current'], replaced['merged_new_pools']),
                         ('OK', True, 1))
        backup = Path(replaced['backup'])
        self.assertEqual(backup.name, f'state.ticker_registry.json.replaced-{now}')
        self.assertEqual(backup.read_bytes(), old, 'the old sidecar is kept')
        loaded = guard.TickerRegistry(self.out, clock=lambda: now)
        self.assertEqual(len(loaded), 7, 'the old sightings are merged')
        self.assertEqual(loaded.coverage_status(now)['covered_since'], self.T0)
        self.assertFalse(summary['coverage_at_now']['warming'])
        # Now the sidecar vouches (current, >= 24 h): it is never replaced.
        written = self.out.read_bytes()
        with self.assertRaises(SystemExit):
            self.run_tool('--replace-stale', clock=lambda: now + MINUTE)
        self.assertEqual(self.out.read_bytes(), written)

    def test_replace_stale_accepts_a_lapsed_or_corrupt_sidecar_only(self):
        stamps = [self.T0 + index * 10 * MINUTE for index in range(26 * 6 + 1)]
        self.write_journal(stamps)
        last = stamps[-1]
        now = last + 30 * MINUTE
        # 30 h of coverage that ended 61 min ago: not current, so it may be replaced.
        self.old_sidecar(covered_since=now - 31 * guard.HOUR_MS, observed_until=now - 61 * MINUTE)
        self.assertTrue(self.tool.existing_sidecar(self.out, now)['replaceable'])
        code, summary = self.run_tool('--replace-stale', clock=lambda: now)
        self.assertEqual((code, summary['replaced']['coverage_current']), (0, False))
        for path in Path(self.tmp.name).glob('*.replaced-*'):
            path.unlink()
        # A corrupt sidecar is replaceable; its backup is kept byte for byte.
        self.out.write_text('{broken', encoding='utf-8')
        code, summary = self.run_tool('--replace-stale', clock=lambda: now)
        self.assertEqual((code, summary['replaced']['status'], summary['replaced']['merged_new_pools']),
                         (0, 'CORRUPT', 0))
        self.assertEqual(Path(summary['replaced']['backup']).read_text(encoding='utf-8'), '{broken')
        # A current sidecar of exactly 24 h vouches and is kept.
        self.out.unlink()
        self.old_sidecar(covered_since=now - 24 * guard.HOUR_MS, observed_until=now - 60 * MINUTE)
        self.assertFalse(self.tool.existing_sidecar(self.out, now)['replaceable'])
        self.assertTrue(self.tool.existing_sidecar(self.out, now + 1)['replaceable'])

    def test_a_continuous_journal_gives_a_covered_sidecar_and_the_journal_is_untouched(self):
        stamps = [self.T0 + index * 10 * MINUTE for index in range(26 * 6 + 1)]            # 26 h
        before = self.write_journal(stamps, extra=('{broken', '[]', json.dumps({'observed_at': 1})))
        last = stamps[-1]
        code, summary = self.run_tool(clock=lambda: last + MINUTE)
        self.assertEqual((code, summary['vouches_at_end']), (0, True))
        self.assertEqual(self.journal.read_bytes(), before, 'the journal is read only')
        self.assertEqual((summary['rows'], summary['invalid_rows'], summary['entries']), (len(stamps) + 4, 3, 6))
        self.assertEqual(summary['version'], 'TICKER_REGISTRY_SEED_V4')
        self.assertEqual(summary['services_stopped_check'], 'NO_UNSTOPPED_SERVICES_MANIFEST')
        # Main's registry status is on /state after every scan, also while main is paused.
        self.assertIn('defensive_entry_layer.ticker_registry.load_status == LOADED', summary['verify_after_start'])
        self.assertEqual(summary['coverage']['covered_since'], self.T0)
        self.assertEqual(summary['coverage']['coverage_hours'], 26.0)
        # The start deadline is observed_until + 60 min, printed in UTC.
        self.assertEqual(summary['start_services_before_utc'], self.tool.iso_utc(last + guard.HOUR_MS))
        self.assertTrue(summary['start_services_before_utc'].endswith('Z'))
        self.assertEqual(summary['clock']['elapsed_seconds'], 0.0)
        self.assertEqual(summary['replay']['version'], 'SEED_REPLAY_WINDOW_V1')
        self.assertEqual((summary['replay']['window_days'], summary['replay']['skipped_bytes']), (15.0, 0))
        loaded = guard.TickerRegistry(self.out, clock=lambda: last + 10 * MINUTE)
        self.assertEqual((loaded.load_status, len(loaded)), ('LOADED', 6))
        relaunch = {'address': MINT, 'pairAddress': PAIR, 'symbol': 'DOTF', 'liquidityUsd': 404_000,
                    'marketCap': 20_100_000, 'pairCreatedAt': last - 3 * DAY}
        self.assertEqual(guard.check(relaunch, last + 10 * MINUTE, loaded)['reasons'], ['rug_ticker_reuse'])
        unseen = dict(relaunch, symbol='FRESH')
        self.assertEqual(guard.check(unseen, last + 10 * MINUTE, loaded)['reasons'], [])
        # An existing sidecar is never replaced.
        written = self.out.read_bytes()
        with self.assertRaises(SystemExit):
            self.run_tool()
        self.assertEqual(self.out.read_bytes(), written)

    def test_a_journal_gap_longer_than_60_minutes_restarts_coverage(self):
        stamps = ([self.T0 + index * 10 * MINUTE for index in range(12)]
                  + [self.T0 + 4 * 60 * MINUTE + index * 10 * MINUTE for index in range(12)])
        self.write_journal(stamps)
        code, summary = self.run_tool(clock=lambda: stamps[-1] + MINUTE)
        # The sidecar is written (sightings count) but its 1.8 h coverage does not vouch: exit 2.
        self.assertEqual((code, summary['written'], summary['vouches_at_end']), (2, True, False))
        self.assertEqual(summary['coverage']['covered_since'], self.T0 + 4 * 60 * MINUTE)
        self.assertEqual(summary['coverage']['resets'], 1)
        self.assertTrue(summary['coverage']['warming'])

    def test_coverage_is_judged_at_the_clock_after_the_replay(self):
        # Review finding: coverage_at_now used the clock read before the replay, which takes
        # about 4 min per journal day; a replay that outlasts the 60-minute window printed
        # warming false and exited 0 although coverage had lapsed.
        stamps = [self.T0 + index * 10 * MINUTE for index in range(26 * 6 + 1)]            # 26 h
        self.write_journal(stamps)
        last = stamps[-1]
        reads = iter([last + MINUTE, last + 62 * MINUTE])
        code, summary = self.run_tool(clock=lambda: next(reads))
        self.assertEqual((code, summary['written'], summary['vouches_at_end']), (2, True, False))
        self.assertEqual((summary['clock']['started_at'], summary['clock']['finished_at']),
                         (last + MINUTE, last + 62 * MINUTE))
        self.assertEqual(summary['clock']['elapsed_seconds'], 61 * 60.0)
        self.assertTrue(summary['coverage_at_now']['warming'])
        self.assertEqual(summary['coverage_at_now']['coverage_hours'], 0.0)
        self.assertEqual(summary['start_services_before_utc'], self.tool.iso_utc(last + guard.HOUR_MS))
        self.assertTrue(self.out.exists(), 'the sightings are still written')

    def test_the_replay_seeks_to_the_window_instead_of_the_first_byte(self):
        # 20 days of rows every 30 min; the default window replays the last 15 days (+1 h slack).
        stamps = [self.T0 + index * 30 * MINUTE for index in range(20 * 48 + 1)]
        self.write_journal(stamps)
        last = stamps[-1]
        with patch.object(self.tool, 'SEEK_GRANULARITY_BYTES', 512):
            window = self.tool.replay_window(self.journal, 15.0)
            code, summary = self.run_tool(clock=lambda: last + MINUTE)
        self.assertEqual(code, 0)
        self.assertEqual(window['target_start_at'], last - 15 * DAY - guard.HOUR_MS)
        self.assertGreater(window['skipped_bytes'], 0)
        replay = summary['replay']
        self.assertEqual((replay['start_offset'], replay['skipped_bytes']), (window['start_offset'],) * 2)
        # Every row of the window is replayed, plus at most the 512-byte search granularity
        # before it (and the relaunch row write_journal appends last).
        in_window = sum(1 for stamp in stamps if stamp >= window['target_start_at'])
        self.assertGreaterEqual(summary['rows'], in_window + 1)
        self.assertLessEqual(summary['rows'], in_window + 1 + 6)
        self.assertLess(summary['rows'], len(stamps))
        self.assertGreaterEqual(summary['coverage']['coverage_hours'], 15 * 24)
        # The skipped start of the journal is past the 14-day retention anyway: same sightings.
        self.out.unlink()
        code, full = self.run_tool('--full-journal', clock=lambda: last + MINUTE)
        self.assertEqual(code, 0)
        self.assertEqual((full['replay']['full_journal'], full['replay']['skipped_bytes']), (True, 0))
        self.assertEqual((full['rows'], full['entries']), (len(stamps) + 1, summary['entries']))
        self.assertFalse(summary['replay']['limited_by_replay_budget'])
        # The window plus the 1 h slack (and at most the search granularity).
        self.assertGreaterEqual(summary['replay']['effective_window_days'], 15.0)
        self.assertLess(summary['replay']['effective_window_days'], 15.1)
        # 15 journal days take about an hour at the reference rate: the replay budget starts it
        # later when the window holds more journal than --max-replay-minutes replays in time.
        size = self.journal.stat().st_size
        budget = self.tool.replay_window(self.journal, 15.0, max_bytes=size // 4)
        self.assertTrue(budget['limited_by_replay_budget'])
        self.assertGreaterEqual(budget['skipped_bytes'], size - size // 4)
        self.assertLess(budget['effective_window_days'], 15 / 3)
        self.out.unlink()
        with patch.object(self.tool, 'REFERENCE_REPLAY_BYTES_PER_S', size / 4 / 60):
            code, capped = self.run_tool('--max-replay-minutes', '1', clock=lambda: last + MINUTE)
        self.assertEqual((code, capped['replay']['limited_by_replay_budget']), (0, True))
        self.assertEqual(capped['replay']['estimated_replay_minutes'], 1.0)
        self.assertGreaterEqual(capped['coverage']['coverage_hours'], 24)
        # A window shorter than 2 days could never reach 24 h of coverage: refused.
        with self.assertRaises(SystemExit):
            self.run_tool('--window-days', '1', clock=lambda: last + MINUTE)

    def test_a_sidecar_stamped_ahead_of_the_wall_clock_is_replaceable(self):
        # A sidecar written by a test with a fixed future clock (or a skewed host) carries
        # coverage that was never observed: it reads as no coverage and may be replaced.
        stamps = [self.T0 + index * 10 * MINUTE for index in range(26 * 6 + 1)]
        self.write_journal(stamps)
        last = stamps[-1]
        now = last + 10 * MINUTE
        self.old_sidecar(covered_since=now - 30 * guard.HOUR_MS, observed_until=now + 90 * DAY)
        verdict = self.tool.existing_sidecar(self.out, now)
        self.assertEqual((verdict['replaceable'], verdict['coverage_current'], verdict['observed_until']),
                         (True, False, None))
        self.assertEqual(verdict['coverage_ahead_minutes'], round(90 * DAY / MINUTE, 1))
        code, summary = self.run_tool('--replace-stale', clock=lambda: now)
        self.assertEqual((code, summary['replaced']['coverage_ahead_minutes']), (0, round(90 * DAY / MINUTE, 1)))
        self.assertFalse(summary['coverage_at_now']['warming'])

    def test_the_tool_refuses_a_runtime_whose_services_were_not_stopped(self):
        # Review finding: --replace-stale under running services wrote the seed, and the running
        # main overwrote it at its next periodic save. A runtime directory whose
        # services/processes.json exists without services/stop.request (Stop writes it, Start
        # removes it) is refused, for the first run and --replace-stale alike.
        stamps = [self.T0 + index * 10 * MINUTE for index in range(26 * 6 + 1)]
        self.write_journal(stamps)
        last = stamps[-1]
        now = last + 20 * MINUTE
        runtime = Path(self.tmp.name) / 'runtime' / 'accounts'
        services = runtime / 'services'
        services.mkdir(parents=True)
        (services / 'processes.json').write_text('{"mode": "PAPER", "processes": []}', encoding='utf-8')
        self.out = runtime / 'state.ticker_registry.json'
        self.assertEqual(self.tool.running_services_root(self.out), runtime)
        with self.assertRaises(SystemExit):
            self.run_tool(clock=lambda: now)
        self.assertFalse(self.out.exists(), 'nothing written while the services may run')
        old = self.old_sidecar(covered_since=last - 30 * MINUTE, observed_until=last + 10 * MINUTE)
        with self.assertRaises(SystemExit):
            self.run_tool('--replace-stale', clock=lambda: now)
        self.assertEqual(self.out.read_bytes(), old)
        self.assertEqual(list(runtime.glob('*.replaced-*')), [])
        # A personal engine's sidecar below the runtime is refused too.
        personal = runtime / 'users' / 'u1' / 'state.ticker_registry.json'
        self.assertEqual(self.tool.running_services_root(personal), runtime)
        # After Stop (stop.request written) the same recovery runs.
        (services / 'stop.request').write_text('2026-10-08T00:00:00Z', encoding='utf-8')
        self.assertIsNone(self.tool.running_services_root(self.out))
        code, summary = self.run_tool('--replace-stale', clock=lambda: now)
        self.assertEqual((code, summary['written']), (0, True))
        self.assertFalse(summary['coverage_at_now']['warming'])

    def test_replace_stale_never_replaces_a_sidecar_it_cannot_read(self):
        stamps = [self.T0 + index * 10 * MINUTE for index in range(26 * 6 + 1)]
        self.write_journal(stamps)
        last = stamps[-1]
        now = last + 20 * MINUTE
        old = self.old_sidecar(covered_since=last - 30 * MINUTE, observed_until=last + 10 * MINUTE)
        original = Path.read_text

        def locked(path, *args, **kwargs):
            if Path(path) == self.out:
                raise PermissionError(32, 'The process cannot access the file')
            return original(path, *args, **kwargs)

        with patch.object(Path, 'read_text', locked):
            verdict = self.tool.existing_sidecar(self.out, now)
            self.assertEqual((verdict['status'], verdict['replaceable']), ('UNREADABLE', False))
            with self.assertRaises(SystemExit):
                self.run_tool('--replace-stale', clock=lambda: now)
        self.assertEqual(self.out.read_bytes(), old)
        self.assertEqual(list(Path(self.tmp.name).glob('*.replaced-*')), [])


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

    def test_warm_up_compares_the_unrounded_span_and_fails_closed_without_a_reference(self):
        # A segment 299.96 s old publishes history_span_s 300.0 but is still warming; before
        # the fix it left warm-up and skipped rule (a) on a +10% surge.
        coin = dict(self.coin, address=MINT, pairAddress=PAIR, priceUsd=1.0)
        history = heat_veto.PairHistory()
        start = self.now - 299_960
        for stamp in (start, start + 60_000, start + 120_000, start + 180_000, start + 240_000, self.now - 1_000):
            history.observe_coin(dict(coin, updatedAt=stamp), stamp)
        result = heat_veto.evaluate(dict(coin, priceUsd=1.10), self.now, history)
        self.assertEqual(result['metrics']['history_span_s'], 300.0)
        self.assertIsNone(result['metrics']['return_5m_pct'])
        self.assertEqual(result['reasons'][:1], ['heat_history_warming'])
        self.assertIn('return_5m', result['metrics']['warming_windows'])
        self.assertTrue(result['vetoed'])
        # Past warm-up but with no price at or before now - 300 s inside the segment (its
        # early samples were evicted by the per-pair bound): (a) is unmeasurable, so warming.
        bounded = heat_veto.PairHistory(heat_veto.HistoryParameters(max_samples_per_pair=3))
        for back in range(400, 0, -20):
            stamp = self.now - back * 1000
            bounded.observe_coin(dict(coin, priceUsd=1 + back / 10_000, updatedAt=stamp), stamp)
        view = bounded.view(coin)
        self.assertLess(view['since'], self.now - 300_000)
        self.assertGreater(view['samples'][0][0], self.now - 300_000)
        result = heat_veto.evaluate(dict(coin, priceUsd=1.10), self.now, bounded)
        self.assertIsNone(result['metrics']['return_5m_pct'])
        self.assertIn('heat_history_warming', result['reasons'])
        self.assertIn('return_5m', result['metrics']['warming_windows'])

    def high_fee_coin(self, price, paid=False, stamp=None):
        """A 110 bps PumpSwap pool (about 2,580 SOL market cap at $116.26 per SOL)."""
        coin = dict(self.coin, address=MINT, pairAddress=PAIR, dexId='pumpswap', quoteTokenAddress=SOL,
                    marketCap=300_000.0, liquidityUsd=100_000.0, priceUsd=price, priceNative=price / 116.26,
                    volume={'m5': 100.0, 'h1': 12_000.0}, txns={'m5': {'buys': 10, 'sells': 10}},
                    priceChange={'h6': 0, 'h24': 0}, sources=['latest'] if paid else [])
        if stamp is not None:
            coin['updatedAt'] = stamp
        return coin

    def feed_high_fee(self, history, start_s, end_s, *, origin=None):
        """Every 10 s: 'latest' from -40 to -20 min, price 1.0 falling to 0.6 at -9 min, then flat."""
        origin = self.now if origin is None else origin
        for t in range(start_s, end_s + 1, 10):
            stamp = origin + t * 1000
            history.observe_coin(self.high_fee_coin(1.0 if t < -540 else 0.6, -2400 <= t <= -1200, stamp), stamp)

    def test_e_and_f_need_their_own_window_coverage_after_a_restart(self):
        self.assertEqual(feasibility.pumpswap_fee_bps(self.high_fee_coin(0.6)), 110.0)
        current = self.high_fee_coin(0.6, stamp=self.now)
        continuous = heat_veto.PairHistory()
        self.feed_high_fee(continuous, -3660, -1)
        self.assertEqual(heat_veto.evaluate(current, self.now, continuous)['reasons'],
                         ['heat_paid_profile_high_fee', 'heat_crash_in_progress'])
        # Restarted 6 minutes ago: the post-restart samples show neither the paid profile
        # nor the 15-min high, so both windows are warming (before the fix: [] and allowed).
        restarted = heat_veto.PairHistory()
        self.feed_high_fee(restarted, -360, -1)
        result = heat_veto.evaluate(current, self.now, restarted)
        self.assertEqual(result['reasons'], ['heat_history_warming'])
        self.assertEqual(result['metrics']['warming_windows'], ['paid_profile_60m', 'crash_15m'])
        self.assertEqual(result['metrics']['fraction_of_15m_high'], 1.0)
        self.assertTrue(result['vetoed'])
        logged = heat_veto.evaluate(current, self.now, restarted, log_only=True)
        self.assertEqual((logged['reasons'], logged['vetoed']), (['heat_history_warming'], False))
        # 15 minutes after the restart the crash window is covered (flat at 0.6: no crash)...
        later = self.now + 9 * MINUTE
        self.feed_high_fee(restarted, -360 + 10, 9 * 60 - 1, origin=self.now)
        result = heat_veto.evaluate(self.high_fee_coin(0.6, stamp=later), later, restarted)
        self.assertEqual(result['reasons'], ['heat_history_warming'])
        self.assertEqual(result['metrics']['warming_windows'], ['paid_profile_60m'])
        # ...and after 60 minutes of observation the paid-profile lookback is too.
        latest = self.now + 54 * MINUTE + 10_000
        for t in range(9 * 60, 54 * 60 + 1, 10):
            stamp = self.now + t * 1000
            restarted.observe_coin(self.high_fee_coin(0.6, stamp=stamp), stamp)
        result = heat_veto.evaluate(self.high_fee_coin(0.6, stamp=latest), latest, restarted)
        self.assertEqual(result['reasons'], [], result['metrics'])
        self.assertGreaterEqual(result['metrics']['process_coverage_s'], 3600)
        # A pool below 100 bps waits only for the 15-min crash window.
        low_fee = dict(self.high_fee_coin(0.6), marketCap=31_000_000.0)
        self.assertLess(feasibility.pumpswap_fee_bps(low_fee), 100)
        history = heat_veto.PairHistory()
        warm(history, low_fee, self.now)
        self.assertEqual(heat_veto.evaluate(dict(low_fee, updatedAt=self.now), self.now, history)['reasons'], [])

    def test_an_old_updated_at_never_claims_window_coverage(self):
        # The first observation carries a 30-minute-old updatedAt: coverage still starts
        # when this process saw it.
        coin = dict(self.coin, address=MINT, pairAddress=PAIR)
        history = heat_veto.PairHistory()
        history.observe_coin(dict(coin, updatedAt=self.now - 30 * MINUTE), self.now - 360_000)
        warm(history, coin, self.now, seconds=350)
        self.assertEqual(history.view(coin)['first_seen'], self.now - 360_000)
        self.assertEqual(history.observing_since, self.now - 360_000)
        result = heat_veto.evaluate(coin, self.now, history)
        self.assertEqual(result['metrics']['warming_windows'], ['crash_15m'])
        self.assertEqual(result['metrics']['pair_coverage_s'], 360.0)
        self.assertEqual(history.status()['observing_since'], self.now - 360_000)
        self.assertFalse(history.status()['persistent'])


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
        layer = entry_defense.DefensiveEntryLayer(registry=covered_registry(self.NOW))
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


class OmittedLiquidityNormalizationTests(unittest.TestCase):
    def test_main_feed_and_exact_pair_refresh_report_an_omitted_liquidity_alike(self):
        """LAB_FORWARD_MARK_LIQUIDITY_V2: the research scan log came through make_coin, which turns an
        omitted DexScreener liquidity into 0.0 (research F6: every such 0 was terminal); the forward
        books read the exact-pair refresh of the same payload the same way."""
        import lab_position_marks
        forward = lab.lab_forward
        for label, extra in (('omitted', {}), ('usd_missing', {'liquidity': {}}), ('zero', {'liquidity': {'usd': 0}}),
                             ('known', {'liquidity': {'usd': 250_000}})):
            with self.subTest(label):
                pair = {'chainId': 'solana', 'pairAddress': PAIR, 'priceUsd': '0.01', 'priceNative': '0.0001',
                        'baseToken': {'address': MINT, 'symbol': 'X'}, 'quoteToken': {'address': SOL}, **extra}
                feed = m.make_coin(MINT, dict(pair), {})
                refresh = lab_position_marks.parse_pair_response({'pairs': [dict(pair)]}, MINT, PAIR, 1)
                expected = 250_000.0 if label == 'known' else 0.0
                self.assertEqual(feed['liquidityUsd'], expected)
                self.assertEqual(refresh['liquidityUsd'], expected)
                self.assertEqual(forward.reported_liquidity_usd(feed), expected)
                self.assertEqual(forward.reported_liquidity_usd(refresh), expected)


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


class TrainingScoreBasisTests(unittest.TestCase):
    """The PAPER_TRAINING_V1 learners stay on NEO_MARKET_SCORE_V1: the context recorded for
    them (GOLD_ADAPTIVE exits read its conviction and hold mode) and their min_score."""

    # Weak flow: the conviction sits next to the CONVICTION_EXIT line (35), so the score
    # model's neo_score term (V1 85-94: 0, V2 under 85: -4) decides the exit.
    FAST = {'quality': 'COMPLETE', 'buy_sell_usd_ratio': 0.7}
    SLOW = {'buy_sell_usd_ratio': 0.9, 'unique_wallets': 3, 'repeat_buy_wallets': 0,
            'whale_buy_usd': 0, 'whale_sell_usd': 0}
    HOLD_FIELDS = ('conviction', 'mode', 'max_hold_minutes', 'target_pct', 'trail_arm_pct', 'trail_pct')

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
        return {'liquidity': {'usd': ratio * cap}, 'marketCap': cap, 'volume': {'h1': 300_000},
                'priceChange': {'m5': 2, 'h1': 5}, 'txns': {'m5': {'buys': 50, 'sells': 50}},
                'pairCreatedAt': m.now_ms() - 3 * DAY - 12 * 60 * MINUTE, 'pairAddress': PAIR, 'priceUsd': 1,
                'baseToken': {'symbol': 'X'}}

    @staticmethod
    def gold_adaptive_exit(context):
        """paper_training's GOLD_ADAPTIVE exit call for a position at net -1% after one minute."""
        params = training.HYPOTHESES['GOLD_ADAPTIVE']
        return training.adaptive_exit_reason({'address': MINT}, context, net_pct=-1.0, peak_net_pct=0.0,
                                             hold_minutes=1.0, stop_pct=params['stop_pct'],
                                             take_profit_pct=params['take_profit_pct'], policy='adaptive')

    def test_gold_adaptive_context_keeps_the_v1_conviction_hold_mode_and_exit(self):
        for ratio in (0.7, 1.1):
            with self.subTest(liq_mc=ratio):
                pair = self.pair(ratio)
                coin = m.make_coin(MINT, pair, {})
                before = ExitContextScoreTests.pre_change_score(pair)
                self.assertEqual(coin['scoreV1'], before)
                old_coin = {key: value for key, value in coin.items() if key != 'scoreV1'}
                old_coin['score'] = before
                # What the pre-change engine recorded for the learners: market_context(coin, {}).
                recorded_before = self.monitor.market_context(old_coin, {})
                recorded_now = self.monitor.training_context(coin)
                for field in self.HOLD_FIELDS:
                    self.assertEqual(recorded_now[field], recorded_before[field], field)
                self.assertEqual(recorded_now['conviction_score_version'], 'NEO_MARKET_SCORE_V1')
                # The engine's own V2 entry context differs here, and would have exited.
                entry = self.monitor.market_context(coin, {})
                self.assertLess(entry['conviction'], 35)
                self.assertGreaterEqual(recorded_now['conviction'], 35)
                self.assertEqual(recorded_now['entry_conviction'], entry['conviction'])
                self.assertEqual(self.gold_adaptive_exit(entry), 'CONVICTION_EXIT')
                self.assertEqual(self.gold_adaptive_exit(recorded_now), self.gold_adaptive_exit(recorded_before))
                self.assertIsNone(self.gold_adaptive_exit(recorded_now))
                # An entry context handed over by the engine is converted the same way.
                self.assertEqual(self.monitor.training_context(coin, context=entry), recorded_now)
                # A held position's context is already V1-based and only labelled.
                position = {'address': MINT, 'pairAddress': PAIR, 'entry_liquidity_usd': coin['liquidityUsd']}
                held = self.monitor.training_context(coin, position)
                self.assertEqual(held['conviction'], self.monitor.market_context(coin, position)['conviction'])
                self.assertEqual(held['conviction_score_version'], 'NEO_MARKET_SCORE_V1')
        self.assertEqual(m.STATE.snapshot()['config']['training_score_version'], 'NEO_MARKET_SCORE_V1')

    def test_learner_min_score_and_probe_signal_read_the_v1_score(self):
        self.assertEqual(training.LEARNER_SCORE_VERSION, 'NEO_MARKET_SCORE_V1')
        self.assertEqual(training.learner_score({'score': 66.0, 'scoreV1': 75.0}), 75.0)
        self.assertEqual(training.learner_score({'score': 72.0}), 72.0, 'recorded before V2: score is V1')
        self.assertEqual(training.learner_score({'score': 72.0, 'scoreV1': 'bad'}), 72.0)
        # CONTROL (min_score 70) on a liq/MC-band pool: V2 66 would refuse, V1 75 does not.
        engine = SimpleNamespace(config=training._config(None), _adaptive_context=lambda row: None)
        now = 1_800_000_000_000

        def reasons(coin):
            row = {'available_at': now, 'observed_at': now, 'coin': coin, 'flow': {}}
            return training.PaperTrainingEngine._flow_reasons(engine, row, training.HYPOTHESES['CONTROL'])
        self.assertNotIn('score', reasons({'score': 66.0, 'scoreV1': 75.0}))
        self.assertIn('score', reasons({'score': 75.0, 'scoreV1': 66.0}))
        self.assertNotIn('score', reasons({'score': 75.0}))
        # The engine's probe candidate signal (the cheapest hypothesis, min_score 60).
        coin = established(now, score=55.0, scoreV1=62.0, liquidityUsd=100_000.0)
        flow = {'fresh': True, 'quality': 'COMPLETE', 'latest_at': now,
                'coverage': {'status': 'COMPLETE', 'address': MINT, 'pairAddress': PAIR}, 'trades': 3,
                'unique_wallets': 2, 'buy_usd': 25, 'buy_sell_usd_ratio': 1.1}
        self.assertTrue(training.training_candidate_signal(coin, flow, now=now))
        self.assertFalse(training.training_candidate_signal(dict(coin, scoreV1=58.0), flow, now=now))


# ------------------------------------------------------------------ engine entry paths

class EngineHarness(unittest.TestCase):
    """The real engine path with fixture quotes, flow, safety and price (offline)."""
    STRATEGY = winner_ensemble.VERSION

    def setUp(self):
        m.activate_strategy(self.STRATEGY)
        self.addCleanup(m.activate_strategy, m.DEFAULT_SIGNAL_STRATEGY)
        assert_temporary(m.STATE_PATH)
        m.STATE_PATH.unlink(missing_ok=True)
        m.STATE = m.State()
        self.monitor = m.Monitor()
        self.now = m.now_ms()
        # Non-persistent per test; its ticker registry has watched the market for 48 h
        # (the 24-hour coverage rule has its own engine test).
        self.monitor._entry_defense = entry_defense.DefensiveEntryLayer(registry=covered_registry(self.now))
        self.addCleanup(self.monitor.stop)
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

    def during_entry_quote(self, change):
        """Run ``change()`` inside every entry quote: the market moves while quotes are prepared."""
        real = m.paper_quotes.entry_quote.side_effect

        def entry_quote(*args, **kwargs):
            change()
            return real(*args, **kwargs)
        m.paper_quotes.entry_quote.side_effect = entry_quote

    def assert_refused_at_commit(self, reason, coin=None):
        """The decision-time layer allowed the pool, a quote was spent, the commit recheck refused it."""
        report = self.open_once(coin)
        self.assertFalse(m.STATE.positions)
        self.assertGreater(self.calls['quote'], 0, 'the decision passed and quotes were prepared')
        self.assertIn(reason, report['rejections'], report['rejections'])
        defensive = report['defensive_entry']
        self.assertEqual(defensive['blocked'], 0, 'nothing was blocked at decision time')
        self.assertEqual(defensive['commit_recheck_blocked'], 1)
        return report

    def surge_during_quotes(self, coin):
        surged = dict(coin, priceUsd=coin['priceUsd'] * 1.05, priceNative=coin['priceNative'] * 1.05,
                      updatedAt=m.now_ms())
        self.during_entry_quote(lambda: setattr(m.STATE, 'feed', [surged]))

    def losses_during_quotes(self):
        self.during_entry_quote(lambda: setattr(
            m.STATE, 'history', losses(MINT, PAIR, self.now, first_age=30 * MINUTE)))


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

    def test_a_fresh_ticker_registry_blocks_a_pool_under_14_days_before_quotes(self):
        # First deploy: the engine's registry has watched nothing yet, so a 4-day-old pool
        # that matches an ensemble rule waits for 24 h of coverage; established pools do not.
        self.monitor._entry_defense = entry_defense.DefensiveEntryLayer()
        recent = established(self.now, pairCreatedAt=self.now - 4 * DAY)
        self.assertTrue(winner_ensemble.market_candidates(recent))
        self.warm(recent)
        report = self.assert_blocked_before_any_quote('rug_ticker_registry_warming', recent)
        self.assertEqual(report['defensive_entry']['rejections'], {'rug_ticker_registry_warming': 1})
        self.assertEqual(report['defensive_entry']['examples'][0]['registry_coverage_h'], 0.0)

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

    def test_commit_recheck_refuses_a_surge_that_appears_while_quotes_are_prepared(self):
        self.warm()
        self.surge_during_quotes(self.coin)
        report = self.assert_refused_at_commit('heat_return_5m_surge')
        example = next(row for row in report['examples'] if 'heat_return_5m_surge' in row['reasons'])
        self.assertGreaterEqual(example['metrics']['heat']['return_5m_pct'], 3.0)

    def test_commit_recheck_refuses_losses_booked_while_quotes_are_prepared(self):
        self.warm()
        self.losses_during_quotes()
        self.assert_refused_at_commit('pool_loss_cooldown')

    def test_rugcheck_prewarm_targets_established_pools_the_layer_allows(self):
        # PREWARM_V2_DEFENSIVE_POPULATION. The old population (ageMinutes <= 360) is entirely
        # blocked by the 12 h young-pool rule, so the prewarm now warms established market
        # candidates the layer allows: an allowed pool gets its RugCheck before its entry.
        self.assertEqual(m.PREWARM_VERSION, 'PREWARM_V2_DEFENSIVE_POPULATION')
        young = established(self.now, address=OTHER_MINT, pairAddress=OTHER_PAIR, symbol='YNG',
                            ageMinutes=60, pairCreatedAt=self.now - 60 * MINUTE)
        self.warm(young)
        self.warm()
        self.monitor.prewarm_entry_checks([young])
        self.assertEqual((self.calls['price'], self.calls['rugcheck']), (0, 0), 'structural block')
        self.monitor.prewarm_entry_checks([dict(self.coin, txns={'m5': {'buys': 95, 'sells': 5}})])
        self.assertEqual((self.calls['price'], self.calls['rugcheck']), (0, 0), 'heat veto')
        m.STATE.history = losses(MINT, PAIR, self.now, first_age=30 * MINUTE)
        self.monitor.prewarm_entry_checks([self.coin])
        self.assertEqual((self.calls['price'], self.calls['rugcheck']), (0, 0), 'loss memory')
        m.STATE.history = []
        with patch.object(m, 'market_candidate', return_value=False):
            self.monitor.prewarm_entry_checks([self.coin])
        self.assertEqual((self.calls['price'], self.calls['rugcheck']), (0, 0), 'not a market candidate')
        self.monitor.prewarm_entry_checks([self.coin, young])
        self.assertEqual((self.calls['price'], self.calls['rugcheck']), (1, 1), 'only the established pool')
        self.assertEqual(m.rug_guard.check.call_args_list[-1].args[0]['address'], MINT)
        self.assertEqual(self.calls['quote'], 0)

    def test_rugcheck_prewarm_ranks_by_activity_and_is_capped(self):
        pools = []
        for index, (mint, pair) in enumerate(zip('HJKLMN', 'PQRSTU')):
            coin = established(self.now, address=mint * 44, pairAddress=pair * 44, symbol=f'EST{index}',
                               txns={'m5': {'buys': 10 + index, 'sells': 10}})
            self.warm(coin)
            pools.append(coin)
        self.monitor.prewarm_entry_checks(pools)
        self.assertEqual(self.calls['rugcheck'], m.PREWARM_MAX_CANDIDATES)
        warmed = [call.args[0]['symbol'] for call in m.rug_guard.check.call_args_list]
        self.assertEqual(warmed, ['EST5', 'EST4', 'EST3', 'EST2'])
        self.assertEqual(m.STATE.snapshot()['config']['prewarm_version'], m.PREWARM_VERSION)


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

    def test_commit_recheck_refuses_a_surge_that_appears_while_quotes_are_prepared(self):
        coin = self.universe_coin()
        self.warm(coin)
        self.surge_during_quotes(coin)
        self.assert_refused_at_commit('heat_return_5m_surge', coin)

    def test_commit_recheck_refuses_losses_booked_while_quotes_are_prepared(self):
        coin = self.universe_coin()
        self.warm(coin)
        self.losses_during_quotes()
        self.assert_refused_at_commit('pool_loss_cooldown', coin)


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
        # The refused preflight is recorded for the learners with the V1-basis context.
        context = self.mocks['observe'].call_args.kwargs['context']
        self.assertEqual(context['conviction_score_version'], 'NEO_MARKET_SCORE_V1')
        self.assertEqual(context['conviction'], context['exit_basis_conviction'])


# ------------------------------------------------------------------ Strategy Lab

class LabPathTests(unittest.TestCase):
    NOW = 1_800_000_000_000

    def setUp(self):
        lab.rush_brain._SAMPLE_BY_PAIR.clear()
        self.books = {s['id']: lab.empty_book(s) for s in lab.STRATEGIES}
        self.layer = entry_defense.DefensiveEntryLayer(registry=covered_registry(self.NOW))
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

    def test_cost_first_books_wait_for_ticker_coverage_on_a_young_pool(self):
        # First deploy: the Lab's registry has watched nothing yet. A USDF-like family pool
        # (2.00% liquidity/market cap, 4 days old) passes every physical screen but must
        # not enter the cost-first universe on an empty ticker memory.
        usdf, _ = fixture('USDF_EpugLBw1')
        self.layer.registry = guard.TickerRegistry()
        coin = self.lab_coin(dexId='pumpswap', symbol='USDF', marketCap=usdf['marketCap'],
                             liquidityUsd=usdf['liquidityUsd'], pairCreatedAt=self.NOW - 4 * DAY,
                             priceUsd=.1, priceNative=.1 / 116.0)
        self.assertEqual(cost_first.physical_rejections(coin, cap_usd=150.0), [])
        lab.maybe_open([coin], {})
        for book_id in cost_first.BOOK_IDS:
            diagnostics = self.books[book_id]['entry_diagnostics']
            self.assertEqual(diagnostics['cost_first']['universe_rejections'], {'rug_ticker_registry_warming': 1})
            self.assertIsNone(self.books[book_id]['position'])
        self.assertEqual(self.calls['rugcheck'], 0)

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

    def test_commit_recheck_refuses_a_loss_booked_during_provider_work(self):
        # One book, so the losses appear after its decision and before its commit: the
        # decision-time layer allowed the pool, the commit recheck refuses it.
        coin = self.lab_coin()
        warm(self.layer.history, coin, self.NOW)
        book = self.books['DEEP_LIQ_MOMENTUM']
        real_price = lab.price_integrity.check.side_effect

        def price(candidate):
            book['history'] = losses(MINT, PAIR, self.NOW, first_age=30 * MINUTE)
            return real_price(candidate)
        lab.price_integrity.check.side_effect = price
        with patch.object(lab, 'STRATEGIES', [s for s in lab.STRATEGIES if s['id'] == 'DEEP_LIQ_MOMENTUM']):
            lab.maybe_open([coin], {})
        diagnostics = book['entry_diagnostics']
        self.assertIsNone(book['position'], diagnostics)
        self.assertEqual(self.calls['price'], 1, 'the decision passed and provider work ran')
        self.assertEqual(diagnostics['defensive_rejected'], 0)
        self.assertEqual(diagnostics['blocked_reason'], 'pool_loss_cooldown')
        self.assertEqual(diagnostics['commit_recheck_rejected'], 1)
        self.assertEqual(diagnostics['defensive_entry']['commit_recheck_blocked'], 1)
        import lab_dashboard_projection
        projected = lab_dashboard_projection.compact_strategy_lab({'books': {book['id']: book}})
        self.assertEqual(projected['books'][book['id']]['entry_diagnostics']['defensive_entry']
                         ['commit_recheck_blocked'], 1, 'the dashboard keeps the count')
        # Without the new losses the same refresh opens the position and records the commit decision.
        book['history'] = []
        lab.price_integrity.check.side_effect = real_price
        with patch.object(lab, 'STRATEGIES', [s for s in lab.STRATEGIES if s['id'] == 'DEEP_LIQ_MOMENTUM']):
            lab.maybe_open([coin], {})
        self.assertIsNotNone(book['position'], book['entry_diagnostics'])
        self.assertTrue(book['position']['defensive_entry']['allowed'])
        self.assertNotIn('commit_recheck_blocked', book['entry_diagnostics']['defensive_entry'])


# ------------------------------------------------------------------ tape scheduler

class TapeSchedulerPathTests(unittest.TestCase):
    """A seat serves every ledger: the structural guard withholds one, heat withholds a new
    one (a running lease expires), loss memory and the tape's own warm-up never do (V5)."""
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
        cover(scheduler.defense.registry, self.NOW - 2 * DAY, self.NOW)
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
        self.assertEqual(defensive['heat_veto_mode'], 'NEW_SEATS_WITHHELD_RUNNING_LEASES_KEPT_WARMING_LOG_ONLY')
        self.assertEqual(defensive['seat_rule'], 'NO_SEAT_FOR_A_STRUCTURALLY_BLOCKED_POOL_NO_NEW_SEAT_FOR_A_HOT_POOL')
        # Without the pin the young pool is screened like any candidate.
        selected, report = scheduler.select(dict(state, positions=[]), now=self.NOW, max_tracked=4)
        self.assertEqual(sorted(row['symbol'] for row in selected), ['Lost', 'Safe'])
        self.assertEqual(report['defensive_entry']['blocked_pools_in_feed'], 2)
        # V6 (LAB_FORWARD_TESTS_V1: no pins for flow-free Lab books) keeps every V5 defensive seat rule.
        self.assertEqual(report['policy_version'], 'STABLE_COST_AWARE_TAPE_DISCOVERY_V6_NO_PINS_FOR_FLOW_FREE_LAB_BOOKS')
        self.assertEqual(report['previous_policy_version'], 'STABLE_COST_AWARE_TAPE_DISCOVERY_V5_DEFENSIVE_ENTRY')
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

    def test_the_tape_warm_up_is_log_only_at_seats(self):
        # After a tape-only restart the scheduler's own history is empty: seats are still
        # given at once (the engines enforce their own warm-up), the flag is counted.
        scheduler = TapePoolScheduler()
        coin = self.coin('Safe')
        selected, report = scheduler.select({'feed': [coin]}, now=self.NOW, max_tracked=2)
        self.assertEqual([row['symbol'] for row in selected], ['Safe'])
        self.assertEqual(report['defensive_entry']['blocked'], 0)
        self.assertEqual(report['defensive_entry']['log_only_flags'], {'heat_history_warming': 1})
        self.assertEqual(report['defensive_entry']['heat_log_only_reasons'], ['heat_history_warming'])
        self.assertFalse(report['defensive_entry']['heat_log_only'])

    def test_a_hot_pool_gets_no_new_seat(self):
        # Spec item 4: no seat for a pool every engine would refuse. A crashing pool and a
        # pool with a 95% buy share get no entry or exploration seat; the calm one does.
        scheduler = TapePoolScheduler()
        calm, crash, hot = self.coin('Calm'), self.coin('Crsh'), self.coin('Hott')
        for coin in (calm, crash, hot):
            warm(scheduler.defense.history, coin, self.NOW)
        crashing = dict(crash, priceUsd=crash['priceUsd'] * 0.7, priceNative=crash['priceNative'] * 0.7)
        buying = dict(hot, txns={'m5': {'buys': 95, 'sells': 5}})
        selected, report = scheduler.select({'feed': [calm, crashing, buying]}, now=self.NOW, max_tracked=3)
        self.assertEqual([row['symbol'] for row in selected], ['Calm'])
        defensive = report['defensive_entry']
        self.assertEqual(defensive['heat_withheld_new_seats'], 2)
        self.assertEqual(defensive['rejections'], {'heat_crash_in_progress': 1, 'heat_buy_share_5m': 1})
        self.assertEqual(defensive['log_only_flags'], {}, 'an enforced flag is a rejection, not a log-only flag')
        self.assertEqual(len(defensive['examples']), 2)

    def test_the_seat_report_keeps_up_to_six_examples(self):
        scheduler = TapePoolScheduler()
        young = [self.coin(name, pairCreatedAt=self.NOW - 30 * MINUTE)
                 for name in ('Ah', 'Bj', 'Ck', 'Dm', 'En', 'Fp', 'Gq')]
        selected, report = scheduler.select({'feed': young}, now=self.NOW, max_tracked=8)
        self.assertEqual(selected, [])
        self.assertEqual(report['defensive_entry']['blocked'], 7)
        self.assertEqual(len(report['defensive_entry']['examples']), tape_scheduler.DEFENSIVE_EXAMPLE_LIMIT)
        self.assertEqual(tape_scheduler.DEFENSIVE_EXAMPLE_LIMIT, 6)

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
        self.assertEqual(report['defensive_entry']['heat_running_leases_kept'], 1)
        self.assertGreaterEqual(report['defensive_entry']['log_only_flags'].get('heat_buy_share_5m', 0), 1)
        # Still hot when its 60 s lease runs out: no new seat, the calm pool gets it.
        later = self.NOW + scheduler.lease_ms + 1_000
        selected, report = scheduler.select({'feed': [dict(flicker, updatedAt=later), dict(other, updatedAt=later)]},
                                            now=later, max_tracked=1)
        self.assertEqual([row['symbol'] for row in selected], [other['symbol']])
        self.assertEqual(report['defensive_entry']['heat_withheld_new_seats'], 1)
        self.assertIn('heat_buy_share_5m', report['defensive_entry']['rejections'])

    def test_live_tape_registry_sidecar_lives_next_to_the_tape_file_and_survives_a_restart(self):
        import live_tape
        self.assertEqual(live_tape._POOL_SCHEDULER.defense.registry.path, live_tape.scheduler_registry_path())
        self.assertEqual(live_tape.scheduler_registry_path().name, f'{live_tape.OUT.stem}.ticker_registry.json')
        self.assertEqual(live_tape.scheduler_registry_path().parent, live_tape.OUT.parent)
        # The tape's registry runs on the wall clock (structural_rug_guard.time) and prunes on
        # load, so every stamp is relative to that clock (a fixed future stamp is pruned 14 days
        # after the wall clock passes it, review finding).
        now = int(guard.time.time() * 1000)
        with tempfile.TemporaryDirectory() as tmp:
            path = live_tape.scheduler_registry_path(Path(tmp) / 'live_tape.json')
            self.assertEqual(path, Path(tmp) / 'live_tape.ticker_registry.json')
            first = TapePoolScheduler(registry_path=path)
            original = self.coin('Wose', symbol='WOSE', pairCreatedAt=now - 10 * DAY, updatedAt=now)
            first.select({'feed': [original]}, now=now, max_tracked=2)
            self.assertTrue(path.exists(), 'saved on the first observation')
            # A restarted tape process remembers the ticker: a relaunch under another mint
            # is a reuse and gets no seat.
            restarted = TapePoolScheduler(registry_path=path)
            self.assertEqual(restarted.defense.registry.status()['load_status'], 'LOADED')
            self.assertEqual(len(restarted.defense.registry), 1)
            relaunch = self.coin('Xyse', symbol='WOSE', pairCreatedAt=now - 2 * DAY, updatedAt=now + MINUTE)
            selected, report = restarted.select({'feed': [relaunch]}, now=now + MINUTE, max_tracked=2)
            self.assertEqual(selected, [])
            self.assertEqual(report['defensive_entry']['rejections'], {'rug_ticker_reuse': 1})
            # A sighting inside the 5-minute save interval is written by the clean-stop flush.
            registry = restarted.defense.registry
            saves = registry.status()['saves']
            restarted.select({'feed': [self.coin('Neww', symbol='NEWT', pairCreatedAt=now - 3 * DAY,
                                                 updatedAt=now + 2 * MINUTE)]},
                             now=now + 2 * MINUTE, max_tracked=2)
            self.assertEqual(registry.status()['saves'], saves, 'not saved inside the interval')
            self.assertEqual(len(guard.TickerRegistry(path)), 2)
            with patch.object(live_tape, '_POOL_SCHEDULER', restarted):
                self.assertTrue(live_tape.flush_scheduler_registry())
                self.assertFalse(live_tape.flush_scheduler_registry(), 'nothing left to save')
            self.assertEqual(len(guard.TickerRegistry(path)), 3)
            with patch.object(live_tape, '_POOL_SCHEDULER', None):
                self.assertFalse(live_tape.flush_scheduler_registry(), 'never raises')


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
        # The defensive seat screen arrived in V5; V6 (LAB_FORWARD_TESTS_V1) keeps it unchanged.
        self.assertEqual(tape_scheduler.PREVIOUS_POLICY_VERSION, 'STABLE_COST_AWARE_TAPE_DISCOVERY_V5_DEFENSIVE_ENTRY')
        self.assertEqual(tape_scheduler.POLICY_VERSION,
                         'STABLE_COST_AWARE_TAPE_DISCOVERY_V6_NO_PINS_FOR_FLOW_FREE_LAB_BOOKS')
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

    def test_the_strategy_lock_records_the_published_definitions(self):
        lock = json.loads((Path(__file__).resolve().parents[1] / 'strategy-lock.json').read_text(encoding='utf-8'))
        defensive = lock['defensive_entry']
        self.assertEqual(defensive['reasons_in_order'], list(entry_defense.REASONS_IN_ORDER))
        structural = defensive['structural_rug_guard']
        self.assertEqual(structural['reasons_in_order'], list(guard.REASONS))
        self.assertEqual(structural['registry_version'], guard.REGISTRY_VERSION)
        self.assertEqual(structural['registry_seed']['version'], guard.REGISTRY_SEED_VERSION)
        self.assertEqual(structural['registry_coverage']['min_coverage_hours'],
                         guard.PARAMS.ticker_registry_min_coverage_minutes / 60)
        self.assertEqual(structural['registry_coverage']['max_gap_minutes'], guard.REGISTRY_MAX_GAP_MS // 60_000)
        seats = lock['tape_seat_policy']['defensive_entry_seats']
        self.assertEqual((seats['rule'], seats['heat_veto_mode']), (tape_scheduler.SEAT_RULE, tape_scheduler.HEAT_SEAT_MODE))
        self.assertEqual(lock['learning']['score_version'], training.LEARNER_SCORE_VERSION)
        self.assertIn('scripts/build_ticker_registry_seed.py', lock['support_files_sha256'])
        coverage = structural['registry_coverage']
        self.assertEqual((coverage['basis_version'], coverage['held_position_sources']),
                         (guard.REGISTRY_COVERAGE_BASIS, sorted(guard.HELD_POSITION_SOURCES)))
        self.assertIn('pair age < 14 days', structural['ticker_input_rule'])
        # Sixth review: the coverage clock, the vouching sighting merge and the seed replay window.
        self.assertEqual((coverage['clock']['version'], coverage['clock']['max_ahead_minutes'] * 60_000),
                         (guard.COVERAGE_CLOCK_VERSION, guard.COVERAGE_MAX_AHEAD_MS))
        self.assertEqual(structural['registry_seed']['vouching_sighting_merge_interval_minutes'] * 60_000,
                         guard.REGISTRY_SIGHTING_MERGE_INTERVAL_MS)
        tool_path = Path(__file__).resolve().parents[1] / 'scripts' / 'build_ticker_registry_seed.py'
        self.assertIn("REPLAY_WINDOW_VERSION = 'SEED_REPLAY_WINDOW_V1'", tool_path.read_text(encoding='utf-8'))
        self.assertEqual(structural['registry_seed']['first_deploy_replay']['version'], 'SEED_REPLAY_WINDOW_V1')
        self.assertIn('defensive_entry_layer.ticker_registry.load_status == LOADED',
                      structural['registry_seed']['verify_after_start'])
        published = guard.config()['registry']
        self.assertEqual((published['seed_version'], published['coverage']['clock']['version']),
                         (guard.REGISTRY_SEED_VERSION, guard.COVERAGE_CLOCK_VERSION))

    def test_owner_decision_on_loss_memory_at_seats_is_recorded_as_accepted(self):
        # Owner decision (1), taken 2026-10-08 by the operator under the owner's delegation:
        # POOL_LOSS_MEMORY_V1 is enforced per ledger at entry, not at shared tape seats.
        import re
        root = Path(__file__).resolve().parents[1]
        lock_text = (root / 'strategy-lock.json').read_text(encoding='utf-8')
        doc_text = (root / 'docs' / 'DEFENSIVE_ENTRY_LAYER.md').read_text(encoding='utf-8')
        accepted = "ACCEPTED 2026-10-08 by the operator under the owner's delegation"
        lock = json.loads(lock_text)
        self.assertTrue(lock['tape_seat_policy']['defensive_entry_seats']['pool_loss_memory_owner_signoff']
                        .startswith(accepted + ': one seat serves every ledger'))
        self.assertIn(accepted, lock['defensive_entry']['pool_loss_memory']['tape_scheduler_scope'])
        pending = re.compile(r"pending (the )?owner|needs the owner|owner'?s sign-off|\"PENDING\"", re.IGNORECASE)
        for name, text in (('strategy-lock.json', lock_text), ('docs/DEFENSIVE_ENTRY_LAYER.md', doc_text)):
            with self.subTest(file=name):
                self.assertIsNone(pending.search(text), name)
                self.assertIn(accepted, text)
        # The behaviour matches the decision: the tape applies no ledger's loss memory.
        self.assertEqual(lock['tape_seat_policy']['defensive_entry_seats']['pool_loss_memory_scope'],
                         'NOT_APPLIED_AT_SEATS_EACH_LEDGER_AT_ITS_OWN_ENTRY')

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

    def test_recorded_policy_refuses_rows_recorded_under_the_layer(self):
        # Review finding: the default replay labelled every journal as predating the layer and
        # skipped it, so a journal recorded after the deploy would admit entries the live engine
        # refused. Engines running the layer stamp coin.scoreVersion (make_coin); a journal
        # with such a row is refused in recorded_policy and needs a labelled counterfactual.
        import main_replay
        from main_replay import MainReplay, RecordedPolicyRefused
        pre = {'observed_at': 10, 'available_at': 10, 'coin': established(10)}
        post = {'observed_at': 20, 'available_at': 20,
                'coin': established(20, scoreVersion=m.SCORE_VERSION, scoreV1=95)}
        self.assertEqual(main_replay.rows_recorded_under_layer([pre, post]), {'rows': 1, 'earliest_at': 20})
        self.assertEqual(main_replay.rows_recorded_under_layer([pre]), {'rows': 0, 'earliest_at': None})
        # make_coin stamps the marker on every observation the engine records.
        pair = {'pairAddress': PAIR, 'dexId': 'pumpswap', 'baseToken': {'symbol': 'TROLL', 'name': 'x'},
                'priceUsd': '0.04', 'liquidity': {'usd': 3_000_000}, 'marketCap': 31_000_000,
                'pairCreatedAt': 1, 'priceChange': {}, 'txns': {}, 'volume': {}}
        self.assertEqual(m.make_coin(MINT, pair, {})[main_replay.LAYER_ERA_COIN_FIELD], m.SCORE_VERSION)
        with tempfile.TemporaryDirectory() as tmp:
            with MainReplay(Path(tmp) / 'recorded') as replay:
                with self.assertRaises(RecordedPolicyRefused) as refused:
                    replay.replay([pre, post])
                self.assertIn('without_layer', str(refused.exception))
                self.assertEqual(replay.replayed, 0, 'refused before any row is ingested')
            with MainReplay(Path(tmp) / 'without', entry_defense='without_layer') as replay:
                decision = replay.monitor.defensive_entry_decision(established(1), 1)
                self.assertTrue(decision['allowed'])
                self.assertEqual(decision['mode'], 'COUNTERFACTUAL_WITHOUT_DEFENSIVE_ENTRY_LAYER_V1')
                result = replay.replay([post])
            defense = result['entry_defense']
            self.assertEqual((defense['mode'], defense['applied'], defense['label']),
                             ('without_layer', False, 'COUNTERFACTUAL_WITHOUT_DEFENSIVE_ENTRY_LAYER_V1'))
            self.assertEqual(defense['rows_recorded_under_layer'], {'rows': 1, 'earliest_at': 20})
            with MainReplay(Path(tmp) / 'pre') as replay:
                result = replay.replay([pre])
            self.assertEqual(result['entry_defense']['label'], 'REPLAY_RECORDED_POLICY_PREDATES_DEFENSIVE_ENTRY_LAYER_V1')
            self.assertEqual(result['entry_defense']['cutover_check'],
                             'RECORDED_POLICY_REFUSES_ROWS_RECORDED_UNDER_THE_LAYER_V1')

    def test_order_flow_adaptive_publishes_that_its_v4_checks_read_the_v2_entry_score(self):
        snapshot = oct4.config_snapshot()
        self.assertEqual(snapshot['decision_filter_version'], 'ORDER_FLOW_BALANCED_V4')
        self.assertEqual(snapshot['entry_score_version'], m.SCORE_VERSION)
        self.assertIn('read the V2 entry score', snapshot['decision_inputs_note'])
        self.assertIn('exit context', snapshot['decision_inputs_note'])


class AccountIsolationTests(unittest.TestCase):
    """The suite never touches a real PAPER account named by the shell's environment."""

    def test_the_paths_are_temporary(self):
        # Other test modules of a discover run may change os.environ after this module was
        # imported; the module globals the tests write through are pinned by setUpModule.
        for path in (m.STATE_PATH, m.AUDIT_PATH, lab.STATE_PATH, lab.COMPACT_PATH, lab.RESET_FLAG_PATH):
            assert_temporary(path)
        with self.assertRaises(AssertionError):
            assert_temporary(Path(_TEMP.name).parent / 'state.json')

    def test_a_shell_account_path_survives_an_engine_test(self):
        # The reviewed reproduction: NEO_MARKET_STATE_PATH set to a stand-in ledger, then an
        # engine test (which deletes and rewrites its state file) runs in a fresh process.
        import subprocess
        import sys
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(prefix='neo-standin-account-') as tmp:
            ledger = Path(tmp) / 'state.json'
            ledger.write_text('{"sentinel": "real PAPER ledger stand-in"}', encoding='utf-8')
            env = dict(os.environ, PYTHONUTF8='1', PYTHONPATH=str(root / 'backend'), NEO_ENGINE_MODE='PAPER',
                       NEO_MARKET_STATE_PATH=str(ledger), NEO_MARKET_AUDIT_PATH=str(Path(tmp) / 'audit.jsonl'))
            test = ('test_defensive_entry_layer.DefaultEnginePathTests.'
                    'test_established_warmed_pool_without_losses_opens_and_records_the_layer')
            result = subprocess.run([sys.executable, '-m', 'unittest', test], cwd=root / 'tests', env=env,
                                    capture_output=True, text=True, timeout=600)
            self.assertEqual(result.returncode, 0, result.stderr[-3000:])
            self.assertEqual(ledger.read_text(encoding='utf-8'), '{"sentinel": "real PAPER ledger stand-in"}')
            self.assertEqual(sorted(path.name for path in Path(tmp).iterdir()), ['state.json'])

    def test_gate_isolated_modules_run_alone_write_nothing_next_to_the_shells_runtime(self):
        # Review finding: modules that patch only defensive_entry_decision still observed through
        # strategy_lab.DEFENSE and live_tape._POOL_SCHEDULER, whose registries live next to
        # NEO_STRATEGY_LAB_PATH / NEO_LIVE_TAPE_PATH. Run alone (PYTHONPATH=backend, without
        # scripts/run_python_checks.py) they wrote strategy_lab.ticker_registry.json with the fixed
        # test clock (January 2027) and fixture sightings, or live_tape.ticker_registry.json, next to
        # a shell's runtime. Every module that isolates the layer runs here in its own process with
        # the state paths pointing at a sentinel directory, which must stay empty.
        import subprocess
        import sys
        root = Path(__file__).resolve().parents[1]
        modules = []
        for suite in ('tests', 'backend/tests'):
            for path in sorted((root / suite).glob('test_*.py')):
                if path.resolve() != Path(__file__).resolve() and 'def _defensive_pass(' in path.read_text(
                        encoding='utf-8'):
                    modules.append((suite, path.stem))
        self.assertIn(('tests', 'test_funded_candidate_alignment'), modules)
        self.assertIn(('tests', 'test_tape_execution_repair'), modules)
        self.assertIn(('backend/tests', 'test_momentum_rush_brain'), modules)
        for suite, module in modules:
            with self.subTest(module=f'{suite}/{module}'), \
                    tempfile.TemporaryDirectory(prefix='neo-sentinel-runtime-') as tmp:
                sentinel = Path(tmp)
                env = dict(os.environ, PYTHONUTF8='1', PYTHONPATH=str(root / 'backend'), NEO_ENGINE_MODE='PAPER',
                           NEO_MARKET_STATE_PATH=str(sentinel / 'state.json'),
                           NEO_MAIN_MARKET_STATE_PATH=str(sentinel / 'state.json'),
                           NEO_STRATEGY_LAB_PATH=str(sentinel / 'strategy_lab.json'),
                           NEO_LIVE_TAPE_PATH=str(sentinel / 'live_tape.json'))
                result = subprocess.run([sys.executable, '-m', 'unittest', module], cwd=root / suite, env=env,
                                        capture_output=True, text=True, timeout=600)
                self.assertEqual(result.returncode, 0, result.stderr[-3000:])
                self.assertEqual(sorted(str(path.relative_to(sentinel)) for path in sentinel.rglob('*')), [])


if __name__ == '__main__':
    unittest.main()
