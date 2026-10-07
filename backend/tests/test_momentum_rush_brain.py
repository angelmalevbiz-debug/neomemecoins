import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import lab_activity as activity
import momentum_rush_brain as brain
import strategy_lab as lab


NOW = 1_800_000_000_000
MINT = 'So11111111111111111111111111111111111111112'
PAIR = 'EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v'
OTHER_PAIR = '7pQvPNLa7s8kN9uUq47XkbKzA9a7JwzsHjFeNFK2M4eH'


def coin(stamp=NOW, price=1.02, **changes):
    return {
        'address': MINT, 'pairAddress': PAIR, 'symbol': 'RESEARCH',
        'name': 'Research fixture', 'priceUsd': price, 'priceNative': price / 150,
        'liquidityUsd': 12_000, 'marketCap': 40_000, 'score': 86,
        'ageMinutes': 20, 'updatedAt': stamp, 'dexId': 'raydium',
        'priceChange': {'m5': 8, 'h1': 30}, 'volume': {'h1': 20_000},
        'txns': {'m5': {'buys': 40, 'sells': 10}}, **changes,
    }


FLOW = {'trades': 8, 'buys': 6, 'sells': 2, 'ratio': 4, 'buy_usd': 800,
        'sell_usd': 100, 'unique_wallets': 6, 'max_sell': 50}


def features(c=None):
    return activity.market_features(c or coin(), copy.deepcopy(FLOW))


def own_trade(sequence, pnl=10):
    return {
        'trade_no': sequence, 'strategy_id': brain.STRATEGY_ID,
        'address': MINT, 'pairAddress': PAIR, 'opened_at': NOW - 100_000 - sequence * 100,
        'closed_at': NOW - sequence * 100, 'notional_usd': 100,
        'pnl_usd': pnl, 'entry_features': features(), 'exit_reason': 'TAKE_PROFIT_10_NET',
    }


