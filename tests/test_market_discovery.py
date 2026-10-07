import tempfile
import unittest
import json
from pathlib import Path
from unittest.mock import Mock
from unittest.mock import patch

import market_discovery as discovery


def pool(mint='A' * 44, **fields):
    return {'chainId': 'solana', 'dexId': 'pumpswap',
            'baseToken': {'address': mint}, 'pairAddress': 'B' * 44,
            'liquidity': {'usd': 250_000}, 'priceUsd': '999', **fields}


def address(index):
    alphabet = '123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz'
    return 'A' * 42 + alphabet[index // len(alphabet)] + alphabet[index % len(alphabet)]


def paprika_pool(mint='C' * 44, **fields):
    return {'chain': 'solana', 'dex_id': 'pumpswap', 'id': 'B' * 44,
            'liquidity_usd': 250_000, 'price_usd': 123456.789,
            'tokens': [{'id': discovery.SOL_QUOTE_MINT, 'chain': 'solana'},
                       {'id': mint, 'chain': 'solana'}], **fields}


class PoolCatalogTests(unittest.TestCase):
    def test_mixed_catalog_filters_wrong_chain_dex_and_invalid_identity(self):
        payload = {'pairs': [pool(), pool(), pool('C' * 44), pool(chainId='base'),
                            pool(dexId='raydium'), pool(mint='bad'),
                            pool('D' * 44, liquidity={'usd': float('nan')})]}
        self.assertEqual(discovery.addresses_from_response(payload), ['C' * 44, 'A' * 44])

    def test_cache_is_shared_by_accounts_and_contains_addresses_only(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'catalog.json'
            fetch = Mock(return_value={'pairs': [pool()]})
            self.assertEqual(discovery.PoolCatalog(path).get(fetch, 100_000), ['A' * 44])
            self.assertEqual(discovery.PoolCatalog(path).get(fetch, 100_001), ['A' * 44])
            fetch.assert_called_once_with(discovery.PATH)
            self.assertNotIn('999', path.read_text())

    def test_empty_result_and_failure_are_backed_off_instead_of_refetched_each_scan(self):
        for initial in ({'pairs': []}, RuntimeError('provider unavailable')):
            with self.subTest(initial=initial), tempfile.TemporaryDirectory() as temp:
                fetch = Mock(side_effect=initial) if isinstance(initial, Exception) else Mock(return_value=initial)
                catalog = discovery.PoolCatalog(Path(temp) / 'catalog.json')
                self.assertEqual(catalog.get(fetch, 100_000), [])
                self.assertEqual(catalog.get(fetch, 103_000), [])
                self.assertEqual(fetch.call_count, 1)

    def test_expired_addresses_do_not_survive_repeated_provider_failure(self):
        with tempfile.TemporaryDirectory() as temp:
            catalog = discovery.PoolCatalog(Path(temp) / 'catalog.json')
            catalog.get(Mock(return_value={'pairs': [pool()]}), 100_000)
            self.assertEqual(catalog.get(Mock(side_effect=RuntimeError()), 100_000 + discovery.MAX_CATALOG_AGE_MS + 1), [])

    def test_temporary_file_sharing_failure_preserves_readable_address_cache(self):
        with tempfile.TemporaryDirectory() as temp:
            catalog = discovery.PoolCatalog(Path(temp) / 'catalog.json')
            catalog.get(Mock(return_value={'pairs': [pool()]}), 100_000)
            with patch.object(Path, 'replace', side_effect=PermissionError('sharing violation')), \
                    patch.object(discovery.time, 'sleep'):
                self.assertEqual(catalog.get(Mock(return_value={'pairs': [pool('C' * 44)]}), 161_000), ['A' * 44])

    def test_failure_to_reserve_durable_request_budget_makes_no_external_request(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'catalog.json'
            dex = Mock(return_value={'pairs': [pool()]})
            paprika = Mock(return_value={'results': [paprika_pool()]})
            catalog = discovery.PoolCatalog(path)
            with patch.object(catalog, '_write', side_effect=PermissionError('sharing violation')):
                self.assertEqual(catalog.get(dex, 100_000, provider_fetch=paprika), [])
            dex.assert_not_called()
            paprika.assert_not_called()

    def test_failed_result_projection_retains_reserved_budget_for_other_accounts(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'catalog.json'
            catalog = discovery.PoolCatalog(path)
            dex = Mock(return_value={'pairs': [pool()]})
            paprika = Mock(return_value={'results': [paprika_pool()]})
            real_write = catalog._write
            writes = []

            def fail_final_write(data):
                writes.append(data)
                if len(writes) == 2:
                    raise PermissionError('projection unavailable')
                real_write(data)

            with patch.object(catalog, '_write', side_effect=fail_final_write):
                self.assertEqual(catalog.get(dex, 100_000, provider_fetch=paprika), [])
            self.assertEqual(discovery.PoolCatalog(path).get(dex, 108_000, provider_fetch=paprika), [])
            self.assertEqual((dex.call_count, paprika.call_count), (1, 1))
            saved = json.loads(path.read_text())['providers']
            self.assertEqual(saved['dexscreener']['next_attempt_at'], 160_000)
            self.assertEqual(saved['dexpaprika']['next_attempt_at'], 160_000)

    def test_paprika_unordered_tokens_identity_chain_dex_and_liquidity_are_verified(self):
        valid = paprika_pool()
        reversed_tokens = paprika_pool('D' * 44, tokens=list(reversed(paprika_pool('D' * 44)['tokens'])))
        invalid = [paprika_pool(chain='base'), paprika_pool(dex_id='raydium'),
                   paprika_pool(id='bad'), paprika_pool(mint='bad'),
                   paprika_pool(liquidity_usd='nan'), paprika_pool(liquidity_usd=199999),
                   paprika_pool(tokens=[{'id': 'A' * 44, 'chain': 'solana'},
                                        {'id': 'C' * 44, 'chain': 'solana'}]),
                   paprika_pool(tokens=[{'id': discovery.SOL_QUOTE_MINT, 'chain': 'solana'},
                                        {'id': 'C' * 44, 'chain': 'base'}]),
                   paprika_pool(tokens=[{'id': discovery.SOL_QUOTE_MINT, 'chain': 'solana'}] * 2),
                   paprika_pool(tokens=[None, {}]), paprika_pool(mint='B' * 44),
                   paprika_pool(id=int('1' * 34)), paprika_pool(mint=int('1' * 34))]
        self.assertEqual(discovery.paprika_addresses_from_response(
            {'results': [valid, reversed_tokens, valid, *invalid]}), ['C' * 44, 'D' * 44])

    def test_independent_source_failure_does_not_discard_available_source(self):
        for failing in ('dexscreener', 'dexpaprika'):
            with self.subTest(failing=failing), tempfile.TemporaryDirectory() as temp:
                path = Path(temp) / 'catalog.json'
                dex = Mock(return_value={'pairs': [pool()]})
                paprika = Mock(return_value={'results': [paprika_pool()]})
                (dex if failing == 'dexscreener' else paprika).side_effect = RuntimeError('unavailable')
                result = discovery.PoolCatalog(path).get(dex, 100_000, provider_fetch=paprika)
                self.assertEqual(result, ['C' * 44] if failing == 'dexscreener' else ['A' * 44])
                data = json.loads(path.read_text())
                self.assertEqual(data['providers'][failing]['status'], 'unavailable')
                self.assertFalse(data['prices_used_for_entry'])
                self.assertNotIn('123456.789', path.read_text())
                self.assertNotIn('999', path.read_text())

    def test_refresh_is_one_request_per_provider_per_minute_across_accounts(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'catalog.json'
            dex = Mock(return_value={'pairs': [pool()]})
            paprika = Mock(return_value={'results': [paprika_pool()]})
            for now in (100_000, 101_000, 108_000, 159_999):
                self.assertEqual(discovery.PoolCatalog(path).get(dex, now, provider_fetch=paprika),
                                 ['A' * 44, 'C' * 44])
            dex.assert_called_once_with(discovery.PATH)
            paprika.assert_called_once_with(discovery.PAPRIKA_URL)
            discovery.PoolCatalog(path).get(dex, 160_000, provider_fetch=paprika)
            self.assertEqual((dex.call_count, paprika.call_count), (2, 2))

    def test_rate_limit_backoff_is_per_provider_and_does_not_refresh_old_observation(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'catalog.json'
            catalog = discovery.PoolCatalog(path)
            dex = Mock(return_value={'pairs': [pool()]})
            paprika = Mock(return_value={'results': [paprika_pool()]})
            catalog.get(dex, 100_000, provider_fetch=paprika)
            error = RuntimeError('rate limit')
            error.response = Mock(status_code=429)
            paprika.side_effect = error
            catalog.get(dex, 160_000, provider_fetch=paprika)
            catalog.get(dex, 220_000, provider_fetch=paprika)
            catalog.get(dex, 280_000, provider_fetch=paprika)
            self.assertEqual((dex.call_count, paprika.call_count), (4, 2))
            cached = json.loads(path.read_text())['providers']['dexpaprika']
            self.assertEqual(cached['observed_at'], 100_000)
            self.assertEqual(cached['next_attempt_at'], 340_000)
            self.assertEqual(cached['status'], 'rate_limited')
            catalog.get(dex, 340_000, provider_fetch=paprika)
            self.assertEqual(paprika.call_count, 3)
            result = catalog.get(dex, 100_000 + discovery.MAX_CATALOG_AGE_MS + 1,
                                 provider_fetch=paprika)
            self.assertEqual(result, ['A' * 44])

    def test_legacy_cache_migrates_without_changing_observation_or_retry_deadline(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'catalog.json'
            path.write_text(json.dumps({'version': 'PUMPSWAP_ADDRESS_CATALOG_V1',
                                        'addresses': ['A' * 44], 'observed_at': 90_000,
                                        'next_attempt_at': 150_000, 'status': 'available'}))
            dex = Mock(side_effect=AssertionError('legacy provider is not due'))
            paprika = Mock(return_value={'results': [paprika_pool()]})
            self.assertEqual(discovery.PoolCatalog(path).get(dex, 100_000, provider_fetch=paprika),
                             ['A' * 44, 'C' * 44])
            dex.assert_not_called()
            saved = json.loads(path.read_text())['providers']['dexscreener']
            self.assertEqual((saved['observed_at'], saved['next_attempt_at']), (90_000, 150_000))

    def test_union_deduplicates_limits_rows_and_preserves_original_catalog(self):
        with tempfile.TemporaryDirectory() as temp:
            dex_rows = [pool(address(index)) for index in range(30)]
            paprika_rows = [paprika_pool(address(index)) for index in range(20, 90)]
            result = discovery.PoolCatalog(Path(temp) / 'catalog.json').get(
                Mock(return_value={'pairs': dex_rows}), 100_000,
                provider_fetch=Mock(return_value={'results': paprika_rows}))
            self.assertEqual(len(result), discovery.MAX_ADDRESSES)
            self.assertEqual(len(set(result)), len(result))
            self.assertTrue(set(address(index) for index in range(30)).issubset(result))
            self.assertNotIn(address(70), result)

    def test_invalid_schema_keeps_only_unexpired_cache_and_empty_success_clears_provider(self):
        for malformed in ({'results': {}}, {'results': None}, [], None):
            with self.subTest(malformed=malformed), tempfile.TemporaryDirectory() as temp:
                catalog = discovery.PoolCatalog(Path(temp) / 'catalog.json')
                dex = Mock(return_value={'pairs': [pool()]})
                catalog.get(dex, 100_000,
                            provider_fetch=Mock(return_value={'results': [paprika_pool()]}))
                result = catalog.get(dex, 160_000, provider_fetch=Mock(return_value=malformed))
                self.assertEqual(result, ['A' * 44, 'C' * 44])
                result = catalog.get(dex, 220_000, provider_fetch=Mock(return_value={'results': []}))
                self.assertEqual(result, ['A' * 44])


if __name__ == '__main__':
    unittest.main()
