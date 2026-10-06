import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from lab_portfolio_migration import (
    ALLOCATION_PER_STRATEGY_USD,
    MAX_POSITION_FRACTION,
    MIGRATION_VERSION,
    PROMOTED_STRATEGIES,
    TOTAL_CAPITAL_USD,
    begin_promotion_drain,
    promote_strategy_lab,
)
import strategy_lab as lab
from strategy_lab import STRATEGIES


class StrategyLabPortfolioMigrationTests(unittest.TestCase):
    def fixture_state(self, root):
        books = {}
        for strategy in STRATEGIES:
            key = strategy['id']
            start = 100.0 if key == 'SCALPER' else 500.0
            books[key] = {
                'id': key, 'name': strategy['name'], 'starting_balance': start,
                'balance': start, 'position': None, 'positions': [], 'history': [],
                'trade_seq': 0, 'last_entry_by_address': {'old': 123},
            }
        for strategy_id in PROMOTED_STRATEGIES:
            book = books[strategy_id]
            pnls = [10.0] * 6 + [-2.0] * 6
            book['history'] = [
                {'trade_no': n + 1, 'address': 'MintEpisodeA', 'opened_at': 1_800_000_000_000 + n * 60_000,
                 'closed_at': 1_800_000_030_000 + n * 60_000, 'pnl_usd': pnl}
                for n, pnl in enumerate(pnls)
            ]
            book['balance'] += sum(pnls)
        for strategy_id, balance in {
            'LIQUIDITY': 379.42, 'SCALPER': 21.02, 'VOLUME_SURGE': 473.33,
            'REVERSAL': 399.67, 'DEEP_LIQ_MOMENTUM': 366.66,
        }.items():
            books[strategy_id]['balance'] = balance
            books[strategy_id]['history'] = [
                {'trade_no': 1, 'address': 'LossMint', 'opened_at': 1000, 'closed_at': 2000, 'pnl_usd': balance - books[strategy_id]['starting_balance']}
            ]
        books['LIQUIDITY']['position'] = {'address': 'OpenLossMint', 'notional_usd': 10}
        books['TREND']['balance'] = 520.17
        books['TREND']['position'] = {'address': 'OpenTestMint', 'notional_usd': 10}

        state = {'status': 'online', 'books': books, 'stats': {'stale': True}}
        (root / 'strategy_lab.json').write_text(json.dumps(state), encoding='utf-8')
        (root / 'strategy_lab_compact.json').write_text(json.dumps({'old': True}), encoding='utf-8')
        # This is the distinct personal account and must remain untouched.
        (root / 'user_accounts.json').write_text(json.dumps({'accounts': {'personal': {'balance': 1000, 'history': ['keep']}}}), encoding='utf-8')
        return state

    def test_promotes_only_qualified_paper_books_and_restarts_losers_after_verified_archive(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            original = self.fixture_state(root)
            result = promote_strategy_lab(root)

            archive = Path(result['archive'])
            archived = json.loads((archive / 'strategy_lab.json').read_text(encoding='utf-8'))
            manifest = json.loads((archive / 'manifest.json').read_text(encoding='utf-8'))
            original_bytes = (archive / 'strategy_lab.json').read_bytes()
            self.assertEqual(hashlib.sha256(original_bytes).hexdigest(), manifest['files']['strategy_lab.json']['sha256'])
            self.assertEqual(archived['books']['EARLY']['balance'], original['books']['EARLY']['balance'])

            current = json.loads((root / 'strategy_lab.json').read_text(encoding='utf-8'))
            cohort = current['portfolio_setup']
            self.assertEqual(cohort['version'], MIGRATION_VERSION)
            self.assertEqual(cohort['total_allocated_capital_usd'], TOTAL_CAPITAL_USD)
            self.assertEqual(cohort['historical_simulations'], 48)
            self.assertEqual(cohort['unique_market_episodes_30m'], 1)
            self.assertTrue(cohort['accounts_are_independent'])
            self.assertFalse(cohort['real_execution_enabled'])

            self.assertEqual(sum(current['books'][key]['starting_balance'] for key in PROMOTED_STRATEGIES), TOTAL_CAPITAL_USD)
            for key in PROMOTED_STRATEGIES:
                book = current['books'][key]
                self.assertEqual(book['portfolio_group'], 'PROMOTED_PAPER')
                self.assertEqual(book['starting_balance'], ALLOCATION_PER_STRATEGY_USD)
                self.assertEqual(book['balance'], ALLOCATION_PER_STRATEGY_USD)
                self.assertEqual(book['history'], [])
                self.assertEqual(book['position'], None)
                self.assertEqual(book['max_position_fraction'], MAX_POSITION_FRACTION)

            for key in ['LIQUIDITY', 'SCALPER', 'VOLUME_SURGE', 'REVERSAL', 'DEEP_LIQ_MOMENTUM']:
                book = current['books'][key]
                self.assertEqual(book['portfolio_group'], 'TEST')
                self.assertEqual(book['balance'], book['starting_balance'])
                self.assertEqual(book['history'], [])
                self.assertIsNone(book['position'])
                self.assertEqual(book['last_reset']['cancelled_open_position'], key == 'LIQUIDITY')

            # The open, non-losing TREND test remains untouched and stays separate.
            self.assertEqual(current['books']['TREND']['history'], original['books']['TREND']['history'])
            self.assertEqual(current['books']['TREND']['position'], original['books']['TREND']['position'])
            self.assertEqual(current['books']['TREND']['portfolio_group'], 'TEST')
            private = json.loads((root / 'user_accounts.json').read_text(encoding='utf-8'))
            self.assertEqual(private['accounts']['personal']['balance'], 1000)
            self.assertEqual(private['accounts']['personal']['history'], ['keep'])
            compact = json.loads((root / 'strategy_lab_compact.json').read_text(encoding='utf-8'))
            self.assertEqual(compact['portfolio_setup']['version'], MIGRATION_VERSION)
            self.assertEqual(compact['books']['EARLY']['portfolio_group'], 'PROMOTED_PAPER')

            # A service restart reloads the new $250 books and selection evidence.
            with patch.object(lab, 'STATE_PATH', root / 'strategy_lab.json'), patch.object(lab, 'RESET_FLAG_PATH', root / 'no-reset-flag'):
                restored = lab.load_state()
            self.assertEqual(sum(restored['books'][key]['balance'] for key in PROMOTED_STRATEGIES), TOTAL_CAPITAL_USD)
            self.assertEqual(restored['portfolio_setup']['historical_simulations'], 48)
            self.assertTrue(all(restored['books'][key]['portfolio_group'] == 'PROMOTED_PAPER' for key in PROMOTED_STRATEGIES))

    def test_rejects_repeat_reset_after_the_funded_cohort_has_started(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.fixture_state(root)
            promote_strategy_lab(root)
            state_path = root / 'strategy_lab.json'
            state = json.loads(state_path.read_text(encoding='utf-8'))
            state['books']['EARLY']['history'] = [{'trade_no': 1, 'pnl_usd': -5, 'closed_at': 9}]
            state_path.write_text(json.dumps(state), encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'already applied'):
                promote_strategy_lab(root)
            self.assertEqual(json.loads(state_path.read_text(encoding='utf-8'))['books']['EARLY']['history'][0]['pnl_usd'], -5)

    def test_rejects_unqualified_history_before_archiving_or_mutating(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = self.fixture_state(root)
            state['books']['EARLY']['balance'] = 500
            state['books']['EARLY']['history'] = [
                {'trade_no': n, 'address': 'MintEpisodeA', 'opened_at': n * 1000,
                 'closed_at': n * 1000 + 500, 'pnl_usd': 1.0 if n <= 6 else -0.5}
                for n in range(1, 13)
            ]
            (root / 'strategy_lab.json').write_text(json.dumps(state), encoding='utf-8')
            with self.assertRaisesRegex(ValueError, r'no longer meets the \$25'):
                promote_strategy_lab(root)
            self.assertEqual(json.loads((root / 'strategy_lab.json').read_text(encoding='utf-8'))['books']['EARLY']['balance'], 500)
            self.assertFalse((root / 'archive').exists())

    def test_drain_transfers_open_winners_to_exit_only_books_and_starts_fresh_cohort(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.fixture_state(root)
            path = root / 'strategy_lab.json'
            state = json.loads(path.read_text(encoding='utf-8'))
            state['books']['EARLY']['position'] = {'address': 'WinnerMint', 'notional_usd': 25}
            path.write_text(json.dumps(state), encoding='utf-8')

            drain = begin_promotion_drain(root)
            self.assertEqual(drain['status'], 'DRAINING')
            self.assertEqual(drain['pending_positions'], ['EARLY'])
            state = json.loads(path.read_text(encoding='utf-8'))
            self.assertTrue(state['books']['EARLY']['promotion_pending'])
            self.assertIsNotNone(state['books']['EARLY']['position'])
            self.assertEqual(len(state['books']['EARLY']['history']), 12)
            self.assertEqual(state['books']['LIQUIDITY']['balance'], 500)
            self.assertIsNone(state['books']['LIQUIDITY']['position'])
            self.assertEqual(state['portfolio_setup']['status'], 'DRAINING')

            # Open legacy exposure is kept in a separate exit-only book while
            # the four newly funded books start immediately at $250 each.
            promoted = promote_strategy_lab(root)
            self.assertEqual(promoted['promoted_capital_usd'], TOTAL_CAPITAL_USD)
            state = json.loads(path.read_text(encoding='utf-8'))
            self.assertEqual(state['portfolio_setup']['status'], 'ACTIVE')
            self.assertIn('LIQUIDITY', state['portfolio_setup']['losing_test_books_restarted'])
            self.assertEqual(state['portfolio_setup']['legacy_open_positions'], ['EARLY'])
            self.assertEqual(state['portfolio_setup']['legacy_draining_books']['EARLY']['position']['address'], 'WinnerMint')
            for key in PROMOTED_STRATEGIES:
                self.assertFalse(state['books'][key]['promotion_pending'])
                self.assertEqual(state['books'][key]['balance'], 250)
                self.assertEqual(state['books'][key]['history'], [])
            self.assertIsNone(state['books']['EARLY']['position'])
            self.assertFalse(state['books']['EARLY']['last_reset']['cancelled_open_position'])


if __name__ == '__main__':
    unittest.main()
