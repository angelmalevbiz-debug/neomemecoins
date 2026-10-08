"""Tape seats for the COST_FIRST universe and pins for personal-engine positions.

STABLE_COST_AWARE_TAPE_DISCOVERY_V4_COST_FIRST_PINS keeps every V3 rule and
adds two observation-only changes:

1. Pools in the COST_FIRST universe (cost_first_established.candidate, the
   Lab book pair's definition) form a seat group directly below estimated
   feasible main/funded candidates and above matched candidates whose modeled
   round trip already exceeds the cost cap (or is unknown). Same seat budget,
   same leases; only an estimated feasible candidate pre-empts a lease.
2. Open positions of every personal PAPER engine in the account registry are
   pinned by exact (mint, pool) like main's. The registry is read-only; each
   engine's /state is read by a background refresher (never on the tape
   poll), failed ports back off, an unreachable engine keeps its last
   positions (published as stale) until they are seen closed, and pools held
   only by personal engines are capped after main's and Lab's pins.

Nothing here admits, sizes or exits a trade. Every engine fetch is injected,
except one test that reads a closed loopback port on the refresher thread.
"""
import importlib.util
import json
import os
import socket
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import cost_first_established as cost_first
import lab_activity
import live_tape as tape
import paper_market_feasibility as feasibility
import strategy_lab as lab
import structural_rug_guard
import tape_pool_scheduler as scheduler_module
from tape_pool_scheduler import PersonalEnginePositions, TapePoolScheduler
import entry_defense as _isolation_entry_defense
import strategy_lab as _isolation_lab
import tape_pool_scheduler as _isolation_tape

_DEFENSIVE_ISOLATION = []


def _defensive_pass(*_args, **_kwargs):
    return _isolation_entry_defense.pass_decision('TEST_GATE_ISOLATION')


def setUpModule():
    """These tests isolate other entry gates. DEFENSIVE_ENTRY_LAYER_V1 (structural rug
    guard, pool loss memory, heat veto and its warm-up) has its own suite in
    tests/test_defensive_entry_layer.py, which proves every path consults it."""
    for target, name in ((_isolation_lab, 'defensive_entry_decision'), (_isolation_tape.TapePoolScheduler, 'defensive_entry_decision'),):
        isolation = patch.object(target, name, _defensive_pass)
        isolation.start()
        _DEFENSIVE_ISOLATION.append(isolation)


def tearDownModule():
    while _DEFENSIVE_ISOLATION:
        _DEFENSIVE_ISOLATION.pop().stop()


NOW = 1_800_000_000_000
SOL_USD = 115


