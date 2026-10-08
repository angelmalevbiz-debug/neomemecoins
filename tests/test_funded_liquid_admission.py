"""Exercise the funded loop with eligible markets and actual modeled costs.

Synthetic fixtures verify admission mechanics, never profitability or returns.
"""
import copy
import unittest
from unittest.mock import patch

import funded_market_candidates as candidates
import promoted_entry_guard as guard
import strategy_lab as lab
from tape_pool_scheduler import TapePoolScheduler
import entry_defense as _isolation_entry_defense
import strategy_lab as _isolation_lab
import tape_pool_scheduler as _isolation_tape

_DEFENSIVE_ISOLATION = []


def _defensive_pass(*_args, **_kwargs):
    return _isolation_entry_defense.pass_decision('TEST_GATE_ISOLATION')


def _path_less_layers():
    """Path-less layers for the module globals that otherwise build one from the env paths."""
    return (
        (_isolation_lab, 'DEFENSE', _isolation_entry_defense.DefensiveEntryLayer()),
    )


def setUpModule():
    """These tests isolate other entry gates. DEFENSIVE_ENTRY_LAYER_V1 (structural rug
    guard, pool loss memory, heat veto and its warm-up) has its own suite in
    tests/test_defensive_entry_layer.py, which proves every path consults it."""
    for target, name in ((_isolation_lab, 'defensive_entry_decision'), (_isolation_tape.TapePoolScheduler, 'defensive_entry_decision'),):
        isolation = patch.object(target, name, _defensive_pass)
        isolation.start()
        _DEFENSIVE_ISOLATION.append(isolation)
    # The layer's ticker registry stays in memory here. Run on its own (without
    # scripts/run_python_checks.py), this module must never write a ticker sidecar next
    # to the shell's NEO_STRATEGY_LAB_PATH / NEO_LIVE_TAPE_PATH (or the /var/lib/neo-market
    # defaults): strategy_lab.maybe_open observes through lab.DEFENSE and
    # live_tape.feed_snapshot through the module-level _POOL_SCHEDULER.
    for target, name, value in _path_less_layers():
        isolation = patch.object(target, name, value)
        isolation.start()
        _DEFENSIVE_ISOLATION.append(isolation)


def tearDownModule():
    while _DEFENSIVE_ISOLATION:
        _DEFENSIVE_ISOLATION.pop().stop()


NOW = 1_800_000_000_000
MINT = 'BrUimx7KncgRNTggAdZdaX2s5XUqQyR6XMRmEg4mpump'
PAIR = '8t34p7n94man8wcmFdHedYkJaWEhA9nKGJMLZyZUzbn'


def coin(**changes):
    return {'address': MINT, 'pairAddress': PAIR, 'symbol': 'FIXTURE',
            'priceUsd': .1, 'priceNative': .1/150,
            'quoteTokenAddress': lab.SOL_QUOTE_MINT, 'dexId': 'pumpswap',
            'liquidityUsd': 2_000_000, 'marketCap': 100_000_000,
            'updatedAt': NOW, 'score': 60, 'ageMinutes': 5000,
            'priceChange': {'m5': 2, 'h1': 5}, 'volume': {'h1': 100_000},
            'txns': {'m5': {'buys': 40, 'sells': 20}}, **changes}


def flows():
    return {(MINT, PAIR): {'verified_flow': {
        'source': guard.FLOW_SOURCE, 'coverage_status': 'COMPLETE',
        'window_ms': guard.FLOW_WINDOW_MS, 'address': MINT, 'pairAddress': PAIR,
        'window_at': NOW, 'available_at': NOW-50, 'latest_event_at': NOW-100,
        'trades': 4, 'unique_wallets': 3, 'buy_usd': 500, 'sell_usd': 100}}}


