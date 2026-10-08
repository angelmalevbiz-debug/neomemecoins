"""DEFENSIVE_ENTRY_LAYER_V1: one pre-entry decision from three independent vetoes.

PAPER only. Every entry path (main engine signal strategies, the training
quote probe, every Strategy Lab book and the tape scheduler's entry seats)
asks this layer before any quote, flow promotion or RugCheck call:

- STRUCTURAL_RUG_GUARD_V1 (structural_rug_guard.py): LP-pullable, young,
  fake-market-cap and ticker-reuse pools, fail closed;
- POOL_LOSS_MEMORY_V1 (pool_loss_memory.py): 6 h pause after 2 consecutive
  losses on the same pool in one ledger;
- HEAT_VETO_STACK_V1 (heat_veto.py): momentum, buy-share, volume
  acceleration, extended move, paid profile on high fees, crash, turnover,
  and a conservative veto while a pool has under 5 minutes of history.

The layer only removes candidates. It never admits, sizes, prices or exits
anything, and no research result shows positive expectancy after costs:
these rules measurably cut losses, they do not create profit.

One layer instance belongs to one process (engine, Lab, tape). It owns that
process's ticker registry (persisted next to its state file) and its pair
history; the caller feeds both with every scan's feed via ``observe``.
"""
import heat_veto
import pool_loss_memory
import structural_rug_guard as rug

VERSION = 'DEFENSIVE_ENTRY_LAYER_V1'
EXAMPLE_LIMIT = 5
VERSIONS = {'defensive_entry': VERSION, 'structural_rug_guard': rug.VERSION,
            'heat_veto': heat_veto.VERSION, 'pool_loss_memory': pool_loss_memory.VERSION}


def registry_path_for(state_path):
    """Sidecar next to a state file: state.json -> state.ticker_registry.json."""
    from pathlib import Path
    path = Path(state_path)
    return path.with_name(f'{path.stem}.ticker_registry.json')


class DefensiveEntryLayer:
    def __init__(self, *, registry_path=None, registry=None, history=None, clock=None):
        self.registry = registry if registry is not None else rug.TickerRegistry(registry_path, clock=clock)
        self.history = history if history is not None else heat_veto.PairHistory()

    def observe(self, feed, now):
        """Feed one scan: register tickers and append pair-history samples."""
        rows = [coin for coin in feed or () if isinstance(coin, dict)]
        self.registry.observe(rows, now)
        self.history.observe(rows, now)

    def evaluate(self, coin, now, *, blocked_pools=None, closed_history=None, heat_log_only=False) -> dict:
        """All three vetoes for one candidate at ``now`` (ms); ``allowed`` only when none blocks.

        ``blocked_pools`` is a pool_loss_memory.index of the deciding ledger
        (computed once per scan); ``closed_history`` is used when it is absent.
        """
        structural = rug.check(coin, now, self.registry)
        loss = pool_loss_memory.check(coin, now, history=closed_history, blocked=blocked_pools)
        heat = heat_veto.evaluate(coin, now, self.history, log_only=heat_log_only)
        reasons = (list(structural['reasons']) if structural['blocked'] else []) + list(loss['reasons'])
        if heat['vetoed']:
            reasons += heat['reasons']
        return {'version': VERSION, 'allowed': not (structural['blocked'] or loss['blocked'] or heat['vetoed']),
                'reasons': reasons, 'log_only_flags': list(heat['reasons']) if heat['log_only'] else [],
                'structural_rug_guard': rug.compact(structural), 'pool_loss_memory': loss, 'heat_veto': heat}

    def status(self) -> dict:
        return {'version': VERSION, 'ticker_registry': self.registry.status(), 'pair_history': self.history.status()}


def pass_decision(mode: str) -> dict:
    """An explicit, labelled non-evaluation (offline replay of journals recorded before this layer)."""
    return {'version': VERSION, 'allowed': True, 'reasons': [], 'log_only_flags': [], 'mode': mode}


