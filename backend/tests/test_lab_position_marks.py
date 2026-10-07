import unittest
from unittest.mock import patch

from lab_position_marks import PositionMarkFeed, fresh_exact_coin, parse_pair_response


MINT = 'So11111111111111111111111111111111111111112'
PAIR = '8bjgcWvX8nU7m3p2zL4bC6aQ1dF5hK9sT2xY7eR3pLmN'
NOW = 1_800_000_000_000


def pair(price='0.001', *, mint=MINT, pair=PAIR, chain='solana'):
    return {
        'chainId': chain,
        'pairAddress': pair,
        'baseToken': {'address': mint, 'name': 'Meme', 'symbol': 'MEME'},
        'quoteToken': {'address': 'So11111111111111111111111111111111111111112'},
        'priceUsd': price,
        'liquidity': {'usd': 25000},
    }


class LabPositionMarkTests(unittest.TestCase):
    def test_parser_accepts_only_exact_solana_pool_and_base_mint(self):
        parsed = parse_pair_response({'pairs': [pair()]}, MINT, PAIR, NOW)
        self.assertEqual(parsed['address'], MINT)
        self.assertEqual(parsed['pairAddress'], PAIR)
        self.assertEqual(parsed['priceUsd'], '0.001')
        self.assertEqual(parsed['mark_source'], 'DEXSCREENER_EXACT_POOL_API')
        self.assertEqual(parsed['updatedAt'], NOW)

    def test_parser_rejects_wrong_pair_mint_chain_and_empty_prices(self):
        self.assertIsNone(parse_pair_response({'pairs': [pair(pair='other')]}, MINT, PAIR, NOW))
        self.assertIsNone(parse_pair_response({'pairs': [pair(mint='other')]}, MINT, PAIR, NOW))
        self.assertIsNone(parse_pair_response({'pairs': [pair(chain='ethereum')]}, MINT, PAIR, NOW))
        self.assertIsNone(parse_pair_response({'pairs': [pair(price='0')]}, MINT, PAIR, NOW))

    def test_feed_mark_must_be_recent_and_match_both_addresses(self):
        coin = {'address': MINT, 'pairAddress': PAIR, 'priceUsd': 0.001, 'updatedAt': NOW}
        self.assertTrue(fresh_exact_coin(coin, MINT, PAIR, NOW + 7_000))
        self.assertFalse(fresh_exact_coin(coin, MINT, PAIR, NOW + 9_000))
        self.assertFalse(fresh_exact_coin(coin, MINT, 'other', NOW))
        self.assertFalse(fresh_exact_coin({**coin, 'priceUsd': 0}, MINT, PAIR, NOW))

    def test_fresh_receipt_cannot_hide_stale_source_or_future_observation(self):
        coin = {'address': MINT, 'pairAddress': PAIR, 'priceUsd': 0.001,
                'updatedAt': NOW - 20_000, 'mark_received_at': NOW}
        self.assertFalse(fresh_exact_coin(coin, MINT, PAIR, NOW))
        for updates in ({'updatedAt': NOW + 1},
                        {'updatedAt': NOW, 'mark_received_at': NOW + 1},
                        {'updatedAt': NOW, 'mark_received_at': 0},
                        {'updatedAt': NOW, 'mark_received_at': float('nan')}):
            with self.subTest(updates=updates):
                self.assertFalse(fresh_exact_coin({**coin, **updates}, MINT, PAIR, NOW))


def normalized_mark(price, observed, *, mint=MINT, pool=PAIR, received=None):
    return {'address': mint, 'pairAddress': pool, 'priceUsd': price,
            'priceNative': price / 100, 'quoteTokenAddress': MINT, 'dexId': 'raydium',
            'liquidityUsd': 50_000, 'updatedAt': observed,
            'mark_received_at': observed if received is None else received,
            'mark_source': 'DEXSCREENER_EXACT_POOL_API'}


