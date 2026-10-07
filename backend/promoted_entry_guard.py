"""Declared evidence and cost admission for funded PAPER Lab entries.

This is a prospective research policy, not a profitable backtest or a live
execution signal. It never changes completed outcomes or manages existing exits.
"""
import math

VERSION = 'PROMOTED_EVIDENCE_COST_V1'
FLOW_SOURCE = 'CONFIRMED_PUMPSWAP_WINDOW'
FLOW_MAX_AGE_MS = 12_000
FLOW_WINDOW_MS = 30_000
SAFETY_MAX_AGE_MS = 60_000
MIN_FLOW_TRADES = 3
MIN_FLOW_WALLETS = 2
MIN_BUY_SELL_RATIO = 1.2
MAX_STOP_BUDGET_COST_FRACTION = .5


def finite(value):
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError, OverflowError):
        return None


def flow_admission(coin, features, now):
    flow = features.get('verified_flow') or {}
    if (flow.get('source') != FLOW_SOURCE or flow.get('coverage_status') != 'COMPLETE'
            or flow.get('window_ms') != FLOW_WINDOW_MS
            or flow.get('address') != coin.get('address')
            or flow.get('pairAddress') != coin.get('pairAddress')):
        return {'allow': False, 'reason': 'promoted_verified_flow_unavailable'}
    window, available, event = [finite(flow.get(name)) for name in
                                ('window_at', 'available_at', 'latest_event_at')]
    feed_stamp = finite(coin.get('updatedAt'))
    if (any(value is None or value <= 0 for value in (window, available, event, feed_stamp))
            or not event <= available <= window <= now
            or not 0 <= now - feed_stamp <= FLOW_MAX_AGE_MS
            or now - event > FLOW_MAX_AGE_MS or now - window > FLOW_MAX_AGE_MS):
        return {'allow': False, 'reason': 'promoted_verified_flow_stale'}
    trades, wallets, buys, sells = [finite(flow.get(name)) for name in
                                  ('trades', 'unique_wallets', 'buy_usd', 'sell_usd')]
    if (any(value is None or value < 0 for value in (trades, wallets, buys, sells))
            or trades != int(trades) or wallets != int(wallets)
            or trades < MIN_FLOW_TRADES or wallets < MIN_FLOW_WALLETS
            or buys <= 0 or buys < MIN_BUY_SELL_RATIO * max(sells, 1.0)):
        return {'allow': False, 'reason': 'promoted_buy_pressure_unconfirmed'}
    return {'allow': True, 'reason': None}


def risk_admission(coin, risk, now, ttl_ms):
    checked = finite((risk or {}).get('checked_at'))
    age_limit = min(SAFETY_MAX_AGE_MS, max(0, finite(ttl_ms) or 0))
    valid = bool(risk and risk.get('status') == 'pass'
                 and not risk.get('provisional_early')
                 and risk.get('mint') == coin.get('address')
                 and risk.get('pair') == coin.get('pairAddress')
                 and checked is not None and checked > 0
                 and 0 <= now - checked <= age_limit)
    return {'allow': valid, 'reason': None if valid else 'promoted_safety_unavailable'}


def max_entry_cost_pct(stop_loss_pct):
    stop = finite(stop_loss_pct)
    if stop is None or stop <= 0:
        return 0.0
    return stop * MAX_STOP_BUDGET_COST_FRACTION


def cost_admission(roundtrip_pnl_pct, stop_loss_pct):
    pnl, stop = finite(roundtrip_pnl_pct), finite(stop_loss_pct)
    valid = bool(pnl is not None and stop is not None and stop > 0
                 and -stop * MAX_STOP_BUDGET_COST_FRACTION <= pnl <= 0)
    return {'allow': valid, 'reason': None if valid else 'promoted_cost_headroom_insufficient'}


def policy_config(stop_loss_pct):
    return {'version': VERSION, 'flow_source': FLOW_SOURCE,
            'flow_max_age_ms': FLOW_MAX_AGE_MS, 'minimum_flow_trades': MIN_FLOW_TRADES,
            'minimum_flow_wallets': MIN_FLOW_WALLETS,
            'minimum_buy_sell_ratio': MIN_BUY_SELL_RATIO,
            'max_stop_budget_cost_fraction': MAX_STOP_BUDGET_COST_FRACTION,
            'maximum_roundtrip_cost_pct': max_entry_cost_pct(stop_loss_pct),
            'flow_window_ms': FLOW_WINDOW_MS,
            'safety_max_age_ms': SAFETY_MAX_AGE_MS,
            'requires_exact_pool_fresh_safety': True,
            'evidence_status': 'PROSPECTIVE_POLICY_UNVALIDATED',
            'profitability_proven': False, 'historical_outcomes_unchanged': True}
