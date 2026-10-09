"""Synthetic-ledger tests for the read-only PAPER edge report. No real ledgers, no network."""
import copy
import importlib.util
import io
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


def hf_close(book, seq, net50_pct, *, pool='HFPOOL1', fee_bps=30.0, cfg='c' * 64, at=None):
    """A LAB_HIGH_FREQUENCY_V1 journal close row (book, config hash and journal seq are its identity)."""
    at = START + seq * MINUTE if at is None else at
    pnl = round(25 * (net50_pct + 1.0) / 100, 6)
    return {'v': 1, 'seq': seq, 'kind': 'close', 'book': book, 'cfg': cfg, 'budget': 'b' * 64, 'at': at,
            'entry_policy_version': 'LAB_HIGH_FREQUENCY_V1', 'trade_no': seq, 'address': 'HFMINT', 'pairAddress': pool,
            'decision_at': at - 150_000, 'entry_fill_price': 1.0, 'exit_fill_price': 0.98, 'close_kind': 'HF_TIME_120',
            'pnl_usd': pnl, 'pnl_pct': round(pnl / 25 * 100, 6), 'net50_usd': round(25 * net50_pct / 100, 6),
            'net50_pct': net50_pct, 'fee_bps': fee_bps, 'balance_after': 1000 + pnl, 'closed_at': at}


def write_hf_journal(root, rows):
    for row in rows:
        path = root / 'journal' / row['book'] / '2026-10-10.jsonl'
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('a', encoding='utf-8') as handle:
            handle.write(json.dumps(row) + '\n')


