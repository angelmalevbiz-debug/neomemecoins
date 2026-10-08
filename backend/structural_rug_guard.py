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
   (pairCreatedAt at ``now``), mint, pool or the ticker registry is missing,
   non-finite or <= 0, or the pool is younger than 14 days and its normalized
   ticker is missing (only rules 5 and 6 read the ticker, so an established
   pool is judged without one). The feed's placeholder for a missing symbol
   ('TOKEN', see market_monitor.make_coin and the Gecko early pools) is a
   missing ticker too, and is never registered. Evaluation stops here.
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
6. ``rug_ticker_registry_warming``: pair age < 14 days, no reuse found, and
   the registry has observed the market continuously for less than 24 h
   (the research scan log covered 22.8 h). An empty or interrupted registry
   cannot tell a relaunch from a first launch, so rule 5 fails closed
   instead of passing (first deploy, a gap > 60 min, a cap eviction).

The ticker check is past-only: ``TickerRegistry`` is fed by the caller with
every scan's feed and only registrations first seen at or before ``now``
count. The registry persists as a small bounded JSON sidecar next to the
account state (atomic replace, entries unseen for 14 days pruned, a missing
file starts empty and never stops the engine). Sidecar read failures
(TICKER_REGISTRY_SIDECAR_READ_V1): a sidecar that cannot be read (an I/O error
after 3 attempts, e.g. a Windows sharing violation) is not corrupt. It is
never overwritten during the run (``UNREADABLE_NOT_OVERWRITTEN``), it is read
again at most every 60 s and merged once readable, and until then the registry
adopts no sibling's coverage (its own memory is unknown, so it keeps warming).
A sidecar that reads but does not parse is corrupt: it is copied to
``<sidecar>.corrupt-<ms>`` before the registry starts empty and replaces it
(``CORRUPT_STARTED_EMPTY``); when that copy fails it is treated as unreadable.

Coverage (TICKER_REGISTRY_V2_COVERAGE): the sidecar keeps ``covered_since``
(start of the current continuous observation) and ``observed_until`` (the
newest market observation, from the feed's updatedAt, so a stale published
feed does not extend it). Only discovered market rows count
(REGISTRY_COVERAGE_BASIS): a row whose sources name only a held position
('open-position', 'open-position-pinned-pair') is registered but observes no
market, so a discovery outage with a position open cannot keep coverage
current. A gap longer than 60 min restarts coverage: in the
research log the median other-mint sibling of a reused ticker was visible for
about 1 min, and 82% of the reuse candidates had every sibling visible for
less than 60 min, so a long gap can hide a whole relaunch. A cap eviction
moves ``covered_since`` to the newest evicted sighting (memory before it is
incomplete). Coverage survives restarts through the sidecar.

Coverage clock (TICKER_COVERAGE_CLOCK_V1): an ``observed_until`` more than
COVERAGE_MAX_AHEAD_MS (5 min) ahead of the reference clock (the scan clock of
``observe``, the registry clock at load, the reseed clock) was never observed:
a sidecar written by a test with a fixed future clock, a sibling with a skewed
clock or a backward wall-clock step. Such coverage is dropped (own sidecar at
load and re-read, a sibling's at seed, the in-memory span at the next
observation, which then starts coverage again and seeds at once), and
``coverage_current`` is False while it lasts. Before this rule a future stamp
kept coverage current forever and hid every gap, so the registry vouched
through outages and passed rug_ticker_reuse.

Bounds: at most 40,000 entries. The research scan log saw about 2,150 pairs a
day, so 14 days need about 30,000; when the cap still evicts, the effective
horizon (the newest evicted sighting) is published in ``status()``.

