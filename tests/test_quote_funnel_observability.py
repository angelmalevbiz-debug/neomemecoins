"""Quote funnel observability and defer accounting; EXIT_IMPACT_EMERGENCY forensics.

All account/audit paths are isolated. No provider is contacted. The exit rule
and every admission gate are unchanged; only accounting and recorded fields
are asserted here.
"""
import copy
import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

IMPORT_TEMP = tempfile.TemporaryDirectory(prefix='neo-funnel-import-')
for name, filename in [('NEO_MARKET_STATE_PATH', 'state.json'), ('NEO_MARKET_AUDIT_PATH', 'audit.jsonl'),
                       ('NEO_LIVE_TAPE_PATH', 'tape.json')]:
    os.environ[name] = str(Path(IMPORT_TEMP.name) / filename)
import market_monitor as m
import engine_entry_policy as entry_policy
import entry_size_backoff
import promoted_entry_guard as promoted_guard

A, B, C = 'A' * 44, 'B' * 44, 'C' * 44


def audit_events():
    if not m.AUDIT_PATH.exists():
        return []
    return [json.loads(line) for line in m.AUDIT_PATH.read_text(encoding='utf-8').splitlines() if line.strip()]


def sidecar_rows():
    path = m.quote_preparation_log_path()
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]


class FunnelFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='neo-funnel-account-')
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.clock = [1_000_000]
        patches = [patch.object(m, 'STATE_PATH', root / 'state.json'), patch.object(m, 'AUDIT_PATH', root / 'audit.jsonl'),
                   patch.object(m, 'LIVE_TAPE_PATH', root / 'tape.json'),
                   patch.object(m, 'now_ms', side_effect=lambda: self.clock[0]),
                   patch.object(m, 'sol_usd_market_price', return_value=100),
                   patch.object(m.pumpswap_stop, 'prime_positions')]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        m.STATE = m.State()
        self.monitor = m.Monitor()
        self.addCleanup(self.monitor.stop)
        self.coin = {'address': A, 'pairAddress': B, 'symbol': 'FIXTURE', 'priceUsd': 2, 'priceNative': .02,
                     'liquidityUsd': 200000, 'marketCap': 1000000, 'ageMinutes': 60, 'score': 90,
                     'priceChange': {'m5': 5, 'h1': 8}, 'txns': {'m5': {'buys': 10, 'sells': 3}}, 'signals': [],
                     'updatedAt': self.clock[0]}
        self.flow = {'quality': 'COMPLETE', 'trades': 4, 'buy_sell_usd_ratio': 3, 'unique_wallets': 3,
                     'buyer_wallets': 3, 'buy_usd': 300, 'sell_usd': 100, 'max_sell_usd': 50}
        self.flow['verified_flow'] = {'source': promoted_guard.FLOW_SOURCE,
                                     'coverage_status': 'COMPLETE', 'window_ms': promoted_guard.FLOW_WINDOW_MS,
                                     'address': A, 'pairAddress': B, 'window_at': self.clock[0],
                                     'latest_event_at': self.clock[0] - 100, 'available_at': self.clock[0] - 50,
                                     'trades': 4, 'unique_wallets': 3, 'buy_usd': 300, 'sell_usd': 100}
        self.context = {'conviction': 80, 'mode': 'STRONG'}
        self.pos = {'id': 'position-1', 'address': A, 'pairAddress': B, 'session_id': m.STATE.demo_session_id,
                    'entry_price': 2, 'current_price': 2, 'quantity': 100, 'notional_usd': 200, 'original_notional_usd': 200,
                    'capital_committed_usd': 200.23, 'entry_network_fee_usd': .03, 'entry_account_reserve_usd': .2,
                    'entry_liquidity_usd': 200000, 'opened_at': self.clock[0], 'updated_at': self.clock[0],
                    'jupiter_token_raw_amount': 100000000, 'execution_mode': 'JUPITER_QUOTE_V2',
                    'entry_price_impact_pct': 0.85, 'jupiter_entry_quote_at': self.clock[0] - 5000,
                    'entry_roundtrip_pnl_pct': -2.29,
                    'preflight_buy_quote': {'priceImpactPct': '0.0081', 'fixture': True},
                    'preflight_sell_quote': {'priceImpactPct': '0.0118', 'fixture': True},
                    'coin_snapshot': copy.deepcopy(self.coin)}
        self.quote = {'net_proceeds_usd': 195, 'gross_proceeds_usd': 195.03, 'network_fee_usd': .03,
                      'dex_fee_usd': 0, 'fill_price': 1.95, 'impact_pct': 1.5, 'slippage_pct': .1, 'latency_pct': 0,
                      'quoted_at': self.clock[0], 'from_cache': False, 'execution_source': 'OFFLINE_FIXTURE',
                      'route': [{'ammKey': B, 'label': 'PumpSwap'}], 'route_matches_entry_pool': True,
                      'context_slot': 500, 'quote_age_ms': 10, 'raw_quote': {'fixture': True}}

    def fresh(self, coin=None, **overrides):
        """The coin and its verified flow observed at the current fixture clock."""
        now = self.clock[0]
        self.flow['verified_flow'].update(window_at=now, latest_event_at=now - 100, available_at=now - 50)
        return dict(coin or self.coin, updatedAt=now, **overrides)

    def position(self, **overrides):
        m.STATE.positions = [dict(copy.deepcopy(self.pos), **overrides)]
        m.STATE.trade_seq = 1
        return m.STATE.positions[0]

    def entry_patches(self):
        def flow_for(address, _seconds=30, pair_address=None):
            proof = {**self.flow['verified_flow'], 'address': address, 'pairAddress': pair_address}
            return {**self.flow, 'verified_flow': proof}

        def safety_for(coin):
            return {'status': 'pass', 'mint': coin['address'], 'pair': coin['pairAddress'],
                    'checked_at': self.clock[0],
                    'metrics': {'decimals': 6, 'token_account_rent_lamports': 1650000}}
        patches = [patch.object(m.STATE, 'live_flow', side_effect=flow_for),
                   patch.object(self.monitor, 'market_context', return_value=self.context),
                   patch.object(m.rug_guard, 'check', side_effect=safety_for),
                   patch.object(m.price_integrity, 'check', return_value={'status': 'pass'})]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def failing_preparation(self, failure):
        """prepare_entry returns None and exposes the given structured refusal."""
        prepare = patch.object(m.paper_quotes, 'prepare_entry', return_value=None)
        error = patch.object(m.paper_quotes, 'last_preparation_error', return_value=dict(failure))
        mocks = []
        for p in (prepare, error):
            mocks.append(p.start())
            self.addCleanup(p.stop)
        return mocks[0]


