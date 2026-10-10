"""Stable bounded discovery seats, with exit pins and explicit model estimates.

This decides what to observe, never whether to trade. Actual flow completeness,
safety checks and executable quotes retain their existing admission authority.

Seat shedding: a pool whose fetched transaction bodies yielded no decoded swap
after a bounded number of bodies cannot become admissible while it keeps
failing to decode, so it releases its entry/exploration seat for a cooldown.
A swap whose only defect is a missing or estimated SOL/USD reference counts as
decoded, so an FX-reference outage cannot shed every candidate at once.
Pinned exit pools are never shed. Shedding changes what is observed at zero
RPC cost; it never admits, sizes or exits anything.

Cost-first seats (V4): pools in the COST_FIRST universe
(cost_first_established.candidate, the same definition the Lab book pair uses)
are an entry-candidate branch ranked directly below estimated-feasible
main/funded candidates and above matched candidates whose modeled round trip
already exceeds the cost cap (or is unknown), which cannot pass the quote gate.
They share the existing seat budget and leases; nothing here admits them.

Personal-engine pins (V4): open positions of every PAPER engine listed in the
account registry (NEO_USER_STATE_PATH, read-only) are pinned by exact
(mint, pool) like the main engine's positions, so their exits keep exact-pool
flow coverage. Each engine's /state is read on 127.0.0.1 by a background
refresher, never on the tape poll; the poll only reads the latest snapshot.
Pools held only by personal engines are capped (NEO_TAPE_PERSONAL_PIN_LIMIT)
after main's and Lab's pins, which are never capped.

Defensive entry seats (V5): a pool that STRUCTURAL_RUG_GUARD_V1 blocks
(DEFENSIVE_ENTRY_LAYER_V1 with the scheduler's own ticker registry) cannot
be entered by any engine, so it gets no entry or exploration seat. Pins of
held positions are never affected. Every candidate group, including the
cost-first universe (cost_first_established V2 includes the structural
guard), shares this screen; nothing here admits a pool.
HEAT_VETO_STACK_V1 with the scheduler's own pair history: a hot or crashing
pool (rules (a)-(g), or an unknown price) gets no NEW entry or exploration
seat, since no engine would enter it. A pool that already holds a running
lease keeps it until the lease expires (dropping a lease on one hot poll
would reset its tape coverage); after that it competes again under the same
screen. heat_history_warming is log-only at seats: it describes the
scheduler's own history after a restart, a seat is how pool data is
gathered, and every engine enforces its own warm-up at decision and commit.
A seat serves every ledger, so POOL_LOSS_MEMORY_V1 (per ledger) does not
withhold one: a loss cooldown in one account or Lab book says nothing about
the others, and a withheld seat would leave every engine without the
COMPLETE exact-pool window it needs. Each entry path applies its own
ledger's memory before quoting.

Flow-free Lab positions (V6): a Lab book whose exits never read tape flow
(LAB_FORWARD_TESTS_V1: no flow gate at entry, no flow exit; marks come from
the shared feed or the DexScreener exact-pair refresh) does not pin its held
pool. A pin there would take an entry seat from main, the personal engines
and the flow-gated Lab books without serving any exit. A pool that main or a
personal engine also holds is still pinned by that holder.
"""
import json
import math
import os
import threading
import time
from pathlib import Path

import lab_activity
import lab_forward_tests
import cost_first_established as cost_first
import entry_defense
import funded_market_candidates
import funded_active_paper as active_paper
import paper_fast_scalp as fast_scalp
import paper_market_feasibility as feasibility
from shared_snapshot_io import read_shared_text
import winner_ensemble


FUNDED_RULES = ('EARLY', 'MOMENTUM', 'PRECISION', 'ULTRA_PRECISION')
LEASE_MS = 60_000
POLICY_VERSION = 'STABLE_COST_AWARE_TAPE_DISCOVERY_V7_FAST_SCALP_COST_FIRST'
PREVIOUS_POLICY_VERSION = 'STABLE_COST_AWARE_TAPE_DISCOVERY_V6_NO_PINS_FOR_FLOW_FREE_LAB_BOOKS'
LAB_PIN_RULE = 'LAB_POSITIONS_PINNED_UNLESS_THEIR_BOOK_NEVER_READS_FLOW'
DEFENSIVE_EXAMPLE_LIMIT = 6
SEAT_RULE = 'NO_SEAT_FOR_A_STRUCTURALLY_BLOCKED_POOL_NO_NEW_SEAT_FOR_A_HOT_POOL'
HEAT_SEAT_MODE = 'NEW_SEATS_WITHHELD_RUNNING_LEASES_KEPT_WARMING_LOG_ONLY'
# Heat reasons that never withhold a seat: the tape's own warm-up (each engine enforces its own).
SEAT_HEAT_LOG_ONLY_REASONS = frozenset({'heat_history_warming'})
SHED_MIN_BODIES = max(1, int(os.getenv('NEO_TAPE_SHED_MIN_BODIES', '40')))
SHED_COOLDOWN_MS = max(60_000, int(os.getenv('NEO_TAPE_SHED_COOLDOWN_MS', '1800000')))
SHED_REASON = 'ZERO_DECODED_SWAPS_AFTER_BODIES'
# Must match live_tape.YIELD_WINDOW_MS: a retry floor older than the recorder's
# in-memory window is equivalent to no floor and can be forgotten.
YIELD_WINDOW_MS = max(60_000, int(os.getenv('NEO_TAPE_YIELD_WINDOW_MS', '7200000')))
SHED_LIST_LIMIT = 64
# Planning notional for the cost-first universe screen: the Lab book's
# requested entry cap (strategy_lab.TRADE_NOTIONAL, same variable and default)
# and the Lab's minimum notional. Universe pools hold >= $250k liquidity, so
# the liquidity-scaled size equals this cap. It is a scheduling hint only.
COST_FIRST_PLANNING_NOTIONAL_USD = float(os.getenv('NEO_LAB_TRADE_NOTIONAL', '150'))
COST_FIRST_MIN_NOTIONAL_USD = lab_activity.MIN_NOTIONAL_USD
COST_FIRST_EXAMPLE_LIMIT = 6
# Seat groups, best first. Only GROUP_FEASIBLE pre-empts an exploration lease
# immediately; every other group shares the remaining seats in this order.
GROUP_FEASIBLE, GROUP_COST_FIRST, GROUP_OVER_BUDGET, GROUP_EXPLORATION = 0, 1, 2, 3
# Personal PAPER engines (gateway-spawned, ports from the account registry).
DEFAULT_USER_STATE_PATH = '/var/lib/neo-market/user_accounts.json'
# Refresh period of the background refresher and of each healthy port.
PERSONAL_STATE_CACHE_MS = max(1000, int(os.getenv('NEO_TAPE_PERSONAL_STATE_CACHE_MS', '5000')))
PERSONAL_STATE_TIMEOUT_SECONDS = min(2.0, max(0.1, float(
    os.getenv('NEO_TAPE_PERSONAL_STATE_TIMEOUT_SECONDS', '0.75'))))
