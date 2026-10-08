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
(mint, pool) exactly like the main engine's positions, so their exits keep
exact-pool flow coverage. Each engine's /state is read on 127.0.0.1 with a
short timeout, at most once per cache period per port; failures are ignored.
"""
import json
import os
from pathlib import Path

import lab_activity
import cost_first_established as cost_first
import funded_market_candidates
import paper_market_feasibility as feasibility
from shared_snapshot_io import read_shared_text
import winner_ensemble


FUNDED_RULES = ('EARLY', 'MOMENTUM', 'PRECISION', 'ULTRA_PRECISION')
LEASE_MS = 60_000
POLICY_VERSION = 'STABLE_COST_AWARE_TAPE_DISCOVERY_V4_COST_FIRST_PINS'
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
PERSONAL_STATE_CACHE_MS = max(1000, int(os.getenv('NEO_TAPE_PERSONAL_STATE_CACHE_MS', '5000')))
PERSONAL_STATE_TIMEOUT_SECONDS = min(2.0, max(0.1, float(
    os.getenv('NEO_TAPE_PERSONAL_STATE_TIMEOUT_SECONDS', '0.75'))))
# The last successful position list of an engine that is briefly unreachable
# (busy under CPU pressure) is kept this long, then dropped.
PERSONAL_STATE_RETAIN_MS = max(0, int(os.getenv('NEO_TAPE_PERSONAL_STATE_RETAIN_MS', '30000')))
PERSONAL_ENGINE_LIMIT = max(0, int(os.getenv('NEO_TAPE_PERSONAL_ENGINE_LIMIT', '16')))


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


def _held_coins(state, market, extra_positions=()):
    """Main and Lab held pools, then other engines' held pools not already pinned.

    Main/Lab rows keep their exact previous order and values. A pool held by a
    personal engine as well as by main or another engine is pinned once.
    Returns the held coins, the identities held by the extra engines and the
    subset of those not already held by main or Lab.
    """
    positions = list(state.get('positions') or [])
    books = (state.get('strategy_lab') or {}).get('books') or {}
    rows = books.values() if isinstance(books, dict) else books
    positions += [book['position'] for book in rows
                  if isinstance(book, dict) and isinstance(book.get('position'), dict)]
    held = {}
    for position in positions:
        identity, coin = _held_coin(position, market)
        if identity:
            held[identity] = coin
    base = set(held)
    extra = set()
    for position in extra_positions or ():
        identity, coin = _held_coin(position, market)
        if identity:
            extra.add(identity)
            held.setdefault(identity, coin)
    return list(held.values()), extra, extra - base


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


class PersonalEnginePositions:
    """Open positions of the per-user PAPER engines listed in the account registry.

    Read-only. ``fetch(port, timeout_seconds)`` returns that engine's /state
    document (injected; the live recorder supplies an HTTP GET on 127.0.0.1).
    Each port is fetched at most once per ``cache_ms`` whether the attempt
    succeeded or not, so a poll never hammers a failing engine. A failed engine
    keeps its last successful positions for ``retain_ms`` (exit coverage across
    a busy moment), then contributes nothing. These positions only pin
    observation seats; they never open, size or close anything.
    """

    def __init__(self, fetch, *, registry_path=None, cache_ms=PERSONAL_STATE_CACHE_MS,
                 timeout_seconds=PERSONAL_STATE_TIMEOUT_SECONDS,
                 retain_ms=PERSONAL_STATE_RETAIN_MS, engine_limit=PERSONAL_ENGINE_LIMIT):
        self.fetch = fetch
        self.registry_path = registry_path
        self.cache_ms = max(1000, int(cache_ms))
        self.timeout_seconds = min(2.0, max(0.1, float(timeout_seconds)))
        self.retain_ms = max(0, int(retain_ms))
        self.engine_limit = max(0, int(engine_limit))
        self.registry_checked_at = None
        self.registry = ([], 'NOT_READ')
        self.engines = {}

    def _path(self):
        return Path(self.registry_path or os.getenv('NEO_USER_STATE_PATH', DEFAULT_USER_STATE_PATH))

    @staticmethod
    def _within(checked_at, now, period):
        return checked_at is not None and 0 <= now - checked_at < period

    def collect(self, now):
        if not self._within(self.registry_checked_at, now, self.cache_ms):
            ports, status = _registry_ports(self._path())
            if status == 'UNREADABLE' and self.registry[1] in ('OK', 'UNREADABLE_USING_LAST_GOOD'):
                # A registry caught mid-replacement or locked is skipped; the
                # last good port list keeps held positions pinned meanwhile.
                ports, status = self.registry[0], 'UNREADABLE_USING_LAST_GOOD'
            self.registry = (ports, status)
            self.registry_checked_at = now
        listed, registry_status = self.registry
        ports = listed[:self.engine_limit]
        self.engines = {port: record for port, record in self.engines.items() if port in ports}
        fetched = unavailable = retained = 0
        positions = []
        for port in ports:
            record = self.engines.setdefault(port, {'checked_at': None, 'ok_at': None,
                                                    'positions': [], 'last_ok': False})
            if not self._within(record['checked_at'], now, self.cache_ms):
                record['checked_at'] = now
                fetched += 1
                try:
                    state = self.fetch(port, self.timeout_seconds)
                    rows = state.get('positions') if isinstance(state, dict) else None
                    if not isinstance(rows, list):
                        raise ValueError('engine state without a positions list')
                    record.update(positions=[row for row in rows if isinstance(row, dict)],
                                  ok_at=now, last_ok=True)
                except Exception:
                    # An unreachable or malformed engine never stops the tape.
                    record['last_ok'] = False
            if record['last_ok']:
                positions += record['positions']
            elif self._within(record['ok_at'], now, self.retain_ms + 1):
                retained += 1
                positions += record['positions']
            else:
                unavailable += 1
                record['positions'] = []
        return positions, {
            'registry_status': registry_status, 'engines_listed': len(listed),
            'engine_limit': self.engine_limit,
            'engines_over_limit': max(0, len(listed) - len(ports)),
            'engines_reachable': sum(bool(self.engines[port]['last_ok']) for port in ports),
            'engines_stale_retained': retained, 'engines_unavailable': unavailable,
            'fetches_this_poll': fetched, 'open_positions': len(positions),
            'cache_ms': self.cache_ms, 'timeout_seconds': self.timeout_seconds,
            'retain_ms': self.retain_ms, 'host': '127.0.0.1', 'read_only': True}


class TapePoolScheduler:
    def __init__(self, lease_ms=LEASE_MS, *, shed_min_bodies=SHED_MIN_BODIES,
                 shed_cooldown_ms=SHED_COOLDOWN_MS, yield_window_ms=YIELD_WINDOW_MS):
        self.lease_ms = max(30_000, int(lease_ms))
        self.leases = {}
        self.last_selected = {}
        self.shed_min_bodies = max(1, int(shed_min_bodies))
        self.shed_cooldown_ms = max(60_000, int(shed_cooldown_ms))
        self.yield_window_ms = max(60_000, int(yield_window_ms))
        self.shed = {}
        self.yield_since = {}

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
        PersonalEnginePositions); they are pinned exactly like main's own.
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
        held, personal_held, personal_only = _held_coins(state, market, personal_positions)
        pins = [coin for coin in held if _supported(coin)]
        pinned = {_identity(coin) for coin in pins}
        unsupported_pins = [coin for coin in held if not _supported(coin)]
        candidates, examples, cost_first_examples = [], [], []
        cost_first_rejections = {}
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
            funded_cost = feasibility.execution_feasibility(coin, 1.5)
            matched = bool(main_rules or funded_rules)
            possible = bool((main_rules and main_cost['model_cost_feasible'] is True)
                            or (funded_rules and funded_cost['model_cost_feasible'] is True))
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
                                     'funded_model': funded_cost})
            # COST_FIRST universe: the Lab book pair's exact physical screen
            # (fee tier, liquidity, modeled fee + impact). Membership requires a
            # known cost estimate within the universe cap; it is a seat priority,
            # never an admission, and full flow/safety/quote gates still apply.
            universe_rejections = cost_first.rejections(
                coin, cap_usd=COST_FIRST_PLANNING_NOTIONAL_USD,
                minimum_notional_usd=COST_FIRST_MIN_NOTIONAL_USD)
            in_cost_first = not universe_rejections
            for reason in universe_rejections:
                cost_first_rejections[reason] = cost_first_rejections.get(reason, 0) + 1
            tx = (coin.get('txns') or {}).get('m5') or {}
            activity = feasibility.number(tx.get('buys')) + feasibility.number(tx.get('sells'))
            priority = (bool(funded_rules and funded_cost['model_cost_feasible'] is True),
                        bool(main_rules), activity >= 30,
                        -abs(activity - 60) if activity >= 12 else -1000 - activity,
                        len(funded_rules), len(main_rules), feasibility.number(coin.get('score')))
            group = (GROUP_FEASIBLE if possible else GROUP_COST_FIRST if in_cost_first
                     else GROUP_OVER_BUDGET if matched else GROUP_EXPLORATION)
            candidates.append({'identity': identity, 'coin': coin, 'group': group,
                               'cost_first': in_cost_first, 'priority': priority})

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
                                            minimum_notional_usd=COST_FIRST_MIN_NOTIONAL_USD)
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
                       'funded_candidate_policy_version':funded_market_candidates.VERSION,
                       'checked_at': now, 'model_is_execution_quote': False,
                       'cost_estimates_are_planning_hints': True,
                       'profitability_proven': False, 'lease_ms': self.lease_ms,
                       'entry_capacity': entry_capacity, 'pinned_exit_pools': len(pins),
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
                       'selected_exploration_pools': sum(row['group'] != 0 for row in selected),
                       'selected_pairs': [coin['pairAddress'] for coin in pins] +
                                         [row['identity'][1] for row in selected],
                       'examples': examples,
                       'selected_cost_first_pools': cost_first_selected,
                       'unselected_cost_first_pools': len(cost_first_rows) - cost_first_selected,
                       # Exact (mint, pool) pins held by personal PAPER engines;
                       # a pool also held by main or Lab is counted here and
                       # once in pinned_exit_pools.
                       'pinned_personal_pools': len(personal_pins),
                       'pinned_personal_only_pools': sum(_identity(coin) in personal_only
                                                         for coin in personal_pins),
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
