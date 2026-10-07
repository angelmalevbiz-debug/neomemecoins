"""Adaptive high-frequency momentum research for the isolated PAPER Strategy Lab.

This module never builds, signs, or sends a transaction. It ranks early momentum
setups using only information available at decision time and shrinks uncertainty
back toward neutral until enough PAPER outcomes exist.
"""
from collections import deque
import math
from typing import Any

VERSION = 'MOMENTUM_RUSH_BRAIN_V1'
TARGET_WIN_RATE_PCT = 89.0  # research target only; never reported as achieved
MAX_SAMPLES_PER_PAIR = 24
_SAMPLE_BY_PAIR: dict[tuple[str, str], deque] = {}


def number(value: Any, default: float = 0.0) -> float:
    try:
        out = float(value)
        return out if math.isfinite(out) else default
    except (TypeError, ValueError, OverflowError):
        return default


def clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, number(value)))


def _market_cap(coin: dict) -> float:
    return max(0.0, number(coin.get('marketCap') or coin.get('fdv')))


def _base_intelligence(coin: dict, features: dict) -> dict:
    flow = features.get('flow') or {}
    score = number(features.get('score'))
    liq = number(features.get('liq'))
    m5 = number(features.get('m5'))
    h1 = number(features.get('h1'))
    bs = number(features.get('bs'))
    lmc = number(features.get('lmc'))
    age = number(features.get('age'), 999999)
    vol_liq = number(features.get('vol_liq'))
    market_cap = _market_cap(coin)
    trades = number(flow.get('trades'))
    ratio = number(flow.get('ratio'))
    buy_usd = number(flow.get('buy_usd'))
    sell_usd = number(flow.get('sell_usd'))
    wallets = number(flow.get('unique_wallets'))
    max_sell = number(flow.get('max_sell'))

    hard_market_gate = (
        market_cap > 0 and liq >= 10_000 and 1 <= age <= 360
        and -3 <= m5 <= 45 and h1 >= -25 and lmc >= .04
    )
    verified_flow_bearish = trades >= 3 and (ratio < .72 or sell_usd > max(150.0, buy_usd * 1.35))
    dump_risk = trades >= 2 and max_sell > max(1_200.0, buy_usd * 1.35)
    low_cap = market_cap <= 50_000
    low_cap_guard = (not low_cap) or (
        lmc >= .15 and bs >= 1.0 and (trades < 2 or ratio >= 1.0)
    )

    quality = clamp((score - 70) / 30)
    move = clamp((m5 + 2) / 18)
    if m5 > 30:
        move *= clamp((45 - m5) / 15)
    hour = clamp((h1 + 15) / 120)
    pressure = .55 * clamp((bs - .8) / 1.8) + .45 * clamp((ratio - .7) / 2.3) if trades else clamp((bs - .8) / 1.8)
    flow_strength = 0.5 if trades == 0 else (
        .30 * clamp(trades / 8)
        + .30 * clamp((ratio - .7) / 2.3)
        + .20 * clamp(buy_usd / 750)
        + .20 * clamp(wallets / 7)
    )
    volume = clamp((vol_liq - .04) / 2.5)
    liquidity_quality = .55 * clamp(liq / 60_000) + .45 * clamp((lmc - .04) / .25)
    early = clamp((180 - age) / 180)

    raw = (
        .18 * quality + .17 * move + .10 * hour + .17 * pressure
        + .13 * flow_strength + .08 * volume + .09 * liquidity_quality + .08 * early
    )
    if low_cap and low_cap_guard and pressure >= .55:
        raw += .04
    if age <= 45 and flow_strength >= .55:
        raw += .03
    if verified_flow_bearish:
        raw -= .28
    if dump_risk:
        raw -= .22
    if h1 > 260:
        raw -= .08

    base_score = clamp(raw)
    allow = hard_market_gate and low_cap_guard and not verified_flow_bearish and not dump_risk and base_score >= .47
    return {
        'version': VERSION,
        'allow': bool(allow),
        'base_score': round(base_score, 4),
        'market_cap_usd': round(market_cap, 2),
        'low_cap': bool(low_cap),
        'low_cap_guard': bool(low_cap_guard),
        'verified_flow_bearish': bool(verified_flow_bearish),
        'dump_risk': bool(dump_risk),
        'components': {
            'quality': round(quality, 4), 'move': round(move, 4),
            'hour': round(hour, 4), 'pressure': round(pressure, 4),
            'flow': round(flow_strength, 4), 'volume': round(volume, 4),
            'liquidity': round(liquidity_quality, 4), 'early': round(early, 4),
        },
    }


