import copy
import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
import funded_active_paper as active
import entry_defense
import structural_rug_guard as rug
import strategy_lab as lab
import funded_market_candidates as candidates
from lab_dashboard_projection import compact_strategy_lab
from tape_pool_scheduler import TapePoolScheduler, lab_pin_positions

NOW = 1_800_000_000_000
MINT = 'BrUimx7KncgRNTggAdZdaX2s5XUqQyR6XMRmEg4mpump'
PAIR = '8t34p7n94man8wcmFdHedYkJaWEhA9nKGJMLZyZUzbn'


def coin(index=0, **changes):
    return {'address': MINT[:-1] + str(index + 1), 'pairAddress': PAIR[:-1] + str(index + 1),
            'symbol': 'FIXTURE', 'priceUsd': .01, 'priceNative': .01 / 150,
            'quoteTokenAddress': active.SOL, 'dexId': 'pumpswap', 'liquidityUsd': 400_000,
            'marketCap': 10_000_000, 'updatedAt': NOW, 'score': 60, 'ageMinutes': 30,
            'pairCreatedAt': NOW - 30 * 60_000,
            'priceChange': {'m5': 2, 'h1': 5}, 'volume': {'h1': 60_000},
            'txns': {'m5': {'buys': 60, 'sells': 20}}, **changes}


def flows(rows):
    return {(c['address'], c['pairAddress']): {'verified_flow': {
        'source': lab.promoted_guard.FLOW_SOURCE, 'coverage_status': 'COMPLETE',
        'window_ms': 30_000, 'address': c['address'], 'pairAddress': c['pairAddress'],
        'window_at': NOW, 'available_at': NOW - 50, 'latest_event_at': NOW - 100,
        'trades': 4, 'unique_wallets': 3, 'buy_usd': 500, 'sell_usd': 100}} for c in rows}


