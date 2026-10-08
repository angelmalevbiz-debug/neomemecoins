"""Real-journal replay identity: the 2026-10-07 main journal slice must book the
live main ledger.

tests/fixtures/replay_main_20261007_slice.jsonl.gz holds recorded journal lines,
byte-identical, of the two pools with main-engine entry evidence: the 10
entry-evidence rows, the mark rows of those episodes, exit_quote guard rows,
linked commit-outcome rows and the exact-pool rows of the 10 s before each
preflight (the feed and flow the live commit re-read). It contains public token,
pool and quote data only. The uncompressed slice is pinned by SHA-256; when the
file is absent the test is skipped, never weakened.

The expected values are the pnl numbers the live engine booked, not values
computed by the replay. Reproducing them proves the instrument, not an edge.
"""
import copy
import gzip
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from main_replay import MainReplay

FIXTURES = Path(__file__).resolve().parent/'fixtures'
EXPECTED_PATH = FIXTURES/'replay_main_20261007.expected.json'
SLICE_SHA256 = '38b3e983e5ce464c285feb9c09815d135ab2ab8793deb0c7c260e17891e9342b'
BOOKED_LIVE_PNL_USD = {  # as booked by the live main engine on 2026-10-07
    'closed': [(-6.9566, 'EXIT_IMPACT_EMERGENCY'), (-1.6385, 'EXIT_IMPACT_EMERGENCY'),
               (-10.1813, 'STOP_LOSS_NET_TARGET')],
    'open': [-3.3917],
}


def load_slice(expected):
    path = FIXTURES/expected['slice']['file']
    if not path.exists():
        raise unittest.SkipTest(f'real journal slice {path.name} is not present (pinned sha256 {SLICE_SHA256})')
    data = gzip.decompress(path.read_bytes())
    return data, [json.loads(line) for line in data.splitlines() if line.strip()]


def entry_evidence(row):
    entry = ((row.get('source') or {}).get('execution_evidence') or {}).get('entry') or {}
    return entry if isinstance(entry, dict) and entry.get('token_raw_amount') is not None and entry.get('raw_quote') else None


class MainJournal20261007ReplayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.expected = json.loads(EXPECTED_PATH.read_text(encoding='utf-8'))
        cls.data, cls.rows = load_slice(cls.expected)
        with tempfile.TemporaryDirectory() as tmp:
            with MainReplay(Path(tmp)) as replay:
                cls.result = replay.replay(copy.deepcopy(cls.rows))
                cls.decisions = list(replay.decisions)

    def test_slice_is_the_pinned_recorded_evidence(self):
        self.assertEqual(hashlib.sha256(self.data).hexdigest(), SLICE_SHA256)
        self.assertEqual(self.expected['slice']['jsonl_sha256'], SLICE_SHA256)
        self.assertEqual(len(self.rows), self.expected['slice']['rows'])
        self.assertEqual(sum(entry_evidence(r) is not None for r in self.rows), 10)
        # Recorded ids are content digests; every row keeps its recorded id.
        self.assertEqual(len({r['id'] for r in self.rows}), len(self.rows))

    def test_slice_is_evidence_only(self):
        """Every row is entry/mark/guard evidence, a linked commit outcome, or
        exact-pool context from the window before an entry preflight."""
        context_ms = self.expected['slice']['context_ms_before_preflight']
        ordered = sorted(self.rows, key=lambda r: r['available_at'])
        linked = {id(r) for r in MainReplay.link_commit_outcomes(ordered).values()}
        windows = []
        for r in ordered:
            entry = entry_evidence(r)
            if entry:
                started = int(entry.get('preflight_started_at') or r['available_at'])
                windows.append(((r['coin']['address'], r['coin']['pairAddress']), started-context_ms, r['available_at']))
        for r in ordered:
            evidence = r['source']['execution_evidence'] or {}
            key = (r['coin']['address'], r['coin']['pairAddress'])
            kept = (entry_evidence(r) is not None or bool(evidence.get('mark'))
                    or 'exit_quote' in (r.get('rejection_reasons') or []) or id(r) in linked
                    or any(key == pool and start <= r['available_at'] <= end for pool, start, end in windows))
            self.assertTrue(kept, r['id'])
        self.assertEqual(len({key for key, _, _ in windows}), 2)

    def test_replay_books_the_live_main_ledger(self):
        tolerance = self.expected['tolerance_usd']
        ledger = self.expected['live_ledger']
        result = self.result
        self.assertEqual(result['report_kind'], 'BASELINE')
        self.assertEqual(result['invalid_records'], 0)
        booked_closed = sorted(ledger['closed_trades'], key=lambda t: t['opened_at'])
        replayed_closed = sorted(result['history'], key=lambda t: t['opened_at'])
        self.assertEqual(len(replayed_closed), len(booked_closed))
        self.assertEqual(len(booked_closed), len(BOOKED_LIVE_PNL_USD['closed']))
        for replayed, booked, (pnl, reason) in zip(replayed_closed, booked_closed, BOOKED_LIVE_PNL_USD['closed']):
            with self.subTest(opened_at=booked['opened_at']):
                # The committed ledger values are the numbers the live engine booked.
                self.assertLess(abs(booked['pnl_usd']-pnl), 5e-5)
                self.assertEqual(booked['exit_reason'], reason)
                self.assertEqual(replayed['exit_reason'], booked['exit_reason'])
                self.assertEqual(replayed['jupiter_token_raw_amount'], booked['jupiter_token_raw_amount'])
                self.assertEqual((replayed['address'], replayed['pairAddress']), (booked['address'], booked['pairAddress']))
                self.assertEqual(replayed['notional_usd'], booked['notional_usd'])
                self.assertLess(abs(replayed['pnl_usd']-booked['pnl_usd']), tolerance)
                self.assertLess(abs(replayed['exit_net_proceeds_usd']-booked['exit_net_proceeds_usd']), tolerance)
                # Same recorded decisions: open and close within one recorded row's lag.
                self.assertLess(abs(replayed['opened_at']-booked['opened_at']), 1_000)
                self.assertLess(abs(replayed['closed_at']-booked['closed_at']), 1_000)
        booked_open = ledger['open_positions']
        self.assertEqual(len(result['positions']), len(booked_open))
        for replayed, booked, pnl in zip(result['positions'], booked_open, BOOKED_LIVE_PNL_USD['open']):
            self.assertLess(abs(booked['pnl_usd']-pnl), 5e-5)
            self.assertEqual(replayed['jupiter_token_raw_amount'], booked['jupiter_token_raw_amount'])
            self.assertEqual(replayed['valuation_status'], booked['valuation_status'])
            self.assertLess(abs(replayed['pnl_usd']-booked['pnl_usd']), tolerance)
            self.assertLess(abs(replayed['opened_at']-booked['opened_at']), 1_000)
        # Exactly the four live-opened entry rows open; the other six WAIT.
        by_id = {d['id']: d for d in self.decisions}
        opened = [r for r in self.rows if entry_evidence(r) is not None and by_id[r['id']]['opened']]
        self.assertEqual(len(opened), len(booked_closed)+len(booked_open))
        self.assertEqual(result['decision_summary']['positions_unvalued_at_end'], 0)


if __name__ == '__main__':
    unittest.main()
