import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import strategy_lab as lab
import entry_defense as _isolation_entry_defense
import strategy_lab as _isolation_lab

_DEFENSIVE_ISOLATION = []


def _defensive_pass(*_args, **_kwargs):
    return _isolation_entry_defense.pass_decision('TEST_GATE_ISOLATION')


def setUpModule():
    """These tests isolate other entry gates. DEFENSIVE_ENTRY_LAYER_V1 (structural rug
    guard, pool loss memory, heat veto and its warm-up) has its own suite in
    tests/test_defensive_entry_layer.py, which proves every path consults it."""
    for target, name in ((_isolation_lab, 'defensive_entry_decision'),):
        isolation = patch.object(target, name, _defensive_pass)
        isolation.start()
        _DEFENSIVE_ISOLATION.append(isolation)


def tearDownModule():
    while _DEFENSIVE_ISOLATION:
        _DEFENSIVE_ISOLATION.pop().stop()


NOW = 1_800_000_000_000
MINT = 'So11111111111111111111111111111111111111112'
PAIR = 'EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v'
CUSTOM = 'MOMENTUM_SWARM'


def custom_book(position=True):
    return {
        'id': CUSTOM, 'name': 'Momentum Swarm', 'starting_balance': 500,
        'balance': 413.58235118, 'portfolio_group': 'TEST', 'trade_seq': 40,
        'last_entry_by_address': {MINT: NOW - 10_000},
        'custom_policy': {'version': 'VPS_SWARM_V4'},
        'position': {
            'strategy_id': CUSTOM, 'address': MINT, 'pairAddress': PAIR,
            'symbol': 'SWARM', 'entry_price': .001, 'current_price': .001,
            'opened_at': NOW - 30_000, 'updated_at': NOW - 20_000,
            'notional_usd': 127.5, 'quantity': 127_500, 'open_pnl_usd': -3.2982,
            'custom_exit_memory': {'keep': True},
        } if position else None,
        'history': [
            {'trade_no': i, 'strategy_id': CUSTOM, 'pnl_usd': -2,
             'opened_at': NOW - i * 60_000, 'closed_at': NOW - i * 60_000 + 1,
             'custom_evidence': {'keep': i}}
            for i in reversed(range(40))
        ],
    }


class LabRegistryCompatibilityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.path = root / 'strategy_lab.json'
        self.compact = root / 'strategy_lab_compact.json'
        for name, value in {
            'STATE_PATH': self.path, 'COMPACT_PATH': self.compact,
            'RESET_FLAG_PATH': root / 'no-reset',
        }.items():
            p = patch.object(lab, name, value)
            p.start(); self.addCleanup(p.stop)
        for name in ('merge_astra_snapshot', 'merge_paired_snapshot'):
            p = patch.object(lab, name, side_effect=lambda state: state)
            p.start(); self.addCleanup(p.stop)
        p = patch.object(lab, 'now_ms', return_value=NOW)
        p.start(); self.addCleanup(p.stop)

    def load(self, custom):
        books = {s['id']: lab.empty_book(s) for s in lab.STRATEGIES}
        books[CUSTOM] = copy.deepcopy(custom)
        original = {'started_at': 42, 'books': books,
                    'stats': {CUSTOM: {'trades': 40, 'realized_pnl': -86.41764882}}}
        self.path.write_text(json.dumps(original), encoding='utf-8')
        loaded = lab.load_state()
        p = patch.object(lab, 'STATE', loaded)
        p.start(); self.addCleanup(p.stop)
        return loaded

    def test_unknown_custom_ledger_survives_persist_and_repeated_restart(self):
        original = custom_book()
        loaded = self.load(original)
        lab.persist()
        stored = json.loads(self.path.read_text(encoding='utf-8'))
        restarted = lab.load_state()
        for state in (loaded, stored, restarted):
            book = state['books'][CUSTOM]
            for field, value in original.items():
                self.assertEqual(book[field], value, field)
            self.assertEqual(book['runtime_compatibility']['status'], 'preserved_inactive')
            self.assertFalse(book['runtime_compatibility']['entry_enabled'])
            self.assertFalse(book['runtime_compatibility']['position_management_enabled'])
            self.assertEqual(state['registry_compatibility']['preserved_strategy_ids'], [CUSTOM])
            self.assertEqual(state['registry_compatibility']['preserved_open_position_ids'], [CUSTOM])
        self.assertEqual(stored['stats'][CUSTOM]['realized_pnl'], -86.41764882)
        self.assertTrue(stored['stats'][CUSTOM]['valuation_stale'])
        projected = json.loads(self.compact.read_text(encoding='utf-8'))
        view = projected['books'][CUSTOM]
        self.assertEqual(view['starting_balance'], original['starting_balance'])
        self.assertEqual(view['balance'], original['balance'])
        self.assertEqual(view['position']['pairAddress'], PAIR)
        self.assertEqual(view['position']['open_pnl_usd'], -3.2982)
        self.assertEqual([t['trade_no'] for t in view['history']],
                         [t['trade_no'] for t in original['history'][:30]])
        self.assertEqual(view['runtime_compatibility']['status'], 'preserved_inactive')
        self.assertEqual(projected['registry_compatibility']['status'], 'attention_required')

    def test_unknown_position_is_not_marked_or_closed_under_another_strategy_policy(self):
        original = custom_book()
        state = self.load(original)
        # Only the unknown book has an open position. Even a large price gap
        # cannot apply the standard book's exit rule to an unknown custom policy.
        with patch.object(lab.POSITION_MARK_FEED, 'resolve') as resolver:
            lab.update_positions({}, [{'address': MINT, 'pairAddress': PAIR, 'priceUsd': .0001}])
        resolver.assert_not_called()
        for field, value in original.items():
            self.assertEqual(state['books'][CUSTOM][field], value, field)

    def test_unknown_flat_book_cannot_enter_despite_candidate_and_registered_entries(self):
        original = custom_book(position=False)
        state = self.load(original)
        for key, book in state['books'].items():
            if key not in (CUSTOM, 'EARLY'):
                book['position'] = {'fixture': 'preserved registered position'}
        coin = {'address': MINT, 'pairAddress': PAIR, 'symbol': 'SWARM',
                'priceUsd': .01, 'priceNative': .00008, 'marketCap': 1e6,
                'quoteTokenAddress': lab.SOL_QUOTE_MINT,
                'liquidityUsd': 1e6, 'dexId': 'raydium', 'updatedAt': NOW,
                'score': 100, 'ageMinutes': 50, 'priceChange': {'m5': 12, 'h1': 50},
                'volume': {'h1': 1e6}, 'txns': {'m5': {'buys': 60, 'sells': 20}}}
        flow = {MINT: {'trades': 20, 'buys': 15, 'sells': 5, 'buy_usd': 1000,
                      'sell_usd': 100, 'unique_wallets': 10, 'ratio': 10, 'max_sell': 30}}
        with patch.object(lab.price_integrity, 'check', return_value={'status': 'pass', 'version': 'OFFLINE_FIXTURE'}):
            lab.maybe_open([coin], flow)
        self.assertIsNotNone(state['books']['EARLY']['position'])
        self.assertEqual(state['books'][CUSTOM]['position'], original['position'])
        self.assertEqual(state['books'][CUSTOM]['history'], original['history'])
        self.assertEqual(state['books'][CUSTOM]['trade_seq'], original['trade_seq'])

    def test_restored_registration_removes_only_our_inactive_annotation(self):
        source = custom_book(position=False)
        source['runtime_compatibility'] = {'version': 'LAB_REGISTRY_COMPATIBILITY_V1',
                                           'status': 'preserved_inactive'}
        source['id'] = 'EARLY'
        books = {'EARLY': source}
        report = lab.registry_compatibility(books)
        self.assertNotIn('runtime_compatibility', books['EARLY'])
        self.assertEqual(report['status'], 'compatible')

    def test_unsupported_custom_schema_refuses_restart_without_erasing_saved_state(self):
        raw = {'started_at': 42, 'books': {CUSTOM: ['unknown schema', {'history': 'retain'}]}}
        self.path.write_text(json.dumps(raw), encoding='utf-8')
        before = self.path.read_bytes()
        with self.assertRaisesRegex(ValueError, 'unsupported Lab book schema'):
            lab.load_state()
        self.assertEqual(self.path.read_bytes(), before)
        self.assertFalse(self.compact.exists())


if __name__ == '__main__':
    unittest.main()