class CausalBrainTests(unittest.TestCase):
    def setUp(self):
        brain._SAMPLE_BY_PAIR.clear()

    def sample(self, stamp, price, **changes):
        c = coin(stamp, price, **changes)
        return brain.evaluate({'id': brain.STRATEGY_ID}, c, features(c), stamp)

    def accelerate(self):
        self.sample(NOW - 16_000, 1)
        self.sample(NOW - 8_000, 1.005)
        return self.sample(NOW, 1.02)

    def test_actual_acceleration_requires_three_distinct_available_snapshots(self):
        first = self.sample(NOW - 16_000, 1)
        second = self.sample(NOW - 8_000, 1.005)
        self.assertFalse(first['allow'])
        self.assertFalse(second['allow'])
        result = self.sample(NOW, 1.02)
        self.assertTrue(result['allow'])
        self.assertTrue(result['temporal']['acceleration_confirmed'])
        self.assertGreater(result['temporal']['price_acceleration_pct_per_min2'], 0)
        self.assertEqual(result['target_win_rate_pct'], 80)
        self.assertFalse(result['target_is_guarantee'])

    def test_repeat_polls_cannot_manufacture_samples_or_rewrite_snapshot(self):
        c = coin(NOW, 1)
        for delay in (0, 8_000, 16_000):
            result = brain.evaluate({'id': brain.STRATEGY_ID}, {**c, 'priceUsd': 1 + delay / 100_000},
                                    features(c), NOW + delay)
            self.assertEqual(result['temporal']['samples'], 1)
            self.assertFalse(result['allow'])
        self.assertEqual(brain._SAMPLE_BY_PAIR[(MINT, PAIR)][0]['price'], 1)

    def test_future_stale_and_out_of_order_snapshots_do_not_enter_memory(self):
        for stamp in (NOW + 1, NOW - brain.MAX_OBSERVATION_AGE_MS - 1):
            result = brain.observe(coin(stamp), features(), NOW)
            self.assertFalse(result['ready'])
            self.assertEqual(len(brain._SAMPLE_BY_PAIR), 0)
        self.sample(NOW, 1)
        result = brain.observe(coin(NOW - 1), features(), NOW)
        self.assertEqual(result['reason'], 'snapshot_timestamp_regression')
        self.assertEqual(len(brain._SAMPLE_BY_PAIR[(MINT, PAIR)]), 1)

    def test_prior_future_observation_is_not_used_in_an_earlier_decision(self):
        self.accelerate()
        result = brain.observe(coin(NOW - 8_000, 1.005), features(), NOW - 8_000)
        self.assertFalse(result['ready'])

    def test_decelerating_or_dumping_price_is_not_confirmed(self):
        self.sample(NOW - 16_000, 1)
        self.sample(NOW - 8_000, 1.01)
        result = self.sample(NOW, 1.012)
        self.assertFalse(result['allow'])
        self.assertLess(result['temporal']['price_acceleration_pct_per_min2'], 0)
        result = self.sample(NOW + 8_000, .97)
        self.assertTrue(result['temporal']['veto'])
        self.assertFalse(result['allow'])

    def test_exact_pool_isolation_and_expiry(self):
        self.accelerate()
        other = brain.observe(coin(pairAddress=OTHER_PAIR), features(), NOW)
        self.assertEqual(other['samples'], 1)
        self.assertFalse(other['ready'])
        expired_at = NOW + brain.SAMPLE_RETENTION_MS + 1
        expired = brain.observe(coin(expired_at), features(), expired_at)
        self.assertEqual(expired['samples'], 1)
        self.assertFalse(expired['ready'])

    def test_observation_cache_has_both_pair_and_per_pair_bounds(self):
        for i in range(brain.MAX_TRACKED_PAIRS + 20):
            brain.observe(coin(pairAddress=f'pool-{i}'), features(), NOW)
        self.assertEqual(len(brain._SAMPLE_BY_PAIR), brain.MAX_TRACKED_PAIRS)
        for i in range(brain.MAX_SAMPLES_PER_PAIR + 20):
            stamp = NOW + i * 1_000
            brain.observe(coin(stamp), features(), stamp)
        self.assertEqual(len(brain._SAMPLE_BY_PAIR[(MINT, PAIR)]), brain.MAX_SAMPLES_PER_PAIR)

    def test_memory_is_bounded_to_own_completed_net_trades(self):
        own = [{**own_trade(i), 'net_pnl_usd': -10} for i in range(1, 101)]
        invalid = [
            {**own_trade(201), 'closed_at': NOW + 1},
            {**own_trade(202), 'strategy_id': 'MOMENTUM'},
            {**own_trade(203), 'is_partial': True},
            {**own_trade(204), 'pnl_usd': None, 'gross_pnl_usd': 100},
            {**own_trade(205), 'opened_at': NOW},
            {**own_trade(206), 'notional_usd': 0},
        ]
        result = brain._memory_intelligence({'id': brain.STRATEGY_ID, 'history': invalid + own}, features(), NOW)
        self.assertEqual(result['closed_samples'], brain.MAX_MEMORY_TRADES)
        self.assertEqual(result['effective_sample'], brain.MAX_MEMORY_TRADES)
        self.assertEqual(result['avg_similar_pnl_pct'], -10)
        self.assertLess(result['posterior_win_rate'], .1)
        self.assertLess(result['score'], .5)
        self.assertEqual(result['basis'], 'OWN_CLOSED_NET_PAPER_TRADES')

    def test_final_net_pnl_already_includes_partial_legs_and_duplicates_do_not_vote(self):
        trades = [{**own_trade(i, 3), 'partial_realized_pnl': 5,
                   'partial_exits': [{'pnl_usd': 5}]} for i in range(1, 11)]
        result = brain._memory_intelligence({'id': brain.STRATEGY_ID, 'history': trades + copy.deepcopy(trades)}, features(), NOW)
        self.assertEqual(result['closed_samples'], 10)
        self.assertEqual(result['effective_sample'], 10)
        self.assertEqual(result['avg_similar_pnl_pct'], 3)
        other = brain._memory_intelligence({'id': 'MOMENTUM', 'history': trades}, features(), NOW)
        self.assertEqual(other['effective_sample'], 0)

    def test_long_ledger_scan_is_bounded_without_mutating_history(self):
        class GuardedHistory(list):
            visited = 0

            def __iter__(self):
                for row in super().__iter__():
                    self.visited += 1
                    if self.visited > brain.MAX_MEMORY_SCAN_ROWS:
                        raise AssertionError('memory scanned past its decision budget')
                    yield row

        rows = [own_trade(i, -10) for i in range(1, brain.MAX_MEMORY_SCAN_ROWS + 1)]
        rows[0]['closed_at'] = NOW + 1
        history = GuardedHistory(rows + [own_trade(i, 100) for i in range(1000, 3000)])
        result = brain._memory_intelligence({'id': brain.STRATEGY_ID, 'history': history}, features(), NOW)
        self.assertEqual(history.visited, brain.MAX_MEMORY_SCAN_ROWS)
        self.assertEqual(len(history), brain.MAX_MEMORY_SCAN_ROWS + 2000)
        self.assertEqual(result['closed_samples'], brain.MAX_MEMORY_TRADES)
        self.assertEqual(result['avg_similar_pnl_pct'], -10)

    def test_bearish_verified_flow_and_unsafe_low_cap_are_still_vetoed(self):
        self.accelerate()
        c = coin()
        bearish = features(c)
        bearish['flow'].update(ratio=.5, sell_usd=2_000)
        self.assertFalse(brain.evaluate({'id': brain.STRATEGY_ID}, c, bearish, NOW)['allow'])
        bad = coin(marketCap=100_000, liquidityUsd=3_000)
        self.assertFalse(brain.evaluate({'id': brain.STRATEGY_ID}, bad, features(bad), NOW)['allow'])

    def test_high_score_never_increases_hard_low_cap_risk_limits(self):
        meta = {'market_cap_usd': 50_000, 'final_score': .99}
        self.assertEqual(brain.candidate_notional_limit(meta, 100, 150), 12)
        self.assertEqual(brain.candidate_notional_limit(meta, 500, 150), 60)
        self.assertEqual(brain.candidate_notional_limit(meta, 100, 5), 5)
        self.assertEqual(brain.candidate_notional_limit({**meta, 'market_cap_usd': 100_000}, 100, 150), 20)
        self.assertEqual(brain.candidate_notional_limit({**meta, 'market_cap_usd': 100_001}, 100, 150), 35)