Seeding (TICKER_REGISTRY_SEED_V4): a registry whose own coverage does not
vouch after loading its sidecar (new, empty, legacy V1, last observation more
than 60 min ago, or current but shorter than 24 h, e.g. services started
before the first-deploy seed) merges the sidecars of the other PAPER services
on the host read-only (``seed_paths``: the main engine, the Lab, the tape; a
personal engine also reads main's through NEO_MAIN_MARKET_STATE_PATH) and
adopts the earliest ``covered_since`` of a sibling whose coverage is current
(its own current span is kept when it starts earlier). A running registry
seeds too (since V3): at once when a gap restarts its coverage (a tape whose
polls stalled for over 60 min while main kept scanning) and at most every
5 min while its coverage is under 24 h (a sibling that vouches later), so a
service no longer waits 24 h, or for a restart, while a sibling vouches. V4:
a registry whose coverage vouches still merges its siblings' sightings (rows
only, never coverage) at most every 5 min, because main observes the
untrimmed feed (the Gecko new pools cut by bounded_feed) and the Lab and the
tape see only main's trimmed published feed; under V3 they never picked up
main's later sightings while they vouched. A ticker registry is market
memory, not account memory, so a seed only adds sightings (it can only block
more); a seed file is never written. For the first deploy,
scripts/build_ticker_registry_seed.py builds main's sidecar offline from the
training observations journal, read in place and read-only: before Stop into
a scratch folder outside .runtime (first deploy only, while no
state.ticker_registry.json exists) and copied in after Stop, or in place
after Stop; a seed reaches the runtime folder only with the services stopped
(docs/PAPER_RUNBOOK.md, first deploy). No seed bridges an outage of more than
60 min: the journal holds the same gap, so coverage restarts and the
registries warm for 24 h.
"""
from dataclasses import asdict, dataclass
import json
import math
import os
from pathlib import Path
import re
import shutil
import tempfile
import threading
import time

from shared_snapshot_io import read_shared_text

VERSION = 'STRUCTURAL_RUG_GUARD_V1'
REGISTRY_VERSION = 'TICKER_REGISTRY_V2_COVERAGE'
# Sidecars of these versions load (their sightings count); they carry no
# coverage, so coverage starts again at the first observation.
LEGACY_REGISTRY_VERSIONS = ('TICKER_REGISTRY_V1',)
REASONS = ('rug_input_unknown', 'rug_lp_pullable', 'rug_young_pool',
           'rug_fake_market_cap', 'rug_ticker_reuse', 'rug_ticker_registry_warming')
DAY_MS = 86_400_000
HOUR_MS = 3_600_000
REGISTRY_RETENTION_DAYS = 14
REGISTRY_MAX_ENTRIES = 40_000
REGISTRY_SAVE_INTERVAL_MS = 300_000
REGISTRY_SEED_VERSION = 'TICKER_REGISTRY_SEED_V4'
PREVIOUS_REGISTRY_SEED_VERSION = 'TICKER_REGISTRY_SEED_V3'
# V3: a running registry under 24 h of coverage seeds again at most this often,
# and at once when a gap restarts its coverage.
REGISTRY_RESEED_INTERVAL_MS = 300_000
# V4: a registry whose coverage vouches merges its siblings' sightings (never their
# coverage) at most this often (main's untrimmed-feed sightings reach the Lab and tape).
REGISTRY_SIGHTING_MERGE_INTERVAL_MS = 300_000
# Own-sidecar read failures: an I/O error is retried, then the sidecar is kept
# (never overwritten during the run) and read again at most this often.
SIDECAR_READ_VERSION = 'TICKER_REGISTRY_SIDECAR_READ_V1'
SIDECAR_READ_ATTEMPTS = 3
SIDECAR_REREAD_INTERVAL_MS = 60_000
# A hard stop (TerminateProcess, taskkill /F, power loss) inside save() skips its
# finally and leaves the mkstemp temporary ('.' + sidecar name + '.' + 8 characters
# + '.tmp', up to several MB) beside the sidecar. The registry deletes its own such
# files once they are older than this, at start and at most this often after that
# (a write in progress is seconds old). Storage hygiene only: no decision changes.
SIDECAR_TEMP_SWEEP_VERSION = 'TICKER_REGISTRY_TEMP_SWEEP_V1'
SIDECAR_TEMP_STALE_SECONDS = 600
# tempfile.mkstemp puts exactly eight characters of this alphabet between prefix and suffix.
_SIDECAR_TEMP_CORE = re.compile(r'[a-z0-9_]{8}')
# A longer absence of market observations restarts the registry's coverage.
REGISTRY_MAX_GAP_MS = HOUR_MS
# A feed observation stamped this far after the scan clock is clock skew, not the future.
OBSERVATION_CLOCK_SKEW_MS = 5_000
# Coverage stamped further ahead of the reference clock was never observed: it is dropped.
# 5 min, not the 5 s feed skew: the engine's concurrent scan and entry threads and the
# offline replay (entry-stage checks at the recorded preflight start, which can precede
# the previous row's observation by a quote sequence) legitimately look seconds to a
# minute behind the newest observation. A future-dated sidecar (months ahead) or a
# backward clock step over 5 min is still caught; a smaller step delays gap detection
# by at most 5 min on top of the 60-min gap rule.
COVERAGE_CLOCK_VERSION = 'TICKER_COVERAGE_CLOCK_V1'
COVERAGE_MAX_AHEAD_MS = 300_000
# Normalized placeholders the feed uses for a missing symbol (market_monitor.make_coin
# and the Gecko early pools write 'TOKEN'): a missing ticker, never a registered one.
PLACEHOLDER_TICKERS = frozenset({'token'})
# Feed sources that only name a held position: market_monitor.scan_once adds a held
# coin under these when discovery did not return it. Such a row is a real sighting
# but no market observation, so it never extends the registry's coverage.
HELD_POSITION_SOURCES = frozenset({'open-position', 'open-position-pinned-pair'})
# What marks coverage: V1 counted every non-empty feed; V2 counts discovered market rows only.
REGISTRY_COVERAGE_BASIS = 'DISCOVERED_MARKET_ROWS_V2'
PREVIOUS_REGISTRY_COVERAGE_BASIS = 'ANY_FEED_ROW_V1'


@dataclass(frozen=True)
class GuardParameters:
    """Frozen thresholds; no environment variable changes them."""
    lp_pullable_min_liq_to_mcap: float = 1.0
    young_pool_max_age_minutes: float = 720.0
    fake_mcap_min_usd: float = 20_000_000.0
    fake_mcap_max_liq_to_mcap: float = 0.02
    fake_mcap_max_age_minutes: float = 14 * 1440.0
    ticker_reuse_max_age_minutes: float = 14 * 1440.0
    # Rule 6: continuous registry coverage needed before 'no reuse found' counts.
    ticker_registry_min_coverage_minutes: float = 1440.0


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


def coverage_ahead_ms(observed_until, reference) -> float:
    """How far ``observed_until`` lies ahead of ``reference`` (ms) when beyond the clock skew, else 0.

    TICKER_COVERAGE_CLOCK_V1: coverage stamped more than COVERAGE_MAX_AHEAD_MS
    after the reference clock was never observed and is dropped by the caller.
    """
    until, current = _finite(observed_until), _finite(reference)
    if until is None or current is None:
        return 0.0
    ahead = until - current
    return ahead if ahead > COVERAGE_MAX_AHEAD_MS else 0.0


def normalize_ticker(symbol) -> str:
    """Alphanumerics only, casefolded ('D O T F' -> 'dotf', 'CATE!' -> 'cate')."""
    if not isinstance(symbol, str):
        return ''
    return ''.join(ch for ch in symbol if ch.isalnum()).casefold()


def ticker_of(coin: dict) -> str:
    """Normalized ticker of an observation; '' when missing or the feed's placeholder ('TOKEN')."""
    ticker = normalize_ticker(coin.get('symbol')) if isinstance(coin, dict) else ''
    return '' if ticker in PLACEHOLDER_TICKERS else ticker


def observation_time(coin: dict, now):
    """When the market was observed: updatedAt when a valid stamp at most 5 s after ``now``, else ``now``.

    The same basis as heat_veto.PairHistory; a stale published feed keeps its old stamps.
    """
    current = _finite(now)
    stamp = _finite(coin.get('updatedAt')) if isinstance(coin, dict) else None
    if stamp is not None and stamp > 0 and (current is None or stamp <= current + OBSERVATION_CLOCK_SKEW_MS):
        return stamp if current is None else min(stamp, current)
    return current


def market_observation(coin: dict) -> bool:
    """True unless the row's ``sources`` name only a held position (REGISTRY_COVERAGE_BASIS).

    A held coin that discovery did not return reaches the feed under
    HELD_POSITION_SOURCES only (market_monitor.scan_once); such a row says
    nothing about the rest of the market. Rows without a source list (every
    production row has one) count as market rows.
    """
    if not isinstance(coin, dict):
        return False
    sources = coin.get('sources')
    if isinstance(sources, str):
        sources = [sources]
    if not isinstance(sources, (list, tuple)) or not sources:
        return True
    return not all(isinstance(source, str) and source in HELD_POSITION_SOURCES for source in sources)


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


def _path_key(path) -> str:
    return os.path.normcase(os.path.abspath(os.fspath(path)))


def _identity(coin: dict):
    mint = coin.get('address')
    pair = coin.get('pairAddress')
    mint = mint.strip() if isinstance(mint, str) else ''
    pair = pair.strip() if isinstance(pair, str) else ''
    return mint, pair