class QuoteDeferAccountingTests(FunnelFixture):
    BUSY = {'stage': 'sequence', 'code': 'ENTRY_SEQUENCE_BUSY', 'queue_ms': 0, 'at': 1_000_000}
    EXIT_PRIORITY = {'stage': 'sequence', 'code': 'EXIT_PRIORITY_PENDING', 'queue_ms': 0, 'at': 1_000_000}
    TIMEOUT = {'stage': 'initial_buy', 'code': 'TIMEOUT', 'queue_ms': 12, 'http_ms': 2500, 'at': 1_000_000}
    RATE_LIMITED = {'stage': 'preview_sell', 'code': 'RATE_LIMITED', 'http_status': 429, 'retry_at': 1_000_900,
                    'queue_ms': 5, 'http_ms': 80, 'at': 1_000_000}
    ROUTE = {'stage': 'consistency', 'code': 'PREFLIGHT_QUANTITY_DRIFT', 'quantity_change_pct': 0.9,
             'maximum_quantity_change_pct': .5, 'at': 1_000_000}

    def test_busy_defer_leaves_quote_attempts_unchanged_and_arms_no_cooldown(self):
        self.entry_patches()
        self.failing_preparation(self.BUSY)
        self.monitor.maybe_open([self.coin])
        report = m.STATE.entry_diagnostics
        self.assertEqual(report['quote_attempts'], 0)
        self.assertEqual(report['quote_defers'], 1)
        self.assertEqual(report['quoted'], 1)
        self.assertEqual(report['rejections'].get('quote_inconsistent'), 1)
        self.assertEqual(report['quote_preparation_codes'], {'ENTRY_SEQUENCE_BUSY': 1})
        self.assertEqual(report['quote_preparation_codes_lifetime'], {'ENTRY_SEQUENCE_BUSY': 1})
        self.assertNotIn(A, self.monitor.entry_quote_retry_after)
        self.assertFalse(m.STATE.positions)
        example = report['quote_preparation_failures'][0]
        self.assertEqual((example['code'], example['counted_as_quote_attempt']), ('ENTRY_SEQUENCE_BUSY', False))

    def test_exit_priority_defer_is_also_not_a_quote_attempt(self):
        self.entry_patches()
        self.failing_preparation(self.EXIT_PRIORITY)
        self.monitor.maybe_open([self.coin])
        report = m.STATE.entry_diagnostics
        self.assertEqual((report['quote_attempts'], report['quote_defers']), (0, 1))
        self.assertNotIn(A, self.monitor.entry_quote_retry_after)

    def test_timeout_increments_quote_attempts_and_arms_cooldown(self):
        self.entry_patches()
        self.failing_preparation(self.TIMEOUT)
        self.monitor.maybe_open([self.coin])
        report = m.STATE.entry_diagnostics
        self.assertEqual(report['quote_attempts'], 1)
        self.assertEqual(report['quote_defers'], 0)
        self.assertEqual(report['rejections'].get('quote_inconsistent'), 1)
        self.assertEqual(report['quote_preparation_codes'], {'TIMEOUT': 1})
        self.assertEqual(self.monitor.entry_quote_retry_after[A],
                         self.clock[0] + entry_size_backoff.QUOTE_RETRY_COOLDOWN_MS)
        example = report['quote_preparation_failures'][0]
        self.assertEqual((example['code'], example['counted_as_quote_attempt']), ('TIMEOUT', True))

    def test_rate_limit_and_route_consistency_keep_todays_accounting(self):
        for failure in (self.RATE_LIMITED, self.ROUTE):
            with self.subTest(code=failure['code']):
                self.setUp()
                self.entry_patches()
                self.failing_preparation(failure)
                self.monitor.maybe_open([self.coin])
                report = m.STATE.entry_diagnostics
                self.assertEqual((report['quote_attempts'], report['quote_defers']), (1, 0))
                self.assertIn(A, self.monitor.entry_quote_retry_after)
                self.assertEqual(report['quote_preparation_codes'], {failure['code']: 1})

    def test_defers_do_not_consume_the_per_scan_budget_but_provider_failures_do(self):
        # Base58 letters only (no I/O), otherwise the unchanged invalid_pair gate rejects first.
        coins = [dict(self.coin, address=x * 44, pairAddress=chr(ord(x) + 1) * 44, symbol=f'C{x}')
                 for x in 'ACEGKMP']
        self.assertGreater(len(coins), entry_policy.MAX_QUOTED_CANDIDATES)
        self.entry_patches()
        prepare = self.failing_preparation(self.BUSY)
        self.monitor.maybe_open(coins)
        report = m.STATE.entry_diagnostics
        self.assertEqual(prepare.call_count, len(coins))
        self.assertEqual((report['quote_attempts'], report['quote_defers']), (0, len(coins)))
        self.assertNotIn('quote_budget', report['rejections'])
        self.assertEqual(report['quote_preparation_codes'], {'ENTRY_SEQUENCE_BUSY': len(coins)})

        self.setUp()
        self.entry_patches()
        prepare = self.failing_preparation(self.TIMEOUT)
        self.monitor.maybe_open(coins)
        report = m.STATE.entry_diagnostics
        self.assertEqual(prepare.call_count, entry_policy.MAX_QUOTED_CANDIDATES)
        self.assertEqual(report['quote_attempts'], entry_policy.MAX_QUOTED_CANDIDATES)
        self.assertEqual(report['rejections'].get('quote_budget'), len(coins) - entry_policy.MAX_QUOTED_CANDIDATES)

    def test_successful_preparation_still_counts_one_attempt(self):
        self.entry_patches()

        def prepared(address, pair, notional):
            return ({'token_raw_expected': int(notional / 2 * 1e6), 'token_raw_amount': int(notional / 2 * 1e6),
                     'input_usdc_raw': int(notional * 1e6), 'price_impact_pct': .2, 'quoted_at': self.clock[0],
                     'raw_quote': {'fixture': True}}, {'expected_usdc': notional - 1, 'floor_usdc': notional - 2})
        with patch.object(m.paper_quotes, 'prepare_entry', side_effect=prepared):
            self.monitor.maybe_open([self.coin])
        report = m.STATE.entry_diagnostics
        self.assertEqual(len(m.STATE.positions), 1)
        self.assertEqual((report['quote_attempts'], report['quote_defers'], report['opened']), (1, 0, 1))
        self.assertEqual(report['quote_preparation_codes'], {})

    def test_failure_row_goes_to_the_sidecar_with_code_pool_notional_and_latency(self):
        self.entry_patches()
        self.failing_preparation(self.TIMEOUT)
        self.monitor.maybe_open([self.coin])
        self.assertEqual(m.quote_preparation_log_path().parent, m.AUDIT_PATH.parent)
        self.assertEqual(m.quote_preparation_log_path().name, 'quote_preparation.jsonl')
        self.assertFalse([row for row in audit_events() if row['event'] == 'QUOTE_PREPARATION_FAILED'])
        rows = sidecar_rows()
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row['event'], 'QUOTE_PREPARATION_FAILED')
        self.assertEqual(row['code'], 'TIMEOUT')
        self.assertEqual(row['stage'], 'initial_buy')
        self.assertEqual(row['pool'], m.abbreviate_address(B))
        self.assertNotEqual(row['pool'], B)
        self.assertEqual((row['pair_address'], row['mint']), (B, A))
        self.assertGreater(row['notional_usd'], 0)
        self.assertEqual((row['latency_ms'], row['queue_ms'], row['http_ms']), (0, 12, 2500))
        self.assertEqual((row['deferred'], row['counted_as_quote_attempt']), (False, True))
        self.assertEqual(row['session_id'], m.STATE.demo_session_id)
        self.assertEqual(m.STATE.entry_diagnostics['quote_preparation_log_events'], 1)

    def test_sidecar_append_takes_no_state_lock_and_no_fsync(self):
        self.entry_patches()
        self.failing_preparation(self.TIMEOUT)
        with patch.object(m.os, 'fsync', side_effect=AssertionError('fsync on a diagnostics row')), \
             patch.object(m, 'append_audit', side_effect=AssertionError('audit row per failure')):
            original = m.append_quote_preparation_log

            def guarded(payload):
                # STATE.lock is re-entrant, so probe it from another thread: a free
                # lock there proves the caller does not hold it during the append.
                probe = []

                def try_lock():
                    acquired = m.STATE.lock.acquire(blocking=False)
                    probe.append(acquired)
                    if acquired:
                        m.STATE.lock.release()
                worker = threading.Thread(target=try_lock)
                worker.start()
                worker.join(5)
                self.assertEqual(probe, [True], 'sidecar append ran under STATE.lock')
                return original(payload)
            with patch.object(m, 'append_quote_preparation_log', side_effect=guarded) as appended:
                self.monitor.maybe_open([self.coin])
        self.assertEqual(appended.call_count, 1)
        self.assertNotIn('quote_preparation_log_error', m.STATE.entry_diagnostics)
        self.assertEqual(len(sidecar_rows()), 1)

    def test_n_failures_never_grow_audit_and_the_sidecar_stays_capped(self):
        self.entry_patches()
        self.failing_preparation(self.BUSY)
        m.AUDIT_PATH.parent.mkdir(parents=True, exist_ok=True)
        m.AUDIT_PATH.write_text('{"event": "SEED", "event_id": "seed"}\n', encoding='utf-8')
        audit_before = m.AUDIT_PATH.read_bytes()
        cap = 4096
        scans = 60
        letters = 'ABCDEFGHJKLMNPQRSTUVWXYZ'  # base58: no I or O
        with patch.object(m, 'QUOTE_PREPARATION_LOG_MAX_BYTES', cap):
            for scan in range(scans):
                # A distinct pool each scan defeats the (pair, code) rate limit so the cap is exercised.
                coin = self.fresh(address=letters[scan % len(letters)] * 44,
                                  pairAddress=letters[scan % len(letters)] * 42 + letters[scan // len(letters)] + 'z')
                self.monitor.maybe_open([coin])
                self.clock[0] += 3000
                self.assertLessEqual(m.quote_preparation_log_path().stat().st_size, cap)
        self.assertEqual(m.AUDIT_PATH.read_bytes(), audit_before)
        rows = sidecar_rows()  # every remaining row is whole JSON, newest kept
        self.assertTrue(rows)
        self.assertLess(len(rows), scans)
        self.assertEqual(rows[-1]['checked_at'], self.clock[0] - 3000)
        self.assertFalse(m.quote_preparation_log_path().with_name('quote_preparation.jsonl.tmp').exists())
        report = m.STATE.entry_diagnostics
        self.assertEqual(report['quote_preparation_codes_lifetime'], {'ENTRY_SEQUENCE_BUSY': scans})
        self.assertEqual(report['quote_preparation_codes_rolling'], {'ENTRY_SEQUENCE_BUSY': scans})

    def test_one_row_per_pair_and_code_per_cooldown_while_histograms_count_every_failure(self):
        self.entry_patches()
        self.failing_preparation(self.BUSY)
        failures = 25
        for _ in range(failures):
            self.monitor.maybe_open([self.fresh()])
            self.clock[0] += 1000
        self.assertLess(failures * 1000, entry_size_backoff.QUOTE_RETRY_COOLDOWN_MS * 2)
        expected_rows = 1 + (failures - 1) * 1000 // entry_size_backoff.QUOTE_RETRY_COOLDOWN_MS
        self.assertEqual(len(sidecar_rows()), expected_rows)
        self.assertFalse(audit_events())
        report = m.STATE.entry_diagnostics
        self.assertEqual(report['quote_preparation_codes_lifetime'], {'ENTRY_SEQUENCE_BUSY': failures})
        self.assertEqual(report['quote_preparation_codes_rolling'], {'ENTRY_SEQUENCE_BUSY': failures})
        self.assertEqual(report['quote_preparation_rolling_scans'], failures)

        # A different code on the same pool is a different key and is logged at once.
        m.paper_quotes.last_preparation_error.return_value = dict(self.EXIT_PRIORITY)
        self.monitor.maybe_open([self.fresh()])
        self.assertEqual(sidecar_rows()[-1]['code'], 'EXIT_PRIORITY_PENDING')
        self.assertEqual(len(sidecar_rows()), expected_rows + 1)

        # After the cooldown the same (pair, code) is logged again.
        m.paper_quotes.last_preparation_error.return_value = dict(self.BUSY)
        self.clock[0] += entry_size_backoff.QUOTE_RETRY_COOLDOWN_MS
        self.monitor.maybe_open([self.fresh()])
        self.assertEqual(len(sidecar_rows()), expected_rows + 2)

    def test_sidecar_rows_are_bounded_per_scan_while_the_histogram_is_complete(self):
        letters = 'ABCDEFGHJKLMNPQRSTUVWXYZ'  # base58: no I or O
        coins = [dict(self.coin, address=letters[i] * 44, pairAddress=letters[i] * 43 + 'z', symbol=f'C{i}')
                 for i in range(m.QUOTE_PREPARATION_LOG_PER_SCAN + 3)]
        self.entry_patches()
        self.failing_preparation(self.BUSY)
        self.monitor.maybe_open(coins)
        self.assertEqual(len(sidecar_rows()), m.QUOTE_PREPARATION_LOG_PER_SCAN)
        self.assertFalse(audit_events())
        report = m.STATE.entry_diagnostics
        self.assertEqual(report['quote_preparation_log_skipped'], 3)
        self.assertEqual(report['quote_preparation_codes'], {'ENTRY_SEQUENCE_BUSY': len(coins)})

    def test_rolling_histogram_is_a_time_window(self):
        self.entry_patches()
        self.failing_preparation(self.BUSY)
        self.monitor.maybe_open([self.coin])
        self.clock[0] += m.QUOTE_PREPARATION_ROLLING_WINDOW_MS - 1000
        m.paper_quotes.last_preparation_error.return_value = dict(self.TIMEOUT)
        self.monitor.maybe_open([self.fresh(address=C, pairAddress='D' * 44)])
        report = m.STATE.entry_diagnostics
        self.assertEqual(report['quote_preparation_codes_rolling'], {'ENTRY_SEQUENCE_BUSY': 1, 'TIMEOUT': 1})
        self.assertEqual(report['quote_preparation_rolling_window_minutes'], 60)
        self.clock[0] += 2000  # the first failure is now older than 60 minutes
        self.monitor.maybe_open([])
        report = m.STATE.entry_diagnostics
        self.assertEqual(report['quote_preparation_codes_rolling'], {'TIMEOUT': 1})
        self.assertEqual(report['quote_preparation_rolling_scans'], 1)
        self.assertEqual(report['quote_preparation_codes_lifetime'], {'ENTRY_SEQUENCE_BUSY': 1, 'TIMEOUT': 1})
        self.assertEqual(report['quote_preparation_lifetime_scope'], 'SINCE_ENGINE_START')

    def test_lifetime_and_rolling_histograms_accumulate_across_scans(self):
        self.entry_patches()
        self.failing_preparation(self.BUSY)
        self.monitor.maybe_open([self.coin])
        self.clock[0] += 1000
        m.paper_quotes.last_preparation_error.return_value = dict(self.TIMEOUT)
        self.monitor.maybe_open([dict(self.coin, address=C, pairAddress='D' * 44)])
        self.clock[0] += 1000
        with patch.object(m.paper_quotes, 'prepare_entry', return_value=None):
            m.paper_quotes.last_preparation_error.return_value = dict(self.BUSY)
            self.monitor.maybe_open([])
        report = m.STATE.entry_diagnostics
        self.assertEqual(report['quote_preparation_codes'], {})
        self.assertEqual(report['quote_preparation_codes_rolling'], {'ENTRY_SEQUENCE_BUSY': 1, 'TIMEOUT': 1})
        self.assertEqual(report['quote_preparation_rolling_scans'], 2)
        self.assertEqual(report['quote_preparation_rolling_window_minutes'], 60)
        self.assertEqual(report['quote_preparation_codes_lifetime'], {'ENTRY_SEQUENCE_BUSY': 1, 'TIMEOUT': 1})
        self.assertEqual(report['quote_preparation_lifetime_since'], 1_000_000)
        self.assertEqual(report['quote_preparation_defer_codes'], ['ENTRY_SEQUENCE_BUSY', 'EXIT_PRIORITY_PENDING'])
        self.assertEqual(report['max_quote_attempts_per_scan'], entry_policy.MAX_QUOTED_CANDIDATES)

    def test_histograms_are_bounded_in_distinct_codes(self):
        histogram = {}
        for i in range(m.QUOTE_PREPARATION_MAX_CODES + 5):
            m.Monitor._bounded_code_add(histogram, f'CODE_{i}')
        self.assertEqual(len(histogram), m.QUOTE_PREPARATION_MAX_CODES + 1)
        self.assertEqual(histogram['OTHER'], 5)


class ExitImpactEmergencyForensicsTests(FunnelFixture):
    def closed(self):
        self.assertFalse(m.STATE.positions)
        self.assertEqual(len(m.STATE.history), 1)
        return m.STATE.history[0]

    def test_trigger_quote_entry_preflight_threshold_and_liquidity_are_recorded(self):
        self.position()
        exit_coin = dict(self.coin, liquidityUsd=180000, updatedAt=self.clock[0])
        with patch.object(m.paper_quotes, 'position_mark', return_value=dict(self.quote)):
            self.monitor.update_positions({A: exit_coin})
        trade = self.closed()
        self.assertEqual(trade['exit_reason'], 'EXIT_IMPACT_EMERGENCY')
        self.assertEqual(trade['exit_price_impact_pct'], 1.5)
        record = trade['exit_impact_emergency']
        self.assertEqual(record['version'], m.EXIT_IMPACT_EMERGENCY_FORENSICS_VERSION)
        self.assertFalse(record['rule_changed'])
        self.assertAlmostEqual(record['threshold_pct'], max(m.EXIT_IMPACT_EMERGENCY_PCT, 0.85 + 0.5))
        self.assertEqual(record['exit_impact_emergency_pct'], m.EXIT_IMPACT_EMERGENCY_PCT)
        self.assertEqual(record['entry_margin_pct'], 0.5)
        preflight = record['entry_preflight']
        self.assertEqual(preflight['buy_impact_pct'], 0.85)
        self.assertAlmostEqual(preflight['preflight_buy_impact_pct'], 0.81)
        self.assertAlmostEqual(preflight['preflight_sell_impact_pct'], 1.18)
        self.assertEqual(preflight['buy_quoted_at'], self.clock[0] - 5000)
        trigger = record['trigger_quote']
        self.assertEqual((trigger['impact_pct'], trigger['quoted_at'], trigger['route_pools']), (1.5, self.clock[0], [B]))
        self.assertTrue(trigger['route_matches_entry_pool'])
        self.assertFalse(trigger['from_cache'])
        self.assertNotIn('raw_quote', trigger)
        self.assertIsNone(record['confirming_quote'])
        self.assertEqual(record['booked_quote'], 'trigger')
        self.assertEqual(record['booked_impact_pct'], 1.5)
        self.assertEqual(record['liquidity'], {'entry_usd': 200000, 'exit_usd': 180000,
                                               'exit_to_entry_ratio': 0.9, 'exit_observed_at': self.clock[0]})
        self.assertFalse(record['trigger_is_requote'])

    def test_confirming_forced_requote_is_recorded_when_the_trigger_came_from_cache(self):
        self.position()
        cached = dict(self.quote, from_cache=True, impact_pct=1.4, quoted_at=self.clock[0] - 300)
        forced = dict(self.quote, from_cache=False, impact_pct=1.62, quoted_at=self.clock[0], net_proceeds_usd=194.5,
                      route=[{'ammKey': C, 'label': 'Other'}], route_matches_entry_pool=False)
        with patch.object(m.paper_quotes, 'position_mark', side_effect=[cached, forced]) as mark:
            self.monitor.update_positions({A: self.coin})
        self.assertEqual(mark.call_count, 2)
        self.assertTrue(mark.call_args_list[1].kwargs.get('force'))
        trade = self.closed()
        self.assertEqual(trade['exit_reason'], 'EXIT_IMPACT_EMERGENCY')
        self.assertEqual(trade['exit_price_impact_pct'], 1.62)
        self.assertFalse(trade['exit_route_matches_entry_pool'])
        record = trade['exit_impact_emergency']
        self.assertEqual(record['trigger_quote']['impact_pct'], 1.4)
        self.assertTrue(record['trigger_quote']['from_cache'])
        self.assertEqual(record['confirming_quote']['impact_pct'], 1.62)
        self.assertEqual(record['confirming_quote']['route_pools'], [C])
        self.assertTrue(record['confirming_quote_meets_threshold'])
        self.assertEqual(record['booked_quote'], 'confirming')
        self.assertEqual(record['booked_impact_pct'], 1.62)

    def test_pending_emergency_keeps_the_trigger_until_the_confirming_quote_books(self):
        self.position()
        cached = dict(self.quote, from_cache=True, impact_pct=1.45, quoted_at=self.clock[0] - 200)
        with patch.object(m.paper_quotes, 'position_mark', side_effect=[cached, None]):
            self.monitor.update_positions({A: self.coin})
        self.assertEqual(len(m.STATE.positions), 1)
        pending = m.STATE.positions[0]
        self.assertEqual(pending['pending_exit_reason'], 'EXIT_IMPACT_EMERGENCY')
        self.assertEqual(pending['exit_impact_emergency']['trigger_quote']['impact_pct'], 1.45)
        self.assertEqual(m.State().positions[0]['exit_impact_emergency']['trigger_quote']['impact_pct'], 1.45)
        self.clock[0] += int(pending['next_exit_retry_at'] - self.clock[0]) + 1
        confirming = dict(self.quote, impact_pct=1.3, quoted_at=self.clock[0])
        with patch.object(m.paper_quotes, 'position_mark', return_value=confirming):
            self.monitor.update_positions({A: dict(self.coin, updatedAt=self.clock[0])})
        trade = self.closed()
        record = trade['exit_impact_emergency']
        self.assertEqual(record['trigger_quote']['impact_pct'], 1.45)
        self.assertEqual(record['confirming_quote']['impact_pct'], 1.3)
        # Informational only: the booked re-quote sat below the threshold, the pending exit still executes.
        self.assertFalse(record['confirming_quote_meets_threshold'])
        self.assertEqual(trade['exit_price_impact_pct'], 1.3)

    def test_non_emergency_closes_and_open_positions_carry_no_record(self):
        self.position()
        benign = dict(self.quote, impact_pct=0.5, net_proceeds_usd=230)
        with patch.object(m.paper_quotes, 'position_mark', return_value=benign):
            self.monitor.update_positions({A: self.coin})
        trade = self.closed()
        self.assertEqual(trade['exit_reason'], 'TAKE_PROFIT_10_NET')
        self.assertNotIn('exit_impact_emergency', trade)
        self.position()
        held = dict(self.quote, impact_pct=0.5, net_proceeds_usd=200)
        with patch.object(m.paper_quotes, 'position_mark', return_value=held):
            self.monitor.update_positions({A: self.coin})
        self.assertEqual(len(m.STATE.positions), 1)
        self.assertNotIn('exit_impact_emergency', m.STATE.positions[0])

    def test_compact_public_trade_exposes_the_forensics_without_raw_quotes(self):
        self.position()
        # 170k stays above the unchanged 80% LIQUIDITY_EMERGENCY floor of the 200k entry.
        with patch.object(m.paper_quotes, 'position_mark', return_value=dict(self.quote)):
            self.monitor.update_positions({A: dict(self.coin, liquidityUsd=170000)})
        trade = self.closed()
        public = m.compact_public_trade(trade)
        self.assertEqual(public['exit_reason'], 'EXIT_IMPACT_EMERGENCY')
        self.assertEqual(public['exit_impact_emergency'], trade['exit_impact_emergency'])
        self.assertEqual((public['entry_price_impact_pct'], public['exit_price_impact_pct']), (0.85, 1.5))
        self.assertEqual((public['entry_liquidity_usd'], public['exit_liquidity_usd']), (200000, 170000))
        self.assertTrue(public['exit_route_matches_entry_pool'])
        self.assertNotIn('exit_quote', public)
        self.assertNotIn('preflight_sell_quote', public)
        self.assertNotIn('raw_quote', json.dumps(public['exit_impact_emergency']))
        self.assertEqual(m.STATE.snapshot()['history'][0]['exit_impact_emergency'], trade['exit_impact_emergency'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