class RushRunnerTests(unittest.TestCase):
    def setUp(self):
        brain._SAMPLE_BY_PAIR.clear()
        self.books = {s['id']: lab.empty_book(s) for s in lab.STRATEGIES}
        for key, book in self.books.items():
            if key != brain.STRATEGY_ID:
                book['position'] = {'fixture': 'existing position preserved'}
        self.book = self.books[brain.STRATEGY_ID]
        state_patch = patch.object(lab, 'STATE', {'books': self.books})
        state_patch.start(); self.addCleanup(state_patch.stop)
        self.guard = patch.object(lab.rug_guard, 'check', return_value={
            'status': 'pass', 'mint': MINT, 'pair': PAIR, 'checked_at': NOW - 1,
        })
        self.guard.start(); self.addCleanup(self.guard.stop)
        price_patch = patch.object(lab.price_integrity, 'check', return_value={
            'status': 'pass', 'mint': MINT, 'pair': PAIR,
        })
        price_patch.start(); self.addCleanup(price_patch.stop)

    def warmup(self):
        for stamp, price in ((NOW - 16_000, 1), (NOW - 8_000, 1.005)):
            with patch.object(lab, 'now_ms', return_value=stamp):
                lab.maybe_open([coin(stamp, price)], {(MINT, PAIR): copy.deepcopy(FLOW)})
        self.assertIsNone(self.book['position'])

    def open(self, c=None):
        with patch.object(lab, 'now_ms', return_value=NOW):
            lab.maybe_open([c or coin()], {(MINT, PAIR): copy.deepcopy(FLOW)})

    def test_registered_runnable_test_book_opens_and_keeps_promoted_books_independent(self):
        self.assertEqual(set(activity.RULES), {s['id'] for s in lab.STRATEGIES})
        self.assertNotIn(brain.STRATEGY_ID, lab.PROMOTED_STRATEGIES)
        before = {key: copy.deepcopy(self.books[key]) for key in lab.PROMOTED_STRATEGIES}
        self.warmup(); self.open()
        position = self.book['position']
        self.assertIsNotNone(position)
        self.assertEqual(self.book['portfolio_group'], 'TEST')
        self.assertEqual(self.book['max_position_fraction'], activity.RUSH_MAX_BALANCE_FRACTION)
        self.assertEqual(position['strategy_id'], brain.STRATEGY_ID)
        self.assertFalse(position['promotion_eligible'])
        self.assertTrue(position['momentum_rush_brain']['allow'])
        self.assertLessEqual(position['notional_usd'], 60)
        self.assertGreaterEqual(position['entry_roundtrip_pnl_pct'], -activity.MAX_ENTRY_COST_PCT)
        self.assertLess(position['open_pnl_usd'], 0)
        self.assertEqual(self.book['balance'], self.book['starting_balance'])
        self.assertEqual(before, {key: self.books[key] for key in lab.PROMOTED_STRATEGIES})

    def test_observations_continue_while_rush_position_is_open(self):
        self.book['position'] = {'fixture': 'existing position'}
        self.warmup_without_position_assertion()
        self.assertEqual(len(brain._SAMPLE_BY_PAIR[(MINT, PAIR)]), 2)
        self.assertEqual(self.book['position'], {'fixture': 'existing position'})

    def warmup_without_position_assertion(self):
        for stamp, price in ((NOW - 16_000, 1), (NOW - 8_000, 1.005)):
            with patch.object(lab, 'now_ms', return_value=stamp):
                lab.maybe_open([coin(stamp, price)], {(MINT, PAIR): copy.deepcopy(FLOW)})

    def test_future_feed_snapshot_cannot_open_a_rush_trade(self):
        self.warmup(); self.open(coin(NOW + 1))
        self.assertIsNone(self.book['position'])
        self.assertEqual(self.book['entry_diagnostics']['blocked_reason'], 'temporal_warmup')

    def test_risk_guard_must_be_fresh_and_bound_to_the_exact_pool(self):
        self.warmup()
        for changes in ({'status': 'pending'}, {'pair': OTHER_PAIR},
                        {'checked_at': NOW - lab.rug_guard.TTL_MS - 1}, {'checked_at': NOW + 1}):
            result = {'status': 'pass', 'mint': MINT, 'pair': PAIR, 'checked_at': NOW - 1, **changes}
            with patch.object(lab.rug_guard, 'check', return_value=result):
                self.open()
            self.assertIsNone(self.book['position'])
            self.assertEqual(self.book['entry_diagnostics']['risk_rejected_candidates'], 1)

    def test_price_crosscheck_must_match_pool_and_missing_or_costly_quotes_block(self):
        self.warmup()
        with patch.object(lab.price_integrity, 'check', return_value={'status': 'pass', 'mint': MINT, 'pair': OTHER_PAIR}):
            self.open()
        self.assertIsNone(self.book['position'])
        with patch.object(lab, 'entry_execution', return_value={
            'network_fee_usd': 1000, 'capital_committed_usd': 1000, 'quantity': 0,
        }):
            self.open()
        self.assertIsNone(self.book['position'])
        self.assertEqual(self.book['entry_diagnostics']['cost_rejected'], 1)
        self.assertEqual(self.book['entry_diagnostics']['blocked_reason'], 'modeled_roundtrip_cost_limit')

    def test_unclamped_net_loss_closes_and_loss_reentry_cooldown_applies(self):
        self.warmup(); self.open()
        position = self.book['position']
        basis = position['remaining_cost_basis_usd']
        quote = {'fill_price': .9, 'net_proceeds_usd': basis - 6,
                 'dex_fee_usd': .3, 'network_fee_usd': .01,
                 'impact_pct': .1, 'slippage_pct': .1, 'latency_pct': .1}
        with patch.object(lab, 'exit_execution', return_value=quote), patch.object(lab, 'now_ms', return_value=NOW):
            lab.close_position(self.book, position, coin(), 'STOP_LOSS_3_NET')
        self.assertEqual(self.book['history'][0]['pnl_usd'], -6)
        self.assertEqual(self.book['balance'], self.book['starting_balance'] - 6)
        self.assertEqual(activity.cooldown_remaining_ms(self.book, MINT, NOW + 1), 89_999)
        self.open()
        self.assertIsNone(self.book['position'])
        self.assertEqual(self.book['entry_diagnostics']['cooldown_rejected'], 1)

    def test_adding_rush_book_preserves_saved_portfolios_and_trade_ledger(self):
        saved = {key: copy.deepcopy(book) for key, book in self.books.items() if key != brain.STRATEGY_ID}
        saved['EARLY'].update(balance=212, history=[{'pnl_usd': -38}], trade_seq=7)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'strategy_lab.json'
            path.write_text(json.dumps({'started_at': 42, 'books': saved,
                                       'portfolio_setup': {'version': 'PROMOTED_PAPER_COHORT_V1', 'status': 'ACTIVE'}}))
            with patch.object(lab, 'STATE_PATH', path), patch.object(lab, 'RESET_FLAG_PATH', Path(directory) / 'absent'):
                loaded = lab.load_state()
        self.assertEqual(loaded['started_at'], 42)
        for key, book in saved.items():
            for field in ('balance', 'history', 'trade_seq', 'position', 'starting_balance'):
                self.assertEqual(loaded['books'][key][field], book[field])
        self.assertEqual(loaded['books'][brain.STRATEGY_ID]['portfolio_group'], 'TEST')

    def test_flow_tape_uses_only_available_events_from_the_exact_pool(self):
        base = {'event_time': NOW - 1000, 'observed_at': NOW - 900, 'available_at': NOW - 800,
                'confirmed_swap': True, 'quality_flags': [], 'address': MINT,
                'pairAddress': PAIR, 'direction': 'BUY', 'wallet': 'buyer', 'usd_amount': 200}
        tape = {'pair_coverage': {PAIR: {'status': 'COMPLETE'}, OTHER_PAIR: {'status': 'COMPLETE'}},
                'events': [base, {**base, 'pairAddress': OTHER_PAIR, 'direction': 'SELL', 'usd_amount': 1000},
                           {**base, 'available_at': NOW + 1}, {**base, 'direction': 'UNKNOWN'}]}
        with patch.object(lab, 'load_json', return_value=tape), patch.object(lab, 'now_ms', return_value=NOW):
            flows = lab.flow_map()
        exact = lab.enrich(coin(), flows)['flow']
        other = lab.enrich(coin(pairAddress=OTHER_PAIR), flows)['flow']
        self.assertEqual(exact['trades'], 1)
        self.assertEqual(exact['buy_usd'], 200)
        self.assertEqual(exact['sell_usd'], 0)
        self.assertEqual(other['buy_usd'], 0)
        self.assertEqual(other['sell_usd'], 1000)


