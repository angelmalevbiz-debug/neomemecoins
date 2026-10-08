"""LAB_FORWARD_TESTS_V1: four PAPER Strategy Lab books forward-testing two pre-registered hypotheses.

PAPER only and offline. Realistic fixtures come from the research scan log
(tests/fixtures/lab_forward_obs_20261008.json, extracted read-only from
obs.sqlite3 table o with the frozen research signals; mint and pool abbreviated
to 8 characters and padded to a valid length where the Lab needs one). Prices,
safety and marks are patched; no test calls an external API or touches a real
ledger. Nothing here measures or claims profitability.
"""
import copy
import dataclasses
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

_TEMP = tempfile.TemporaryDirectory(prefix='neo-lab-forward-tests-')
for _name, _file in (('NEO_MARKET_STATE_PATH', 'state.json'), ('NEO_MARKET_AUDIT_PATH', 'audit.jsonl'),
                     ('NEO_LIVE_TAPE_PATH', 'tape.json'), ('NEO_STRATEGY_LAB_PATH', 'strategy_lab.json'),
                     ('NEO_STRATEGY_LAB_COMPACT_PATH', 'strategy_lab_compact.json'),
                     ('NEO_STRATEGY_LAB_RESET_FLAG', 'strategy_lab.reset'), ('NEO_RISK_CACHE_DIR', 'risk'),
                     ('NEO_PRICE_CHECK_DIR', 'price-check'), ('NEO_TRAINING_ROOT', 'training')):
    os.environ[_name] = str(Path(_TEMP.name) / _file)
os.environ.pop('NEO_MAIN_MARKET_STATE_PATH', None)

import entry_defense
import heat_veto
import lab_activity
import lab_dashboard_projection
import lab_forward_tests as lf
import lab_strategy_lifecycle as lifecycle
import paper_market_feasibility as feasibility
import strategy_lab as lab
import structural_rug_guard as guard
import tape_pool_scheduler

_MODULE_PATCHES = []


def setUpModule():
    """Pin the Lab ledger paths (a discover run may have imported strategy_lab first)."""
    for name, file in (('STATE_PATH', 'strategy_lab.json'), ('COMPACT_PATH', 'strategy_lab_compact.json'),
                       ('RESET_FLAG_PATH', 'strategy_lab.reset')):
        pinned = patch.object(lab, name, Path(_TEMP.name) / file)
        pinned.start()
        _MODULE_PATCHES.append(pinned)


def tearDownModule():
    while _MODULE_PATCHES:
        _MODULE_PATCHES.pop().stop()


FIXTURES = json.loads((Path(__file__).resolve().parent / 'fixtures' / 'lab_forward_obs_20261008.json')
                      .read_text(encoding='utf-8'))
SOL = feasibility.SOL_QUOTE_MINT
MINUTE = 60_000
HOUR = 60 * MINUTE
DAY = 24 * HOUR
# Frozen config hashes (sha256 of each book's canonical parameter JSON). A parameter
# change must change these pins, strategy-lock.json and docs/STRATEGY_VALIDATION.md together.
PINNED_CONFIG_HASHES = {
    'LAB_A_SURGE_EST_GUARD': '883e08b8379fe319e7c5d783eb821d35938911f2c29c500665f5dd499bc6cc34',
    'RND_LAB_A': '8be0ce70a1bc1247b326063f912f7ec917da70258e34ad63b686846b7614d5c1',
    'LAB_B_DIP_MKTDIP_GUARD': '08e20572c482bd44e2d2092e600e81579a6bbcac3890906deba99752ed85cfd8',
    'RND_LAB_B': '76ef3c3083bede7b02ce301d010f17179e12c51e10fce9059bc94eb5030872c9',
}


def full(short):
    """A valid-length base58 identity from an 8-character research abbreviation."""
    return short + '1' * (44 - len(short))


def feed_coin(point, **changes):
    coin = copy.deepcopy(point)
    coin['address'], coin['pairAddress'] = full(coin['address']), full(coin['pairAddress'])
    coin.setdefault('name', coin.get('symbol'))
    coin.update(changes)
    return coin


def covered_registry(now, hours=48):
    """An empty ticker registry that has watched the market without a gap up to ``now``."""
    registry = guard.TickerRegistry()
    stamp = now - hours * HOUR
    while stamp < now:
        registry.mark_observed(stamp)
        stamp += 30 * MINUTE
    registry.mark_observed(now)
    return registry


def pool(mint, pair, price, liquidity, stamp, **changes):
    coin = {'address': mint, 'pairAddress': pair, 'dexId': 'pumpswap', 'quoteTokenAddress': SOL,
            'priceUsd': price, 'liquidityUsd': liquidity, 'updatedAt': stamp}
    coin.update(changes)
    return coin


def observe(history, memory, coins):
    """Feed observations in time order to the PairHistory and the forward memory."""
    for coin in sorted(coins, key=lambda c: c['updatedAt']):
        history.observe_coin(coin, coin['updatedAt'])
        memory.observe_coin(coin, coin['updatedAt'])


def regime_inputs():
    dip = FIXTURES['lab_b_dip']
    coins = []
    for row in dip['regime_pools']:
        for point in (row['ref'], row['now']):
            if point:
                coins.append(pool(row['mint'], row['pair'], point[1], point[2], point[0]))
    return dip, coins


def closes(book_id, values, *, pairs=6, start=1_800_000_000_000, config_hash=None, version=lf.VERSION):
    """Closed forward-test rows (newest first, as a ledger stores them) with the given net50 $ values."""
    rows = []
    for index, value in enumerate(values):
        mint = full(f'M{index % pairs}x')
        rows.append({'trade_no': index + 1, 'strategy_id': book_id, 'address': mint,
                     'pairAddress': full(f'P{index % pairs}x'), 'opened_at': start + index * 20 * MINUTE,
                     'closed_at': start + index * 20 * MINUTE + 10 * MINUTE, 'pnl_usd': value + 1.0,
                     'net50_usd': value, 'net50_pct': value / 2, 'notional_usd': 200.0,
                     'lab_forward_version': version,
                     'lab_config_hash': config_hash or lf.CONFIG_HASHES[book_id]})
    return list(reversed(rows))


# ------------------------------------------------------------------ definitions

class DefinitionTests(unittest.TestCase):
    def test_four_isolated_test_books_are_registered(self):
        ids = [s['id'] for s in lab.STRATEGIES]
        for book_id in lf.BOOK_IDS:
            with self.subTest(book=book_id):
                self.assertIn(book_id, ids)
                self.assertIn(book_id, lab_activity.RULES)
                book = lab.empty_book(next(s for s in lab.STRATEGIES if s['id'] == book_id))
                self.assertEqual((book['starting_balance'], book['balance']), (500.0, 500.0))
                self.assertEqual(book['portfolio_group'], 'TEST')
                self.assertTrue(lab.heat_log_only_book(book_id))
        self.assertEqual(set(lab_activity.RULES), set(ids))
        self.assertEqual(lf.CONTROL_OF, {'LAB_A_SURGE_EST_GUARD': 'RND_LAB_A',
                                         'LAB_B_DIP_MKTDIP_GUARD': 'RND_LAB_B'})
        # heat_veto keeps reserving the two hypothesis arms (engine config hashes unchanged);
        # the forward-test module adds their random controls.
        self.assertEqual(heat_veto.LOG_ONLY_BOOK_IDS, {'LAB_A_SURGE_EST_GUARD', 'LAB_B_DIP_MKTDIP_GUARD'})
        self.assertTrue(heat_veto.LOG_ONLY_BOOK_IDS <= lf.HEAT_LOG_ONLY_BOOK_IDS)
        self.assertFalse(lab.heat_log_only_book('DEEP_LIQ_MOMENTUM'))
        self.assertFalse(lf.AUTOMATIC_PROMOTION)

    def test_parameters_are_the_pre_registered_research_values(self):
        self.assertEqual((lf.SURGE.min_buys_5m, lf.SURGE.min_buys_1h_exclusive, lf.SURGE.pace_multiple,
                          lf.SURGE.hourly_pace_divisor), (30.0, 0.0, 3.0, 12.0))
        self.assertEqual((lf.UNIVERSE_A.min_liquidity_usd, lf.UNIVERSE_A.max_fee_tier_bps), (50_000.0, 95.0))
        self.assertEqual((lf.UNIVERSE_B.min_liquidity_usd, lf.UNIVERSE_B.max_fee_tier_bps), (50_000.0, None))
        self.assertEqual((lf.DIP.lookback_seconds, lf.DIP.max_return_pct, lf.DIP.min_liquidity_ratio,
                          lf.DIP.min_change_24h_pct_exclusive, lf.DIP.max_regime_med15_pct_exclusive),
                         (900, -10.0, 0.85, -50.0, -0.2))
        self.assertEqual((lf.REGIME.grid_ms, lf.REGIME.active_within_ms, lf.REGIME.min_liquidity_usd,
                          lf.REGIME.lookback_ms, lf.REGIME.max_reference_staleness_ms, lf.REGIME.min_pools),
                         (60_000, 180_000, 20_000.0, 900_000, 600_000, 8))
        self.assertEqual((lf.RANDOM_A.probability, lf.RANDOM_A.salt), (0.0005, 'synA'))
        self.assertEqual((lf.RANDOM_B.probability, lf.RANDOM_B.salt), (0.0007, 'synB'))
        for book_id, (stop, take, hold) in ((lf.LAB_A_ID, (5.0, 10.0, 60.0)), (lf.RND_A_ID, (5.0, 10.0, 60.0)),
                                            (lf.LAB_B_ID, (15.0, 20.0, 60.0)), (lf.RND_B_ID, (15.0, 20.0, 60.0))):
            exits = lf.EXITS[book_id]
            self.assertEqual((exits.stop_loss_net_pct, exits.take_profit_net_pct, exits.max_hold_minutes,
                              exits.pool_cooldown_seconds), (stop, take, hold, 300))
        self.assertEqual(lf.NOTIONAL_USD, 200.0)
        self.assertEqual(lf.EXECUTION_MODEL, lab.EXECUTION_MODEL_VERSION)
        # The Lab's one admission rule (0.5 x net stop, bounded by the 2.75% model ceiling) on each own stop.
        self.assertEqual(lf.admission_cost_cap_pct(lf.LAB_A_ID), 2.5)
        self.assertEqual(lf.admission_cost_cap_pct(lf.LAB_B_ID), lab_activity.MAX_ENTRY_COST_PCT)
        self.assertEqual(lab.lab_cost_cap_pct(), 1.5, 'every other book keeps its cap')

    def test_config_hash_is_frozen_canonical_and_sensitive(self):
        self.assertEqual(lf.CONFIG_HASHES, PINNED_CONFIG_HASHES)
        for book_id in lf.BOOK_IDS:
            with self.subTest(book=book_id):
                canonical = json.dumps(lf.book_parameters(book_id), sort_keys=True, separators=(',', ':'))
                self.assertEqual(hashlib.sha256(canonical.encode()).hexdigest(), lf.config_hash(book_id))
                self.assertEqual(lf.config_hash(book_id), lf.config_hash(book_id), 'stable across calls')
        self.assertEqual(len(set(lf.CONFIG_HASHES.values())), 4)
        with patch.dict(lf.EXITS, {lf.LAB_A_ID: lf.ExitParameters('PROBE', 5.0, 11.0, 60.0)}):
            self.assertNotEqual(lf.config_hash(lf.LAB_A_ID), PINNED_CONFIG_HASHES[lf.LAB_A_ID])
        with patch.object(lf, 'RANDOM', {**lf.RANDOM, lf.RND_B_ID: lf.RandomParameters(0.0008, 'synB')}):
            self.assertNotEqual(lf.config_hash(lf.RND_B_ID), PINNED_CONFIG_HASHES[lf.RND_B_ID])
        with patch.object(heat_veto, 'VERSION', 'HEAT_VETO_PROBE'):
            self.assertNotEqual(lf.config_hash(lf.LAB_B_ID), PINNED_CONFIG_HASHES[lf.LAB_B_ID])
        # The env-resolved Lab cost model is part of the frozen config: a changed knob is a new test.
        for field, value in (('base_slippage_bps', 15.0), ('latency_buffer_bps', 12.0),
                             ('network_fee_sol', 0.0002), ('max_price_impact_pct', 25.0),
                             ('generic_dex_fee_bps', 35.0)):
            with self.subTest(cost_model=field), \
                    patch.object(lf, 'COST_MODEL', dataclasses.replace(lf.COST_MODEL, **{field: value})):
                self.assertNotEqual(lf.config_hash(lf.RND_A_ID), PINNED_CONFIG_HASHES[lf.RND_A_ID])
        with patch.object(lf, 'COST_MODEL', dataclasses.replace(lf.COST_MODEL, base_slippage_bps=float('nan'))):
            self.assertEqual(lf.book_parameters(lf.LAB_A_ID)['costs']['model']['base_slippage_bps'], 'nan')
        with patch.object(lf, 'CLOSE_POLICY', dataclasses.replace(lf.CLOSE_POLICY, vanish_haircut_pct=5.0)):
            self.assertNotEqual(lf.config_hash(lf.LAB_A_ID), PINNED_CONFIG_HASHES[lf.LAB_A_ID])
        with patch.object(lf, 'CASH', dataclasses.replace(lf.CASH, network_fee_reserve_usd=1.0)):
            self.assertNotEqual(lf.config_hash(lf.LAB_B_ID), PINNED_CONFIG_HASHES[lf.LAB_B_ID])
        # LAB_FORWARD_SIGNAL_CARRY_V1 and LAB_FORWARD_CONTROL_CONTINUITY_V1 are part of every book's test.
        with patch.object(lf, 'SIGNAL_CARRY', dataclasses.replace(lf.SIGNAL_CARRY, max_age_ms=30_000)):
            for book_id in lf.BOOK_IDS:
                self.assertNotEqual(lf.config_hash(book_id), PINNED_CONFIG_HASHES[book_id])
        with patch.object(lf, 'CONTROL_CONTINUITY', dataclasses.replace(lf.CONTROL_CONTINUITY, version='PROBE')):
            for book_id in lf.BOOK_IDS:
                self.assertNotEqual(lf.config_hash(book_id), PINNED_CONFIG_HASHES[book_id])
        for book_id in lf.BOOK_IDS:
            parameters = lf.book_parameters(book_id)
            self.assertEqual(parameters['signal_carry']['max_age_ms'], 60_000)
            self.assertEqual(parameters['signal_carry']['carried_blocker'], 'price_crosscheck_pending')
            self.assertEqual(parameters['control_continuity']['version'], lf.CONTROL_CONTINUITY_VERSION)
            self.assertEqual(parameters['size']['start_balance_usd'], 500.0)

    def test_the_hashed_cost_model_is_the_labs_resolved_model(self):
        self.assertEqual(lf.cost_model_mismatches(**lab.forward_cost_model()), [])
        self.assertEqual(dataclasses.asdict(lf.COST_MODEL), lab.forward_cost_model())
        self.assertEqual((lf.COST_MODEL.base_slippage_bps, lf.COST_MODEL.latency_buffer_bps,
                          lf.COST_MODEL.network_fee_sol, lf.COST_MODEL.max_price_impact_pct,
                          lf.COST_MODEL.generic_dex_fee_bps), (10.0, 10.0, 0.0001, 20.0, 30.0))
        with patch.object(lab, 'LATENCY_BUFFER_BPS', 25.0):
            self.assertEqual(lf.cost_model_mismatches(**lab.forward_cost_model()), ['latency_buffer_bps'])
        self.assertEqual(lf.cost_model_mismatches(base_slippage_bps=float('nan')), ['base_slippage_bps'])

    def test_the_strategy_lock_records_the_books(self):
        lock = json.loads((Path(__file__).resolve().parents[1] / 'strategy-lock.json').read_text(encoding='utf-8'))
        section = lock['lab_forward_tests']
        self.assertEqual(section['version'], lf.VERSION)
        self.assertEqual(section['config_hashes'], PINNED_CONFIG_HASHES)
        self.assertEqual(section['kill_rule']['version'], lf.KILL_RULE_VERSION)
        self.assertEqual(section['heat_log_only_book_ids'], sorted(lf.HEAT_LOG_ONLY_BOOK_IDS))
        self.assertEqual(section['research_prereg_sha256'], lf.RESEARCH_PREREG_SHA256)
        self.assertIn('backend/lab_forward_tests.py', lock['support_files_sha256'])
        self.assertTrue(lock['defensive_entry']['heat_veto']['log_only_books_registered'])
        for book_id, row in section['books'].items():
            self.assertEqual(row['config_hash'], PINNED_CONFIG_HASHES[book_id])
        self.assertEqual(section['cost_model'], lf._hashable(dataclasses.asdict(lf.COST_MODEL)))
        self.assertEqual(section['close_policy'], dataclasses.asdict(lf.CLOSE_POLICY))
        self.assertEqual(section['cash_state'], dataclasses.asdict(lf.CASH))
        self.assertEqual(section['control_continuity'], dataclasses.asdict(lf.CONTROL_CONTINUITY))
        self.assertEqual(section['signal_carry'], dataclasses.asdict(lf.SIGNAL_CARRY))
        self.assertEqual(section['known_versions'], list(lf.KNOWN_VERSIONS))
        self.assertFalse(section['tape_pin_required'])
        self.assertEqual(section['promotion_gate']['min_control_coverage'], 0.9)
        seats = lock['tape_seat_policy']
        self.assertEqual((seats['version'], seats['previous_version']),
                         (tape_pool_scheduler.POLICY_VERSION, tape_pool_scheduler.PREVIOUS_POLICY_VERSION))
        self.assertFalse(seats['flow_free_lab_positions_pinned'])
        self.assertEqual(lock['tape_decoder']['seat_shedding_policy_version'], tape_pool_scheduler.POLICY_VERSION)


