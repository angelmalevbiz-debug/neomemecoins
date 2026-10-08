"""Synthetic-ledger tests for the read-only PAPER edge report. No real ledgers, no network."""
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location(
    'paper_edge_report', Path(__file__).resolve().parents[1] / 'scripts' / 'paper_edge_report.py')
edge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(edge)

START = 1_791_000_000_000
MINUTE = 60_000
HOUR = 3_600_000
DAY = 86_400_000
UUID = '3aa07b45-1234-4abc-9def-0123456789ab'


def engine_trade(session, n, pnl_usd, *, notional=100.0, mint='MINT1', pool='POOL1', opened=None,
                 hold_ms=30_000, exit_reason='STOP_LOSS_NET_TARGET', entry_rt_pct=-2.0,
                 entry_policy='ENTRY_V4', exit_policy='EXIT_V1', move_pct=0.0, exit_state='CLOSED'):
    opened = START + n * MINUTE if opened is None else opened
    entry_price = 0.01
    quantity = notional / entry_price
    exit_price = entry_price * (1 + move_pct / 100)
    # net proceeds reproduce pnl exactly: pnl = net - (notional + fixed entry cost)
    fixed_entry = 0.03
    net_proceeds = notional + fixed_entry + pnl_usd
    return {
        'id': f'{session}:{mint}:{n}', 'session_id': session, 'trade_no': n,
        'strategy_id': 'WINNER_ENSEMBLE_PAPER_V1', 'entry_policy_version': entry_policy,
        'exit_policy_version': exit_policy, 'address': mint, 'pairAddress': pool,
        'opened_at': opened, 'closed_at': opened + hold_ms, 'exit_state': exit_state,
        'exit_reason': exit_reason, 'notional_usd': notional, 'original_notional_usd': notional,
        'pnl_usd': pnl_usd, 'pnl_pct': pnl_usd / notional * 100,
        'market_entry_price': entry_price, 'entry_price': entry_price, 'exit_price': exit_price,
        'execution_entry_price': entry_price * 1.01, 'quantity': quantity,
        'entry_roundtrip_pnl_pct': entry_rt_pct, 'entry_dex_fee_usd': 0.0,
        'entry_network_fee_usd': fixed_entry, 'entry_account_reserve_usd': 0.0,
        'exit_net_proceeds_usd': net_proceeds, 'exit_gross_proceeds_usd': net_proceeds + 0.03,
        'exit_dex_fee_usd': 0.0, 'exit_network_fee_usd': 0.03,
        'exit_price_impact_pct': 1.0, 'exit_slippage_pct': 0.1, 'partial_fills': [{'leg': 1}],
    }


def engine_state(session, trades, starting=1000.0):
    return {'demo_session_id': session, 'demo_starting_balance_usd': starting,
            'demo_balance_usd': starting + sum(t['pnl_usd'] for t in trades),
            'positions': [], 'history': list(reversed(trades))}


def lab_trade(book, n, pnl_usd, *, notional=50.0, mint='LMINT', pool='LPOOL', opened=None, hold_ms=40_000,
              exit_reason='STOP_LOSS_3_NET', policy='LAB_POLICY_V1', move_pct=-1.0, partial=False):
    opened = START + n * MINUTE if opened is None else opened
    entry_price = 0.02
    exit_price = entry_price * (1 + move_pct / 100)
    row = {'trade_no': n, 'strategy_id': book, 'address': mint, 'pairAddress': pool,
           'opened_at': opened, 'closed_at': opened + hold_ms, 'exit_reason': exit_reason,
           'entry_policy_version': policy, 'notional_usd': notional, 'pnl_usd': pnl_usd,
           'pnl_pct': pnl_usd / notional * 100, 'entry_price': entry_price, 'exit_price': exit_price,
           'quantity': notional / entry_price, 'original_quantity': notional / entry_price,
           'entry_roundtrip_pnl_pct': -2.5, 'entry_dex_fee_usd': 0.3, 'entry_network_fee_usd': 0.01,
           'exit_dex_fee_usd': 0.3, 'exit_network_fee_usd': 0.01, 'exit_price_impact_pct': 0.1,
           'exit_slippage_pct': 0.2, 'remaining_cost_basis_usd': notional + 0.01,
           'final_leg_pnl_usd': pnl_usd, 'remaining_fraction': 1.0, 'partial_exits': []}
    if partial:
        row['remaining_fraction'] = 0.5
        row['quantity'] = row['quantity'] / 2
        row['partial_exits'] = [{'fraction_of_original': 0.5, 'move_pct': 4.0, 'dex_fee_usd': 0.15,
                                 'network_fee_usd': 0.01, 'pnl_usd': 1.0}]
        row['final_leg_pnl_usd'] = pnl_usd - 1.0
    return row


