"""COST_FIRST_ESTABLISHED_V1: isolated Lab book pair on a cost-defined universe.

Synthetic fixtures verify the universe rule, the size rule, the shared evidence
gates and the two exit geometries. No outcome, win rate or return is claimed.
"""
import copy
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

TMP = tempfile.TemporaryDirectory()
os.environ['NEO_STRATEGY_LAB_PATH'] = str(Path(TMP.name) / 'state.json')
os.environ['NEO_STRATEGY_LAB_COMPACT_PATH'] = str(Path(TMP.name) / 'compact.json')
os.environ['NEO_STRATEGY_LAB_RESET_FLAG'] = str(Path(TMP.name) / 'reset')
import cost_first_established as cf
import lab_activity as activity
import paper_market_feasibility as feasibility
import promoted_entry_guard as guard
import strategy_lab as lab

NOW = 1_800_000_000_000
MINT = 'BrUimx7KncgRNTggAdZdaX2s5XUqQyR6XMRmEg4mpump'
PAIR = '8t34p7n94man8wcmFdHedYkJaWEhA9nKGJMLZyZUzbn'
SOL_USD = 150.0


def coin(**changes):
    # Tier 30 bps (market cap 100,000 SOL), $2M liquidity, SOL-quoted PumpSwap pool.
    return {'address': MINT, 'pairAddress': PAIR, 'symbol': 'FIXTURE',
            'priceUsd': .1, 'priceNative': .1 / SOL_USD,
            'quoteTokenAddress': lab.SOL_QUOTE_MINT, 'dexId': 'pumpswap',
            'liquidityUsd': 2_000_000, 'marketCap': 100_000 * SOL_USD,
            'updatedAt': NOW, 'score': 40, 'ageMinutes': 5000,
            'priceChange': {'m5': .1, 'h1': -2}, 'volume': {'h1': 50_000},
            'txns': {'m5': {'buys': 10, 'sells': 12}}, **changes}


def verified_flows():
    return {(MINT, PAIR): {'trades': 4, 'buys': 3, 'sells': 1, 'buy_usd': 500, 'sell_usd': 100,
                           'unique_wallets': 3, 'ratio': 5, 'max_sell': 100, 'verified_flow': {
        'source': guard.FLOW_SOURCE, 'coverage_status': 'COMPLETE',
        'window_ms': guard.FLOW_WINDOW_MS, 'address': MINT, 'pairAddress': PAIR,
        'window_at': NOW, 'available_at': NOW - 50, 'latest_event_at': NOW - 100,
        'trades': 4, 'unique_wallets': 3, 'buy_usd': 500, 'sell_usd': 100}}}