# ------------------------------------------------------------------ LAB_A signal

class SurgeSignalTests(unittest.TestCase):
    NOW = 1_800_000_000_000

    def setUp(self):
        self.history = heat_veto.PairHistory()
        self.memory = lf.ForwardFeedMemory()

    def coin(self, stamp, buys_5m, buys_1h, **changes):
        coin = pool('MintA', 'PoolA', 0.01, 300_000.0, stamp, marketCap=10_000_000.0,
                    priceNative=0.01 / 120.0, txns={'m5': {'buys': buys_5m, 'sells': 5},
                                                    'h1': {'buys': buys_1h, 'sells': 50}})
        coin.update(changes)
        return coin

    def evaluate(self, coin, book_id=lf.LAB_A_ID):
        return lf.evaluate(book_id, coin, coin['updatedAt'], self.memory, self.history)

    def test_research_fresh_crossings_fire_once(self):
        for crossing in FIXTURES['lab_a_crossings']:
            with self.subTest(pool=crossing['name']):
                history, memory = heat_veto.PairHistory(), lf.ForwardFeedMemory()
                outcomes = []
                for point in crossing['points']:
                    coin = feed_coin(point)
                    self.assertEqual(lf.surge_state(coin), crossing['research_surge'][len(outcomes)])
                    history.observe_coin(coin, coin['updatedAt'])
                    memory.observe_coin(coin, coin['updatedAt'])
                    outcomes.append(lf.evaluate(lf.LAB_A_ID, coin, coin['updatedAt'], memory, history))
                self.assertEqual([o['universe_rejections'] for o in outcomes], [[], [], []])
                self.assertEqual(outcomes[0]['signal_rejections'][:1], ['lab_a_no_surge'])
                self.assertTrue(outcomes[1]['matched'], outcomes[1])
                self.assertEqual(outcomes[1]['signal']['previous_surge'], False)
                # The next refresh repeats the same counts: the surge is no longer fresh.
                self.assertEqual(outcomes[2]['signal_rejections'], ['lab_a_surge_not_fresh'])

    def test_crafted_surge_rules(self):
        cases = ((30, 120, True), (29, 12, False), (30, 0, False), (40, 200, False), (50, 200, True))
        for buys_5m, buys_1h, expected in cases:
            with self.subTest(m5=buys_5m, h1=buys_1h):
                self.assertEqual(lf.surge_state(self.coin(self.NOW, buys_5m, buys_1h)), expected)
        self.assertFalse(lf.surge_state({'txns': {'m5': {'buys': 90}}}), 'missing h1 count is not a surge')
        self.assertFalse(lf.surge_state({'txns': 'malformed'}))

    def test_first_observation_and_repeated_observations(self):
        first = self.coin(self.NOW, 60, 120)
        observe(self.history, self.memory, [first])
        self.assertEqual(self.evaluate(first)['signal_rejections'], ['lab_a_no_previous_observation'])
        quiet = self.coin(self.NOW + 5_000, 10, 120)
        surge = self.coin(self.NOW + 10_000, 60, 130)
        observe(self.history, self.memory, [quiet, surge])
        self.assertTrue(self.evaluate(surge)['matched'])
        # The same observation served again (same updatedAt) is still the fresh crossing
        # against its previous distinct observation; it is never compared with itself.
        self.memory.observe_coin(surge, surge['updatedAt'] + 2_000)
        self.assertTrue(self.evaluate(surge)['matched'])
        later = self.coin(self.NOW + 15_000, 61, 131)
        observe(self.history, self.memory, [later])
        self.assertEqual(self.evaluate(later)['signal_rejections'], ['lab_a_surge_not_fresh'])

    def test_universe_rejections(self):
        base = self.coin(self.NOW, 60, 120)
        self.assertEqual(lf.universe_rejections(lf.LAB_A_ID, base), [])
        self.assertEqual(lf.universe_rejections(lf.LAB_A_ID, dict(base, dexId='raydium')), ['lab_dex_not_pumpswap'])
        self.assertEqual(lf.universe_rejections(lf.LAB_A_ID, dict(base, quoteTokenAddress='USDC')),
                         ['lab_quote_not_sol'])
        self.assertEqual(lf.universe_rejections(lf.LAB_A_ID, dict(base, liquidityUsd=49_999.0)),
                         ['lab_liquidity_below_minimum'])
        # 1,000,000 USD market cap at 120 USD/SOL is the 100 bps tier: outside LAB_A, inside LAB_B.
        small = dict(base, marketCap=1_000_000.0)
        self.assertEqual(feasibility.pumpswap_fee_bps(small), 100.0)
        self.assertEqual(lf.universe_rejections(lf.LAB_A_ID, small), ['lab_fee_tier_above_maximum'])
        self.assertEqual(lf.universe_rejections(lf.RND_A_ID, small), ['lab_fee_tier_above_maximum'])
        self.assertEqual(lf.universe_rejections(lf.LAB_B_ID, small), [])
        self.assertEqual(lf.universe_rejections(lf.RND_B_ID, small), [])
        # An unknown denomination is the 125 bps tier, never the cheapest.
        self.assertEqual(lf.universe_rejections(lf.LAB_A_ID, dict(base, priceNative=None)),
                         ['lab_fee_tier_above_maximum'])

    def test_memory_is_bounded_and_ignores_other_pools(self):
        memory = lf.ForwardFeedMemory(lf.MemoryParameters(max_pairs=3))
        for index in range(5):
            memory.observe_coin(pool(f'm{index}', f'p{index}', 1.0, 60_000.0, self.NOW), self.NOW)
        self.assertEqual(len(memory.keys()), 3)
        self.assertFalse(memory.observe_coin(pool('x', 'y', 1.0, 1.0, self.NOW, dexId='raydium'), self.NOW))
        self.assertFalse(memory.observe_coin(pool('x', 'y', 1.0, 1.0, self.NOW, quoteTokenAddress='USDC'), self.NOW))
        # Same observation set as PairHistory: an observation without a positive price is skipped.
        self.assertFalse(memory.observe_coin(pool('m4', 'p4', None, 70_000.0, self.NOW + 1), self.NOW + 1))
        self.assertFalse(heat_veto.PairHistory().observe_coin(pool('m4', 'p4', None, 70_000.0, self.NOW + 1),
                                                              self.NOW + 1))
        self.assertEqual(memory.liquidity_at(('m4', 'p4'), self.NOW + 1), (True, 60_000.0))
        self.assertEqual(memory.prune(self.NOW + 2 * HOUR), 3)


# ------------------------------------------------------------------ LAB_B regime and signal

class RegimeTests(unittest.TestCase):
    T = 1_800_000_000_000 - (1_800_000_000_000 % MINUTE)

    def setUp(self):
        self.history = heat_veto.PairHistory()
        self.memory = lf.ForwardFeedMemory()

    def market(self, changes, *, liquidity=50_000.0, ref_age_s=905, now_age_s=30):
        coins = []
        for index, change in enumerate(changes):
            coins.append(pool(f'm{index}', f'p{index}', 1.0, liquidity, self.T - ref_age_s * 1000))
            coins.append(pool(f'm{index}', f'p{index}', 1.0 + change / 100, liquidity, self.T - now_age_s * 1000))
        observe(self.history, self.memory, coins)

    def test_research_regime_is_reproduced_from_the_feed(self):
        dip, coins = regime_inputs()
        observe(self.history, self.memory, coins)
        regime = self.memory.regime(dip['minute_T'], self.history)
        self.assertEqual(regime['pools'], dip['research_counted_pools'])
        self.assertAlmostEqual(regime['med15_pct'], dip['research_med15_pct'], places=9)
        self.assertTrue(regime['dipping'])
        # Any time inside the minute reads the same whole-minute grid point.
        self.assertEqual(self.memory.regime(dip['minute_T'] + 59_999, self.history), regime)

    def test_median_threshold_and_minimum_pools(self):
        self.market([-1, -0.5, -0.3, -0.25, -0.2, 0.0, 0.5, 1.0])
        regime = self.memory.regime(self.T, self.history)
        self.assertEqual(regime['pools'], 8)
        self.assertAlmostEqual(regime['med15_pct'], -0.225, places=9)
        self.assertTrue(regime['dipping'])
        history, memory = heat_veto.PairHistory(), lf.ForwardFeedMemory()
        self.history, self.memory = history, memory
        self.market([-1] * 7)
        regime = self.memory.regime(self.T, self.history)
        self.assertEqual((regime['pools'], regime['med15_pct'], regime['dipping']), (7, None, False))

    def test_activity_liquidity_and_staleness_rules(self):
        self.market([-1] * 8)                                               # counted
        observe(self.history, self.memory, [
            pool('stale_ref', 'q1', 1.0, 50_000.0, self.T - 1_501_000),   # reference 10 min + 1 s stale
            pool('stale_ref', 'q1', 0.5, 50_000.0, self.T - 10_000),
            pool('quiet', 'q2', 1.0, 50_000.0, self.T - 905_000),           # last print 181 s before T
            pool('quiet', 'q2', 0.5, 50_000.0, self.T - 181_000),
            pool('thin', 'q3', 1.0, 19_999.0, self.T - 905_000),            # under $20k at T
            pool('thin', 'q3', 0.5, 19_999.0, self.T - 10_000),
            pool('late', 'q4', 1.0, 50_000.0, self.T - 899_000),            # no point at or before T - 15 min
            pool('late', 'q4', 0.5, 50_000.0, self.T - 10_000),
            pool('after', 'q5', 1.0, 50_000.0, self.T - 905_000),           # only prints after T count later
            pool('after', 'q5', 0.5, 50_000.0, self.T + 5_000),
            pool('ray', 'q6', 1.0, 50_000.0, self.T - 905_000, dexId='raydium'),
            pool('ray', 'q6', 0.5, 50_000.0, self.T - 10_000, dexId='raydium'),
        ])
        regime = self.memory.regime(self.T, self.history)
        self.assertEqual(regime['pools'], 8)
        self.assertAlmostEqual(regime['med15_pct'], -1.0, places=9)

    def test_reference_inside_a_feed_gap_and_inside_a_segment(self):
        # Observed every 60 s around T - 15 min (contiguous): the reference is the price in
        # effect then (2.0). A pool absent from T - 20 min to T - 5 min has its reference in
        # that feed gap, 5 min stale (<= 10 min): counted with its pre-gap price (1.0).
        coins = []
        for index in range(8):
            for back in range(1_200, 0, -60):
                price = 2.0 if back >= 900 else 1.0
                coins.append(pool(f'c{index}', f'c{index}', price, 50_000.0, self.T - back * 1000))
        coins.append(pool('gap', 'gap', 1.0, 50_000.0, self.T - 1_200_000))
        coins.append(pool('gap', 'gap', 0.9, 50_000.0, self.T - 300_000))
        coins.append(pool('gap', 'gap', 0.5, 50_000.0, self.T - 60_000))
        observe(self.history, self.memory, coins)
        regime = self.memory.regime(self.T, self.history)
        self.assertEqual(regime['pools'], 9)
        self.assertAlmostEqual(regime['med15_pct'], -50.0, places=9)


class DipSignalTests(unittest.TestCase):
    def setUp(self):
        self.history = heat_veto.PairHistory()
        self.memory = lf.ForwardFeedMemory()
        self.dip, coins = regime_inputs()
        self.reference = feed_coin(self.dip['reference'])
        self.decision = feed_coin(self.dip['decision'])
        observe(self.history, self.memory, coins + [self.reference])

    def evaluate(self, coin, book_id=lf.LAB_B_ID):
        observe(self.history, self.memory, [coin])
        return lf.evaluate(book_id, coin, coin['updatedAt'], self.memory, self.history)

    def test_research_dip_during_a_market_dip_fires(self):
        result = self.evaluate(self.decision)
        self.assertTrue(result['matched'], result)
        signal = result['signal']
        self.assertAlmostEqual(signal['return_15m_pct'], (0.001112 / 0.001236 - 1) * 100, places=9)
        self.assertGreaterEqual(signal['liquidity_ratio_15m'], 0.85)
        self.assertAlmostEqual(signal['regime']['med15_pct'], self.dip['research_med15_pct'], places=9)

    def test_each_dip_condition_is_required(self):
        cases = (({'priceUsd': 0.001236 * 0.901}, 'lab_b_no_dip'),
                 ({'liquidityUsd': 122_412.28 * 0.84}, 'lab_b_liquidity_fell'),
                 ({'priceChange': {'h24': -50.0}}, 'lab_b_change_24h'),
                 ({'priceChange': {}}, 'lab_b_change_24h'))
        for changes, reason in cases:
            with self.subTest(reason=reason, changes=changes):
                coin = dict(self.decision, **changes)
                result = lf.evaluate(lf.LAB_B_ID, coin, coin['updatedAt'], self.memory, self.history)
                self.assertEqual(result['signal_rejections'], [reason])

    def test_no_reference_and_no_regime(self):
        history, memory = heat_veto.PairHistory(), lf.ForwardFeedMemory()
        observe(history, memory, [self.decision])
        result = lf.evaluate(lf.LAB_B_ID, self.decision, self.decision['updatedAt'], memory, history)
        self.assertEqual(result['signal_rejections'], ['lab_b_no_reference'])
        observe(history, memory, [self.reference])  # out of order: ignored, never a look-back rewrite
        result = lf.evaluate(lf.LAB_B_ID, self.decision, self.decision['updatedAt'], memory, history)
        self.assertEqual(result['signal_rejections'], ['lab_b_no_reference'])
        history, memory = heat_veto.PairHistory(), lf.ForwardFeedMemory()
        observe(history, memory, [self.reference, self.decision])
        result = lf.evaluate(lf.LAB_B_ID, self.decision, self.decision['updatedAt'], memory, history)
        self.assertEqual(result['signal_rejections'], ['lab_b_regime_unavailable'])

    def test_a_market_that_is_not_dipping_blocks_the_signal(self):
        history, memory = heat_veto.PairHistory(), lf.ForwardFeedMemory()
        T = self.dip['minute_T']
        coins = [self.reference]
        for index in range(10):
            coins.append(pool(f'u{index}', f'u{index}', 1.0, 50_000.0, T - 905_000))
            coins.append(pool(f'u{index}', f'u{index}', 1.001, 50_000.0, T - 20_000))
        observe(history, memory, coins + [self.decision])
        result = lf.evaluate(lf.LAB_B_ID, self.decision, self.decision['updatedAt'], memory, history)
        self.assertEqual(result['signal_rejections'], ['lab_b_market_not_dipping'])
        self.assertAlmostEqual(result['signal']['regime']['med15_pct'], 0.1, places=9)


