"""STRUCTURAL_RUG_GUARD_V1: a pure, fail-closed, pre-entry structural rug screen.

PAPER only. This module decides whether a market observation is structurally
unsafe to enter; it never admits an entry on its own and claims no edge. It is
distinct from ``engine_rug_guard`` (RUG_GUARD_V2, the cached RugCheck/RPC
safety report), which stays unchanged and still runs later for every entry.

Evidence (research 2026-10-08 on 22.8 h of the engine's own scan log, PAPER):
- 157 terminal drains among 1,549 PumpSwap SOL pools; all 19 drains in pools
  that ever held >= $250k liquidity came from two structural families:
  9 LP-pullable (liquidity/market cap 1.2-1.9, liquidity to 0 at an unchanged
  price, recycled tickers) and 10 fake market cap ($23M-$1.3B "market cap" on
  liquidity/market cap 0.25-1.9%, 0.3-4 days old, the same tickers relaunched
  as new mints). The other drains hit young pools (median age 12.8 min; pools
  under 15 min drained 87% of the time within 2 h).
- 10 of the 64 pools that entered the cost-first universe drained within the
  22.8 h; the cost-first account opened 3 rug-family positions within 80 s of
  going live. RUG_GUARD_V2 never ran on these families (checked_at 0) and
  passed SharkTank, which later collapsed.
- Holdout: drain-event recall 0.897 at a point false-positive rate of 0.148
  (liquidity >= $20k); drain hazard per position-hour 4.39% -> 1.31%. Random
  entries still lose with the guard: it removes a loss tail, it creates no
  profit.

Rules, evaluated in this order (every rule that applies is recorded):
1. ``rug_input_unknown``: liquidity, market cap (marketCap, else fdv), pair age
   (pairCreatedAt at ``now``), mint, pool, normalized ticker or the ticker
   registry is missing, non-finite or <= 0. Evaluation stops here.
2. ``rug_lp_pullable``: liquidity / market cap >= 1.0 (the pool holds most of
   the supply; the creator controls the LP).
3. ``rug_young_pool``: pair age < 720 min.
4. ``rug_fake_market_cap``: market cap >= $20M and liquidity / market cap < 2%
   and pair age < 14 days. The 2% threshold (the research froze 1% on train)
   is the recommended conservative variant that also catches DAWS-like pools;
   it is holdout-informed and not validated.
5. ``rug_ticker_reuse``: pair age < 14 days and the normalized ticker
   (alphanumerics only, casefolded) was already seen on another pool with a
   DIFFERENT mint. A token's own second pool (same mint) is not a reuse.

The ticker check is past-only: ``TickerRegistry`` is fed by the caller with
every scan's feed and only registrations first seen at or before ``now``
count. The registry persists as a small bounded JSON sidecar next to the
account state (atomic replace, entries unseen for 14 days pruned, a corrupt or
missing file starts empty and never stops the engine).
"""
from dataclasses import asdict, dataclass
import json
import math
import os
from pathlib import Path
import tempfile
import threading
import time

VERSION = 'STRUCTURAL_RUG_GUARD_V1'
REGISTRY_VERSION = 'TICKER_REGISTRY_V1'
REASONS = ('rug_input_unknown', 'rug_lp_pullable', 'rug_young_pool',
           'rug_fake_market_cap', 'rug_ticker_reuse')
DAY_MS = 86_400_000


@dataclass(frozen=True)
class GuardParameters:
    """Frozen thresholds; no environment variable changes them."""
    lp_pullable_min_liq_to_mcap: float = 1.0
    young_pool_max_age_minutes: float = 720.0
    fake_mcap_min_usd: float = 20_000_000.0
    fake_mcap_max_liq_to_mcap: float = 0.02
    fake_mcap_max_age_minutes: float = 14 * 1440.0
    ticker_reuse_max_age_minutes: float = 14 * 1440.0


PARAMS = GuardParameters()


def _finite(value):
    if isinstance(value, bool) or value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return result if math.isfinite(result) else None


def _positive(value):
    result = _finite(value)
    return result if result is not None and result > 0 else None


def normalize_ticker(symbol) -> str:
    """Alphanumerics only, casefolded ('D O T F' -> 'dotf', 'CATE!' -> 'cate')."""
    if not isinstance(symbol, str):
        return ''
    return ''.join(ch for ch in symbol if ch.isalnum()).casefold()


