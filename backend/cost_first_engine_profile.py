"""COST_FIRST_ESTABLISHED_PAPER_V1: opt-in per-account engine profile (Stage 2).

PAPER only. Selected per engine process with NEO_SIGNAL_STRATEGY (set for one
account by the user gateway from the account registry); every other engine
keeps the default WINNER_ENSEMBLE_PAPER_V1. Nothing here submits swaps,
resets a ledger or claims an edge.

Entry: the market candidate screen is the cost-first universe of
``cost_first_established`` (PumpSwap tier <= 50 bps, liquidity >= $250k,
modeled fee + impact round trip <= 1.2% at the sized entry), imported, never
restated. Every later engine gate is unchanged: signal data, confirmed 30 s
exact-pool flow, independent price, rug guard, rent/SOL, executable quote
preflight with the engine's own cost caps, commit-time rechecks. Size is the
universe's liquidity rule applied to the engine notional, then the engine's
daily-budget sizing and size backoff. Positions, daily loss, drawdown,
exposure, cooldown and the net stop stay engine-owned.

Exit (policy ``cost_first``): the engine's fixed net geometry (-5% stop,
+10% target, 60 min hold), LIQUIDITY_EMERGENCY and STALE_MARKET_EXIT unchanged,
and EXIT_IMPACT_EMERGENCY_V2 instead of V1. V2 anchors the threshold to the
entry preflight SELL quote impact (research 2026-10-08: sell-side quotes ran
about +0.33 pp above buy-side quotes at the same instant and 15/100 trades met
the V1 condition already at entry) and requires a fresh forced exact-pool sell
quote at least 2 s after the first trigger before it exits. Only an exact-pool
mark arms, and a re-arm cooldown plus a per-position confirmation budget bound
the forced quotes it spends from the shared quote budget. Whether that is
better or worse for this universe is unknown; it is a versioned hypothesis.
"""
from dataclasses import asdict, dataclass
import math
from typing import Any

import cost_first_established as cost_first
import engine_exit_policy as exit_policy

STRATEGY_ID = 'COST_FIRST_ESTABLISHED_PAPER_V1'
PROFILE_VERSION = 'COST_FIRST_ENGINE_PROFILE_V1'
ENTRY_POLICY_VERSION = 'COST_FIRST_ESTABLISHED_ENTRY_V1'
EXIT_POLICY = 'cost_first'
EXIT_POLICY_VERSION = 'COST_FIRST_NET_EXIT_V1'
# The stop / target / hold decision is engine_exit_policy's fixed geometry,
# evaluated by the unchanged function; only the impact emergency differs.
EXIT_GEOMETRY_POLICY = 'fixed'
LEARNING_MODE = 'NONE_FIXED_HYPOTHESIS'
SIGNAL_SOURCE = cost_first.VERSION
SIGNAL_EVIDENCE = 'COST_FIRST_UNIVERSE_V1_ON_CONFIRMED_EXACT_POOL_FLOW_WITH_VERIFIED_EXECUTION_CHECKS'
SIZE_POLICY = 'COST_FIRST_LIQUIDITY_SCALED_V1'
SIZE_RULE = ('min(TRADE_NOTIONAL_USD, liquidity_usd * liquidity_size_fraction), then the engine '
             'daily-budget sizing (engine_runtime.plan_notional) and entry_size_backoff steps')
# The engine refuses any planned entry below $10 (risk_budget_unavailable).
MIN_ENTRY_NOTIONAL_USD = 10.0
EXECUTION_NOTE = ('PAPER only; COST_FIRST_ESTABLISHED universe (PumpSwap tier <= 50 bps, liquidity >= '
                  '$250k, modeled fee+impact round trip <= 1.2%) on the engine\'s exact-pool flow, safety, '
                  'price and executable-quote gates. Quotes and fees are modeled, not executed fills; gaps '
                  'and unsellable losses remain possible. Unvalidated hypothesis, no profitability claim.')
