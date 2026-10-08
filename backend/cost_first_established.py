"""COST_FIRST_ESTABLISHED: a cost-defined PAPER universe for one isolated Lab book pair.

Pure definitions only. This module decides whether a market observation belongs
to the universe and how large a modeled PAPER entry may be; it never admits an
entry on its own. Confirmed exact-pool flow, full fresh safety, independent
price identity and the Lab's round-trip cost cap are applied unchanged by the
caller. A later per-account engine profile must import these same definitions
instead of restating them.

Hypothesis (research 2026-10-08, cost-first lens): the Lab and engine losses
are dominated by a 2.4-2.7% modeled round trip on 90-125 bps PumpSwap pools
under a 3-5% net stop. Pools in fee tiers <= 50 bps with >= $250,000 liquidity
are the only observed universe whose fee+impact round trip leaves several
percentage points of gross headroom before the stop. Nothing here claims that
such pools have positive expectancy; the two books exist to measure that on
future, untouched observations under docs/STRATEGY_VALIDATION.md.
"""
from dataclasses import asdict, dataclass
import math

import paper_market_feasibility as feasibility

VERSION = 'COST_FIRST_ESTABLISHED_V1'
ENTRY_POLICY_VERSION = 'COST_FIRST_ESTABLISHED_V1'
UNIVERSE_VERSION = 'COST_FIRST_UNIVERSE_V1'
CONTROL_BOOK_ID = 'COST_FIRST_CONTROL'
SCALED_BOOK_ID = 'COST_FIRST_SCALED'
BOOK_IDS = (CONTROL_BOOK_ID, SCALED_BOOK_ID)
BOOK_NAMES = {CONTROL_BOOK_ID: 'Cost-First Control 3/10',
              SCALED_BOOK_ID: 'Cost-First Scaled 3/4'}
START_BALANCE_USD = 500.0
PORTFOLIO_GROUP = 'TEST'
AUTOMATIC_PROMOTION = False
SOL_QUOTE_MINT = feasibility.SOL_QUOTE_MINT


@dataclass(frozen=True)
class UniverseParameters:
    """Physical cost screen; score, age and momentum are deliberately absent."""
    dex_id: str = 'pumpswap'
    quote_token_address: str = SOL_QUOTE_MINT
    max_fee_tier_bps: float = 50.0
    min_liquidity_usd: float = 250_000.0
    # Modeled DEX fee on both legs plus constant-product impact at the sized
    # entry. Slippage/latency buffers and network fees are excluded here on
    # purpose; the Lab's full cost model still caps admission afterwards.
    max_fee_impact_roundtrip_pct: float = 1.2
    liquidity_size_fraction: float = 0.001


@dataclass(frozen=True)
class ExitParameters:
    """Net (after modeled costs) exit geometry of one book."""
    label: str
    stop_loss_net_pct: float
    take_profit_net_pct: float
    max_hold_minutes: float

    @property
    def stop_reason(self) -> str:
        return f'STOP_LOSS_{_whole(self.stop_loss_net_pct)}_NET'

    @property
    def take_profit_reason(self) -> str:
        return f'TAKE_PROFIT_{_whole(self.take_profit_net_pct)}_NET'

    @property
    def max_hold_reason(self) -> str:
        return f'ABSOLUTE_MAX_HOLD_{_whole(self.max_hold_minutes)}'


