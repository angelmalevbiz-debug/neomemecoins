import unittest

from lab_position_marks import fresh_exact_coin, parse_pair_response


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


if __name__ == '__main__':
    unittest.main()