# No outcome-based throttling or sizing: a fixed, pre-registered hypothesis.
NO_LEARNING = {'closed_trades': 0, 'win_rate_pct': None, 'profit_factor': None,
               'losses': 0, 'mode': LEARNING_MODE, 'throttled_strategies': []}

EIE_VERSION = 'EXIT_IMPACT_EMERGENCY_V2'
EIE_REASON = 'EXIT_IMPACT_EMERGENCY'
ANCHOR_SELL = 'ENTRY_PREFLIGHT_SELL_IMPACT'
ANCHOR_BUY_FALLBACK = 'ENTRY_BUY_IMPACT_FALLBACK_V1'
ANCHOR_NONE = 'NO_ENTRY_IMPACT_FLOOR_ONLY'
EIE_V2_RULE = ('arm when the exit quote impact >= max(exit_impact_emergency_pct, entry preflight sell '
               'impact + entry_margin_pct) (entry buy impact only when no sell impact was recorded); exit '
               'only if a fresh forced exact-pool sell quote taken >= confirm_delay_ms after arming still '
               'meets the threshold, otherwise disarm and record it; only an exact-entry-pool mark arms, '
               'and after any disarm no re-arm for rearm_cooldown_ms and at most max_confirms_per_window '
               'forced confirmation quotes per position per confirm_window_ms')
# Confirmation-quote problems that disarm instead of exiting.
CONFIRM_PROBLEMS = ('confirm_quote_unavailable', 'confirm_quote_invalid', 'confirm_quote_cached',
                    'confirm_quote_stale', 'confirm_quote_before_delay', 'confirm_quote_not_exact_pool',
                    'confirm_below_threshold')
# Confirmation problems whose quote is still a finite, fresh mark: the net
# geometry (stop/target/max hold) is evaluated on it before disarming.
CONFIRM_PROBLEMS_WITHOUT_MARK = ('confirm_quote_unavailable', 'confirm_quote_invalid', 'confirm_quote_stale')
# Marks at/above the threshold that do not arm (counted, never a forced quote).
# An off-pool best route can never confirm (confirmation requires the exact entry
# pool), so arming on it would only spend shared quote budget.
TRIGGER_BLOCKS = ('trigger_not_exact_pool', 'trigger_in_rearm_cooldown', 'trigger_confirm_budget_exhausted')


@dataclass(frozen=True)
class ImpactEmergencyV2:
    """Strategy-owned EXIT_IMPACT_EMERGENCY_V2 parameters (no environment override)."""
    floor_pct: float = 0.75          # equals the engine EXIT_IMPACT_EMERGENCY_PCT
    entry_margin_pct: float = 0.50   # equals the engine V1 margin; only the anchor changes
    confirm_delay_ms: int = 2000
    max_disarm_records: int = 10
    # Quote-budget bounds: every confirmation is one forced exit-priority Jupiter
    # quote from the shared keyless budget (one quote per ~2.1 s for all processes,
    # and exits pre-empt entries).
    rearm_cooldown_ms: int = 15_000
    max_confirms_per_window: int = 3
    confirm_window_ms: int = 300_000

    def __post_init__(self):
        for name, value in asdict(self).items():
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
                raise ValueError(f'{name} must be a finite positive number')


EIE_V2 = ImpactEmergencyV2()