def remove_stale_sidecar_temps(path, max_age_seconds=SIDECAR_TEMP_STALE_SECONDS, now=None) -> list:
    """Delete save() temporaries of this sidecar abandoned by a hard stop; returns the names removed.

    Only names ``TickerRegistry.save`` creates for this exact sidecar, in its own
    directory, are candidates (``.<sidecar name>.<8 characters>.tmp``, regular
    files whose os.stat mtime is at least ``max_age_seconds`` old): the sidecar
    itself, other services' sidecars and temporaries, ledgers and every other
    file are never touched. Never raises; a file still open elsewhere is left for
    a later sweep.
    """
    if path is None:
        return []
    path = Path(path)
    prefix, suffix = '.' + path.name + '.', '.tmp'
    current = time.time() if now is None else now
    removed = []
    try:
        entries = list(os.scandir(path.parent))
    except OSError:
        return removed
    for entry in entries:
        name = entry.name
        if not (name.startswith(prefix) and name.endswith(suffix)
                and _SIDECAR_TEMP_CORE.fullmatch(name[len(prefix):len(name) - len(suffix)])):
            continue
        try:
            if not entry.is_file(follow_symlinks=False):
                continue
            # os.stat, not the cached directory entry: Windows can report a stale mtime
            # there for a file another handle is still writing.
            if current - os.stat(entry.path, follow_symlinks=False).st_mtime < max_age_seconds:
                continue
            os.unlink(entry.path)
        except OSError:
            continue
        removed.append(name)
    return removed


