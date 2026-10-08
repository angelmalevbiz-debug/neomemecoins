import unittest
from unittest.mock import patch

import strategy_lab as lab
from lab_dashboard_projection import compact_strategy_lab
import entry_defense as _isolation_entry_defense
import strategy_lab as _isolation_lab

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
    for target, name in ((_isolation_lab, 'defensive_entry_decision'),):
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


MINT = 'BrUimx7KncgRNTggAdZdaX2s5XUqQyR6XMRmEg4mpump'
PAIR = '8t34p7n94man8wcmFdHedYkJaWEhA9nKGJMLZyZUzbn'


def signal(now):
    return {
        'address': MINT, 'pairAddress': PAIR, 'priceUsd': 1.0,
        'updatedAt': now - 500,
    }


def review(price=1.0):
    return {
        'status': 'review', 'reason': 'price_crosscheck_pending_needs_jupiter',
        'observed_price': price, 'mint': MINT, 'pair': PAIR,
    }


def probe(now, *, quoted_at=None, mint=MINT, pair=PAIR, decimals=9, price=1.01):
    # $10 buys about 9.90099 units from a read-only Jupiter route.
    raw = int((10.0 / price) * (10 ** decimals))
    return {
        'mint': mint, 'pair': pair, 'decimals': decimals,
        'quote': {
            'input_mint': lab.paper_quotes.USDC_MINT,
            'output_mint': mint,
            'quoted_at': now - 100 if quoted_at is None else quoted_at,
            'input_usdc_raw': 10_000_000,
            'token_raw_amount': raw,
        },
    }