# A port whose read failed is retried after cache_ms, doubling per further
# failure up to this ceiling. The gateway never removes a stopped engine's
# port from the registry, so dead ports accumulate; backoff bounds their cost.
PERSONAL_BACKOFF_MAX_MS = max(1000, int(os.getenv('NEO_TAPE_PERSONAL_BACKOFF_MAX_MS', '60000')))
# A last successful read older than this is published as stale. Its positions
# stay pinned (exit safety) until a successful read shows them closed or the
# port leaves the registry.
PERSONAL_STATE_STALE_MS = max(1000, int(os.getenv('NEO_TAPE_PERSONAL_STATE_STALE_MS', '60000')))
# Reads per refresh pass. Due ports over this limit are rotated across passes
# (ports with known open positions first) and raise an operator alert.
PERSONAL_ENGINE_LIMIT = max(0, int(os.getenv('NEO_TAPE_PERSONAL_ENGINE_LIMIT', '16')))
# Pools held only by personal engines that may take a seat, after main's and
# Lab's pins (never capped). Default: the tape's own pool budget.
PERSONAL_PIN_LIMIT = max(0, int(os.getenv('NEO_TAPE_PERSONAL_PIN_LIMIT',
                                          os.getenv('NEO_TAPE_MAX_PAIRS', '12'))))
# Marker added to a shallow copy of a stale engine's position rows; read only
# by the pin ordering (it never reaches a coin row).
PERSONAL_STALE_MARK = 'tape_personal_engine_stale'
PERSONAL_PIN_ORDER = 'FRESH_BEFORE_STALE_THEN_OLDEST_OPENED_AT_THEN_POOL'
OVER_READ_LIMIT_ALERT = 'PERSONAL_ENGINES_OVER_READ_LIMIT'


def _identity(coin):
    mint, pair = coin.get('address'), coin.get('pairAddress')
    return (str(mint), str(pair)) if mint and pair else None


def _supported(coin):
    return str(coin.get('dexId') or '').lower() == 'pumpswap'


def _held_coin(position, market):
    if not isinstance(position, dict):
        return None, None
    coin = dict(position.get('coin_snapshot') or {})
    coin.update({key: position[key] for key in
                 ('address', 'pairAddress', 'dexId', 'quoteTokenAddress', 'symbol')
                 if position.get(key)})
    identity = _identity(coin)
    if identity:
        # Refresh from the exact current pool only; another pool of the same
        # mint cannot replace a held position's decoder identity.
        coin.update(market.get(identity) or {})
    return identity, coin


def _opened_at(position):
    try:
        value = float(position.get('opened_at'))
    except (TypeError, ValueError, OverflowError):
        return math.inf
    return value if math.isfinite(value) else math.inf


def lab_pin_positions(state):
    """(Lab positions that need an exit pin, number of flow-free Lab positions left unpinned).

    A LAB_FORWARD_TESTS_V1 position never reads tape flow (lab_forward_tests.tape_pin_required),
    so it takes no seat; every other Lab position is pinned exactly as before.
    """
    books = (state.get('strategy_lab') or {}).get('books') or {}
    rows = books.values() if isinstance(books, dict) else books
    pinned, unpinned = [], 0
    for book in rows:
        if not isinstance(book, dict):
            continue
        for position in active_paper.positions(book):
            if (not active_paper.is_active_position(position)
                    and lab_forward_tests.tape_pin_required(position)):
                pinned.append(position)
            else:
                unpinned += 1
    return pinned, unpinned


