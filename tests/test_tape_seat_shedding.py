"""Tape seats shed pools that fetched bodies without one engine-usable swap.

The recorder counts, in memory and per pool, how many transaction bodies it
classified and how many events without quality flags they yielded. The
scheduler excludes a supported entry candidate from entry/exploration seats
after a bounded number of bodies with zero usable swaps, for a cooldown, and
publishes the shed list in live_tape_status.entry_scheduling. Pinned exit
pools are never shed. Nothing here admits, sizes or exits a trade.
"""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import live_tape as tape
import tape_pool_scheduler as scheduler_module
from tape_pool_scheduler import TapePoolScheduler
from test_tape_execution_repair import META, NOW, non_swap, pubkey, transaction

NOW_MS = 1_800_000_000_000


def coin(index, *, activity=60):
    return {'address': pubkey(index), 'pairAddress': pubkey(index+1), 'symbol': f'C{index}',
            'dexId': 'pumpswap', 'score': 80, 'liquidityUsd': 12_000, 'marketCap': 100_000,
            'ageMinutes': 30, 'priceUsd': 1, 'priceNative': 1, 'updatedAt': NOW_MS,
            'priceChange': {'m5': 5, 'h1': 10},
            'txns': {'m5': {'buys': activity*2//3, 'sells': activity//3}},
            'volume': {'h1': 6_000}}


class RecorderYieldTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.clock = [NOW+1000]
        self.rec = tape.TapeRecorder(Path(self.directory.name)/'tape.sqlite',
                                     clock=lambda: self.clock[0], tx_budget=8, yield_window_ms=600_000)

    def tearDown(self):
        self.rec.close()
        self.directory.cleanup()

    def pending(self, signature, pair=META['pair']):
        with self.rec.db:
            self.rec.db.execute('''INSERT INTO signatures(signature,pair,slot,event_time,observed,metadata,state)
                VALUES(?,?,1,?,?,?,'pending')''', (signature, pair, NOW, NOW, json.dumps(META)))

    def test_bodies_and_usable_swaps_are_counted_per_pool_and_retries_are_not_bodies(self):
        other = pubkey(60)
        for signature in ('swap', 'plain', 'unavailable', 'flagged'):
            self.pending(signature)
        self.pending('other-plain', other)
        flagged = transaction()
        for entry in flagged['transaction']['message']['accountKeys']:
            entry['signer'] = False
        bodies = {'swap': {'result': transaction()}, 'plain': {'result': non_swap()},
                  'unavailable': {'error': {'code': 'TRANSACTION_RPC_UNAVAILABLE'}},
                  'flagged': {'result': flagged}, 'other-plain': {'result': non_swap()}}
        self.rec.process(lambda calls: [bodies[params[0]] for _, params in calls])
        stats = self.rec.decode_yield(META['pair'])
        self.assertEqual((stats['bodies'], stats['usable_swaps'], stats['shadow_swaps']), (3, 1, 0))
        self.assertEqual((stats['first_body_at'], stats['last_body_at']), (self.clock[0], self.clock[0]))
        self.assertEqual(self.rec.decode_yield(other)['bodies'], 1)
        self.assertEqual(self.rec.decode_yield(pubkey(61))['bodies'], 0)
        self.assertEqual(self.rec.db.execute("SELECT state FROM signatures WHERE signature='unavailable'").fetchone()[0], 'pending')

    def test_since_cutoff_and_window_prune_old_rows(self):
        self.pending('plain')
        self.rec.process(lambda calls: [{'result': non_swap()} for _ in calls])
        first = self.clock[0]
        self.clock[0] += 5000
        self.pending('plain-2')
        self.rec.process(lambda calls: [{'result': non_swap()} for _ in calls])
        self.assertEqual(self.rec.decode_yield(META['pair'])['bodies'], 2)
        self.assertEqual(self.rec.decode_yield(META['pair'], since=first+1)['bodies'], 1)
        self.assertEqual(self.rec.decode_yield(META['pair'], since=self.clock[0]+1)['bodies'], 0)
        self.clock[0] = first+600_001
        self.assertEqual(self.rec.decode_yield(META['pair'])['bodies'], 1)
        self.clock[0] += 600_000
        self.assertEqual(self.rec.decode_yield(META['pair'])['bodies'], 0)
        self.assertNotIn(META['pair'], self.rec._yield)


class SchedulerSheddingTests(unittest.TestCase):
    def setUp(self):
        self.coins = [coin(10), coin(20), coin(30), coin(40), coin(50)]
        self.stats = {}
        self.calls = []
        self.scheduler = TapePoolScheduler(shed_min_bodies=40, shed_cooldown_ms=1_800_000)

    def decode_yield(self, pair, since=0):
        self.calls.append((pair, since))
        bodies, usable, shadow = self.stats.get(pair, (0, 0, 0))
        return {'bodies': bodies, 'usable_swaps': usable, 'shadow_swaps': shadow,
                'first_body_at': NOW_MS-60_000 if bodies else None,
                'last_body_at': NOW_MS-1000 if bodies else None, 'window_ms': 7_200_000, 'since': since}

    def select(self, *, now=NOW_MS, max_tracked=3, positions=(), decode_yield='self'):
        state = {'feed': copy.deepcopy(self.coins), 'positions': list(positions)}
        return self.scheduler.select(state, now=now, max_tracked=max_tracked,
                                     decode_yield=self.decode_yield if decode_yield == 'self' else decode_yield)

    def test_zero_usable_swaps_after_bounded_bodies_releases_the_seat_with_a_published_reason(self):
        self.stats = {pubkey(11): (40, 0, 0), pubkey(21): (39, 0, 0), pubkey(31): (40, 1, 0),
                      pubkey(41): (400, 0, 7)}
        selected, diagnostics = self.select()
        pairs = [row['pairAddress'] for row in selected]
        self.assertNotIn(pubkey(11), pairs)
        self.assertNotIn(pubkey(41), pairs)
        self.assertEqual(len(pairs), 3)
        self.assertEqual(diagnostics['policy_version'], scheduler_module.POLICY_VERSION)
        self.assertEqual(diagnostics['shed_pool_count'], 2)
        self.assertEqual(diagnostics['shed_pools_in_feed'], 2)
        self.assertEqual(diagnostics['supported_candidate_pools'], 3)
        shed = {row['pairAddress']: row for row in diagnostics['shed_pools']}
        self.assertEqual(set(shed), {pubkey(11), pubkey(41)})
        self.assertEqual(shed[pubkey(11)]['reason'], 'ZERO_USABLE_SWAPS_AFTER_BODIES')
        self.assertEqual((shed[pubkey(11)]['bodies'], shed[pubkey(11)]['usable_swaps']), (40, 0))
        self.assertEqual((shed[pubkey(41)]['bodies'], shed[pubkey(41)]['shadow_swaps']), (400, 7))
        self.assertEqual(shed[pubkey(11)]['address'], pubkey(10))
        self.assertEqual(shed[pubkey(11)]['symbol'], 'C10')
        self.assertEqual((shed[pubkey(11)]['shed_at'], shed[pubkey(11)]['retry_at']), (NOW_MS, NOW_MS+1_800_000))
        self.assertEqual(diagnostics['shed_policy'], {
            'reason': 'ZERO_USABLE_SWAPS_AFTER_BODIES', 'min_bodies': 40, 'cooldown_ms': 1_800_000,
            'counts_engine_usable_swaps_only': True, 'pinned_exit_pools_exempt': True,
            'yield_source': 'RECORDER_IN_MEMORY'})
        self.assertEqual(diagnostics['selected_pairs'], pairs)

    def test_shedding_releases_an_active_lease_and_retries_after_the_cooldown_from_a_fresh_count(self):
        self.scheduler.last_selected[(pubkey(20), pubkey(21))] = NOW_MS-1
        self.scheduler.last_selected[(pubkey(30), pubkey(31))] = NOW_MS-1
        self.scheduler.last_selected[(pubkey(40), pubkey(41))] = NOW_MS-1
        self.scheduler.last_selected[(pubkey(50), pubkey(51))] = NOW_MS-1
        selected, _ = self.select(max_tracked=1)
        self.assertEqual([row['pairAddress'] for row in selected], [pubkey(11)])
        self.assertIn((pubkey(10), pubkey(11)), self.scheduler.leases)
        self.stats = {pubkey(11): (40, 0, 0)}
        selected, diagnostics = self.select(now=NOW_MS+10_000, max_tracked=1)
        self.assertNotEqual([row['pairAddress'] for row in selected], [pubkey(11)])
        self.assertEqual(len(selected), 1)
        self.assertNotIn((pubkey(10), pubkey(11)), self.scheduler.leases)
        retry_at = diagnostics['shed_pools'][0]['retry_at']
        # Still shed while cooling down, without re-reading the yield.
        self.calls.clear()
        self.select(now=retry_at-1, max_tracked=1)
        self.assertNotIn(pubkey(11), [pair for pair, _ in self.calls])
        self.assertEqual(self.scheduler.shed[(pubkey(10), pubkey(11))]['retry_at'], retry_at)
        # After the cooldown the pool is re-evaluated from bodies fetched after retry_at only.
        self.calls.clear()
        _, diagnostics = self.select(now=retry_at+5000, max_tracked=5)
        self.assertIn((pubkey(11), retry_at), self.calls)
        self.assertEqual(diagnostics['shed_pool_count'], 1)  # still 40/0 within the stale window stats
        self.stats = {}
        self.calls.clear()
        _, diagnostics = self.select(now=retry_at+1_800_000+5000, max_tracked=5)
        self.assertEqual(diagnostics['shed_pool_count'], 0)
        self.assertIn(pubkey(11), diagnostics['selected_pairs'])

    def test_pinned_exit_pools_are_never_shed_and_missing_yield_disables_shedding(self):
        self.stats = {pubkey(11): (500, 0, 0)}
        position = {'address': pubkey(10), 'pairAddress': pubkey(11),
                    'coin_snapshot': {'dexId': 'pumpswap', 'symbol': 'C10', 'priceUsd': 1}}
        selected, diagnostics = self.select(positions=[position])
        self.assertEqual(selected[0]['pairAddress'], pubkey(11))
        self.assertEqual(diagnostics['pinned_exit_pools'], 1)
        self.assertEqual(diagnostics['shed_pool_count'], 0)
        self.assertEqual(diagnostics['shed_pools'], [])
        selected, diagnostics = self.select(decode_yield=None)
        self.assertEqual(diagnostics['shed_pool_count'], 0)
        self.assertEqual(diagnostics['shed_policy']['yield_source'], 'UNAVAILABLE')
        self.assertEqual(len(selected), 3)

    def test_shed_records_outlive_a_feed_gap_until_their_cooldown_ends(self):
        self.stats = {pubkey(11): (40, 0, 0)}
        self.select()
        self.coins = self.coins[1:]
        _, diagnostics = self.select(now=NOW_MS+60_000)
        self.assertEqual(diagnostics['shed_pool_count'], 1)
        self.assertEqual(diagnostics['shed_pools_in_feed'], 0)
        _, diagnostics = self.select(now=NOW_MS+1_800_001)
        self.assertEqual(diagnostics['shed_pool_count'], 0)
        self.assertEqual(self.scheduler.yield_since, {})


class FeedSnapshotWiringTests(unittest.TestCase):
    def test_feed_snapshot_hands_the_recorder_yield_to_the_scheduler(self):
        class Response:
            def raise_for_status(self):
                return None

            def json(self):
                return {'feed': [coin(10), coin(20)], 'positions': []}

        class Recorder:
            def __init__(self):
                self.calls = []

            def decode_yield(self, pair, since=0):
                self.calls.append((pair, since))
                return {'bodies': 40 if pair == pubkey(11) else 0, 'usable_swaps': 0, 'shadow_swaps': 0,
                        'first_body_at': None, 'last_body_at': None, 'window_ms': 1, 'since': since}

        recorder = Recorder()
        with patch.object(tape.SESSION, 'get', return_value=Response()), \
             patch.object(tape, 'shared_quote_reference', return_value=None), \
             patch.object(tape, '_RECORDER', recorder), \
             patch.object(tape, '_POOL_SCHEDULER', TapePoolScheduler(shed_min_bodies=40)), \
             patch.object(tape, 'MAX_TRACKED', 2):
            feed = tape.feed_snapshot()
            scheduling = tape.STATUS['entry_scheduling']
        self.assertEqual([row['pair'] for row in feed], [pubkey(21)])
        self.assertEqual(scheduling['shed_policy']['yield_source'], 'RECORDER_IN_MEMORY')
        self.assertEqual([row['pairAddress'] for row in scheduling['shed_pools']], [pubkey(11)])
        self.assertEqual({pair for pair, _ in recorder.calls}, {pubkey(11), pubkey(21)})


if __name__ == '__main__':
    unittest.main()