def _temporal_intelligence(coin: dict, features: dict, now: int) -> dict:
    key = (str(coin.get('address') or ''), str(coin.get('pairAddress') or ''))
    ring = _SAMPLE_BY_PAIR.setdefault(key, deque(maxlen=MAX_SAMPLES_PER_PAIR))
    flow = features.get('flow') or {}
    current = {
        'ts': int(now), 'price': number(coin.get('priceUsd')),
        'liq': number(features.get('liq')), 'buy_usd': number(flow.get('buy_usd')),
        'wallets': number(flow.get('unique_wallets')),
    }
    reference = None
    for sample in ring:
        if now - int(sample['ts']) >= 8_000:
            reference = sample
    if reference is None and ring:
        reference = ring[0]
    ring.append(current)
    if not reference or number(reference.get('price')) <= 0:
        return {'samples': len(ring), 'score': .5, 'price_velocity_pct': 0.0,
                'liquidity_velocity_pct': 0.0, 'buy_flow_delta_usd': 0.0,
                'wallet_delta': 0.0, 'veto': False}

    price_velocity = (current['price'] / max(number(reference.get('price')), 1e-18) - 1) * 100
    liq_velocity = (current['liq'] / max(number(reference.get('liq')), 1e-18) - 1) * 100
    buy_delta = current['buy_usd'] - number(reference.get('buy_usd'))
    wallet_delta = current['wallets'] - number(reference.get('wallets'))
    score = (
        .45 * clamp((price_velocity + .35) / 2.5)
        + .22 * clamp((liq_velocity + 2.0) / 12.0)
        + .20 * clamp((buy_delta + 25.0) / 300.0)
        + .13 * clamp((wallet_delta + .5) / 4.0)
    )
    veto = price_velocity <= -1.1 or liq_velocity <= -14
    return {
        'samples': len(ring), 'score': round(clamp(score), 4),
        'price_velocity_pct': round(price_velocity, 4),
        'liquidity_velocity_pct': round(liq_velocity, 4),
        'buy_flow_delta_usd': round(buy_delta, 2),
        'wallet_delta': round(wallet_delta, 2), 'veto': bool(veto),
    }


def _memory_intelligence(book: dict, features: dict, now: int) -> dict:
    rows = []
    for trade in sorted(book.get('history') or [], key=lambda t: number(t.get('closed_at')), reverse=True):
        prior = trade.get('entry_features')
        closed = int(number(trade.get('closed_at')))
        if not isinstance(prior, dict) or closed <= 0 or closed > now:
            continue
        rows.append(trade)
        if len(rows) >= 80:
            break
    scales = {'m5': 18.0, 'h1': 120.0, 'bs': 1.2, 'lmc': .18,
              'vol_liq': 3.0, 'age': 240.0, 'score': 20.0}
    weighted = weighted_wins = weighted_pnl = 0.0
    for trade in rows:
        prior = trade['entry_features']; distance = 0.0; used = 0
        for key, scale in scales.items():
            if key not in prior:
                continue
            distance += min(2.0, abs(number(features.get(key)) - number(prior.get(key))) / scale)
            used += 1
        if not used:
            continue
        weight = math.exp(-1.25 * (distance / used))
        if weight < .08:
            continue
        pnl = number(trade.get('pnl_usd'))
        weighted += weight
        weighted_wins += weight * (1.0 if pnl > 0 else 0.0)
        weighted_pnl += weight * pnl / max(number(trade.get('notional_usd'), 100.0), 1e-9) * 100
    if weighted < 4.0:
        return {'score': .5, 'effective_sample': round(weighted, 3), 'posterior_win_rate': .5,
                'avg_similar_pnl_pct': 0.0}
    posterior = (2.0 + weighted_wins) / (4.0 + weighted)
    avg_pnl = weighted_pnl / weighted
    pnl_signal = clamp(.5 + avg_pnl / 18.0)
    raw = .72 * posterior + .28 * pnl_signal
    reliability = clamp(weighted / 18.0)
    score = .5 + (raw - .5) * reliability
    return {'score': round(clamp(score), 4), 'effective_sample': round(weighted, 3),
            'posterior_win_rate': round(posterior, 4), 'avg_similar_pnl_pct': round(avg_pnl, 4)}


def evaluate(book: dict, coin: dict, features: dict, now: int) -> dict:
    base = _base_intelligence(coin, features)
    temporal = _temporal_intelligence(coin, features, now)
    memory = _memory_intelligence(book, features, now)
    final = .65 * number(base.get('base_score')) + .22 * number(temporal.get('score'), .5) + .13 * number(memory.get('score'), .5)
    components = base.get('components') or {}
    votes = sum([
        number(components.get('quality')) >= .35,
        number(components.get('move')) >= .25,
        number(components.get('pressure')) >= .40,
        number(components.get('liquidity')) >= .30,
        number(components.get('early')) >= .20,
        number(temporal.get('score'), .5) >= .45,
        number(memory.get('score'), .5) >= .42,
    ])
    allow = bool(base.get('allow')) and not temporal.get('veto') and votes >= 4 and final >= .50
    return {**base, 'allow': allow, 'final_score': round(clamp(final), 4),
            'votes': int(votes), 'votes_required': 4, 'temporal': temporal, 'memory': memory,
            'target_win_rate_pct': TARGET_WIN_RATE_PCT, 'target_is_guarantee': False}


def candidate_notional_limit(meta: dict, balance: float, requested: float) -> float:
    """Keep low-cap PAPER experiments frequent without pretending they are low risk."""
    balance = max(0.0, number(balance)); requested = max(0.0, number(requested))
    market_cap = number(meta.get('market_cap_usd'))
    final = number(meta.get('final_score'))
    if market_cap <= 50_000:
        fraction, absolute = .12, 60.0
    elif market_cap <= 100_000:
        fraction, absolute = .20, 100.0
    else:
        fraction, absolute = .30, requested
    if final >= .72:
        fraction = min(.35, fraction + .05)
    return max(0.0, min(requested, absolute, balance * fraction))