def _held_coins(state, market, extra_positions=(), extra_limit=None):
    """Main and Lab held pools, then other engines' held pools not already pinned.

    Main/Lab rows keep their exact previous order and values and are never
    capped; Lab rows of books that never read flow are left out (V6,
    lab_pin_positions). A pool held by a personal engine as well as by main or another
    engine is pinned once. Supported pools held only by personal engines
    follow, in PERSONAL_PIN_ORDER, at most ``extra_limit`` of them (None: no
    cap); the rest are reported, not pinned. Returns the held coins, the
    identities held by the extra engines that are pinned or shared with main,
    the admitted personal-only identities and an operator report.
    """
    positions = list(state.get('positions') or [])
    # V6: Lab positions of books that never read tape flow take no pin (lab_pin_positions).
    positions += lab_pin_positions(state)[0]
    held = {}
    for position in positions:
        identity, coin = _held_coin(position, market)
        if identity:
            held[identity] = coin
    base = set(held)
    personal = {}
    for position in extra_positions or ():
        identity, coin = _held_coin(position, market)
        if not identity:
            continue
        order = (bool(position.get(PERSONAL_STALE_MARK)), _opened_at(position))
        if identity not in personal:
            personal[identity] = [coin, order]
        elif order < personal[identity][1]:
            # The first row's values stay; the pool is ordered by its best
            # (freshest, then oldest opened) holder.
            personal[identity][1] = order
    shared = {identity for identity in personal if identity in base}
    only = sorted((identity for identity in personal if identity not in base),
                  key=lambda identity: (personal[identity][1], identity[1], identity[0]))
    supported_only = [identity for identity in only if _supported(personal[identity][0])]
    limit = len(supported_only) if extra_limit is None else max(0, int(extra_limit))
    admitted = supported_only[:limit]
    over_limit = supported_only[limit:]
    excluded = set(over_limit)
    for identity in only:
        if identity not in excluded:
            held[identity] = personal[identity][0]
    admitted_set = set(admitted)
    report = {'personal_pin_limit': None if extra_limit is None else limit,
              'personal_pin_order': PERSONAL_PIN_ORDER,
              'personal_only_pools_held': len(supported_only),
              'personal_pins_over_limit': len(over_limit),
              'personal_pins_over_limit_pairs': [identity[1] for identity in over_limit][:SHED_LIST_LIMIT],
              'stale_personal_only_pins': sum(personal[identity][1][0] for identity in admitted)}
    return list(held.values()), shared | admitted_set, admitted_set, report


def _registry_ports(path):
    """Engine ports from the account registry; never writes, never raises."""
    try:
        if not path.exists():
            return [], 'MISSING'
        data = json.loads(read_shared_text(path, encoding='utf-8'))
    except (OSError, ValueError):
        return [], 'UNREADABLE'
    accounts = data.get('accounts') if isinstance(data, dict) else None
    if not isinstance(accounts, dict):
        return [], 'UNREADABLE'
    ports = set()
    for account in accounts.values():
        if not isinstance(account, dict):
            continue
        value = account.get('engine_port')
        if isinstance(value, bool) or not isinstance(value, (int, str)):
            continue
        try:
            port = int(value)
        except (TypeError, ValueError, OverflowError):
            continue
        if 1024 <= port <= 65535:
            ports.add(port)
    return sorted(ports), 'OK'


def _wall_ms():
    return int(time.time() * 1000)