# ------------------------------------------------------------------ random controls

class RandomControlTests(unittest.TestCase):
    def test_hashed_coin_reproduces_the_research_draws(self):
        for draw in FIXTURES['random_draws']:
            with self.subTest(salt=draw['salt'], pair=draw['pair'][:1]):
                fired = [draw['t0'] + k * draw['step_ms'] for k in range(draw['points'])
                         if lf.hashed_coin(draw['pair'], draw['t0'] + k * draw['step_ms'],
                                           draw['probability'], draw['salt'])]
                self.assertEqual(fired, draw['firing_t'])

    def test_draw_rate_and_determinism(self):
        draws = [lf.hashed_coin('Z' * 44, 1_800_000_000_000 + k * 1000, 0.0007, 'synB') for k in range(200_000)]
        self.assertEqual(draws, [lf.hashed_coin('Z' * 44, 1_800_000_000_000 + k * 1000, 0.0007, 'synB')
                                 for k in range(200_000)])
        rate = sum(draws) / len(draws)
        self.assertGreater(rate, 0.0007 * 0.7)
        self.assertLess(rate, 0.0007 * 1.3)
        self.assertFalse(lf.hashed_coin('Z' * 44, 1, 0.0, 'synA'))
        self.assertTrue(lf.hashed_coin('Z' * 44, 1, 1.0, 'synA'))

    def test_controls_draw_per_observation_in_their_universe(self):
        draw = next(d for d in FIXTURES['random_draws'] if d['salt'] == 'synB' and d['firing_t'])
        stamp = draw['firing_t'][0]
        coin = pool('m' * 44, draw['pair'], 0.01, 300_000.0, stamp, marketCap=1_000_000.0, priceNative=0.01 / 120)
        memory, history = lf.ForwardFeedMemory(), heat_veto.PairHistory()
        result = lf.evaluate(lf.RND_B_ID, coin, stamp, memory, history)
        # No history, no dip, no regime: the control needs none of them.
        self.assertTrue(result['matched'], result)
        self.assertEqual(result['signal'], {'kind': 'random', 'probability': 0.0007, 'salt': 'synB', 'drawn': True})
        self.assertEqual(lf.evaluate(lf.RND_B_ID, dict(coin, updatedAt=stamp + 1), stamp + 1, memory,
                                     history)['signal_rejections'],
                         [] if lf.hashed_coin(draw['pair'], stamp + 1, 0.0007, 'synB') else ['rnd_coin_not_drawn'])
        # The same pool in the 100 bps tier is outside RND_LAB_A's fee universe.
        self.assertEqual(lf.evaluate(lf.RND_A_ID, coin, stamp, memory, history)['universe_rejections'],
                         ['lab_fee_tier_above_maximum'])


# ------------------------------------------------------------------ costs and net50

class CostTests(unittest.TestCase):
    def test_calibration_per_leg(self):
        fixed = 1e4 * (0.185 / 2 + (0.03 - 0.012)) / 200
        cases = ((30.0, 300_000, 22.0, 0.0), (50.0, 300_000, 22.0, 0.0), (52.5, 300_000, 10.0, 0.0),
                 (85.0, 300_000, 10.0, 0.0), (125.0, 300_000, 10.0, 0.0),
                 (30.0, 49_999, 22.0, 25.0), (85.0, 66_000, 10.0, 25.0), (85.0, 66_667, 10.0, 0.0))
        for fee, liquidity, bucket, margin in cases:
            with self.subTest(fee=fee, liquidity=liquidity):
                calib = lf.calib_extra_bps_per_leg(fee, liquidity, 200.0)
                self.assertEqual((calib['fee_bucket_bps'], calib['unmeasured_margin_bps']), (bucket, margin))
                self.assertAlmostEqual(calib['engine_fixed_bps'], fixed, places=9)
                self.assertAlmostEqual(calib['total_bps'], bucket + margin + fixed, places=9)
        self.assertAlmostEqual(fixed, 5.525, places=9)

    def test_net50_from_booked_net(self):
        stop = lf.net50(-10.0, 200.0, 'STOP_LOSS_5_NET')
        self.assertAlmostEqual(stop['net50_pct'], 100 * (0.95 * 0.995 * (1 - 0.025) - 1), places=6)
        self.assertEqual(stop['net50_exit_extra_bps'], 200.0)
        hold = lf.net50(0.0, 200.0, 'ABSOLUTE_MAX_HOLD_60')
        self.assertAlmostEqual(hold['net50_usd'], 200 * (0.995 * 0.995 - 1), places=6)
        self.assertEqual(lf.net50(0.0, 200.0, 'TAKE_PROFIT_10_NET')['net50_exit_extra_bps'], 0.0)
        self.assertEqual(lf.net50(0.0, 200.0, 'RUSH_PROFIT_TRAIL')['net50_exit_extra_bps'], 100.0)
        self.assertEqual(lf.net50(None, 200.0, 'x'), {'net50_usd': None, 'net50_pct': None})

    def test_lab_booking_adds_a_per_leg_price_penalty_to_the_unchanged_shared_model(self):
        coin = pool(full('Mint'), full('Pair'), 0.01, 300_000.0, 1, marketCap=10_000_000.0, priceNative=0.01 / 120)
        model, booked = lab.entry_execution(coin, 200.0), lab.calibrated_entry_execution(coin, 200.0, 30.0)
        self.assertEqual(lab.calibrated_entry_execution(coin, 200.0, 0.0), model)
        self.assertNotIn('calibration_extra_pct', model)
        self.assertEqual(booked['calibration_extra_pct'], 0.3)
        self.assertAlmostEqual(booked['fill_price'] / model['fill_price'],
                               (1 + (model['impact_pct'] + 0.2 + 0.3) / 100) / (1 + (model['impact_pct'] + 0.2) / 100))
        self.assertAlmostEqual(booked['quantity'], (200.0 - model['dex_fee_usd']) / booked['fill_price'], places=9)
        self.assertLess(booked['quantity'], model['quantity'])
        self.assertEqual(booked['capital_committed_usd'], model['capital_committed_usd'])
        plain = lab.exit_execution(coin, model['quantity'])
        out = lab.calibrated_exit_execution(coin, model['quantity'], 30.0)
        self.assertEqual(lab.calibrated_exit_execution(coin, model['quantity'], 0.0), plain)
        penalty = (plain['impact_pct'] + 0.2 + 0.3) / 100
        gross = model['quantity'] * plain['market_price'] * (1 - penalty)
        self.assertAlmostEqual(out['net_proceeds_usd'],
                               gross * (1 - plain['dex_fee_bps'] / 1e4) - plain['network_fee_usd'], places=9)
        self.assertLess(out['net_proceeds_usd'], plain['net_proceeds_usd'])

    def test_drain_aware_exit_is_the_shared_model_until_the_sale_is_large(self):
        """LAB_FORWARD_CLOSE_POLICY_V1: x / (1 + x) above the 20% cap; liquidity 0 is worth 0."""
        coin = pool(full('Mint'), full('Pair'), 0.01, 300_000.0, 1, marketCap=10_000_000.0, priceNative=0.01 / 120)
        qty = lab.entry_execution(coin, 200.0)['quantity']
        capped = lab.calibrated_exit_execution(coin, qty, 27.5)
        self.assertEqual(lab.calibrated_exit_execution(coin, qty, 27.5, drain_aware=True)['net_proceeds_usd'],
                         capped['net_proceeds_usd'], 'a $200 sale into $300k is the unchanged shared model')
        for liquidity in (1_600.0, 1_000.0, 400.0, 50.0):
            with self.subTest(liquidity=liquidity):
                thin = dict(coin, liquidityUsd=liquidity)
                shared = lab.exit_execution(thin, qty)
                drained = lab.calibrated_exit_execution(thin, qty, 0.0, drain_aware=True)
                x = 2 * shared['market_value_usd'] / liquidity
                self.assertAlmostEqual(drained['impact_pct'], max(shared['impact_pct'], 100 * x / (1 + x)), places=9)
                self.assertLessEqual(drained['net_proceeds_usd'], shared['net_proceeds_usd'])
        # x = 0.25 (a sale of 12.5% of liquidity) is the last point where both agree.
        edge = dict(coin, liquidityUsd=2 * lab.exit_execution(coin, qty)['market_value_usd'] / 0.25)
        self.assertAlmostEqual(lab.calibrated_exit_execution(edge, qty, 0.0, drain_aware=True)['impact_pct'],
                               lab.exit_execution(edge, qty)['impact_pct'], places=9)
        drained = lab.calibrated_exit_execution(dict(coin, liquidityUsd=0.0), qty, 27.5, drain_aware=True)
        self.assertEqual((drained['impact_pct'], drained['net_proceeds_usd']), (100.0, 0.0))
        self.assertAlmostEqual(lab.exit_execution(dict(coin, liquidityUsd=0.0), qty)['impact_pct'], 20.0,
                               msg='the shared model alone books a drained pool at about -21%')
        # The DexScreener pair object's own liquidity dict wins; a dict without usd is unknown.
        self.assertEqual(lf.reported_liquidity_usd({'liquidityUsd': 0.0, 'liquidity': {'usd': 5_000}}), 5_000)
        self.assertIsNone(lf.reported_liquidity_usd({'liquidityUsd': 0.0, 'liquidity': {}}))
        self.assertEqual(lf.reported_liquidity_usd({'liquidityUsd': 0.0}), 0.0)
        unknown = dict(coin, liquidity={})
        unknown.pop('liquidityUsd')
        self.assertEqual(lab.calibrated_exit_execution(unknown, qty, 0.0, drain_aware=True)['impact_pct'],
                         lab.exit_execution(unknown, qty)['impact_pct'])


# ------------------------------------------------------------------ kill rule and promotion gate