class HighFrequencyJournalTests(unittest.TestCase):
    def test_hf_closes_are_lab_trades_deduped_by_book_config_and_seq_with_fee_bucket_gaps(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'strategy_lab_hf'
            rows = []
            for seq in range(1, 31):
                pool = f'HFPOOL{seq % 5}'
                fee = 30.0 if seq % 2 else 70.0
                rows.append(hf_close('HF_RND_E95', seq, -3.7, pool=pool, fee_bps=fee))
                rows.append(hf_close('HF_QUIET_E95', 100 + seq, -3.5 if fee == 30.0 else -3.9, pool=pool,
                                     fee_bps=fee, at=START + seq * MINUTE))
            write_hf_journal(root, rows + rows[:4])             # a copied tail: duplicates
            write_hf_journal(root / 'archive' / 'reset-1', [hf_close('HF_RND_E95', 999, 50.0)])
            with (root / 'journal' / 'HF_RND_E95' / '2026-10-10.jsonl').open('a', encoding='utf-8') as handle:
                handle.write('{"v":1,"seq"\n')
            collector = edge.LedgerCollector()
            collector.add_hf_journal(root)
            report = edge.build_report(collector, iterations=50, seed=3)
            self.assertEqual(report['lab']['unique_closed_trades'], 60)
            self.assertEqual(collector.duplicates, 4)
            self.assertEqual(collector.rejected['malformed'], 1)
            self.assertEqual(report['lab']['by_book']['HF_RND_E95']['closed_trades'], 30)
            hf = report['high_frequency']
            self.assertEqual(hf['unique_closed_trades'], 60)
            gap = hf['gaps']['HF_QUIET_E95']
            self.assertEqual(gap['control'], 'HF_RND_E95')
            self.assertAlmostEqual(gap['gap_net50_pct'], 0.0, places=6)
            self.assertAlmostEqual(gap['within_fee_bucket']['fee<=50']['gap_net50_pct'], 0.2, places=6)
            self.assertAlmostEqual(gap['within_fee_bucket']['fee55-95']['gap_net50_pct'], -0.2, places=6)
            self.assertEqual(len(gap['gap_ci95_pair_bootstrap']), 2)
            self.assertIn('High-frequency books', edge.render_markdown(report))
            self.assertEqual(collector.sources[0]['kind'], 'lab_hf')

    def test_cli_reads_a_lab_hf_copy_and_refuses_a_missing_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'copy' / 'strategy_lab_hf'
            write_hf_journal(root, [hf_close('HF_RND_E95', seq, -3.7) for seq in range(1, 4)])
            out = Path(tmp) / 'reports' / 'edge'
            with patch('sys.stdout', new_callable=io.StringIO), patch('sys.stderr', new_callable=io.StringIO):
                self.assertEqual(edge.main(['--lab-hf', str(root), '--output', str(out), '--bootstrap', '20']), 0)
            report = json.loads(out.with_name('edge.json').read_text(encoding='utf-8'))
            self.assertEqual(report['lab']['by_book']['HF_RND_E95']['closed_trades'], 3)
            with patch('sys.stderr', new_callable=io.StringIO), self.assertRaises(SystemExit):
                edge.main(['--lab-hf', str(Path(tmp) / 'absent')])

    def test_archive_dir_skips_hf_checkpoints_and_still_reads_the_ledgers(self):
        # Review finding: reset-all archives strategy_lab_hf/ (with its state.json checkpoint) next to
        # the engine ledgers, and an HF Lab reset archives it inside strategy_lab_hf/archive/. The
        # documented --archive-dir scan sent those checkpoints to the engine reader and exited 2.
        checkpoint = {'version': 'HF_CHECKPOINT_V1', 'family': 'LAB_HIGH_FREQUENCY_V1', 'seq': 7,
                      'books': {'HF_RND_E95': {'start_balance': 1000.0, 'balance': 999.3}}}
        with tempfile.TemporaryDirectory() as tmp:
            backup = Path(tmp) / 'ledgers' / 'archive'
            write(backup / 'reset-1' / 'state.json', engine_state('S1', [engine_trade('S1', 1, -1.0)]))
            hf_root = backup / 'reset-2' / 'strategy_lab_hf'
            write(hf_root / 'state.json', checkpoint)
            write_hf_journal(hf_root, [hf_close('HF_RND_E95', 1, -3.7)])
            write(hf_root / 'archive' / 'reset-9-abcd' / 'state.json', checkpoint)
            # A checkpoint copied out of its directory is still recognised by its version.
            write(backup / 'loose' / 'state.json', checkpoint)
            output = Path(tmp) / 'out' / 'edge'
            code, err = run_cli(['--archive-dir', f'main={backup}', '--output', str(output), '--bootstrap', '5',
                                 '--quiet'])
            self.assertEqual(code, 0, err)
            self.assertIn('skipped 3 LAB_HIGH_FREQUENCY_V1 checkpoint(s)', err)
            self.assertIn('--lab-hf', err)
            report = json.loads(output.with_name('edge.json').read_text(encoding='utf-8'))
            self.assertEqual(report['engine']['by_account']['main']['closed_trades'], 1)
            self.assertEqual(report['lab']['unique_closed_trades'], 0)
            self.assertEqual(sorted(item['file'] for item in report['skipped_inputs']),
                             ['loose/state.json', 'reset-2/strategy_lab_hf/archive/reset-9-abcd/state.json',
                              'reset-2/strategy_lab_hf/state.json'])
            self.assertIn('Skipped reset-2/strategy_lab_hf/state.json',
                          output.with_name('edge.md').read_text(encoding='utf-8'))
            # The HF journal of that archive is read with --lab-hf, as the note says.
            code, err = run_cli(['--archive-dir', f'main={backup}', '--lab-hf', str(hf_root), '--quiet'])
            self.assertEqual(code, 0, err)
            # Anything else that is not an engine ledger still fails the scan.
            write(backup / 'other' / 'state.json', {'version': 'SOMETHING_ELSE'})
            code, err = run_cli(['--archive-dir', f'main={backup}', '--quiet'])
            self.assertEqual(code, 2)
            self.assertIn('not an engine ledger', err)


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


SESSION_DIR = '94cc15b2-1111-4222-8333-944455556666'   # a UUID-named scratch directory, like the research copies
OTHER_UUID = '42d3192d-5678-4def-8abc-0123456789cd'


def run_cli(argv):
    """Run main() capturing stderr; returns (exit code, stderr text)."""
    err = io.StringIO()
    with patch('sys.stderr', err):
        try:
            code = edge.main(argv)
        except SystemExit as exc:
            code = exc.code
    return code, err.getvalue()


class AccountLabelTests(unittest.TestCase):
    def test_uuid_named_ancestor_is_not_an_account(self):
        research = Path('/scratch') / SESSION_DIR / 'research' / 'exit-audit' / 'ledgers'
        for name in ('main_state.json', 'user_3aa07b45_state.json', 'user_42d3192d_state.json'):
            self.assertEqual(edge.derive_account_label(research / name), 'main')
        # Only the component directly under the last 'users' directory names an account, and it must be a pure UUID.
        self.assertEqual(edge.derive_account_label(Path('/scratch') / SESSION_DIR / 'users' / UUID / 'state.json'),
                         'user_3aa07b45')
        self.assertEqual(edge.derive_account_label(Path('/b/users') / OTHER_UUID / 'archive' / SESSION_DIR / 'state.json'),
                         'user_42d3192d')
        self.assertIsNone(edge.derive_account_label(Path('/b/users/3aa07b4512345678abcd/state.json')))
        self.assertIsNone(edge.derive_account_label(Path('/b/users/state.json')))
        self.assertIsNone(edge.derive_account_label(Path('/b/users') / (UUID + 'x') / 'state.json'))
        self.assertEqual(edge.derive_account_label(Path('/b/Users/someone/backup/state.json')), 'main')

    def research_copies(self, root):
        ledgers = root / SESSION_DIR / 'research' / 'exit-audit' / 'ledgers'
        main_archive = write(ledgers / 'archive_code-repair-1_state.json',
                             engine_state('20261006-210327', [engine_trade('20261006-210327', n, -1.0) for n in range(3)]))
        user_archive = write(ledgers / 'archive_code-repair-1_user_3aa07b45_state.json',
                             engine_state('USER-3aa07b45-607879', [engine_trade('USER-3aa07b45-607879', n, 2.0) for n in range(3)]))
        main_live = write(ledgers / 'main_state.json',
                          engine_state('PAPER-RESET-1791362408708-ce67', [engine_trade('PAPER-RESET-1791362408708-ce67', 1, 1.0)]))
        user_live = write(ledgers / 'user_42d3192d_state.json',
                          engine_state('PAPER-RESET-1791362427723-36b0', [engine_trade('PAPER-RESET-1791362427723-36b0', 1, 4.0)]))
        return main_archive, user_archive, main_live, user_live

    def test_research_copies_without_labels_are_refused_not_pooled(self):
        with tempfile.TemporaryDirectory() as tmp:
            main_archive, user_archive, main_live, user_live = self.research_copies(Path(tmp))
            # A USER-<8> session found outside users/<uuid>/ cannot silently become 'main'.
            collector = edge.LedgerCollector()
            with self.assertRaisesRegex(ValueError, 'names "user_3aa07b45"'):
                collector.add_engine_ledger(user_archive)
            # Two reset-era ledgers of different accounts both fall back to 'main' with different sessions.
            collector = edge.LedgerCollector()
            collector.add_engine_ledger(main_live)
            with self.assertRaisesRegex(ValueError, 'explicit label'):
                collector.add_engine_ledger(user_live)
            code, err = run_cli(['--state', str(main_archive), '--state', str(main_live), '--quiet'])
            self.assertEqual(code, 2)
            self.assertIn('label', err)
            self.assertNotIn(SESSION_DIR, err)   # UUID path components are shortened in messages
            self.assertIn(SESSION_DIR[:8] + '...', err)

    def test_explicit_labels_keep_research_accounts_separate(self):
        with tempfile.TemporaryDirectory() as tmp:
            main_archive, user_archive, main_live, user_live = self.research_copies(Path(tmp))
            collector = edge.LedgerCollector()
            collector.add_engine_ledger(main_archive, 'main')
            collector.add_engine_ledger(main_live, 'main')
            collector.add_engine_ledger(user_archive, 'user_3aa07b45')
            collector.add_engine_ledger(user_live, 'user_42d3192d')
            report = edge.build_report(collector, iterations=10, seed=1)
            by_account = report['engine']['by_account']
            self.assertEqual({name: row['closed_trades'] for name, row in by_account.items()},
                             {'main': 4, 'user_3aa07b45': 3, 'user_42d3192d': 1})
            self.assertEqual(by_account['main']['drawdown']['segments'], 2)
            pooled = report['engine']['by_exit_reason']['STOP_LOSS_NET_TARGET']
            self.assertEqual(pooled['account_count'], 3)
            self.assertEqual(pooled['accounts'], ['main', 'user_3aa07b45', 'user_42d3192d'])
            self.assertIn('cross-account aggregates', report['engine']['note'])
            markdown = edge.render_markdown(report)
            self.assertIn('3: main, user_3aa07b45, user_42d3192d', markdown)
            self.assertIn('pooled across the listed accounts', markdown)
            self.assertNotIn(SESSION_DIR, json.dumps(report) + markdown)

    def test_users_dir_without_uuid_and_archive_dir_labels(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write(root / 'bad' / 'users' / 'someone' / 'state.json', engine_state('S1', [engine_trade('S1', 1, 1.0)]))
            code, err = run_cli(['--archive-dir', str(root / 'bad'), '--quiet'])
            self.assertEqual(code, 2)
            self.assertIn('not an account UUID', err)
            # Several main sessions plus a user tree: unlabelled is refused, LABEL=DIR is accepted and the
            # users/<uuid>/ ledgers keep their own account (archives of one user may span sessions).
            backup = root / 'backup'
            write(backup / 'archive' / 'reset-1' / 'state.json', engine_state('S1', [engine_trade('S1', 1, -1.0)]))
            write(backup / 'state.json', engine_state('S2', [engine_trade('S2', 1, -2.0)]))
            write(backup / 'users' / UUID / 'state.json', engine_state('U2', [engine_trade('U2', 1, 3.0)]))
            write(backup / 'users' / UUID / 'archive' / 'reset-1' / 'state.json',
                  engine_state('USER-3aa07b45-607879', [engine_trade('USER-3aa07b45-607879', 1, 3.0)]))
            code, err = run_cli(['--archive-dir', str(backup), '--quiet'])
            self.assertEqual(code, 2)
            self.assertIn('both resolve to account "main"', err)
            output = root / 'out' / 'edge'
            code, err = run_cli(['--archive-dir', f'main={backup}', '--output', str(output), '--bootstrap', '5', '--quiet'])
            self.assertEqual(code, 0, err)
            report = json.loads((root / 'out' / 'edge.json').read_text(encoding='utf-8'))
            self.assertEqual({name: row['closed_trades'] for name, row in report['engine']['by_account'].items()},
                             {'main': 2, 'user_3aa07b45': 2})
            # A directory label never overrides a ledger whose session names another user.
            write(backup / 'stray' / 'state.json',
                  engine_state('USER-42d3192d-607879', [engine_trade('USER-42d3192d-607879', 1, 1.0)]))
            code, err = run_cli(['--archive-dir', f'main={backup}', '--quiet'])
            self.assertEqual(code, 2)
            self.assertIn('user_42d3192d', err)


class MainPoolingTests(unittest.TestCase):
    def write(self, root, rel, data):
        path = Path(root) / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data), encoding='utf-8')
        return path

    def test_explicit_main_and_unlabelled_other_session_are_not_pooled(self):
        with tempfile.TemporaryDirectory() as root:
            main = self.write(root, 'a/state.json', engine_state('PAPER-RESET-1-aaaa', [engine_trade('PAPER-RESET-1-aaaa', 1, -1.0)]))
            other = self.write(root, 'b/state.json', engine_state('PAPER-RESET-2-bbbb', [engine_trade('PAPER-RESET-2-bbbb', 1, -2.0)]))
            collector = edge.LedgerCollector()
            collector.add_engine_ledger(main, 'main')
            with self.assertRaisesRegex(ValueError, 'pooled as account'):
                collector.add_engine_ledger(other)
            reverse = edge.LedgerCollector()
            reverse.add_engine_ledger(other)
            with self.assertRaisesRegex(ValueError, 'pooled as account'):
                reverse.add_engine_ledger(main, 'main')

    def test_explicit_main_labels_may_span_sessions(self):
        with tempfile.TemporaryDirectory() as root:
            first = self.write(root, 'a/state.json', engine_state('PAPER-RESET-1-aaaa', [engine_trade('PAPER-RESET-1-aaaa', 1, -1.0)]))
            second = self.write(root, 'b/state.json', engine_state('PAPER-RESET-2-bbbb', [engine_trade('PAPER-RESET-2-bbbb', 1, -2.0)]))
            collector = edge.LedgerCollector()
            collector.add_engine_ledger(first, 'main')
            collector.add_engine_ledger(second, 'main')
            self.assertEqual(len(collector.unique_trades()), 2)

    def test_unlabelled_ledger_without_session_is_refused(self):
        with tempfile.TemporaryDirectory() as root:
            data = engine_state('X', [engine_trade('X', 1, -1.0)])
            data.pop('demo_session_id')
            path = self.write(root, 'a/state.json', data)
            with self.assertRaisesRegex(ValueError, 'no demo_session_id'):
                edge.LedgerCollector().add_engine_ledger(path)