class PersonalEnginePositions:
    """Open positions of the per-user PAPER engines listed in the account registry.

    Read-only. ``fetch(port, timeout_seconds)`` returns that engine's /state
    document (injected; the live recorder supplies an HTTP GET on 127.0.0.1).

    The HTTP reads never run on the tape poll: ``start()`` launches a daemon
    thread that calls ``refresh()`` every ``cache_ms`` and publishes a
    lock-protected snapshot; the poll calls ``latest()``, which only copies
    that snapshot. Per refresh pass at most ``engine_limit`` ports are read;
    due ports over the limit are rotated (ports with known open positions
    first, then the least recently attempted). A failed port is retried after
    ``cache_ms`` doubling per further failure up to ``backoff_max_ms``. A
    failed engine keeps its last successful positions until a successful read
    shows them closed or its port leaves the registry; once its last success
    is ``stale_ms`` old those rows are published as stale (and ordered after
    fresh rows for the personal pin cap). These positions only pin
    observation seats; they never open, size or close anything.
    """

    def __init__(self, fetch, *, registry_path=None, cache_ms=PERSONAL_STATE_CACHE_MS,
                 timeout_seconds=PERSONAL_STATE_TIMEOUT_SECONDS,
                 stale_ms=PERSONAL_STATE_STALE_MS, backoff_max_ms=PERSONAL_BACKOFF_MAX_MS,
                 engine_limit=PERSONAL_ENGINE_LIMIT, clock=_wall_ms):
        self.fetch = fetch
        self.registry_path = registry_path
        self.cache_ms = max(1000, int(cache_ms))
        self.timeout_seconds = min(2.0, max(0.1, float(timeout_seconds)))
        self.stale_ms = max(1000, int(stale_ms))
        self.backoff_max_ms = max(self.cache_ms, int(backoff_max_ms))
        self.engine_limit = max(0, int(engine_limit))
        self.clock = clock
        self.registry_checked_at = None
        self.registry = ([], 'NOT_READ')
        # Mutated only by refresh() (the refresher thread in production).
        self.engines = {}
        self._lock = threading.Lock()
        self._snapshot = None
        self._thread = None
        self._stop = threading.Event()

    def _path(self):
        return Path(self.registry_path or os.getenv('NEO_USER_STATE_PATH', DEFAULT_USER_STATE_PATH))

    @staticmethod
    def _within(checked_at, now, period):
        return checked_at is not None and 0 <= now - checked_at < period

    def backoff_ms(self, failures):
        """Retry delay after ``failures`` consecutive failed reads of one port."""
        if failures <= 0:
            return self.cache_ms
        return min(self.backoff_max_ms, self.cache_ms * 2 ** min(failures - 1, 16))

    def _due(self, record, now):
        next_at = record['next_at']
        # A clock that stepped backwards cannot park a port beyond the ceiling.
        return next_at is None or now >= next_at or next_at - now > self.backoff_max_ms

    def refresh(self, now=None):
        """One bounded refresh pass with blocking reads; publishes a new snapshot.

        Runs on the refresher thread (or directly in tests and tools), never on
        the tape poll. Returns ``latest(now)``.
        """
        now = self.clock() if now is None else now
        if not self._within(self.registry_checked_at, now, self.cache_ms):
            ports, status = _registry_ports(self._path())
            if status == 'UNREADABLE' and self.registry[1] in ('OK', 'UNREADABLE_USING_LAST_GOOD'):
                # A registry caught mid-replacement or locked is skipped; the
                # last good port list keeps held positions pinned meanwhile.
                ports, status = self.registry[0], 'UNREADABLE_USING_LAST_GOOD'
            self.registry = (ports, status)
            self.registry_checked_at = now
        listed, registry_status = self.registry
        self.engines = {port: self.engines.get(port) or
                        {'attempted_at': None, 'ok_at': None, 'next_at': None,
                         'failures': 0, 'positions': [], 'last_ok': False}
                        for port in listed}
        due = sorted((port for port in listed if self._due(self.engines[port], now)),
                     key=lambda port: (not self.engines[port]['positions'],
                                       -1 if self.engines[port]['attempted_at'] is None
                                       else self.engines[port]['attempted_at'], port))
        chosen = due[:self.engine_limit]
        for port in chosen:
            record = self.engines[port]
            record['attempted_at'] = now
            try:
                state = self.fetch(port, self.timeout_seconds)
                rows = state.get('positions') if isinstance(state, dict) else None
                if not isinstance(rows, list):
                    raise ValueError('engine state without a positions list')
                record.update(positions=[row for row in rows if isinstance(row, dict)],
                              ok_at=now, last_ok=True, failures=0, next_at=now + self.cache_ms)
            except Exception:
                # An unreachable or malformed engine never stops the refresher;
                # its last successful positions stay pinned.
                record['failures'] += 1
                record['last_ok'] = False
                record['next_at'] = now + self.backoff_ms(record['failures'])
        snapshot = {'refreshed_at': now, 'registry_status': registry_status,
                    'engines_listed': len(listed), 'reads_this_refresh': len(chosen),
                    'due_over_read_limit': len(due) - len(chosen),
                    'engines': {port: dict(record) for port, record in self.engines.items()}}
        with self._lock:
            self._snapshot = snapshot
        return self.latest(now)

    def latest(self, now):
        """The newest published snapshot as (positions, operator report). No I/O."""
        with self._lock:
            snapshot = self._snapshot
            thread = self._thread
        running = thread is not None and thread.is_alive()
        config = {'refresh_ms': self.cache_ms, 'timeout_seconds': self.timeout_seconds,
                  'stale_ms': self.stale_ms, 'backoff_ms': [self.cache_ms, self.backoff_max_ms],
                  'engine_read_limit_per_refresh': self.engine_limit,
                  'refresher': 'RUNNING' if running else 'NOT_RUNNING',
                  'reads_on_tape_poll': False, 'host': '127.0.0.1', 'read_only': True}
        if snapshot is None:
            return [], {**config, 'registry_status': 'NOT_REFRESHED', 'snapshot_age_ms': None,
                        'snapshot_stale': True, 'engines_listed': 0, 'open_positions': 0,
                        'operator_alerts': ['PERSONAL_SNAPSHOT_NOT_REFRESHED']}
        positions = []
        reachable = failing = never = stale_engines = stale_positions = backing_off = 0
        for record in snapshot['engines'].values():
            reachable += int(record['last_ok'])
            backing_off += int(record['failures'] > 0)
            if record['ok_at'] is None:
                never += 1
                continue
            failing += int(not record['last_ok'])
            rows = record['positions']
            if now - record['ok_at'] >= self.stale_ms:
                stale_engines += 1
                stale_positions += len(rows)
                rows = [dict(row, **{PERSONAL_STALE_MARK: True}) for row in rows]
            positions += rows
        age = now - snapshot['refreshed_at']
        snapshot_stale = age >= self.stale_ms
        alerts = []
        if snapshot['engines_listed'] > self.engine_limit:
            alerts.append(OVER_READ_LIMIT_ALERT)
        if snapshot_stale:
            alerts.append('PERSONAL_SNAPSHOT_STALE')
        if stale_engines:
            alerts.append('PERSONAL_ENGINE_POSITIONS_STALE')
        return positions, {
            **config, 'registry_status': snapshot['registry_status'],
            'snapshot_age_ms': age, 'snapshot_stale': snapshot_stale,
            'engines_listed': snapshot['engines_listed'],
            'engines_over_read_limit': max(0, snapshot['engines_listed'] - self.engine_limit),
            'due_over_read_limit_last_refresh': snapshot['due_over_read_limit'],
            'reads_last_refresh': snapshot['reads_this_refresh'],
            'engines_reachable': reachable, 'engines_failing_positions_kept': failing,
            'engines_never_reached': never, 'engines_backing_off': backing_off,
            'engines_stale': stale_engines, 'open_positions': len(positions),
            'stale_positions': stale_positions, 'operator_alerts': alerts}

    def _run(self):
        while not self._stop.is_set():
            try:
                self.refresh(self.clock())
            except Exception:
                # A bug in one pass must not end the refresher; the previous
                # snapshot stays published (and turns stale if this repeats).
                pass
            self._stop.wait(self.cache_ms / 1000)

    def start(self):
        """Start the daemon refresher once; returns whether a thread was started."""
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return False
            self._stop.clear()
            self._thread = threading.Thread(target=self._run, name='tape-personal-engines',
                                            daemon=True)
            self._thread.start()
            return True

    def stop(self, timeout=None):
        self._stop.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout)