class LabJupiterCrosscheckTests(unittest.TestCase):
    def call_with_probe(self, validation, coin, quote, now):
        with patch.object(lab, 'LAB_PRICE_PROBE_CACHE', {(MINT, PAIR): quote}):
            return lab.cached_jupiter_tiebreak(validation, coin, now=now)

    def test_fresh_same_token_route_can_resolve_gecko_pending_review(self):
        now = lab.now_ms()
        result = self.call_with_probe(review(), signal(now), probe(now), now)
        self.assertEqual(result['status'], 'pass')
        self.assertTrue(result['jupiter_tiebreak'])
        self.assertLessEqual(result['jupiter_vs_observed_pct'], 3.0)

    def test_too_different_quote_remains_blocked(self):
        now = lab.now_ms()
        result = self.call_with_probe(review(), signal(now), probe(now, price=1.08), now)
        self.assertEqual(result['status'], 'blocked')
        self.assertEqual(result['reason'], 'price_tiebreak_failed')

    def test_stale_or_future_quote_cannot_resolve_review(self):
        now = lab.now_ms()
        old = probe(now, quoted_at=now - lab.LAB_PRICE_PROBE_TTL_MS - 1)
        future = probe(now, quoted_at=now + 1_000)
        self.assertEqual(self.call_with_probe(review(), signal(now), old, now)['status'], 'review')
        self.assertEqual(self.call_with_probe(review(), signal(now), future, now)['status'], 'review')

    def test_quote_must_follow_signal_and_match_mint_and_pair_identity(self):
        now = lab.now_ms()
        coin = signal(now)
        coin['updatedAt'] = now + 1
        self.assertEqual(self.call_with_probe(review(), coin, probe(now), now)['status'], 'review')
        wrong_pair = probe(now, pair='7vXSFb9tUBL4qAEmJ3FXcG6mUzrr2TpnPHwyS9q1EcGS')
        self.assertEqual(self.call_with_probe(review(), signal(now), wrong_pair, now)['status'], 'review')

    def test_background_probe_accepts_any_valid_route_for_the_guarded_token(self):
        now = lab.now_ms()
        coin = signal(now)
        fresh_quote = {
            'inputMint': lab.paper_quotes.USDC_MINT, 'outputMint': MINT,
            'inAmount': '10000000', 'outAmount': str(int(10 / 1.01 * 10**9)),
            '_received_at': now - 100,
            'routePlan': [{'swapInfo': {
                'ammKey': 'another-valid-pool',
                'inputMint': lab.paper_quotes.USDC_MINT, 'outputMint': MINT,
            }}],
        }
        cache = {}

        class ImmediateThread:
            def __init__(self, target, **_kwargs):
                self.target = target

            def start(self):
                self.target()

        with patch.object(lab, 'LAB_PRICE_PROBE_CACHE', cache), \
             patch.object(lab, 'LAB_PRICE_PROBE_RETRY_AFTER', {}), \
             patch.object(lab, 'LAB_PRICE_PROBE_INFLIGHT', False), \
             patch.object(lab.rug_guard, 'check', return_value={
                 'status': 'pass', 'mint': MINT, 'pair': PAIR,
                 'metrics': {'decimals': 9},
             }), \
             patch.object(lab.paper_quotes, 'quote', return_value=fresh_quote) as quote_call, \
             patch.object(lab.threading, 'Thread', ImmediateThread):
            self.assertTrue(lab.schedule_jupiter_price_probe(coin))
            result = lab.cached_jupiter_tiebreak(review(), coin, now=now)

        quote_call.assert_called_once_with(lab.paper_quotes.USDC_MINT, MINT, 10_000_000,
                                           purpose='background')
        self.assertEqual(cache[(MINT, PAIR)]['quote']['route_amm_keys'], ['another-valid-pool'])
        self.assertEqual(result['status'], 'pass')

    def test_strategy_table_shows_marked_total_and_realized_results(self):
        book = {
            'starting_balance': 100.0, 'balance': 102.5, 'history': [],
            'position': {'open_pnl_usd': -3.0, 'notional_usd': 20.0,
                         'mark_received_at': lab.now_ms()},
        }
        result = lab.stats(book)
        self.assertEqual(result['realized_pnl'], 2.5)
        self.assertEqual(result['unrealized_pnl'], -3.0)
        self.assertEqual(result['total_pnl'], -0.5)
        self.assertEqual(result['equity'], 99.5)

    def test_compact_dashboard_preserves_per_strategy_entry_blockers(self):
        state = compact_strategy_lab({'books': {'MOMENTUM': {
            'entry_diagnostics': {
                'signal_candidates': 2, 'price_crosscheck_pending': 1,
                'cost_rejected': 1, 'flow_missing_candidates': 4,
            },
        }}})
        self.assertEqual(state['books']['MOMENTUM']['entry_diagnostics'], {
            'signal_candidates': 2, 'price_crosscheck_pending': 1,
            'cost_rejected': 1, 'flow_missing_candidates': 4,
        })

    def test_fresh_jupiter_tiebreak_is_consumed_by_each_matching_paper_strategy(self):
        now = lab.now_ms()
        coin = {
            'address': MINT, 'pairAddress': PAIR, 'symbol': 'TEST', 'name': 'Test',
            'priceUsd': 0.001, 'liquidityUsd': 100_000, 'marketCap': 500_000,
            'priceNative': 0.001 / 150, 'quoteTokenAddress': lab.SOL_QUOTE_MINT,
            'updatedAt': now, 'score': 100, 'dexId': 'raydium', 'ageMinutes': 30,
            'priceChange': {'m5': 10, 'h1': 20},
            'volume': {'h1': 100_000},
            'txns': {'m5': {'buys': 20, 'sells': 10}},
        }
        books = {strategy['id']: lab.empty_book(strategy) for strategy in lab.STRATEGIES}
        state = {'books': books, 'portfolio_setup': {'status': 'ACTIVE'}}
        confirmed = {**review(), 'status': 'pass', 'reason': '', 'jupiter_tiebreak': True,
                     'jupiter_entry_price': coin['priceUsd']}
        import promoted_entry_guard as promoted_guard
        verified={'source':promoted_guard.FLOW_SOURCE,'coverage_status':'COMPLETE',
                  'window_ms':promoted_guard.FLOW_WINDOW_MS,'address':MINT,'pairAddress':PAIR,
                  'window_at':now,'latest_event_at':now-100,'available_at':now-50,
                  'trades':4,'unique_wallets':3,'buy_usd':500,'sell_usd':100}
        flows={(MINT,PAIR):{'verified_flow':verified}}
        risk={'status':'pass','mint':MINT,'pair':PAIR,'checked_at':now}
        with patch.object(lab, 'STATE', state), \
             patch.object(lab.price_integrity, 'check', return_value=review()), \
             patch.object(lab, 'cached_jupiter_tiebreak', return_value=confirmed):
            with patch.object(lab.rug_guard, 'check', return_value=risk):
                lab.maybe_open([coin], flows)

        for strategy_id in ('EARLY', 'MOMENTUM', 'PRECISION', 'ULTRA_PRECISION'):
            position = state['books'][strategy_id]['position']
            self.assertIsNotNone(position, strategy_id)
            self.assertTrue(position['price_crosscheck']['jupiter_tiebreak'])
        self.assertIsNone(state['books']['ORDER_FLOW']['position'])


if __name__ == '__main__':
    unittest.main()