class SegmentedDrawdownTests(unittest.TestCase):
    def test_engine_sessions_are_separate_equity_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = [engine_trade('S1', 0, -300.0), engine_trade('S1', 1, -100.0)]
            second = [engine_trade('S2', 10, -50.0), engine_trade('S2', 11, 20.0), engine_trade('S2', 12, -200.0)]
            collector = edge.LedgerCollector()
            collector.add_engine_ledger(write(root / 'a' / 'state.json', engine_state('S1', first)), 'main')
            collector.add_engine_ledger(write(root / 'b' / 'state.json', engine_state('S2', second)), 'main')
            report = edge.build_report(collector, iterations=10, seed=1)
            dd = report['engine']['by_account']['main']['drawdown']
            # Concatenated, the path would fall 630 from $1,000 (63%); per session the worst is S1: 400 of 1,000.
            self.assertEqual(dd['segments'], 2)
            self.assertEqual(dd['max_drawdown_usd'], 400.0)
            self.assertEqual(dd['max_drawdown_pct_of_peak'], 40.0)
            self.assertEqual(dd['worst_segment'], 'main|S1')
            self.assertEqual(dd['max_drawdown_pct_of_peak_any_segment'], 40.0)
            # S2 alone: peak 0, trough -230 -> 230 of 1,000.
            reason = report['engine']['by_exit_reason']['STOP_LOSS_NET_TARGET']['drawdown']
            self.assertEqual(reason['segments'], 2)
            self.assertEqual(reason['max_drawdown_usd'], 400.0)

    def lab_rows(self, with_trade_no=True):
        rows = []
        balance = 500.0
        for n, pnl in enumerate([-200.0, -150.0, -100.0], start=1):
            balance += pnl
            row = lab_trade('ALPHA', n, pnl)
            row['balance_after'] = balance
            rows.append(row)
        balance = 500.0   # reset: the book restarts at its starting balance
        for n, pnl in enumerate([-100.0, -63.0], start=1):
            balance += pnl
            row = lab_trade('ALPHA', n, pnl, opened=START + DAY + n * MINUTE)
            row['balance_after'] = balance
            if not with_trade_no:
                row['trade_no'] = 100 + n   # still increasing: only the balance restart reveals the reset
            rows.append(row)
        return rows

    def test_lab_reset_segments_by_trade_no_and_by_balance_restart(self):
        for with_trade_no in (True, False):
            with self.subTest(with_trade_no=with_trade_no), tempfile.TemporaryDirectory() as tmp:
                path = write(Path(tmp) / 'strategy_lab.json', lab_state({'ALPHA': ('TEST', self.lab_rows(with_trade_no))}))
                collector = edge.LedgerCollector()
                collector.add_lab_ledger(path)
                report = edge.build_report(collector, iterations=10, seed=1)
                dd = report['lab']['by_book']['ALPHA']['drawdown']
                # Concatenated against one $500 balance this was 613 / 500 = 122.6%.
                self.assertEqual(dd['segments'], 2)
                self.assertEqual(dd['max_drawdown_usd'], 450.0)
                self.assertEqual(dd['max_drawdown_pct_of_peak'], 90.0)
                self.assertEqual(dd['worst_segment'], 'LAB:ALPHA|segment-1')
                self.assertLessEqual(dd['max_drawdown_pct_of_peak_any_segment'], 100.0)
                self.assertIn('(90.00%, 2 seg)', edge.render_markdown(report))

    def test_zero_capital_control_closes_never_enter_net_pnl_or_the_equity_path(self):
        """LAB_FORWARD_CONTROL_CONTINUITY_V1: closes past the funding are counted per trade only."""
        rows = []
        balance = 500.0
        for n in range(1, 9):
            balance -= 45.0
            row = lab_trade('RND_LAB_B', n, -45.0, notional=200.0, opened=START + n * HOUR)
            row.update(balance_after=balance, capital_mode='funded', balance_effect_usd=-45.0)
            rows.append(row)
        for n in range(9, 21):
            row = lab_trade('RND_LAB_B', n, -12.0, notional=200.0, opened=START + n * HOUR)
            row.update(balance_after=balance, capital_mode='zero_capital_control', balance_effect_usd=0.0)
            rows.append(row)
        with tempfile.TemporaryDirectory() as tmp:
            path = write(Path(tmp) / 'strategy_lab.json', lab_state({'RND_LAB_B': ('TEST', rows)}))
            collector = edge.LedgerCollector()
            collector.add_lab_ledger(path)
            book = edge.build_report(collector, iterations=10, seed=1)['lab']['by_book']['RND_LAB_B']
        self.assertEqual(book['closed_trades'], 20)
        self.assertEqual(book['net_pnl_usd'], -360.0, 'the balance fell $360, not $504')
        self.assertEqual((book['zero_capital_closes'], book['zero_capital_pnl_usd']), (12, -144.0))
        self.assertEqual(book['expectancy_usd_per_trade'], round(-504.0 / 20, 6), 'per trade, every close counts')
        self.assertEqual(book['cost_stress_plus_50bps_per_leg']['net_pnl_usd'], round(-360.0 - 8 * 2.0, 6))
        dd = book['drawdown']
        self.assertEqual(dd['segments'], 1, 'a zero-capital close is not a reset')
        self.assertEqual(dd['max_drawdown_usd'], 360.0)
        self.assertEqual(dd['max_drawdown_pct_of_peak'], 72.0)

    def test_lab_window_gap_does_not_split_a_segment(self):
        with tempfile.TemporaryDirectory() as tmp:
            rows = self.lab_rows()[:3]
            path = write(Path(tmp) / 'strategy_lab.json', lab_state({'ALPHA': ('TEST', rows)}))
            collector = edge.LedgerCollector(since=rows[1]['closed_at'])
            collector.add_lab_ledger(path)
            dd = edge.build_report(collector, iterations=10, seed=1)['lab']['by_book']['ALPHA']['drawdown']
            # Starts at the implied balance before trade 2 ($300) and continues as one segment.
            self.assertEqual(dd['segments'], 1)
            self.assertEqual(dd['max_drawdown_usd'], 250.0)
            self.assertEqual(dd['max_drawdown_pct_of_peak'], round(250 / 300 * 100, 6))