def finite(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return result if math.isfinite(result) else None


# ---------------------------------------------------------------- entry ----

def universe_rejections(coin: dict[str, Any], cap_usd: float) -> list[str]:
    """Cost-first universe failures in evaluation order; empty means candidate."""
    return cost_first.rejections(coin, cap_usd=cap_usd, minimum_notional_usd=MIN_ENTRY_NOTIONAL_USD)


def is_market_candidate(coin: dict[str, Any], cap_usd: float) -> bool:
    return not universe_rejections(coin, cap_usd)


def universe_metrics(coin: dict[str, Any], cap_usd: float) -> dict[str, Any]:
    """Rejection-example metrics: the observed values each universe rule used."""
    described = cost_first.describe(coin, cap_usd=cap_usd, minimum_notional_usd=MIN_ENTRY_NOTIONAL_USD)
    roundtrip = described.get('fee_impact_roundtrip_pct')
    return {
        'universe_version': described['universe_version'],
        'dex_id': str(coin.get('dexId') or '').lower() or None,
        'quote_token_is_sol': cost_first.feasibility.quote_token_address(coin) == cost_first.SOL_QUOTE_MINT,
        'fee_tier_bps': described.get('fee_tier_bps'),
        'max_fee_tier_bps': cost_first.UNIVERSE.max_fee_tier_bps,
        'liquidity_usd': described.get('liquidity_usd'),
        'min_liquidity_usd': cost_first.UNIVERSE.min_liquidity_usd,
        'planned_notional_usd': described.get('planned_notional_usd'),
        'fee_impact_roundtrip_pct': None if roundtrip is None else round(roundtrip, 4),
        'max_fee_impact_roundtrip_pct': cost_first.UNIVERSE.max_fee_impact_roundtrip_pct,
        'is_execution_quote': False,
    }


def requested_notional(coin: dict[str, Any], cap_usd: float) -> float:
    """The universe size rule; it only shrinks the engine notional, never raises it."""
    return cost_first.size_for(cost_first.liquidity_usd(coin) or 0.0, cap_usd)


def impact_pct_from_raw_quote(raw_quote: Any) -> float | None:
    """Percent impact of a stored raw provider quote (priceImpactPct is a fraction)."""
    if not isinstance(raw_quote, dict):
        return None
    value = finite(raw_quote.get('priceImpactPct'))
    return None if value is None else value * 100


def entry_sell_impact_pct(initial_exit: dict[str, Any] | None) -> float | None:
    """Impact of the entry preflight (immediate-exit) sell quote, in percent."""
    initial_exit = initial_exit or {}
    value = finite(initial_exit.get('price_impact_pct'))
    return value if value is not None else impact_pct_from_raw_quote(initial_exit.get('raw_quote'))


# ----------------------------------------------------------------- exit ----

def emergency_threshold(position: dict[str, Any], params: ImpactEmergencyV2 = EIE_V2) -> dict[str, Any]:
    """V2 threshold and the anchor it used, recorded with every arm/exit."""
    sell = finite(position.get('entry_sell_impact_pct'))
    source = 'entry_sell_impact_pct'
    if sell is None:
        sell = impact_pct_from_raw_quote(position.get('preflight_sell_quote'))
        source = 'preflight_sell_quote'
    buy = finite(position.get('entry_price_impact_pct'))
    if sell is not None:
        anchor, anchor_impact = ANCHOR_SELL, sell
    elif buy is not None:
        anchor, anchor_impact, source = ANCHOR_BUY_FALLBACK, buy, 'entry_price_impact_pct'
    else:
        anchor, anchor_impact, source = ANCHOR_NONE, None, None
    threshold = params.floor_pct if anchor_impact is None else max(params.floor_pct,
                                                                   anchor_impact + params.entry_margin_pct)
    return {'rule_version': EIE_VERSION, 'threshold_pct': round(threshold, 6), 'anchor': anchor,
            'anchor_source': source, 'anchor_impact_pct': anchor_impact,
            'entry_sell_impact_pct': sell, 'entry_buy_impact_pct': buy,
            'floor_pct': params.floor_pct, 'entry_margin_pct': params.entry_margin_pct}


def confirm_quote_problem(quote: dict[str, Any] | None, *, armed_at: float, now: float,
                          max_age_ms: float, threshold_pct: float,
                          params: ImpactEmergencyV2 = EIE_V2) -> str | None:
    """None when the confirmation quote proves the emergency, else the disarm code.

    Only a newly received (forced, not cached) quote for the exact entry pool,
    taken at least ``confirm_delay_ms`` after arming and still fresh, counts.
    """
    if not isinstance(quote, dict):
        return 'confirm_quote_unavailable'
    net = finite(quote.get('net_proceeds_usd'))
    quoted_at = finite(quote.get('quoted_at'))
    impact = finite(quote.get('impact_pct'))
    if net is None or quoted_at is None or impact is None:
        return 'confirm_quote_invalid'
    if quote.get('from_cache'):
        return 'confirm_quote_cached'
    if not 0 <= now - quoted_at <= max_age_ms:
        return 'confirm_quote_stale'
    if quoted_at - armed_at < params.confirm_delay_ms:
        return 'confirm_quote_before_delay'
    if quote.get('route_matches_entry_pool') is not True:
        return 'confirm_quote_not_exact_pool'
    if impact < threshold_pct:
        return 'confirm_below_threshold'
    return None


def confirm_quote_is_mark(quote: dict[str, Any] | None, problem: str | None, *, now: float,
                          max_age_ms: float) -> bool:
    """True when a confirmation quote is a finite, fresh mark the net geometry may act on.

    Every problem except unavailable/invalid/stale qualifies (cached, before the delay,
    off-pool, below the threshold); freshness is checked again because the cached
    check precedes the age check in confirm_quote_problem.
    """
    if problem in CONFIRM_PROBLEMS_WITHOUT_MARK or not isinstance(quote, dict):
        return False
    quoted_at = finite(quote.get('quoted_at'))
    return (finite(quote.get('net_proceeds_usd')) is not None and quoted_at is not None
            and 0 <= now - quoted_at <= max_age_ms)


def disarmed(state: dict[str, Any], *, code: str, now: float, confirm: dict[str, Any] | None,
             params: ImpactEmergencyV2 = EIE_V2) -> dict[str, Any]:
    """Return the V2 state after a disarm; the record keeps the last few disarms."""
    record = {'armed_at': state.get('armed_at'), 'disarmed_at': now, 'reason': code,
              'threshold_pct': state.get('threshold_pct'), 'anchor': state.get('anchor'),
              'trigger_impact_pct': (state.get('trigger_quote') or {}).get('impact_pct'),
              'confirm_quote': confirm}
    disarms = (list(state.get('disarms') or []) + [record])[-int(params.max_disarm_records):]
    return {**state, 'armed': False, 'armed_at': None, 'trigger_quote': None,
            'disarm_count': int(state.get('disarm_count') or 0) + 1, 'last_disarm': record,
            'disarms': disarms, 'rearm_not_before': now + params.rearm_cooldown_ms}


def recent_confirms(state: dict[str, Any], now: float, params: ImpactEmergencyV2 = EIE_V2) -> list[float]:
    """Forced confirmation quote times inside the rolling confirm window."""
    times = (finite(value) for value in state.get('confirm_attempts_at') or [])
    return [value for value in times if value is not None and 0 <= now - value < params.confirm_window_ms]


def arm_block(state: dict[str, Any], quote: dict[str, Any], *, now: float,
              params: ImpactEmergencyV2 = EIE_V2) -> str | None:
    """None when a mark at/above the threshold may arm, else the TRIGGER_BLOCKS code."""
    if quote.get('route_matches_entry_pool') is not True:
        return 'trigger_not_exact_pool'
    rearm_not_before = finite(state.get('rearm_not_before'))
    if rearm_not_before is not None and now < rearm_not_before:
        return 'trigger_in_rearm_cooldown'
    if len(recent_confirms(state, now, params)) >= params.max_confirms_per_window:
        return 'trigger_confirm_budget_exhausted'
    return None


def blocked_trigger(state: dict[str, Any], *, code: str, now: float, impact_pct: Any) -> dict[str, Any]:
    """Count a mark that met the threshold but did not arm (no forced quote is taken)."""
    counts = dict(state.get('trigger_blocks') or {})
    counts[code] = int(counts.get(code) or 0) + 1
    return {**state, 'trigger_blocks': counts,
            'last_trigger_block': {'reason': code, 'at': now, 'impact_pct': finite(impact_pct)}}


def confirm_attempted(state: dict[str, Any], now: float, params: ImpactEmergencyV2 = EIE_V2) -> dict[str, Any]:
    """Record one forced confirmation quote (bounded to the rolling window)."""
    attempts = recent_confirms(state, now, params) + [now]
    return {**state, 'confirm_attempts_at': attempts[-int(params.max_confirms_per_window):],
            'confirm_count': int(state.get('confirm_count') or 0) + 1}


def exit_parameters(stop_loss_pct: float, take_profit_pct: float) -> dict[str, Any]:
    return {
        'exit_policy': EXIT_POLICY, 'exit_policy_version': EXIT_POLICY_VERSION,
        'geometry': f'engine_exit_policy.{EXIT_GEOMETRY_POLICY} ({exit_policy.VERSION} decisions)',
        'stop_loss_net_pct': -float(stop_loss_pct), 'stop_reason': 'STOP_LOSS_NET_TARGET',
        'take_profit_net_pct': float(take_profit_pct), 'take_profit_reason': 'TAKE_PROFIT_10_NET',
        'max_hold_minutes': exit_policy.FIXED_MAX_HOLD_MINUTES,
        'max_hold_reason': exit_policy.max_hold_label(exit_policy.FIXED_MAX_HOLD_MINUTES),
        'safety_exits_unchanged': ['LIQUIDITY_EMERGENCY', 'STALE_MARKET_EXIT'],
        'exit_impact_emergency': {'version': EIE_VERSION, 'rule': EIE_V2_RULE, **asdict(EIE_V2),
                                  'anchors': [ANCHOR_SELL, ANCHOR_BUY_FALLBACK, ANCHOR_NONE],
                                  'disarm_codes': list(CONFIRM_PROBLEMS),
                                  'trigger_block_codes': list(TRIGGER_BLOCKS),
                                  'geometry_on_confirm_quote_unless': list(CONFIRM_PROBLEMS_WITHOUT_MARK)},
        # Engine order, unchanged: a pending exit or a safety exit first, then the net
        # geometry; V2 can only act when none of them fired, on the arming mark and
        # again on the confirmation quote.
        'priority': ['pending exit / LIQUIDITY_EMERGENCY / STALE_MARKET_EXIT', 'STOP_LOSS_NET_TARGET',
                     'TAKE_PROFIT_10_NET', 'MAX_HOLD_60', 'EXIT_IMPACT_EMERGENCY (V2: arm, then confirm)'],
        'applies_to': 'positions opened with exit_policy cost_first only; other positions keep their own policy',
    }


def universe_parameters() -> dict[str, Any]:
    return {**asdict(cost_first.UNIVERSE), 'universe_version': cost_first.UNIVERSE_VERSION,
            'minimum_entry_notional_usd': MIN_ENTRY_NOTIONAL_USD,
            'rejection_reasons': list(cost_first.REJECTION_REASONS),
            'source_module': 'backend/cost_first_established.py'}


def size_rule(cap_usd: float) -> dict[str, Any]:
    return {'size_policy': SIZE_POLICY, 'rule': SIZE_RULE, 'notional_cap_usd': float(cap_usd),
            'liquidity_size_fraction': cost_first.UNIVERSE.liquidity_size_fraction,
            'minimum_entry_notional_usd': MIN_ENTRY_NOTIONAL_USD, 'size_backoff': 'entry_size_backoff (engine default)'}


def hash_basis(*, cap_usd: float, stop_loss_pct: float, take_profit_pct: float) -> dict[str, Any]:
    """Every profile-owned value that changes decisions; part of effective_config_hash."""
    return {'strategy_id': STRATEGY_ID, 'profile_version': PROFILE_VERSION,
            'entry_policy_version': ENTRY_POLICY_VERSION, 'learning_mode': LEARNING_MODE,
            'universe': universe_parameters(), 'size': size_rule(cap_usd),
            'exit': exit_parameters(stop_loss_pct, take_profit_pct)}


def config_ownership(engine_ownership: dict[str, str]) -> dict[str, str]:
    """Engine ownership with the profile's own keys named; risk limits stay engine-owned."""
    owner = f'strategy_profile:{STRATEGY_ID}'
    ownership = {key: f'engine-owned: {value}' for key, value in engine_ownership.items()}
    ownership.update({
        'signal_strategy': 'NEO_SIGNAL_STRATEGY (account registry signal_strategy via user_gateway)',
        'entry_policy_version': f'{owner} constant {ENTRY_POLICY_VERSION}',
        'exit_policy': f'{owner} constant {EXIT_POLICY} ({EXIT_POLICY_VERSION})',
        'take_profit_pct': 'engine-owned: market_monitor.TAKE_PROFIT_PCT via the fixed exit geometry',
        'max_hold_minutes': 'engine-owned: engine_exit_policy.FIXED_MAX_HOLD_MINUTES (60)',
        'trailing_pct': 'engine-owned constant, unused by the cost_first exit policy',
        'exit_impact_emergency': f'{owner} cost_first_engine_profile.EIE_V2 ({EIE_VERSION})',
        'entry_score': f'{owner}: not a gate (universe is cost-defined)',
        'min_liquidity_usd': f'{owner}: cost_first_established.UNIVERSE.min_liquidity_usd',
        'effective_entry_thresholds': f'{owner}: cost_first_established.UNIVERSE',
        'universe_parameters': f'{owner}: cost_first_established.UNIVERSE (imported, not restated)',
        'size_rule': f'{owner}: cost_first_established.size_for on the engine trade_notional_usd',
        'learning_mode': f'{owner} constant {LEARNING_MODE} (no outcome throttle)',
        'environment_overrides_honored': ('engine values only (scan, position scan, notional cap, daily loss, '
                                          'risk caps; cost caps can only be lowered); no profile value'),
    })
    return ownership


def config_snapshot(*, cap_usd: float, stop_loss_pct: float, take_profit_pct: float,
                    engine_ownership: dict[str, str]) -> dict[str, Any]:
    return {
        'strategy_id': STRATEGY_ID, 'profile_version': PROFILE_VERSION,
        'entry_policy_version': ENTRY_POLICY_VERSION, 'learning_mode': LEARNING_MODE,
        'signal_source': SIGNAL_SOURCE, 'signal_evidence': SIGNAL_EVIDENCE,
        'universe': universe_parameters(), 'size_rule': size_rule(cap_usd),
        'exit': exit_parameters(stop_loss_pct, take_profit_pct),
        'engine_gates_unchanged': [
            'engine_entry_policy.signal_data_rejections',
            'promoted_entry_guard.flow_admission (confirmed 30 s exact-pool PumpSwap window)',
            'pair_price_integrity independent price (Jupiter tie-break)', 'engine_rug_guard fail-closed safety',
            'promoted_entry_guard.risk_admission', 'token account rent and SOL price known',
            'executable entry + immediate-exit quote preflight with the engine cost caps',
            'commit-time recheck of data, flow, universe, safety, budget, quote age and signal age'],
        'engine_owned': ['max_positions', 'max_daily_loss_usd', 'max_drawdown_pct', 'max_total_exposure_pct',
                         'max_position_full_loss_risk_usd', 'same_token_cooldown_seconds', 'stop_loss_pct',
                         'trade_notional_usd', 'strict_max_entry_impact_pct', 'strict_max_roundtrip_cost_pct',
                         'strict_max_worst_case_cost_pct', 'scan_seconds', 'position_scan_seconds'],
        'config_ownership': config_ownership(engine_ownership),
        'evidence_status': 'PROSPECTIVE_HYPOTHESIS_UNVALIDATED',
        'profitability_proven': False,
    }