def lab_state(books):
    return {'started_at': START, 'updated_at': START + 6 * DAY,
            'books': {book_id: {'id': book_id, 'starting_balance': 500.0, 'portfolio_group': group,
                                'history': list(reversed(rows))}
                      for book_id, (group, rows) in books.items()}}


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding='utf-8')
    return path


class CollectorTests(unittest.TestCase):
    def test_duplicate_copies_count_once_and_conflicts_are_excluded(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            trades = [engine_trade('S1', n, -3.0) for n in range(5)]
            live = write(root / 'users' / UUID / 'state.json', engine_state('S1', trades[3:]))
            archive = write(root / 'users' / UUID / 'archive' / 'reset-1' / 'state.json',
                            engine_state('S1', trades))
            conflicting = copy.deepcopy(trades[0])
            conflicting['pnl_usd'] = 99.0
            other = write(root / 'users' / UUID / 'archive' / 'reset-2' / 'state.json',
                          engine_state('S1', [conflicting]))
            collector = edge.LedgerCollector()
            for path in (live, archive, other):
                collector.add_engine_ledger(path)
            unique = collector.unique_trades()
            self.assertEqual(len(unique), 4)
            self.assertEqual(collector.duplicates, 3)
            self.assertEqual(collector.conflicts, {trades[0]['id']})
            self.assertEqual({t['account'] for t in unique}, {'user_3aa07b45'})
            report = edge.build_report(collector, iterations=50, seed=1)
            serialized = json.dumps(report) + edge.render_markdown(report)
            self.assertNotIn(UUID, serialized)
            self.assertEqual(report['dedupe']['conflicting_ids_excluded'], 1)
            self.assertEqual(report['engine']['by_account']['user_3aa07b45']['closed_trades'], 4)

    def test_open_or_malformed_rows_are_rejected_not_counted(self):
        with tempfile.TemporaryDirectory() as tmp:
            good = engine_trade('S1', 1, -1.0)
            pending = engine_trade('S1', 2, -1.0, exit_state='PENDING_EXIT')
            unclosed = dict(engine_trade('S1', 3, 5.0), closed_at=None)
            nan = dict(engine_trade('S1', 4, -1.0), pnl_usd=float('nan'))
            data = engine_state('S1', [good, pending, unclosed])
            data['history'] += [nan, 'junk']
            path = write(Path(tmp) / 'state.json', data)
            collector = edge.LedgerCollector()
            collector.add_engine_ledger(path)
            self.assertEqual(len(collector.unique_trades()), 1)
            self.assertEqual(collector.rejected, {'invalid_closed_record': 2, 'not_closed': 1, 'malformed': 1})

    def test_label_syntax_and_uuid_truncation(self):
        label, path = edge.parse_labelled_path(f'{UUID}=C:/x/state.json')
        self.assertEqual(label, 'user_3aa07b45')
        self.assertEqual(path, Path('C:/x/state.json'))
        label, path = edge.parse_labelled_path('C:/x/state.json')
        self.assertIsNone(label)
        self.assertEqual(edge.derive_account_label(Path('/a/users') / UUID / 'archive' / 'r' / 'state.json'),
                         'user_3aa07b45')
        self.assertEqual(edge.derive_account_label(Path('/a/archive/r/state.json')), 'main')


class MeasurementTests(unittest.TestCase):
    def test_profit_factor_null_below_ten_losses(self):
        nine = [engine_trade('S', n, -2.0) for n in range(9)] + [engine_trade('S', 9, 4.0)]
        stats = edge.measure(edge_rows(nine), starting_balance=1000, iterations=50, seed=1)
        self.assertIsNone(stats['profit_factor'])
        self.assertIn('fewer than 10 losses', stats['profit_factor_status'])
        ten = nine + [engine_trade('S', 10, -2.0)]
        stats = edge.measure(edge_rows(ten), starting_balance=1000, iterations=50, seed=1)
        self.assertEqual(stats['profit_factor'], round(4.0 / 20.0, 6))
        self.assertEqual(stats['profit_factor_status'], 'defined')
        no_loss = edge.measure(edge_rows([engine_trade('S', 1, 1.0)]), starting_balance=None, iterations=50, seed=1)
        self.assertIsNone(no_loss['profit_factor'])
        self.assertEqual(no_loss['profit_factor_status'], 'insufficient data: no losses')

    def test_net_basis_counts_expectancy_stress_and_drawdown(self):
        rows = [engine_trade('S', 0, 10.0), engine_trade('S', 1, -4.0), engine_trade('S', 2, 0.0),
                engine_trade('S', 3, -6.0, hold_ms=90_000)]
        stats = edge.measure(edge_rows(rows), starting_balance=1000, iterations=50, seed=1)
        self.assertEqual((stats['wins'], stats['losses'], stats['breakeven']), (1, 2, 1))
        self.assertEqual(stats['avg_net_win_usd'], 10.0)
        self.assertEqual(stats['avg_net_loss_usd'], -5.0)
        self.assertEqual(stats['expectancy_usd_per_trade'], 0.0)
        self.assertEqual(stats['expectancy_pct_per_trade'], 0.0)
        self.assertEqual(stats['net_pnl_usd'], 0.0)
        # +50 bps per leg on a $100 notional costs $1 per trade and 1 percentage point.
        self.assertEqual(stats['cost_stress_plus_50bps_per_leg']['expectancy_usd_per_trade'], -1.0)
        self.assertEqual(stats['cost_stress_plus_50bps_per_leg']['expectancy_pct_per_trade'], -1.0)
        # Equity path 10, 6, 6, 0 -> peak 10, trough 0.
        self.assertEqual(stats['drawdown']['max_drawdown_usd'], 10.0)
        self.assertEqual(stats['drawdown']['max_drawdown_pct_of_peak'], round(10 / 1010 * 100, 6))
        self.assertEqual(stats['median_hold_s'], 30.0)
        self.assertEqual(stats['distinct_mints'], 1)
        self.assertEqual(stats['distinct_mint_pool_hour_clusters'], 1)
        self.assertEqual(stats['evidence_status'], 'insufficient data')
        self.assertEqual(len(stats['evidence_missing']), 3)
        self.assertEqual(stats['expectancy_usd_ci95_cluster_bootstrap']['status'], 'insufficient data')
        self.assertEqual(stats['period']['calendar_days'], 1)
        self.assertEqual(stats['trades_per_hour'], 4.0)

    def test_sufficient_sample_is_descriptive_only_and_bootstrap_is_deterministic(self):
        rows = []
        for n in range(40):
            opened = START + (n % 5) * DAY + (n // 5) * HOUR
            rows.append(engine_trade('S', n, 2.0 if n % 2 else -1.0, mint=f'M{n % 8}', opened=opened))
        stats = edge.measure(edge_rows(rows), starting_balance=1000, iterations=200, seed=7)
        self.assertEqual(stats['evidence_status'], 'descriptive sample (no edge claim)')
        self.assertEqual(stats['evidence_missing'], [])
        self.assertEqual(stats['distinct_mint_pool_hour_clusters'], 40)
        self.assertEqual(stats['period']['calendar_days'], 5)
        ci = stats['expectancy_usd_ci95_cluster_bootstrap']
        self.assertEqual(ci['status'], 'computed')
        self.assertLessEqual(ci['ci95'][0], stats['expectancy_usd_per_trade'])
        self.assertGreaterEqual(ci['ci95'][1], stats['expectancy_usd_per_trade'])
        again = edge.measure(edge_rows(rows), starting_balance=1000, iterations=200, seed=7)
        self.assertEqual(again['expectancy_usd_ci95_cluster_bootstrap'], ci)
        self.assertNotIn('profitable', json.dumps(stats).lower())

    def test_cost_decomposition_and_cost_only_share(self):
        cost_only = engine_trade('S', 0, -2.0, entry_rt_pct=-2.1)       # net - entry RT = +0.1 -> cost only
        real_loss = engine_trade('S', 1, -8.0, entry_rt_pct=-2.0, move_pct=-6.0)
        stats = edge.measure(edge_rows([cost_only, real_loss]), starting_balance=None, iterations=10, seed=1)
        costs = stats['costs']
        self.assertEqual(costs['decomposable_trades'], 2)
        self.assertEqual(costs['gross_mark_move_usd'], -6.0)
        self.assertEqual(costs['total_modeled_cost_usd'], 4.0)
        self.assertEqual(costs['entry_roundtrip_quote_cost_usd'], 4.1)
        self.assertEqual(costs['exit_fees_usd'], 0.06)
        self.assertEqual(costs['entry_fixed_fees_usd'], 0.06)
        self.assertEqual(costs['cost_only_losses'], 1)
        self.assertEqual(costs['cost_only_loss_share_pct'], 50.0)
        partial = engine_trade('S', 2, -1.0)
        partial['partial_fills'] = [{'leg': 1}, {'leg': 2}]
        stats = edge.measure(edge_rows([cost_only, partial]), starting_balance=None, iterations=10, seed=1)
        self.assertEqual(stats['costs']['excluded_partial_or_unmarked'], 1)


def edge_rows(rows):
    return [edge._engine_trade(row, 'main', 'state.json') for row in rows]


class LabAndCliTests(unittest.TestCase):
    def test_lab_books_are_deduped_and_measured_separately(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            alpha = [lab_trade('ALPHA', n, -1.5) for n in range(12)] + [lab_trade('ALPHA', 12, 6.0, exit_reason='TAKE_PROFIT_10_NET', move_pct=14.0)]
            beta = [lab_trade('BETA', n, 2.0, exit_reason='TAKE_PROFIT_10_NET', partial=True) for n in range(3)]
            live = write(root / 'strategy_lab.json', lab_state({'ALPHA': ('TEST', alpha[8:]), 'BETA': ('PROMOTED_PAPER', beta)}))
            archive = write(root / 'archive' / 'reset-1' / 'strategy_lab.json',
                            lab_state({'ALPHA': ('TEST', alpha), 'BETA': ('PROMOTED_PAPER', beta[:1])}))
            collector = edge.LedgerCollector()
            collector.add_lab_ledger(live)
            collector.add_lab_ledger(archive)
            report = edge.build_report(collector, iterations=20, seed=3)
            self.assertEqual(report['lab']['unique_closed_trades'], 16)
            self.assertEqual(collector.duplicates, 6)
            books = report['lab']['by_book']
            self.assertEqual(books['ALPHA']['closed_trades'], 13)
            self.assertEqual(books['ALPHA']['profit_factor'], round(6.0 / 18.0, 6))
            self.assertEqual(books['ALPHA']['drawdown']['max_drawdown_pct_of_peak'], round(18 / 500 * 100, 6))
            self.assertEqual(books['BETA']['closed_trades'], 3)
            self.assertEqual(books['BETA']['costs']['exit_fees_usd'], round(3 * (0.31 + 0.16), 6))
            self.assertEqual(set(report['lab']['by_exit_reason']), {'STOP_LOSS_3_NET', 'TAKE_PROFIT_10_NET'})
            self.assertEqual(report['lab']['by_exit_reason']['TAKE_PROFIT_10_NET']['wins'], 4)
            self.assertEqual(list(report['lab']['by_policy_version']), ['LAB:TEST | entry=LAB_POLICY_V1',
                                                                        'LAB:PROMOTED_PAPER | entry=LAB_POLICY_V1'])
            self.assertEqual(report['engine']['by_account'], {})
            self.assertIn('insufficient data', edge.render_markdown(report))

    def test_archive_dir_window_and_read_only_outputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            trades = [engine_trade('S1', n, -1.0, opened=START + n * DAY) for n in range(6)]
            state = write(root / 'backup' / 'state.json', engine_state('S1', trades))
            lab = write(root / 'backup' / 'archive' / 'reset-9' / 'strategy_lab.json',
                        lab_state({'ALPHA': ('TEST', [lab_trade('ALPHA', 1, -1.0, opened=START + 2 * DAY)])}))
            user = write(root / 'backup' / 'users' / UUID / 'state.json',
                         engine_state('S2', [engine_trade('S2', 0, 3.0, opened=START + 3 * DAY)]))
            before = {path: path.read_bytes() for path in (state, lab, user)}
            output = root / 'reports' / 'edge.json'
            argv = ['--archive-dir', str(root / 'backup'), '--since', edge.iso(START + DAY),
                    '--until', str(START + 4 * DAY + HOUR), '--output', str(output), '--bootstrap', '20', '--quiet']
            with patch('sys.stderr'):
                self.assertEqual(edge.main(argv), 0)
            self.assertEqual({path: path.read_bytes() for path in (state, lab, user)}, before)
            report = json.loads((root / 'reports' / 'edge.json').read_text(encoding='utf-8'))
            markdown = (root / 'reports' / 'edge.md').read_text(encoding='utf-8')
            self.assertEqual(report['window']['filtered_out_by_window'], 2)
            self.assertEqual(report['engine']['unique_closed_trades'], 5)
            self.assertEqual(report['lab']['unique_closed_trades'], 1)
            self.assertEqual(set(report['engine']['by_account']), {'main', 'user_3aa07b45'})
            self.assertFalse(report['profitability_claim'])
            self.assertIn('not a profitability claim', markdown)
            self.assertIn('insufficient data', markdown)
            self.assertNotIn(UUID, markdown)
            self.assertNotIn('profitable', markdown.lower())
            # Output may never land on or inside an input.
            with patch('sys.stderr'), self.assertRaises(SystemExit):
                edge.main(['--state', str(state), '--output', str(state), '--quiet'])
            with patch('sys.stderr'), self.assertRaises(SystemExit):
                edge.main(['--archive-dir', str(root / 'backup'), '--output', str(root / 'backup' / 'edge'), '--quiet'])
            self.assertEqual({path: path.read_bytes() for path in (state, lab, user)}, before)

    def test_cli_refuses_missing_inputs_and_bad_windows(self):
        with patch('sys.stderr'):
            with self.assertRaises(SystemExit):
                edge.main(['--quiet'])
            with self.assertRaises(SystemExit):
                edge.main(['--state', 'does-not-exist.json', '--quiet'])
            with self.assertRaises(SystemExit):
                edge.main(['--state', 'x.json', '--since', '2026-10-09', '--until', '2026-10-08', '--quiet'])
        self.assertEqual(edge.parse_stamp('2026-10-08T00:00:00Z'), 1791417600000)
        self.assertEqual(edge.parse_stamp('1791417600000'), 1791417600000)
        with self.assertRaises(ValueError):
            edge.parse_stamp('yesterday')


if __name__ == '__main__':
    unittest.main()
