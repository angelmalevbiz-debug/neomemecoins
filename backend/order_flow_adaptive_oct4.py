"""ORDER_FLOW_ADAPTIVE: the October 4, 2026 PAPER decision policy as an explicit,
versioned, opt-in strategy profile.

The decision logic is reconstructed from legacy ``neo-meme-trade`` commit 5b78efd
(entry policy ORDER_FLOW_BALANCED_V4, learning mode ADAPTIVE_CONTEXT_HOLD): the
strict order-flow entry filter, the conviction/context model, the five adaptive
hold modes and the exit ladder that ``engine_exit_policy`` evaluates on net
executable marks. Only decisions are restored. Execution evidence (exact pool,
confirmed flow, rug guard, independent price, Jupiter/PumpSwap quotes),
accounting (net marks, uncapped gap losses), durability and per-user isolation
stay on the modern engine. Nothing here submits swaps or promises profit.

Every value in ``Profile`` is strategy-owned: no environment variable changes
it, so the code, the strategy lock and the runtime cannot drift apart silently.
"""
from dataclasses import asdict, dataclass
import math
import re
from typing import Any

STRATEGY_ID = 'ORDER_FLOW_ADAPTIVE'
LEARNING_MODE = 'ADAPTIVE_CONTEXT_HOLD'
# The restored October 4 decision checks keep their historical label; the entry
# policy V5 is those unchanged checks behind DEFENSIVE_ENTRY_LAYER_V1, a modern
# overlay that only removes entries (2026-10-08).
DECISION_FILTER_VERSION = 'ORDER_FLOW_BALANCED_V4'
ENTRY_POLICY_VERSION = 'ORDER_FLOW_BALANCED_V5'
PREVIOUS_ENTRY_POLICY_VERSION = 'ORDER_FLOW_BALANCED_V4'
RESTORE_VERSION = 'ORDER_FLOW_ADAPTIVE_OCT4_RESTORE_V1'
SOURCE_COMMIT = 'neo-meme-trade@5b78efd (2026-10-04)'
SIGNAL_EVIDENCE = 'ORDER_FLOW_BALANCED_V4_ON_CONFIRMED_EXACT_POOL_FLOW_WITH_VERIFIED_EXECUTION_CHECKS'
EXECUTION_NOTE = ('PAPER only; October 4 ORDER_FLOW_ADAPTIVE decisions (one position, strict '
                  'order-flow entry, conviction-based adaptive hold) on modern exact-pool quote '
                  'evidence. Quotes and fees are modeled, not executed fills; gaps and unsellable '
                  'losses remain possible.')
# October 4 entry decisions read a 60-second exact-pool window; the conviction
# model reads the 30-second (fast) and 300-second (slow) windows.
ENTRY_FLOW_WINDOW_SECONDS = 60
FAST_FLOW_WINDOW_SECONDS = 30
SLOW_FLOW_WINDOW_SECONDS = 300
ABSOLUTE_MAX_HOLD_MINUTES = 120
# October 4 quoted the flat strategy notional once per candidate; the modern
# ensemble's smaller-size retries are not part of this profile.
SIZE_POLICY = 'FLAT_NOTIONAL_NO_BACKOFF'
_ADDRESS = re.compile(r'^[1-9A-HJ-NP-Za-km-z]{32,44}$')

# No outcome-based throttling or sizing existed on October 4.
NO_LEARNING = {'closed_trades': 0, 'win_rate_pct': None, 'profit_factor': None,
               'losses': 0, 'mode': LEARNING_MODE, 'throttled_strategies': []}