class RushExitTests(unittest.TestCase):
    def setUp(self):
        self.book = {'id':brain.STRATEGY_ID,'balance':500,'history':[], 'position':{
            'strategy_id':brain.STRATEGY_ID,'address':MINT,'pairAddress':PAIR,
            'entry_price':1,'entry_liquidity_usd':12000,'peak_price':1,
            'notional_usd':100,'remaining_cost_basis_usd':100,'quantity':100,
            'opened_at':NOW-60000,'updated_at':NOW-1000,'partial_realized_pnl':0}}
        state = patch.object(lab,'STATE',{'books':{'RUSH':self.book}})
        state.start();self.addCleanup(state.stop)
        clock = patch.object(lab,'now_ms',return_value=NOW)
        clock.start();self.addCleanup(clock.stop)

    def mark(self, pnl=0, *, mark=None, flow=None):
        mark = coin(price=1.2, liquidityUsd=12000) if mark is None else mark
        quote = {'fill_price':1.2,'net_proceeds_usd':100+pnl,'dex_fee_usd':.3,
                 'network_fee_usd':.01,'impact_pct':.1,'slippage_pct':.1,'latency_pct':.1}
        with patch.object(lab.POSITION_MARK_FEED,'resolve',return_value=mark), \
             patch.object(lab,'exit_execution',return_value=quote) as execute:
            lab.update_positions(flow or {}, [mark])
        return execute

    def reason(self):
        return self.book['history'][0]['exit_reason'] if self.book['history'] else None

    def test_net_take_profit_and_20_minute_max_hold(self):
        self.mark(7)
        self.assertEqual(self.reason(),'RUSH_TAKE_PROFIT_7_NET')
        self.assertEqual(self.book['history'][0]['pnl_usd'],7)
        self.setUp()
        self.book['position']['opened_at']=NOW-20*60000
        self.mark(0)
        self.assertEqual(self.reason(),'RUSH_MAX_HOLD_20')

    def test_net_stop_takes_priority_over_collapse_without_loss_clamp(self):
        self.mark(-10,mark=coin(liquidityUsd=6000))
        self.assertEqual(self.reason(),'STOP_LOSS_3_NET')
        self.assertEqual(self.book['history'][0]['pnl_usd'],-10)
        self.assertEqual(self.book['balance'],490)

    def test_fresh_liquidity_collapse_exits_but_unknown_liquidity_is_not_zero(self):
        mark=coin();del mark['liquidityUsd']
        self.mark(0,mark=mark)
        self.assertIsNotNone(self.book['position'])
        self.mark(0,mark=coin(liquidityUsd=7799))
        self.assertEqual(self.reason(),'RUSH_LIQUIDITY_COLLAPSE')

    def test_trailing_uses_net_peak_and_accounts_for_costs(self):
        self.mark(6)
        self.assertEqual(self.book['position']['peak_net_pct'],6)
        self.mark(3.5)
        self.assertEqual(self.reason(),'RUSH_PROFIT_TRAIL')
        self.assertEqual(self.book['history'][0]['pnl_usd'],3.5)

    def test_only_fresh_same_pool_flow_can_trigger_reversal(self):
        bearish={'trades':4,'ratio':.5,'sell_usd':200,'buy_usd':100,
                 'window_at':NOW,'available_at':NOW-1}
        for flow in ({(MINT,OTHER_PAIR):bearish}, {MINT:bearish},
                     {(MINT,PAIR):{**bearish,'available_at':NOW+1}},
                     {(MINT,PAIR):{**bearish,'window_at':NOW-60001}}):
            self.mark(0,flow=flow)
            self.assertIsNotNone(self.book['position'])
        self.mark(0,flow={(MINT,PAIR):bearish})
        self.assertEqual(self.reason(),'RUSH_FLOW_REVERSAL')

    def test_unavailable_stale_future_and_other_pool_marks_cannot_close(self):
        for mark in (coin(NOW-12001),coin(NOW+1),coin(pairAddress=OTHER_PAIR)):
            execute=self.mark(-10,mark=mark)
            execute.assert_not_called()
            self.assertIsNotNone(self.book['position'])
            self.assertEqual(self.book['history'],[])
            self.assertNotIn('peak_net_pct',self.book['position'])

    def test_other_books_keep_standard_exit_framework(self):
        self.book['id']='MOMENTUM'
        self.book['position'].update(strategy_id='MOMENTUM',opened_at=NOW-20*60000,
                                     peak_net_pct=20)
        self.mark(7,mark=coin(liquidityUsd=1000))
        self.assertIsNotNone(self.book['position'])
        self.mark(10)
        self.assertEqual(self.reason(),'TAKE_PROFIT_10_NET')


if __name__ == '__main__':
    unittest.main()
