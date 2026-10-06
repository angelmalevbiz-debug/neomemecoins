"""Four previously profitable Lab entry rules routed into one PAPER account.

Historical Lab profits used modeled fills and do not establish expected future
returns. A match is a market signal; the main engine independently verifies
the exact pool, safety, executable quotes, costs and available account capital.
"""
from dataclasses import asdict
import math
import os
from typing import Any

import lab_activity

VERSION = 'WINNER_ENSEMBLE_PAPER_V1'
ENTRY_POLICY_VERSION = 'WINNER_ENSEMBLE_ENTRY_V1'
DEFAULT_STRATEGIES = ('EARLY', 'MOMENTUM', 'PRECISION', 'ULTRA_PRECISION')
_strategy_env = os.getenv('NEO_WINNER_STRATEGIES', '').strip()
if _strategy_env.upper() == 'ALL':
    STRATEGIES = tuple(lab_activity.RULES.keys())
elif _strategy_env:
    requested = tuple(part.strip().upper() for part in _strategy_env.split(',') if part.strip())
    unknown = [name for name in requested if name not in lab_activity.RULES]
    if unknown:
        raise ValueError(f'unknown PAPER strategies: {unknown}')
    STRATEGIES = requested or DEFAULT_STRATEGIES
else:
    STRATEGIES = DEFAULT_STRATEGIES
MIN_SCORE = min(lab_activity.RULES[name].score for name in STRATEGIES)
MIN_LIQUIDITY_USD = min(lab_activity.RULES[name].liquidity for name in STRATEGIES)


def matches(coin: dict[str, Any], flow: dict[str, Any] | None = None) -> list[str]:
    features = lab_activity.market_features(coin, flow)
    return [name for name in STRATEGIES if lab_activity.RULES[name].matches(features)]


def rule_config() -> dict[str, dict[str, Any]]:
    return {
        name: {key: (None if isinstance(value, float) and not math.isfinite(value) else value)
               for key, value in asdict(lab_activity.RULES[name]).items()}
        for name in STRATEGIES
    }