class UniverseRuleTests(unittest.TestCase):
    def test_versions_books_and_exit_geometries(self):
        self.assertEqual(cf.VERSION, 'COST_FIRST_ESTABLISHED_V1')
        self.assertEqual(cf.ENTRY_POLICY_VERSION, 'COST_FIRST_ESTABLISHED_V1')
        self.assertEqual(cf.BOOK_IDS, ('COST_FIRST_CONTROL', 'COST_FIRST_SCALED'))
        self.assertEqual((cf.CONTROL_EXITS.stop_loss_net_pct, cf.CONTROL_EXITS.take_profit_net_pct,
                          cf.CONTROL_EXITS.max_hold_minutes), (3.0, 10.0, 60.0))
        self.assertEqual((cf.SCALED_EXITS.stop_loss_net_pct, cf.SCALED_EXITS.take_profit_net_pct,
                          cf.SCALED_EXITS.max_hold_minutes), (3.0, 4.0, 60.0))
        self.assertEqual(cf.CONTROL_EXITS.take_profit_reason, 'TAKE_PROFIT_10_NET')
        self.assertEqual(cf.SCALED_EXITS.take_profit_reason, 'TAKE_PROFIT_4_NET')
        self.assertEqual(cf.SCALED_EXITS.stop_reason, 'STOP_LOSS_3_NET')
        self.assertEqual(cf.SCALED_EXITS.max_hold_reason, 'ABSOLUTE_MAX_HOLD_60')
        self.assertEqual(cf.START_BALANCE_USD, 500.0)
        self.assertEqual(cf.PORTFOLIO_GROUP, 'TEST')
        self.assertFalse(cf.AUTOMATIC_PROMOTION)
        config = cf.config()
        self.assertFalse(config['profitability_proven'])
        self.assertFalse(config['automatic_promotion'])
        self.assertEqual(config['universe']['max_fee_tier_bps'], 50.0)
        self.assertEqual(config['universe']['min_liquidity_usd'], 250_000.0)
        self.assertEqual(config['universe']['max_fee_impact_roundtrip_pct'], 1.2)
        self.assertEqual(config['universe']['liquidity_size_fraction'], .001)

    def test_fee_tier_follows_pump_fee_tiers_from_market_cap_in_sol(self):
        self.assertEqual(cf.fee_tier_bps(coin()), 30.0)
        self.assertEqual(cf.fee_tier_bps(coin(marketCap=60_000 * SOL_USD)), 50.0)
        self.assertEqual(cf.fee_tier_bps(coin(marketCap=56_000 * SOL_USD)), 52.5)
        self.assertEqual(cf.fee_tier_bps(coin(marketCap=1_000 * SOL_USD)), 120.0)
        self.assertEqual(cf.fee_tier_bps(coin(marketCap=60_000 * SOL_USD)),
                         feasibility.pumpswap_fee_bps(coin(marketCap=60_000 * SOL_USD)))
        for broken in (coin(dexId='raydium'), coin(marketCap=0), coin(marketCap=None, fdv=None),
                       coin(priceNative=0), coin(quoteTokenAddress='USDC')):
            self.assertIsNone(cf.fee_tier_bps(broken))

    def test_size_rule_only_shrinks_the_book_cap(self):
        self.assertEqual(cf.size_for(2_000_000, 150), 150.0)
        self.assertEqual(cf.size_for(250_000, 500), 250.0)
        self.assertEqual(cf.size_for(250_000, 150), 150.0)
        self.assertEqual(cf.size_for(123_456, 500), 123.45)
        self.assertEqual(cf.size_for(0, 150), 0.0)
        self.assertEqual(cf.size_for(1_000_000, 0), 0.0)
        self.assertEqual(cf.size_for('x', 150), 0.0)
        self.assertEqual(cf.size_for(1_000_000, float('nan')), 0.0)

    def test_fee_impact_roundtrip_excludes_buffers_but_includes_both_fee_legs(self):
        tier30 = cf.fee_impact_roundtrip_pct(coin(), 150)
        self.assertAlmostEqual(tier30, .6 + 4 * 150 / 2_000_000 * 100, delta=.02)
        tier50 = cf.fee_impact_roundtrip_pct(coin(marketCap=60_000 * SOL_USD), 150)
        self.assertAlmostEqual(tier50, 1.0 + 4 * 150 / 2_000_000 * 100, delta=.02)
        self.assertIsNone(cf.fee_impact_roundtrip_pct(coin(), 0))
        self.assertIsNone(cf.fee_impact_roundtrip_pct(coin(priceNative=0), 150))

    def test_candidate_matrix(self):
        self.assertTrue(cf.candidate(coin(), cap_usd=150))
        self.assertTrue(cf.candidate(coin(marketCap=60_000 * SOL_USD), cap_usd=150))
        self.assertTrue(cf.candidate(coin(score=0), cap_usd=150))
        self.assertTrue(cf.candidate(coin(ageMinutes=30), cap_usd=150))
        cases = {
            'fee_tier_above_maximum': coin(marketCap=56_000 * SOL_USD),
            'liquidity_below_minimum': coin(liquidityUsd=249_999),
            'liquidity_unknown': coin(liquidityUsd=None),
            'dex_not_pumpswap': coin(dexId='raydium'),
            'quote_token_not_sol': coin(quoteTokenAddress='EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v'),
            'network_price_unknown': coin(priceNative=0),
            'market_cap_unknown': coin(marketCap=0),
            'fee_impact_roundtrip_above_maximum': coin(marketCap=60_000 * SOL_USD, liquidityUsd=250_000),
        }
        for reason, market in cases.items():
            self.assertIn(reason, cf.rejections(market, cap_usd=150), reason)
            self.assertFalse(cf.candidate(market, cap_usd=150), reason)
        self.assertEqual(cf.rejections(coin(), cap_usd=0), ['size_below_minimum'])
        self.assertEqual(cf.rejections(coin(), cap_usd=5, minimum_notional_usd=10), ['size_below_minimum'])
        for reason in cf.rejections(coin(dexId='raydium', liquidityUsd=1000), cap_usd=150):
            self.assertIn(reason, cf.REJECTION_REASONS)

    def test_describe_is_a_planning_record_not_a_quote(self):
        record = cf.describe(coin(), cap_usd=150)
        self.assertEqual(record['fee_tier_bps'], 30.0)
        self.assertEqual(record['planned_notional_usd'], 150.0)
        self.assertEqual(record['rejections'], [])
        self.assertFalse(record['is_execution_quote'])
        self.assertEqual(record['universe_version'], cf.UNIVERSE_VERSION)