class KillRuleTests(unittest.TestCase):
    NOW = 1_900_000_000_000

    def books(self, **histories):
        books = {s['id']: lab.empty_book(s) for s in lab.STRATEGIES}
        for book_id, history in histories.items():
            books[book_id]['history'] = history
        return books

    def review(self, books):
        with patch.object(lab, 'now_ms', return_value=self.NOW):
            return lab.review_strategy_lifecycle(books)

    def test_retires_after_50_closes_with_negative_mean_and_ci(self):
        values = [-6.0, -9.0, -3.0, -12.0, 1.0] * 10
        books = self.books(LAB_A_SURGE_EST_GUARD=closes(lf.LAB_A_ID, values[:49]))
        report = self.review(books)
        marker = books[lf.LAB_A_ID]['strategy_lifecycle']
        self.assertEqual((marker['status'], marker['reason']), ('active', 'kill_rule_min_closes_not_reached'))
        self.assertNotIn(lf.LAB_A_ID, report['retired_strategy_ids'])
        books = self.books(LAB_A_SURGE_EST_GUARD=closes(lf.LAB_A_ID, values))
        report = self.review(books)
        marker = books[lf.LAB_A_ID]['strategy_lifecycle']
        self.assertEqual((marker['version'], marker['status'], marker['reason']),
                         (lf.KILL_RULE_VERSION, 'retired', 'pre_registered_kill_rule'))
        self.assertFalse(lifecycle.entry_enabled(books[lf.LAB_A_ID]))
        self.assertTrue(marker['position_management_enabled'])
        self.assertEqual(marker['evidence']['closed_trades'], 50)
        self.assertLess(marker['evidence']['ci95_mean_net50_usd'][1], 0)
        self.assertEqual(marker['evidence']['ci_method'], 'pair_bootstrap')
        self.assertIn(lf.LAB_A_ID, report['retired_strategy_ids'])
        self.assertEqual(report['lab_forward_kill_rule']['min_closes'], 50)

    def test_ci_upper_bound_must_be_below_zero(self):
        values = [-40.0, 30.0] * 25          # mean -5, wide CI
        books = self.books(RND_LAB_A=closes(lf.RND_A_ID, values))
        self.review(books)
        marker = books[lf.RND_A_ID]['strategy_lifecycle']
        self.assertLess(marker['evidence']['mean_net50_usd'], 0)
        self.assertGreater(marker['evidence']['ci95_mean_net50_usd'][1], 0)
        self.assertEqual((marker['status'], marker['reason']), ('active', 'kill_rule_threshold_not_met'))
        books = self.books(RND_LAB_B=closes(lf.RND_B_ID, [2.0] * 60))
        self.review(books)
        self.assertEqual(books[lf.RND_B_ID]['strategy_lifecycle']['status'], 'active')

    def test_the_shared_twelve_close_heuristic_does_not_apply(self):
        values = [-8.0] * 20
        books = self.books(LAB_B_DIP_MKTDIP_GUARD=closes(lf.LAB_B_ID, values))
        for row in books[lf.LAB_B_ID]['history']:
            row.update(entry_policy_version=lf.ENTRY_POLICY_VERSION, execution_mode=lab.EXECUTION_MODEL_VERSION,
                       price_crosscheck={'status': 'pass', 'mint': row['address'], 'pair': row['pairAddress']})
        report = self.review(books)
        self.assertEqual(books[lf.LAB_B_ID]['strategy_lifecycle']['version'], lf.KILL_RULE_VERSION)
        self.assertEqual(books[lf.LAB_B_ID]['strategy_lifecycle']['status'], 'active')
        self.assertIn(lf.LAB_B_ID, report['active_registered_strategy_ids'])
        self.assertEqual(report['version'], lifecycle.VERSION, 'the shared review is extended, not replaced')

    def test_only_the_frozen_config_counts_and_retirement_is_permanent(self):
        values = [-10.0] * 60
        books = self.books(LAB_A_SURGE_EST_GUARD=closes(lf.LAB_A_ID, values, config_hash='0' * 64))
        self.review(books)
        self.assertEqual(books[lf.LAB_A_ID]['strategy_lifecycle']['evidence']['closed_trades'], 0)
        books = self.books(LAB_A_SURGE_EST_GUARD=closes(lf.LAB_A_ID, values, version='OTHER'))
        self.review(books)
        self.assertEqual(books[lf.LAB_A_ID]['strategy_lifecycle']['evidence']['closed_trades'], 0)
        books = self.books(LAB_A_SURGE_EST_GUARD=closes(lf.LAB_A_ID, values))
        books[lf.LAB_A_ID].update(balance=431.25, position={'trade_no': 61, 'strategy_id': lf.LAB_A_ID})
        before = copy.deepcopy({k: books[lf.LAB_A_ID][k] for k in ('balance', 'history', 'position')})
        self.review(books)
        marker = copy.deepcopy(books[lf.LAB_A_ID]['strategy_lifecycle'])
        self.assertEqual(marker['status'], 'retired')
        self.assertEqual({k: books[lf.LAB_A_ID][k] for k in ('balance', 'history', 'position')}, before)
        books[lf.LAB_A_ID]['history'] = closes(lf.LAB_A_ID, [20.0] * 80)
        report = self.review(books)
        self.assertEqual(books[lf.LAB_A_ID]['strategy_lifecycle']['status'], 'retired')
        self.assertEqual(books[lf.LAB_A_ID]['strategy_lifecycle']['evidence'], marker['evidence'])
        self.assertIn(lf.LAB_A_ID, report['retired_open_position_ids'])

    def test_bootstrap_is_deterministic_with_a_normal_fallback(self):
        rows = closes(lf.LAB_A_ID, [-3.0, 1.0, -7.0, 2.0, -5.0, 0.5] * 10)
        first, second = lf.bootstrap_ci(rows), lf.bootstrap_ci(copy.deepcopy(rows))
        self.assertEqual(first, second)
        self.assertEqual((first['method'], first['groups']), ('pair_bootstrap', 6))
        self.assertLess(first['low'], -1.75)
        self.assertLess(first['low'], first['high'])
        two_pairs = closes(lf.LAB_A_ID, [-3.0, 1.0] * 30, pairs=2)
        fallback = lf.bootstrap_ci(two_pairs)
        self.assertEqual(fallback['method'], 'normal_per_trade')
        self.assertAlmostEqual((fallback['low'] + fallback['high']) / 2, -1.0, places=9)

    def test_promotion_gate_is_reported_never_applied(self):
        values = [6.0, 4.0, 5.0, -2.0] * 45
        rows = closes(lf.LAB_A_ID, values, pairs=30)
        for index, row in enumerate(rows):
            row['opened_at'] = 1_800_000_000_000 + index * 31 * MINUTE
            row['closed_at'] = row['opened_at'] + 10 * MINUTE
        control = closes(lf.RND_A_ID, [-6.0, -9.0, 1.0] * 60, pairs=30)
        for index, row in enumerate(control):
            row['opened_at'] = 1_800_000_000_000 + index * 29 * MINUTE
            row['closed_at'] = row['opened_at'] + 10 * MINUTE
        books = self.books(LAB_A_SURGE_EST_GUARD=rows, RND_LAB_A=control)
        report = self.review(books)
        gate = books[lf.LAB_A_ID]['strategy_lifecycle']['promotion_gate']
        criteria = gate['criteria']
        for name in ('closed_trades', 'pairs', 'days', 'utc_hours_covered', 'mean_net50_usd', 'ci95_low_net50_usd',
                     'control_coverage', 'beats_same_period_control_usd', 'top_pair_share',
                     'mean_without_best_pair_usd', 'best_day_share_of_pnl', 'entries_on_rug_flagged_pools',
                     'vanished_or_unpriced_share'):
            with self.subTest(criterion=name):
                self.assertTrue(criteria[name]['pass'], criteria[name])
        self.assertTrue(gate['all_evaluable_pass'])
        self.assertEqual(gate['not_evaluated'], ['max_drawdown_pct_3slot_1000'])
        self.assertFalse(gate['automatic_promotion'])
        book = books[lf.LAB_A_ID]
        self.assertEqual(book['portfolio_group'], 'TEST')
        self.assertTrue(lifecycle.entry_enabled(book))
        self.assertNotIn('promotion_gate', books[lf.RND_A_ID]['strategy_lifecycle'])
        self.assertNotIn(lf.LAB_A_ID, report['retired_strategy_ids'])
        weak = self.books(LAB_A_SURGE_EST_GUARD=rows[:40], RND_LAB_A=control)
        self.review(weak)
        self.assertFalse(weak[lf.LAB_A_ID]['strategy_lifecycle']['promotion_gate']['all_evaluable_pass'])

    def stop_hypothesis(self, books, hypothesis_id, *, at=None):
        """A hypothesis already retired by its kill rule (persisted marker): its control loses continuity."""
        books[hypothesis_id]['strategy_lifecycle'] = {
            'version': lf.KILL_RULE_VERSION, 'status': 'retired', 'entry_enabled': False,
            'reason': 'pre_registered_kill_rule', 'retired_at': at or self.NOW - DAY, 'evidence': {}}

    def test_cash_exhausted_book_is_marked_not_active(self):
        """$500 at a fixed $200: about $300 of loss headroom, often < 50 closes (LAB_FORWARD_CASH_STATE_V1).

        A control is only cash_exhausted once its hypothesis can no longer enter
        (LAB_FORWARD_CONTROL_CONTINUITY_V1); before that it keeps measuring at zero capital.
        """
        books = self.books(RND_LAB_A=closes(lf.RND_A_ID, [-8.0] * 40))
        books[lf.RND_A_ID]['balance'] = 160.0
        self.stop_hypothesis(books, lf.LAB_A_ID)
        report = self.review(books)
        marker = books[lf.RND_A_ID]['strategy_lifecycle']
        self.assertEqual((marker['version'], marker['status'], marker['reason'], marker['entry_enabled']),
                         (lf.KILL_RULE_VERSION, 'cash_exhausted', 'balance_below_fixed_notional', False))
        self.assertFalse(marker['kill_rule_evaluable'])
        self.assertEqual(marker['evidence']['closed_trades'], 40)
        self.assertEqual(marker['cash_state_version'], lf.CASH_STATE_VERSION)
        newest = max(row['closed_at'] for row in books[lf.RND_A_ID]['history'])
        self.assertEqual((marker['cash']['exhausted'], marker['cash']['exhausted_at'], marker['cash']['balance_usd']),
                         (True, newest, 160.0))
        self.assertEqual(marker['cash']['min_entry_balance_usd'], 200.1)
        self.assertIn(lf.RND_A_ID, report['lab_forward_cash_exhausted_ids'])
        self.assertNotIn(lf.RND_A_ID, report['active_registered_strategy_ids'])
        self.assertNotIn(lf.RND_A_ID, report['retired_strategy_ids'])
        # Never touches the ledger; an open position can still refund the book, so it is active.
        books[lf.RND_A_ID]['position'] = {'trade_no': 41, 'strategy_id': lf.RND_A_ID, 'opened_at': newest + 1}
        self.review(books)
        self.assertEqual(books[lf.RND_A_ID]['strategy_lifecycle']['status'], 'active')
        self.assertEqual(books[lf.RND_A_ID]['balance'], 160.0)
        books[lf.RND_A_ID].update(position=None, balance=200.1)
        self.review(books)
        self.assertEqual(books[lf.RND_A_ID]['strategy_lifecycle']['status'], 'active')
        # When the kill rule is met it takes precedence (and persists).
        books = self.books(RND_LAB_A=closes(lf.RND_A_ID, [-8.0] * 55))
        books[lf.RND_A_ID]['balance'] = 60.0
        self.stop_hypothesis(books, lf.LAB_A_ID)
        self.review(books)
        self.assertEqual(books[lf.RND_A_ID]['strategy_lifecycle']['status'], 'retired')
        self.assertTrue(books[lf.RND_A_ID]['strategy_lifecycle']['cash']['exhausted'])
        # A hypothesis out of cash is cash_exhausted itself (continuity is for controls only).
        books = self.books(LAB_B_DIP_MKTDIP_GUARD=closes(lf.LAB_B_ID, [-9.0] * 35))
        books[lf.LAB_B_ID]['balance'] = 185.0
        report = self.review(books)
        self.assertEqual(books[lf.LAB_B_ID]['strategy_lifecycle']['status'], 'cash_exhausted')
        self.assertNotIn('control_continuity', books[lf.LAB_B_ID]['strategy_lifecycle'])
        self.assertIn(lf.LAB_B_ID, report['lab_forward_cash_exhausted_ids'])

    def test_a_control_keeps_entering_while_its_hypothesis_can(self):
        """LAB_FORWARD_CONTROL_CONTINUITY_V1: neither the control's kill rule nor its cash ends the comparison."""
        # Kill rule met (55 closes at -$8): deferred while LAB_A can enter, published as met.
        books = self.books(RND_LAB_A=closes(lf.RND_A_ID, [-8.0] * 55))
        report = self.review(books)
        marker = books[lf.RND_A_ID]['strategy_lifecycle']
        self.assertTrue(marker['evidence']['kill_rule_met'])
        self.assertEqual((marker['status'], marker['reason'], marker['entry_enabled'], marker['capital_mode']),
                         ('active', 'control_kill_rule_deferred', True, lf.FUNDED))
        self.assertEqual(marker['control_continuity'], {
            'version': lf.CONTROL_CONTINUITY_VERSION, 'hypothesis': lf.LAB_A_ID, 'hypothesis_can_enter': True,
            'kill_rule_deferred': True, 'zero_capital_entries': False})
        self.assertTrue(lifecycle.entry_enabled(books[lf.RND_A_ID]))
        self.assertEqual(report['lab_forward_kill_rule_deferred_ids'], [lf.RND_A_ID])
        self.assertIn(lf.RND_A_ID, report['active_registered_strategy_ids'])
        self.assertNotIn(lf.RND_A_ID, report['retired_strategy_ids'])
        self.assertEqual(report['lab_forward_kill_rule']['control_continuity_version'], lf.CONTROL_CONTINUITY_VERSION)
        # The same evidence retires a hypothesis: continuity is never applied to a hypothesis.
        books = self.books(LAB_A_SURGE_EST_GUARD=closes(lf.LAB_A_ID, [-8.0] * 55))
        self.review(books)
        self.assertEqual(books[lf.LAB_A_ID]['strategy_lifecycle']['status'], 'retired')
        # Out of cash with fewer than 50 closes: zero-capital entries while LAB_B can enter.
        books = self.books(RND_LAB_B=closes(lf.RND_B_ID, [-9.0] * 35))
        books[lf.RND_B_ID]['balance'] = 185.0
        report = self.review(books)
        marker = books[lf.RND_B_ID]['strategy_lifecycle']
        self.assertEqual((marker['status'], marker['reason'], marker['capital_mode']),
                         ('active', 'kill_rule_min_closes_not_reached', lf.ZERO_CAPITAL))
        self.assertTrue(marker['control_continuity']['zero_capital_entries'])
        self.assertTrue(marker['cash']['exhausted'])
        self.assertEqual(report['lab_forward_zero_capital_control_ids'], [lf.RND_B_ID])
        self.assertEqual(report['lab_forward_cash_exhausted_ids'], [])
        self.assertTrue(lf.zero_capital_entry_allowed(lf.RND_B_ID, books))
        self.assertTrue(lf.zero_capital_entry_allowed(lf.RND_B_ID, books, set(lf.BOOK_IDS)))
        self.assertFalse(lf.zero_capital_entry_allowed(lf.RND_B_ID, books, {lf.RND_B_ID}),
                         'a hypothesis that is no longer registered cannot keep its control running')
        self.assertFalse(lf.zero_capital_entry_allowed(lf.LAB_B_ID, books), 'never for a hypothesis')
        self.assertEqual(lf.entry_window_end(books[lf.RND_B_ID]), (None, None))
        # Once the hypothesis stops (out of cash), the control's own rules apply again.
        books[lf.LAB_B_ID]['balance'] = 120.0
        books[lf.LAB_B_ID]['history'] = closes(lf.LAB_B_ID, [-12.0] * 30)
        report = self.review(books)
        self.assertEqual(books[lf.LAB_B_ID]['strategy_lifecycle']['status'], 'cash_exhausted')
        self.assertEqual(books[lf.RND_B_ID]['strategy_lifecycle']['status'], 'cash_exhausted')
        self.assertFalse(lf.zero_capital_entry_allowed(lf.RND_B_ID, books))
        self.assertEqual(sorted(report['lab_forward_cash_exhausted_ids']), [lf.LAB_B_ID, lf.RND_B_ID])
        # A deferred control is retired as soon as its hypothesis is retired.
        books = self.books(RND_LAB_A=closes(lf.RND_A_ID, [-8.0] * 55))
        self.stop_hypothesis(books, lf.LAB_A_ID)
        report = self.review(books)
        self.assertEqual((books[lf.RND_A_ID]['strategy_lifecycle']['status'],
                          books[lf.RND_A_ID]['strategy_lifecycle']['reason']), ('retired', 'pre_registered_kill_rule'))
        self.assertIn(lf.RND_A_ID, report['retired_strategy_ids'])
        # Balances and histories are never touched by the review.
        books = self.books(RND_LAB_B=closes(lf.RND_B_ID, [-9.0] * 35))
        books[lf.RND_B_ID]['balance'] = 185.0
        before = copy.deepcopy({key: books[lf.RND_B_ID][key] for key in ('balance', 'history', 'position')})
        self.review(books)
        self.assertEqual({key: books[lf.RND_B_ID][key] for key in ('balance', 'history', 'position')}, before)

    def test_a_new_config_hash_starts_a_new_sample_not_a_new_ledger(self):
        """A parameter, cost-model or funding change is a new evidence sample in the same ledger.

        Closes of another hash never count, but the book keeps its balance and a
        kill-rule retirement persists across hashes; a new test needs new book ids.
        """
        old_hash = '0' * 64
        books = self.books(RND_LAB_B=closes(lf.RND_B_ID, [-9.0] * 60, config_hash=old_hash))
        books[lf.RND_B_ID]['balance'] = 180.0
        books[lf.RND_B_ID]['strategy_lifecycle'] = {
            'version': lf.KILL_RULE_VERSION, 'status': 'retired', 'entry_enabled': False,
            'reason': 'pre_registered_kill_rule', 'retired_at': self.NOW - DAY,
            'evidence': {'config_hash': old_hash, 'closed_trades': 60, 'kill_rule_met': True}}
        self.review(books)
        marker = books[lf.RND_B_ID]['strategy_lifecycle']
        self.assertEqual(marker['status'], 'retired', 'a retirement under another hash persists')
        self.assertEqual(marker['evidence']['config_hash'], old_hash)
        self.assertEqual(marker['current_evidence']['config_hash'], lf.CONFIG_HASHES[lf.RND_B_ID])
        self.assertEqual(marker['current_evidence']['closed_trades'], 0)
        self.assertFalse(lifecycle.entry_enabled(books[lf.RND_B_ID]))
        self.assertEqual(books[lf.RND_B_ID]['balance'], 180.0)
        # The Lab loads an existing book's ledger as stored, whatever START_BALANCE_USD says now.
        stored = {'books': {lf.RND_A_ID: {**lab.empty_book(next(s for s in lab.STRATEGIES if s['id'] == lf.RND_A_ID)),
                                          'starting_balance': 500.0, 'balance': 180.0}}}
        lab.STATE_PATH.write_text(json.dumps(stored), encoding='utf-8')
        try:
            with patch.dict(lab.STRATEGY_START_BALANCES, {lf.RND_A_ID: 2_000.0}), \
                    patch.object(lab, 'now_ms', return_value=self.NOW):
                state = lab.load_state()
        finally:
            lab.STATE_PATH.unlink()
        self.assertEqual((state['books'][lf.RND_A_ID]['starting_balance'], state['books'][lf.RND_A_ID]['balance']),
                         (500.0, 180.0))
        self.assertEqual(state['books'][lf.LAB_A_ID]['starting_balance'], lf.START_BALANCE_USD)

    def test_gate_compares_only_the_period_in_which_the_control_could_enter(self):
        start = 1_800_000_000_000
        rows = closes(lf.LAB_A_ID, [6.0, 4.0, 5.0, -2.0] * 40, pairs=30)
        for index, row in enumerate(sorted(rows, key=lambda r: r['trade_no'])):
            row['opened_at'] = start + index * 40 * MINUTE          # 160 closes over 4.4 days
            row['closed_at'] = row['opened_at'] + 10 * MINUTE
        control = closes(lf.RND_A_ID, [-6.0, -9.0, 1.0, -2.0] * 10, pairs=30)
        for index, row in enumerate(sorted(control, key=lambda r: r['trade_no'])):
            row['opened_at'] = start + index * 30 * MINUTE          # 40 closes in the first 20 h
            row['closed_at'] = row['opened_at'] + 10 * MINUTE
        books = self.books(LAB_A_SURGE_EST_GUARD=rows, RND_LAB_A=control)
        books[lf.RND_A_ID]['balance'] = 160.0                       # then out of cash
        # LAB_FORWARD_CONTROL_CONTINUITY_V1: while LAB_A can enter, the control keeps entering
        # at zero capital, so the window is the hypothesis's whole period.
        self.review(books)
        self.assertEqual((books[lf.RND_A_ID]['strategy_lifecycle']['status'],
                          books[lf.RND_A_ID]['strategy_lifecycle']['capital_mode']), ('active', lf.ZERO_CAPITAL))
        gate = books[lf.LAB_A_ID]['strategy_lifecycle']['promotion_gate']
        self.assertEqual(gate['control_window']['control_entry_end_reason'], None)
        self.assertEqual(gate['criteria']['control_coverage']['value'], 1.0)
        self.assertTrue(gate['criteria']['control_coverage']['pass'])
        self.assertEqual(gate['control_cash_state']['balance_usd'], 160.0)
        # Without continuity (a hypothesis stopped by an earlier marker) the control's cash ends it.
        books = self.books(LAB_A_SURGE_EST_GUARD=rows, RND_LAB_A=control)
        books[lf.RND_A_ID]['balance'] = 160.0
        self.stop_hypothesis(books, lf.LAB_A_ID, at=self.NOW)
        self.review(books)
        self.assertEqual(books[lf.RND_A_ID]['strategy_lifecycle']['status'], 'cash_exhausted')
        gate = books[lf.LAB_A_ID]['strategy_lifecycle']['promotion_gate']
        window = gate['control_window']
        control_end = start + 39 * 30 * MINUTE + 10 * MINUTE
        self.assertEqual((window['control_entry_end_reason'], window['control_entry_end_at'], window['end']),
                         ('cash_exhausted', control_end, control_end))
        self.assertEqual(window['control_status'], 'cash_exhausted')
        coverage = gate['criteria']['control_coverage']
        self.assertFalse(coverage['pass'])
        self.assertLess(coverage['value'], 0.25)
        self.assertEqual(gate['book_closed_trades_same_period'],
                         sum(1 for row in rows if row['opened_at'] <= control_end))
        self.assertEqual(gate['control_closed_trades_same_period'], 40)
        self.assertFalse(gate['all_evaluable_pass'])
        # A control retired by its own kill rule ends the window at its retirement.
        books = self.books(LAB_A_SURGE_EST_GUARD=rows, RND_LAB_A=control)
        books[lf.RND_A_ID]['strategy_lifecycle'] = {'version': lf.KILL_RULE_VERSION, 'status': 'retired',
                                                     'entry_enabled': False, 'retired_at': start + 2 * DAY,
                                                     'evidence': {}}
        self.review(books)
        window = books[lf.LAB_A_ID]['strategy_lifecycle']['promotion_gate']['control_window']
        self.assertEqual((window['control_entry_end_reason'], window['end']), ('retired', start + 2 * DAY))
        self.assertAlmostEqual(window['time_share'], 2 * DAY / (159 * 40 * MINUTE + 10 * MINUTE), places=5)
        # No control book at all: not evaluable as a pass.
        books = self.books(LAB_A_SURGE_EST_GUARD=rows)
        del books[lf.RND_A_ID]
        with patch.object(lab, 'now_ms', return_value=self.NOW):
            lf.apply_kill_rules(books, {}, registered_ids=set(lf.BOOK_IDS), now=self.NOW)
        gate = books[lf.LAB_A_ID]['strategy_lifecycle']['promotion_gate']
        self.assertEqual(gate['control_window']['control_entry_end_reason'], 'control_book_missing')
        self.assertFalse(gate['criteria']['control_coverage']['pass'])
        self.assertFalse(gate['criteria']['beats_same_period_control_usd']['pass'])

    def test_vanished_and_drained_closes_count_against_the_gate(self):
        rows = closes(lf.LAB_A_ID, [6.0, 4.0, 5.0, -2.0] * 40, pairs=30)
        for row in rows[:10]:
            row['close_kind'] = 'vanished'
        for row in rows[10:16]:
            row['close_kind'] = 'drained'
        books = self.books(LAB_A_SURGE_EST_GUARD=rows)
        self.review(books)
        marker = books[lf.LAB_A_ID]['strategy_lifecycle']
        self.assertEqual((marker['evidence']['vanished_closes'], marker['evidence']['drained_closes']), (10, 6))
        share = marker['promotion_gate']['criteria']['vanished_or_unpriced_share']
        self.assertAlmostEqual(share['value'], 16 / 160, places=6)
        self.assertFalse(share['pass'])
        # An open position past its max hold without a mark is a pending vanished close.
        rows = closes(lf.LAB_A_ID, [6.0] * 9)
        books = self.books(LAB_A_SURGE_EST_GUARD=rows)
        books[lf.LAB_A_ID]['position'] = {'trade_no': 10, 'strategy_id': lf.LAB_A_ID,
                                          'lab_forward_version': lf.VERSION, 'quote_status': 'stale',
                                          'opened_at': self.NOW - 65 * MINUTE}
        self.review(books)
        marker = books[lf.LAB_A_ID]['strategy_lifecycle']
        self.assertTrue(marker['evidence']['open_unpriced_past_max_hold'])
        self.assertAlmostEqual(marker['promotion_gate']['criteria']['vanished_or_unpriced_share']['value'], 0.1)


