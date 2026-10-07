import copy
import json
import math
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import lab_strategy_lifecycle as lifecycle
import strategy_lab as lab


NOW = 1_800_000_000_000
MINT = 'So11111111111111111111111111111111111111112'
PAIR = 'EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v'
RESEARCH = 'LIQUIDITY'


def history(pnls, strategy_id=RESEARCH):
    return [{
        'trade_no': index + 1, 'strategy_id': strategy_id,
        'address': MINT, 'pairAddress': PAIR, 'pnl_usd': pnl,
        'opened_at': NOW - 100_000 + index * 1000,
        'closed_at': NOW - 99_000 + index * 1000,
        'entry_policy_version': lab.activity.POLICY_VERSION,
        'execution_mode': lab.EXECUTION_MODEL_VERSION,
        'price_crosscheck': {'status': 'pass', 'mint': MINT, 'pair': PAIR},
        'quote_status': 'fresh',
    } for index, pnl in enumerate(pnls)]


def book(pnls, strategy_id=RESEARCH):
    source = next(s for s in lab.STRATEGIES if s['id'] == strategy_id)
    result = lab.empty_book(source)
    result['history'] = history(pnls, strategy_id)
    result['balance'] = result['starting_balance'] + sum(pnls)
    result['trade_seq'] = len(pnls)
    return result


def review(books):
    return lifecycle.apply_lifecycle(
        books, registered_ids=set(books), promoted_ids=set(lab.PROMOTED_STRATEGIES),
        activity_version=lab.activity.POLICY_VERSION,
        execution_version=lab.EXECUTION_MODEL_VERSION, now=NOW)