def liquidity_usd(coin: dict):
    value = _positive(coin.get('liquidityUsd'))
    if value is None and isinstance(coin.get('liquidity'), dict):
        value = _positive(coin['liquidity'].get('usd'))
    return value


def market_cap_usd(coin: dict):
    """marketCap, falling back to fdv when marketCap is missing, non-finite or <= 0."""
    value = _positive(coin.get('marketCap'))
    return value if value is not None else _positive(coin.get('fdv'))


def age_minutes(coin: dict, now):
    """Pair age at ``now`` (ms) from pairCreatedAt (ms); None when unknown or not positive."""
    created, current = _positive(coin.get('pairCreatedAt')), _finite(now)
    if created is None or current is None or current <= created:
        return None
    return (current - created) / 60_000


def _identity(coin: dict):
    mint = coin.get('address')
    pair = coin.get('pairAddress')
    mint = mint.strip() if isinstance(mint, str) else ''
    pair = pair.strip() if isinstance(pair, str) else ''
    return mint, pair


class TickerRegistry:
    """Past-only record of (mint, pool) -> normalized ticker with first/last sighting.

    ``observe(feed, now)`` registers every coin of one scan. ``reusers`` lists
    other-mint pools of a ticker first seen at or before ``now``. Entries not
    seen for ``retention_ms`` are pruned and at most ``max_entries`` are kept
    (least recently seen evicted first), so memory and the sidecar stay
    bounded. All methods are thread-safe and never raise on persistence
    problems; a failed save is reported in ``status()``.
    """

    def __init__(self, path=None, *, retention_ms=14 * DAY_MS, max_entries=20_000,
                 save_interval_ms=300_000, prune_interval_ms=60_000, clock=None):
        self.path = Path(path) if path else None
        self.retention_ms = max(DAY_MS, int(retention_ms))
        self.max_entries = max(16, int(max_entries))
        self.save_interval_ms = max(0, int(save_interval_ms))
        self.prune_interval_ms = max(0, int(prune_interval_ms))
        self.clock = clock or (lambda: int(time.time() * 1000))
        self._lock = threading.RLock()
        self._entries = {}      # (mint, pair) -> [ticker, first_seen, last_seen]
        self._by_ticker = {}    # ticker -> set of (mint, pair)
        self._dirty = False
        self._saved_at = None
        self._pruned_at = None
        self.load_status = 'NO_PATH' if self.path is None else 'NOT_LOADED'
        self.load_skipped_rows = 0
        self.save_error = None
        self.saves = 0
        if self.path is not None:
            self._load()

    # ----------------------------------------------------------- mutation --
    def _add(self, key, ticker, first_seen, last_seen):
        old = self._entries.get(key)
        if old is not None and old[0] != ticker:
            self._by_ticker.get(old[0], set()).discard(key)
            if not self._by_ticker.get(old[0]):
                self._by_ticker.pop(old[0], None)
            old = None
        if old is None:
            self._entries[key] = [ticker, first_seen, last_seen]
            self._by_ticker.setdefault(ticker, set()).add(key)
            return True
        old[1] = min(old[1], first_seen)
        old[2] = max(old[2], last_seen)
        return False

    def _remove(self, key):
        row = self._entries.pop(key, None)
        if row is None:
            return
        keys = self._by_ticker.get(row[0])
        if keys is not None:
            keys.discard(key)
            if not keys:
                self._by_ticker.pop(row[0], None)

    def observe_coin(self, coin: dict, now) -> bool:
        """Register one observation; returns True when the (mint, pool) is new."""
        stamp = _finite(now)
        if not isinstance(coin, dict) or stamp is None:
            return False
        mint, pair = _identity(coin)
        ticker = normalize_ticker(coin.get('symbol'))
        if not mint or not pair or not ticker:
            return False
        with self._lock:
            added = self._add((mint, pair), ticker, stamp, stamp)
            self._dirty = True
            return added

    def observe(self, feed, now) -> int:
        """Register a whole scan; prunes and saves at most once per interval."""
        added = 0
        for coin in feed or ():
            added += int(self.observe_coin(coin, now))
        stamp = _finite(now)
        if stamp is not None:
            self.prune(stamp)
            self.maybe_save(stamp)
        return added

    def prune(self, now, *, force=False) -> int:
        stamp = _finite(now)
        if stamp is None:
            return 0
        with self._lock:
            if (not force and self._pruned_at is not None
                    and 0 <= stamp - self._pruned_at < self.prune_interval_ms):
                return 0
            self._pruned_at = stamp
            horizon = stamp - self.retention_ms
            stale = [key for key, row in self._entries.items() if row[2] < horizon]
            for key in stale:
                self._remove(key)
            removed = len(stale)
            if len(self._entries) > self.max_entries:
                # Evict the least recently seen down to 90% so eviction is amortized.
                keep = int(self.max_entries * 0.9)
                order = sorted(self._entries.items(), key=lambda item: (item[1][2], item[0]))
                for key, _row in order[:len(self._entries) - keep]:
                    self._remove(key)
                    removed += 1
            if removed:
                self._dirty = True
            return removed

    # -------------------------------------------------------------- query --
    def reusers(self, ticker: str, mint: str, pair: str, now) -> list:
        """Other-mint pools of ``ticker`` first seen at or before ``now`` (sorted)."""
        stamp = _finite(now)
        if stamp is None or not ticker:
            return []
        with self._lock:
            return sorted(key for key in self._by_ticker.get(ticker, ())
                          if key[0] != mint and key[1] != pair and self._entries[key][1] <= stamp)

    def __len__(self):
        with self._lock:
            return len(self._entries)

    def status(self) -> dict:
        with self._lock:
            return {'version': REGISTRY_VERSION, 'entries': len(self._entries),
                    'tickers': len(self._by_ticker), 'max_entries': self.max_entries,
                    'retention_days': self.retention_ms / DAY_MS,
                    'persistent': self.path is not None,
                    'sidecar': self.path.name if self.path is not None else None,
                    'load_status': self.load_status, 'load_skipped_rows': self.load_skipped_rows,
                    'saved_at': self._saved_at, 'saves': self.saves, 'save_error': self.save_error}

    # -------------------------------------------------------- persistence --
    def _load(self):
        try:
            if not self.path.exists():
                self.load_status = 'MISSING_STARTED_EMPTY'
                return
            data = json.loads(self.path.read_text(encoding='utf-8'))
            if not isinstance(data, dict) or data.get('version') != REGISTRY_VERSION:
                raise ValueError('unsupported ticker registry')
            rows = data.get('entries')
            if not isinstance(rows, list):
                raise ValueError('ticker registry without entries')
        except Exception:
            # A corrupt or unreadable sidecar starts empty; the next save replaces it.
            self.load_status = 'CORRUPT_STARTED_EMPTY'
            self._entries, self._by_ticker = {}, {}
            return
        skipped = 0
        with self._lock:
            for row in rows:
                try:
                    mint, pair, ticker, first_seen, last_seen = row
                    first, last = _finite(first_seen), _finite(last_seen)
                    if (not isinstance(mint, str) or not mint or not isinstance(pair, str) or not pair
                            or not isinstance(ticker, str) or not ticker or normalize_ticker(ticker) != ticker
                            or first is None or last is None or first > last):
                        raise ValueError('invalid row')
                except (TypeError, ValueError):
                    skipped += 1
                    continue
                self._add((mint, pair), ticker, first, last)
            self.load_skipped_rows = skipped
            self.load_status = 'LOADED'
            self._dirty = False
        self.prune(self.clock(), force=True)

    def maybe_save(self, now) -> bool:
        stamp = _finite(now)
        with self._lock:
            if self.path is None or not self._dirty or stamp is None:
                return False
            if (self._saved_at is not None and self.save_interval_ms
                    and 0 <= stamp - self._saved_at < self.save_interval_ms):
                return False
        return self.save(stamp)

    def flush(self) -> bool:
        """Save now when there are unsaved observations (shutdown path)."""
        with self._lock:
            dirty = self._dirty
        return self.save() if dirty and self.path is not None else False

    def save(self, now=None) -> bool:
        """Atomically replace the sidecar (temp file + fsync + replace); never raises."""
        if self.path is None:
            return False
        stamp = _finite(now)
        stamp = self.clock() if stamp is None else stamp
        with self._lock:
            rows = sorted([mint, pair, row[0], row[1], row[2]]
                          for (mint, pair), row in self._entries.items())
            self._dirty = False
            self._saved_at = stamp
        text = json.dumps({'version': REGISTRY_VERSION, 'guard_version': VERSION, 'saved_at': stamp,
                           'retention_days': self.retention_ms / DAY_MS, 'entries': rows},
                          separators=(',', ':'), allow_nan=False)
        name = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, name = tempfile.mkstemp(prefix='.' + self.path.name + '.', suffix='.tmp', dir=self.path.parent)
            with os.fdopen(fd, 'w', encoding='utf-8') as handle:
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
            for attempt in range(6):
                try:
                    os.replace(name, self.path)
                    name = None
                    break
                except PermissionError:
                    # Windows readers can briefly deny replacement of an open file.
                    if attempt == 5:
                        raise
                    time.sleep(.01 * (attempt + 1))
            self.saves += 1
            self.save_error = None
            return True
        except Exception as exc:
            with self._lock:
                self._dirty = True
            self.save_error = type(exc).__name__
            return False
        finally:
            if name is not None:
                try:
                    os.unlink(name)
                except OSError:
                    pass


