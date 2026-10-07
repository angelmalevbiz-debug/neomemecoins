"""Offline regression tests. Quotes are fixtures; no test calls external APIs."""
import copy
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

_TEMP = tempfile.TemporaryDirectory(prefix='neo-engine-entry-tests-')
os.environ['NEO_MARKET_STATE_PATH'] = str(Path(_TEMP.name) / 'state.json')
os.environ['NEO_MARKET_AUDIT_PATH'] = str(Path(_TEMP.name) / 'audit.jsonl')
os.environ['NEO_LIVE_TAPE_PATH'] = str(Path(_TEMP.name) / 'tape.json')
for key in ['NEO_TRADE_NOTIONAL_USD', 'NEO_MAX_DAILY_LOSS_USD', 'NEO_STRICT_ENTRY_SCORE',
            'NEO_STRICT_MIN_CONVICTION', 'NEO_STRICT_MIN_LIQUIDITY_USD',
            'NEO_STRICT_MAX_ENTRY_IMPACT_PCT', 'NEO_STRICT_MAX_ROUNDTRIP_COST_PCT']:
    os.environ.pop(key, None)
import market_monitor as m
import engine_entry_policy as policy
import winner_ensemble
import promoted_entry_guard as promoted_guard


class EngineEntryTests(unittest.TestCase):
    def setUp(self):
        m.STATE_PATH.unlink(missing_ok=True)
        m.STATE = m.State()
        self.monitor = m.Monitor()
        self.coin = {'address': 'A'*44, 'pairAddress': 'B'*44, 'symbol': 'FIXTURE',
                     'name': 'Offline fixture', 'score': 90, 'liquidityUsd': 200000,
                     'marketCap': 1000000, 'priceUsd': .001, 'priceNative': .00001,
                     'ageMinutes': 60, 'priceChange': {'m5': 6, 'h1': -15},
                     'txns': {'m5': {'buys': 20, 'sells': 18}}, 'volume': {'h1': 50000},
                     'signals': [], 'updatedAt': m.now_ms()}
        self.flow = {'quality': 'COMPLETE', 'trades': 4, 'buys': 3, 'sells': 1, 'buy_sell_usd_ratio': 2.0,
                     'unique_wallets': 4, 'buyer_wallets': 3, 'wallet_buy_sell_ratio': 3,
                     'max_sell_usd': 50, 'buy_usd': 300,
                     'verified_flow': {'source': promoted_guard.FLOW_SOURCE,
                         'coverage_status': 'COMPLETE', 'window_ms': promoted_guard.FLOW_WINDOW_MS,
                         'address': 'A'*44, 'pairAddress': 'B'*44, 'window_at': m.now_ms(),
                         'latest_event_at': m.now_ms()-100, 'available_at': m.now_ms()-50,
                         'trades': 4, 'unique_wallets': 3, 'buy_usd': 300, 'sell_usd': 50}}
        self.context = {'conviction': 80, 'mode': 'STRONG'}
        self.entry = {'token_raw_expected': 100000000, 'token_raw_floor': 99000000,
                      'input_usdc_raw': 200000000,'token_raw_amount': 100000000,'raw_quote': {'fixture': True}, 'price_impact_pct': 1.0,
                      'slippage_bps': 100, 'route': [], 'quoted_at': m.now_ms()}
        self.exit = {'expected_usdc': 198.0, 'floor_usdc': 196.8, 'provider_expected_usdc':198.0, 'quoted_at':m.now_ms(), 'raw_quote':{'fixture':True}}
        self.patches = [patch.object(m.STATE, 'live_flow', return_value=self.flow),
                        patch.object(self.monitor, 'market_context', return_value=self.context),
                        patch.object(m.paper_quotes, 'entry_quote', return_value=self.entry),
                        patch.object(m.paper_quotes, 'exit_quote', return_value=self.exit),
                        patch.object(m, 'append_audit'),
                        patch.object(m.rug_guard,'check',return_value={'status':'pass','mint':'A'*44,'pair':'B'*44,
                            'checked_at':m.now_ms(),'metrics':{'decimals':6,'token_account_rent_lamports':1650000}}),
                        patch.object(m.price_integrity,'check',return_value={'status':'pass','version':'TEST'})]
        self.mocks = [p.start() for p in self.patches]
        self.addCleanup(lambda: [p.stop() for p in reversed(self.patches)])
        self.addCleanup(self.monitor.stop)

    def test_critical_rug_blocks_before_quote(self):
        self.mocks[5].return_value={'status':'blocked','reasons':['rugcheck_critical']}
        self.monitor.maybe_open([self.coin])
        self.mocks[2].assert_not_called()
        self.assertFalse(m.STATE.positions)

    def test_unavailable_risk_does_not_invent_clearance(self):
        self.mocks[5].return_value={'status':'pending','reasons':['risk_check_pending']}
        self.monitor.maybe_open([self.coin])
        self.mocks[2].assert_not_called()
        self.assertFalse(m.STATE.positions)

    def test_remaining_daily_risk_budget_sizes_down_before_request(self):
        m.STATE.demo_balance_usd=905
        m.STATE.risk_day_start_balance_usd=1000
        with patch.object(m,'MAX_DAILY_LOSS_USD',100), patch.object(m.paper_quotes,'prepare_entry',return_value=None) as quote:
            self.monitor.maybe_open([self.coin])
        self.assertTrue(quote.called)
        size=quote.call_args.args[2]
        self.assertLess(size,200)
        self.assertGreaterEqual(size,10)
        self.assertLessEqual(size*.055+.225,5.01)


    def test_preserve_user_risk_settings(self):
        self.assertEqual(m.TRADE_NOTIONAL_USD, 200)
        self.assertEqual(m.MAX_DAILY_LOSS_USD, 100)
        self.assertEqual(m.STOP_LOSS_PCT, 5)
        self.assertEqual(m.MAX_POSITIONS, 8)
        self.assertEqual(m.MAX_DRAWDOWN_PCT, 20)
        self.assertEqual(m.TAKE_PROFIT_PCT, 10)
        self.assertEqual(m.STRICT_MAX_ROUNDTRIP_COST_PCT, 1.5)
        self.assertEqual(m.STRICT_MAX_WORST_CASE_COST_PCT, 2.5)

    def test_valid_formerly_overfiltered_entry_reaches_quote_and_opens(self):
        self.monitor.maybe_open([self.coin])
        self.assertEqual(len(m.STATE.positions), 1)
        pos = m.STATE.positions[0]
        self.assertEqual(pos['notional_usd'], 200)
        self.assertEqual(pos['entry_policy_version'], winner_ensemble.ENTRY_POLICY_VERSION)
        self.assertEqual(pos['entry_mode'], 'WINNER_ENSEMBLE_VERIFIED_FLOW')
        self.assertEqual(pos['verified_entry_flow']['source'], promoted_guard.FLOW_SOURCE)
        self.assertEqual(pos['strategy_id'], winner_ensemble.VERSION)
        self.assertEqual(pos['strategy_matches'], [
            'VERIFIED_FLOW_MOMENTUM', 'EARLY', 'MOMENTUM', 'PRECISION'])
        self.assertEqual(m.STATE.entry_diagnostics['status'], 'opened')
        self.assertLess(pos['entry_roundtrip_pnl_pct'], 0)
        self.assertIsNone(pos['hard_stop_net_pct'])
        self.assertEqual(pos['planned_stop_net_pct'], -5)

    def test_no_balance_session_history_reset(self):
        m.STATE.demo_balance_usd = 975
        m.STATE.demo_session_id = 'KEEP-MY-SESSION'
        old = [{'id': 'historical', 'address': 'C'*44, 'pnl_usd': -25, 'closed_at': 1}]
        m.STATE.history = copy.deepcopy(old)
        self.monitor.maybe_open([self.coin])
        self.assertEqual(m.STATE.demo_balance_usd, 975)
        self.assertEqual(m.STATE.demo_session_id, 'KEEP-MY-SESSION')
        self.assertEqual(m.STATE.history, old)

    def test_main_entries_require_complete_confirmed_exact_pool_flow_before_quote(self):
        with patch.object(m.STATE, 'live_flow', return_value={**self.flow, 'verified_flow': None}):
            self.monitor.maybe_open([self.coin])
        self.assertFalse(m.STATE.positions)
        self.assertEqual(m.STATE.entry_diagnostics['rejections']['promoted_verified_flow_unavailable'], 1)
        self.mocks[2].assert_not_called()

    def test_verified_flow_is_rechecked_immediately_before_entry_commit(self):
        with patch.object(m.STATE, 'live_flow', side_effect=[
                self.flow, {**self.flow, 'verified_flow': None}, self.flow]):
            self.monitor.maybe_open([self.coin])
        self.assertFalse(m.STATE.positions)
        self.assertEqual(m.STATE.entry_diagnostics['rejections']['promoted_verified_flow_unavailable'], 1)

    def test_live_flow_proof_uses_confirmed_fresh_exact_pair_swaps_only(self):
        now = m.now_ms()
        events = []
        for index, (direction, wallet, amount) in enumerate((
                ('BUY', 'wallet-a', 120), ('BUY', 'wallet-b', 80), ('SELL', 'wallet-a', 20))):
            event_at = now - 2_000 + index * 300
            observed_at = event_at + 100
            available_at = observed_at + 100
            events.append({'event_id': f'event-{index}', 'address': 'A'*44, 'pairAddress': 'B'*44,
                'ts': event_at, 'event_time': event_at, 'observed_at': observed_at,
                'available_at': available_at, 'ingested_at': available_at, 'confirmed_swap': True,
                'quality_flags': [], 'direction': direction, 'wallet': wallet, 'usd_amount': amount})
        tape = {'updated_at': now, 'events': events, 'pair_coverage': {'B'*44: {
            'address': 'A'*44, 'pairAddress': 'B'*44, 'status': 'COMPLETE',
            'complete_since_ms': now-60_000, 'last_poll_at': now}}}
        with patch.object(m, 'read_live_tape', return_value=tape):
            flow = m.State().live_flow('A'*44, 30, 'B'*44)
        decision = promoted_guard.flow_admission(
            {**self.coin, 'updatedAt': now}, {'verified_flow': flow['verified_flow']}, m.now_ms())
        self.assertTrue(decision['allow'])
        self.assertEqual(flow['verified_flow']['trades'], 3)
        self.assertEqual(flow['verified_flow']['unique_wallets'], 2)
        events[-1]['confirmed_swap'] = False
        tape['events'] = events
        with patch.object(m, 'read_live_tape', return_value=tape):
            unconfirmed = m.State().live_flow('A'*44, 30, 'B'*44)
        decision = promoted_guard.flow_admission(
            {**self.coin, 'updatedAt': now}, {'verified_flow': unconfirmed['verified_flow']}, m.now_ms())
        self.assertFalse(decision['allow'])
        self.assertEqual(unconfirmed['quality'], 'DEGRADED')
        for corrupt in ('direction', 'missing_id', 'duplicate_id'):
            bad_events = [dict(event) for event in events]
            if corrupt == 'direction':
                bad_events[0]['direction'] = 'UNKNOWN'
            elif corrupt == 'missing_id':
                bad_events[0].pop('event_id')
            else:
                bad_events[1]['event_id'] = bad_events[0]['event_id']
            tape['events'] = bad_events
            with patch.object(m, 'read_live_tape', return_value=tape):
                invalid = m.State().live_flow('A'*44, 30, 'B'*44)
            self.assertIsNone(invalid['verified_flow'], corrupt)
            self.assertEqual(invalid['quality'], 'DEGRADED', corrupt)

    def test_daily_loss_blocks_new_entry(self):
        m.STATE.demo_balance_usd = 900
        m.STATE.risk_day_start_balance_usd = 1000
        with patch.object(m,'MAX_DAILY_LOSS_USD',100):
            self.monitor.maybe_open([self.coin])
        self.assertFalse(m.STATE.positions)
        self.mocks[2].assert_not_called()
        self.assertEqual(m.STATE.entry_diagnostics['status'], 'daily_limit')

    def test_existing_position_is_preserved_when_second_position_opens(self):
        original = {'id': 'existing', 'address': 'D'*44, 'notional_usd': 200, 'quantity': 123}
        m.STATE.positions = [copy.deepcopy(original)]
        self.monitor.maybe_open([self.coin])
        self.assertEqual(m.STATE.positions[0], original)
        self.assertEqual(len(m.STATE.positions), 2)
        self.assertTrue(self.mocks[2].called)

    def test_no_quote_does_not_invent_a_fill(self):
        self.mocks[2].return_value = None
        self.monitor.maybe_open([self.coin])
        self.assertFalse(m.STATE.positions)
        self.assertEqual(m.STATE.entry_diagnostics['rejections']['quote_inconsistent'], 1)

    def test_no_sell_route_does_not_open(self):
        self.mocks[3].return_value = None
        self.monitor.maybe_open([self.coin])
        self.assertFalse(m.STATE.positions)
        self.assertEqual(m.STATE.entry_diagnostics['rejections']['quote_inconsistent'], 1)

    def test_costs_above_cap_are_still_rejected(self):
        attempted = []

        def too_costly_at_every_size(_address, _pair, notional):
            attempted.append(notional)
            amount = float(notional)
            quote = {**self.entry, 'input_usdc_raw': int(amount * 1_000_000),
                     'token_raw_amount': int(amount / self.coin['priceUsd'] * 1_000_000),
                     'price_impact_pct': 1.0, 'quoted_at': m.now_ms()}
            sale = {**self.exit, 'expected_usdc': amount * .98,
                    'floor_usdc': amount * .975, 'quoted_at': m.now_ms()}
            return quote, sale

        with patch.object(m.paper_quotes, 'prepare_entry', side_effect=too_costly_at_every_size):
            self.monitor.maybe_open([self.coin])
        self.assertEqual(attempted, [200.0, 100.0, 50.0])
        self.assertFalse(m.STATE.positions)
        self.assertIn('roundtrip_cost', m.STATE.entry_diagnostics['rejections'])

    def test_smaller_fresh_quote_can_open_after_cost_checked_size_retry(self):
        attempted = []

        def size_sensitive_quotes(_address, _pair, notional):
            attempted.append(notional)
            amount = float(notional)
            quote = {**self.entry, 'input_usdc_raw': int(amount * 1_000_000),
                     'token_raw_amount': int(amount / self.coin['priceUsd'] * 1_000_000),
                     'price_impact_pct': 2.1 if amount > 100 else 1.0,
                     'quoted_at': m.now_ms()}
            sale = {**self.exit,
                    'expected_usdc': amount * (.95 if amount > 100 else .985 if amount > 50 else .992),
                    'floor_usdc': amount * (.94 if amount > 100 else .975 if amount > 50 else .985),
                    'quoted_at': m.now_ms()}
            return quote, sale

        with patch.object(m.paper_quotes, 'prepare_entry', side_effect=size_sensitive_quotes):
            self.monitor.maybe_open([self.coin])
        self.assertEqual(attempted, [200.0, 100.0, 50.0])
        self.assertEqual(len(m.STATE.positions), 1)
        self.assertEqual(m.STATE.positions[0]['notional_usd'], 50.0)
        self.assertEqual(m.STATE.entry_diagnostics['size_retries'], 2)

    def test_impact_cap_is_enforced(self):
        self.entry['price_impact_pct'] = 2.01
        self.monitor.maybe_open([self.coin])
        self.assertFalse(m.STATE.positions)
        self.assertIn('impact', m.STATE.entry_diagnostics['rejections'])

    def test_stale_quote_is_not_used(self):
        self.entry['quoted_at'] -= 20000
        self.monitor.maybe_open([self.coin])
        self.assertFalse(m.STATE.positions)
        self.assertIn('quote_inconsistent', m.STATE.entry_diagnostics['rejections'])

    def test_winner_signal_rejects_below_broad_candidate_liquidity_and_score(self):
        for key,val in [('liquidityUsd',9999),('score',75.9)]:
            self.monitor.maybe_open([{**self.coin,key:val}])
            self.assertFalse(m.STATE.positions)
            self.assertIn('winner_signal',m.STATE.entry_diagnostics['rejections'])

    def test_market_winner_rule_does_not_invent_verified_flow(self):
        self.flow['unique_wallets']=0
        self.flow['quality']='UNKNOWN'
        self.flow['verified_flow']['trades']=2
        self.flow['verified_flow']['unique_wallets']=1
        self.monitor.maybe_open([self.coin])
        self.assertFalse(m.STATE.positions)
        self.assertEqual(m.STATE.entry_diagnostics['rejections']['promoted_buy_pressure_unconfirmed'], 1)
        self.mocks[2].assert_not_called()

    def test_independent_price_failure_blocks_before_quote(self):
        self.mocks[6].return_value={'status':'blocked','reason':'price_source_disagreement'}
        self.monitor.maybe_open([self.coin])
        self.mocks[2].assert_not_called()
        self.assertFalse(m.STATE.positions)

    def test_stale_feed_is_explained(self):
        self.coin['updatedAt'] -= 60000
        self.monitor.maybe_open([self.coin])
        self.assertFalse(m.STATE.positions)
        self.assertIn('stale_feed', m.STATE.entry_diagnostics['rejections'])
        self.assertIn('Откази', m.STATE.entry_diagnostics['message'])

    def test_quote_attempts_are_bounded(self):
        self.mocks[2].return_value = None
        self.mocks[0].side_effect = lambda address, *_args, **_kwargs: {
            **self.flow, 'verified_flow': {**self.flow['verified_flow'], 'address': address}}
        self.mocks[5].side_effect = lambda coin: {
            'status': 'pass', 'mint': coin['address'], 'pair': coin['pairAddress'],
            'checked_at': m.now_ms(), 'metrics': {'decimals': 6, 'token_account_rent_lamports': 1650000}}
        self.monitor.maybe_open([dict(self.coin, address=x*44) for x in 'ACDEFGH'])
        self.assertEqual(self.mocks[2].call_count, policy.MAX_QUOTED_CANDIDATES,
                         m.STATE.entry_diagnostics)
        self.assertEqual(m.STATE.entry_diagnostics['signal_passed'], 7)

    def test_reported_thresholds_match_real_policy(self):
        snapshot = m.STATE.snapshot()
        self.assertEqual(snapshot['config']['entry_score'], winner_ensemble.MIN_SCORE)
        self.assertEqual(snapshot['config']['min_liquidity_usd'], winner_ensemble.MIN_LIQUIDITY_USD)
        self.assertEqual(snapshot['config']['entry_policy_version'], winner_ensemble.ENTRY_POLICY_VERSION)
        self.assertEqual(snapshot['config']['strict_max_roundtrip_cost_pct'], 1.5)
        self.assertEqual(snapshot['config']['strict_max_worst_case_cost_pct'], 2.5)
        self.assertEqual(snapshot['config']['verified_entry_policy']['version'], promoted_guard.VERSION)
        self.assertFalse(snapshot['config']['verified_entry_policy']['profitability_proven'])
        self.assertEqual(snapshot['config']['ensemble_strategies'], list(winner_ensemble.STRATEGIES))
        self.assertIn('entry_diagnostics', snapshot)

if __name__ == '__main__':
    unittest.main(verbosity=2)