@dataclass(frozen=True)
class Profile:
    """Strategy-owned runtime values recovered from 5b78efd."""
    scan_seconds: int = 15
    position_scan_seconds: float = 2.0
    max_positions: int = 1
    trade_notional_usd: float = 200.0
    max_daily_loss_usd: float = 100.0
    stop_loss_pct: float = 5.0
    same_token_cooldown_seconds: int = 1200
    # Present as constants on October 4 but inert: the adaptive hold mode
    # overrides them. Published for transparency, never used for decisions.
    legacy_take_profit_pct: float = 18.0
    legacy_trailing_pct: float = 4.0
    legacy_max_hold_minutes: int = 7
    strict_entry_score: float = 85.0
    strict_min_conviction: float = 72.0
    strict_min_liquidity_usd: float = 30000.0
    strict_max_entry_impact_pct: float = 2.0
    strict_max_roundtrip_cost_pct: float = 2.75
    strict_max_worst_case_cost_pct: float = 4.5
    max_feed_age_ms: int = 30_000
    max_entry_quote_age_ms: int = 10_000
    max_quoted_candidates: int = 2

    def __post_init__(self):
        for name, value in asdict(self).items():
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
                raise ValueError(f'{name} must be a finite positive number')
        if not 0 <= self.strict_entry_score <= 100 or not 0 <= self.strict_min_conviction <= 100:
            raise ValueError('score and conviction thresholds must be within 0..100')
        if self.max_positions != 1:
            raise ValueError('ORDER_FLOW_ADAPTIVE holds exactly one position')
        if not self.strict_max_roundtrip_cost_pct <= self.strict_max_worst_case_cost_pct < self.stop_loss_pct:
            raise ValueError('cost caps must leave headroom below the net stop')

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


PROFILE = Profile()

ENTRY_CHECK_ORDER = (
    'invalid_pair', 'invalid_price', 'stale_feed', 'score', 'liquidity', 'momentum',
    'hour_trend', 'market_buyers', 'liquidity_ratio', 'flow_quality', 'flow_count',
    'flow_ratio', 'buy_volume', 'wallet_count', 'buyer_count', 'wallet_ratio',
    'large_sells', 'conviction',
)
DATA_CHECKS = ('invalid_pair', 'invalid_price', 'stale_feed')
MARKET_CHECKS = ('score', 'liquidity', 'momentum', 'hour_trend', 'market_buyers', 'liquidity_ratio')

HOLD_MODES = (
    # mode, minimum conviction, max hold minutes, fixed target pct, trail arm pct, trail pct
    ('RUNNER', 85.0, 60.0, None, 15.0, 7.0),
    ('STRONG', 72.0, 30.0, None, 12.0, 6.0),
    ('NORMAL', 58.0, 15.0, 20.0, 9.0, 5.0),
    ('CAUTIOUS', 45.0, 8.0, 14.0, 7.0, 4.0),
    ('WEAK', 0.0, 4.0, 8.0, 5.0, 3.0),
)
EXIT_LADDER = (
    'STOP_LOSS_NET_TARGET', 'CONVICTION_EXIT', 'ORDERFLOW_EXIT', 'ADAPTIVE_TP_<mode target>',
    'CONVICTION_PROFIT_LOCK', 'ADAPTIVE_TRAILING', 'ADAPTIVE_MAX_HOLD', 'ABSOLUTE_MAX_HOLD',
)
MODERN_OVERLAYS_RETAINED = (
    'entry_defense DEFENSIVE_ENTRY_LAYER_V1 (STRUCTURAL_RUG_GUARD_V1, POOL_LOSS_MEMORY_V1, '
    'HEAT_VETO_STACK_V1) before flow, safety and quotes',
    'promoted_entry_guard.flow_admission (confirmed 30s exact-pool PumpSwap window)',
    'engine_rug_guard fail-closed safety', 'pair_price_integrity exact-pool price confirmation',
    'Jupiter/PumpSwap executable entry and immediate-exit quotes', 'LIQUIDITY_EMERGENCY',
    'STALE_MARKET_EXIT', 'EXIT_IMPACT_EMERGENCY', 'uncapped net gap losses',
    'daily loss, drawdown and exposure caps', 'atomic ledger writes and audit outbox',
)


