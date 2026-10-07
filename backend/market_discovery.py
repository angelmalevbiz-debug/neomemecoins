"""Bounded, shared PumpSwap address discovery, independent of paid promotion.

Cached rows identify markets to refresh through the normal scanner. They never
provide an entry price, flow, safety result, or a profitability prediction.
"""
import json
import math
import os
import re
import time
import uuid
from pathlib import Path

import compat_file_lock as fcntl

VERSION = 'PUMPSWAP_ADDRESS_CATALOG_V2_DUAL_PROVIDER'
PATH = '/latest/dex/search?q=pumpswap'
PAPRIKA_URL = ('https://api.dexpaprika.com/networks/solana/pools/search'
               '?dex_name=pumpswap&order_by=volume_usd_24h&sort=desc'
               '&limit=50&liquidity_usd_min=200000')
SOL_QUOTE_MINT = 'So11111111111111111111111111111111111111112'
REFRESH_MS = 60_000
FAILURE_BACKOFF_MS = 60_000
RATE_LIMIT_BACKOFF_MS = 180_000
MAX_CATALOG_AGE_MS = 900_000
MAX_ADDRESSES = 60
MAX_DEX_ADDRESSES = 30
MAX_PAPRIKA_ROWS = 50
MIN_PAPRIKA_LIQUIDITY_USD = 200_000
ADDRESS = re.compile(r'^[1-9A-HJ-NP-Za-km-z]{32,44}$')


def addresses_from_response(payload):
    rows = payload.get('pairs') if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        raise ValueError('invalid pool catalog')
    supported = []
    for row in rows:
        if not isinstance(row, dict) or row.get('chainId') != 'solana' or row.get('dexId') != 'pumpswap':
            continue
        mint = str((row.get('baseToken') or {}).get('address') or '')
        pair = str(row.get('pairAddress') or '')
        if not ADDRESS.fullmatch(mint) or not ADDRESS.fullmatch(pair):
            continue
        try:
            liquidity = float((row.get('liquidity') or {}).get('usd') or 0)
        except (ValueError, TypeError, OverflowError):
            continue
        if math.isfinite(liquidity) and liquidity >= 10_000:
            supported.append((liquidity, mint))
    # Include liquid, established pools even when they have no active boost.
    result = []
    for _, mint in sorted(supported, reverse=True):
        if mint not in result:
            result.append(mint)
    return result[:MAX_DEX_ADDRESSES]


def paprika_addresses_from_response(payload):
    """Extract identities only; another provider must refresh market prices.

    DexPaprika's token array is unordered. Require exactly two distinct Solana
    identities, one of which is WSOL, rather than guessing that tokens[0] is
    the base mint. Ignore all prices, fee fields, and transaction observations.
    """
    rows = payload.get('results') if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        raise ValueError('invalid liquid pool catalog')
    result = []
    for row in rows[:MAX_PAPRIKA_ROWS]:
        if (not isinstance(row, dict) or row.get('chain') != 'solana'
                or row.get('dex_id') != 'pumpswap'
                or not isinstance(row.get('id'), str)
                or not ADDRESS.fullmatch(row['id'])):
            continue
        tokens = row.get('tokens')
        if not isinstance(tokens, list) or len(tokens) != 2:
            continue
        if any(not isinstance(token, dict) or token.get('chain') != 'solana'
               or not isinstance(token.get('id'), str)
               or not ADDRESS.fullmatch(token['id']) for token in tokens):
            continue
        identities = {token['id'] for token in tokens}
        if len(identities) != 2 or SOL_QUOTE_MINT not in identities:
            continue
        try:
            liquidity = float(row.get('liquidity_usd') or 0)
        except (ValueError, TypeError, OverflowError):
            continue
        if not math.isfinite(liquidity) or liquidity < MIN_PAPRIKA_LIQUIDITY_USD:
            continue
        mint = next(identity for identity in identities if identity != SOL_QUOTE_MINT)
        if mint == row['id'] or row['id'] == SOL_QUOTE_MINT:
            continue
        if mint not in result:
            result.append(mint)
    return result