def compact(decision: dict) -> dict:
    """Record for a newly opened position and for rejection examples."""
    heat = decision.get('heat_veto') or {}
    return {'version': decision.get('version', VERSION), 'allowed': bool(decision.get('allowed')),
            'reasons': list(decision.get('reasons') or []),
            'log_only_flags': list(decision.get('log_only_flags') or []),
            **({'mode': decision['mode']} if decision.get('mode') else {}),
            'versions': dict(VERSIONS),
            'structural_rug_guard': decision.get('structural_rug_guard'),
            'pool_loss_memory': decision.get('pool_loss_memory'),
            'heat_veto': {'version': heat.get('version', heat_veto.VERSION), 'vetoed': bool(heat.get('vetoed')),
                          'log_only': bool(heat.get('log_only')), 'reasons': list(heat.get('reasons') or []),
                          'metrics': dict(heat.get('metrics') or {})} if heat else None}


def new_summary() -> dict:
    return {'version': VERSION, 'versions': dict(VERSIONS), 'checked': 0, 'blocked': 0,
            'rejections': {}, 'primary_rejections': {}, 'log_only_flags': {}, 'examples': []}


def primary_reason(summary: dict) -> str | None:
    """Most frequent first (highest-priority) reason of the blocked decisions."""
    counts = summary.get('primary_rejections') or {}
    order = {reason: index for index, reason in enumerate(
        rug.REASONS + (pool_loss_memory.REASON,) + heat_veto.REASONS)}
    ranked = sorted(counts.items(), key=lambda item: (-item[1], order.get(item[0], len(order)), item[0]))
    return ranked[0][0] if ranked else None


def record(summary: dict, decision: dict, coin: dict | None = None, *, example_limit: int = EXAMPLE_LIMIT) -> None:
    """Count one decision into a per-scan diagnostics summary (bounded examples)."""
    summary['checked'] = int(summary.get('checked') or 0) + 1
    for flag in decision.get('log_only_flags') or ():
        summary['log_only_flags'][flag] = summary['log_only_flags'].get(flag, 0) + 1
    if decision.get('allowed'):
        return
    summary['blocked'] = int(summary.get('blocked') or 0) + 1
    reasons = list(decision.get('reasons') or [])
    for reason in reasons:
        summary['rejections'][reason] = summary['rejections'].get(reason, 0) + 1
    if reasons:
        primary = summary.setdefault('primary_rejections', {})
        primary[reasons[0]] = primary.get(reasons[0], 0) + 1
    if coin is not None and len(summary['examples']) < example_limit:
        structural = decision.get('structural_rug_guard') or {}
        heat = decision.get('heat_veto') or {}
        loss = decision.get('pool_loss_memory') or {}
        summary['examples'].append({
            'symbol': str(coin.get('symbol') or '')[:40], 'reasons': list(decision.get('reasons') or []),
            'liq_mcap': structural.get('liq_mcap'), 'age_min': structural.get('age_min'),
            'mcap': structural.get('mcap'), 'ticker_reused_by': structural.get('ticker_reused_by'),
            'heat_metrics': dict(heat.get('metrics') or {}),
            'pool_loss_blocked_until': loss.get('blocked_until')})


def metrics(decision: dict) -> dict:
    """Compact rejection-example metrics for engine_entry_policy.record."""
    structural = decision.get('structural_rug_guard') or {}
    heat = decision.get('heat_veto') or {}
    loss = decision.get('pool_loss_memory') or {}
    return {'defensive_entry_version': VERSION, 'liq_mcap': structural.get('liq_mcap'),
            'age_min': structural.get('age_min'), 'mcap': structural.get('mcap'),
            'ticker_reused_by': structural.get('ticker_reused_by'),
            'heat': dict(heat.get('metrics') or {}), 'pool_loss_blocked_until': loss.get('blocked_until')}


def config() -> dict:
    """Published definition of the whole layer; included in effective config hashes."""
    return {'version': VERSION, 'versions': dict(VERSIONS),
            'order': ['structural_rug_guard', 'pool_loss_memory', 'heat_veto'],
            'applies_before': ['quotes', 'flow promotion (promoted_entry_guard.flow_admission)',
                               'RugCheck (engine_rug_guard.check)', 'Jupiter price probes'],
            'structural_rug_guard': rug.config(), 'heat_veto': heat_veto.config(),
            'pool_loss_memory': pool_loss_memory.config(),
            'exits_changed': False, 'is_entry_authorization': False, 'profitability_proven': False,
            'evidence_status': 'LOSS_REDUCTION_MEASURED_NO_POSITIVE_EXPECTANCY'}