class ActivePolicyTests(unittest.TestCase):
    def setUp(self):
        env = patch.dict(os.environ, {active.ENV: '1'})
        env.start(); self.addCleanup(env.stop)
        self.clock = NOW
        self.strategy = next(s for s in lab.STRATEGIES if s['id'] == 'EARLY')
        self.book = lab.empty_book(self.strategy)
        self.book['portfolio_group'] = 'PROMOTED_PAPER'
        self.book['history'] = [{'pnl_usd': -1, 'entry_policy_version': 'LEGACY', 'closed_at': NOW - 1}]
        self.state = {'books': {'EARLY': self.book}}
        self.layer = entry_defense.DefensiveEntryLayer()
        self.layer.registry = Mock()
        self.layer.registry.coverage_ms.return_value = 48 * 3_600_000
        self.layer.registry.reusers.return_value = []
        for p in (
            patch.object(lab, 'STATE', self.state), patch.object(lab, 'STRATEGIES', [self.strategy]),
            patch.object(lab, 'now_ms', side_effect=lambda: self.clock),
            patch.object(lab, 'entry_defense_layer', return_value=self.layer),
            patch.object(lab.rug_guard, 'check', side_effect=lambda c: {
                'status': 'pass', 'mint': c['address'], 'pair': c['pairAddress'], 'checked_at': self.clock}),
            patch.object(lab.price_integrity, 'check', side_effect=lambda c: {
                'status': 'pass', 'mint': c['address'], 'pair': c['pairAddress']}),
            patch.object(entry_defense.heat_veto, 'evaluate', return_value={
                'vetoed': False, 'log_only': False, 'reasons': [], 'metrics': {}}),
        ):
            p.start(); self.addCleanup(p.stop)

    def test_every_named_rule_has_a_structurally_reachable_age(self):
        for sid in active.RULES:
            c = coin()
            self.assertTrue(active.matches(sid, c), sid)
            result = rug.check(c, NOW, self.layer.registry, params=active.structural_parameters(sid))
            self.assertFalse(result['blocked'], (sid, result))
        self.assertIn('rug_young_pool', rug.check(coin(), NOW, self.layer.registry)['reasons'])

    def test_actual_loop_opens_early_without_rewriting_history_or_balance(self):
        before = copy.deepcopy(self.book)
        c = coin()
        lab.maybe_open([c], flows([c]))
        p = self.book['position']
        self.assertIsNotNone(p)
        self.assertEqual(p['entry_policy_version'], active.VERSION)
        self.assertEqual(p['notional_usd'], 25)
        self.assertEqual(p['exit_parameters']['max_hold_minutes'], 4)
        self.assertLess(p['open_pnl_usd'], 0)
        self.assertLess(p['entry_roundtrip_pnl_pct'], 0)
        for key in ('history', 'starting_balance', 'balance'):
            self.assertEqual(self.book[key], before[key])

    def test_all_four_books_use_the_same_admission_not_just_early(self):
        for sid in active.RULES:
            s = next(s for s in lab.STRATEGIES if s['id'] == sid) if sid == 'EARLY' else {'id': sid, 'name': sid}
            b = lab.empty_book(s); b['portfolio_group'] = 'PROMOTED_PAPER'
            with patch.object(lab, 'STATE', {'books': {sid: b}}), patch.object(lab, 'STRATEGIES', [s]):
                c = coin(); lab.maybe_open([c], flows([c]))
                self.assertIsNotNone(b['position'], sid)

    def test_four_slots_are_real_and_duplicate_mints_never_fill_them(self):
        rows = [coin(i) for i in range(5)]
        for _ in range(5):
            lab.maybe_open(rows, flows(rows))
        positions = active.positions(self.book)
        self.assertEqual(len(positions), 4)
        self.assertEqual(len({p['address'] for p in positions}), 4)
        self.assertEqual(active.capacity(self.book, NOW)['blocked_reason'], 'funded_active_slots_full')
        self.assertLessEqual(sum(p['remaining_cost_basis_usd'] for p in positions), 125)

    def test_full_legacy_position_consumes_exposure(self):
        self.book['position'] = {'notional_usd': 124, 'remaining_cost_basis_usd': 124,
                                 'address': 'old', 'open_pnl_usd': -1}
        c = coin(); lab.maybe_open([c], flows([c]))
        self.assertEqual(len(active.positions(self.book)), 1)
        self.assertEqual(self.book['entry_diagnostics']['blocked_reason'], 'funded_active_exposure_limit')

    def test_restart_deduplicates_alias_and_close_preserves_other_slots(self):
        rows = [coin(i) for i in range(2)]
        for _ in rows: lab.maybe_open(rows, flows(rows))
        restored = json.loads(json.dumps(self.book))
        self.assertEqual(len(active.positions(restored)), 2)
        active.synchronize_alias(restored)
        self.assertIs(restored['position'], restored['positions'][0])
        p = active.positions(restored)[1]
        lab.close_position(restored, p, rows[1], 'TEST')
        self.assertEqual(len(active.positions(restored)), 1)
        self.assertIs(restored['position'], restored['positions'][0])
        self.assertEqual(restored['history'][0]['trade_no'], p['trade_no'])

    def test_unrealized_pnl_uses_every_slot_and_projection_keeps_them(self):
        rows = [coin(i) for i in range(2)]
        for _ in rows: lab.maybe_open(rows, flows(rows))
        ps = active.positions(self.book); ps[0]['open_pnl_usd'] = -1; ps[1]['open_pnl_usd'] = -2
        st = lab.stats(self.book)
        self.assertEqual(st['unrealized_pnl'], -3)
        self.assertEqual(st['open_positions'], 2)
        view = compact_strategy_lab({'books': {'EARLY': self.book}, 'stats': {'EARLY': st}})
        self.assertEqual(len(view['books']['EARLY']['positions']), 2)
        self.assertEqual(view['stats']['EARLY']['funded_active']['trades'], 0)
        self.assertIsNone(view['stats']['EARLY']['funded_active']['win_rate'])

    def test_order_cap_counts_opens_and_closes_not_legacy_or_double_alias(self):
        self.book['history'] += [dict(entry_policy_version=active.VERSION, opened_at=NOW-1000,
                                     closed_at=NOW, pnl_usd=0) for _ in range(50)]
        c = coin(); lab.maybe_open([c], flows([c]))
        self.assertIsNone(self.book['position'])
        self.assertEqual(self.book['entry_diagnostics']['blocked_reason'], 'funded_active_hourly_order_limit')
        self.clock = NOW + 3_600_001
        self.assertEqual(active.capacity(self.book, self.clock)['orders_last_60m'], 0)

    def test_daily_loss_cap_includes_open_marks(self):
        self.book['position'] = {'open_pnl_usd': -12.5, 'notional_usd': 25}
        self.assertEqual(active.capacity(self.book, NOW)['blocked_reason'], 'funded_active_daily_loss_limit')

    def test_stale_held_position_prevents_more_entries(self):
        self.book['position']={'quote_status':'stale','notional_usd':25,'open_pnl_usd':10}
        self.assertEqual(active.capacity(self.book,NOW)['blocked_reason'],'funded_active_marks_unavailable')

    def test_missing_flow_or_failed_safety_or_price_never_enters(self):
        c = coin()
        lab.maybe_open([c], {})
        self.assertIsNone(self.book['position'])
        with patch.object(lab.rug_guard, 'check', return_value={'status': 'pending'}):
            lab.maybe_open([c], flows([c])); self.assertIsNone(self.book['position'])
        with patch.object(lab.price_integrity, 'check', return_value={'status': 'fail'}):
            lab.maybe_open([c], flows([c])); self.assertIsNone(self.book['position'])

    def test_lp_pullable_and_ticker_reuse_are_not_relaxed(self):
        c = coin(liquidityUsd=10_000_000)
        lab.maybe_open([c], flows([c])); self.assertIsNone(self.book['position'])
        self.layer.registry.reusers.return_value = ['other']
        c = coin(); lab.maybe_open([c], flows([c])); self.assertIsNone(self.book['position'])
        self.assertEqual(self.book['entry_diagnostics']['blocked_reason'], 'rug_ticker_reuse')

    def test_active_exits_manage_all_slots_and_survive_flag_off(self):
        rows = [coin(i) for i in range(2)]
        for _ in rows: lab.maybe_open(rows, flows(rows))
        self.clock += 240_001
        prices = [{**c, 'updatedAt': self.clock, 'mark_received_at': self.clock} for c in rows]
        with patch.dict(os.environ, {active.ENV: '0'}), patch.object(lab.POSITION_MARK_FEED, 'resolve',
                side_effect=lambda p, by_pair, now: by_pair[(p['address'], p['pairAddress'])]):
            lab.update_positions({}, prices)
        self.assertEqual(len(active.positions(self.book)), 0)
        closes = active.current_trades(self.book)
        self.assertEqual(len(closes), 2)
        self.assertTrue(all(t['exit_reason'] == 'FUNDED_ACTIVE_MAX_HOLD_4' for t in closes))
        self.assertTrue(all(t['pnl_usd'] < 0 for t in closes))

    def test_stale_mark_never_closes_an_active_position(self):
        c = coin(); lab.maybe_open([c], flows([c])); self.clock += 300_000
        with patch.object(lab.POSITION_MARK_FEED, 'resolve', return_value=c):
            lab.update_positions({}, [c])
        self.assertIsNotNone(self.book['position'])
        self.assertEqual(len(active.current_trades(self.book)), 0)

    def test_net_trail_and_gaps_use_frozen_parameters_without_clamping(self):
        p = {'exit_parameters': active.exit_parameters('EARLY'), 'peak_net_pct': 3.5}
        self.assertEqual(active.exit_reason(p, 2.4, 1), 'FUNDED_ACTIVE_PROFIT_TRAIL_NET')
        self.assertEqual(active.exit_reason(p, -30, 1), 'FUNDED_ACTIVE_STOP_NET')

    def test_scheduler_observes_young_eligible_pool_and_no_flow_free_exit_pins(self):
        scheduler = TapePoolScheduler(); scheduler.defense = self.layer
        c = coin()
        decision = scheduler.defensive_entry_decision(c, NOW)
        self.assertTrue(decision['allowed'])
        lab.maybe_open([c], flows([c]))
        pinned, unpinned = lab_pin_positions({'strategy_lab': self.state})
        self.assertEqual(pinned, []); self.assertEqual(unpinned, 1)
        selected, _ = scheduler.select({'feed': [c]}, now=NOW, max_tracked=1)
        self.assertEqual(selected, [c])

    def test_disabled_mode_keeps_legacy_candidate_policy(self):
        c = coin()
        with patch.dict(os.environ, {active.ENV: '0'}):
            self.assertEqual(candidates.matched_branches('EARLY', c, lab.enrich(c, {})), [])


if __name__ == '__main__':
    unittest.main()