def check(coin: dict, now, registry, *, params: GuardParameters = PARAMS) -> dict:
    """Structural rug screen of one observation at ``now`` (ms). Never an entry authorization.

    Returns {'version', 'blocked', 'reasons', 'liq_mcap', 'age_min', 'mcap'} plus
    the observed liquidity, normalized ticker and other-mint pools of the ticker.
    """
    coin = coin if isinstance(coin, dict) else {}
    liquidity = liquidity_usd(coin)
    cap = market_cap_usd(coin)
    age = age_minutes(coin, now)
    mint, pair = _identity(coin)
    ticker = normalize_ticker(coin.get('symbol'))
    result = {'version': VERSION, 'blocked': True, 'reasons': [],
              'liq_mcap': None if liquidity is None or cap is None else liquidity / cap,
              'age_min': age, 'mcap': cap, 'liquidity_usd': liquidity, 'ticker': ticker or None,
              'ticker_reused_by': 0}
    if (liquidity is None or cap is None or age is None or not mint or not pair or not ticker
            or registry is None):
        result['reasons'] = ['rug_input_unknown']
        return result
    ratio = liquidity / cap
    reasons = []
    if ratio >= params.lp_pullable_min_liq_to_mcap:
        reasons.append('rug_lp_pullable')
    if age < params.young_pool_max_age_minutes:
        reasons.append('rug_young_pool')
    if (cap >= params.fake_mcap_min_usd and ratio < params.fake_mcap_max_liq_to_mcap
            and age < params.fake_mcap_max_age_minutes):
        reasons.append('rug_fake_market_cap')
    if age < params.ticker_reuse_max_age_minutes:
        reusers = registry.reusers(ticker, mint, pair, now)
        result['ticker_reused_by'] = len(reusers)
        if reusers:
            reasons.append('rug_ticker_reuse')
    result['reasons'] = reasons
    result['blocked'] = bool(reasons)
    return result


