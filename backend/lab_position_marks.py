"""Fresh exact-pool spot marks for Strategy Lab positions.

The entry feed is intentionally a short discovery window. Held positions need
their own exact-pair refresh path when they rotate out of that window. This
module only reads market data; it never builds, signs, or submits a transaction.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import math
import threading
import time
from typing import Any, Callable

import requests

MARK_MAX_AGE_MS = 12_000
FEED_MAX_AGE_MS = 8_000
REQUEST_INTERVAL_SECONDS = 1.0
RETRY_SECONDS = 5.0
MAX_PENDING_PAIRS = 16


def number(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
        return result if math.isfinite(result) else default
    except (TypeError, ValueError):
        return default


def fresh_exact_coin(coin: dict[str, Any] | None, mint: str, pair: str,
                     now_ms: int, max_age_ms: int = FEED_MAX_AGE_MS) -> bool:
    if not isinstance(coin, dict):
        return False
    received = number(coin.get('mark_received_at'), number(coin.get('updatedAt')))
    return (
        coin.get('address') == mint
        and coin.get('pairAddress') == pair
        and number(coin.get('priceUsd')) > 0
        and received > 0
        and -5_000 <= now_ms - received <= max_age_ms
    )


def parse_pair_response(payload: dict[str, Any], mint: str, pair: str,
                        received_at: int) -> dict[str, Any] | None:
    """Accept only the requested Solana pool with the held mint as its base."""
    for coin in payload.get('pairs') or []:
        base = coin.get('baseToken') or {}
        if (coin.get('chainId') != 'solana' or coin.get('pairAddress') != pair
                or base.get('address') != mint):
            continue
        price = number(coin.get('priceUsd'))
        if price <= 0:
            return None
        normalized = dict(coin)
        normalized.update({
            'address': mint,
            'pairAddress': pair,
            'name': base.get('name') or '',
            'symbol': base.get('symbol') or '',
            'liquidityUsd': number((coin.get('liquidity') or {}).get('usd')),
            'updatedAt': received_at,
            'mark_received_at': received_at,
            'mark_source': 'DEXSCREENER_EXACT_POOL_API',
        })
        return normalized
    return None


class PositionMarkFeed:
    """Deduplicated, rate-limited exact-pair refresh with a bounded fresh cache."""

    def __init__(self, *, get: Callable[..., Any] | None = None,
                 now_ms: Callable[[], int] | None = None,
                 interval_seconds: float = REQUEST_INTERVAL_SECONDS,
                 cache_max_age_ms: int = MARK_MAX_AGE_MS):
        self._get = get or requests.get
        self._now_ms = now_ms or (lambda: int(time.time() * 1000))
        self._interval = max(0.0, float(interval_seconds))
        self._cache_max_age_ms = max(1, int(cache_max_age_ms))
        self._lock = threading.Lock()
        self._cache: dict[tuple[str, str], dict[str, Any]] = {}
        self._pending: set[tuple[str, str]] = set()
        self._retry_after: dict[tuple[str, str], float] = {}
        self._last_request = 0.0
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='lab-position-mark')
        self._http = requests.Session()
        self._http.headers.update({'user-agent': 'NEO-Strategy-Lab-Position-Marks/1.0',
                                   'accept': 'application/json'})

    def resolve(self, position: dict[str, Any], feed_prices: dict[tuple[str, str], dict[str, Any]],
                now: int) -> dict[str, Any] | None:
        mint, pair = str(position.get('address') or ''), str(position.get('pairAddress') or '')
        if not mint or not pair:
            return None
        key = (mint, pair)
        from_feed = feed_prices.get(key)
        if fresh_exact_coin(from_feed, mint, pair, now):
            result = dict(from_feed)
            result['mark_received_at'] = number(result.get('updatedAt'))
            result['mark_source'] = 'SHARED_LIVE_FEED_EXACT_POOL'
            return result

        with self._lock:
            cached = self._cache.get(key)
            if cached and 0 <= now - int(cached.get('mark_received_at') or 0) <= self._cache_max_age_ms:
                return dict(cached)
            can_schedule = (
                key not in self._pending
                and len(self._pending) < MAX_PENDING_PAIRS
                and time.monotonic() >= self._retry_after.get(key, 0.0)
            )
            if can_schedule:
                self._pending.add(key)
                self._executor.submit(self._refresh, key)
        return None

    def _refresh(self, key: tuple[str, str]) -> None:
        mint, pair = key
        success = False
        try:
            delay = self._interval - (time.monotonic() - self._last_request)
            if delay > 0:
                time.sleep(delay)
            self._last_request = time.monotonic()
            response = self._http.get(
                f'https://api.dexscreener.com/latest/dex/pairs/solana/{pair}', timeout=(1.0, 3.0)
            )
            response.raise_for_status()
            received = self._now_ms()
            coin = parse_pair_response(response.json(), mint, pair, received)
            if coin:
                with self._lock:
                    self._cache[key] = coin
                success = True
        except (requests.RequestException, ValueError, TypeError, KeyError, AttributeError):
            pass
        finally:
            with self._lock:
                self._pending.discard(key)
                self._retry_after[key] = time.monotonic() + (1.0 if success else RETRY_SECONDS)


POSITION_MARK_FEED = PositionMarkFeed()
