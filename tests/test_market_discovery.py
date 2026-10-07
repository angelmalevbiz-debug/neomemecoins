import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock
from unittest.mock import patch

import market_discovery as discovery


def pool(mint='A' * 44, **fields):
    return {'chainId': 'solana', 'dexId': 'pumpswap',
            'baseToken': {'address': mint}, 'pairAddress': 'B' * 44,
            'liquidity': {'usd': 250_000}, 'priceUsd': '999', **fields}


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
            with patch.object(Path, 'replace', side_effect=PermissionError('sharing violation')):
                self.assertEqual(catalog.get(Mock(return_value={'pairs': [pool('C' * 44)]}), 161_000), ['A' * 44])


if __name__ == '__main__':
    unittest.main()