class FundedLiquidAdmissionTests(unittest.TestCase):
    def setUp(self):
        self.clock = NOW
        books = {s['id']: lab.empty_book(s) for s in lab.STRATEGIES}
        for book in books.values():
            book['position'] = {'preserved': book['id']}
        self.book = books['MOMENTUM']
        self.book['position'] = None
        self.book['history'] = [{'trade_no': 1, 'closed_at': NOW-100_000,
                                 'address': 'old', 'pnl_usd': -1,
                                 'entry_policy_version': 'LEGACY_UNCHANGED'}]
        self.book['trade_seq'] = 1
        self.state = {'books': books}
        self.risk = {'status': 'pass', 'mint': MINT, 'pair': PAIR, 'checked_at': NOW}
        self.price = {'status': 'pass', 'mint': MINT, 'pair': PAIR}
        for mock in (patch.object(lab, 'STATE', self.state),
                     patch.object(lab, 'now_ms', side_effect=lambda: self.clock),
                     patch.object(lab.rug_guard, 'check', return_value=self.risk),
                     patch.object(lab.price_integrity, 'check', return_value=self.price)):
            mock.start(); self.addCleanup(mock.stop)

    def test_actual_loop_opens_bounded_new_policy_trade_without_rewriting_history(self):
        before = copy.deepcopy(self.state)
        lab.maybe_open([coin()], flows())
        position = self.book['position']
        self.assertIsNotNone(position)
        self.assertEqual(position['entry_policy_version'], guard.FUNDED_POLICY_VERSION)
        self.assertEqual(position['entry_matched_candidate_branches'],
                         ['ESTABLISHED_LIQUID_MOMENTUM'])
        self.assertEqual(position['notional_usd'],
                         lab.PROMOTED_ALLOCATION * lab.PROMOTED_MAX_POSITION_FRACTION)
        self.assertLess(position['open_pnl_usd'], 0)
        self.assertGreaterEqual(position['entry_roundtrip_pnl_pct'], -1.5)
        for field in ('history', 'balance', 'starting_balance'):
            self.assertEqual(self.book[field], before['books']['MOMENTUM'][field])
        for key, book in before['books'].items():
            if key != 'MOMENTUM':
                for field in ('position', 'history', 'balance', 'starting_balance', 'trade_seq'):
                    self.assertEqual(self.state['books'][key][field], book[field])
        self.assertEqual(lab.stats(self.book)['promoted_policy_trades'], 0)

    def test_established_market_never_replaces_missing_flow_or_wrong_pool_safety_price(self):
        checks = [({}, self.risk, self.price),
                  (flows(), {**self.risk, 'pair': 'other'}, self.price),
                  (flows(), self.risk, {**self.price, 'mint': 'other'})]
        before = copy.deepcopy(self.book)
        for flow, risk, price in checks:
            with self.subTest(flow=bool(flow), risk=risk, price=price), \
                 patch.object(lab.rug_guard, 'check', return_value=risk), \
                 patch.object(lab.price_integrity, 'check', return_value=price):
                lab.maybe_open([coin()], flow)
                self.assertIsNone(self.book['position'])
                for field in ('history', 'balance', 'trade_seq', 'last_entry_by_address'):
                    self.assertEqual(self.book[field], before[field])

    def test_market_branch_still_refuses_actual_modeled_cost_above_budget(self):
        c = coin(marketCap=5_000_000)
        self.assertEqual(candidates.matched_branches('MOMENTUM', c,
                         lab.activity.market_features(c)), ['ESTABLISHED_LIQUID_MOMENTUM'])
        lab.maybe_open([c], flows())
        self.assertIsNone(self.book['position'])
        self.assertEqual(self.book['entry_diagnostics']['promoted_cost_rejected'], 1)

    def test_completed_safety_uses_receipt_clock_instead_of_poll_start(self):
        def completed_check(_):
            self.clock = NOW+500
            return {**self.risk, 'checked_at': self.clock}
        with patch.object(lab.rug_guard, 'check', side_effect=completed_check):
            lab.maybe_open([coin()], flows())
        self.assertIsNotNone(self.book['position'])
        self.assertEqual(self.book['position']['risk_guard']['checked_at'], NOW+500)

    def test_provider_delay_cannot_commit_when_flow_has_expired(self):
        def delayed_price(_):
            self.clock = NOW+12_001
            return self.price
        with patch.object(lab.price_integrity, 'check', side_effect=delayed_price):
            lab.maybe_open([coin()], flows())
        self.assertIsNone(self.book['position'])
        self.assertEqual(self.book['entry_diagnostics']['commit_recheck_rejected'], 1)
        self.assertEqual(self.book['entry_diagnostics']['blocked_reason'],
                         'promoted_verified_flow_stale')

    def test_observation_scheduler_and_admission_use_same_mature_universe(self):
        selected, diag = TapePoolScheduler().select({'feed': [coin()]}, now=NOW, max_tracked=1)
        self.assertEqual(selected, [coin()])
        self.assertEqual(diag['estimated_feasible_market_candidates'], 1)
        self.assertEqual(diag['examples'][0]['funded_rules'], ['MOMENTUM', 'PRECISION'])
        self.assertEqual(diag['selected_exploration_pools'], 0)
        self.assertFalse(diag['profitability_proven'])


if __name__ == '__main__':
    unittest.main()
