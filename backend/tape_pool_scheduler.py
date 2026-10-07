"""Stable bounded discovery seats, with exit pins and explicit model estimates.

This decides what to observe, never whether to trade. Actual flow completeness,
safety checks and executable quotes retain their existing admission authority.
"""
import lab_activity
import paper_market_feasibility as feasibility
import winner_ensemble


FUNDED_RULES = ('EARLY', 'MOMENTUM', 'PRECISION', 'ULTRA_PRECISION')
LEASE_MS = 60_000


def _identity(coin):
    mint, pair = coin.get('address'), coin.get('pairAddress')
    return (str(mint), str(pair)) if mint and pair else None


def _supported(coin):
    return str(coin.get('dexId') or '').lower() == 'pumpswap'


def _held_coins(state, market):
    positions = list(state.get('positions') or [])
    books = (state.get('strategy_lab') or {}).get('books') or {}
    rows = books.values() if isinstance(books, dict) else books
    positions += [book['position'] for book in rows
                  if isinstance(book, dict) and isinstance(book.get('position'), dict)]
    held = {}
    for position in positions:
        if not isinstance(position, dict):
            continue
        coin = dict(position.get('coin_snapshot') or {})
        coin.update({key: position[key] for key in
                     ('address', 'pairAddress', 'dexId', 'quoteTokenAddress', 'symbol')
                     if position.get(key)})
        identity = _identity(coin)
        if identity:
            # Refresh from the exact current pool only; another pool of the same
            # mint cannot replace a held position's decoder identity.
            coin.update(market.get(identity) or {})
            held[identity] = coin
    return list(held.values())


class TapePoolScheduler:
    def __init__(self, lease_ms=LEASE_MS):
        self.lease_ms = max(30_000, int(lease_ms))
        self.leases = {}
        self.last_selected = {}

    def select(self, state, *, now, max_tracked):
        market = {}
        for coin in state.get('feed') or []:
            if not isinstance(coin, dict):
                continue
            identity = _identity(coin)
            if identity and (identity not in market or
                             feasibility.number(coin.get('updatedAt')) >
                             feasibility.number(market[identity].get('updatedAt'))):
                market[identity] = coin
        held = _held_coins(state, market)
        pins = [coin for coin in held if _supported(coin)]
        pinned = {_identity(coin) for coin in pins}
        unsupported_pins = [coin for coin in held if not _supported(coin)]
        candidates, examples = [], []
        model_possible = model_excluded = model_unknown = 0
        main_cost_cap = feasibility.number((state.get('config') or {}).get(
            'strict_max_roundtrip_cost_pct'), 1.5)
        for identity, coin in market.items():
            if not _supported(coin) or identity in pinned:
                continue
            features = lab_activity.market_features(coin)
            main_rules = winner_ensemble.market_candidates(coin)
            funded_rules = [name for name in FUNDED_RULES
                            if lab_activity.RULES[name].matches(features, require_flow=False)]
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
            tx = (coin.get('txns') or {}).get('m5') or {}
            activity = feasibility.number(tx.get('buys')) + feasibility.number(tx.get('sells'))
            priority = (bool(funded_rules and funded_cost['model_cost_feasible'] is True),
                        bool(main_rules), activity >= 30,
                        -abs(activity - 60) if activity >= 12 else -1000 - activity,
                        len(funded_rules), len(main_rules), feasibility.number(coin.get('score')))
            candidates.append({'identity': identity, 'coin': coin,
                               'group': 0 if possible else 1 if matched else 2,
                               'priority': priority})

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
        feasible_total = sum(row['group'] == 0 for row in candidates)
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

        diagnostics = {'policy_version': 'STABLE_COST_AWARE_TAPE_DISCOVERY_V1',
                       'checked_at': now, 'model_is_execution_quote': False,
                       'cost_estimates_are_planning_hints': True,
                       'profitability_proven': False, 'lease_ms': self.lease_ms,
                       'entry_capacity': entry_capacity, 'pinned_exit_pools': len(pins),
                       'unsupported_held_pools': len(unsupported_pins),
                       'supported_candidate_pools': len(candidates),
                       'estimated_feasible_market_candidates': model_possible,
                       'estimated_fixed_cost_over_budget': model_excluded,
                       'estimated_cost_unknown': model_unknown,
                       'selected_entry_pools': len(selected),
                       'selected_exploration_pools': sum(row['group'] != 0 for row in selected),
                       'selected_pairs': [coin['pairAddress'] for coin in pins] +
                                         [row['identity'][1] for row in selected],
                       'examples': examples}
        return pins + [row['coin'] for row in selected], diagnostics
