"""Pure, versioned PAPER exit decisions on net executable marks.

The historical chart stop/clamp is intentionally excluded. A planned stop is
an exit intent, never a promise of the eventual proceeds through a price gap.
"""
VERSION = 'HONEST_NET_EXIT_V1'
ADAPTIVE_VERSION = 'GOLD_ADAPTIVE_NET_CANDIDATE_V1'

def exit_reason(position, context, *, net_pct, peak_net_pct, hold_minutes,
                stop_pct=5.0, take_profit_pct=10.0, policy='fixed'):
    if net_pct <= -stop_pct:
        return 'STOP_LOSS_NET_TARGET'
    if policy == 'fixed':
        if net_pct >= take_profit_pct: return 'TAKE_PROFIT_10_NET'
        if hold_minutes >= 60: return 'MAX_HOLD_60'
        return None
    if policy != 'adaptive': raise ValueError('unknown exit policy')
    conviction = float(context.get('conviction') or 0)
    fast = context.get('fast_flow') or {}
    if conviction < 35 and net_pct < 0: return 'CONVICTION_EXIT'
    if (float(fast.get('trades') or 0) >= 4 and float(fast.get('sells') or 0) >= 3
        and float(fast.get('sell_usd') or 0) >= max(200.,float(fast.get('buy_usd') or 0)*2)
        and net_pct < 3): return 'ORDERFLOW_EXIT'
    target = context.get('target_pct')
    if target is not None and net_pct >= float(target): return f'ADAPTIVE_TP_{float(target):.0f}'
    if peak_net_pct >= 10 and conviction < 50 and net_pct > 2: return 'CONVICTION_PROFIT_LOCK'
    arm = float(context.get('trail_arm_pct') or 8)
    trail = float(context.get('trail_pct') or 4)
    # Express trailing drawdown on the same net liquidation value as stop/TP.
    if peak_net_pct >= arm and 100+net_pct <= (100+peak_net_pct)*(1-trail/100): return 'ADAPTIVE_TRAILING'
    if hold_minutes >= float(context.get('max_hold_minutes') or 60) and conviction < 72: return 'ADAPTIVE_MAX_HOLD'
    if hold_minutes >= 120: return 'ABSOLUTE_MAX_HOLD'
    return None
