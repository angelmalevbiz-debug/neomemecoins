"""Balanced discovery uses available body capacity without claiming coverage."""
import json
import tempfile
import unittest
from pathlib import Path

import live_tape as tape
from test_tape_execution_repair import META, NOW, non_swap, pubkey


class TapeDiscoveryBudgetTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.clock = [NOW+1000]
        self.rec = tape.TapeRecorder(
            Path(self.directory.name)/'tape.sqlite', clock=lambda: self.clock[0],
            page_size=1000, page_budget=1, tx_budget=48)
        self.feed = [dict(META, pair=pubkey(20+i), address=pubkey(40+i))
                     for i in range(4)]

    def tearDown(self):
        self.rec.close()
        self.directory.cleanup()

    def pending(self, count, *, timestamp=None, retry=0):
        timestamp = NOW if timestamp is None else timestamp
        with self.rec.db:
            for i in range(count):
                self.rec.db.execute('''INSERT INTO signatures(signature,pair,slot,
                    event_time,observed,metadata,state,next_retry)
                    VALUES(?,?,1,?,?,?,'pending',?)''',
                    (f'retry-{i}',META['pair'],timestamp,timestamp,json.dumps(META),retry))

    def limits(self):
        calls = []

        def rpc(batch):
            calls.extend(batch)
            return [{'result': []} for _ in batch]

        self.rec.discover(self.feed, rpc)
        return [params[1]['limit'] for _,params in calls]

    def test_empty_due_queue_uses_all_declared_capacity_across_four_pools(self):
        self.assertEqual(self.limits(), [12]*4)

    def test_only_due_fresh_retries_reserve_capacity(self):
        self.pending(8)
        self.assertEqual(self.limits(), [10]*4)

    def test_future_retry_deadline_does_not_reserve_unused_capacity(self):
        self.pending(48, retry=self.clock[0]+1)
        self.assertEqual(self.limits(), [12]*4)

    def test_historical_queue_does_not_reserve_current_capacity(self):
        self.pending(48, timestamp=NOW-tape.WINDOW_MS-1)
        self.assertEqual(self.limits(), [12]*4)

    def test_full_retry_queue_keeps_every_pool_progressing_and_rows_durable(self):
        self.pending(48)
        self.assertEqual(self.limits(), [1]*4)
        self.assertEqual(self.rec.db.execute('SELECT count(*) FROM signatures').fetchone()[0],48)

    def test_all_pinned_pools_share_discovery_when_they_exceed_normal_entry_cohort(self):
        self.feed = [dict(META, pair=pubkey(20+i), address=pubkey(40+i),
                          pinned=True) for i in range(12)]
        self.assertEqual(self.limits(), [4]*12)
        pairs = {row['pair'] for row in self.rec.db.execute('SELECT pair FROM pairs')}
        self.assertEqual(pairs, {m['pair'] for m in self.feed})

    def test_discovery_and_process_keep_body_request_cap_and_unknown_until_pages_complete(self):
        rows = {m['pair']: [{'signature':f'{index}-{i}', 'slot':100+i,
                           'blockTime':NOW//1000, 'err':None}
                          for i in range(20,0,-1)]
                for index,m in enumerate(self.feed)}
        bodies = []

        def rpc(calls):
            out = []
            for method,params in calls:
                if method == 'getTransaction':
                    bodies.append(params[0])
                    out.append({'result':non_swap()})
                    continue
                pair, options = params
                sequence = rows[pair]
                start = next((i+1 for i,r in enumerate(sequence)
                              if r['signature']==options.get('before')),0)
                stop = next((i for i,r in enumerate(sequence)
                             if r['signature']==options.get('until')),len(sequence))
                out.append({'result':sequence[start:min(stop,start+options['limit'])]})
            return out

        first = self.rec.poll(self.feed, rpc)
        self.assertEqual(len(bodies), 48)
        self.assertTrue(all(r['status']=='UNKNOWN' and r['pagination_pending']
                            for r in first['pair_coverage'].values()))
        bodies.clear()
        second = self.rec.poll(self.feed, rpc)
        self.assertEqual(len(bodies), 32)
        self.assertTrue(all(r['status']=='COMPLETE' for r in second['pair_coverage'].values()))
        self.assertEqual(self.rec.db.execute('SELECT count(*) FROM signatures').fetchone()[0],80)

    def test_unused_short_pages_are_redistributed_within_existing_round_budget(self):
        self.rec.page_budget = 2
        rounds = []

        def rpc(calls):
            rounds.append([params[1]['limit'] for _,params in calls])
            out = []
            for _,params in calls:
                pair, options = params
                if pair != self.feed[0]['pair']:
                    out.append({'result':[]})
                else:
                    base = 100 if not options.get('before') else 50
                    out.append({'result':[{'signature':f'page-{base-i}',
                                           'slot':base-i,'blockTime':NOW//1000,'err':None}
                                          for i in range(options['limit'])]})
            return out

        self.rec.discover(self.feed, rpc)
        self.assertEqual(rounds, [[6]*4, [42]])
        self.assertEqual(self.rec.db.execute('SELECT count(*) FROM signatures').fetchone()[0],48)
        coverage = self.rec.snapshot(self.feed)['pair_coverage'][self.feed[0]['pair']]
        self.assertEqual(coverage['status'], 'UNKNOWN')

    def test_stalled_head_older_than_decision_window_restarts_live_without_erasing_journal(self):
        meta = self.feed[0]
        now = self.clock[0]
        with self.rec.db:
            self.rec.db.execute('''INSERT INTO pairs(pair,mint,cursor,before_sig,
                scan_head,complete_since,last_poll,reason,metadata) VALUES(?,?,?,?,?,?,?,?,?)''',
                (meta['pair'],meta['address'],'cursor','before','stale-head',now-100_000,
                 now-1000,'PAGINATION_PENDING',json.dumps(meta)))
            self.rec.db.execute('''INSERT INTO signatures(signature,pair,slot,
                event_time,observed,metadata,state) VALUES(?,?,1,?,?,?,'processed')''',
                ('stale-head',meta['pair'],now-30_001,now-31_000,json.dumps(meta)))
        requested = []

        def rpc(calls):
            requested.extend(params[1] for _,params in calls)
            limit = calls[0][1][1]['limit']
            return [{'result':[{'signature':f'new-{i}', 'slot':100+i,
                                'blockTime':now//1000, 'err':None}
                               for i in range(limit)]}]

        self.rec.discover([meta], rpc)
        self.assertNotIn('before', requested[0])
        self.assertNotIn('until', requested[0])
        coverage = self.rec.snapshot([meta])['pair_coverage'][meta['pair']]
        self.assertEqual(coverage['status'], 'UNKNOWN')
        self.assertIsNone(coverage['complete_since_ms'])
        self.assertTrue(coverage['pagination_pending'])
        self.assertEqual(self.rec.db.execute("SELECT state FROM signatures WHERE signature='stale-head'").fetchone()[0], 'processed')

    def test_provider_oversized_page_does_not_insert_unbounded_work_or_claim_complete(self):
        meta = self.feed[0]

        def rpc(calls):
            limit = calls[0][1][1]['limit']
            return [{'result':[{'signature':f'excess-{i}', 'slot':100+i,
                                'blockTime':NOW//1000, 'err':None}
                               for i in range(limit+1)]}]

        self.rec.discover([meta], rpc)
        coverage = self.rec.snapshot([meta])['pair_coverage'][meta['pair']]
        self.assertEqual(coverage['status'], 'UNKNOWN')
        self.assertEqual(coverage['reason'], 'SIGNATURE_SCHEMA_MISMATCH')
        self.assertEqual(self.rec.db.execute('SELECT count(*) FROM signatures').fetchone()[0],0)


if __name__ == '__main__':
    unittest.main()