# ------------------------------------------------------------------ signal carry

class SignalCarryTests(unittest.TestCase):
    """LAB_FORWARD_SIGNAL_CARRY_V1 episodes: bounded, per book, counted per config hash in the ledger."""
    T = 1_800_000_000_000

    @staticmethod
    def evaluation(stamp, book_id=lf.RND_B_ID):
        return {'book_id': book_id, 'observed_at': stamp, 'universe_rejections': [], 'signal_rejections': [],
                'matched': True, 'signal': {'kind': 'random', 'drawn': True}}

    def test_episodes_window_outcomes_and_counters(self):
        carry, book, T = lf.SignalCarry(), {'id': lf.RND_B_ID, 'history': []}, self.T
        a, b, c = ('M1', 'P1'), ('M2', 'P2'), ('M3', 'P3')
        self.assertTrue(carry.hold(book, a, self.evaluation(T), T + 1_000))
        self.assertFalse(carry.hold(book, a, self.evaluation(T + 3_000), T + 4_000), 'the same episode, refreshed')
        carried = carry.carried_evaluation(lf.RND_B_ID, a, T + 5_000)
        self.assertEqual(carried['observed_at'], T + 3_000)
        self.assertEqual((carried['carry']['signal_observed_at'], carried['carry']['first_signal_observed_at'],
                          carried['carry']['signals'], carried['carry']['attempts']), (T + 3_000, T, 2, 2))
        carried['signal']['drawn'] = 'mutated'
        self.assertTrue(carry.carried_evaluation(lf.RND_B_ID, a, T + 5_000)['signal']['drawn'], 'a copy')
        self.assertFalse(carry.hold(book, a, carried, T + 6_000), 'a carried retry never extends the window')
        self.assertEqual(carry.carried_evaluation(lf.RND_B_ID, a, T + 6_000)['observed_at'], T + 3_000)
        # The window: 60 s after the latest signal observation.
        carry.hold(book, b, self.evaluation(T + 1_000), T + 2_000)
        self.assertIsNotNone(carry.carried_evaluation(lf.RND_B_ID, b, T + 61_000))
        self.assertIsNone(carry.carried_evaluation(lf.RND_B_ID, b, T + 61_001))
        self.assertEqual(carry.expire(book, T + 61_001), 1)
        self.assertEqual(carry.pending_count(lf.RND_B_ID), 1)
        self.assertTrue(carry.resolve(book, a, 'entered'))
        self.assertFalse(carry.resolve(book, a, 'entered'), 'an episode ends once')
        carry.hold(book, b, self.evaluation(T + 70_000), T + 70_500)
        carry.hold(book, c, self.evaluation(T + 70_000), T + 70_500)
        self.assertEqual(carry.clear(book, 'superseded', keep=c), 1)
        self.assertTrue(carry.resolve(book, c, 'dropped_by_gate', 'modeled_roundtrip_cost_limit'))
        carry.hold(book, a, self.evaluation(T + 80_000), T + 80_500)
        self.assertEqual(carry.clear(book, 'book_stopped'), 1)
        counters = lf.signal_carry_counters(book)
        self.assertEqual({name: counters[name] for name in lf.CARRY_COUNTERS},
                         {'pending_signals': 5, 'entered': 1, 'lost_price_pending': 1, 'superseded': 1,
                          'book_stopped': 1})
        self.assertEqual(counters['dropped_by_gate'], {'modeled_roundtrip_cost_limit': 1})
        self.assertEqual((counters['ended'], counters['lost_price_pending_share']), (5, 0.2))
        self.assertEqual(counters['config_hash'], lf.CONFIG_HASHES[lf.RND_B_ID])
        # Stored in the ledger per config hash: another hash's counters are never reported.
        store = book['lab_forward_signal_carry']
        self.assertEqual(list(store['by_config_hash']), [lf.CONFIG_HASHES[lf.RND_B_ID]])
        store['by_config_hash']['0' * 64] = {'pending_signals': 99, 'lost_price_pending': 99}
        self.assertEqual(lf.signal_carry_counters(book)['pending_signals'], 5)
        self.assertEqual(lf.signal_carry_counters({'id': lf.LAB_A_ID})['pending_signals'], 0)
        # Other books' episodes are separate; unknown books are ignored.
        other = {'id': lf.LAB_A_ID}
        carry.hold(other, a, self.evaluation(T, lf.LAB_A_ID), T + 1_000)
        self.assertEqual((carry.pending_count(lf.LAB_A_ID), carry.pending_count(lf.RND_B_ID)), (1, 0))
        self.assertFalse(carry.hold({'id': 'TREND'}, a, self.evaluation(T), T))
        self.assertEqual(carry.status()['pending'], {lf.LAB_A_ID: 1})
        self.assertFalse(carry.status()['persistent'])

    def test_pending_signals_are_bounded(self):
        carry = lf.SignalCarry(dataclasses.replace(lf.SIGNAL_CARRY, max_pending_per_book=2))
        book = {'id': lf.RND_A_ID}
        for index in range(3):
            carry.hold(book, (f'M{index}', f'P{index}'), self.evaluation(self.T + index, lf.RND_A_ID), self.T + 10)
        self.assertEqual(carry.pending_count(lf.RND_A_ID), 2)
        self.assertIsNone(carry.carried_evaluation(lf.RND_A_ID, ('M0', 'P0'), self.T + 10), 'the oldest is evicted')
        counters = lf.signal_carry_counters(book)
        self.assertEqual((counters['pending_signals'], counters['lost_price_pending']), (3, 1))


# ------------------------------------------------------------------ Strategy Lab integration

