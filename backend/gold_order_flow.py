"""High-frequency EARLY Order Flow entry predicate for PAPER trading.

User-directed mode: enter earlier and collect many more samples. Rug/price/
execution vetoes remain outside this module and are still mandatory.
"""
import math
from dataclasses import dataclass, asdict

SOURCE_COMMIT='EARLY_ORDER_FLOW_REPAIR_2026_10_05'
CORE_AST_SHA256='VERSIONED_REPAIR_MANIFEST'

@dataclass(frozen=True)
class EntryThresholds:
    min_score: float = 58.0
    min_liquidity: float = 4000.0
    min_conviction: float = 30.0

    def __post_init__(self):
        for name, value in asdict(self).items():
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError(f'{name} must be finite')
        if not 0 <= self.min_score <= 100 or not 0 <= self.min_conviction <= 100 or self.min_liquidity <= 0:
            raise ValueError('invalid entry thresholds')

    def as_dict(self):
        return asdict(self)

def _n(value, default=0.0):
    try:
        result = float(value)
        return result if math.isfinite(result) else default
    except (TypeError, ValueError):
        return default

def entry_mode(coin, flow, context, thresholds=None):
    thresholds = thresholds or EntryThresholds()
    score=_n(coin.get('score'))
    liquidity=_n(coin.get('liquidityUsd'))
    change_m5=_n((coin.get('priceChange') or {}).get('m5'))
    age=_n(coin.get('ageMinutes'),999999)
    conviction=_n(context.get('conviction'))
    trades=_n(flow.get('trades'))
    ratio=_n(flow.get('buy_sell_usd_ratio'))
    buy_usd=_n(flow.get('buy_usd'))
    sell_usd=_n(flow.get('sell_usd'))
    unique_wallets=_n(flow.get('unique_wallets'))
    max_sell=_n(flow.get('max_sell_usd'))

    if liquidity < thresholds.min_liquidity or unique_wallets < 1:
        return None
    if max_sell >= max(900.0, buy_usd * 1.25):
        return None

    # First real buying impulse. Small scouts are sized later by liquidity and
    # learned performance, so this gate can deliberately fire early.
    ultra_early=(
        age <= 45 and score >= thresholds.min_score and -8 <= change_m5 <= 25
        and trades >= 1 and ratio >= 1.05 and buy_usd >= 8
        and buy_usd >= sell_usd * 1.02 and conviction >= thresholds.min_conviction
    )
    if ultra_early:
        return 'ULTRA_EARLY'

    early=(
        age <= 180 and score >= thresholds.min_score + 12 and liquidity >= thresholds.min_liquidity + 1000
        and -8 <= change_m5 <= 30 and trades >= 2 and ratio >= 1.10
        and buy_usd >= 15 and buy_usd >= sell_usd * 1.05
        and conviction >= thresholds.min_conviction + 15
    )
    if early:
        return 'EARLY'

    # Still allow a strong flow setup if discovery saw it later.
    momentum=(
        score >= thresholds.min_score + 20 and liquidity >= thresholds.min_liquidity + 3500 and -5 <= change_m5 <= 30
        and trades >= 3 and ratio >= 1.20 and buy_usd >= 25
        and conviction >= thresholds.min_conviction + 25
    )
    if momentum:
        return 'MOMENTUM'

    return None

def qualifies(coin,flow,context,thresholds=None):
    try:
        return entry_mode(coin,flow,context,thresholds) is not None
    except (KeyError,TypeError,ValueError):
        return False