class CostIdentityAndRuntimeTests(unittest.TestCase):
    def test_negative_mark_cost_is_flagged_with_implied_gross(self):
        stale = engine_trade('S', 0, 5.0, move_pct=-3.0, exit_reason='STALE_MARKET_EXIT', entry_rt_pct=-2.0)
        stats = edge.measure(edge_rows([stale]), starting_balance=1000, iterations=10, seed=1)
        costs = stats['costs']
        self.assertEqual(costs['total_modeled_cost_usd'], -8.0)
        self.assertFalse(costs['mark_identity_reliable'])
        self.assertTrue(costs['mark_identity_status'].startswith('unreliable'))
        self.assertEqual(costs['stale_exit_trades_in_identity'], 1)
        self.assertEqual(costs['implied_gross_usd'], 7.0)   # net 5 minus entry round trip (-2% of $100)
        self.assertEqual(costs['implied_gross_trades'], 1)
        row = edge._row('S', stats)
        self.assertIn('-8.00 / -3.00 UNRELIABLE', row)
        self.assertIn('| 7.00 |', row)
        self.assertEqual(row.count('|'), edge.TABLE_HEADER.splitlines()[0].count('|'))
        normal = edge.measure(edge_rows([engine_trade('S', 1, -8.0, move_pct=-6.0)]), starting_balance=None,
                              iterations=10, seed=1)
        self.assertTrue(normal['costs']['mark_identity_reliable'])
        self.assertNotIn('UNRELIABLE', edge._row('S', normal))

    def test_inputs_and_outputs_under_a_live_runtime_are_refused_before_reading(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime = root / 'live'
            write(runtime / 'user_accounts.json', {'accounts': {}})
            state = write(runtime / 'users' / UUID / 'state.json', engine_state('S1', [engine_trade('S1', 1, 1.0)]))
            archive = write(runtime / 'archive' / 'reset-1' / 'state.json', engine_state('S0', [engine_trade('S0', 1, 1.0)]))
            copy_dir = root / 'copy'
            copied = write(copy_dir / 'state.json', engine_state('S1', [engine_trade('S1', 1, 1.0)]))
            with patch.object(edge, 'load_json', side_effect=AssertionError('read a runtime file')):
                for argv in (['--state', str(state)], ['--archive-dir', str(runtime / 'archive')],
                             ['--archive-dir', str(root)], ['--lab', str(archive)],
                             ['--state', str(copied), '--output', str(runtime / 'reports' / 'edge')],
                             ['--state', str(copied), '--output', str(root / '.runtime' / 'reports' / 'edge')]):
                    code, err = run_cli(argv + ['--quiet'])
                    self.assertEqual(code, 2, argv)
                    self.assertIn('live runtime', err)
                    self.assertIn('--allow-runtime', err)
                    self.assertNotIn(UUID, err)
            code, err = run_cli(['--state', str(state), '--allow-runtime', '--quiet'])
            self.assertEqual(code, 0, err)
            code, err = run_cli(['--state', str(copied), '--output', str(root / 'reports' / 'edge'), '--quiet'])
            self.assertEqual(code, 0, err)
            self.assertNotIn(UUID, err)


if __name__ == '__main__':
    unittest.main()
