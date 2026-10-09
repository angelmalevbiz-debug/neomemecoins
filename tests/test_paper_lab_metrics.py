import copy
import unittest

from paper_lab_metrics import evaluate_lab, wilson_interval

START = 1_791_000_000_000
DAY = 86_400_000


def trade(n, pnl, day=0):
    return {'address': f'mint-{n}', 'pairAddress': f'pool-{n}',
            'opened_at': START + day * DAY + n * 1000,
            'closed_at': START + day * DAY + n * 1000 + 500,
            'pnl_usd': pnl}


def state(rows, **book_fields):
    return {'started_at': START, 'updated_at': START + 5 * DAY,
            'books': {'RUSH': {'id': 'RUSH', 'starting_balance': 500,
                               'history': rows, **book_fields}}}


class LabMeasurementTests(unittest.TestCase):
    def test_empty_sample_is_unknown_not_zero_percent_evidence(self):
        result = evaluate_lab(state([]))['books']['RUSH']
        self.assertIsNone(result['win_rate_pct'])
        self.assertIsNone(result['win_rate_wilson_95_pct'])
        self.assertEqual(result['evidence_status'], 'INSUFFICIENT_SAMPLE')
        self.assertFalse(result['target_observed_in_sample'])

    def test_eighty_percent_can_lose_money(self):
        rows = [trade(n, 1 if n % 5 else -10, n % 5) for n in range(100)]
        result = evaluate_lab(state(rows))['books']['RUSH']
        self.assertEqual(result['win_rate_pct'], 80)
        self.assertEqual(result['realized_net_pnl_usd'], -120)
        self.assertEqual(result['profit_factor'], .4)
        self.assertEqual(result['evidence_status'], 'TARGET_NOT_MET')
        self.assertFalse(result['target_observed_in_sample'])
        self.assertLess(result['win_rate_wilson_95_pct'][0], 80)

    def test_profitable_target_sample_still_has_lower_interval_and_no_promotion(self):
        rows = [trade(n, 5 if n % 5 else -1, n % 5) for n in range(100)]
        original = state(rows)
        untouched = copy.deepcopy(original)
        report = evaluate_lab(original)
        result = report['books']['RUSH']
        self.assertEqual(result['evidence_status'], 'TARGET_OBSERVED_IN_SAMPLE')
        self.assertFalse(result['win_rate_interval_lower_meets_target'])
        self.assertEqual(result['closed_per_hour'], round(100 / 120, 6))
        self.assertEqual(result['closed_per_24h_normalized'], 20)
        self.assertEqual(original, untouched)

    def test_future_nan_duplicate_and_old_rows_do_not_inflate_win_rate(self):
        good = trade(1, -5)
        future = trade(2, 100, 6)
        old = trade(3, 100)
        old['opened_at'] = START - 1000
        malformed = trade(4, float('nan'))
        result = evaluate_lab(state([good, good, future, old, malformed]))['books']['RUSH']
        self.assertEqual(result['closed_trades'], 1)
        self.assertEqual(result['realized_net_pnl_usd'], -5)
        self.assertEqual(result['rejected_rows'], {'duplicate': 1, 'future': 1,
                                                  'outside_session': 1, 'malformed': 1})
        self.assertEqual(result['evidence_status'], 'INCOMPLETE_LEDGER')

    def test_compact_history_is_detected(self):
        data = state([trade(n, 5) for n in range(30)])
        data['stats'] = {'RUSH': {'trades': 100}}
        result = evaluate_lab(data)['books']['RUSH']
        self.assertTrue(result['history_truncated'])
        self.assertEqual(result['evidence_status'], 'INCOMPLETE_LEDGER')

    def test_open_trade_and_partial_proceeds_do_not_become_additional_wins(self):
        rows = [trade(1, -10)]
        rows[0]['partial_realized_pnl'] = 5
        result = evaluate_lab(state(rows, position={'opened_at': START + DAY,
                                                     'partial_realized_pnl': 50}))['books']['RUSH']
        self.assertEqual(result['opened_trades'], 2)
        self.assertEqual(result['closed_trades'], 1)
        self.assertEqual(result['wins'], 0)
        self.assertEqual(result['realized_net_pnl_usd'], -10)
        self.assertEqual(result['closed_ledger_drawdown_pct'], 2)

    def test_zero_capital_control_closes_never_enter_realized_pnl_or_drawdown(self):
        """LAB_FORWARD_CONTROL_CONTINUITY_V1: a control's closes past its funding never moved the balance."""
        rows = []
        for n in range(8):
            row = trade(n, -45.0)
            row.update(capital_mode='funded', balance_effect_usd=-45.0)
            rows.append(row)
        for n in range(8, 20):
            row = trade(n, -12.0)
            row.update(capital_mode='zero_capital_control', balance_effect_usd=0.0)
            rows.append(row)
        data = state(list(reversed(rows)), balance=140.0)
        data['books']['RUSH']['id'] = 'RND_LAB_B'
        result = evaluate_lab(data)['books']['RUSH']
        # Lab stats: balance $140, realized_pnl -$360 (the review found -504.0 and 100.8%).
        self.assertEqual(result['realized_net_pnl_usd'], -360.0)
        self.assertEqual(result['closed_ledger_drawdown_pct'], 72.0)
        self.assertEqual((result['zero_capital_closes'], result['zero_capital_pnl_usd']), (12, -144.0))
        self.assertEqual((result['closed_trades'], result['funded_closed_trades']), (20, 8))
        self.assertEqual(result['net_expectancy_usd_per_close'], round(-504.0 / 20, 6))
        # A row without a capital mode moves the balance by its P&L, as before.
        plain = evaluate_lab(state([trade(1, -10.0)]))['books']['RUSH']
        self.assertEqual((plain['realized_net_pnl_usd'], plain['zero_capital_closes']), (-10.0, 0))

    def test_new_book_uses_own_creation_window(self):
        result = evaluate_lab(state([trade(1, -5)], created_at=START + 4 * DAY))['books']['RUSH']
        self.assertEqual(result['elapsed_hours'], 24)
        self.assertEqual(result['closed_trades'], 0)

    def test_one_market_day_is_not_sufficient_even_with_many_wins(self):
        result = evaluate_lab(state([trade(n, 1) for n in range(100)]))['books']['RUSH']
        self.assertEqual(result['evidence_status'], 'INSUFFICIENT_SAMPLE')
        self.assertEqual(result['profit_factor_status'], 'no_losses')
        self.assertFalse(result['target_observed_in_sample'])

    def test_current_incomplete_utc_day_cannot_satisfy_day_gate(self):
        data = state([trade(n, 1, n % 5) for n in range(100)])
        data['updated_at'] = START + 4 * DAY + 1_000_000
        result = evaluate_lab(data)['books']['RUSH']
        self.assertEqual(result['closed_trades'], 100)
        self.assertEqual(result['completed_utc_days'], 4)
        self.assertEqual(result['evidence_status'], 'INSUFFICIENT_SAMPLE')

    def test_numeric_string_timestamps_cannot_evade_trade_deduplication(self):
        first = trade(1, 1)
        second = dict(first, opened_at=str(first['opened_at']), closed_at=str(first['closed_at']))
        result = evaluate_lab(state([first, second]))['books']['RUSH']
        self.assertEqual(result['closed_trades'], 1)
        self.assertEqual(result['rejected_rows']['duplicate'], 1)

    def test_invalid_target_or_snapshot_is_refused(self):
        for target in (0, 100, float('nan')):
            with self.assertRaises(ValueError):
                evaluate_lab(state([]), target=target)
        with self.assertRaises(ValueError):
            evaluate_lab({'books': {}})

    def test_wilson_extremes_are_finite_and_bounded(self):
        self.assertEqual(wilson_interval(0, 0), None)
        self.assertEqual(wilson_interval(0, 100)[0], 0)
        self.assertEqual(wilson_interval(100, 100)[1], 100)
        self.assertLess(wilson_interval(100, 100)[0], 100)