class LabPositionMarkSelectionTests(unittest.TestCase):
    def setUp(self):
        self.marks = PositionMarkFeed(now_ms=lambda: NOW)
        self.position = {'address': MINT, 'pairAddress': PAIR}
        self.key = (MINT, PAIR)
        self.submit_patch = patch.object(self.marks._executor, 'submit')
        self.submit = self.submit_patch.start()

    def tearDown(self):
        self.submit_patch.stop()
        self.marks._executor.shutdown(wait=True)

    def test_newer_adverse_exact_pool_cache_wins_over_older_fresh_discovery(self):
        old_feed = normalized_mark(0.001, NOW - 7_000)
        new_cache = normalized_mark(0.00090, NOW - 500)
        self.marks._cache[self.key] = new_cache
        chosen = self.marks.resolve(self.position, {self.key: old_feed}, NOW)
        self.assertEqual(chosen['priceUsd'], 0.00090)
        self.assertEqual(chosen['mark_received_at'], NOW - 500)
        self.assertEqual(chosen['mark_source'], 'DEXSCREENER_EXACT_POOL_API')
        self.assertEqual(self.marks._cache[self.key], new_cache)
        self.submit.assert_not_called()

    def test_newer_adverse_cache_reaches_lab_stop_instead_of_older_feed_price(self):
        import strategy_lab as lab

        old_feed = normalized_mark(0.001, NOW - 7_000)
        self.marks._cache[self.key] = normalized_mark(0.0009, NOW - 500)
        book = lab.empty_book({'id': 'EARLY', 'name': 'Early Runner'})
        book['position'] = {**self.position, 'strategy_id': 'EARLY',
                            'entry_price': 0.001, 'opened_at': NOW - 60_000,
                            'updated_at': NOW - 7_000, 'notional_usd': 50,
                            'quantity': 50_000, 'original_quantity': 50_000,
                            'remaining_cost_basis_usd': 50,
                            'partial_realized_pnl': 0}
        test_state = {**lab.STATE, 'books': {'EARLY': book}, 'portfolio_setup': {}}
        with patch.object(lab, 'STATE', test_state), \
             patch.object(lab, 'POSITION_MARK_FEED', self.marks), \
             patch.object(lab, 'now_ms', return_value=NOW):
            lab.update_positions({}, [old_feed])
        self.assertIsNone(book['position'])
        self.assertEqual(len(book['history']), 1)
        closed = book['history'][0]
        self.assertEqual(closed['exit_reason'], 'STOP_LOSS_3_NET')
        self.assertEqual(closed['exit_price'], 0.0009)
        self.assertEqual(closed['mark_received_at'], NOW - 500)
        self.assertLess(closed['pnl_usd'], -5)
        self.submit.assert_not_called()

    def test_newer_feed_wins_and_preserves_observation_age(self):
        self.marks._cache[self.key] = normalized_mark(0.0009, NOW - 3_000)
        new_feed = normalized_mark(0.0011, NOW - 500, received=NOW)
        chosen = self.marks.resolve(self.position, {self.key: new_feed}, NOW)
        self.assertEqual(chosen['priceUsd'], 0.0011)
        self.assertEqual(chosen['mark_received_at'], NOW - 500)
        self.assertEqual(chosen['mark_source'], 'SHARED_LIVE_FEED_EXACT_POOL')
        self.assertEqual(new_feed['mark_received_at'], NOW)
        self.submit.assert_not_called()

    def test_receipt_time_cannot_rank_old_price_ahead_of_newer_cache(self):
        self.marks._cache[self.key] = normalized_mark(0.0009, NOW - 1_000)
        old_feed = normalized_mark(0.0011, NOW - 7_000, received=NOW)
        chosen = self.marks.resolve(self.position, {self.key: old_feed}, NOW)
        self.assertEqual(chosen['priceUsd'], 0.0009)
        self.assertEqual(chosen['mark_received_at'], NOW - 1_000)
        self.submit.assert_not_called()

    def test_invalid_cached_identity_future_or_stale_source_cannot_replace_valid_feed(self):
        feed = normalized_mark(0.001, NOW - 2_000)
        invalid = [normalized_mark(0.9, NOW - 100, mint='other'),
                   normalized_mark(0.9, NOW - 100, pool='other'),
                   normalized_mark(0.9, NOW + 1),
                   normalized_mark(0.9, NOW - 20_000, received=NOW),
                   normalized_mark(0, NOW - 100)]
        for cached in invalid:
            with self.subTest(cached=cached):
                self.marks._cache[self.key] = cached
                chosen = self.marks.resolve(self.position, {self.key: feed}, NOW)
                self.assertEqual(chosen['priceUsd'], 0.001)
                self.assertEqual(chosen['mark_received_at'], NOW - 2_000)
        self.submit.assert_not_called()

    def test_no_valid_mark_returns_none_and_schedules_one_bounded_refresh(self):
        self.marks._cache[self.key] = normalized_mark(0.9, NOW - 20_000, received=NOW)
        invalid_feed = normalized_mark(0.001, NOW + 1)
        self.assertIsNone(self.marks.resolve(self.position, {self.key: invalid_feed}, NOW))
        self.assertIsNone(self.marks.resolve(self.position, {self.key: invalid_feed}, NOW))
        self.submit.assert_called_once_with(self.marks._refresh, self.key)

    def test_cache_age_limit_remains_distinct_from_discovery_age_limit(self):
        self.marks._cache[self.key] = normalized_mark(0.001, NOW - 10_000)
        chosen = self.marks.resolve(self.position, {}, NOW)
        self.assertEqual(chosen['mark_received_at'], NOW - 10_000)
        self.submit.assert_not_called()


if __name__ == '__main__':
    unittest.main()
