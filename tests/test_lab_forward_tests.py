"""LAB_FORWARD_TESTS_V1: four PAPER Strategy Lab books forward-testing two pre-registered hypotheses.

PAPER only and offline. Realistic fixtures come from the research scan log
(tests/fixtures/lab_forward_obs_20261008.json, extracted read-only from
obs.sqlite3 table o with the frozen research signals; mint and pool abbreviated
to 8 characters and padded to a valid length where the Lab needs one). Prices,
safety and marks are patched; no test calls an external API or touches a real
ledger. Nothing here measures or claims profitability.
"""
import copy
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
    'LAB_A_SURGE_EST_GUARD': 'c97d2b4fd03c6df6376b0173a3b39c015928e7ccb1419bbee167b3b938c43644',
    'RND_LAB_A': 'a0a7f03f75fc3f66d33ec201e1461c177878cdd411194f82b751c513f7faba02',
    'LAB_B_DIP_MKTDIP_GUARD': '8f6e09cb1f3aef7ba9eec3425de019ce25614ff59bee77e9b884d3f134d87f39',
    'RND_LAB_B': 'fdf97559ac929c7a2b30a53358256f3912e9085bdb1bff6453b4c7eecaaf5ab1',
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
                     'beats_same_period_control_usd', 'top_pair_share', 'mean_without_best_pair_usd',
                     'best_day_share_of_pnl', 'entries_on_rug_flagged_pools', 'vanished_or_unpriced_share'):
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


# ------------------------------------------------------------------ Strategy Lab integration

class LabIntegrationTests(unittest.TestCase):
    def setUp(self):
        lab.rush_brain._SAMPLE_BY_PAIR.clear()
        self.books = {s['id']: lab.empty_book(s) for s in lab.STRATEGIES}
        self.clock = [0]
        self.calls = {'rugcheck': 0, 'price': 0}
        self.memory = lf.ForwardFeedMemory()

        def price(coin):
            self.calls['price'] += 1
            return {'status': 'pass', 'mint': coin['address'], 'pair': coin['pairAddress']}

        def rugcheck(coin):
            self.calls['rugcheck'] += 1
            return {'status': 'pass', 'mint': coin['address'], 'pair': coin['pairAddress']}

        def resolve(position, prices, now):
            coin = prices.get((position.get('address'), position.get('pairAddress')))
            return dict(coin, mark_received_at=coin['updatedAt'], mark_source='SHARED_LIVE_FEED_EXACT_POOL') if coin else None

        for p in (patch.object(lab, 'STATE', {'started_at': 42, 'books': self.books}),
                  patch.object(lab, 'FORWARD_MEMORY', self.memory),
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
            'history': [{'net50_usd': -3.2, 'net50_pct': -1.6, 'lab_config_hash': 'h', 'model_pnl_usd': -1.0}]}}})
        self.assertEqual(projected['books']['X']['history'][0]['net50_usd'], -3.2)
        self.assertEqual(projected['books']['X']['history'][0]['lab_config_hash'], 'h')


if __name__ == '__main__':
    unittest.main()
