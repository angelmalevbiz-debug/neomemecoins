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