def market_coin(name, *, cap=10_000_000, liquidity=1_500_000, activity=60, score=98):
    """A main/funded-matching market observation (same shape as the V3 tests)."""
    return {'address': name + '-mint', 'pairAddress': name + '-pair',
            'symbol': name, 'dexId': 'pumpswap',
            'quoteTokenAddress': feasibility.SOL_QUOTE_MINT,
            'priceUsd': .001, 'priceNative': .001 / SOL_USD,
            'score': score, 'marketCap': cap, 'liquidityUsd': liquidity,
            'ageMinutes': 30, 'updatedAt': NOW,
            'priceChange': {'m5': 5, 'h1': 10},
            'txns': {'m5': {'buys': activity * 2 // 3, 'sells': activity // 3}},
            'volume': {'h1': liquidity * .5}}


def cost_first_coin(name, *, activity=60, liquidity=1_500_000, cap=10_000_000):
    """In the COST_FIRST universe (37.5 bps tier, >= $250k) but matching no
    main or funded market rule (score 0, no momentum)."""
    coin = market_coin(name, cap=cap, liquidity=liquidity, activity=activity, score=0)
    coin.update(priceChange={'m5': -40, 'h1': -60}, ageMinutes=100_000, volume={'h1': 0},
                pairCreatedAt=NOW - 100_000 * 60_000)
    return coin


def universe_candidate(coin, **kwargs):
    """cost_first_established.candidate (V2: with STRUCTURAL_RUG_GUARD_V1) for a fresh registry at NOW."""
    return cost_first.candidate(coin, now=NOW, ticker_registry=structural_rug_guard.TickerRegistry(), **kwargs)


def feasible_coin(name):
    """Estimated feasible for main/funded, but outside COST_FIRST (55 bps tier)."""
    return market_coin(name, cap=5_750_000)


def over_budget_coin(name):
    """Matches main/funded market rules but its modeled fixed cost exceeds the cap."""
    return market_coin(name, cap=100_000, liquidity=15_000)


def position(coin, dex='pumpswap'):
    return {'address': coin['address'], 'pairAddress': coin['pairAddress'],
            'coin_snapshot': {'dexId': dex, 'symbol': coin['symbol'], 'priceUsd': 1}}


def pin(name, dex='pumpswap'):
    return {'address': name + '-mint', 'pairAddress': name + '-pair',
            'coin_snapshot': {'dexId': dex, 'symbol': name, 'priceUsd': 1}}


class FixturePreconditions(unittest.TestCase):
    def test_fixtures_are_what_the_tests_claim(self):
        self.assertTrue(universe_candidate(cost_first_coin('cf'), cap_usd=150))
        self.assertEqual(lab_activity.market_features(cost_first_coin('cf'))['score'], 0)
        _, report = TapePoolScheduler().select({'feed': [cost_first_coin('cf')]}, now=NOW, max_tracked=1)
        self.assertEqual((report['estimated_feasible_market_candidates'],
                          report['estimated_fixed_cost_over_budget'],
                          report['estimated_cost_unknown']), (0, 0, 0))
        _, report = TapePoolScheduler().select({'feed': [over_budget_coin('ob')]}, now=NOW, max_tracked=1)
        self.assertEqual(report['estimated_fixed_cost_over_budget'], 1)
        self.assertFalse(universe_candidate(over_budget_coin('ob'), cap_usd=150))

    def test_planning_notional_is_the_lab_book_definition(self):
        self.assertEqual(scheduler_module.COST_FIRST_PLANNING_NOTIONAL_USD, lab.TRADE_NOTIONAL)
        self.assertEqual(scheduler_module.COST_FIRST_MIN_NOTIONAL_USD, lab_activity.MIN_NOTIONAL_USD)


class CostFirstSeatTests(unittest.TestCase):
    def test_cost_first_ranks_above_a_matched_over_budget_candidate(self):
        feed = [over_budget_coin('costly'), cost_first_coin('cheap')]
        selected, report = TapePoolScheduler().select({'feed': feed}, now=NOW, max_tracked=1)
        self.assertEqual([row['symbol'] for row in selected], ['cheap'])
        self.assertEqual(report['policy_version'], scheduler_module.POLICY_VERSION)
        self.assertEqual(report['previous_policy_version'], 'STABLE_COST_AWARE_TAPE_DISCOVERY_V4_COST_FIRST_PINS')
        self.assertEqual((report['selected_cost_first_pools'], report['unselected_cost_first_pools']), (1, 0))
        self.assertEqual(report['cost_first']['candidate_pools'], 1)
        self.assertEqual(report['estimated_fixed_cost_over_budget'], 1)
        self.assertFalse(report['cost_first']['is_entry_authorization'])
        self.assertFalse(report['model_is_execution_quote'])
        self.assertFalse(report['profitability_proven'])

    def test_cost_first_ranks_above_unmatched_exploration_and_unknown_cost(self):
        unknown = market_coin('unknown')
        unknown['quoteTokenAddress'] = None
        exploration = cost_first_coin('explore', liquidity=20_000)
        self.assertFalse(universe_candidate(exploration, cap_usd=150))
        feed = [unknown, exploration, cost_first_coin('cheap')]
        selected, report = TapePoolScheduler().select({'feed': feed}, now=NOW, max_tracked=1)
        self.assertEqual([row['symbol'] for row in selected], ['cheap'])
        self.assertEqual(report['estimated_cost_unknown'], 1)

    def test_estimated_feasible_candidate_still_precedes_cost_first(self):
        feasible = feasible_coin('feasible')
        self.assertFalse(universe_candidate(feasible, cap_usd=150))
        selected, report = TapePoolScheduler().select(
            {'feed': [cost_first_coin('cheap', activity=900), feasible]}, now=NOW, max_tracked=1)
        self.assertEqual([row['symbol'] for row in selected], ['feasible'])
        self.assertEqual(report['estimated_feasible_market_candidates'], 1)
        self.assertEqual((report['selected_cost_first_pools'], report['unselected_cost_first_pools']), (0, 1))
        self.assertEqual(report['cost_first']['examples'][0]['selected'], False)

    def test_cost_first_member_that_is_also_estimated_feasible_stays_in_the_feasible_group(self):
        both = market_coin('both')
        both['pairCreatedAt'] = NOW - 100_000 * 60_000   # established pool: the structural guard passes
        self.assertTrue(universe_candidate(both, cap_usd=150))
        _, report = TapePoolScheduler().select({'feed': [both]}, now=NOW, max_tracked=1)
        self.assertEqual(report['cost_first']['also_estimated_feasible'], 1)
        self.assertEqual(report['selected_exploration_pools'], 0)
        self.assertEqual(report['cost_first']['examples'][0]['group'], scheduler_module.GROUP_FEASIBLE)

    def test_seat_budget_is_never_exceeded_and_counts_add_up(self):
        feed = [cost_first_coin(f'cf{index}') for index in range(10)] + [over_budget_coin('costly')]
        state = {'feed': feed, 'positions': [pin('held-a'), pin('held-b')]}
        selected, report = TapePoolScheduler().select(state, now=NOW, max_tracked=4)
        self.assertEqual(len(selected), 4)
        self.assertEqual(report['entry_capacity'], 2)
        self.assertEqual(report['selected_entry_pools'], 2)
        self.assertEqual((report['selected_cost_first_pools'], report['unselected_cost_first_pools']), (2, 8))
        self.assertEqual(len(report['cost_first']['examples']), scheduler_module.COST_FIRST_EXAMPLE_LIMIT)
        self.assertTrue(all(row['is_execution_quote'] is False for row in report['cost_first']['examples']))
        self.assertNotIn('costly-pair', report['selected_pairs'])

    def test_pins_beyond_the_budget_leave_no_cost_first_seat(self):
        state = {'feed': [cost_first_coin('cheap')], 'positions': [pin(f'held{index}') for index in range(5)]}
        selected, report = TapePoolScheduler().select(state, now=NOW, max_tracked=4)
        self.assertEqual(len(selected), 5)
        self.assertEqual(report['entry_capacity'], 0)
        self.assertEqual((report['selected_cost_first_pools'], report['unselected_cost_first_pools']), (0, 1))

    def test_existing_cost_aware_priority_orders_cost_first_pools(self):
        feed = [cost_first_coin('quiet', activity=3), cost_first_coin('active', activity=60)]
        selected, _ = TapePoolScheduler().select({'feed': feed}, now=NOW, max_tracked=1)
        self.assertEqual([row['symbol'] for row in selected], ['active'])

    def test_cost_first_waits_for_an_exploration_lease_but_feasible_still_preempts(self):
        scheduler = TapePoolScheduler()
        costly = over_budget_coin('costly')
        selected, _ = scheduler.select({'feed': [costly]}, now=NOW, max_tracked=1)
        self.assertEqual(selected[0]['symbol'], 'costly')
        feed = [costly, cost_first_coin('cheap')]
        selected, _ = scheduler.select({'feed': feed}, now=NOW + 2_000, max_tracked=1)
        self.assertEqual(selected[0]['symbol'], 'costly')  # running lease kept
        selected, _ = scheduler.select({'feed': feed}, now=NOW + 60_000, max_tracked=1)
        self.assertEqual(selected[0]['symbol'], 'cheap')  # first refill after expiry
        feasible = feasible_coin('feasible')
        selected, _ = scheduler.select({'feed': feed + [feasible]}, now=NOW + 62_000, max_tracked=1)
        self.assertEqual(selected[0]['symbol'], 'feasible')  # V3 pre-emption unchanged

    def test_cost_first_lease_is_retained_before_lower_groups_when_seats_shrink(self):
        scheduler = TapePoolScheduler()
        feed = [cost_first_coin('cheap'), over_budget_coin('costly')]
        selected, _ = scheduler.select({'feed': feed}, now=NOW, max_tracked=2)
        self.assertEqual({row['symbol'] for row in selected}, {'cheap', 'costly'})
        selected, _ = scheduler.select({'feed': feed, 'positions': [pin('held')]}, now=NOW + 1_000,
                                       max_tracked=2)
        self.assertEqual([row['symbol'] for row in selected], ['held', 'cheap'])

    def test_universe_membership_matches_the_lab_definition_and_rejections_are_published(self):
        feed = [cost_first_coin('cheap'), cost_first_coin('thin', liquidity=100_000),
                cost_first_coin('pricey', cap=100_000), over_budget_coin('costly')]
        _, report = TapePoolScheduler().select({'feed': feed}, now=NOW, max_tracked=4)
        expected = sum(universe_candidate(coin, cap_usd=lab.TRADE_NOTIONAL,
                                            minimum_notional_usd=lab_activity.MIN_NOTIONAL_USD)
                       for coin in feed)
        self.assertEqual(report['cost_first']['candidate_pools'], expected)
        self.assertEqual(report['cost_first']['universe_version'], cost_first.UNIVERSE_VERSION)
        self.assertEqual(report['cost_first']['rejections'],
                         {'fee_tier_above_maximum': 1, 'liquidity_below_minimum': 2})

    def test_shed_cost_first_pool_releases_its_seat_like_any_candidate(self):
        cheap = cost_first_coin('cheap')
        stats = {'bodies': 40, 'decoded_swaps': 0, 'usable_swaps': 0, 'shadow_swaps': 0}
        selected, report = TapePoolScheduler().select(
            {'feed': [cheap]}, now=NOW, max_tracked=1, decode_yield=lambda pair, since=0: stats)
        self.assertEqual(selected, [])
        self.assertEqual(report['shed_pool_count'], 1)
        self.assertEqual(report['cost_first']['candidate_pools'], 0)


class RegistryFixture(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.registry = Path(self.directory.name) / 'user_accounts.json'
        self.engines = {}
        self.calls = []

    def write_registry(self, accounts):
        self.registry.write_text(json.dumps({'accounts': accounts}), encoding='utf-8')

    def fetch(self, port, timeout_seconds):
        self.calls.append((port, timeout_seconds))
        engine = self.engines.get(port)
        if isinstance(engine, Exception):
            raise engine
        if engine is None:
            raise ConnectionRefusedError('engine not listening')
        return engine

    def source(self, **kwargs):
        return PersonalEnginePositions(self.fetch, registry_path=self.registry, **kwargs)


class PersonalEngineSourceTests(RegistryFixture):
    def test_missing_registry_is_skipped_without_any_fetch(self):
        positions, report = self.source().refresh(NOW)
        self.assertEqual(positions, [])
        self.assertEqual(report['registry_status'], 'MISSING')
        self.assertEqual(self.calls, [])

    def test_unreadable_registry_is_skipped_without_any_fetch(self):
        for text in ('{not json', '[]', '{"accounts": []}'):
            self.registry.write_text(text, encoding='utf-8')
            positions, report = self.source().refresh(NOW)
            self.assertEqual((positions, report['registry_status']), ([], 'UNREADABLE'))
        self.assertEqual(self.calls, [])

    def test_registry_unreadable_after_a_good_read_keeps_the_last_good_ports(self):
        self.write_registry({'a': {'engine_port': 18800}})
        self.engines[18800] = {'positions': [pin('a1')]}
        source = self.source(cache_ms=1_000)
        source.refresh(NOW)
        self.registry.write_text('{"accounts": {"a": {"engine_port": 188', encoding='utf-8')  # mid-write
        positions, report = source.refresh(NOW + 1_000)
        self.assertEqual([row['pairAddress'] for row in positions], ['a1-pair'])
        self.assertEqual(report['registry_status'], 'UNREADABLE_USING_LAST_GOOD')
        self.registry.unlink()
        positions, report = source.refresh(NOW + 2_000)
        self.assertEqual((positions, report['registry_status']), ([], 'MISSING'))

    def test_registry_is_only_read(self):
        self.write_registry({'user-a': {'engine_port': 18800, 'balance': 990.0}})
        before = (self.registry.read_bytes(), self.registry.stat().st_mtime_ns)
        self.engines[18800] = {'positions': []}
        self.source().refresh(NOW)
        self.assertEqual((self.registry.read_bytes(), self.registry.stat().st_mtime_ns), before)

    def test_invalid_and_duplicate_ports_are_skipped_and_limit_applies(self):
        self.write_registry({'a': {'engine_port': 18800}, 'b': {'engine_port': '18800'},
                             'c': {'engine_port': True}, 'd': {'engine_port': 80},
                             'e': {'engine_port': 'x'}, 'f': {}, 'g': 'bad',
                             'h': {'engine_port': 18802}, 'i': {'engine_port': 18801},
                             'j': {'engine_port': [18803]}})
        for port in (18800, 18801, 18802):
            self.engines[port] = {'positions': []}
        _, report = self.source(engine_limit=2).refresh(NOW)
        self.assertEqual(sorted(port for port, _ in self.calls), [18800, 18801])
        self.assertEqual((report['engines_listed'], report['engines_over_read_limit'],
                          report['due_over_read_limit_last_refresh']), (3, 1, 1))
        self.assertIn(scheduler_module.OVER_READ_LIMIT_ALERT, report['operator_alerts'])

    def test_unreachable_engine_is_ignored_and_others_are_pinned(self):
        self.write_registry({'a': {'engine_port': 18800}, 'b': {'engine_port': 18801},
                             'c': {'engine_port': 18802}})
        self.engines[18800] = {'positions': [pin('a1')]}
        self.engines[18801] = TimeoutError('busy')
        self.engines[18802] = {'no_positions': True}  # malformed state
        positions, report = self.source().refresh(NOW)
        self.assertEqual([row['pairAddress'] for row in positions], ['a1-pair'])
        self.assertEqual((report['engines_reachable'], report['engines_never_reached']), (1, 2))
        self.assertTrue(all(timeout <= 2.0 for _, timeout in self.calls))

    def test_each_port_is_fetched_at_most_once_per_cache_period_even_after_failure(self):
        self.write_registry({'a': {'engine_port': 18800}, 'b': {'engine_port': 18801}})
        self.engines[18800] = {'positions': [pin('a1')]}
        source = self.source(cache_ms=5_000)
        for offset in (0, 1_000, 2_000, 4_999):
            positions, report = source.refresh(NOW + offset)
            self.assertEqual([row['pairAddress'] for row in positions], ['a1-pair'])
        self.assertEqual(sorted(self.calls), [(18800, source.timeout_seconds), (18801, source.timeout_seconds)])
        self.assertEqual(report['reads_last_refresh'], 0)
        source.refresh(NOW + 5_000)
        self.assertEqual(len(self.calls), 4)

    def test_failed_engine_keeps_its_positions_as_stale_until_seen_closed(self):
        self.write_registry({'a': {'engine_port': 18800}})
        self.engines[18800] = {'positions': [pin('a1')]}
        source = self.source(cache_ms=1_000, stale_ms=10_000, backoff_max_ms=2_000)
        source.refresh(NOW)
        self.engines[18800] = ConnectionResetError('restarting')
        positions, report = source.refresh(NOW + 5_000)
        self.assertEqual([row['pairAddress'] for row in positions], ['a1-pair'])
        self.assertNotIn(scheduler_module.PERSONAL_STALE_MARK, positions[0])
        self.assertEqual((report['engines_failing_positions_kept'], report['engines_stale']), (1, 0))
        # Past the stale age the pin is kept (exit safety) and published as stale.
        positions, report = source.refresh(NOW + 60_000)
        self.assertEqual([row['pairAddress'] for row in positions], ['a1-pair'])
        self.assertTrue(positions[0][scheduler_module.PERSONAL_STALE_MARK])
        self.assertEqual((report['engines_stale'], report['stale_positions']), (1, 1))
        self.assertIn('PERSONAL_ENGINE_POSITIONS_STALE', report['operator_alerts'])
        # The stale mark is added to a copy; the engine's own row is unchanged.
        self.assertNotIn(scheduler_module.PERSONAL_STALE_MARK, source.engines[18800]['positions'][0])
        self.engines[18800] = {'positions': []}
        positions, report = source.refresh(NOW + 120_000)
        self.assertEqual((positions, report['engines_reachable'], report['engines_stale']), ([], 1, 0))

    def test_closed_position_is_dropped_on_the_next_successful_read(self):
        self.write_registry({'a': {'engine_port': 18800}})
        self.engines[18800] = {'positions': [pin('a1')]}
        source = self.source(cache_ms=1_000)
        source.refresh(NOW)
        self.engines[18800] = {'positions': []}
        positions, _ = source.refresh(NOW + 1_000)
        self.assertEqual(positions, [])

    def test_removed_account_stops_being_polled_after_the_registry_refresh(self):
        self.write_registry({'a': {'engine_port': 18800}})
        self.engines[18800] = {'positions': [pin('a1')]}
        source = self.source(cache_ms=1_000)
        source.refresh(NOW)
        self.write_registry({})
        positions, report = source.refresh(NOW + 1_000)
        self.assertEqual((positions, report['engines_listed']), ([], 0))
        self.assertEqual(len(self.calls), 1)

    def test_diagnostics_carry_no_account_identity(self):
        self.write_registry({'00000000-aaaa-bbbb-cccc-123456789abc': {'engine_port': 18800,
                                                                      'email': 'x@example.invalid'}})
        self.engines[18800] = {'positions': []}
        _, report = self.source().refresh(NOW)
        text = json.dumps(report)
        self.assertNotIn('00000000', text)
        self.assertNotIn('example.invalid', text)


class PersonalRefresherOffThePollTests(RegistryFixture):
    """The tape poll never waits on a personal engine (review finding, PR #19)."""

    def test_failed_port_backoff_doubles_to_the_ceiling_and_resets_on_success(self):
        self.write_registry({'a': {'engine_port': 18800}})
        source = self.source(cache_ms=5_000, backoff_max_ms=60_000)
        self.assertEqual([source.backoff_ms(n) for n in range(1, 7)],
                         [5_000, 10_000, 20_000, 40_000, 60_000, 60_000])
        attempts = []
        for second in range(0, 300):
            before = len(self.calls)
            source.refresh(NOW + second * 1_000)
            if len(self.calls) > before:
                attempts.append(second)
        gaps = [later - earlier for earlier, later in zip(attempts, attempts[1:])]
        self.assertEqual(gaps[:6], [5, 10, 20, 40, 60, 60])
        self.assertTrue(all(gap == 60 for gap in gaps[4:]))
        self.engines[18800] = {'positions': [pin('a1')]}
        second = attempts[-1] + 60
        positions, _ = source.refresh(NOW + second * 1_000)
        self.assertEqual([row['pairAddress'] for row in positions], ['a1-pair'])
        self.assertEqual(source.engines[18800]['next_at'], NOW + second * 1_000 + 5_000)

    def test_eight_dead_ports_cost_at_most_their_backoff_not_every_refresh(self):
        self.write_registry({str(port): {'engine_port': port} for port in range(18800, 18808)})
        source = self.source(cache_ms=5_000, backoff_max_ms=60_000)
        for second in range(0, 600, 5):
            source.refresh(NOW + second * 1_000)
        # 8 ports x (5, 10, 20, 40 s, then every 60 s) over 10 minutes.
        self.assertLessEqual(len(self.calls), 8 * 14)
        self.assertEqual(source.latest(NOW + 600_000)[1]['engines_backing_off'], 8)

    def test_over_limit_ports_rotate_and_ports_with_open_positions_go_first(self):
        ports = list(range(18800, 18806))
        self.write_registry({str(port): {'engine_port': port} for port in ports})
        for port in ports:
            self.engines[port] = {'positions': []}
        self.engines[18805] = {'positions': [pin('held')]}
        source = self.source(cache_ms=1_000, engine_limit=2)
        seen = []
        for step in range(3):
            before = len(self.calls)
            source.refresh(NOW + step * 1_000)
            seen.append(sorted(port for port, _ in self.calls[before:]))
        self.assertEqual(seen[0], [18800, 18801])
        self.assertEqual(seen[1], [18802, 18803])
        # Never-read ports come first; afterwards 18805, now known to hold an
        # open position, is preferred over the least recently read ports.
        self.assertEqual(seen[2], [18804, 18805])
        before = len(self.calls)
        source.refresh(NOW + 3_000)
        self.assertIn(18805, [port for port, _ in self.calls[before:]])
        self.assertEqual(sorted(set(port for port, _ in self.calls)), ports)

    def test_latest_never_fetches(self):
        self.write_registry({'a': {'engine_port': 18800}})
        self.engines[18800] = {'positions': [pin('a1')]}
        source = self.source()
        positions, report = source.latest(NOW)
        self.assertEqual((positions, report['registry_status'], self.calls), ([], 'NOT_REFRESHED', []))
        source.refresh(NOW)
        calls = len(self.calls)
        for offset in range(0, 100_000, 1_000):
            source.latest(NOW + offset)
        self.assertEqual(len(self.calls), calls)
        self.assertTrue(source.latest(NOW + 60_000)[1]['snapshot_stale'])
        self.assertEqual(len(source.latest(NOW + 60_000)[0]), 1)  # pins kept while stale

    def test_snapshot_updates_asynchronously_on_the_refresher_thread(self):
        self.write_registry({'a': {'engine_port': 18800}})
        self.engines[18800] = {'positions': [pin('a1')]}
        source = self.source(cache_ms=1_000)
        self.addCleanup(source.stop, 5)
        self.assertTrue(source.start())
        self.assertFalse(source.start())  # idempotent
        deadline = time.monotonic() + 5
        while not source.latest(scheduler_module._wall_ms())[0] and time.monotonic() < deadline:
            time.sleep(.01)
        positions, report = source.latest(scheduler_module._wall_ms())
        self.assertEqual([row['pairAddress'] for row in positions], ['a1-pair'])
        self.assertEqual((report['refresher'], report['reads_on_tape_poll']), ('RUNNING', False))
        self.assertNotEqual(threading.current_thread().ident,
                            source._thread.ident)

    def test_sleeping_engine_does_not_delay_feed_snapshot(self):
        self.write_registry({'a': {'engine_port': 18800}})
        release = threading.Event()
        self.addCleanup(release.set)

        def slow_fetch(port, timeout_seconds):
            release.wait(10)
            return {'positions': [pin('slow')]}

        source = PersonalEnginePositions(slow_fetch, registry_path=self.registry, cache_ms=1_000)
        self.addCleanup(source.stop, 15)
        self.addCleanup(release.set)
        source.start()
        main = {'feed': [feasible_coin('feasible')], 'positions': [pin('main')]}
        with patch.object(tape.SESSION, 'get', return_value=FeedSnapshotWiringTests.Response(main)), \
                patch.object(tape, '_POOL_SCHEDULER', TapePoolScheduler()), \
                patch.object(tape, '_PERSONAL_ENGINES', source), \
                patch.object(tape, 'shared_quote_reference', return_value=None), \
                patch.object(tape, 'MAX_TRACKED', 2):
            time.sleep(.05)  # the refresher is now blocked inside slow_fetch
            started = time.perf_counter()
            rows = tape.feed_snapshot()
            elapsed = time.perf_counter() - started
        self.assertLess(elapsed, .1)
        self.assertEqual([row['pair'] for row in rows], ['main-pair', 'feasible-pair'])

    def test_closed_loopback_port_does_not_delay_feed_snapshot(self):
        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        probe.bind(('127.0.0.1', 0))
        port = probe.getsockname()[1]
        probe.close()  # nothing listens here now
        self.write_registry({'dead': {'engine_port': port}})
        source = PersonalEnginePositions(tape.personal_engine_state, registry_path=self.registry,
                                         cache_ms=1_000, timeout_seconds=.75)
        self.addCleanup(source.stop, 5)
        source.start()
        main = {'feed': [feasible_coin('feasible')], 'positions': [pin('main')]}
        durations = []
        with patch.object(tape.SESSION, 'get', return_value=FeedSnapshotWiringTests.Response(main)), \
                patch.object(tape, '_POOL_SCHEDULER', TapePoolScheduler()), \
                patch.object(tape, '_PERSONAL_ENGINES', source), \
                patch.object(tape, 'shared_quote_reference', return_value=None), \
                patch.object(tape, 'MAX_TRACKED', 2):
            for _ in range(20):
                started = time.perf_counter()
                tape.feed_snapshot()
                durations.append(time.perf_counter() - started)
                time.sleep(.05)
        self.assertLess(max(durations), .1)
        deadline = time.monotonic() + 5
        while (source.latest(scheduler_module._wall_ms())[1].get('engines_never_reached') != 1
               and time.monotonic() < deadline):
            time.sleep(.02)
        self.assertEqual(source.latest(scheduler_module._wall_ms())[1]['engines_never_reached'], 1)


class PersonalPinSchedulingTests(unittest.TestCase):
    def test_personal_positions_are_pinned_before_entry_seats(self):
        state = {'feed': [cost_first_coin('cheap'), feasible_coin('feasible')],
                 'positions': [pin('main')]}
        selected, report = TapePoolScheduler().select(
            state, now=NOW, max_tracked=2, personal_positions=[pin('user')])
        self.assertEqual([row['pairAddress'] for row in selected], ['main-pair', 'user-pair'])
        self.assertEqual((report['pinned_exit_pools'], report['pinned_personal_pools'],
                          report['personal_pins_operator']['pinned_personal_only_pools'],
                          report['entry_capacity']), (2, 1, 1, 0))
        self.assertEqual(report['selected_pairs'], ['main-pair', 'user-pair'])

    def test_duplicate_pools_across_engines_and_main_take_one_seat(self):
        shared, other = pin('shared'), pin('other')
        state = {'feed': [cost_first_coin('cheap')], 'positions': [shared],
                 'strategy_lab': {'books': {'book': {'position': other}}}}
        personal = [dict(shared), dict(other), pin('user'), pin('user'), dict(shared)]
        selected, report = TapePoolScheduler().select(state, now=NOW, max_tracked=4,
                                                      personal_positions=personal)
        pairs = [row['pairAddress'] for row in selected]
        self.assertEqual(pairs, ['shared-pair', 'other-pair', 'user-pair', 'cheap-pair'])
        self.assertEqual(len(pairs), len(set(pairs)))
        self.assertEqual((report['pinned_exit_pools'], report['pinned_personal_pools'],
                          report['personal_pins_operator']['pinned_personal_only_pools']), (3, 3, 1))

    def test_main_pin_values_are_unchanged_when_a_personal_engine_holds_the_same_pool(self):
        main = {'address': 'm-mint', 'pairAddress': 'm-pair', 'dexId': 'pumpswap', 'symbol': 'MAIN'}
        personal = {'address': 'm-mint', 'pairAddress': 'm-pair', 'dexId': 'pumpswap', 'symbol': 'USER'}
        selected, _ = TapePoolScheduler().select({'positions': [main]}, now=NOW, max_tracked=1,
                                                 personal_positions=[personal])
        self.assertEqual(selected[0]['symbol'], 'MAIN')

    def test_personal_pins_beyond_the_seat_budget_are_kept_up_to_the_pin_limit(self):
        personal = [pin(f'user{index}') for index in range(6)]
        selected, report = TapePoolScheduler(personal_pin_limit=12).select(
            {'feed': [cost_first_coin('cheap')]}, now=NOW, max_tracked=4, personal_positions=personal)
        self.assertEqual(len(selected), 6)
        self.assertEqual(report['entry_capacity'], 0)
        self.assertEqual(report['personal_pins_operator']['personal_pins_over_limit'], 0)

    def test_default_personal_pin_limit_is_the_tape_pool_budget(self):
        def load(environment):
            spec = importlib.util.spec_from_file_location('tape_pool_scheduler_env_copy',
                                                          scheduler_module.__file__)
            module = importlib.util.module_from_spec(spec)
            with patch.dict(os.environ, environment):
                os.environ.pop('NEO_TAPE_PERSONAL_PIN_LIMIT', None)
                os.environ.update(environment)
                spec.loader.exec_module(module)
            return module

        self.assertEqual(load({'NEO_TAPE_MAX_PAIRS': '7'}).PERSONAL_PIN_LIMIT, 7)
        self.assertEqual(load({'NEO_TAPE_MAX_PAIRS': '7', 'NEO_TAPE_PERSONAL_PIN_LIMIT': '3'})
                         .PERSONAL_PIN_LIMIT, 3)
        self.assertEqual(TapePoolScheduler().personal_pin_limit, scheduler_module.PERSONAL_PIN_LIMIT)

    def test_main_pins_keep_their_share_while_personal_only_pins_grow_beyond_the_limit(self):
        main_positions = [pin('main0'), pin('main1')]
        lab = {'book': {'position': pin('lab0')}}
        state = {'feed': [feasible_coin('feasible'), cost_first_coin('cheap')],
                 'positions': main_positions, 'strategy_lab': {'books': lab}}
        shapes = []
        for count in (3, 4, 10, 40):
            personal = [dict(pin(f'user{index:02d}'), opened_at=NOW - index) for index in range(count)]
            personal.append(dict(pin('main1'), opened_at=0))  # shared with main: uncapped
            selected, report = TapePoolScheduler(personal_pin_limit=4).select(
                state, now=NOW, max_tracked=8, personal_positions=personal)
            pairs = [row['pairAddress'] for row in selected]
            self.assertEqual(pairs[:3], ['main0-pair', 'main1-pair', 'lab0-pair'])
            operator = report['personal_pins_operator']
            self.assertEqual(operator['personal_pins_over_limit'], max(0, count - 4))
            self.assertEqual(operator['pinned_personal_only_pools'], min(count, 4))
            shapes.append((len(pairs), report['pinned_exit_pools'], report['entry_capacity']))
            if count >= 4:
                # Oldest open personal-only positions first, deterministically.
                expected = [f'user{index:02d}-pair' for index in range(count - 1, count - 5, -1)]
                self.assertEqual(pairs[3:7], expected)
        # Beyond the limit the selection, the pin count and the entry seats no
        # longer change, so main's pins keep their share of the body budget.
        self.assertEqual(shapes[1:], [(8, 7, 1)] * 3)
        self.assertEqual(shapes[0], (8, 6, 2))

    def test_fresh_personal_pins_precede_stale_ones_under_the_limit(self):
        stale = dict(pin('stale'), opened_at=1, **{scheduler_module.PERSONAL_STALE_MARK: True})
        fresh = [dict(pin(f'fresh{index}'), opened_at=100 + index) for index in range(2)]
        missing_time = pin('untimed')
        selected, report = TapePoolScheduler(personal_pin_limit=3).select(
            {}, now=NOW, max_tracked=4, personal_positions=[stale, missing_time] + fresh)
        self.assertEqual([row['pairAddress'] for row in selected],
                         ['fresh0-pair', 'fresh1-pair', 'untimed-pair'])
        self.assertEqual(report['personal_pins_operator']['personal_pins_over_limit_pairs'], ['stale-pair'])
        self.assertNotIn(scheduler_module.PERSONAL_STALE_MARK, selected[0])

    def test_zero_pin_limit_keeps_main_and_shared_personal_pins(self):
        selected, report = TapePoolScheduler(personal_pin_limit=0).select(
            {'positions': [pin('main')]}, now=NOW, max_tracked=4,
            personal_positions=[pin('main'), pin('user')])
        self.assertEqual([row['pairAddress'] for row in selected], ['main-pair'])
        self.assertEqual((report['pinned_personal_pools'],
                          report['personal_pins_operator']['personal_pins_over_limit']), (1, 1))

    def test_unsupported_personal_pool_is_reported_not_pinned(self):
        selected, report = TapePoolScheduler().select(
            {}, now=NOW, max_tracked=4, personal_positions=[pin('ray', dex='raydium')])
        self.assertEqual(selected, [])
        self.assertEqual((report['unsupported_held_pools'], report['pinned_personal_pools']), (1, 0))

    def test_personal_pin_refreshes_from_the_exact_pool_and_is_never_shed(self):
        cheap = cost_first_coin('cheap')
        other_pool = dict(cheap, pairAddress='other-pair', symbol='OTHER')
        stats = {'bodies': 500, 'decoded_swaps': 0, 'usable_swaps': 0, 'shadow_swaps': 0}
        selected, report = TapePoolScheduler().select(
            {'feed': [other_pool, cheap]}, now=NOW, max_tracked=1,
            decode_yield=lambda pair, since=0: stats, personal_positions=[position(cheap)])
        self.assertEqual(selected[0]['pairAddress'], 'cheap-pair')
        self.assertEqual(selected[0]['liquidityUsd'], cheap['liquidityUsd'])
        self.assertNotIn(('cheap-mint', 'cheap-pair'), {(r['address'], r['pairAddress'])
                                                        for r in report['shed_pools']})

    def test_without_personal_positions_reports_zero(self):
        _, report = TapePoolScheduler().select({'feed': []}, now=NOW, max_tracked=4)
        self.assertEqual((report['pinned_personal_pools'],
                          report['personal_pins_operator']['pinned_personal_only_pools'],
                          report['personal_pins_operator']['personal_pins_over_limit']), (0, 0, 0))


class FeedSnapshotWiringTests(RegistryFixture):
    class Response:
        def __init__(self, payload):
            self.payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self.payload

    def test_feed_snapshot_pins_personal_positions_and_publishes_them(self):
        self.write_registry({'a': {'engine_port': 18900}, 'b': {'engine_port': 18901}})
        self.engines[18900] = {'positions': [pin('user')]}
        self.engines[18901] = OSError('down')
        main = {'feed': [cost_first_coin('cheap'), over_budget_coin('costly')], 'positions': [pin('main')]}
        source = self.source()
        source.refresh(NOW)  # the refresher's pass; feed_snapshot only reads it
        calls = len(self.calls)
        with patch.object(tape.SESSION, 'get', return_value=self.Response(main)) as get, \
                patch.object(tape, '_POOL_SCHEDULER', TapePoolScheduler()), \
                patch.object(tape, '_PERSONAL_ENGINES', source), \
                patch.object(tape, 'shared_quote_reference', return_value=None), \
                patch.object(tape, 'MAX_TRACKED', 3), \
                patch.object(tape, 'now_ms', return_value=NOW):
            rows = tape.feed_snapshot()
        self.assertEqual(get.call_count, 1)  # only main /state
        self.assertEqual(len(self.calls), calls)  # no personal read on the poll
        self.assertEqual([row['pair'] for row in rows], ['main-pair', 'user-pair', 'cheap-pair'])
        scheduling = tape.STATUS['entry_scheduling']
        self.assertEqual(scheduling['pinned_personal_pools'], 1)
        self.assertEqual(scheduling['selected_cost_first_pools'], 1)
        operator = tape.STATUS['personal_engines']
        self.assertEqual(operator['registry_status'], 'OK')
        self.assertEqual((operator['engines_reachable'], operator['engines_never_reached']), (1, 1))
        self.assertEqual((operator['pinned_personal_only_pools'], operator['personal_pins_over_limit']), (1, 0))
        self.assertTrue(operator['read_only'])

    def test_entry_scheduling_carries_no_personal_engine_aggregates(self):
        self.write_registry({'a': {'engine_port': 18900}})
        self.engines[18900] = {'positions': [pin('user')]}
        source = self.source()
        source.refresh(NOW)
        main = {'feed': [], 'positions': []}
        with patch.object(tape.SESSION, 'get', return_value=self.Response(main)), \
                patch.object(tape, '_POOL_SCHEDULER', TapePoolScheduler()), \
                patch.object(tape, '_PERSONAL_ENGINES', source), \
                patch.object(tape, 'shared_quote_reference', return_value=None), \
                patch.object(tape, 'MAX_TRACKED', 2), \
                patch.object(tape, 'now_ms', return_value=NOW):
            tape.feed_snapshot()
        scheduling = tape.STATUS['entry_scheduling']
        self.assertEqual(scheduling['pinned_personal_pools'], 1)
        text = json.dumps(scheduling)
        for field in ('personal_engines', 'personal_pins_operator', 'engines_listed',
                      'engines_reachable', 'open_positions', 'pinned_personal_only_pools',
                      'personal_pins_over_limit'):
            self.assertNotIn(field, text)
        # market_monitor forwards entry_scheduling to every user's /state but not
        # the tape file's top-level personal_engines status.
        monitor = (Path(__file__).resolve().parents[1] / 'backend' / 'market_monitor.py').read_text(encoding='utf-8')
        forwarded = monitor[monitor.index("'live_tape_status'"):].split('\n', 1)[0]
        self.assertIn("'entry_scheduling'", forwarded)
        self.assertNotIn('personal_engines', forwarded)

    def test_feed_snapshot_with_missing_registry_behaves_as_before(self):
        main = {'feed': [feasible_coin('feasible')], 'positions': [pin('main')]}
        with patch.object(tape.SESSION, 'get', return_value=self.Response(main)), \
                patch.object(tape, '_POOL_SCHEDULER', TapePoolScheduler()), \
                patch.object(tape, '_PERSONAL_ENGINES', self.source()), \
                patch.object(tape, 'shared_quote_reference', return_value=None), \
                patch.object(tape, 'MAX_TRACKED', 2), \
                patch.object(tape, 'now_ms', return_value=NOW):
            rows = tape.feed_snapshot()
        self.assertEqual([row['pair'] for row in rows], ['main-pair', 'feasible-pair'])
        self.assertEqual(tape.STATUS['personal_engines']['registry_status'], 'NOT_REFRESHED')
        self.assertEqual(self.calls, [])

    def test_default_fetch_reads_only_loopback_state_with_the_given_timeout(self):
        with patch.object(tape.PERSONAL_SESSION, 'get', return_value=self.Response({'positions': []})) as get, \
                patch.object(tape.SESSION, 'get') as poll_get:
            self.assertEqual(tape.personal_engine_state(18950, .75), {'positions': []})
        get.assert_called_once_with('http://127.0.0.1:18950/state', timeout=.75, allow_redirects=False)
        poll_get.assert_not_called()

    def test_main_starts_the_refresher_before_the_first_poll(self):
        order = []

        class Stop(Exception):
            pass

        def first_poll():
            order.append('poll')
            raise Stop

        source = self.source()
        with patch.object(tape, '_PERSONAL_ENGINES', source), \
                patch.object(source, 'start', side_effect=lambda: order.append('start')), \
                patch.object(tape, 'poll_once', side_effect=first_poll), \
                patch.object(tape, 'failure_summary', side_effect=Stop):
            with self.assertRaises(Stop):
                tape.main()
        self.assertEqual(order, ['start', 'poll'])

    def test_default_source_reads_the_registry_named_by_the_environment(self):
        self.write_registry({})
        with patch.dict(os.environ, {'NEO_USER_STATE_PATH': str(self.registry)}):
            _, report = PersonalEnginePositions(self.fetch).refresh(NOW)
        self.assertEqual((report['registry_status'], report['engines_listed']), ('OK', 0))


if __name__ == '__main__':
    unittest.main()
