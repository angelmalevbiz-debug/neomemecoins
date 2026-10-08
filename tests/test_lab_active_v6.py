"""LAB_ACTIVE_V6: one admission cost cap (0.5 x net stop) for every Lab book.

Synthetic fixtures verify cap consistency, headroom records and ledger
preservation. Nothing here measures or claims profitability.
"""
import copy
import json
import math
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

TMP = tempfile.TemporaryDirectory()
os.environ['NEO_STRATEGY_LAB_PATH'] = str(Path(TMP.name) / 'state.json')
os.environ['NEO_STRATEGY_LAB_COMPACT_PATH'] = str(Path(TMP.name) / 'compact.json')
os.environ['NEO_STRATEGY_LAB_RESET_FLAG'] = str(Path(TMP.name) / 'reset')
import lab_activity as a
import promoted_entry_guard as promoted_guard
import strategy_lab as lab

ADDRESS = 'So11111111111111111111111111111111111111112'
PAIR = 'EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v'
NOW = 1_800_000_000_000
SOL_USD = 150.0


def coin(**changes):
    return {'address': ADDRESS, 'pairAddress': PAIR, 'symbol': 'TEST', 'name': 'Test',
            'priceUsd': .01, 'priceNative': .01 / SOL_USD, 'marketCap': 1e6, 'liquidityUsd': 1e6,
            'quoteTokenAddress': lab.SOL_QUOTE_MINT,
            'dexId': 'raydium', 'updatedAt': NOW, 'score': 100, 'ageMinutes': 50,
            'priceChange': {'m5': 12, 'h1': 50}, 'volume': {'h1': 1e6},
            'txns': {'m5': {'buys': 60, 'sells': 20}}, **changes}


def expensive_coin():
    # PumpSwap 95 bps tier (market cap 12,000 SOL): fee-only round trip 1.9%,
    # with the Lab's 0.4% slippage/latency buffers about 2.3%. V5 TEST books
    # admitted this (cap 2.75%); V6 rejects it (cap 1.5%).
    return coin(dexId='pumpswap', marketCap=12_000 * SOL_USD)


def flows():
    return {ADDRESS: {'trades': 20, 'buys': 15, 'sells': 5, 'buy_usd': 1000, 'sell_usd': 100,
                      'unique_wallets': 10, 'ratio': 10, 'max_sell': 30}}


