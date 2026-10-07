"""Bounded, shared PumpSwap address discovery, independent of paid promotion.

Cached rows identify markets to refresh through the normal scanner. They never
provide an entry price, flow, safety result, or a profitability prediction.
"""
import json
import math
import os
import re
import time
from pathlib import Path

import compat_file_lock as fcntl

VERSION = 'PUMPSWAP_ADDRESS_CATALOG_V1'
PATH = '/latest/dex/search?q=pumpswap'
REFRESH_MS = 60_000
FAILURE_BACKOFF_MS = 60_000
RATE_LIMIT_BACKOFF_MS = 180_000
MAX_CATALOG_AGE_MS = 900_000
MAX_ADDRESSES = 30
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
    return result[:MAX_ADDRESSES]


class PoolCatalog:
    def __init__(self, path: Path):
        self.path = Path(path)

    def _read(self):
        try:
            data = json.loads(self.path.read_text(encoding='utf-8'))
            if not isinstance(data, dict):
                return {}
            for field in ('next_attempt_at', 'observed_at'):
                if type(data.get(field)) is not int or data[field] < 0:
                    data[field] = 0
            return data
        except (OSError, ValueError):
            return {}

    def _usable(self, data, now):
        try:
            if not 0 <= now - int(data.get('observed_at', 0)) <= MAX_CATALOG_AGE_MS:
                return []
        except (ValueError, TypeError):
            return []
        rows = data.get('addresses')
        return [mint for mint in rows[:MAX_ADDRESSES] if isinstance(mint, str) and ADDRESS.fullmatch(mint)] if isinstance(rows, list) else []

    def get(self, fetch, now=None):
        now = int(time.time() * 1000) if now is None else now
        data = self._read()
        try:
            return self._get(fetch, now, data)
        except (OSError, ValueError, TypeError):
            # A temporary Windows sharing violation must not break the other
            # independent discovery sources or promote a stale catalog price.
            return self._usable(data, now)

    def _get(self, fetch, now, data):
        if now < data.get('next_attempt_at', 0):
            return self._usable(data, now)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.with_suffix('.lock').open('a+') as handle:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except (BlockingIOError, OSError):
                return self._usable(data, now)
            try:
                data = self._read()
                if now < data.get('next_attempt_at', 0):
                    return self._usable(data, now)
                try:
                    addresses = addresses_from_response(fetch(PATH))
                    data = {'version': VERSION, 'addresses': addresses, 'observed_at': now,
                            'next_attempt_at': now + REFRESH_MS, 'status': 'available',
                            'prices_used_for_entry': False}
                except Exception as exc:
                    rate_limited = getattr(getattr(exc, 'response', None), 'status_code', None) == 429
                    data.update(next_attempt_at=now + (RATE_LIMIT_BACKOFF_MS if rate_limited else FAILURE_BACKOFF_MS),
                                status='rate_limited' if rate_limited else 'unavailable',
                                prices_used_for_entry=False)
                temporary = self.path.with_name(self.path.name + f'.{os.getpid()}.tmp')
                temporary.write_text(json.dumps(data), encoding='utf-8')
                temporary.replace(self.path)
                return self._usable(data, now)
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)