def _n(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
        return result if math.isfinite(result) else default
    except (TypeError, ValueError, OverflowError):
        return default


def _checks(coin: dict[str, Any], flow: dict[str, Any], context: dict[str, Any], *,
            now: float, profile: Profile) -> dict[str, bool]:
    changes = coin.get('priceChange') or {}
    tx = (coin.get('txns') or {}).get('m5') or {}
    liq = _n(coin.get('liquidityUsd'))
    mc = _n(coin.get('marketCap') or coin.get('fdv'))
    observed = _n(coin.get('updatedAt'))
    market_ratio = _n(tx.get('buys')) / max(_n(tx.get('sells')), 1.0)
    return {
        'invalid_pair': bool(_ADDRESS.fullmatch(str(coin.get('address') or '')))
                        and bool(_ADDRESS.fullmatch(str(coin.get('pairAddress') or ''))),
        'invalid_price': _n(coin.get('priceUsd')) > 0,
        'stale_feed': observed > 0 and 0 <= now - observed <= profile.max_feed_age_ms,
        'score': _n(coin.get('score')) >= profile.strict_entry_score,
        'liquidity': liq >= profile.strict_min_liquidity_usd,
        'momentum': -3.0 <= _n(changes.get('m5'), -999) <= 25.0,
        'hour_trend': -30.0 <= _n(changes.get('h1'), -999) <= 150.0,
        'market_buyers': market_ratio >= 1.0,
        'liquidity_ratio': mc > 0 and liq / mc >= 0.03,
        # Modern fail-closed addition: an incomplete exact-pool window cannot
        # decide an entry. October 4 read an unpinned window without this check.
        'flow_quality': str(flow.get('quality') or '').upper() == 'COMPLETE',
        'flow_count': _n(flow.get('trades')) >= 4,
        'flow_ratio': _n(flow.get('buy_sell_usd_ratio')) >= 1.30,
        'buy_volume': _n(flow.get('buy_usd')) >= 150.0,
        'wallet_count': _n(flow.get('unique_wallets')) >= 4,
        'buyer_count': _n(flow.get('buyer_wallets')) >= 3,
        'wallet_ratio': _n(flow.get('wallet_buy_sell_ratio')) >= 1.0,
        'large_sells': _n(flow.get('max_sell_usd')) < max(250.0, _n(flow.get('buy_usd')) * 0.5),
        'conviction': _n(context.get('conviction')) >= profile.strict_min_conviction,
    }


def signal_rejections(coin: dict[str, Any], flow: dict[str, Any], context: dict[str, Any], *,
                      now: float, profile: Profile = PROFILE) -> list[str]:
    """ORDER_FLOW_BALANCED_V4: every check must pass; failures keep their historical names."""
    checks = _checks(coin, flow or {}, context or {}, now=now, profile=profile)
    return [key for key in ENTRY_CHECK_ORDER if not checks[key]]


def market_rejections(coin: dict[str, Any], *, now: float, profile: Profile = PROFILE) -> list[str]:
    """Data and market-only checks; flow and conviction are decided later with tape evidence."""
    checks = _checks(coin, {}, {}, now=now, profile=profile)
    return [key for key in DATA_CHECKS + MARKET_CHECKS if not checks[key]]


def is_market_candidate(coin: dict[str, Any], *, now: float, profile: Profile = PROFILE) -> bool:
    return not market_rejections(coin, now=now, profile=profile)


def signal_metrics(coin: dict[str, Any], flow: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    """Observed values behind a decision, so diagnostics can say which check failed."""
    changes = coin.get('priceChange') or {}
    tx = (coin.get('txns') or {}).get('m5') or {}
    flow = flow or {}
    context = context or {}
    liq = _n(coin.get('liquidityUsd'))
    mc = _n(coin.get('marketCap') or coin.get('fdv'))
    return {
        'score': round(_n(coin.get('score')), 2), 'liquidity_usd': round(liq, 2),
        'market_cap_usd': round(mc, 2), 'liquidity_cap_ratio': round(liq / mc, 4) if mc > 0 else None,
        'm5_pct': round(_n(changes.get('m5')), 3), 'h1_pct': round(_n(changes.get('h1')), 3),
        'market_buy_sell_ratio': round(_n(tx.get('buys')) / max(_n(tx.get('sells')), 1.0), 3),
        'flow_window_seconds': flow.get('seconds'), 'flow_quality': flow.get('quality'),
        'flow_trades': flow.get('trades'), 'flow_buy_sell_usd_ratio': flow.get('buy_sell_usd_ratio'),
        'flow_buy_usd': flow.get('buy_usd'), 'flow_unique_wallets': flow.get('unique_wallets'),
        'flow_buyer_wallets': flow.get('buyer_wallets'),
        'flow_wallet_buy_sell_ratio': flow.get('wallet_buy_sell_ratio'),
        'flow_max_sell_usd': flow.get('max_sell_usd'),
        'conviction': context.get('conviction'), 'hold_mode': context.get('mode'),
    }


def conviction_score(*, fast_ratio: float, slow_ratio: float, unique_wallets: float,
                     repeat_buy_wallets: float, whale_buy_usd: float, whale_sell_usd: float,
                     m5: float, h1: float, market_ratio: float, liquidity_ratio: float,
                     neo_score: float) -> float:
    """October 4 market_context scoring: start at 50, apply live context, clamp to 0..100."""
    score = 50.0
    if fast_ratio >= 3.0:
        score += 18
    elif fast_ratio >= 2.0:
        score += 12
    elif fast_ratio >= 1.4:
        score += 6
    elif fast_ratio < 0.8:
        score -= 20
    elif fast_ratio < 1.0:
        score -= 10

    if slow_ratio >= 2.0:
        score += 12
    elif slow_ratio >= 1.4:
        score += 7
    elif slow_ratio < 0.8:
        score -= 15
    elif slow_ratio < 1.0:
        score -= 7

    if unique_wallets >= 10:
        score += 6
    elif unique_wallets >= 5:
        score += 3
    elif unique_wallets <= 2:
        score -= 5

    if repeat_buy_wallets >= 3:
        score += 6
    elif repeat_buy_wallets >= 1:
        score += 3

    if whale_buy_usd > 0 and whale_buy_usd >= whale_sell_usd * 1.3:
        score += 6
    elif whale_sell_usd > 0 and whale_sell_usd >= max(whale_buy_usd * 1.3, 750.0):
        score -= 8

    if 0 <= m5 <= 10:
        score += 8
    elif -2 <= m5 < 0:
        score += 2
    elif m5 > 20:
        score -= 8
    elif m5 < -5:
        score -= 15

    if 0 <= h1 <= 120:
        score += 4
    elif h1 < -15:
        score -= 8
    elif h1 > 250:
        score -= 5

    if market_ratio >= 1.4:
        score += 8
    elif market_ratio >= 1.1:
        score += 4
    elif market_ratio < 0.8:
        score -= 8

    if liquidity_ratio >= 0.95:
        score += 3
    elif liquidity_ratio < 0.80:
        score -= 15
    elif liquidity_ratio < 0.90:
        score -= 7

    if neo_score >= 95:
        score += 4
    elif neo_score < 85:
        score -= 4

    return round(max(0.0, min(100.0, score)), 1)


def hold_mode(conviction: float) -> dict[str, Any]:
    """ADAPTIVE_CONTEXT_HOLD: RUNNER >= 85, STRONG >= 72, NORMAL >= 58, CAUTIOUS >= 45, else WEAK."""
    level = _n(conviction)
    for mode, floor, max_hold, target, trail_arm, trail in HOLD_MODES:
        if level >= floor:
            return {'mode': mode, 'max_hold_minutes': max_hold, 'target_pct': target,
                    'trail_arm_pct': trail_arm, 'trail_pct': trail}
    mode, _floor, max_hold, target, trail_arm, trail = HOLD_MODES[-1]
    return {'mode': mode, 'max_hold_minutes': max_hold, 'target_pct': target,
            'trail_arm_pct': trail_arm, 'trail_pct': trail}


def effective_entry_thresholds(profile: Profile = PROFILE) -> dict[str, dict[str, Any]]:
    return {STRATEGY_ID: {
        'score': profile.strict_entry_score, 'liquidity': profile.strict_min_liquidity_usd,
        'conviction': profile.strict_min_conviction, 'move_5m_pct': [-3, 25],
        'move_1h_pct': [-30, 150], 'market_buy_sell': 1.0, 'liquidity_cap': 0.03,
        'flow_window_seconds': ENTRY_FLOW_WINDOW_SECONDS, 'flow_quality': 'COMPLETE',
        'flow_trades': 4, 'flow_ratio': 1.3, 'flow_buy_usd': 150.0, 'wallets': 4, 'buyers': 3,
        'wallet_ratio': 1.0, 'max_sell_usd': 'max(250, buy_usd * 0.5)',
        'impact': profile.strict_max_entry_impact_pct,
        'roundtrip_cost': profile.strict_max_roundtrip_cost_pct,
        'worst_case_cost': profile.strict_max_worst_case_cost_pct,
        'feed_age_ms': profile.max_feed_age_ms, 'quote_age_ms': profile.max_entry_quote_age_ms,
        'max_quoted_candidates': profile.max_quoted_candidates,
    }}


def config_ownership(profile: Profile = PROFILE) -> dict[str, str]:
    owner = f'strategy_profile:{STRATEGY_ID}'
    ownership = {key: owner for key in profile.as_dict()}
    ownership.update({
        'signal_strategy': 'NEO_SIGNAL_STRATEGY (account registry signal_strategy via user_gateway)',
        'entry_policy_version': f'{owner} constant {ENTRY_POLICY_VERSION}',
        'exit_policy': f'{owner} constant adaptive (engine_exit_policy.ADAPTIVE_VERSION)',
        'hold_modes': f'{owner} HOLD_MODES', 'conviction_model': f'{owner} conviction_score',
        'legacy_take_profit_pct': 'inert constant (adaptive hold overrides)',
        'legacy_trailing_pct': 'inert constant (adaptive hold overrides)',
        'legacy_max_hold_minutes': 'inert constant (adaptive hold overrides)',
        'environment_overrides_honored': 'none for strategy values',
    })
    return ownership


def config_snapshot(profile: Profile = PROFILE) -> dict[str, Any]:
    return {
        'strategy_id': STRATEGY_ID, 'learning_mode': LEARNING_MODE,
        'entry_policy_version': ENTRY_POLICY_VERSION, 'restore_version': RESTORE_VERSION,
        'decision_filter_version': DECISION_FILTER_VERSION,
        'source_commit': SOURCE_COMMIT, 'profile': profile.as_dict(),
        'entry_flow_window_seconds': ENTRY_FLOW_WINDOW_SECONDS,
        'conviction_flow_windows_seconds': [FAST_FLOW_WINDOW_SECONDS, SLOW_FLOW_WINDOW_SECONDS],
        'entry_checks': list(ENTRY_CHECK_ORDER),
        'hold_modes': [{'mode': mode, 'min_conviction': floor, 'max_hold_minutes': max_hold,
                        'target_pct': target, 'trail_arm_pct': trail_arm, 'trail_pct': trail}
                       for mode, floor, max_hold, target, trail_arm, trail in HOLD_MODES],
        'exit_ladder': list(EXIT_LADDER), 'absolute_max_hold_minutes': ABSOLUTE_MAX_HOLD_MINUTES,
        'size_policy': SIZE_POLICY,
        'inert_legacy_constants': ['legacy_take_profit_pct', 'legacy_trailing_pct', 'legacy_max_hold_minutes'],
        'modern_overlays_retained': list(MODERN_OVERLAYS_RETAINED),
        'config_ownership': config_ownership(profile),
        'profitability_proven': False,
    }