class LabIntegrationTests(unittest.TestCase):
    def setUp(self):
        lab.rush_brain._SAMPLE_BY_PAIR.clear()
        self.books = {s['id']: lab.empty_book(s) for s in lab.STRATEGIES}
        self.clock = [0]
        self.calls = {'rugcheck': 0, 'price': 0}
        self.memory = lf.ForwardFeedMemory()
        self.carry = lf.SignalCarry()
        # Price cross-check script: None passes every call; else a callable(coin) -> status.
        self.price_script = None

        def price(coin):
            self.calls['price'] += 1
            status = self.price_script(coin) if self.price_script else 'pass'
            if status == 'review':
                # A cold GeckoTerminal reference: the fetch is queued and Jupiter would be needed.
                return {'status': 'review', 'reason': 'price_crosscheck_pending_needs_jupiter',
                        'observed_price': coin['priceUsd'], 'reference_price': None,
                        'mint': coin['address'], 'pair': coin['pairAddress']}
            if status == 'blocked':
                return {'status': 'blocked', 'reason': 'price_source_disagreement',
                        'mint': coin['address'], 'pair': coin['pairAddress']}
            return {'status': 'pass', 'mint': coin['address'], 'pair': coin['pairAddress']}

        def rugcheck(coin):
            self.calls['rugcheck'] += 1
            return {'status': 'pass', 'mint': coin['address'], 'pair': coin['pairAddress']}

        def resolve(position, prices, now):
            coin = prices.get((position.get('address'), position.get('pairAddress')))
            return dict(coin, mark_received_at=coin['updatedAt'], mark_source='SHARED_LIVE_FEED_EXACT_POOL') if coin else None

        self.unpriced = {}
        for p in (patch.object(lab, 'STATE', {'started_at': 42, 'books': self.books}),
                  patch.object(lab, 'FORWARD_MEMORY', self.memory),
                  patch.object(lab, 'FORWARD_SIGNAL_CARRY', self.carry),
                  patch.object(lab, 'FORWARD_UNPRICED_SINCE', self.unpriced),
                  patch.object(lab, 'now_ms', side_effect=lambda: self.clock[0]),
                  patch.object(lab.price_integrity, 'check', side_effect=price),
                  patch.object(lab.rug_guard, 'check', side_effect=rugcheck),
                  patch.object(lab.POSITION_MARK_FEED, 'resolve', side_effect=resolve),
                  patch.object(lab, 'schedule_jupiter_price_probe', return_value=False)):
            p.start()
            self.addCleanup(p.stop)

    def use_layer(self, now):
        self.layer = entry_defense.DefensiveEntryLayer(registry=covered_registry(now))
        p = patch.object(lab, 'DEFENSE', self.layer)
        p.start()
        self.addCleanup(p.stop)

    def only(self, *book_ids):
        return patch.object(lab, 'STRATEGIES', [s for s in lab.STRATEGIES if s['id'] in book_ids])

    def refresh(self, coin, *, book_ids=(lf.LAB_A_ID,), lag_ms=1_000):
        self.clock[0] = int(coin['updatedAt']) + lag_ms
        with self.only(*book_ids):
            lab.maybe_open([coin], {})

    def open_neet(self):
        crossing = next(c for c in FIXTURES['lab_a_crossings'] if c['name'].startswith('neet'))
        points = [feed_coin(point) for point in crossing['points']]
        self.use_layer(points[0]['updatedAt'])
        self.refresh(points[0])
        self.assertIsNone(self.books[lf.LAB_A_ID]['position'])
        self.refresh(points[1])
        return points

    def test_lab_a_opens_on_the_fresh_crossing_with_calibrated_booking(self):
        points = self.open_neet()
        book = self.books[lf.LAB_A_ID]
        position = book['position']
        self.assertIsNotNone(position, book['entry_diagnostics'])
        self.assertEqual(position['entry_policy_version'], lf.VERSION)
        self.assertEqual(position['lab_forward_version'], lf.VERSION)
        self.assertEqual(position['lab_config_hash'], PINNED_CONFIG_HASHES[lf.LAB_A_ID])
        self.assertEqual(position['notional_usd'], 200.0)
        self.assertEqual(position['entry_cost_cap_pct'], 2.5)
        self.assertEqual(position['stop_loss_net_pct'], 5.0)
        self.assertEqual(position['exit_policy_label'], 'LAB_A_5_10_60')
        self.assertFalse(position['promotion_eligible'])
        # Heat is log-only: the pool was seen 5 s ago, so the warm-up flag is recorded, not applied.
        self.assertIn('heat_history_warming', position['heat_log_only_flags'])
        self.assertEqual(position['defensive_entry']['log_only_flags'], position['heat_log_only_flags'])
        self.assertTrue(position['defensive_entry']['allowed'])
        self.assertFalse(position['defensive_entry']['structural_rug_guard']['blocked'])
        # 30 bps tier, $2M liquidity: CALIB_V1 22 bps + 5.525 bps engine fixed costs per leg.
        self.assertAlmostEqual(position['calib_bps_per_leg'], 27.525, places=6)
        self.assertLess(position['quantity'], position['model_quantity'])
        self.assertLess(position['booked_entry_roundtrip_pnl_pct'], position['entry_roundtrip_pnl_pct'])
        self.assertAlmostEqual(position['entry_roundtrip_pnl_pct'] - position['booked_entry_roundtrip_pnl_pct'],
                               0.55, delta=0.02)
        self.assertEqual(position['lab_forward']['signal']['previous_surge'], False)
        self.assertEqual(position['lab_forward']['observed_at'], points[1]['updatedAt'])
        diagnostics = book['entry_diagnostics']['lab_forward']
        self.assertEqual((diagnostics['universe_candidates'], diagnostics['signals']), (1, 1))
        self.assertEqual(self.calls['rugcheck'], 0, 'DexScreener-only research arm: no RugCheck gate added')

    def test_exit_on_model_net_stop_records_net50_and_identity(self):
        points = self.open_neet()
        book = self.books[lf.LAB_A_ID]
        position = book['position']
        entry_price = position['entry_price']
        # A 3.8% mark drop: model net about -4.85% (no stop), booked about -5.4%.
        mark = dict(points[1], priceUsd=entry_price * 0.962, updatedAt=points[1]['updatedAt'] + 60_000)
        self.clock[0] = mark['updatedAt'] + 500
        lab.update_positions({}, [mark])
        self.assertIsNotNone(book['position'])
        self.assertGreater(book['position']['model_pnl_pct'], -5.0)
        self.assertLess(book['position']['pnl_pct'], -5.0)
        mark = dict(mark, priceUsd=entry_price * 0.95, updatedAt=mark['updatedAt'] + 30_000)
        self.clock[0] = mark['updatedAt'] + 500
        lab.update_positions({}, [mark])
        self.assertIsNone(book['position'])
        trade = book['history'][0]
        self.assertEqual(trade['exit_reason'], 'STOP_LOSS_5_NET')
        self.assertEqual(trade['lab_config_hash'], PINNED_CONFIG_HASHES[lf.LAB_A_ID])
        self.assertEqual(trade['lab_forward_version'], lf.VERSION)
        self.assertIn('heat_history_warming', trade['heat_log_only_flags'])
        self.assertLessEqual(trade['model_pnl_pct'], -5.0)
        self.assertGreater(trade['calibration_cost_usd'], 0)
        self.assertAlmostEqual(trade['model_pnl_usd'] - trade['pnl_usd'], trade['calibration_cost_usd'], places=4)
        expected = lf.net50(trade['pnl_usd'], 200.0, 'STOP_LOSS_5_NET')
        self.assertAlmostEqual(trade['net50_usd'], expected['net50_usd'], places=6)
        self.assertEqual(trade['net50_exit_extra_bps'], 200.0)
        self.assertAlmostEqual(book['balance'], 500.0 + trade['pnl_usd'], places=3)
        # The research's 300 s pool cooldown, then the next fresh crossing on that pool may enter.
        quiet = dict(points[0], updatedAt=mark['updatedAt'] + 10_000)
        surge = dict(points[1], updatedAt=mark['updatedAt'] + 20_000)
        self.refresh(quiet)
        self.refresh(surge)
        self.assertIsNone(book['position'])
        self.assertEqual(book['entry_diagnostics']['blocked_reason'], 'reentry_cooldown')
        quiet = dict(points[0], updatedAt=mark['updatedAt'] + 300_000)
        surge = dict(points[1], updatedAt=mark['updatedAt'] + 305_000)
        self.refresh(quiet)
        self.refresh(surge)
        self.assertIsNotNone(book['position'], book['entry_diagnostics'])

    def test_take_profit_and_max_hold(self):
        points = self.open_neet()
        book = self.books[lf.LAB_A_ID]
        price = book['position']['entry_price']
        mark = dict(points[1], priceUsd=price * 1.12, updatedAt=points[1]['updatedAt'] + 90_000)
        self.clock[0] = mark['updatedAt'] + 100
        lab.update_positions({}, [mark])
        self.assertEqual(book['history'][0]['exit_reason'], 'TAKE_PROFIT_10_NET')
        self.assertEqual(book['history'][0]['net50_exit_extra_bps'], 0.0)
        book['history'] = []
        self.refresh(dict(points[0], updatedAt=mark['updatedAt'] + 400_000))
        self.refresh(dict(points[1], updatedAt=mark['updatedAt'] + 405_000))
        opened = book['position']['opened_at']
        mark = dict(points[1], updatedAt=opened + 60 * MINUTE)
        self.clock[0] = mark['updatedAt'] + 100
        lab.update_positions({}, [mark])
        self.assertEqual(book['history'][0]['exit_reason'], 'ABSOLUTE_MAX_HOLD_60')

    def control_coin(self, book_id):
        """A pool observation of the control's universe at a fixture stamp where its coin fires."""
        params = lf.RANDOM[book_id]
        draw = next(d for d in FIXTURES['random_draws'] if d['salt'] == params.salt and d['firing_t'])
        stamp = draw['firing_t'][0]
        coin = pool(full('RndMint'), draw['pair'], 0.01, 1_000_000.0, stamp, marketCap=30_000_000.0,
                    priceNative=0.01 / 120, pairCreatedAt=stamp - 40 * DAY, symbol='RNDC', name='control fixture',
                    priceChange={'m5': 0.5, 'h1': 1.0, 'h24': 2.0},
                    txns={'m5': {'buys': 20, 'sells': 18}, 'h1': {'buys': 200, 'sells': 190}})
        self.assertTrue(lf.hashed_coin(draw['pair'], stamp, params.probability, params.salt))
        return coin

    def open_control(self, book_id):
        """A random-control entry through maybe_open, at a fixture observation where its coin fires."""
        coin = self.control_coin(book_id)
        self.use_layer(coin['updatedAt'])
        self.refresh(coin, book_ids=(book_id,))
        return coin

    def price_pending_for(self, calls):
        """The next ``calls`` price cross-checks are pending (cold reference), then they pass."""
        left = [calls]

        def script(coin):
            if left[0] > 0:
                left[0] -= 1
                return 'review'
            return 'pass'

        self.price_script = script

    def assert_control_position(self, book_id, coin):
        book = self.books[book_id]
        position = book['position']
        self.assertIsNotNone(position, book['entry_diagnostics'])
        self.assertEqual(position['strategy_id'], book_id)
        self.assertEqual(position['entry_policy_version'], lf.VERSION)
        self.assertEqual(position['lab_config_hash'], PINNED_CONFIG_HASHES[book_id])
        self.assertEqual(position['lab_forward']['role'], 'random_control')
        self.assertEqual(position['lab_forward']['hypothesis'], lf.HYPOTHESIS_OF[book_id])
        self.assertEqual(position['lab_forward']['config_hash'], PINNED_CONFIG_HASHES[book_id])
        self.assertEqual(position['lab_forward']['observed_at'], coin['updatedAt'])
        params = lf.RANDOM[book_id]
        self.assertEqual(position['lab_forward']['signal'], {'kind': 'random', 'probability': params.probability,
                                                             'salt': params.salt, 'drawn': True})
        self.assertEqual(position['notional_usd'], 200.0)
        self.assertEqual(position['stop_loss_net_pct'], lf.EXITS[book_id].stop_loss_net_pct)
        self.assertEqual(position['exit_policy_label'], lf.EXITS[book_id].label)
        # 30 bps tier on $1M: CALIB_V1 22 bps + 5.525 bps engine fixed costs per leg.
        self.assertAlmostEqual(position['calib_bps_per_leg'], 27.525, places=6)
        self.assertGreater(position['model_quantity'], position['quantity'])
        self.assertAlmostEqual(position['model_quantity'], lab.entry_execution(coin, 200.0)['quantity'], places=6)
        self.assertIn('heat_history_warming', position['heat_log_only_flags'])
        self.assertEqual(position['heat_veto_mode'], 'log_only')
        self.assertEqual(position['close_policy_version'], lf.CLOSE_POLICY_VERSION)
        self.assertEqual(position['last_mark']['priceUsd'], coin['priceUsd'])
        self.assertFalse(position['promotion_eligible'])
        self.assertEqual(book['entry_diagnostics']['lab_forward']['signals'], 1)
        return position

    def test_random_control_a_opens_through_the_lab_entry_path_and_closes_with_net50(self):
        coin = self.open_control(lf.RND_A_ID)
        position = self.assert_control_position(lf.RND_A_ID, coin)
        self.assertEqual(position['entry_cost_cap_pct'], 2.5)
        mark = dict(coin, priceUsd=coin['priceUsd'] * 0.94, priceNative=coin['priceNative'] * 0.94,
                    updatedAt=coin['updatedAt'] + 90_000)
        self.clock[0] = mark['updatedAt'] + 200
        lab.update_positions({}, [mark])
        book = self.books[lf.RND_A_ID]
        self.assertIsNone(book['position'])
        trade = book['history'][0]
        self.assertEqual(trade['exit_reason'], 'STOP_LOSS_5_NET')
        self.assertEqual((trade['lab_forward_version'], trade['lab_config_hash']),
                         (lf.VERSION, PINNED_CONFIG_HASHES[lf.RND_A_ID]))
        self.assertEqual((trade['close_kind'], trade['drain_valuation_cost_usd']), ('marked', 0.0))
        self.assertAlmostEqual(trade['net50_usd'], lf.net50(trade['pnl_usd'], 200.0, 'STOP_LOSS_5_NET')['net50_usd'],
                               places=6)
        self.assertEqual(trade['lab_forward']['role'], 'random_control')
        self.assertAlmostEqual(book['balance'], 500.0 + trade['pnl_usd'], places=3)

    def test_random_control_b_opens_through_the_lab_entry_path_and_closes_with_net50(self):
        coin = self.open_control(lf.RND_B_ID)
        position = self.assert_control_position(lf.RND_B_ID, coin)
        self.assertEqual(position['entry_cost_cap_pct'], 2.75)
        mark = dict(coin, priceUsd=coin['priceUsd'] * 1.25, priceNative=coin['priceNative'] * 1.25,
                    updatedAt=coin['updatedAt'] + 120_000)
        self.clock[0] = mark['updatedAt'] + 200
        lab.update_positions({}, [mark])
        trade = self.books[lf.RND_B_ID]['history'][0]
        self.assertEqual(trade['exit_reason'], 'TAKE_PROFIT_20_NET')
        self.assertEqual(trade['net50_exit_extra_bps'], 0.0)
        self.assertAlmostEqual(trade['net50_usd'], lf.net50(trade['pnl_usd'], 200.0, 'TAKE_PROFIT_20_NET')['net50_usd'],
                               places=6)
        self.assertEqual(trade['lab_config_hash'], PINNED_CONFIG_HASHES[lf.RND_B_ID])

    # ---------------------------------------------- LAB_FORWARD_SIGNAL_CARRY_V1

    def assert_carried_entry(self, book_id, signal, later):
        """``signal`` waited on the price check; the pool's next observation entered it."""
        book = self.books[book_id]
        position = book['position']
        self.assertIsNotNone(position, book['entry_diagnostics'])
        self.assertEqual(position['lab_forward']['observed_at'], signal['updatedAt'])
        self.assertEqual(position['lab_forward']['entry_observed_at'], later['updatedAt'])
        carry = position['lab_forward']['carry']
        self.assertEqual((carry['version'], carry['signal_observed_at'], carry['attempts'], carry['max_age_ms']),
                         (lf.SIGNAL_CARRY_VERSION, signal['updatedAt'], 1, 60_000))
        self.assertEqual(position['entry_price'], later['priceUsd'], 'it enters at the current observation')
        self.assertEqual(position['lab_config_hash'], PINNED_CONFIG_HASHES[book_id])
        diagnostics = book['entry_diagnostics']['lab_forward']
        self.assertEqual((diagnostics['signals'], diagnostics['carried_signals_retried']), (0, 1))
        counters = lf.signal_carry_counters(book)
        self.assertEqual((counters['pending_signals'], counters['entered'], counters['lost_price_pending'],
                          counters['dropped_by_gate'], counters['superseded']), (1, 1, 0, {}, 0))
        self.assertEqual(diagnostics['signal_carry']['entered'], 1)
        self.assertEqual(diagnostics['signal_carry']['pending_now'], 0)
        return position

    def carried_control_entry(self, book_id):
        coin = self.control_coin(book_id)
        params = lf.RANDOM[book_id]
        later = dict(coin, updatedAt=coin['updatedAt'] + 2_200, priceUsd=coin['priceUsd'] * 1.001,
                     priceNative=coin['priceNative'] * 1.001)
        self.assertFalse(lf.hashed_coin(coin['pairAddress'], later['updatedAt'], params.probability, params.salt),
                         'the next observation does not draw: without the carry the signal was lost')
        self.use_layer(coin['updatedAt'])
        self.price_pending_for(1)
        self.refresh(coin, book_ids=(book_id,))
        book = self.books[book_id]
        self.assertIsNone(book['position'])
        diagnostics = book['entry_diagnostics']
        self.assertEqual((diagnostics['blocked_reason'], diagnostics['price_crosscheck_pending']),
                         (lf.PRICE_CHECK_PENDING_REASON, 1))
        self.assertEqual(diagnostics['lab_forward']['price_crosscheck_pending_signals'], 1)
        self.assertEqual((diagnostics['lab_forward']['signal_carry']['pending_now'],
                          diagnostics['lab_forward']['signal_carry']['pending_signals']), (1, 1))
        self.refresh(later, book_ids=(book_id,))
        self.assertEqual(book['entry_diagnostics']['lab_forward']['signal_rejections'], {'rnd_coin_not_drawn': 1})
        position = self.assert_carried_entry(book_id, coin, later)
        self.assertEqual(position['lab_forward']['signal']['drawn'], True)
        return position

    def test_a_random_draw_a_waiting_on_the_price_check_enters_at_the_next_observation(self):
        self.carried_control_entry(lf.RND_A_ID)

    def test_a_random_draw_b_waiting_on_the_price_check_enters_at_the_next_observation(self):
        self.carried_control_entry(lf.RND_B_ID)

    def test_a_lab_a_fresh_crossing_waiting_on_the_price_check_enters_at_the_next_observation(self):
        crossing = next(c for c in FIXTURES['lab_a_crossings'] if c['name'].startswith('neet'))
        points = [feed_coin(point) for point in crossing['points']]
        self.use_layer(points[0]['updatedAt'])
        self.refresh(points[0])
        self.price_pending_for(1)
        self.refresh(points[1])
        book = self.books[lf.LAB_A_ID]
        self.assertIsNone(book['position'])
        self.assertEqual(book['entry_diagnostics']['blocked_reason'], lf.PRICE_CHECK_PENDING_REASON)
        later = dict(points[1], updatedAt=points[1]['updatedAt'] + 3_000)
        self.refresh(later)
        self.assertEqual(book['entry_diagnostics']['lab_forward']['signal_rejections'], {'lab_a_surge_not_fresh': 1},
                         'the crossing is not fresh at the next observation')
        position = self.assert_carried_entry(lf.LAB_A_ID, points[1], later)
        self.assertEqual(position['lab_forward']['signal']['previous_surge'], False)

    def test_a_carried_signal_is_lost_after_60_s_or_dropped_by_another_gate(self):
        coin = self.control_coin(lf.RND_B_ID)
        stamp = coin['updatedAt']
        self.use_layer(stamp)
        self.price_script = lambda c: 'review'
        self.refresh(coin, book_ids=(lf.RND_B_ID,))
        self.refresh(dict(coin, updatedAt=stamp + 30_000), book_ids=(lf.RND_B_ID,))
        book = self.books[lf.RND_B_ID]
        self.assertIsNone(book['position'])
        self.assertEqual(book['entry_diagnostics']['lab_forward']['carried_signals_retried'], 1)
        self.assertEqual(self.carry.pending_count(lf.RND_B_ID), 1)
        # 60 s after the signal observation the episode is lost to the still-pending check.
        self.refresh(dict(coin, updatedAt=stamp + 61_000), book_ids=(lf.RND_B_ID,), lag_ms=0)
        self.assertIsNone(book['position'])
        counters = lf.signal_carry_counters(book)
        self.assertEqual((counters['pending_signals'], counters['lost_price_pending'], counters['entered']), (1, 1, 0))
        self.assertEqual(counters['lost_price_pending_share'], 1.0)
        self.assertEqual(self.carry.pending_count(lf.RND_B_ID), 0)
        self.assertEqual(book['entry_diagnostics']['blocked_reason'], 'no_market_signal')
        # Published cumulatively in the lifecycle marker and in its hypothesis's gate.
        with patch.object(lab, 'now_ms', return_value=self.clock[0]):
            lab.review_strategy_lifecycle(self.books)
        self.assertEqual(book['strategy_lifecycle']['signal_carry']['lost_price_pending'], 1)
        gate = self.books[lf.LAB_B_ID]['strategy_lifecycle']['promotion_gate']
        self.assertEqual(gate['signal_carry']['control']['lost_price_pending'], 1)
        self.assertEqual(gate['signal_carry']['book']['pending_signals'], 0)
        # A carried retry refused by another gate (here a price disagreement) ends the episode.
        later = dict(coin, updatedAt=stamp + 200_000)
        signal = {'book_id': lf.RND_B_ID, 'observed_at': later['updatedAt'] - 3_000, 'universe_rejections': [],
                  'signal_rejections': [], 'matched': True, 'signal': {'kind': 'random', 'drawn': True}}
        self.carry.hold(book, (coin['address'], coin['pairAddress']), signal, later['updatedAt'] - 2_000)
        self.price_script = lambda c: 'blocked'
        self.refresh(later, book_ids=(lf.RND_B_ID,))
        self.assertIsNone(book['position'])
        counters = lf.signal_carry_counters(book)
        self.assertEqual(counters['dropped_by_gate'], {'price_verification': 1})
        self.assertEqual(self.carry.pending_count(lf.RND_B_ID), 0)
        params = lf.RANDOM[lf.RND_B_ID]
        for offset in (30_000, 61_000, 200_000):
            self.assertFalse(lf.hashed_coin(coin['pairAddress'], stamp + offset, params.probability, params.salt))

    # ---------------------------------------------- LAB_FORWARD_CONTROL_CONTINUITY_V1

    def test_a_control_out_of_cash_enters_at_zero_capital_while_its_hypothesis_can(self):
        book = self.books[lf.RND_A_ID]
        book['balance'] = 150.0
        coin = self.control_coin(lf.RND_A_ID)
        self.use_layer(coin['updatedAt'])
        # Its hypothesis is registered and can enter (the control coin is no LAB_A surge).
        self.refresh(coin, book_ids=(lf.RND_A_ID, lf.LAB_A_ID))
        self.assertIsNone(self.books[lf.LAB_A_ID]['position'])
        position = self.assert_control_position(lf.RND_A_ID, coin)
        self.assertEqual(position['capital_mode'], lf.ZERO_CAPITAL)
        self.assertEqual(book['entry_diagnostics']['lab_forward']['capital_mode'], lf.ZERO_CAPITAL)
        self.assertEqual(lab.stats(book)['equity'], 150.0, 'a zero-capital position never moves equity')
        mark = dict(coin, priceUsd=coin['priceUsd'] * 0.94, priceNative=coin['priceNative'] * 0.94,
                    updatedAt=coin['updatedAt'] + 90_000)
        self.clock[0] = mark['updatedAt'] + 200
        lab.update_positions({}, [mark])
        self.assertIsNone(book['position'])
        trade = book['history'][0]
        self.assertEqual(trade['exit_reason'], 'STOP_LOSS_5_NET')
        self.assertEqual((trade['capital_mode'], trade['balance_effect_usd'], trade['balance_after']),
                         (lf.ZERO_CAPITAL, 0.0, 150.0))
        self.assertLess(trade['pnl_usd'], -10.0)
        self.assertAlmostEqual(trade['net50_usd'], lf.net50(trade['pnl_usd'], 200.0, 'STOP_LOSS_5_NET')['net50_usd'],
                               places=6)
        self.assertEqual(book['balance'], 150.0)
        stats = lab.stats(book)
        self.assertEqual((stats['zero_capital_trades'], stats['realized_pnl'], stats['trades']), (1, -350.0, 1))
        self.assertAlmostEqual(stats['zero_capital_pnl_usd'], round(trade['pnl_usd'], 2), places=2)
        self.assertEqual(lf.evidence(book, lf.RND_A_ID, self.clock[0])['zero_capital_closes'], 1)
        self.assertEqual(lf.cash_state(book)['zero_capital_closes'], 1)
        # A funded control close moves the balance as before (capital_mode 'funded').
        funded = self.books[lf.RND_B_ID]
        funded_coin = self.open_control(lf.RND_B_ID)
        self.assertEqual(funded['position']['capital_mode'], lf.FUNDED)
        self.clock[0] = funded_coin['updatedAt'] + 61 * MINUTE
        lab.update_positions({}, [dict(funded_coin, updatedAt=self.clock[0] - 500)])
        self.assertEqual(funded['history'][0]['capital_mode'], lf.FUNDED)
        self.assertAlmostEqual(funded['balance'], 500.0 + funded['history'][0]['pnl_usd'], places=3)
        self.assertAlmostEqual(funded['history'][0]['balance_effect_usd'], funded['history'][0]['pnl_usd'], places=3)
        # Once its hypothesis is retired, the control is out of cash again.
        self.books[lf.LAB_A_ID]['strategy_lifecycle'] = {
            'version': lf.KILL_RULE_VERSION, 'status': 'retired', 'entry_enabled': False,
            'reason': 'pre_registered_kill_rule', 'retired_at': self.clock[0], 'evidence': {}}
        self.refresh(dict(coin, updatedAt=coin['updatedAt'] + 20 * MINUTE), book_ids=(lf.RND_A_ID, lf.LAB_A_ID))
        self.assertIsNone(book['position'])
        self.assertEqual(book['entry_diagnostics']['blocked_reason'], lf.CASH_EXHAUSTED_REASON)
        self.assertEqual(book['strategy_lifecycle']['status'], 'cash_exhausted')
        self.assertEqual(self.books[lf.LAB_A_ID]['entry_diagnostics']['blocked_reason'], lf.RETIRED_REASON)

    # ---------------------------------------------- positions keep their recorded exits

    def test_an_open_position_keeps_its_recorded_exits_and_booking(self):
        points = self.open_neet()
        book = self.books[lf.LAB_A_ID]
        entry_price = book['position']['entry_price']
        # A later release: LAB_FORWARD_TESTS_V2 with a -6% LAB_A stop; V1 stays a known version.
        newer = dataclasses.replace(lf.EXITS_A, label='LAB_A_6_10_60', stop_loss_net_pct=6.0)
        with patch.object(lf, 'VERSION', 'LAB_FORWARD_TESTS_V2'), \
                patch.object(lf, 'KNOWN_VERSIONS', ('LAB_FORWARD_TESTS_V1', 'LAB_FORWARD_TESTS_V2')), \
                patch.dict(lf.EXITS, {lf.LAB_A_ID: newer}):
            mark = dict(points[1], priceUsd=entry_price * 0.955, updatedAt=points[1]['updatedAt'] + 60_000)
            self.clock[0] = mark['updatedAt'] + 500
            lab.update_positions({}, [mark])
        self.assertIsNone(book['position'])
        trade = book['history'][0]
        self.assertLess(trade['model_pnl_pct'], -5.0)
        self.assertGreater(trade['model_pnl_pct'], -6.0)
        self.assertEqual(trade['exit_reason'], 'STOP_LOSS_5_NET', 'the stored -5% stop, not the newer -6%')
        self.assertEqual((trade['lab_forward_version'], trade['lab_config_hash']),
                         ('LAB_FORWARD_TESTS_V1', PINNED_CONFIG_HASHES[lf.LAB_A_ID]))
        self.assertEqual(trade['close_kind'], 'marked')
        self.assertGreater(trade['calibration_cost_usd'], 0)
        self.assertAlmostEqual(trade['net50_usd'], lf.net50(trade['pnl_usd'], 200.0, 'STOP_LOSS_5_NET')['net50_usd'],
                               places=6)
        # A position opened with other (stored) exits keeps them: a -15% stop and a 90 min hold.
        self.refresh(dict(points[0], updatedAt=mark['updatedAt'] + 400_000))
        self.refresh(dict(points[1], updatedAt=mark['updatedAt'] + 405_000))
        position = book['position']
        self.assertIsNotNone(position, book['entry_diagnostics'])
        position['exit_parameters'].update(label='OLDER_15_20_90', stop_loss_net_pct=15.0, take_profit_net_pct=20.0,
                                           max_hold_minutes=90.0)
        mark = dict(points[1], priceUsd=position['entry_price'] * 0.95, updatedAt=position['opened_at'] + 65 * MINUTE)
        self.clock[0] = mark['updatedAt'] + 100
        lab.update_positions({}, [mark])
        self.assertIsNotNone(book['position'], 'neither the -5% stop nor the 60 min hold of the current config')
        stale = {**book['position'], 'quote_status': 'stale'}
        self.assertFalse(lf.unpriced_past_max_hold(stale, position['opened_at'] + 75 * MINUTE))
        self.assertTrue(lf.unpriced_past_max_hold(stale, position['opened_at'] + 90 * MINUTE))
        self.assertFalse(lf.vanish_due(stale, position['opened_at'] + 95 * MINUTE, position['opened_at'], True))
        self.assertTrue(lf.vanish_due(stale, position['opened_at'] + 100 * MINUTE, position['opened_at'], True))
        mark = dict(mark, updatedAt=position['opened_at'] + 90 * MINUTE)
        self.clock[0] = mark['updatedAt'] + 100
        lab.update_positions({}, [mark])
        self.assertEqual(book['history'][0]['exit_reason'], 'ABSOLUTE_MAX_HOLD_90')
        # Malformed stored exits fall back to the book's current ones; other books are not forward.
        self.assertEqual(lf.position_exits({'strategy_id': lf.LAB_A_ID, 'exit_parameters': {'stop_loss_net_pct': 'x'}}),
                         lf.EXITS_A)
        self.assertEqual(lf.position_exits({'strategy_id': lf.LAB_B_ID}), lf.EXITS_B)
        self.assertIn('LAB_FORWARD_TESTS_V1', lf.KNOWN_VERSIONS)
        self.assertFalse(lf.is_forward_position({'strategy_id': lf.LAB_A_ID, 'lab_forward_version': 'OTHER'}))
        self.assertFalse(lf.is_forward_position({'strategy_id': 'TREND', 'lab_forward_version': lf.VERSION}))

    def test_a_drained_pool_is_booked_at_zero_not_at_the_capped_impact(self):
        """LP pull: liquidity 0 at an unchanged price. The shared capped model alone would book about -21%."""
        points = self.open_neet()
        book = self.books[lf.LAB_A_ID]
        mark = dict(points[1], liquidityUsd=0.0, updatedAt=points[1]['updatedAt'] + 60_000)
        self.clock[0] = mark['updatedAt'] + 500
        lab.update_positions({}, [mark])
        self.assertIsNone(book['position'])
        trade = book['history'][0]
        self.assertEqual(trade['exit_reason'], 'STOP_LOSS_5_NET', 'the model trigger is unchanged')
        self.assertEqual((trade['close_kind'], trade['exit_liquidity_usd']), ('drained', 0.0))
        self.assertAlmostEqual(trade['pnl_pct'], -100.0, delta=0.05)
        self.assertLess(trade['model_pnl_pct'], -20.0)
        self.assertGreater(trade['model_pnl_pct'], -23.0)
        self.assertGreater(trade['drain_valuation_cost_usd'], 150.0)
        self.assertGreater(trade['calibration_cost_usd'], 0.0)
        self.assertLess(trade['calibration_cost_usd'], 2.0)
        self.assertAlmostEqual(trade['model_pnl_usd'] - trade['pnl_usd'],
                               trade['calibration_cost_usd'] + trade['drain_valuation_cost_usd'], places=3)
        self.assertAlmostEqual(trade['net50_pct'], -100.0, delta=0.05)
        self.assertEqual(lf.evidence(book, lf.LAB_A_ID, self.clock[0])['drained_closes'], 1)

    def test_a_pool_without_marks_closes_as_vanished_after_max_hold_plus_grace(self):
        points = self.open_neet()
        book = self.books[lf.LAB_A_ID]
        position = book['position']
        opened, last_price = position['opened_at'], position['last_mark']['priceUsd']
        alive = pool(full('OtherMint'), full('OtherPair'), 1.0, 100_000.0, 0)
        unpriced = self.unpriced

        def poll(minutes, *, feed_alive=True):
            self.clock[0] = int(opened + minutes * MINUTE)
            lab.update_positions({}, [dict(alive, updatedAt=self.clock[0] - 1_000)] if feed_alive else [])

        poll(30)
        self.assertIsNotNone(book['position'])
        self.assertEqual(book['position']['quote_status'], 'stale')
        poll(69.9)
        self.assertIsNotNone(book['position'], 'never before max hold + 10 min')
        self.assertTrue(lf.evidence(book, lf.LAB_A_ID, self.clock[0])['open_unpriced_past_max_hold'])
        poll(75, feed_alive=False)
        self.assertIsNotNone(book['position'], 'a dead shared feed is an outage, not a vanished pool')
        unpriced.clear()                                     # a Lab restart forgets the no-mark clock
        poll(76)
        poll(76.9)
        self.assertIsNotNone(book['position'], 'after a restart the exact-pair refresh is retried for 60 s first')
        poll(77.05)
        self.assertIsNone(book['position'])
        trade = book['history'][0]
        self.assertEqual(trade['exit_reason'], 'VANISHED_NO_FRESH_MARK')
        self.assertEqual((trade['close_kind'], trade['quote_status'], trade['vanish_haircut_pct']),
                         ('vanished', 'vanished', 10.0))
        self.assertAlmostEqual(trade['exit_price'], last_price * 0.9, places=12)
        self.assertEqual(trade['last_mark_at'], opened)
        self.assertLess(trade['pnl_pct'], -10.0)
        self.assertGreater(trade['pnl_pct'], -11.5)
        self.assertEqual(trade['net50_exit_extra_bps'], 0.0)
        self.assertAlmostEqual(trade['net50_usd'], lf.net50(trade['pnl_usd'], 200.0, trade['exit_reason'])['net50_usd'],
                               places=6)
        self.assertEqual(unpriced, {})
        self.assertEqual(lf.evidence(book, lf.LAB_A_ID, self.clock[0])['vanished_closes'], 1)

    def test_a_book_that_cannot_fund_its_fixed_entry_reports_cash_exhausted(self):
        crossing = next(c for c in FIXTURES['lab_a_crossings'] if c['name'].startswith('neet'))
        points = [feed_coin(point) for point in crossing['points']]
        self.use_layer(points[0]['updatedAt'])
        book = self.books[lf.LAB_A_ID]
        book['balance'] = 200.05
        self.refresh(points[0])
        self.refresh(points[1])
        self.assertIsNone(book['position'])
        diagnostics = book['entry_diagnostics']
        self.assertEqual(diagnostics['blocked_reason'], 'lab_forward_cash_exhausted')
        self.assertEqual(diagnostics['min_entry_balance_usd'], 200.1)
        self.assertEqual(book['strategy_lifecycle']['status'], 'cash_exhausted')
        self.assertEqual(book['balance'], 200.05)

    def test_a_changed_lab_cost_model_fails_forward_entries_closed(self):
        crossing = next(c for c in FIXTURES['lab_a_crossings'] if c['name'].startswith('neet'))
        points = [feed_coin(point) for point in crossing['points']]
        self.use_layer(points[0]['updatedAt'])
        with patch.object(lab, 'BASE_SLIPPAGE_BPS', 12.0):
            self.refresh(points[0])
            self.refresh(points[1])
        book = self.books[lf.LAB_A_ID]
        self.assertIsNone(book['position'])
        self.assertEqual(book['entry_diagnostics']['blocked_reason'], 'lab_forward_cost_model_mismatch')
        self.assertEqual(book['entry_diagnostics']['cost_model_mismatched_fields'], ['base_slippage_bps'])

    def test_cost_cap_refuses_an_expensive_research_dip_and_never_shrinks_the_size(self):
        dip, coins = regime_inputs()
        decision = feed_coin(dip['decision'])
        reference = feed_coin(dip['reference'])
        self.use_layer(decision['updatedAt'])
        observe(self.layer.history, self.memory, coins + [reference])
        self.refresh(decision, book_ids=(lf.LAB_B_ID,), lag_ms=2_000)
        book = self.books[lf.LAB_B_ID]
        diagnostics = book['entry_diagnostics']
        self.assertEqual(diagnostics['lab_forward']['signals'], 1, diagnostics)
        self.assertAlmostEqual(diagnostics['lab_forward']['regime']['med15_pct'], dip['research_med15_pct'], places=9)
        # The 100 bps tier on $116k: a 3.06% modeled round trip at $200 > the 2.75% ceiling.
        self.assertIsNone(book['position'])
        self.assertEqual(diagnostics['blocked_reason'], 'modeled_roundtrip_cost_limit')
        self.assertEqual(diagnostics['max_entry_roundtrip_cost_pct'], 2.75)
        self.assertEqual(diagnostics['stop_loss_net_pct'], 15.0)
        self.assertEqual(diagnostics['cost_rejected'], 1)

    def test_lab_b_opens_a_cheap_dip_in_a_dipping_market(self):
        T = 1_800_000_000_000 - (1_800_000_000_000 % MINUTE)
        mint, pair = full('DipMint'), full('DipPair')
        cheap = {'marketCap': 30_000_000.0, 'priceNative': 1.0 / 120, 'pairCreatedAt': T - 40 * DAY,
                 'symbol': 'DIPPY', 'name': 'dip fixture', 'priceChange': {'h24': -20.0, 'm5': -3.0, 'h1': -6.0}}
        reference = pool(mint, pair, 1.0, 1_000_000.0, T - 960_000, **cheap)
        decision = pool(mint, pair, 0.89, 950_000.0, T + 3_000, **cheap)
        coins = [reference]
        for index in range(10):
            coins.append(pool(f'w{index}', f'w{index}', 1.0, 60_000.0, T - 905_000))
            coins.append(pool(f'w{index}', f'w{index}', 0.99, 60_000.0, T - 10_000))
        self.use_layer(decision['updatedAt'])
        observe(self.layer.history, self.memory, coins)
        self.refresh(decision, book_ids=(lf.LAB_B_ID, lf.RND_B_ID))
        position = self.books[lf.LAB_B_ID]['position']
        self.assertIsNotNone(position, self.books[lf.LAB_B_ID]['entry_diagnostics'])
        self.assertEqual(position['lab_config_hash'], PINNED_CONFIG_HASHES[lf.LAB_B_ID])
        self.assertAlmostEqual(position['lab_forward']['signal']['regime']['med15_pct'], -1.0, places=9)
        self.assertEqual(position['stop_loss_net_pct'], 15.0)
        self.assertEqual(position['entry_cost_cap_pct'], 2.75)
        rnd = self.books[lf.RND_B_ID]
        drawn = lf.hashed_coin(pair, decision['updatedAt'], 0.0007, 'synB')
        self.assertEqual(rnd['position'] is not None, drawn)
        self.assertEqual(rnd['entry_diagnostics']['lab_forward']['universe_candidates'], 1)
        # -15% net stop on the model.
        mark = dict(decision, priceUsd=0.89 * 0.84, updatedAt=decision['updatedAt'] + 120_000)
        self.clock[0] = mark['updatedAt'] + 100
        lab.update_positions({}, [mark])
        trade = self.books[lf.LAB_B_ID]['history'][0]
        self.assertEqual(trade['exit_reason'], 'STOP_LOSS_15_NET')
        self.assertEqual(trade['net50_exit_extra_bps'], 200.0)

    def test_structural_guard_and_loss_memory_still_apply(self):
        crossing = next(c for c in FIXTURES['lab_a_crossings'] if c['name'].startswith('neet'))
        young = [feed_coin(point, pairCreatedAt=point['updatedAt'] - 600 * MINUTE) for point in crossing['points']]
        self.use_layer(young[0]['updatedAt'])
        self.refresh(young[0])
        self.refresh(young[1])
        book = self.books[lf.LAB_A_ID]
        self.assertIsNone(book['position'])
        self.assertEqual(book['entry_diagnostics']['blocked_reason'], 'rug_young_pool')
        self.assertEqual(self.calls['price'], 0, 'the guard runs before any price check')
        points = [feed_coin(point) for point in crossing['points']]
        book['history'] = [{'address': points[1]['address'], 'pairAddress': points[1]['pairAddress'],
                            'pnl_usd': -5.0, 'closed_at': points[1]['updatedAt'] - age * MINUTE}
                           for age in (30, 60)]
        self.refresh(dict(points[0], updatedAt=points[1]['updatedAt'] - 3_000))
        self.refresh(points[1])
        self.assertIsNone(book['position'])
        self.assertEqual(book['entry_diagnostics']['blocked_reason'], 'pool_loss_cooldown')

    def test_retired_book_stops_entries_only(self):
        points = self.open_neet()
        book = self.books[lf.LAB_A_ID]
        self.assertIsNotNone(book['position'])
        book['strategy_lifecycle'] = {'version': lf.KILL_RULE_VERSION, 'status': 'retired', 'entry_enabled': False,
                                      'position_management_enabled': True, 'reason': 'pre_registered_kill_rule',
                                      'evidence': {}}
        mark = dict(points[1], priceUsd=book['position']['entry_price'] * 1.15, updatedAt=points[1]['updatedAt'] + 9_000)
        self.clock[0] = mark['updatedAt'] + 100
        lab.update_positions({}, [mark])
        self.assertEqual(book['history'][0]['exit_reason'], 'TAKE_PROFIT_10_NET', 'open exits still run')
        self.refresh(dict(points[0], updatedAt=mark['updatedAt'] + 400_000))
        self.refresh(dict(points[1], updatedAt=mark['updatedAt'] + 405_000))
        self.assertIsNone(book['position'])
        self.assertEqual(book['entry_diagnostics']['blocked_reason'], lf.RETIRED_REASON)

    def test_state_and_dashboard_publish_the_books(self):
        self.open_neet()
        for name in ('merge_astra_snapshot', 'merge_paired_snapshot'):
            p = patch.object(lab, name, side_effect=lambda state: state)
            p.start()
            self.addCleanup(p.stop)
        lab.persist('online')
        config = lab.STATE['activity_config']['lab_forward_tests']
        self.assertEqual(config['config_hashes'], PINNED_CONFIG_HASHES)
        self.assertFalse(config['automatic_promotion'])
        self.assertEqual(lab.STATE['activity_config']['lab_forward_tests_state']['version'], lf.MEMORY_VERSION)
        compact = json.loads(lab.COMPACT_PATH.read_text(encoding='utf-8'))
        for book_id in lf.BOOK_IDS:
            with self.subTest(book=book_id):
                self.assertIn(book_id, compact['books'])
                self.assertEqual(compact['books'][book_id]['portfolio_group'], 'TEST')
                self.assertEqual(compact['books'][book_id]['strategy_lifecycle']['version'], lf.KILL_RULE_VERSION)
        published = compact['books'][lf.LAB_A_ID]
        self.assertEqual(published['position']['lab_config_hash'], PINNED_CONFIG_HASHES[lf.LAB_A_ID])
        self.assertIn('heat_history_warming', published['position']['heat_log_only_flags'])
        self.assertEqual(published['entry_diagnostics']['lab_forward']['config_hash'], PINNED_CONFIG_HASHES[lf.LAB_A_ID])
        self.assertIn('promotion_gate', published['strategy_lifecycle'])
        projected = lab_dashboard_projection.compact_strategy_lab({'books': {'X': {
            'history': [{'net50_usd': -3.2, 'net50_pct': -1.6, 'lab_config_hash': 'h', 'model_pnl_usd': -1.0,
                         'close_kind': 'drained', 'drain_valuation_cost_usd': 150.0}]}}})
        self.assertEqual(projected['books']['X']['history'][0]['net50_usd'], -3.2)
        self.assertEqual(projected['books']['X']['history'][0]['lab_config_hash'], 'h')
        self.assertEqual(projected['books']['X']['history'][0]['close_kind'], 'drained')
        self.assertEqual(projected['books']['X']['history'][0]['drain_valuation_cost_usd'], 150.0)
        config = lab.STATE['activity_config']['lab_forward_tests']
        self.assertEqual(config['close_policy']['version'], lf.CLOSE_POLICY_VERSION)
        self.assertEqual(config['cash_state']['version'], lf.CASH_STATE_VERSION)
        self.assertFalse(config['tape_pin_required'])


