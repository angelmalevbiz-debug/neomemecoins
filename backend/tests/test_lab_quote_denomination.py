import copy
import unittest
from unittest.mock import patch

import strategy_lab as lab
import promoted_entry_guard as promoted_guard


NOW = 1_800_000_000_000
MINT = 'BrUimx7KncgRNTggAdZdaX2s5XUqQyR6XMRmEg4mpump'
PAIR = '8t34p7n94man8wcmFdHedYkJaWEhA9nKGJMLZyZUzbn'
USDC = 'EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v'
USDT = 'Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB'


def coin(**changes):
    return {
        'address': MINT, 'pairAddress': PAIR, 'symbol': 'TEST',
        'priceUsd': .01, 'priceNative': .01 / 150,
        'quoteTokenAddress': lab.SOL_QUOTE_MINT,
        'marketCap': 50_000, 'liquidityUsd': 1_000_000,
        'dexId': 'raydium', 'updatedAt': NOW, 'score': 100,
        'ageMinutes': 20, 'priceChange': {'m5': 10, 'h1': 20},
        'volume': {'h1': 100_000}, 'txns': {'m5': {'buys': 20, 'sells': 10}},
        **changes,
    }


class LabQuoteDenominationTests(unittest.TestCase):
    def setUp(self):
        self.book = lab.empty_book({'id': 'EARLY', 'name': 'Early'})
        self.book['history'] = [{'trade_no': 1, 'address': 'prior', 'pnl_usd': 2}]
        self.book['trade_seq'] = 1
        books = {strategy['id']: lab.empty_book(strategy) for strategy in lab.STRATEGIES}
        books['EARLY'] = self.book
        for key, book in books.items():
            if key != 'EARLY':
                book['position'] = {'preserved': key}
        for name, value in {'STATE': {'books': books}, 'now_ms': lambda: NOW}.items():
            mock = patch.object(lab, name, value)
            mock.start(); self.addCleanup(mock.stop)

    def test_explicit_sol_ratio_values_network_and_pumpswap_fees(self):
        c = coin(dexId='pumpswap')
        self.assertAlmostEqual(lab.sol_usd_from_coin(c), 150)
        self.assertEqual(lab.pumpswap_fee_bps(c), 125)
        opening = lab.entry_execution(c, 50)
        closing = lab.exit_execution(c, opening['quantity'])
        self.assertAlmostEqual(opening['network_fee_usd'], lab.NETWORK_FEE_SOL * 150)
        self.assertAlmostEqual(closing['network_fee_usd'], lab.NETWORK_FEE_SOL * 150)
        self.assertLess(closing['net_proceeds_usd'], opening['capital_committed_usd'])

    def test_raw_dex_quote_identity_supports_position_marks_and_checks_conflicts(self):
        raw = coin(quoteTokenAddress=None, quoteToken={'address': lab.SOL_QUOTE_MINT})
        self.assertAlmostEqual(lab.sol_usd_from_coin(raw), 150)
        same = coin(quoteToken={'address': lab.SOL_QUOTE_MINT})
        self.assertAlmostEqual(lab.sol_usd_from_coin(same), 150)
        for c in (coin(quoteToken={'address': USDC}),
                  coin(quoteTokenAddress=USDC, quoteToken={'address': lab.SOL_QUOTE_MINT}),
                  coin(quoteToken='malformed')):
            self.assertEqual(lab.sol_usd_from_coin(c), 0)

    def test_stable_unknown_missing_and_invalid_ratios_cannot_create_free_fills(self):
        rows = [coin(quoteTokenAddress=asset, priceNative=.01) for asset in (USDC, USDT, 'unknown', None)]
        rows.extend(coin(priceNative=value) for value in (0, None, float('nan'), float('inf')))
        rows.append(coin(priceUsd=1e308, priceNative=1e-308))
        for c in rows:
            with self.subTest(c=c):
                self.assertEqual(lab.sol_usd_from_coin(c), 0)
                self.assertEqual(lab.pumpswap_fee_bps({**c, 'dexId': 'pumpswap'}), 125)
                with self.assertRaisesRegex(ValueError, 'network_price_unknown'):
                    lab.entry_execution(c, 50)
                with self.assertRaisesRegex(ValueError, 'network_price_unknown'):
                    lab.exit_execution(c, 5_000)

    def test_unknown_conversion_blocks_new_entry_before_provider_checks_preserving_ledger(self):
        before = copy.deepcopy(self.book)
        for asset in (USDC, USDT, 'unknown', None):
            with self.subTest(asset=asset), \
                 patch.object(lab.price_integrity, 'check') as price_check, \
                 patch.object(lab, 'entry_execution') as execute:
                lab.maybe_open([coin(quoteTokenAddress=asset, priceNative=.01)], {})
                price_check.assert_not_called()
                execute.assert_not_called()
                self.assertIsNone(self.book['position'])
                self.assertEqual(self.book['entry_diagnostics']['blocked_reason'], 'network_price_unknown')
                self.assertEqual(self.book['entry_diagnostics']['network_cost_rejected'], 1)
                for field in ('balance', 'history', 'trade_seq', 'last_entry_by_address'):
                    self.assertEqual(self.book[field], before[field])

    def test_identified_sol_entry_records_cost_provenance(self):
        verified={'source':promoted_guard.FLOW_SOURCE,'coverage_status':'COMPLETE',
            'window_ms':promoted_guard.FLOW_WINDOW_MS,'address':MINT,'pairAddress':PAIR,
            'window_at':NOW,'latest_event_at':NOW-100,'available_at':NOW-50,
            'trades':4,'unique_wallets':3,'buy_usd':500,'sell_usd':100}
        flow={(MINT,PAIR):{'verified_flow':verified}}
        risk={'status':'pass','mint':MINT,'pair':PAIR,'checked_at':NOW}
        with patch.object(lab.price_integrity, 'check', return_value={'status':'pass','mint':MINT,'pair':PAIR}), patch.object(lab.rug_guard,'check',return_value=risk):
            lab.maybe_open([coin()], flow)
        position = self.book['position']
        self.assertIsNotNone(position)
        self.assertGreater(position['entry_network_fee_usd'], 0)
        self.assertEqual(position['entry_quote_token_address'], lab.SOL_QUOTE_MINT)
        self.assertEqual(position['entry_network_cost_basis'], 'IDENTIFIED_SOL_POOL_USD_NATIVE_RATIO')
        self.assertEqual(len(self.book['history']), 1)

    def test_unavailable_conversion_retains_historical_position_and_does_not_realize_exit(self):
        self.book['position'] = {
            'strategy_id': 'EARLY', 'address': MINT, 'pairAddress': PAIR,
            'entry_price': .01, 'quantity': 5_000, 'notional_usd': 50,
            'remaining_cost_basis_usd': 50.015, 'opened_at': NOW - 61 * 60_000,
            'updated_at': NOW - 1_000, 'mark_received_at': NOW - 1_000,
            'open_pnl_usd': -1, 'custom_legacy_field': {'keep': True},
        }
        before = copy.deepcopy(self.book)
        for asset in (USDC, USDT, 'unknown', None):
            with self.subTest(asset=asset), \
                 patch.object(lab.POSITION_MARK_FEED, 'resolve', return_value=coin(quoteTokenAddress=asset)), \
                 patch.object(lab, 'exit_execution') as execute:
                lab.update_positions({}, [])
                execute.assert_not_called()
                self.assertIsNotNone(self.book['position'])
                self.assertEqual(self.book['balance'], before['balance'])
                self.assertEqual(self.book['history'], before['history'])
                for field, value in before['position'].items():
                    self.assertEqual(self.book['position'][field], value)
                self.assertEqual(self.book['position']['quote_status'], 'unavailable')
                self.assertEqual(self.book['position']['quote_unavailable_reason'], 'network_price_unknown')
                self.assertTrue(lab.stats(self.book)['valuation_stale'])


if __name__ == '__main__':
    unittest.main()
