"""Restart mechanics on synthetic observations, not evidence of profit."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import entry_defense
import heat_veto
import paper_heat_history as continuity


class ObservedHeatContinuityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='neo-heat-continuity-')
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'account.pair_history.json'
        self.now = 1_800_000_000_000
        self.coin = dict(address='mint', pairAddress='pool', priceUsd=1.0, dexId='pumpswap',
                         updatedAt=self.now, sources=['pumpswap-address-catalog'],
                         liquidityUsd=1_000_000, marketCap=20_000_000, priceNative=1/150,
                         quoteTokenAddress='So11111111111111111111111111111111111111112',
                         volume={'m5':1000, 'h1':30_000}, txns={'m5':{'buys':6, 'sells':4}},
                         priceChange={'h6':1, 'h24':1})

    def history(self):
        return continuity.PersistentPairHistory(self.path, clock=lambda:self.now)

    def warm(self, history, seconds=960, *, paid=False):
        for back in range(seconds, -1, -30):
            stamp = self.now - back*1000
            coin = dict(self.coin, updatedAt=stamp)
            if paid and back == seconds:
                coin['sources'] = ['latest']
            history.observe_coin(coin, stamp)
        self.assertTrue(history.flush())

    def mutate(self, edit):
        data = json.loads(self.path.read_text())
        edit(data)
        self.path.write_text(json.dumps(data))

    def test_short_restart_keeps_exact_windows_not_just_coverage_counters(self):
        history = self.history()
        self.warm(history)
        before = history.view(self.coin)
        decision = heat_veto.evaluate(self.coin, self.now, history)
        self.assertNotIn('heat_history_warming', decision['reasons'])
        self.now += 10_000
        restarted = self.history()
        self.assertEqual(restarted.view(self.coin), before)
        self.assertEqual(restarted.observing_since, history.observing_since)
        self.assertEqual(restarted.status()['load_status'], 'RESTORED_OBSERVATIONS')
        self.assertNotIn('heat_history_warming', heat_veto.evaluate(self.coin,self.now,restarted)['reasons'])

    def test_paid_sighting_high_and_gap_are_not_forgotten_at_restart(self):
        history = self.history()
        self.warm(history, seconds=600, paid=True)
        self.now += 200_000
        history.observe_coin(dict(self.coin, updatedAt=self.now, priceUsd=.7), self.now)
        self.assertTrue(history.flush())
        restarted = self.history()
        self.assertEqual(restarted.view(self.coin), history.view(self.coin))
        record = restarted.view(self.coin)
        self.assertEqual(len(record['gaps']),1)
        high_fee = dict(self.coin, marketCap=500_000, priceUsd=.7)
        result = heat_veto.evaluate(high_fee, self.now, restarted)
        self.assertIn('heat_paid_profile_high_fee',result['reasons'])
        self.assertIn('heat_crash_in_progress',result['reasons'])
        self.assertIn('heat_history_warming',result['reasons'])

    def test_hour_of_observations_survives_but_short_history_does_not_claim_hour(self):
        history = self.history()
        self.warm(history, seconds=3600)
        high_fee = dict(self.coin, marketCap=500_000)
        self.assertNotIn('heat_history_warming',heat_veto.evaluate(high_fee,self.now,self.history())['reasons'])
        self.path.unlink()
        history = self.history()
        self.warm(history,seconds=960)
        result = heat_veto.evaluate(high_fee,self.now,self.history())
        self.assertIn('paid_profile_60m',result['metrics']['warming_windows'])

    def test_long_outage_is_cold_and_flush_cannot_refresh_old_observations(self):
        history = self.history()
        self.warm(history)
        self.now += 120_001
        self.assertTrue(history.flush())
        restarted = self.history()
        self.assertEqual(len(restarted),0)
        self.assertEqual(restarted.status()['load_status'],'STALE_SIDECAR_IGNORED')
        self.assertIn('heat_history_warming',heat_veto.evaluate(self.coin,self.now,restarted)['reasons'])

    def test_one_fresh_pair_cannot_repair_another_pairs_real_gap(self):
        history = self.history()
        self.warm(history)
        self.now += 130_000
        history.observe_coin(dict(self.coin,address='other',pairAddress='another',updatedAt=self.now),self.now)
        history.flush()
        restarted = self.history()
        self.assertEqual(restarted.status()['load_status'],'RESTORED_OBSERVATIONS')
        self.assertIn('heat_history_warming',heat_veto.evaluate(self.coin,self.now,restarted)['reasons'])
        restarted.observe_coin(dict(self.coin,updatedAt=self.now),self.now)
        self.assertEqual(restarted.view(self.coin)['since'],self.now)

    def test_corrupt_future_incompatible_and_oversized_files_never_grant_coverage(self):
        history = self.history()
        self.warm(history)
        original = self.path.read_bytes()
        cases = [lambda d:d.update(version='unknown'),
                 lambda d:d.update(saved_at=self.now+1),
                 lambda d:d.update(observed_until=self.now+1),
                 lambda d:d['pairs'][0]['samples'].append([self.now+1,1,False]),
                 lambda d:d['pairs'][0].update(samples=[]),
                 lambda d:d['pairs'][0]['samples'][0].__setitem__(1,-1),
                 lambda d:d['pairs'][0]['samples'][0].__setitem__(2,'false'),
                 lambda d:d['pairs'][0].update(gaps=[[self.now-1000,self.now]]),
                 lambda d:d['pairs'][0].update(since=self.now+1),
                 lambda d:d.update(pairs=d['pairs']*2),
                 lambda d:d.update(parameters={})]
        for edit in cases:
            self.path.write_bytes(original)
            self.mutate(edit)
            restarted = self.history()
            self.assertEqual(len(restarted),0,edit)
            self.assertIn('heat_history_warming',heat_veto.evaluate(self.coin,self.now,restarted)['reasons'])
        self.path.write_bytes(original)
        with patch.object(continuity,'MAX_BYTES',32):
            self.assertEqual(len(self.history()),0)

    def test_all_or_nothing_validation_preserves_original_bad_file(self):
        history = self.history()
        self.warm(history)
        def edit(data):
            second = copy.deepcopy(data['pairs'][0])
            second.update(address='other',pairAddress='another',samples=[])
            data['pairs'].append(second)
        self.mutate(edit)
        before = self.path.read_bytes()
        restarted = self.history()
        self.assertEqual(len(restarted),0)
        restarted.observe_coin(self.coin,self.now)
        self.assertFalse(restarted.flush())
        self.assertEqual(self.path.read_bytes(),before)
        self.assertTrue(restarted.status()['kept_not_overwritten'])

    def test_failed_atomic_write_retains_complete_checkpoint_and_removes_temp(self):
        history = self.history()
        self.warm(history)
        before = self.path.read_bytes()
        self.now += 30_000
        history.observe_coin(dict(self.coin,updatedAt=self.now,priceUsd=1.001),self.now)
        with patch.object(continuity,'replace_shared_snapshot',side_effect=PermissionError):
            self.assertFalse(history.flush())
        self.assertEqual(self.path.read_bytes(),before)
        self.assertEqual(list(self.path.parent.glob('*.tmp')),[])
        self.assertEqual(history.status()['save_error'],'PermissionError')
        self.assertTrue(history.flush())
        self.assertIsNone(history.status()['save_error'])

    def test_heartbeat_checkpoint_rate_and_clean_stop_do_not_backdate_first_seen(self):
        history = self.history()
        history.observe([dict(self.coin,updatedAt=self.now-5000)],self.now)
        self.assertEqual(history.view(self.coin)['first_seen'],self.now)
        self.assertEqual(history.saves,1)
        self.now += 1000
        history.observe([dict(self.coin,updatedAt=self.now)],self.now)
        self.assertEqual(history.saves,1)
        self.assertTrue(history.flush())
        self.assertEqual(history.saves,2)
        self.assertEqual(self.history().view(self.coin)['first_seen'],self.now-1000)

    def test_duplicate_or_future_observations_do_not_invent_restart_coverage(self):
        history = self.history()
        history.observe([self.coin],self.now)
        before = self.path.read_bytes()
        self.now += 30_000
        history.observe([self.coin],self.now)
        self.assertEqual(self.path.read_bytes(),before)
        self.assertEqual(history.status()['observed_until'],self.now-30_000)
        future = dict(self.coin,updatedAt=self.now+1000)
        history.observe_coin(future,self.now)
        self.assertFalse(history.flush())
        self.assertEqual(self.path.read_bytes(),before)

    def test_retention_is_applied_at_checkpoint_clock_not_old_pairs_last_clock(self):
        history = self.history()
        self.warm(history,seconds=3600)
        self.now += 45*60_000
        other = dict(self.coin,address='other',pairAddress='another',updatedAt=self.now)
        history.observe_coin(other,self.now)
        self.assertTrue(history.flush())
        restarted = self.history()
        self.assertEqual(restarted.status()['load_status'],'RESTORED_OBSERVATIONS')
        self.assertEqual(len(restarted),2)
        self.assertTrue(all(s[0]>=self.now-history.params.retention_ms
                            for s in restarted.view(self.coin)['samples']))

    def test_io_error_is_visible_does_not_stop_observations_and_never_overwrites_evidence(self):
        history = self.history()
        self.warm(history)
        before = self.path.read_bytes()
        with patch.object(continuity,'open_shared_text',side_effect=PermissionError):
            restarted = self.history()
        self.assertEqual(restarted.status()['load_status'],'INVALID_OR_UNREADABLE_SIDECAR')
        restarted.observe([self.coin],self.now)
        self.assertEqual(len(restarted),1)
        self.assertEqual(self.path.read_bytes(),before)

    def test_service_layer_owns_separate_sidecar_and_flushes_both_memories(self):
        registry = Mock()
        registry.flush.return_value = True
        sidecar = self.path.with_name('account.ticker_registry.json')
        layer = entry_defense.DefensiveEntryLayer(registry_path=sidecar,registry=registry,clock=lambda:self.now)
        self.assertEqual(layer.history.path,self.path)
        self.warm(layer.history)
        self.assertTrue(layer.flush())
        registry.flush.assert_called_once()
        self.assertTrue(layer.status()['pair_history']['persistent'])
        self.assertFalse(entry_defense.DefensiveEntryLayer(registry=registry).history.status()['persistent'])


if __name__ == '__main__':
    unittest.main()