# ------------------------------------------------------------------ tape seats

class TapeSeatTests(unittest.TestCase):
    """tape_pool_scheduler V6: forward-test positions never read flow, so they take no tape seat."""
    NOW = 1_800_000_000_000

    def lab_state(self, *, forward=True, other=True):
        books = {}
        if forward:
            for index, book_id in enumerate(lf.BOOK_IDS):
                books[book_id] = {'id': book_id, 'position': {
                    'strategy_id': book_id, 'lab_forward_version': lf.VERSION,
                    'lab_config_hash': lf.CONFIG_HASHES[book_id], 'address': full(f'Fw{index}M'),
                    'pairAddress': full(f'Fw{index}P'), 'dexId': 'pumpswap', 'quoteTokenAddress': SOL,
                    'symbol': f'FW{index}', 'opened_at': self.NOW - MINUTE}}
        if other:
            books['TREND'] = {'id': 'TREND', 'position': {
                'strategy_id': 'TREND', 'address': full('TrndM'), 'pairAddress': full('TrndP'),
                'dexId': 'pumpswap', 'quoteTokenAddress': SOL, 'symbol': 'TRND', 'opened_at': self.NOW - MINUTE}}
        # The tape reads main's /state, which carries the compact Lab projection.
        return {'feed': [], 'strategy_lab': lab_dashboard_projection.compact_strategy_lab({'books': books})}

    def test_forward_positions_leave_entry_capacity_unchanged(self):
        scheduler = tape_pool_scheduler.TapePoolScheduler
        _, baseline = scheduler().select(self.lab_state(forward=False), now=self.NOW, max_tracked=4)
        selected, report = scheduler().select(self.lab_state(), now=self.NOW, max_tracked=4)
        self.assertEqual((baseline['pinned_exit_pools'], baseline['entry_capacity']), (1, 3))
        self.assertEqual((report['pinned_exit_pools'], report['entry_capacity']), (1, 3))
        self.assertEqual([coin['pairAddress'] for coin in selected], [full('TrndP')])
        self.assertEqual(report['unpinned_flow_free_lab_positions'], 4)
        self.assertEqual(report['lab_pin_rule'], tape_pool_scheduler.LAB_PIN_RULE)
        self.assertEqual(report['policy_version'], 'STABLE_COST_AWARE_TAPE_DISCOVERY_V6_NO_PINS_FOR_FLOW_FREE_LAB_BOOKS')
        self.assertEqual(report['previous_policy_version'], 'STABLE_COST_AWARE_TAPE_DISCOVERY_V5_DEFENSIVE_ENTRY')
        # A pool main also holds keeps its pin (main's exit reads flow).
        state = self.lab_state(other=False)
        held = state['strategy_lab']['books'][lf.LAB_A_ID]['position']
        state['positions'] = [{'address': held['address'], 'pairAddress': held['pairAddress'], 'dexId': 'pumpswap'}]
        _, shared = scheduler().select(state, now=self.NOW, max_tracked=4)
        self.assertEqual((shared['pinned_exit_pools'], shared['entry_capacity']), (1, 3))
        # Any other Lab position (including one stamped with another forward version) is pinned as before.
        state = self.lab_state(other=False)
        state['strategy_lab']['books'][lf.RND_B_ID]['position']['lab_forward_version'] = 'LAB_FORWARD_TESTS_V0'
        _, stamped = scheduler().select(state, now=self.NOW, max_tracked=4)
        self.assertEqual((stamped['pinned_exit_pools'], stamped['entry_capacity']), (1, 3))
        self.assertFalse(lf.tape_pin_required({'strategy_id': lf.LAB_A_ID, 'lab_forward_version': lf.VERSION}))
        self.assertTrue(lf.tape_pin_required({'strategy_id': 'TREND'}))
        self.assertTrue(lf.tape_pin_required({'strategy_id': 'TREND', 'lab_forward_version': lf.VERSION}))


if __name__ == '__main__':
    unittest.main()
