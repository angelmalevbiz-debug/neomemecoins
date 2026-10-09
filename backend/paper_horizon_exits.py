"""Owner-requested PAPER holding horizons; hypotheses, not a proven profit edge."""
import math

VERSION = 'PAPER_HORIZON_EXIT_V1_60_240_720'
PROFILES = {
    'EARLY': ('QUICK', 60, 6.0, 2.0, .75),
    'MOMENTUM': ('QUICK', 60, 6.0, 2.0, .75),
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
    return dict(version=VERSION, holding_profile=profile, max_hold_minutes=minutes,
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