class TickerRegistry:
    """Past-only record of (mint, pool) -> normalized ticker with first/last sighting.

    ``observe(feed, now)`` registers every coin of one scan. ``reusers`` lists
    other-mint pools of a ticker first seen at or before ``now``. Entries not
    seen for ``retention_ms`` are pruned and at most ``max_entries`` are kept
    (least recently seen evicted first), so memory and the sidecar stay
    bounded. All methods are thread-safe and never raise on persistence
    problems; a failed save is reported in ``status()``. A registry whose own
    coverage after loading its sidecar is not current or shorter than 24 h is
    seeded from ``seed_paths``; while running it seeds again when a gap
    restarts its coverage and at most every ``reseed_interval_ms`` while its
    coverage is under 24 h, and merges the siblings' sightings (never their
    coverage) at most every ``sighting_merge_interval_ms`` while it vouches.
    An own sidecar that cannot be read is never overwritten during the run
    (see ``_load``).

    Coverage: ``mark_observed(at, now)`` records one market observation
    (``observe`` calls it with the newest observation time of the feed's
    discovered market rows and its scan clock; a feed of held-position rows
    only marks nothing, see ``market_observation``).
    ``coverage_ms(now)`` is how long the registry has observed the market
    without a gap longer than ``max_gap_ms``; 0 while it never observed, when
    its last observation is more than ``max_gap_ms`` before ``now``, or when
    that observation is stamped more than COVERAGE_MAX_AHEAD_MS after ``now``
    (TICKER_COVERAGE_CLOCK_V1).
    """

    def __init__(self, path=None, *, retention_ms=REGISTRY_RETENTION_DAYS * DAY_MS,
                 max_entries=REGISTRY_MAX_ENTRIES, save_interval_ms=REGISTRY_SAVE_INTERVAL_MS,
                 prune_interval_ms=60_000, clock=None, seed_paths=(), max_gap_ms=REGISTRY_MAX_GAP_MS,
                 reseed_interval_ms=REGISTRY_RESEED_INTERVAL_MS,
                 sighting_merge_interval_ms=REGISTRY_SIGHTING_MERGE_INTERVAL_MS):
        self.path = Path(path) if path else None
        self.retention_ms = max(DAY_MS, int(retention_ms))
        self.max_entries = max(16, int(max_entries))
        self.save_interval_ms = max(0, int(save_interval_ms))
        self.prune_interval_ms = max(0, int(prune_interval_ms))
        self.max_gap_ms = max(60_000, int(max_gap_ms))
        self.reseed_interval_ms = max(0, int(reseed_interval_ms))
        self.sighting_merge_interval_ms = max(0, int(sighting_merge_interval_ms))
        self.clock = clock or (lambda: int(time.time() * 1000))
        own = _path_key(self.path) if self.path is not None else None
        seeds = {}
        for seed in seed_paths or ():
            if seed and _path_key(seed) != own:
                seeds.setdefault(_path_key(seed), Path(seed))
        self.seed_paths = tuple(seeds.values())
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
        # Effective memory horizon (finding: a full cap evicts before 14 days).
        self.cap_evictions = 0
        self._cap_horizon_ms = None
        self._cap_evicted_at = None
        self._oldest_last_seen = None
        # Continuous market coverage (persisted): start, newest observation, restarts.
        self._covered_since = None
        self._observed_until = None
        self.coverage_resets = 0
        self._last_gap_ms = None
        # Non-empty scans whose rows named only held positions (no coverage marked; not persisted).
        self.held_only_scans = 0
        self.loaded_version = None
        self.seed_status = {}
        self.seeded_entries = 0
        self.coverage_adopted_from = None
        # Running seeds (TICKER_REGISTRY_SEED_V3): count, last attempt, pending after a gap.
        self.reseeds = 0
        self._seeded_at = None
        self._coverage_restarted = False
        # Sibling sightings merged while the coverage vouches (TICKER_REGISTRY_SEED_V4).
        self.sighting_merges = 0
        self.sighting_merge_new_pools = 0
        self.sighting_merge_status = {}
        self._sightings_merged_at = None
        # Coverage stamped ahead of the reference clock and dropped (TICKER_COVERAGE_CLOCK_V1).
        self.future_coverage_drops = 0
        self._last_future_ahead_ms = None
        # Own sidecar that could not be read (or backed up when corrupt): kept, never
        # overwritten during the run, read again at most every SIDECAR_REREAD_INTERVAL_MS.
        self._own_sidecar_kept = False
        self._own_reread_at = None
        self.own_reread_attempts = 0
        self.corrupt_backup = None
        # TICKER_REGISTRY_TEMP_SWEEP_V1: temporaries a hard-stopped save() left behind.
        self.stale_temps_removed = 0
        self._next_temp_sweep = None
        if self.path is not None:
            self._sweep_stale_temps()
            self._load()
        # Seeded unless its own coverage already vouches (current and >= 24 h): a sidecar
        # left by a short earlier run (services started before the first-deploy seed)
        # still adopts a current sibling's longer coverage.
        if self.seed_paths and not self._coverage_vouches(self.clock()):
            self._seed()

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
        ticker = ticker_of(coin)
        if not mint or not pair or not ticker:
            return False
        with self._lock:
            added = self._add((mint, pair), ticker, stamp, stamp)
            self._dirty = True
            return added

    def observe(self, feed, now) -> int:
        """Register a whole scan; prunes and saves at most once per interval.

        Every row is registered. Coverage is marked at the newest observation
        time (``observation_time``) of the feed's discovered market rows
        (``market_observation``), so an empty scan, a stale published feed or a
        feed holding only held positions (a discovery outage with a position
        open) never extends the registry's coverage. Coverage stamped more than
        COVERAGE_MAX_AHEAD_MS after ``now`` is dropped first
        (TICKER_COVERAGE_CLOCK_V1), so the observation starts coverage again.
        """
        stamp = _finite(now)
        if stamp is not None:
            self.drop_coverage_ahead(stamp)
        added = 0
        newest = None
        held_only = False
        for coin in feed or ():
            added += int(self.observe_coin(coin, now))
            if not isinstance(coin, dict):
                continue
            if not market_observation(coin):
                held_only = True
                continue
            observed = observation_time(coin, now)
            if observed is not None and (newest is None or observed > newest):
                newest = observed
        if newest is not None:
            self.mark_observed(newest, stamp)
        elif held_only:
            with self._lock:
                self.held_only_scans += 1
        if stamp is not None:
            self.maybe_reread_own_sidecar(stamp)
            self.maybe_reseed(stamp)
            self.prune(stamp)
            self.maybe_save(stamp)
        return added

    def drop_coverage_ahead(self, reference) -> bool:
        """Drop coverage stamped more than COVERAGE_MAX_AHEAD_MS after ``reference`` (TICKER_COVERAGE_CLOCK_V1).

        Such an ``observed_until`` was never observed (a sidecar written with a
        fixed future test clock, a backward wall-clock step): kept, it made the
        coverage current forever and hid every gap. The next observation starts
        coverage again and a registry with seed paths seeds at once. Returns
        True when it dropped.
        """
        with self._lock:
            ahead = coverage_ahead_ms(self._observed_until, reference)
            if not ahead:
                return False
            self._covered_since = self._observed_until = None
            self.future_coverage_drops += 1
            self._last_future_ahead_ms = ahead
            self._coverage_restarted = True
            self._dirty = True
            return True

    def mark_observed(self, at, now=None) -> None:
        """Record one market observation at ``at`` (ms) for the coverage bookkeeping.

        ``now`` (the caller's scan clock; ``observe`` passes it) drops coverage
        stamped ahead of it first (``drop_coverage_ahead``); without it the
        stamps alone are compared (the offline seed builder's replay).
        """
        stamp = _finite(at)
        if stamp is None:
            return
        reference = _finite(now)
        if reference is not None:
            self.drop_coverage_ahead(reference)
        with self._lock:
            until = self._observed_until
            if until is not None and stamp <= until:
                return
            if self._covered_since is None:
                self._covered_since = stamp
            elif until is not None and stamp - until > self.max_gap_ms:
                # Sightings during the gap are unknown: coverage starts again, and a
                # registry with seed paths seeds at its next observe (maybe_reseed).
                self._covered_since = stamp
                self.coverage_resets += 1
                self._last_gap_ms = stamp - until
                self._coverage_restarted = True
            self._observed_until = stamp
            self._dirty = True

    def coverage_current(self, now) -> bool:
        """True when the last observation is at most ``max_gap_ms`` before ``now``.

        False while it is stamped more than COVERAGE_MAX_AHEAD_MS after ``now``
        (TICKER_COVERAGE_CLOCK_V1): a future stamp never vouches.
        """
        stamp = _finite(now)
        with self._lock:
            since, until = self._covered_since, self._observed_until
        return (stamp is not None and since is not None and until is not None
                and stamp - until <= self.max_gap_ms and until - stamp <= COVERAGE_MAX_AHEAD_MS)

    def coverage_ms(self, now) -> float:
        """Continuous observation before ``now`` (ms): 0 when never observed or the coverage lapsed."""
        stamp = _finite(now)
        if not self.coverage_current(stamp):
            return 0.0
        with self._lock:
            since = self._covered_since
        return max(0.0, stamp - since)

    def _coverage_vouches(self, now) -> bool:
        """True when coverage at ``now`` is current and at least the 24 h of rule 6."""
        return self.coverage_ms(now) >= PARAMS.ticker_registry_min_coverage_minutes * 60_000

    def maybe_reseed(self, now) -> bool:
        """Seed a running registry from ``seed_paths`` when its coverage does not vouch (never raises).

        At once after a gap restarted the coverage (``mark_observed``), else
        at most every ``reseed_interval_ms`` while coverage at ``now`` is under
        24 h, so a registry whose siblings vouch (or start vouching later) does
        not wait 24 h or for a restart. While the coverage vouches it merges the
        siblings' sightings only (``maybe_merge_sibling_sightings``,
        TICKER_REGISTRY_SEED_V4). Returns True when it seeded.
        """
        stamp = _finite(now)
        if not self.seed_paths or stamp is None:
            return False
        with self._lock:
            restarted = self._coverage_restarted
            last = self._seeded_at
        if self._coverage_vouches(stamp):
            with self._lock:
                self._coverage_restarted = False
            try:
                self.maybe_merge_sibling_sightings(stamp)
            except Exception:
                # Sightings only add memory; a failed merge is retried at the next interval.
                pass
            return False
        if (not restarted and last is not None
                and 0 <= stamp - last < self.reseed_interval_ms):
            return False
        with self._lock:
            self._coverage_restarted = False
            self.reseeds += 1
        try:
            self._seed(stamp)
        except Exception:
            # A seed only adds memory; a failed one leaves the registry warming (fail closed).
            return False
        return True

    def maybe_merge_sibling_sightings(self, now) -> int:
        """Merge the siblings' sightings, never their coverage (TICKER_REGISTRY_SEED_V4); never raises.

        At most every ``sighting_merge_interval_ms`` (a seed counts as a merge).
        Main observes the untrimmed feed, including the Gecko new pools that
        bounded_feed cuts; the Lab and the tape see only main's trimmed feed, so
        without this a relaunch sibling seen only by main never reached their
        registries while they vouched. Sightings only add memory (they can only
        block more). Returns the number of new pools.
        """
        stamp = _finite(now)
        if not self.seed_paths or stamp is None:
            return 0
        with self._lock:
            last = self._sightings_merged_at
            if last is not None and 0 <= stamp - last < self.sighting_merge_interval_ms:
                return 0
            self._sightings_merged_at = stamp
            self.sighting_merges += 1
        added = 0
        for path in self.seed_paths:
            status, rows, _coverage = self._read_sidecar(path, shared=True)
            if status == 'OK':
                with self._lock:
                    before = len(self._entries)
                    self._merge_rows(rows)
                    added += len(self._entries) - before
            self.sighting_merge_status[path.name] = 'MERGED' if status == 'OK' else status
        if added:
            with self._lock:
                self.sighting_merge_new_pools += added
                self._dirty = True
            self.prune(stamp, force=True)
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
                evicted = order[:len(self._entries) - keep]
                for key, _row in evicted:
                    self._remove(key)
                    removed += 1
                # Sightings last seen before this are forgotten earlier than the retention.
                self.cap_evictions += len(evicted)
                self._cap_horizon_ms = stamp - evicted[-1][1][2]
                self._cap_evicted_at = stamp
                # Memory is complete only after the newest evicted sighting.
                if self._covered_since is not None:
                    self._covered_since = max(self._covered_since, evicted[-1][1][2])
            self._oldest_last_seen = min((row[2] for row in self._entries.values()), default=None)
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

    def coverage_status(self, now=None) -> dict:
        """Published coverage at ``now`` (the registry clock when None)."""
        stamp = _finite(now)
        stamp = _finite(self.clock()) if stamp is None else stamp
        coverage = self.coverage_ms(stamp)
        with self._lock:
            last_gap = self._last_gap_ms
            ahead = self._last_future_ahead_ms
            return {'covered_since': self._covered_since, 'observed_until': self._observed_until,
                    'coverage_hours': round(coverage / HOUR_MS, 2),
                    'min_coverage_hours': PARAMS.ticker_registry_min_coverage_minutes / 60,
                    'warming': coverage < PARAMS.ticker_registry_min_coverage_minutes * 60_000,
                    'max_gap_minutes': self.max_gap_ms / 60_000, 'resets': self.coverage_resets,
                    'last_gap_minutes': None if last_gap is None else round(last_gap / 60_000, 1),
                    'adopted_from': self.coverage_adopted_from, 'basis': REGISTRY_COVERAGE_BASIS,
                    'held_only_scans': self.held_only_scans,
                    # TICKER_COVERAGE_CLOCK_V1: coverage stamped ahead of the clock and dropped.
                    'clock_version': COVERAGE_CLOCK_VERSION,
                    'future_drops': self.future_coverage_drops,
                    'last_future_ahead_minutes': None if ahead is None else round(ahead / 60_000, 1)}

    def status(self) -> dict:
        coverage = self.coverage_status()
        with self._lock:
            reference = self._pruned_at
            oldest = self._oldest_last_seen
            return {'version': REGISTRY_VERSION, 'loaded_version': self.loaded_version,
                    'coverage': coverage, 'entries': len(self._entries),
                    'tickers': len(self._by_ticker), 'max_entries': self.max_entries,
                    'retention_days': self.retention_ms / DAY_MS,
                    # Effective horizon at the last prune: the oldest sighting kept and,
                    # when the entry cap evicted, the age of the newest evicted sighting.
                    'oldest_last_seen_days': (None if oldest is None or reference is None
                                              else round(max(0.0, reference - oldest) / DAY_MS, 2)),
                    'cap_evictions': self.cap_evictions,
                    'cap_limited_horizon_days': (None if self._cap_horizon_ms is None
                                                 else round(max(0.0, self._cap_horizon_ms) / DAY_MS, 2)),
                    'cap_evicted_at': self._cap_evicted_at,
                    'persistent': self.path is not None,
                    'sidecar': self.path.name if self.path is not None else None,
                    'load_status': self.load_status, 'load_skipped_rows': self.load_skipped_rows,
                    'sidecar_read': {'version': SIDECAR_READ_VERSION,
                                     'kept_not_overwritten': self._own_sidecar_kept,
                                     'reread_attempts': self.own_reread_attempts,
                                     'corrupt_backup': self.corrupt_backup},
                    'stale_temps': {'version': SIDECAR_TEMP_SWEEP_VERSION,
                                    'removed': self.stale_temps_removed},
                    'seed': {'version': REGISTRY_SEED_VERSION, 'sources': dict(self.seed_status),
                             'entries': self.seeded_entries, 'running_reseeds': self.reseeds,
                             'last_seed_at': self._seeded_at,
                             # V4: sibling sightings merged while the coverage vouches.
                             'sighting_merges': self.sighting_merges,
                             'sighting_merge_new_pools': self.sighting_merge_new_pools,
                             'sighting_merge_sources': dict(self.sighting_merge_status),
                             'last_sighting_merge_at': self._sightings_merged_at},
                    'saved_at': self._saved_at, 'saves': self.saves, 'save_error': self.save_error}

    # -------------------------------------------------------- persistence --
    @staticmethod
    def _read_sidecar(path, *, shared=False, attempts=SIDECAR_READ_ATTEMPTS, reference=None):
        """('OK', rows, coverage), 'MISSING', 'UNREADABLE' or 'CORRUPT' (with [], None); never raises.

        ``coverage`` is (version, covered_since, observed_until, resets,
        ahead_ms); the stamps are None for a legacy sidecar or invalid values,
        and also when ``reference`` is given and ``observed_until`` lies more
        than COVERAGE_MAX_AHEAD_MS after it (TICKER_COVERAGE_CLOCK_V1: never
        observed; ``ahead_ms`` then says by how much, else None). ``shared``
        reads with delete sharing (a sibling service may be replacing it).
        An I/O error (a Windows sharing violation, a permission error) is
        retried ``attempts`` times and then reported as 'UNREADABLE': the file
        may be intact. Only content that reads but does not decode or parse as
        a ticker registry is 'CORRUPT'.
        """
        text = None
        for attempt in range(max(1, int(attempts))):
            try:
                if not path.exists():
                    return 'MISSING', [], None
                text = read_shared_text(path) if shared else path.read_text(encoding='utf-8')
                break
            except FileNotFoundError:
                return 'MISSING', [], None
            except ValueError:
                # Undecodable bytes (UnicodeDecodeError): the content is corrupt.
                return 'CORRUPT', [], None
            except Exception:
                if attempt + 1 >= max(1, int(attempts)):
                    return 'UNREADABLE', [], None
                time.sleep(.02 * (attempt + 1))
        try:
            data = json.loads(text)
            version = data.get('version') if isinstance(data, dict) else None
            if version != REGISTRY_VERSION and version not in LEGACY_REGISTRY_VERSIONS:
                raise ValueError('unsupported ticker registry')
            rows = data.get('entries')
            if not isinstance(rows, list):
                raise ValueError('ticker registry without entries')
            since = until = ahead = None
            resets = 0
            if version == REGISTRY_VERSION:
                since, until = _finite(data.get('covered_since')), _finite(data.get('observed_until'))
                if since is None or until is None or since > until:
                    since = until = None
                resets = max(0, int(_finite(data.get('coverage_resets')) or 0))
            if until is not None and reference is not None and coverage_ahead_ms(until, reference):
                ahead = coverage_ahead_ms(until, reference)
                since = until = None
            return 'OK', rows, (version, since, until, resets, ahead)
        except Exception:
            return 'CORRUPT', [], None

    @staticmethod
    def read_sidecar(path, *, reference=None):
        """Public read-only form of ``_read_sidecar`` (the offline seed builder)."""
        return TickerRegistry._read_sidecar(Path(path), reference=reference)

    def _note_future_drop(self, ahead):
        """Count coverage dropped for lying ahead of the clock (caller holds the lock)."""
        if ahead:
            self.future_coverage_drops += 1
            self._last_future_ahead_ms = ahead

    def _merge_own_coverage(self, since, until):
        """Merge the coverage of this registry's own sidecar read late (caller holds the lock).

        The in-memory span (observations since the start of this run, or a
        sibling's) and the sidecar's span are one continuous coverage when
        neither starts more than ``max_gap_ms`` after the other ended; the
        earliest start is then kept. Otherwise the in-memory span stays.
        """
        if since is None or until is None:
            return
        mine_since, mine_until = self._covered_since, self._observed_until
        if mine_since is None or mine_until is None:
            self._covered_since, self._observed_until = since, until
            return
        if mine_since - until <= self.max_gap_ms and since - mine_until <= self.max_gap_ms:
            self._covered_since = min(mine_since, since)
            self._observed_until = max(mine_until, until)

    def _backup_corrupt_sidecar(self):
        """Copy a corrupt own sidecar to '<sidecar>.corrupt-<ms>' (no service reads it); None on failure."""
        try:
            stamp = int(_finite(self.clock()) or 0)
            backup = self.path.with_name(f'{self.path.name}.corrupt-{stamp}')
            suffix = 0
            while backup.exists():
                suffix += 1
                backup = self.path.with_name(f'{self.path.name}.corrupt-{stamp}-{suffix}')
            shutil.copyfile(self.path, backup)
            return backup.name
        except Exception:
            return None

    def _start_after_unusable_sidecar(self, status):
        """'UNREADABLE' or 'CORRUPT' own sidecar: start empty; keep the file unless a corrupt copy is saved."""
        backup = self._backup_corrupt_sidecar() if status == 'CORRUPT' else None
        with self._lock:
            self._entries, self._by_ticker = {}, {}
            if backup is not None:
                # A corrupt sidecar is replaced at the next save; its bytes stay in the copy.
                self.corrupt_backup = backup
                self.load_status = 'CORRUPT_STARTED_EMPTY'
                self._own_sidecar_kept = False
            else:
                # Possibly intact (an I/O error) or corrupt without a copy: never overwrite it
                # during this run; read it again later (maybe_reread_own_sidecar).
                self.load_status = ('UNREADABLE_NOT_OVERWRITTEN' if status == 'UNREADABLE'
                                    else 'CORRUPT_NOT_OVERWRITTEN')
                self._own_sidecar_kept = True

    def maybe_reread_own_sidecar(self, now, *, force=False) -> bool:
        """Read a kept own sidecar again (at most every SIDECAR_REREAD_INTERVAL_MS); never raises.

        Once it reads, its sightings and coverage are merged and saving
        resumes ('LOADED_AFTER_REREAD'); a corrupt one is copied aside first; a
        vanished one simply resumes saving. ``force`` skips the interval (the
        clean-stop flush). Returns True when the sidecar is no longer kept.
        """
        stamp = _finite(now)
        with self._lock:
            if not self._own_sidecar_kept or self.path is None or stamp is None:
                return False
            last = self._own_reread_at
            if not force and last is not None and 0 <= stamp - last < SIDECAR_REREAD_INTERVAL_MS:
                return False
            self._own_reread_at = stamp
            self.own_reread_attempts += 1
        status, rows, coverage = self._read_sidecar(self.path, reference=stamp)
        if status == 'UNREADABLE':
            return False
        if status == 'CORRUPT':
            backup = self._backup_corrupt_sidecar()
            if backup is None:
                return False
            with self._lock:
                self.corrupt_backup = backup
                self.load_status = 'CORRUPT_STARTED_EMPTY'
                self._own_sidecar_kept = False
                self._dirty = True
            return True
        with self._lock:
            if status == 'OK':
                version, since, until, resets, ahead = coverage
                self.load_skipped_rows = self._merge_rows(rows)
                # Coverage stamped ahead of the clock was dropped by the read (never merged).
                self._note_future_drop(ahead)
                self._merge_own_coverage(since, until)
                self.coverage_resets = max(self.coverage_resets, resets)
                self.loaded_version = version
                self.load_status = 'LOADED_AFTER_REREAD'
            else:
                self.load_status = 'MISSING_STARTED_EMPTY'
            self._own_sidecar_kept = False
            self._dirty = True
        self.prune(stamp, force=True)
        return True

    def merge_rows(self, rows) -> tuple:
        """Add valid sidecar rows (sightings only, never coverage); returns (new pools, skipped rows)."""
        with self._lock:
            before = len(self._entries)
            skipped = self._merge_rows(rows)
            added = len(self._entries) - before
            if added:
                self._dirty = True
        return added, skipped

    def _merge_rows(self, rows) -> int:
        """Add valid sidecar rows (caller holds the lock); returns the number skipped."""
        skipped = 0
        for row in rows:
            try:
                mint, pair, ticker, first_seen, last_seen = row
                first, last = _finite(first_seen), _finite(last_seen)
                if (not isinstance(mint, str) or not mint or not isinstance(pair, str) or not pair
                        or not isinstance(ticker, str) or not ticker or normalize_ticker(ticker) != ticker
                        or ticker in PLACEHOLDER_TICKERS
                        or first is None or last is None or first > last):
                    raise ValueError('invalid row')
            except (TypeError, ValueError):
                skipped += 1
                continue
            self._add((mint, pair), ticker, first, last)
        return skipped

    def _load(self):
        reference = _finite(self.clock())
        status, rows, coverage = self._read_sidecar(self.path, reference=reference)
        if status == 'MISSING':
            self.load_status = 'MISSING_STARTED_EMPTY'
            return
        if status in ('CORRUPT', 'UNREADABLE'):
            # Starts empty. An unreadable sidecar may be intact: it is kept and read again;
            # a corrupt one is copied to '<sidecar>.corrupt-<ms>' before a save replaces it.
            self._start_after_unusable_sidecar(status)
            return
        version, since, until, resets, ahead = coverage
        with self._lock:
            self.load_skipped_rows = self._merge_rows(rows)
            self.load_status = 'LOADED'
            self.loaded_version = version
            # A legacy sidecar carries no coverage: it starts at the next observation. Nor
            # does coverage stamped ahead of the clock (dropped by the read; the registry
            # seeds and warms instead of vouching for a span it never observed).
            self._covered_since, self._observed_until = since, until
            self.coverage_resets = resets
            self._note_future_drop(ahead)
            # Saved again without the future stamp at the next save.
            self._dirty = bool(ahead)
        self.prune(self.clock(), force=True)

    def _seed(self, reference=None):
        """Merge sibling sidecars into a registry whose coverage does not vouch yet (read-only; never raises).

        Adopts the earliest ``covered_since`` of the siblings whose own coverage
        is current at ``reference`` (the registry clock when None; their merged
        sightings cover it). When the registry's own coverage is current too (a
        short earlier run, or a running registry after a gap), both spans end
        within the gap of the reference, so their union is continuous and the
        earliest start of the two is kept. While the registry's own sidecar is
        kept unread (``UNREADABLE_NOT_OVERWRITTEN``) sightings are merged but
        no coverage is adopted: its own memory is unknown (fail closed). A
        sibling's coverage stamped more than COVERAGE_MAX_AHEAD_MS after the
        reference is never adopted, and neither is its own
        (TICKER_COVERAGE_CLOCK_V1). A seed also counts as a sighting merge
        (``maybe_merge_sibling_sightings``).
        """
        reference = _finite(self.clock() if reference is None else reference)
        if reference is not None and self.drop_coverage_ahead(reference):
            with self._lock:
                # This seed is the one a dropped coverage asks for.
                self._coverage_restarted = False
        own_current = self.coverage_current(reference)
        with self._lock:
            own = ((self._covered_since, self._observed_until, None) if own_current else None)
            self._seeded_at = reference
            self._sightings_merged_at = reference
            adopt = not self._own_sidecar_kept
        current = []    # (covered_since, observed_until, name) of siblings with current coverage
        for path in self.seed_paths:
            status, rows, coverage = self._read_sidecar(path, shared=True, reference=reference)
            added = 0
            if status == 'OK':
                with self._lock:
                    before = len(self._entries)
                    self._merge_rows(rows)
                    added = len(self._entries) - before
                _version, since, until, _resets, ahead = coverage
                if (since is not None and until is not None and reference is not None
                        and reference - until <= self.max_gap_ms):
                    current.append((since, until, path.name))
                # Sightings merged; coverage stamped ahead of the clock ignored (and named).
                status = 'SEEDED_COVERAGE_AHEAD_IGNORED' if ahead else 'SEEDED'
            self.seed_status[path.name] = status
            self.seeded_entries += added
        if current and adopt:
            earliest = min(current)
            spans = current + ([own] if own is not None else [])
            with self._lock:
                self._covered_since = min(row[0] for row in spans)
                self._observed_until = max(row[1] for row in spans)
                if own is None or earliest[0] < own[0]:
                    self.coverage_adopted_from = earliest[2]
                self._dirty = True
        if self.seeded_entries:
            with self._lock:
                self._dirty = True
        if (self.seeded_entries or current) and reference is not None:
            self.prune(reference, force=True)

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
        """Save now when there are unsaved observations (shutdown path).

        A kept own sidecar is read once more first; if it still cannot be read
        it is left untouched (this run's memory is lost, the file's is not).
        """
        if self.path is None:
            return False
        try:
            self.maybe_reread_own_sidecar(self.clock(), force=True)
        except Exception:
            pass
        with self._lock:
            dirty = self._dirty
        return self.save() if dirty else False

    def _sweep_stale_temps(self, *, force=True) -> int:
        """Delete this sidecar's abandoned save() temporaries (start, then at most every 10 min)."""
        if self.path is None:
            return 0
        current = time.monotonic()
        if not force and self._next_temp_sweep is not None and current < self._next_temp_sweep:
            return 0
        self._next_temp_sweep = current + SIDECAR_TEMP_STALE_SECONDS
        removed = len(remove_stale_sidecar_temps(self.path))
        self.stale_temps_removed += removed
        return removed

    def save(self, now=None) -> bool:
        """Atomically replace the sidecar (temp file + fsync + replace); never raises.

        Refused while the own sidecar is kept unread (it may hold intact
        memory this run has not seen); the observations stay dirty and are
        saved once ``maybe_reread_own_sidecar`` has merged it. At most every
        10 minutes it first deletes this sidecar's save() temporaries that a
        hard stop abandoned (the one left by this process's own start is too
        young to go at start).
        """
        if self.path is None:
            return False
        try:
            self._sweep_stale_temps(force=False)
        except Exception:
            pass
        stamp = _finite(now)
        stamp = self.clock() if stamp is None else stamp
        with self._lock:
            if self._own_sidecar_kept:
                self.save_error = 'OWN_SIDECAR_KEPT_UNREAD'
                return False
            rows = sorted([mint, pair, row[0], row[1], row[2]]
                          for (mint, pair), row in self._entries.items())
            coverage = {'covered_since': self._covered_since, 'observed_until': self._observed_until,
                        'coverage_resets': self.coverage_resets}
            self._dirty = False
            self._saved_at = stamp
        text = json.dumps({'version': REGISTRY_VERSION, 'guard_version': VERSION, 'saved_at': stamp,
                           'retention_days': self.retention_ms / DAY_MS, **coverage, 'entries': rows},
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
    the observed liquidity, normalized ticker, other-mint pools of the ticker
    and the registry's continuous coverage at ``now`` (hours).
    """
    coin = coin if isinstance(coin, dict) else {}
    liquidity = liquidity_usd(coin)
    cap = market_cap_usd(coin)
    age = age_minutes(coin, now)
    mint, pair = _identity(coin)
    ticker = ticker_of(coin)
    coverage_of = getattr(registry, 'coverage_ms', None)
    # A registry without coverage bookkeeping never vouches for 'no reuse' (fail closed).
    coverage = _finite(coverage_of(now)) if callable(coverage_of) else None
    coverage = 0.0 if coverage is None else max(0.0, coverage)
    result = {'version': VERSION, 'blocked': True, 'reasons': [],
              'liq_mcap': None if liquidity is None or cap is None else liquidity / cap,
              'age_min': age, 'mcap': cap, 'liquidity_usd': liquidity, 'ticker': ticker or None,
              'ticker_reused_by': 0, 'registry_version': REGISTRY_VERSION,
              'registry_coverage_h': round(coverage / HOUR_MS, 2) if registry is not None else None}
    # Only rules 5 and 6 (pools under 14 days) read the ticker: an established pool
    # whose symbol normalizes to nothing or to the placeholder is judged without it.
    needs_ticker = age is not None and age < params.ticker_reuse_max_age_minutes
    if (liquidity is None or cap is None or age is None or not mint or not pair
            or (needs_ticker and not ticker) or registry is None):
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
        elif coverage < params.ticker_registry_min_coverage_minutes * 60_000:
            # 'No other mint seen' means nothing until the registry has watched long enough.
            reasons.append('rug_ticker_registry_warming')
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
            'ticker': result.get('ticker'), 'ticker_reused_by': int(result.get('ticker_reused_by') or 0),
            'registry_version': result.get('registry_version', REGISTRY_VERSION),
            'registry_coverage_h': rounded(result.get('registry_coverage_h'), 2)}


def config(params: GuardParameters = PARAMS) -> dict:
    """Published definition; part of every effective config hash that applies it."""
    return {'version': VERSION, 'registry_version': REGISTRY_VERSION,
            'reasons_in_order': list(REASONS), 'parameters': asdict(params),
            'market_cap_basis': 'marketCap, else fdv', 'age_basis': 'pairCreatedAt at decision time',
            'ticker_normalization': 'alphanumerics only, casefolded',
            'unknown_ticker_placeholders': sorted(PLACEHOLDER_TICKERS),
            'ticker_input_rule': ('a missing ticker (empty after normalization, or a placeholder) is '
                                  'rug_input_unknown only when pair age < 14 days; rules 5 and 6 alone read it'),
            'ticker_reuse_rule': 'another pool with a DIFFERENT mint registered at or before now',
            'ticker_registry_warming_rule': ('pair age < 14 days, no reuse found and less than 24 h of '
                                             'continuous registry coverage at now'),
            'registry': {'version': REGISTRY_VERSION, 'legacy_versions_loaded': list(LEGACY_REGISTRY_VERSIONS),
                         'retention_days': REGISTRY_RETENTION_DAYS,
                         'max_entries': REGISTRY_MAX_ENTRIES,
                         'save_interval_seconds': REGISTRY_SAVE_INTERVAL_MS // 1000,
                         'cap_eviction': ('least recently seen first, down to 90%; the effective horizon is '
                                          'published and coverage restarts at the newest evicted sighting'),
                         'coverage': {'min_hours': params.ticker_registry_min_coverage_minutes / 60,
                                      'max_gap_minutes': REGISTRY_MAX_GAP_MS // 60_000,
                                      'basis_version': REGISTRY_COVERAGE_BASIS,
                                      'previous_basis_version': PREVIOUS_REGISTRY_COVERAGE_BASIS,
                                      'observation_basis': ('newest updatedAt of the feed\'s discovered market '
                                                            'rows (at most 5 s after the scan clock), else the '
                                                            'scan clock; rows whose sources name only a held '
                                                            'position are registered but never mark coverage'),
                                      'held_position_sources': sorted(HELD_POSITION_SOURCES),
                                      'persisted': True,
                                      'clock': {'version': COVERAGE_CLOCK_VERSION,
                                                'max_ahead_seconds': COVERAGE_MAX_AHEAD_MS / 1000,
                                                'rule': ('observed_until more than max_ahead_seconds after the '
                                                         'reference clock (scan clock, load or seed clock) was never '
                                                         'observed: never current, dropped from the own sidecar, '
                                                         'from a sibling at seed and from memory at the next '
                                                         'observation, which starts coverage again')}},
                         'seed_version': REGISTRY_SEED_VERSION,
                         'previous_seed_version': PREVIOUS_REGISTRY_SEED_VERSION,
                         'seed_rule': ('a registry whose coverage after loading its sidecar is not current or '
                                       'shorter than 24 h merges the sibling services\' sidecars read-only (main '
                                       'engine, Lab, tape; NEO_MAIN_MARKET_STATE_PATH for personal engines), adopts '
                                       'the earliest coverage of a sibling whose coverage is current (its own '
                                       'current span kept when earlier); seeds only add sightings'),
                         'running_seed_rule': ('a running registry seeds again at once when a gap over 60 min '
                                               'restarts its coverage and at most every '
                                               f'{REGISTRY_RESEED_INTERVAL_MS // 60_000} min while its coverage is '
                                               'under 24 h'),
                         'vouching_sighting_merge_rule': ('a registry whose coverage vouches merges the sibling '
                                                          'sidecars\' sightings (never their coverage) at most every '
                                                          f'{REGISTRY_SIGHTING_MERGE_INTERVAL_MS // 60_000} min, so '
                                                          'main\'s untrimmed-feed sightings reach the Lab and the tape'),
                         'sidecar_read': {'version': SIDECAR_READ_VERSION, 'attempts': SIDECAR_READ_ATTEMPTS,
                                          'unreadable': ('an I/O error after the attempts keeps the own sidecar: '
                                                         'never overwritten during the run, read again at most every '
                                                         f'{SIDECAR_REREAD_INTERVAL_MS // 1000} s, no sibling '
                                                         'coverage adopted until it is read'),
                                          'corrupt': ('copied to <sidecar>.corrupt-<ms>, then the registry starts '
                                                      'empty and replaces it; kept like an unreadable one when the '
                                                      'copy fails')},
                         'first_deploy_seed': ('scripts/build_ticker_registry_seed.py (offline, read-only on the '
                                               'training journal, read in place; market rows mark coverage; '
                                               '--replace-stale replaces only a sidecar that does not vouch, keeping '
                                               'a copy; a seed reaches the runtime folder only with the services '
                                               'stopped: the tool refuses a runtime whose services/processes.json has '
                                               'no services/stop.request; on the first deploy only, while no '
                                               'state.ticker_registry.json exists, it may be built before Stop into a '
                                               'folder outside .runtime and copied in after Stop; it vouches only '
                                               'when the journal holds 24 h of market coverage ending within 60 min '
                                               'of the end of its run, so no seed bridges an outage over 60 min; '
                                               'procedure: docs/PAPER_RUNBOOK.md, first deploy)')},
            'fail_closed': True, 'distinct_from': 'engine_rug_guard.RUG_GUARD_V2 (unchanged)',
            'fake_market_cap_threshold_note': '2% liquidity/market cap is holdout-informed (research froze 1%); conservative, not validated',
            'is_entry_authorization': False, 'profitability_proven': False}