def compact(result: dict) -> dict:
    """Diagnostics/position record of one check (rounded, no raw coin)."""
    def rounded(value, digits):
        value = _finite(value)
        return None if value is None else round(value, digits)
    return {'version': result.get('version', VERSION), 'blocked': bool(result.get('blocked')),
            'reasons': list(result.get('reasons') or []),
            'liq_mcap': rounded(result.get('liq_mcap'), 6), 'age_min': rounded(result.get('age_min'), 1),
            'mcap': rounded(result.get('mcap'), 2), 'liquidity_usd': rounded(result.get('liquidity_usd'), 2),
            'ticker': result.get('ticker'), 'ticker_reused_by': int(result.get('ticker_reused_by') or 0)}


def config(params: GuardParameters = PARAMS) -> dict:
    """Published definition; part of every effective config hash that applies it."""
    return {'version': VERSION, 'registry_version': REGISTRY_VERSION,
            'reasons_in_order': list(REASONS), 'parameters': asdict(params),
            'market_cap_basis': 'marketCap, else fdv', 'age_basis': 'pairCreatedAt at decision time',
            'ticker_normalization': 'alphanumerics only, casefolded',
            'ticker_reuse_rule': 'another pool with a DIFFERENT mint registered at or before now',
            'fail_closed': True, 'distinct_from': 'engine_rug_guard.RUG_GUARD_V2 (unchanged)',
            'fake_market_cap_threshold_note': '2% liquidity/market cap is holdout-informed (research froze 1%); conservative, not validated',
            'is_entry_authorization': False, 'profitability_proven': False}