class LabBookPairTests(unittest.TestCase):
    def setUp(self):
        self.clock = NOW
        self.books = {s['id']: lab.empty_book(s) for s in lab.STRATEGIES}
        for key, book in self.books.items():
            if key not in cf.BOOK_IDS:
                book['position'] = {'preserved': key}
        self.control = self.books[cf.CONTROL_BOOK_ID]
        self.scaled = self.books[cf.SCALED_BOOK_ID]
        self.risk = {'status': 'pass', 'mint': MINT, 'pair': PAIR, 'checked_at': NOW}
        self.price = {'status': 'pass', 'mint': MINT, 'pair': PAIR}
        for mock in (patch.object(lab, 'STATE', {'books': self.books}),
                     patch.object(lab, 'now_ms', side_effect=lambda: self.clock),
                     patch.object(lab.rug_guard, 'check', return_value=self.risk),
                     patch.object(lab.price_integrity, 'check', return_value=self.price)):
            mock.start(); self.addCleanup(mock.stop)

    def test_registered_as_isolated_test_books_with_500_each(self):
        self.assertEqual(set(activity.RULES), {s['id'] for s in lab.STRATEGIES})
        for key in cf.BOOK_IDS:
            self.assertNotIn(key, lab.PROMOTED_STRATEGIES)
            book = self.books[key]
            self.assertEqual(book['portfolio_group'], 'TEST')
            self.assertEqual(book['starting_balance'], 500.0)
            self.assertEqual(book['balance'], 500.0)
            self.assertEqual(book['max_position_fraction'], 1.0)

    def test_both_books_open_the_same_universe_entry_with_their_own_exit_parameters(self):
        before = copy.deepcopy(self.books)
        lab.maybe_open([coin()], verified_flows())
        for book, exits in ((self.control, cf.CONTROL_EXITS), (self.scaled, cf.SCALED_EXITS)):
            position = book['position']
            self.assertIsNotNone(position, book['id'])
            self.assertEqual(position['entry_policy_version'], 'COST_FIRST_ESTABLISHED_V1')
            self.assertEqual(position['entry_universe_version'], cf.UNIVERSE_VERSION)
            self.assertEqual(position['notional_usd'], 150.0)
            self.assertFalse(position['promotion_eligible'])
            self.assertEqual(position['exit_parameters']['take_profit_net_pct'], exits.take_profit_net_pct)
            self.assertEqual(position['exit_policy_label'], exits.label)
            self.assertEqual(position['verified_entry_flow']['address'], MINT)
            self.assertEqual(position['risk_guard'], self.risk)
            self.assertEqual(position['entry_evidence_guard_version'], guard.VERSION)
            self.assertEqual(position['entry_universe']['fee_tier_bps'], 30.0)
            self.assertEqual(position['entry_cost_cap_pct'], 1.5)
            self.assertGreaterEqual(position['entry_roundtrip_pnl_pct'], -1.5)
            self.assertGreaterEqual(position['stop_headroom_pct'], 1.5)
            self.assertLess(position['open_pnl_usd'], 0)
            self.assertEqual(book['balance'], 500.0)
            diagnostics = book['entry_diagnostics']
            self.assertEqual(diagnostics['cost_first']['universe_candidates'], 1)
            self.assertEqual(diagnostics['entry_policy_version'], 'COST_FIRST_ESTABLISHED_V1')
            self.assertFalse(diagnostics['cost_first']['automatic_promotion'])
        self.assertEqual(self.control['position']['address'], self.scaled['position']['address'])
        for key, book in before.items():
            if key not in cf.BOOK_IDS:
                for field in ('position', 'history', 'balance', 'starting_balance', 'trade_seq'):
                    self.assertEqual(self.books[key][field], book[field], (key, field))

    def test_liquidity_scaled_size_shrinks_the_lab_entry_limit(self):
        # $120k liquidity would size to $120 but fails the universe floor; use
        # a pool just above the floor: min($150, $260k x 0.001) = $150. A larger
        # book cap only matters when liquidity x 0.001 is below it.
        self.assertEqual(cf.size_for(260_000, lab.TRADE_NOTIONAL), 150.0)
        with patch.object(lab, 'TRADE_NOTIONAL', 400.0):
            lab.maybe_open([coin(liquidityUsd=300_000)], verified_flows())
        self.assertIsNotNone(self.control['position'])
        self.assertEqual(self.control['position']['notional_usd'], 300.0)
        self.assertEqual(self.control['position']['entry_universe']['planned_notional_usd'], 300.0)

    def test_universe_rejections_are_counted_and_cost_first_never_uses_the_score(self):
        lab.maybe_open([coin(marketCap=56_000 * SOL_USD, score=100)], verified_flows())
        for book in (self.control, self.scaled):
            self.assertIsNone(book['position'])
            diagnostics = book['entry_diagnostics']
            self.assertEqual(diagnostics['signal_candidates'], 0)
            self.assertEqual(diagnostics['blocked_reason'], 'no_market_signal')
            self.assertEqual(diagnostics['cost_first']['universe_rejections'], {'fee_tier_above_maximum': 1})

    def test_confirmed_flow_fresh_safety_and_price_identity_remain_required(self):
        checks = [({(MINT, PAIR): {'trades': 9}}, self.risk, self.price, 'promoted_verified_flow_unavailable'),
                  (verified_flows(), {**self.risk, 'pair': 'other'}, self.price, 'promoted_safety_unavailable'),
                  (verified_flows(), {**self.risk, 'checked_at': NOW - guard.SAFETY_MAX_AGE_MS - 1}, self.price,
                   'promoted_safety_unavailable'),
                  (verified_flows(), self.risk, {**self.price, 'mint': 'other'}, 'promoted_price_identity_unverified')]
        before = copy.deepcopy(self.books)
        for flow, risk, price, reason in checks:
            with self.subTest(reason=reason), \
                 patch.object(lab.rug_guard, 'check', return_value=risk), \
                 patch.object(lab.price_integrity, 'check', return_value=price):
                lab.maybe_open([coin()], flow)
                for book in (self.control, self.scaled):
                    self.assertIsNone(book['position'])
                    self.assertEqual(book['entry_diagnostics']['blocked_reason'], reason)
                    self.assertEqual(book['entry_diagnostics']['cost_first']['universe_candidates'], 1)
        for key, book in before.items():
            for field in ('history', 'balance', 'trade_seq', 'last_entry_by_address'):
                self.assertEqual(self.books[key][field], book[field], key)

    def test_lab_cost_cap_still_applies_inside_the_universe(self):
        # Tier 50 bps on $250k liquidity: fee+impact 1.24% > 1.2% universe rule.
        # Tier 50 bps on $300k: universe passes (1.2%) but the Lab's full model
        # (fee + impact + 0.4% buffers) exceeds the shared 1.5% cap.
        lab.maybe_open([coin(marketCap=60_000 * SOL_USD, liquidityUsd=300_000)], verified_flows())
        for book in (self.control, self.scaled):
            self.assertIsNone(book['position'])
            diagnostics = book['entry_diagnostics']
            self.assertEqual(diagnostics['cost_first']['universe_candidates'], 1)
            self.assertEqual(diagnostics['cost_rejected'], 1)
            self.assertEqual(diagnostics['cost_infeasible_candidates'], 1)
            self.assertEqual(diagnostics['blocked_reason'], 'modeled_roundtrip_cost_limit')
            self.assertEqual(diagnostics['max_entry_roundtrip_cost_pct'], 1.5)

    def test_commit_recheck_refuses_expired_flow(self):
        def delayed_price(_):
            self.clock = NOW + guard.FLOW_MAX_AGE_MS + 1
            return self.price
        with patch.object(lab.price_integrity, 'check', side_effect=delayed_price):
            lab.maybe_open([coin()], verified_flows())
        # The first book's provider delay expires the flow at commit; the second
        # book then sees the expired flow at its own pre-check. Neither opens.
        self.assertEqual(self.control['entry_diagnostics']['commit_recheck_rejected'], 1)
        for book in (self.control, self.scaled):
            self.assertIsNone(book['position'])
            self.assertEqual(book['entry_diagnostics']['blocked_reason'], 'promoted_verified_flow_stale')

    def mark(self, net_pct, *, hold_minutes=1.0):
        lab.maybe_open([coin()], verified_flows())
        for book in (self.control, self.scaled):
            self.assertIsNotNone(book['position'])
            book['position']['opened_at'] = NOW - int(hold_minutes * 60_000)
        quotes = {}
        for book in (self.control, self.scaled):
            basis = book['position']['remaining_cost_basis_usd']
            quotes[book['id']] = basis + book['position']['notional_usd'] * net_pct / 100
        def exit_execution(market, quantity):
            book = next(b for b in (self.control, self.scaled)
                        if b['position'] and abs(b['position']['quantity'] - quantity) < 1e-12)
            return {'fill_price': .1, 'net_proceeds_usd': quotes[book['id']], 'dex_fee_usd': .3,
                    'network_fee_usd': .01, 'impact_pct': .1, 'slippage_pct': .1, 'latency_pct': .1}
        with patch.object(lab, 'exit_execution', side_effect=exit_execution):
            lab.update_positions({}, [coin(updatedAt=NOW)])

    def test_scaled_book_takes_4_net_while_control_waits_for_10(self):
        self.mark(4.5)
        self.assertIsNotNone(self.control['position'])
        self.assertEqual(self.scaled['history'][0]['exit_reason'], 'TAKE_PROFIT_4_NET')
        self.assertAlmostEqual(self.scaled['history'][0]['pnl_usd'], 150 * .045, places=4)
        self.assertEqual(self.scaled['history'][0]['entry_policy_version'], 'COST_FIRST_ESTABLISHED_V1')
        self.assertEqual(self.scaled['history'][0]['exit_policy_label'], 'COST_SCALED_3_4_60')
        self.assertEqual(lab.stats(self.scaled)['active_policy_trades'], 1)

    def test_control_book_takes_10_net(self):
        self.mark(10.5)
        self.assertEqual(self.control['history'][0]['exit_reason'], 'TAKE_PROFIT_10_NET')
        self.assertEqual(self.scaled['history'][0]['exit_reason'], 'TAKE_PROFIT_4_NET')

    def test_both_books_stop_at_minus_3_net_without_clamping_the_gap(self):
        self.mark(-7.5)
        for book in (self.control, self.scaled):
            self.assertEqual(book['history'][0]['exit_reason'], 'STOP_LOSS_3_NET')
            self.assertAlmostEqual(book['history'][0]['pnl_usd'], -150 * .075, places=4)
            self.assertAlmostEqual(book['balance'], 500 - 150 * .075, places=4)

    def test_both_books_leave_at_60_minutes(self):
        self.mark(1.0, hold_minutes=60)
        for book in (self.control, self.scaled):
            self.assertEqual(book['history'][0]['exit_reason'], 'ABSOLUTE_MAX_HOLD_60')
        self.setUp(); self.mark(1.0, hold_minutes=59)
        for book in (self.control, self.scaled):
            self.assertIsNotNone(book['position'])

    def test_lifecycle_and_portfolio_setup_never_promote_the_pair(self):
        self.mark(10.5)
        lab.STATE['portfolio_setup'] = {}
        lab.STATE['stats'] = {}
        lab.persist('online')
        self.assertEqual(lab.STATE['portfolio_setup']['strategies'], list(lab.PROMOTED_STRATEGIES))
        self.assertFalse(lab.STATE['activity_config']['cost_first_established']['automatic_promotion'])
        for key in cf.BOOK_IDS:
            self.assertEqual(lab.STATE['books'][key]['portfolio_group'], 'TEST')
            self.assertIn(key, lab.STATE['strategy_lifecycle']['active_registered_strategy_ids'])

    def test_every_universe_candidate_size_rejected_names_the_size_block(self):
        self.assertGreater(activity.entry_minimum_notional(cf.CONTROL_BOOK_ID), .5)
        with patch.object(lab.cost_first, 'size_for', return_value=.5):
            lab.maybe_open([coin()], verified_flows())
        for book in (self.control, self.scaled):
            self.assertIsNone(book['position'])
            diagnostics = book['entry_diagnostics']
            self.assertEqual(diagnostics['signal_candidates'], 0)
            self.assertEqual(diagnostics['cost_first']['universe_rejections'], {'size_below_minimum': 1})
            self.assertEqual(diagnostics['blocked_reason'], 'cost_first_size_below_minimum')
        # A physical rejection elsewhere in the feed is not a size block.
        with patch.object(lab.cost_first, 'size_for', return_value=.5):
            lab.maybe_open([coin(), coin(address=MINT[:-4] + 'aaaa', pairAddress=PAIR[:-4] + 'aaaa',
                                         dexId='raydium')], verified_flows())
        self.assertEqual(self.control['entry_diagnostics']['blocked_reason'], 'cost_first_size_below_minimum')

    def test_every_universe_candidate_in_reentry_cooldown_names_the_cooldown(self):
        self.mark(10.5)
        for book in (self.control, self.scaled):
            self.assertIsNone(book['position'])
        lab.maybe_open([coin()], verified_flows())
        for book in (self.control, self.scaled):
            self.assertIsNone(book['position'])
            diagnostics = book['entry_diagnostics']
            self.assertEqual(diagnostics['cooldown_rejected'], 1)
            self.assertEqual(diagnostics['blocked_reason'], 'reentry_cooldown')

    def losing_rows(self, book, count, *, first_trade_no, version=cf.ENTRY_POLICY_VERSION):
        return [{'trade_no': first_trade_no + index, 'strategy_id': book['id'],
                 'address': MINT, 'pairAddress': PAIR, 'pnl_usd': -4.5,
                 'opened_at': NOW - 900_000 + index * 10_000,
                 'closed_at': NOW - 899_000 + index * 10_000,
                 'entry_policy_version': version,
                 'execution_mode': lab.EXECUTION_MODEL_VERSION,
                 'price_crosscheck': {'status': 'pass', 'mint': MINT, 'pair': PAIR},
                 'quote_status': 'fresh', 'exit_reason': 'STOP_LOSS_3_NET'}
                for index in range(count)]

    def test_twelve_losing_cost_first_closes_retire_entries_but_keep_exits_and_ledger(self):
        lab.maybe_open([coin()], verified_flows())
        for book in (self.control, self.scaled):
            self.assertIsNotNone(book['position'])
            book['history'] = self.losing_rows(book, 12, first_trade_no=2) + book['history']
            book['balance'] -= 4.5 * 12
        before = copy.deepcopy(self.books)
        report = lab.review_strategy_lifecycle(self.books)
        for book in (self.control, self.scaled):
            marker = book['strategy_lifecycle']
            self.assertEqual(marker['status'], 'retired', book['id'])
            self.assertEqual(marker['reason'], 'repeated_observed_paper_losses')
            self.assertEqual(marker['evidence']['closed_trades'], 12)
            self.assertEqual(marker['evidence']['activity_version'], 'COST_FIRST_ESTABLISHED_V1')
            self.assertEqual(marker['evidence']['net_pnl_usd'], -54.0)
            self.assertIn(book['id'], report['retired_strategy_ids'])
            self.assertIn(book['id'], report['retired_open_position_ids'])
            for field, value in before[book['id']].items():
                if field != 'strategy_lifecycle':
                    self.assertEqual(book[field], value, field)
        self.assertEqual(report['policy']['activity_versions_by_strategy'],
                         {key: 'COST_FIRST_ESTABLISHED_V1' for key in cf.BOOK_IDS})
        # Retirement stops new entries only; the open positions still exit normally.
        quotes = {book['id']: book['position']['remaining_cost_basis_usd'] * .925
                  for book in (self.control, self.scaled)}
        def exit_execution(market, quantity):
            book = next(b for b in (self.control, self.scaled)
                        if b['position'] and abs(b['position']['quantity'] - quantity) < 1e-12)
            return {'fill_price': .1, 'net_proceeds_usd': quotes[book['id']], 'dex_fee_usd': .3,
                    'network_fee_usd': .01, 'impact_pct': .1, 'slippage_pct': .1, 'latency_pct': .1}
        with patch.object(lab, 'exit_execution', side_effect=exit_execution):
            lab.update_positions({}, [coin(updatedAt=NOW)])
        for book in (self.control, self.scaled):
            self.assertIsNone(book['position'])
            self.assertEqual(book['history'][0]['exit_reason'], 'STOP_LOSS_3_NET')
            self.assertEqual(len(book['history']), 13)
        # The real close is counted like the synthetic ones; the marker keeps its evidence.
        evidence = lab.lifecycle.observed_evidence(
            self.control, activity_version=cf.ENTRY_POLICY_VERSION,
            execution_version=lab.EXECUTION_MODEL_VERSION, now=NOW)
        self.assertEqual(evidence['closed_trades'], 13)
        self.clock = NOW + 24 * 3_600_000
        lab.maybe_open([coin(updatedAt=self.clock)], verified_flows())
        for book in (self.control, self.scaled):
            self.assertIsNone(book['position'])
            self.assertEqual(book['entry_diagnostics']['blocked_reason'], 'strategy_retired_observed_losses')

    def test_pair_evidence_counts_only_its_own_entry_policy_version(self):
        for book in (self.control, self.scaled):
            book['history'] = self.losing_rows(book, 12, first_trade_no=1, version=activity.POLICY_VERSION)
        lab.review_strategy_lifecycle(self.books)
        for book in (self.control, self.scaled):
            marker = book['strategy_lifecycle']
            self.assertEqual(marker['status'], 'active')
            self.assertEqual(marker['evidence']['closed_trades'], 0)
            self.assertEqual(marker['evidence']['excluded_rows'], 12)


if __name__ == '__main__':
    unittest.main()