class CapConsistencyTests(unittest.TestCase):
    def setUp(self):
        guard = patch.object(lab.price_integrity, 'check', side_effect=lambda c: {
            'status': 'pass', 'version': 'OFFLINE_FIXTURE', 'mint': c['address'], 'pair': c['pairAddress']})
        guard.start(); self.addCleanup(guard.stop)
        lab.rush_brain._SAMPLE_BY_PAIR.clear()
        lab.STATE = {'started_at': 42, 'books': {s['id']: lab.empty_book(s) for s in lab.STRATEGIES}}

    def test_policy_version_and_cap_are_tied_to_the_promoted_guard(self):
        self.assertEqual(a.POLICY_VERSION, 'LAB_ACTIVE_V6_STOP_BUDGET_COST_CAP')
        self.assertEqual(a.PREVIOUS_POLICY_VERSION, 'LAB_ACTIVE_V5_CAUSAL_MOMENTUM_RUSH_BRAIN')
        self.assertEqual(a.admission_cost_cap_pct(lab.STOP_LOSS), promoted_guard.max_entry_cost_pct(lab.STOP_LOSS))
        self.assertEqual(lab.lab_cost_cap_pct(), 1.5)
        # The research model ceiling is unchanged and still bounds any caller.
        self.assertEqual(a.MAX_ENTRY_COST_PCT, 2.75)
        self.assertEqual(a.admission_cost_cap_pct(10), 2.75)
        self.assertEqual(a.admission_cost_cap_pct(0), 0.0)
        self.assertEqual(a.admission_cost_cap_pct('bad'), 0.0)
        config = a.policy_config(lab.STOP_LOSS)
        self.assertEqual(config['max_entry_roundtrip_cost_pct'], 1.5)
        self.assertEqual(config['model_cost_ceiling_pct'], 2.75)
        self.assertEqual(config['previous_test_book_cost_cap_pct'], 2.75)
        self.assertEqual(config['admission_cost_cap_stop_fraction'], .5)
        self.assertTrue(config['records_stop_headroom_pct'])

    def test_stop_headroom_is_stop_minus_entry_cost_and_never_invented(self):
        self.assertEqual(a.stop_headroom_pct(3.0, -1.2), 1.8)
        self.assertEqual(a.stop_headroom_pct(3.0, 0.0), 3.0)
        self.assertEqual(a.stop_headroom_pct(3.0, 0.4), 3.0)
        for stop, pnl in ((0, -1), (-3, -1), (math.nan, -1), (3, math.nan), ('x', -1), (3, None)):
            self.assertIsNone(a.stop_headroom_pct(stop, pnl), (stop, pnl))

    def test_every_book_rejects_a_round_trip_the_v5_test_cap_would_have_admitted(self):
        c = expensive_coin()
        legacy = a.affordable_entry(c, 500, 150, lab.entry_execution, lab.exit_execution)
        self.assertIsNotNone(legacy)
        self.assertLess(legacy['initial_pnl_pct'], -1.5)
        self.assertGreaterEqual(legacy['initial_pnl_pct'], -2.75)
        self.assertIsNone(a.affordable_entry(c, 500, 150, lab.entry_execution, lab.exit_execution,
                                             max_entry_cost_pct=a.admission_cost_cap_pct(lab.STOP_LOSS)))
        with patch.object(lab, 'now_ms', return_value=NOW):
            lab.maybe_open([c], flows())
        signalled = 0
        for book in lab.STATE['books'].values():
            self.assertIsNone(book['position'], book['id'])
            diagnostics = book['entry_diagnostics']
            self.assertEqual(diagnostics['max_entry_roundtrip_cost_pct'], 1.5, book['id'])
            if diagnostics.get('signal_candidates'):
                signalled += 1
                if diagnostics.get('matched_candidates'):
                    self.assertGreaterEqual(diagnostics['cost_rejected'], 1, book['id'])
                    self.assertEqual(diagnostics['cost_infeasible_candidates'], diagnostics['cost_rejected'])
                    self.assertEqual(diagnostics['affordable_candidates'], 0)
                    self.assertIn(diagnostics['blocked_reason'],
                                  {'modeled_roundtrip_cost_limit', 'promoted_cost_headroom_insufficient',
                                   'promoted_verified_flow_unavailable'}, book['id'])
                feasibility = diagnostics['cost_feasibility']
                self.assertEqual(feasibility['maximum_roundtrip_cost_pct'], 1.5)
                self.assertFalse(feasibility['profitability_proven'])
                self.assertGreater(feasibility['minimum_model_roundtrip_cost_pct'], 1.5)
        self.assertGreater(signalled, 10)

    def test_cost_limited_test_books_report_the_reason_instead_of_hiding_it(self):
        with patch.object(lab, 'now_ms', return_value=NOW):
            lab.maybe_open([expensive_coin()], flows())
        trend = lab.STATE['books']['TREND']['entry_diagnostics']
        self.assertEqual(trend['signal_candidates'], 1)
        self.assertEqual(trend['matched_candidates'], 1)
        self.assertEqual(trend['cost_rejected'], 1)
        self.assertEqual(trend['blocked_reason'], 'modeled_roundtrip_cost_limit')
        self.assertEqual(trend['entry_policy_version'], a.POLICY_VERSION)
        self.assertEqual(trend['stop_loss_net_pct'], 3.0)

    def test_admitted_entries_and_closes_record_cap_and_headroom(self):
        with patch.object(lab, 'now_ms', return_value=NOW):
            lab.maybe_open([coin()], flows())
        book = lab.STATE['books']['TREND']
        position = book['position']
        self.assertIsNotNone(position)
        self.assertEqual(position['entry_policy_version'], a.POLICY_VERSION)
        self.assertEqual(position['entry_cost_cap_pct'], 1.5)
        self.assertEqual(position['stop_loss_net_pct'], 3.0)
        self.assertGreaterEqual(position['entry_roundtrip_pnl_pct'], -1.5)
        self.assertAlmostEqual(position['stop_headroom_pct'], 3.0 + position['entry_roundtrip_pnl_pct'], places=5)
        self.assertGreaterEqual(position['stop_headroom_pct'], 1.5)
        quote = {'fill_price': .0094, 'net_proceeds_usd': position['remaining_cost_basis_usd'] - 6,
                 'dex_fee_usd': 0, 'network_fee_usd': 0, 'impact_pct': 0, 'slippage_pct': 0, 'latency_pct': 0}
        with patch.object(lab, 'exit_execution', return_value=quote), patch.object(lab, 'now_ms', return_value=NOW + 5000):
            lab.update_positions({}, [coin(updatedAt=NOW + 5000)])
        closed = book['history'][0]
        self.assertEqual(closed['exit_reason'], 'STOP_LOSS_3_NET')
        self.assertEqual(closed['pnl_usd'], -6)
        self.assertEqual(closed['entry_policy_version'], 'LAB_ACTIVE_V6_STOP_BUDGET_COST_CAP')
        self.assertEqual(closed['entry_cost_cap_pct'], 1.5)
        self.assertEqual(closed['stop_headroom_pct'], position['stop_headroom_pct'])
        self.assertEqual(lab.stats(book)['active_policy_trades'], 1)

    def test_existing_v5_ledgers_balances_and_open_positions_are_untouched(self):
        saved = {s['id']: lab.empty_book(s) for s in lab.STRATEGIES if s['id'] not in lab.cost_first.BOOK_IDS}
        saved['TREND'].update(balance=412.5, trade_seq=9, history=[{
            'trade_no': 9, 'address': ADDRESS, 'pairAddress': PAIR, 'pnl_usd': -4.1,
            'entry_policy_version': a.PREVIOUS_POLICY_VERSION, 'entry_roundtrip_pnl_pct': -2.7,
            'closed_at': NOW - 1000, 'opened_at': NOW - 5000}])
        saved['LIQUIDITY']['position'] = {'trade_no': 3, 'address': ADDRESS, 'pairAddress': PAIR,
                                          'entry_policy_version': a.PREVIOUS_POLICY_VERSION,
                                          'notional_usd': 150, 'quantity': 1}
        path = Path(TMP.name) / 'ledger.json'
        path.write_text(json.dumps({'started_at': 42, 'books': saved, 'activity_version': a.PREVIOUS_POLICY_VERSION}))
        with patch.object(lab, 'STATE_PATH', path), patch.object(lab, 'RESET_FLAG_PATH', Path(TMP.name) / 'absent.reset'):
            state = lab.load_state()
        for key, book in saved.items():
            for field in ('balance', 'starting_balance', 'history', 'position', 'trade_seq'):
                self.assertEqual(state['books'][key][field], book[field], (key, field))
        self.assertEqual(state['books']['TREND']['history'][0]['entry_policy_version'], a.PREVIOUS_POLICY_VERSION)
        self.assertEqual(state['books']['TREND']['history'][0]['entry_roundtrip_pnl_pct'], -2.7)
        for key in lab.cost_first.BOOK_IDS:
            self.assertEqual(state['books'][key]['balance'], 500)
            self.assertEqual(state['books'][key]['portfolio_group'], 'TEST')
            self.assertEqual(state['books'][key]['history'], [])
        self.assertEqual(state['activity_version'], a.PREVIOUS_POLICY_VERSION)

    def test_persisted_activity_config_publishes_the_shared_cap(self):
        lab.STATE['books']['TREND']['balance'] = 499.0
        before = copy.deepcopy(lab.STATE['books'])
        with patch.object(lab, 'now_ms', return_value=NOW):
            lab.persist('online')
        config = lab.STATE['activity_config']
        self.assertEqual(config['version'], a.POLICY_VERSION)
        self.assertEqual(config['max_entry_roundtrip_cost_pct'], 1.5)
        self.assertEqual(config['stop_loss_net_pct'], 3.0)
        self.assertEqual(config['promoted_entry_policy']['maximum_roundtrip_cost_pct'], 1.5)
        self.assertFalse(config['cost_first_established']['automatic_promotion'])
        for key, book in before.items():
            for field in ('balance', 'history', 'position'):
                self.assertEqual(lab.STATE['books'][key][field], book[field], key)
        published = json.loads(Path(lab.COMPACT_PATH).read_text(encoding='utf-8'))
        self.assertEqual(published['activity_config']['max_entry_roundtrip_cost_pct'], 1.5)


if __name__ == '__main__':
    unittest.main()
