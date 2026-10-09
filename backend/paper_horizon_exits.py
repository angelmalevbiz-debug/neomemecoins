"""Owner-requested PAPER holding horizons; hypotheses, not a proven profit edge."""
import math

VERSION = 'PAPER_HORIZON_EXIT_V1_60_240_720'
SCALP_VERSION = 'PAPER_MOMENTUM_SCALP_EXIT_V1_15'
SIZED_VERSION = 'PAPER_HORIZON_EXIT_V2_PROPORTIONAL_250'
VERSIONS = frozenset({VERSION, SCALP_VERSION, SIZED_VERSION})
PROFILES = {
    'EARLY': ('QUICK', 60, 6.0, 2.0, .75),
    'MOMENTUM': ('SCALP', 15, 6.0, 2.0, .75),
    'PRECISION': ('SWING', 240, 15.0, 4.0, 1.0),
    'ULTRA_PRECISION': ('HOLDER', 720, 30.0, 4.0, 1.0),
}


def parameters(strategy_id, notional_usd=100.0):
    if strategy_id not in PROFILES:
        raise ValueError('Unknown PAPER holding profile')
    if isinstance(notional_usd, bool):
        raise ValueError('Positive finite notional required')
    notional = float(notional_usd)
    if not math.isfinite(notional) or notional <= 0:
        raise ValueError('Positive finite notional required')
    profile, minutes, target, arm, minimum = PROFILES[strategy_id]
    # Freeze a distinct receipt on NEW Momentum entries only. Existing V1
    # positions retain their original 60/240/720 policy and observed floors.
    version = SCALP_VERSION if strategy_id == 'MOMENTUM' else VERSION
    return dict(version=version, holding_profile=profile, max_hold_minutes=minutes,
        take_profit_net_usd=target, stop_loss_net_usd=5.0,
        take_profit_net_pct=target/notional*100, stop_loss_net_pct=5.0/notional*100,
        profit_protection_arm_net_usd=arm, profit_giveback_fraction=.20,
        minimum_profit_giveback_usd=minimum, profit_trail_arm_net_pct=None,
        profit_trail_drawdown_pct=None, trigger_basis='TOTAL_POSITION_NET_PNL_AFTER_MODELED_COSTS',
        hold_clock_basis='ORIGINAL_OPENED_AT_NEVER_RESTART_OR_AMENDMENT',
        timeout_applies_to_losses=True, fresh_price_required=True,
        thresholds_are_not_guaranteed_fills=True,
        protection_peak_basis='PRESERVED_OBSERVED_NET_PEAK_OR_FIRST_FRESH_MARK',
        historical_peak_reused=False, profitability_proven=False)


def sized_parameters(strategy_id, notional_usd):
    """New lots only: preserve percentage risk/headroom when increasing size.

    A $5 stop on $250 would shrink from 5% to 2%, with up to 1.5% already
    consumed by admitted friction. No fitting to a few winners or reuse of an
    old peak. Previously frozen V1 dollar exits keep their original semantics.
    """
    result = parameters(strategy_id, notional_usd)
    factor = float(notional_usd)/100.0
    for key in ('take_profit_net_usd', 'stop_loss_net_usd',
                'profit_protection_arm_net_usd', 'minimum_profit_giveback_usd'):
        result[key] *= factor
    result.update(version=SIZED_VERSION,
        take_profit_net_pct=result['take_profit_net_usd']/float(notional_usd)*100,
        stop_loss_net_pct=result['stop_loss_net_usd']/float(notional_usd)*100,
        sizing_basis='PRESERVE_V1_PERCENTAGE_RISK_AND_COST_HEADROOM_NEW_LOTS_ONLY')
    return result