class LifecycleEvidenceTests(unittest.TestCase):
    def test_retirement_preserves_every_original_ledger_field(self):
        original = book([-4] * 12)
        original['position'] = {'strategy_id': RESEARCH, 'open_pnl_usd': -1}
        stored = copy.deepcopy(original)
        report = review({RESEARCH: stored})
        self.assertEqual(report['retired_strategy_ids'], [RESEARCH])
        self.assertEqual(report['retired_open_position_ids'], [RESEARCH])
        for field, value in original.items():
            self.assertEqual(stored[field], value, field)
        marker = stored['strategy_lifecycle']
        self.assertFalse(marker['entry_enabled'])
        self.assertTrue(marker['position_management_enabled'])
        self.assertEqual(marker['evidence']['closed_trades'], 12)
        self.assertEqual(marker['evidence']['net_pnl_usd'], -48)
        self.assertEqual(marker['retired_at'], NOW)

    def test_no_evidence_small_sample_profitable_or_recovering_book_is_not_retired(self):
        samples = ([], [-4] * 8, [-4] * 5 + [1] + [-4] * 4 + [1], [10, -1] * 12,
                   [10] * 6 + [-5] * 18, [-5] * 18 + [10] * 6)
        for pnls in samples:
            with self.subTest(pnls=pnls):
                stored = book(pnls)
                self.assertEqual(review({RESEARCH: stored})['retired_strategy_ids'], [])
                self.assertTrue(lifecycle.entry_enabled(stored))

    def test_negative_total_with_many_wins_does_not_pass_win_rate_filter(self):
        stored = book([1, 1, -5] * 8)
        report = review({RESEARCH: stored})
        self.assertEqual(report['retired_strategy_ids'], [])
        self.assertGreater(stored['strategy_lifecycle']['evidence']['win_rate_upper_bound_95'], .5)

    def test_nine_all_negative_unique_closes_can_retire_but_eight_or_breakeven_cannot(self):
        for pnls, retired in (([-4] * 9, True), ([-4] * 8, False), ([-4] * 8 + [0], False)):
            with self.subTest(pnls=pnls):
                stored = book(pnls)
                self.assertEqual(bool(review({RESEARCH: stored})['retired_strategy_ids']), retired)

    def test_old_regime_invalid_price_identity_and_nonfinite_rows_cannot_retire(self):
        for field, value in (
                ('entry_policy_version', 'OLDER_POLICY'),
                ('execution_mode', 'OLDER_EXECUTION'), ('pnl_usd', math.nan),
                ('closed_at', NOW + 1), ('opened_at', NOW), ('pnl_usd', True),
                ('quote_status', 'stale'), ('audit_pending', True),
                ('price_crosscheck', {'status': 'pass', 'mint': 'wrong', 'pair': PAIR})):
            with self.subTest(field=field):
                stored = book([-4] * 9)
                stored['history'][0][field] = value
                self.assertEqual(review({RESEARCH: stored})['retired_strategy_ids'], [])
                self.assertEqual(stored['strategy_lifecycle']['evidence']['closed_trades'], 8)

    def test_duplicate_identity_never_inflates_sample_and_conflicts_are_excluded(self):
        stored = book([-4] * 8)
        stored['history'].extend(copy.deepcopy(stored['history']))
        self.assertEqual(review({RESEARCH: stored})['retired_strategy_ids'], [])
        self.assertEqual(stored['strategy_lifecycle']['evidence']['closed_trades'], 8)
        stored = book([-4] * 9)
        conflict = copy.deepcopy(stored['history'][0])
        conflict['pnl_usd'] = 4
        stored['history'].append(conflict)
        self.assertEqual(review({RESEARCH: stored})['retired_strategy_ids'], [])
        self.assertEqual(stored['strategy_lifecycle']['evidence']['closed_trades'], 8)

    def test_malformed_quote_status_and_pool_identities_are_excluded_without_throwing(self):
        for field, value in (('quote_status', []), ('quote_status', {}),
                             ('address', [MINT]), ('address', {'mint': MINT}),
                             ('pairAddress', [PAIR]), ('pairAddress', {'pair': PAIR}),
                             ('address', '   '), ('pairAddress', '')):
            with self.subTest(field=field, value=value):
                stored = book([-4] * 9)
                stored['history'][0][field] = value
                self.assertEqual(review({RESEARCH: stored})['retired_strategy_ids'], [])
                self.assertEqual(stored['strategy_lifecycle']['evidence']['closed_trades'], 8)
    def test_overflow_cannot_create_non_json_retirement_evidence(self):
        stored = book([-4] * 12)
        for row in stored['history']:
            row['pnl_usd'] = -1e308
        self.assertEqual(review({RESEARCH: stored})['retired_strategy_ids'], [])
        json.dumps(stored['strategy_lifecycle'], allow_nan=False)

    def test_promoted_books_are_exempt_from_test_retirement(self):
        for strategy_id in lab.PROMOTED_STRATEGIES:
            stored = book([-4] * 30, strategy_id)
            stored['portfolio_group'] = 'TEST'
            self.assertEqual(review({strategy_id: stored})['retired_strategy_ids'], [])
            self.assertTrue(lifecycle.entry_enabled(stored))

    def test_retirement_survives_restart_and_has_no_timed_reactivation(self):
        stored = book([-4] * 12)
        review({RESEARCH: stored})
        marker = copy.deepcopy(stored['strategy_lifecycle'])
        restarted = json.loads(json.dumps(stored))
        restarted['history'] = history([10] * 30)
        review({RESEARCH: restarted})
        self.assertEqual(restarted['strategy_lifecycle'], marker)
        self.assertFalse(lifecycle.entry_enabled(restarted))


class LifecycleIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        for name, value in {
            'STATE_PATH': root / 'strategy_lab.json',
            'COMPACT_PATH': root / 'strategy_lab_compact.json',
            'RESET_FLAG_PATH': root / 'no-reset',
        }.items():
            p = patch.object(lab, name, value)
            p.start(); self.addCleanup(p.stop)
        p = patch.object(lab, 'now_ms', return_value=NOW)
        p.start(); self.addCleanup(p.stop)
        for name in ('merge_astra_snapshot', 'merge_paired_snapshot'):
            p = patch.object(lab, name, side_effect=lambda state: state)
            p.start(); self.addCleanup(p.stop)
        state = {'started_at': 42, 'books': {s['id']: lab.empty_book(s) for s in lab.STRATEGIES}}
        p = patch.object(lab, 'STATE', state)
        p.start(); self.addCleanup(p.stop)

    def coin(self):
        return {'address': MINT, 'pairAddress': PAIR, 'symbol': 'COIN',
                'priceUsd': .01, 'priceNative': .00008, 'marketCap': 1e6,
                'quoteTokenAddress': lab.SOL_QUOTE_MINT,
                'liquidityUsd': 1e6, 'dexId': 'raydium', 'updatedAt': NOW,
                'score': 100, 'ageMinutes': 50, 'priceChange': {'m5': 12, 'h1': 50},
                'volume': {'h1': 1e6}, 'txns': {'m5': {'buys': 60, 'sells': 20}}}

    def test_retired_flat_registered_book_cannot_enter_and_restart_keeps_ledger(self):
        for source in lab.STATE['books'].values():
            source['position'] = {'fixture': 'other registered book'}
        original = book([-4] * 12)
        lab.STATE['books'][RESEARCH] = copy.deepcopy(original)
        with patch.object(lab.price_integrity, 'check') as price_check:
            lab.maybe_open([self.coin()], {})
        price_check.assert_not_called()
        self.assertIsNone(lab.STATE['books'][RESEARCH]['position'])
        self.assertEqual(lab.STATE['books'][RESEARCH]['trade_seq'], 12)
        self.assertEqual(lab.STATE['books'][RESEARCH]['entry_diagnostics']['blocked_reason'],
                         'strategy_retired_observed_losses')
        lab.persist()
        reloaded = lab.load_state()
        for field, value in original.items():
            self.assertEqual(reloaded['books'][RESEARCH][field], value, field)
        self.assertFalse(lifecycle.entry_enabled(reloaded['books'][RESEARCH]))
        projected = json.loads(lab.COMPACT_PATH.read_text(encoding='utf-8'))
        self.assertEqual(projected['books'][RESEARCH]['strategy_lifecycle']['status'], 'retired')
        self.assertEqual(projected['strategy_lifecycle']['retired_strategy_ids'], [RESEARCH])
        self.assertNotIn('runtime_compatibility', reloaded['books'][RESEARCH])

    def test_retired_open_position_continues_registered_exit_management(self):
        target = book([-4] * 12)
        target['position'] = {'strategy_id': RESEARCH, 'address': MINT, 'pairAddress': PAIR,
                              'entry_price': .01, 'peak_price': .01, 'quantity': 10_000,
                              'notional_usd': 100, 'remaining_cost_basis_usd': 100,
                              'opened_at': NOW - 30_000}
        lab.STATE['books'][RESEARCH] = target
        review({RESEARCH: target})
        coin = self.coin()
        coin.update(priceUsd=.008, priceNative=.000064)
        with patch.object(lab.POSITION_MARK_FEED, 'resolve', return_value=coin) as resolver:
            lab.update_positions({}, [coin])
        resolver.assert_called_once()
        self.assertIsNone(target['position'])
        self.assertEqual(len(target['history']), 13)
        self.assertEqual(target['history'][0]['exit_reason'], 'STOP_LOSS_3_NET')
        self.assertFalse(lifecycle.entry_enabled(target))

    def test_promoted_market_mismatch_does_not_claim_missing_flow(self):
        for source in lab.STATE['books'].values():
            source['position'] = {'fixture': 'other registered book'}
        promoted = lab.STATE['books']['EARLY']
        promoted['position'] = None
        coin = self.coin()
        coin['ageMinutes'] = 121  # Outside the shared EARLY screen's 120-minute age limit.
        with patch.object(lab.promoted_guard, 'flow_admission') as flow_gate:
            lab.maybe_open([coin], {})
        flow_gate.assert_not_called()
        diag = promoted['entry_diagnostics']
        self.assertEqual(diag['signal_candidates'], 0)
        self.assertEqual(diag['flow_missing_candidates'], 0)
        self.assertEqual(diag['market_rejected_candidates'], 1)
        self.assertEqual(diag['blocked_reason'], 'no_market_signal')
        lab.persist()
        projection = json.loads(lab.COMPACT_PATH.read_text(encoding='utf-8'))
        self.assertEqual(projection['books']['EARLY']['entry_diagnostics']['market_rejected_candidates'], 1)


if __name__ == '__main__':
    unittest.main()