class PoolCatalog:
    def __init__(self, path: Path):
        self.path = Path(path)

    def _read(self):
        try:
            data = json.loads(self.path.read_text(encoding='utf-8'))
            if not isinstance(data, dict):
                return {}
            # Preserve the old address-only cache when upgrading. Every
            # provider retains its own observation age and retry deadline.
            if not isinstance(data.get('providers'), dict):
                data['providers'] = {'dexscreener': {
                    key: data[key] for key in ('addresses', 'observed_at',
                                               'next_attempt_at', 'status') if key in data}}
            for name in ('dexscreener', 'dexpaprika'):
                provider = data['providers'].get(name)
                if not isinstance(provider, dict):
                    data['providers'][name] = {}
                    provider = data['providers'][name]
                for field in ('next_attempt_at', 'observed_at'):
                    if type(provider.get(field)) is not int or provider[field] < 0:
                        provider[field] = 0
            return data
        except (OSError, ValueError):
            return {}

    def _usable_provider(self, data, now, limit):
        try:
            if not 0 <= now - int(data.get('observed_at', 0)) <= MAX_CATALOG_AGE_MS:
                return []
        except (ValueError, TypeError):
            return []
        rows = data.get('addresses')
        return [mint for mint in rows[:limit] if isinstance(mint, str) and ADDRESS.fullmatch(mint)] if isinstance(rows, list) else []

    def _usable(self, data, now):
        providers = data.get('providers') or {}
        result = []
        for name, limit in (('dexscreener', MAX_DEX_ADDRESSES),
                            ('dexpaprika', MAX_PAPRIKA_ROWS)):
            provider = providers.get(name) or {}
            for mint in self._usable_provider(provider, now, limit):
                if mint not in result:
                    result.append(mint)
        return result[:MAX_ADDRESSES]

    def _write(self, data):
        temporary = self.path.with_name(self.path.name + f'.{os.getpid()}.{uuid.uuid4().hex}.tmp')
        try:
            with temporary.open('w', encoding='utf-8') as handle:
                json.dump(data, handle, allow_nan=False)
                handle.flush()
                os.fsync(handle.fileno())
            for attempt in range(8):
                try:
                    temporary.replace(self.path)
                    break
                except PermissionError:
                    if attempt == 7:
                        raise
                    time.sleep(.025 * (attempt + 1))
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass

    def get(self, fetch, now=None, *, provider_fetch=None):
        now = int(time.time() * 1000) if now is None else now
        data = self._read()
        try:
            return self._get(fetch, provider_fetch, now, data)
        except (OSError, ValueError, TypeError):
            # A temporary Windows sharing violation must not break the other
            # independent discovery sources or promote a stale catalog price.
            return self._usable(data, now)

    def _get(self, fetch, provider_fetch, now, data):
        sources = [('dexscreener', fetch, PATH, addresses_from_response)]
        if provider_fetch is not None:
            sources.append(('dexpaprika', provider_fetch, PAPRIKA_URL,
                            paprika_addresses_from_response))
        providers = data.get('providers') or {}
        if all(now < (providers.get(name) or {}).get('next_attempt_at', 0)
               for name, _, _, _ in sources):
            return self._usable(data, now)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.with_suffix('.lock').open('a+') as handle:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except (BlockingIOError, OSError):
                return self._usable(data, now)
            try:
                data = self._read()
                providers = data.setdefault('providers', {})
                if all(now < (providers.get(name) or {}).get('next_attempt_at', 0)
                       for name, _, _, _ in sources):
                    return self._usable(data, now)
                due = []
                for source in sources:
                    name = source[0]
                    provider = providers.setdefault(name, {})
                    if now < provider.get('next_attempt_at', 0):
                        continue
                    provider.update(next_attempt_at=now + REFRESH_MS, status='pending')
                    due.append(source)
                # Reserve the shared budget durably before making requests.
                # A process crash or failed result projection must not cause
                # every account/scan to repeat the same external request.
                data.update(version=VERSION, addresses=self._usable(data, now),
                            prices_used_for_entry=False,
                            provider_request_budget_per_minute={'dexscreener': 1, 'dexpaprika': 1},
                            max_addresses=MAX_ADDRESSES)
                self._write(data)
                for name, callback, url, parse in due:
                    provider = providers[name]
                    try:
                        addresses = parse(callback(url))
                        providers[name] = {'addresses': addresses, 'observed_at': now,
                                           'next_attempt_at': now + REFRESH_MS,
                                           'status': 'available'}
                    except Exception as exc:
                        rate_limited = getattr(getattr(exc, 'response', None), 'status_code', None) == 429
                        provider.update(next_attempt_at=now + (RATE_LIMIT_BACKOFF_MS if rate_limited else FAILURE_BACKOFF_MS),
                                        status='rate_limited' if rate_limited else 'unavailable')
                data.update(version=VERSION, addresses=self._usable(data, now),
                            prices_used_for_entry=False,
                            provider_request_budget_per_minute={'dexscreener': 1, 'dexpaprika': 1},
                            max_addresses=MAX_ADDRESSES)
                self._write(data)
                return self._usable(data, now)
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)
