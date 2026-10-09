"""Prospective market branches for the four funded PAPER Lab strategies.

These screens select candidates only. Exact-pool confirmed flow, full fresh
safety, independent prices and the funded cost limit still authorize admission
in the caller. No branch establishes a win rate or alters existing outcomes.
"""
import math
from dataclasses import asdict
from typing import Any

import lab_activity as activity
import funded_active_paper as active_paper


VERSION = 'FUNDED_MARKET_BRANCHES_V1'
FUNDED_STRATEGIES = ('EARLY', 'MOMENTUM', 'PRECISION', 'ULTRA_PRECISION')
SOL_QUOTE_MINT = 'So11111111111111111111111111111111111111112'

# Established pools use physical market conditions rather than the headline
# score, which includes discovery and young-pool bonuses. The fixed envelopes
# are independent of a particular token or an account's historical outcomes.
ESTABLISHED_BRANCHES = {
    'MOMENTUM': {
        'id': 'ESTABLISHED_LIQUID_MOMENTUM',
        'constraints': {
            'liquidity': 200_000.0,
            'age': (1440.0, 10_000_000.0),
            'move': (.5, 15.0),
            'hour': (0.0, 40.0),
            'buy_sell': 1.2,
            'liquidity_cap': .015,
            'volume_liquidity': (.03, 3.0),
            'dex_id': 'pumpswap',
            'quote_token_address': SOL_QUOTE_MINT,
            'requires_finite_raw_fields': True,
        },
        'score_is_admission_gate': False,
    },
    'PRECISION': {
        'id': 'ESTABLISHED_LIQUID_PRECISION',
        'constraints': {
            'liquidity': 300_000.0,
            'age': (1440.0, 10_000_000.0),
            'move': (.5, 8.0),
            'hour': (0.0, 20.0),
            'buy_sell': 1.5,
            'liquidity_cap': .015,
            'volume_liquidity': (.03, 1.5),
            'dex_id': 'pumpswap',
            'quote_token_address': SOL_QUOTE_MINT,
            'requires_finite_raw_fields': True,
        },
        'score_is_admission_gate': False,
    },
}


def _finite(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return result if math.isfinite(result) else None


def _raw_dict(coin: dict, name: str) -> dict:
    value = coin.get(name)
    return value if isinstance(value, dict) else {}


def _established_matches(coin: dict, constraints: dict) -> bool:
    if str(coin.get('dexId') or '').lower() != constraints['dex_id']:
        return False
    # Require the normalized denomination supplied by the market feed. A raw
    # quote identity, when supplied too, must agree rather than replace it.
    quote = coin.get('quoteTokenAddress')
    raw_quote = coin.get('quoteToken')
    if (quote != constraints['quote_token_address']
            or (raw_quote is not None and not isinstance(raw_quote, dict))
            or (isinstance(raw_quote, dict) and raw_quote.get('address') is not None
                and raw_quote['address'] != quote)):
        return False

    changes = _raw_dict(coin, 'priceChange')
    txns = _raw_dict(coin, 'txns').get('m5')
    txns = txns if isinstance(txns, dict) else {}
    volume = _raw_dict(coin, 'volume')
    # FDV is an explicit feed fallback when marketCap is absent. Invalid or
    # non-positive marketCap cannot silently turn into a plausible FDV value.
    cap_value = coin.get('marketCap')
    if cap_value is None:
        cap_value = coin.get('fdv')
    values = [_finite(value) for value in (
        coin.get('liquidityUsd'), cap_value, coin.get('ageMinutes'),
        changes.get('m5'), changes.get('h1'), volume.get('h1'),
        txns.get('buys'), txns.get('sells'),
    )]
    if any(value is None for value in values):
        return False
    liquidity, cap, age, move, hour, turnover, buys, sells = values
    if (liquidity <= 0 or cap <= 0 or turnover < 0 or buys < 0 or sells < 0
            or buys != int(buys) or sells != int(sells)):
        return False

    return (
        liquidity >= constraints['liquidity']
        and constraints['age'][0] <= age <= constraints['age'][1]
        and constraints['move'][0] <= move <= constraints['move'][1]
        and constraints['hour'][0] <= hour <= constraints['hour'][1]
        and buys / max(sells, 1.0) >= constraints['buy_sell']
        and liquidity / cap >= constraints['liquidity_cap']
        and constraints['volume_liquidity'][0] <= turnover / liquidity
        <= constraints['volume_liquidity'][1]
    )


def matched_branches(strategy_id: str, coin: dict, features: dict,
                     require_flow: bool = True) -> list[str]:
    """Return matching branch IDs; this function never admits a PAPER entry.

    The original activity rules are used unchanged, including their existing
    optional flow screen. Established branches are physical market screens;
    their evidence and execution gates must be applied by the funded caller.
    """
    if strategy_id not in FUNDED_STRATEGIES:
        return []
    if active_paper.enabled():
        return [active_paper.reporting_version() + '_' + strategy_id] if active_paper.matches(strategy_id, coin) else []
    matches = []
    if activity.RULES[strategy_id].matches(features, require_flow=require_flow):
        matches.append(strategy_id)
    branch = ESTABLISHED_BRANCHES.get(strategy_id)
    if branch and _established_matches(coin, branch['constraints']):
        matches.append(branch['id'])
    return matches


def _json_rule(strategy_id: str) -> dict:
    return {
        field: (None if isinstance(value, float) and not math.isfinite(value) else value)
        for field, value in asdict(activity.RULES[strategy_id]).items()
    }


def candidate_config() -> dict[str, dict]:
    """Publish all branches while preserving the legacy top-level rule fields."""
    if active_paper.enabled():
        return active_paper.candidate_config()
    configs = {}
    for strategy_id in FUNDED_STRATEGIES:
        original = _json_rule(strategy_id)
        branches = [{'id': strategy_id, 'constraints': original.copy(),
                     'score_is_admission_gate': True}]
        established = ESTABLISHED_BRANCHES.get(strategy_id)
        if established:
            branches.append({**established, 'constraints': established['constraints'].copy()})
        configs[strategy_id] = {**original, 'candidate_branches': branches}
    return configs