class TapePoolScheduler:
    def __init__(self, lease_ms=LEASE_MS, *, shed_min_bodies=SHED_MIN_BODIES,
                 shed_cooldown_ms=SHED_COOLDOWN_MS, yield_window_ms=YIELD_WINDOW_MS,
                 personal_pin_limit=PERSONAL_PIN_LIMIT, registry_path=None, registry_seed_paths=()):
        self.lease_ms = max(30_000, int(lease_ms))
        self.personal_pin_limit = max(0, int(personal_pin_limit))
        self.leases = {}
        self.last_selected = {}
        self.shed_min_bodies = max(1, int(shed_min_bodies))
        self.shed_cooldown_ms = max(60_000, int(shed_cooldown_ms))
        self.yield_window_ms = max(60_000, int(yield_window_ms))
        self.shed = {}
        self.yield_since = {}
        # DEFENSIVE_ENTRY_LAYER_V1 of the tape process (own registry and history); a
        # registry without current coverage is seeded read-only from ``registry_seed_paths``.
        self.defense = entry_defense.DefensiveEntryLayer(registry_path=registry_path,
                                                         seed_paths=registry_seed_paths)
        if registry_path:
            self.defense.funded_heat_seed=active_paper.seed_pair_history(
                self.defense.history,Path(registry_path).parent/'funded_heat_seed.json',_wall_ms())

    def defensive_entry_decision(self, coin, now, *, leased=False):
        """Seat screen: no seat for a structurally blocked pool, no NEW seat for a hot one.

        The structural guard withholds every entry/exploration seat. A heat
        rule ((a)-(g) or an unknown price) withholds a new seat, while a pool
        holding a running lease (``leased``) keeps it until the lease expires,
        so its tape coverage is not reset by one hot poll. heat_history_warming
        stays log-only here: it describes this process's own history (a seat
        is how pool data is gathered), and every engine enforces its own
        warm-up at decision and commit. No ledger's pool loss memory applies
        (a seat serves every ledger; each entry path applies its own).
        """
        decision = self.defense.evaluate(coin, now, blocked_pools={}, heat_log_only=True)
        if active_paper.enabled() and not decision.get('allowed'):
            matching=[sid for sid in active_paper.RULES if active_paper.matches(sid,coin)]
            warning_books=[sid for sid in matching if active_paper.ticker_warning_enabled(sid)]
            reasons=set(decision.get('reasons') or [])
            if matching and ({'rug_young_pool','rug_ticker_registry_warming'} & reasons or
                             (warning_books and 'rug_ticker_reuse' in reasons)):
                sid=min(warning_books or matching,key=lambda name:active_paper.RULES[name]['age'][0])
                decision=self.defense.evaluate(coin,now,blocked_pools={},heat_log_only=True,
                    structural_parameters=active_paper.structural_parameters(sid),
                    ticker_reuse_log_only=active_paper.ticker_warning_enabled(sid))
        if not decision.get('allowed'):
            return decision
        # Only heat warnings have the running-lease exception. A funded ticker
        # warning must not accidentally be promoted back to a heat veto here.
        heat = decision.get('heat_veto') or {}
        flags = list(heat.get('reasons') or []) if heat.get('log_only') else []
        enforced = [reason for reason in flags if reason not in SEAT_HEAT_LOG_ONLY_REASONS]
        if not enforced:
            return decision
        if leased:
            return {**decision, 'seat_heat_lease_kept': True}
        return {**decision, 'allowed': False, 'reasons': list(decision.get('reasons') or []) + enforced,
                'log_only_flags': [flag for flag in decision.get('log_only_flags') or []
                                   if flag not in enforced], 'seat_heat_withheld': True}

    def _shed_record(self, identity, coin, now, decode_yield):
        """Return the active shed record for a supported entry candidate, if any."""
        record = self.shed.get(identity)
        if record is not None:
            if now < record['retry_at']:
                return record
            # The cooldown ended: the next attempt counts only bodies fetched
            # from now on, so a retried pool gets a fresh bounded chance.
            self.yield_since[identity] = record['retry_at']
            del self.shed[identity]
        if decode_yield is None:
            return None
        stats = decode_yield(identity[1], self.yield_since.get(identity, 0))
        bodies = int(feasibility.number(stats.get('bodies')))
        usable = int(feasibility.number(stats.get('usable_swaps')))
        # Decoded swaps include FX-reference-flagged events; a yield source
        # without that field falls back to the stricter usable count.
        decoded = int(feasibility.number(stats.get('decoded_swaps', usable)))
        if bodies < self.shed_min_bodies or decoded > 0 or usable > 0:
            return None
        record = {'symbol': coin.get('symbol'), 'address': identity[0],
                  'pairAddress': identity[1], 'reason': SHED_REASON,
                  'bodies': bodies, 'decoded_swaps': decoded, 'usable_swaps': usable,
                  'shadow_swaps': int(feasibility.number(stats.get('shadow_swaps'))),
                  'first_body_at': stats.get('first_body_at'),
                  'last_body_at': stats.get('last_body_at'),
                  'shed_at': now, 'retry_at': now + self.shed_cooldown_ms}
        self.shed[identity] = record
        return record

    def select(self, state, *, now, max_tracked, decode_yield=None, personal_positions=()):
        """Choose observed pools: exit pins first, then bounded entry seats.

        ``personal_positions`` are open positions of other PAPER engines (see
        PersonalEnginePositions). A pool also held by main or Lab is pinned
        with main's pins; pools held only by personal engines are pinned after
        them, at most ``personal_pin_limit`` in PERSONAL_PIN_ORDER. Fields
        under ``personal_pins_operator`` belong in the operator-only tape
        status; the caller removes them before publishing entry_scheduling.
        """
        market = {}
        for coin in state.get('feed') or []:
            if not isinstance(coin, dict):
                continue
            identity = _identity(coin)
            if identity and (identity not in market or
                             feasibility.number(coin.get('updatedAt')) >
                             feasibility.number(market[identity].get('updatedAt'))):
                market[identity] = coin
        held, personal_held, personal_only, personal_report = _held_coins(
            state, market, personal_positions, self.personal_pin_limit)
        pins = [coin for coin in held if _supported(coin)]
        pinned = {_identity(coin) for coin in pins}
        unsupported_pins = [coin for coin in held if not _supported(coin)]
        candidates, examples, cost_first_examples = [], [], []
        cost_first_rejections = {}
        # Every poll feeds the scheduler's ticker registry and pair history.
        self.defense.observe(list(market.values()), now)
        defensive_summary = entry_defense.new_summary()
        defensive_summary['heat_log_only'] = False
        defensive_blocked = []
        heat_withheld = heat_leases_kept = 0
        model_possible = model_excluded = model_unknown = 0
        main_cost_cap = feasibility.number((state.get('config') or {}).get(
            'strict_max_roundtrip_cost_pct'), 1.5)
        # Expired shed records of pools that left the feed or are now held
        # (pinned, therefore never shed) are dropped; an expired record of a
        # feed candidate is converted into a fresh bounded attempt below.
        # Either way the retry floor survives while it still excludes bodies
        # from the recorder's window, so a pool returning after its cooldown
        # is judged on bodies fetched after retry_at, not the shed evidence.
        kept = {}
        for identity, record in self.shed.items():
            if now < record['retry_at'] or (identity in market and identity not in pinned):
                kept[identity] = record
            else:
                self.yield_since[identity] = record['retry_at']
        self.shed = kept
        self.yield_since = {identity: since for identity, since in self.yield_since.items()
                            if identity in market or now - since < self.yield_window_ms}
        shed_now = []
        for identity, coin in market.items():
            if not _supported(coin) or identity in pinned:
                continue
            shed = self._shed_record(identity, coin, now, decode_yield)
            if shed is not None:
                shed_now.append(shed)
                continue
            # A pool the structural guard blocks for every entry path spends no
            # seat (pins are exempt above); a hot pool gets no new seat, while a
            # running lease runs out; heat warming is counted log-only.
            start = self.leases.get(identity)
            leased = start is not None and 0 <= now - start < self.lease_ms
            defensive = self.defensive_entry_decision(coin, now, leased=leased)
            entry_defense.record(defensive_summary, defensive, coin, example_limit=DEFENSIVE_EXAMPLE_LIMIT)
            if defensive.get('seat_heat_lease_kept'):
                heat_leases_kept += 1
            if not defensive['allowed']:
                defensive_blocked.append(identity)
                heat_withheld += int(bool(defensive.get('seat_heat_withheld')))
                continue
            features = lab_activity.market_features(coin)
            main_rules = winner_ensemble.market_candidates(coin)
            funded_rules = [name for name in FUNDED_RULES
                            if funded_market_candidates.matched_branches(
                                name,coin,features,require_flow=False)]
            # Main has no modeled slippage/latency floor: its eventual fresh
            # route quotes supply those costs. Even the fee floor remains a
            # planning estimate because canonical/noncanonical fees can differ.
            main_cost = feasibility.execution_feasibility(
                coin, main_cost_cap, base_slippage_bps=0, latency_buffer_bps=0)
            funded_cap=(max(active_paper.entry_cost_limit(sid) for sid in funded_rules)
                        if active_paper.enabled() and funded_rules else 1.5)
            funded_cost = feasibility.execution_feasibility(coin, funded_cap)
            if active_paper.sized_enabled() and funded_rules:
                # Don't spend scarce transaction bodies on a fee-floor pass
                # that fails at the owner's actual $250 size after impact.
                # This is planning only, never an execution/safety receipt.
                sized_cost = feasibility.modeled_roundtrip(coin, active_paper.entry_notional())
                if sized_cost['status'] == 'estimate':
                    cost_pct = max(0.0, -sized_cost['initial_pnl_pct'])
                    funded_cost = {**funded_cost,
                        'basis':'PAPER_MODELED_COSTS_AT_FIXED_ENTRY_SIZE',
                        'planned_notional_usd':active_paper.entry_notional(),
                        'minimum_model_roundtrip_cost_pct':cost_pct,
                        'model_cost_feasible':cost_pct <= funded_cap,
                        'reason':'sized_model_within_cap' if cost_pct <= funded_cap else 'sized_model_cost_above_limit'}
            # Independent scalper has an affordable physical branch that must
            # not wait for the slower funded books' 5m momentum screen. Observe
            # it within the SAME seat/lease/RPC budget, never authorize an entry.
            fast_model = fast_scalp.discovery_estimate(coin) if fast_scalp.enabled() else None
            fast_candidate = bool(fast_model and fast_model['candidate'])
            matched = bool(main_rules or funded_rules or fast_candidate)
            possible = bool((main_rules and main_cost['model_cost_feasible'] is True)
                            or (funded_rules and funded_cost['model_cost_feasible'] is True)
                            or fast_candidate)
            unknown = bool(matched and (main_cost['model_cost_feasible'] is None
                                       or funded_cost['model_cost_feasible'] is None))
            if matched:
                model_possible += int(possible)
                model_unknown += int(unknown and not possible)
                model_excluded += int(not possible and not unknown)
                if len(examples) < 12:
                    examples.append({'symbol': coin.get('symbol'), 'address': identity[0],
                                     'pairAddress': identity[1], 'main_rules': main_rules,
                                     'funded_rules': funded_rules, 'main_model': main_cost,
                                     'funded_model': funded_cost, 'fast_scalp_model':fast_model})
            # COST_FIRST universe: the Lab book pair's exact physical screen
            # (fee tier, liquidity, modeled fee + impact). Membership requires a
            # known cost estimate within the universe cap; it is a seat priority,
            # never an admission, and full flow/safety/quote gates still apply.
            universe_rejections = cost_first.rejections(
                coin, cap_usd=COST_FIRST_PLANNING_NOTIONAL_USD,
                minimum_notional_usd=COST_FIRST_MIN_NOTIONAL_USD,
                now=now, ticker_registry=self.defense.registry)
            in_cost_first = not universe_rejections
            for reason in universe_rejections:
                cost_first_rejections[reason] = cost_first_rejections.get(reason, 0) + 1
            tx = (coin.get('txns') or {}).get('m5') or {}
            activity = feasibility.number(tx.get('buys')) + feasibility.number(tx.get('sells'))
            priority = (bool(funded_rules and funded_cost['model_cost_feasible'] is True),
                        bool(main_rules), fast_candidate, activity >= 30,
                        -abs(activity - 60) if activity >= 12 else -1000 - activity,
                        len(funded_rules), len(main_rules), feasibility.number(coin.get('score')))
            group = (GROUP_FEASIBLE if possible else GROUP_COST_FIRST if in_cost_first
                     else GROUP_OVER_BUDGET if matched else GROUP_EXPLORATION)
            candidates.append({'identity': identity, 'coin': coin, 'group': group,
                               'cost_first': in_cost_first, 'priority': priority,
                               'fast_scalp':fast_candidate})

        by_identity = {row['identity']: row for row in candidates}
        # Exit monitoring can exceed the entry discovery budget. Every held
        # supported pool remains pinned; duplicates never consume extra seats.
        entry_capacity = max(0, int(max_tracked) - len(pins))
        self.leases = {identity: start for identity, start in self.leases.items()
                       if identity in by_identity and now - start < self.lease_ms
                       and 0 <= now - start}
        selected = [by_identity[identity] for identity in self.leases]
        selected.sort(key=lambda row: row['group'])
        # A new estimated affordable opportunity can replace an exploration
        # seat immediately. Affordable cohorts otherwise retain their full
        # observation lease instead of churning with each market score update.
        # Cost-first and every lower group share the non-feasible seats: they
        # keep their lease, are retained before lower groups and refill freed
        # seats first, but never pre-empt a running lease themselves.
        feasible_total = sum(row['group'] == GROUP_FEASIBLE for row in candidates)
        exploration_capacity = max(0, entry_capacity - feasible_total)
        retained_exploration = 0
        retained = []
        for row in selected:
            if row['group'] == 0 or retained_exploration < exploration_capacity:
                retained.append(row)
                retained_exploration += int(row['group'] != 0)
        selected = retained[:entry_capacity]
        selected_ids = {row['identity'] for row in selected}
        # New pools are observed fairly once an existing 60s lease expires.
        # Models cannot create a zero-tape bootstrap: spare seats and cohorts
        # without estimated feasible candidates still explore bounded pools.
        remaining = sorted((row for row in candidates if row['identity'] not in selected_ids),
                           key=lambda row: (row['group'],
                                            self.last_selected.get(row['identity'], -1),
                                            tuple(-float(value) for value in row['priority']),
                                            row['identity']))
        selected += remaining[:max(0, entry_capacity - len(selected))]
        selected_ids = {row['identity'] for row in selected}
        self.leases = {row['identity']: self.leases.get(row['identity'], now) for row in selected}
        for identity in selected_ids:
            self.last_selected[identity] = self.leases[identity]
        if len(self.last_selected) > 4096:
            keep = sorted(self.last_selected, key=self.last_selected.get, reverse=True)[:2048]
            self.last_selected = {identity: self.last_selected[identity] for identity in keep}

        cost_first_rows = [row for row in candidates if row['cost_first']]
        cost_first_selected = sum(row['identity'] in selected_ids for row in cost_first_rows)
        for row in sorted(cost_first_rows, key=lambda row: (row['identity'] not in selected_ids,
                                                             row['identity']))[:COST_FIRST_EXAMPLE_LIMIT]:
            described = cost_first.describe(row['coin'], cap_usd=COST_FIRST_PLANNING_NOTIONAL_USD,
                                            minimum_notional_usd=COST_FIRST_MIN_NOTIONAL_USD,
                                            now=now, ticker_registry=self.defense.registry)
            cost_first_examples.append({
                'symbol': row['coin'].get('symbol'), 'address': row['identity'][0],
                'pairAddress': row['identity'][1], 'selected': row['identity'] in selected_ids,
                'group': row['group'], 'fee_tier_bps': described['fee_tier_bps'],
                'liquidity_usd': described['liquidity_usd'],
                'planned_notional_usd': described['planned_notional_usd'],
                'fee_impact_roundtrip_pct': described['fee_impact_roundtrip_pct'],
                'is_execution_quote': False})
        personal_pins = [coin for coin in pins if _identity(coin) in personal_held]
        shed_records = sorted(self.shed.values(), key=lambda row: (-int(row['shed_at']), row['pairAddress']))
        diagnostics = {'policy_version': POLICY_VERSION,
                       'previous_policy_version': PREVIOUS_POLICY_VERSION,
                       'defensive_entry': {
                           **defensive_summary, 'examples': defensive_summary['examples'][:DEFENSIVE_EXAMPLE_LIMIT],
                           'blocked_pools_in_feed': len(defensive_blocked),
                           'heat_withheld_new_seats': heat_withheld,
                           'heat_running_leases_kept': heat_leases_kept,
                           'seat_rule': SEAT_RULE,
                           'pinned_exit_pools_exempt': True,
                           'heat_veto_mode': HEAT_SEAT_MODE,
                           'heat_log_only_reasons': sorted(SEAT_HEAT_LOG_ONLY_REASONS),
                           'pool_loss_memory_scope': 'NOT_APPLIED_AT_SEATS_EACH_LEDGER_AT_ITS_OWN_ENTRY',
                           'layer': self.defense.status(), 'is_entry_authorization': False},
                       'funded_candidate_policy_version':funded_market_candidates.VERSION,
                       'checked_at': now, 'model_is_execution_quote': False,
                       'cost_estimates_are_planning_hints': True,
                       'profitability_proven': False, 'lease_ms': self.lease_ms,
                       'entry_capacity': entry_capacity, 'pinned_exit_pools': len(pins),
                       # V6: held Lab positions of books that never read flow (no seat taken).
                       'lab_pin_rule': LAB_PIN_RULE,
                       'unpinned_flow_free_lab_positions': lab_pin_positions(state)[1],
                       'unsupported_held_pools': len(unsupported_pins),
                       'supported_candidate_pools': len(candidates),
                       'shed_policy': {'reason': SHED_REASON, 'min_bodies': self.shed_min_bodies,
                                       'cooldown_ms': self.shed_cooldown_ms,
                                       'sheds_on_zero_decoded_swaps': True,
                                       'decoded_allows_flags': ['QUOTE_ASSET_USD_REFERENCE_ESTIMATE',
                                                                'QUOTE_USD_UNKNOWN'],
                                       'pinned_exit_pools_exempt': True,
                                       'yield_source': 'RECORDER_IN_MEMORY' if decode_yield is not None else 'UNAVAILABLE'},
                       'shed_pool_count': len(self.shed),
                       'shed_pools_in_feed': len(shed_now),
                       'shed_pools': [dict(row) for row in shed_records[:SHED_LIST_LIMIT]],
                       'estimated_feasible_market_candidates': model_possible,
                       'estimated_fixed_cost_over_budget': model_excluded,
                       'estimated_cost_unknown': model_unknown,
                       'selected_entry_pools': len(selected),
                       'fast_scalp': {'enabled':fast_scalp.enabled(), 'version':fast_scalp.VERSION,
                           'candidate_pools':sum(row['fast_scalp'] for row in candidates),
                           'selected_pools':sum(row['fast_scalp'] for row in selected),
                           'same_seat_budget':True, 'is_entry_authorization':False},
                       'selected_exploration_pools': sum(row['group'] != 0 for row in selected),
                       'selected_pairs': [coin['pairAddress'] for coin in pins] +
                                         [row['identity'][1] for row in selected],
                       'examples': examples,
                       'selected_cost_first_pools': cost_first_selected,
                       'unselected_cost_first_pools': len(cost_first_rows) - cost_first_selected,
                       # Exact (mint, pool) pins held by personal PAPER engines;
                       # a pool also held by main or Lab is counted here and
                       # once in pinned_exit_pools. Pool ids are public chain
                       # data; personal aggregates stay operator-only.
                       'pinned_personal_pools': len(personal_pins),
                       'personal_pins_operator': {
                           **personal_report,
                           'pinned_personal_only_pools': sum(_identity(coin) in personal_only
                                                             for coin in personal_pins)},
                       'cost_first': {
                           'universe_version': cost_first.UNIVERSE_VERSION,
                           'seat_group': 'BELOW_ESTIMATED_FEASIBLE_ABOVE_OVER_BUDGET_OR_UNKNOWN',
                           'planning_notional_usd': COST_FIRST_PLANNING_NOTIONAL_USD,
                           'minimum_notional_usd': COST_FIRST_MIN_NOTIONAL_USD,
                           'candidate_pools': len(cost_first_rows),
                           'selected_pools': cost_first_selected,
                           'unselected_pools': len(cost_first_rows) - cost_first_selected,
                           'also_estimated_feasible': sum(row['group'] == GROUP_FEASIBLE
                                                          for row in cost_first_rows),
                           'rejections': dict(sorted(cost_first_rejections.items())),
                           'examples': cost_first_examples,
                           'is_entry_authorization': False}}
        return pins + [row['coin'] for row in selected], diagnostics