class HighFrequencyMeasurementTests(unittest.TestCase):
    """LAB_HIGH_FREQUENCY_V1 journal rows: three slots per book, so orders and closes are counted per row."""

    def rows(self):
        cfg = 'c' * 64
        out = []
        seq = 0

        def row(kind, at, **fields):
            nonlocal seq
            seq += 1
            out.append({'v': 1, 'seq': seq, 'kind': kind, 'book': 'HF_RND_E95', 'cfg': cfg, 'at': at, **fields})
        # Three overlapping slots, then a cancel and two closes.
        for trade_no in (1, 2, 3):
            row('order', START + trade_no * 1000, side='entry', trade_no=trade_no)
        row('fill', START + 5000, side='entry', trade_no=1)
        row('cancel', START + 6000, trade_no=2, reason='hf_entry_no_next_observation')
        row('close', START + 150_000, trade_no=1, pnl_usd=-0.7, net50_usd=-0.9, net0_usd=-0.4,
            close_kind='HF_TIME_120')
        row('close', START + 160_000, trade_no=3, pnl_usd=0.2, net50_usd=-0.05, net0_usd=0.4,
            close_kind='VANISHED')
        row('order', START + 3_600_000, side='entry', trade_no=4)
        return out

    def test_multi_slot_rates_and_dedupe_by_book_config_and_seq(self):
        from paper_lab_metrics import measure_hf_rows
        rows = self.rows()
        report = measure_hf_rows(rows + [dict(rows[0])], as_of=START + 3_600_000)
        book = report['books']['HF_RND_E95:' + 'c' * 12]
        self.assertEqual(report['rejected_rows']['duplicate'], 1)
        self.assertEqual((book['orders'], book['closes'], book['fills']), (4, 2, 1))
        self.assertEqual(book['max_concurrent_slots'], 3)
        self.assertEqual(book['cancels'], {'hf_entry_no_next_observation': 1})
        self.assertEqual(book['close_kinds'], {'HF_TIME_120': 1, 'VANISHED': 1})
        self.assertEqual(book['open_at_end'], 1)
        self.assertAlmostEqual(book['opened_per_hour'], 4 / ((3_600_000 - 1000) / 3_600_000), places=6)
        self.assertAlmostEqual(book['booked_usd'], -0.5)
        self.assertAlmostEqual(book['net50_usd_per_close'], -0.475)
        self.assertEqual(book['win_rate_booked_pct'], 50.0)
        self.assertEqual(book['win_rate_net50_pct'], 0.0)
        self.assertTrue(report['paper_only'] and report['read_only'])

    def test_two_sessions_with_the_same_seq_are_both_measured(self):
        # Review finding: seq and trade_no restart at 1 after an HF reset; keyed by (book, cfg, seq)
        # the second session's rows were dropped as duplicates. The journal epoch keeps them apart.
        from paper_lab_metrics import measure_hf_rows
        first = [dict(row, epoch='a' * 16) for row in self.rows()]
        second = [dict(row, epoch='b' * 16, at=row['at'] + 2 * 3_600_000) for row in self.rows()]
        report = measure_hf_rows(first + second + [dict(second[0])], as_of=START + 6 * 3_600_000)
        book = report['books']['HF_RND_E95:' + 'c' * 12]
        self.assertEqual(report['rejected_rows']['duplicate'], 1)
        self.assertEqual((book['orders'], book['closes'], book['fills']), (8, 4, 2))
        self.assertEqual(book['max_concurrent_slots'], 3)       # per session, never 3 + 3
        self.assertEqual(book['open_at_end'], 2)
        self.assertEqual(book['journal_epochs'], 2)
        self.assertAlmostEqual(book['booked_usd'], -1.0)

    def test_a_line_torn_inside_a_multibyte_symbol_is_one_malformed_line(self):
        import json
        import tempfile
        from pathlib import Path
        from paper_lab_metrics import read_hf_journal
        order = json.dumps({'v': 1, 'seq': 2, 'kind': 'order', 'book': 'HF_RND_E95', 'cfg': 'c', 'at': START,
                            'symbol': 'ПЕПЕ🐸'}, ensure_ascii=False).encode('utf-8')
        torn = order[:order.index('🐸'.encode('utf-8')) + 2]
        good = [json.dumps({'v': 1, 'seq': seq, 'kind': 'order', 'book': 'HF_RND_E95', 'cfg': 'c',
                            'at': START, 'symbol': 'ПЕПЕ'}, ensure_ascii=False).encode('utf-8') for seq in (1, 3)]
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / 'journal' / 'HF_RND_E95'
            folder.mkdir(parents=True)
            (folder / '2026-10-10.jsonl').write_bytes(good[0] + b'\n' + torn + b'\n' + good[1] + b'\n')
            rows, malformed = read_hf_journal(Path(tmp))
        self.assertEqual(malformed, 1)
        self.assertEqual([row['seq'] for row in rows], [1, 3])
        self.assertEqual(rows[1]['symbol'], 'ПЕПЕ')

    def test_malformed_and_future_rows_are_counted_not_measured(self):
        from paper_lab_metrics import measure_hf_rows
        rows = self.rows()
        report = measure_hf_rows(rows + [{'kind': 'close'}, {'v': 1, 'seq': True, 'kind': 'order', 'book': 'X',
                                                             'cfg': 'c', 'at': START}],
                                 as_of=START + 200_000)
        self.assertEqual(report['rejected_rows']['malformed'], 2)
        self.assertEqual(report['rejected_rows']['future'], 1)
        self.assertEqual(report['books']['HF_RND_E95:' + 'c' * 12]['orders'], 3)

    def test_the_journal_reader_skips_archives_and_torn_lines(self):
        import json
        import tempfile
        from pathlib import Path
        from paper_lab_metrics import read_hf_journal
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            live = root / 'journal' / 'HF_RND_E95'
            live.mkdir(parents=True)
            (live / '2026-10-10.jsonl').write_text(
                json.dumps({'v': 1, 'seq': 1, 'kind': 'order', 'book': 'HF_RND_E95', 'cfg': 'c', 'at': START})
                + '\n{"v":1,"seq"\n', encoding='utf-8')
            old = root / 'archive' / 'reset-1' / 'journal' / 'HF_RND_E95'
            old.mkdir(parents=True)
            (old / '2026-10-09.jsonl').write_text('{"v":1,"seq":1}\n', encoding='utf-8')
            rows, malformed = read_hf_journal(root)
        self.assertEqual(len(rows), 1)
        self.assertEqual(malformed, 1)