def _whole(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else str(value)


UNIVERSE = UniverseParameters()
# Book A keeps the Lab's standard -3% net stop / +10% net target / 60 min hold.
CONTROL_EXITS = ExitParameters('LAB_STANDARD_3_10_60', 3.0, 10.0, 60.0)
# Book B scales the target to the universe's cost structure: same stop, +4% net.
SCALED_EXITS = ExitParameters('COST_SCALED_3_4_60', 3.0, 4.0, 60.0)
EXITS = {CONTROL_BOOK_ID: CONTROL_EXITS, SCALED_BOOK_ID: SCALED_EXITS}

REJECTION_REASONS = (
    'dex_not_pumpswap', 'quote_token_not_sol', 'network_price_unknown',
    'market_cap_unknown', 'liquidity_unknown', 'liquidity_below_minimum',
    'fee_tier_above_maximum', 'size_below_minimum', 'cost_model_unavailable',
    'fee_impact_roundtrip_above_maximum',
)


def _finite(value):
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return result if math.isfinite(result) else None


def liquidity_usd(coin: dict) -> float | None:
    value = _finite(coin.get('liquidityUsd'))
    if value is None and isinstance(coin.get('liquidity'), dict):
        value = _finite(coin['liquidity'].get('usd'))
    return value


def fee_tier_bps(coin: dict) -> float | None:
    """PumpSwap fee tier from market cap in SOL (marketCap / (priceUsd/priceNative)).

    Returns None when the denomination or market cap cannot be established, so
    an unknown fee can never be read as the cheapest tier.
    """
    if str(coin.get('dexId') or '').lower() != UNIVERSE.dex_id:
        return None
    if feasibility.sol_usd_from_coin(coin) <= 0:
        return None
    cap_value = coin.get('marketCap')
    if cap_value is None:
        cap_value = coin.get('fdv')
    cap = _finite(cap_value)
    if cap is None or cap <= 0:
        return None
    return feasibility.pumpswap_fee_bps(coin)


def size_for(liquidity: float, cap_usd: float, *,
             params: UniverseParameters = UNIVERSE) -> float:
    """Liquidity-scaled PAPER notional: min(book cap, liquidity x fraction), in cents.

    The rule only shrinks a size the caller already allows; it never raises it.
    """
    liquidity_value, cap = _finite(liquidity), _finite(cap_usd)
    if liquidity_value is None or cap is None or liquidity_value <= 0 or cap <= 0:
        return 0.0
    size = min(cap, liquidity_value * params.liquidity_size_fraction)
    return max(0.0, math.floor(size * 100) / 100)


def fee_impact_roundtrip_pct(coin: dict, notional: float, *,
                             params: UniverseParameters = UNIVERSE) -> float | None:
    """Modeled fee + constant-product impact round trip, as a positive percentage.

    Uses the shared Lab spot model with zero slippage/latency buffers and zero
    network fee so the figure is exactly 'fee + impact'. Unknown inputs yield
    None; the caller treats None as not a candidate.
    """
    if _finite(notional) is None or notional <= 0:
        return None
    estimate = feasibility.modeled_roundtrip(
        coin, notional, base_slippage_bps=0.0, latency_buffer_bps=0.0, network_fee_sol=0.0)
    if estimate.get('status') != 'estimate':
        return None
    pnl = _finite(estimate.get('initial_pnl_pct'))
    return None if pnl is None else max(0.0, -pnl)


def rejections(coin: dict, *, cap_usd: float, minimum_notional_usd: float = 0.0,
               params: UniverseParameters = UNIVERSE) -> list[str]:
    """Every universe rule the observation fails, in evaluation order (empty = candidate)."""
    reasons: list[str] = []
    if str(coin.get('dexId') or '').lower() != params.dex_id:
        reasons.append('dex_not_pumpswap')
    if feasibility.quote_token_address(coin) != params.quote_token_address:
        reasons.append('quote_token_not_sol')
    elif feasibility.sol_usd_from_coin(coin) <= 0:
        reasons.append('network_price_unknown')
    liquidity = liquidity_usd(coin)
    if liquidity is None or liquidity <= 0:
        reasons.append('liquidity_unknown')
    elif liquidity < params.min_liquidity_usd:
        reasons.append('liquidity_below_minimum')
    if reasons:
        return reasons
    tier = fee_tier_bps(coin)
    if tier is None:
        return ['market_cap_unknown']
    if tier > params.max_fee_tier_bps:
        return ['fee_tier_above_maximum']
    size = size_for(liquidity, cap_usd, params=params)
    if size <= 0 or size < max(0.0, _finite(minimum_notional_usd) or 0.0):
        return ['size_below_minimum']
    roundtrip = fee_impact_roundtrip_pct(coin, size, params=params)
    if roundtrip is None:
        return ['cost_model_unavailable']
    if roundtrip > params.max_fee_impact_roundtrip_pct:
        return ['fee_impact_roundtrip_above_maximum']
    return []


def candidate(coin: dict, *, cap_usd: float, minimum_notional_usd: float = 0.0,
              params: UniverseParameters = UNIVERSE) -> bool:
    """True only when every universe rule holds. Never an entry authorization."""
    return not rejections(coin, cap_usd=cap_usd, minimum_notional_usd=minimum_notional_usd,
                          params=params)


def describe(coin: dict, *, cap_usd: float, minimum_notional_usd: float = 0.0,
             params: UniverseParameters = UNIVERSE) -> dict:
    """Transparent planning record for diagnostics and the entry ledger."""
    liquidity = liquidity_usd(coin)
    size = size_for(liquidity or 0.0, cap_usd, params=params)
    return {
        'universe_version': UNIVERSE_VERSION,
        'fee_tier_bps': fee_tier_bps(coin),
        'liquidity_usd': liquidity,
        'planned_notional_usd': size,
        'fee_impact_roundtrip_pct': fee_impact_roundtrip_pct(coin, size, params=params) if size > 0 else None,
        'rejections': rejections(coin, cap_usd=cap_usd,
                                 minimum_notional_usd=minimum_notional_usd, params=params),
        'is_execution_quote': False,
    }


def config() -> dict:
    """Published definition of the book pair; no outcome or promotion claim."""
    return {
        'version': VERSION,
        'entry_policy_version': ENTRY_POLICY_VERSION,
        'universe_version': UNIVERSE_VERSION,
        'books': {book_id: {'name': BOOK_NAMES[book_id], 'exits': asdict(EXITS[book_id]),
                            'stop_reason': EXITS[book_id].stop_reason,
                            'take_profit_reason': EXITS[book_id].take_profit_reason,
                            'max_hold_reason': EXITS[book_id].max_hold_reason}
                  for book_id in BOOK_IDS},
        'universe': asdict(UNIVERSE),
        'size_rule': 'min(book_entry_cap_usd, liquidity_usd * liquidity_size_fraction)',
        'starting_balance_usd': START_BALANCE_USD,
        'portfolio_group': PORTFOLIO_GROUP,
        'automatic_promotion': AUTOMATIC_PROMOTION,
        'requires_confirmed_exact_pool_flow': True,
        'requires_fresh_full_safety': True,
        'requires_exact_pool_price_identity': True,
        'lab_roundtrip_cost_cap_applies': True,
        'evidence_status': 'PROSPECTIVE_HYPOTHESIS_UNVALIDATED',
        'profitability_proven': False,
    }
