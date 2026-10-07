"""Adaptive high-frequency momentum research for the isolated PAPER Strategy Lab.

This module never builds, signs, or sends a transaction. It ranks early momentum
setups using only information available at decision time and shrinks uncertainty
back toward neutral until enough PAPER outcomes exist.
"""
from collections import OrderedDict, deque
import heapq
from itertools import islice
import math
from typing import Any

STRATEGY_ID = 'MOMENTUM_RUSH_BRAIN'
VERSION = 'MOMENTUM_RUSH_BRAIN_V2_CAUSAL'
TARGET_WIN_RATE_PCT = 80.0  # research target only; never reported as achieved
MAX_SAMPLES_PER_PAIR = 24
MAX_TRACKED_PAIRS = 256
SAMPLE_RETENTION_MS = 120_000
MAX_OBSERVATION_AGE_MS = 20_000
MIN_OBSERVATION_SPAN_MS = 8_000
MAX_MEMORY_TRADES = 80
MAX_MEMORY_SCAN_ROWS = 256
LOW_CAP_MAX_BALANCE_FRACTION = .12
LOW_CAP_MAX_NOTIONAL_USD = 60.0
_SAMPLE_BY_PAIR: OrderedDict[tuple[str, str], deque] = OrderedDict()


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


def _neutral_temporal(samples=0, reason='temporal_warmup') -> dict:
    return {'samples': samples, 'score': .5, 'ready': False, 'reason': reason,
            'price_velocity_pct': 0.0, 'price_velocity_pct_per_min': 0.0,
            'price_acceleration_pct_per_min2': 0.0, 'acceleration_confirmed': False,
            'liquidity_velocity_pct': 0.0, 'buy_flow_delta_usd': 0.0,
            'wallet_delta': 0.0, 'veto': False}


def observe(coin: dict, features: dict, now: int) -> dict:
    """Observe a distinct available snapshot of this exact pool, once.

    Polling the same snapshot cannot manufacture momentum. Three snapshots,
    separated by at least eight seconds, are required to measure acceleration.
    This bounded, ephemeral market memory is separate from the trade ledger.
    """
    now = int(now)
    key = (str(coin.get('address') or ''), str(coin.get('pairAddress') or ''))
    stamp = int(number(coin.get('updatedAt')))
    if (not all(key) or stamp <= 0 or not 0 <= now - stamp <= MAX_OBSERVATION_AGE_MS
            or number(coin.get('priceUsd')) <= 0 or number(features.get('liq')) <= 0):
        return _neutral_temporal(reason='unavailable_or_noncausal_snapshot')
    for stale_key, samples in list(_SAMPLE_BY_PAIR.items()):
        if samples and now - samples[-1]['observed_at'] > SAMPLE_RETENTION_MS:
            _SAMPLE_BY_PAIR.pop(stale_key, None)
    ring = _SAMPLE_BY_PAIR.get(key)
    if ring is None:
        ring = deque(maxlen=MAX_SAMPLES_PER_PAIR)
        _SAMPLE_BY_PAIR[key] = ring
    if ring and (stamp < ring[-1]['ts'] or now < ring[-1]['observed_at']):
        return _neutral_temporal(reason='snapshot_timestamp_regression')
    _SAMPLE_BY_PAIR.move_to_end(key)
    while len(_SAMPLE_BY_PAIR) > MAX_TRACKED_PAIRS:
        _SAMPLE_BY_PAIR.popitem(last=False)
    flow = features.get('flow') or {}
    current = {
        'ts': stamp, 'observed_at': now, 'price': number(coin.get('priceUsd')),
        'liq': number(features.get('liq')), 'buy_usd': number(flow.get('buy_usd')),
        'wallets': number(flow.get('unique_wallets')),
    }
    if not ring or stamp > ring[-1]['ts']:
        ring.append(current)
    else:
        # A repeat with changed fields remains the originally observed snapshot.
        current = ring[-1]
    samples = [s for s in ring if s['observed_at'] <= now
               and 0 <= now - s['ts'] <= SAMPLE_RETENTION_MS]
    reference = next((s for s in reversed(samples)
                      if current['ts'] - s['ts'] >= MIN_OBSERVATION_SPAN_MS), None)
    previous = next((s for s in reversed(samples)
                     if reference and reference['ts'] - s['ts'] >= MIN_OBSERVATION_SPAN_MS), None)
    if not reference or not previous:
        return _neutral_temporal(len(samples))

    price_velocity = (current['price'] / max(number(reference.get('price')), 1e-18) - 1) * 100
    span_minutes = (current['ts'] - reference['ts']) / 60_000
    previous_span = (reference['ts'] - previous['ts']) / 60_000
    rate = price_velocity / span_minutes
    previous_rate = (reference['price'] / previous['price'] - 1) * 100 / previous_span
    acceleration = (rate - previous_rate) / ((span_minutes + previous_span) / 2)
    liq_velocity = (current['liq'] / max(number(reference.get('liq')), 1e-18) - 1) * 100
    buy_delta = current['buy_usd'] - number(reference.get('buy_usd'))
    wallet_delta = current['wallets'] - number(reference.get('wallets'))
    score = (
        .35 * clamp((rate + .5) / 12.0)
        + .20 * clamp(.5 + acceleration / 100.0)
        + .20 * clamp((liq_velocity + 2.0) / 12.0)
        + .15 * clamp((buy_delta + 25.0) / 300.0)
        + .10 * clamp((wallet_delta + .5) / 4.0)
    )
    veto = price_velocity <= -1.1 or liq_velocity <= -14
    return {
        'samples': len(samples), 'score': round(clamp(score), 4), 'ready': True,
        'reason': '', 'observation_span_ms': current['ts'] - reference['ts'],
        'price_velocity_pct_per_min': round(rate, 4),
        'price_acceleration_pct_per_min2': round(acceleration, 4),
        'acceleration_confirmed': rate > 0 and acceleration >= 0,
        'price_velocity_pct': round(price_velocity, 4),
        'liquidity_velocity_pct': round(liq_velocity, 4),
        'buy_flow_delta_usd': round(buy_delta, 2),
        'wallet_delta': round(wallet_delta, 2), 'veto': bool(veto),
    }


def _memory_intelligence(book: dict, features: dict, now: int) -> dict:
    neutral = {'score': .5, 'effective_sample': 0.0, 'posterior_win_rate': .5,
               'avg_similar_pnl_pct': 0.0, 'closed_samples': 0,
               'basis': 'OWN_CLOSED_NET_PAPER_TRADES', 'is_win_rate_estimate': True}
    if book.get('id') != STRATEGY_ID:
        return neutral

    def closed_net_trades():
        # Lab histories are newest first. Bound work per candidate while retaining
        # the complete ledger; older rows do not participate in this decision.
        for trade in islice(book.get('history') or [], MAX_MEMORY_SCAN_ROWS):
            if not isinstance(trade, dict):
                continue
            closed = number(trade.get('closed_at'))
            opened = number(trade.get('opened_at'))
            net = number(trade.get('net_pnl_usd', trade.get('pnl_usd')), math.nan)
            if (trade.get('strategy_id') != STRATEGY_ID
                    or not isinstance(trade.get('entry_features'), dict)
                    or not 0 < opened < closed <= now or number(trade.get('notional_usd')) <= 0
                    or not math.isfinite(net) or trade.get('is_partial') is True
                    or str(trade.get('exit_reason') or '').startswith('PARTIAL')):
                continue
            yield trade

    latest = heapq.nlargest(MAX_MEMORY_TRADES, closed_net_trades(), key=lambda t: number(t.get('closed_at')))
    rows = []; seen = set()
    for trade in latest:
        identity = (trade.get('trade_no'), trade.get('address'), trade.get('pairAddress'), trade.get('opened_at'))
        if identity not in seen:
            seen.add(identity); rows.append(trade)
    scales = {'m5': 18.0, 'h1': 120.0, 'bs': 1.2, 'lmc': .18,
              'vol_liq': 3.0, 'age': 240.0, 'score': 20.0}
    weighted = weighted_wins = weighted_pnl = 0.0
    for trade in rows:
        prior = trade['entry_features']; distance = 0.0; used = 0
        for key, scale in scales.items():
            if not math.isfinite(number(prior.get(key), math.nan)) or not math.isfinite(number(features.get(key), math.nan)):
                continue
            distance += min(2.0, abs(number(features.get(key)) - number(prior.get(key))) / scale)
            used += 1
        if used < 3:
            continue
        weight = math.exp(-1.25 * (distance / used))
        if weight < .08:
            continue
        # The Lab's pnl_usd is already the final net total, including its partial
        # realized legs. Never add partial_exits a second time or learn gross PnL.
        pnl = number(trade.get('net_pnl_usd', trade.get('pnl_usd')))
        weighted += weight
        weighted_wins += weight * (1.0 if pnl > 0 else 0.0)
        weighted_pnl += weight * pnl / number(trade.get('notional_usd')) * 100
    if weighted < 4.0:
        return {**neutral, 'effective_sample': round(weighted, 3), 'closed_samples': len(rows)}
    posterior = (2.0 + weighted_wins) / (4.0 + weighted)
    avg_pnl = weighted_pnl / weighted
    pnl_signal = clamp(.5 + avg_pnl / 18.0)
    raw = .72 * posterior + .28 * pnl_signal
    reliability = clamp(weighted / 18.0)
    score = .5 + (raw - .5) * reliability
    return {**neutral, 'score': round(clamp(score), 4), 'effective_sample': round(weighted, 3),
            'closed_samples': len(rows),
            'posterior_win_rate': round(posterior, 4), 'avg_similar_pnl_pct': round(avg_pnl, 4)}


def evaluate(book: dict, coin: dict, features: dict, now: int, *, temporal: dict | None = None) -> dict:
    base = _base_intelligence(coin, features)
    temporal = observe(coin, features, now) if temporal is None else temporal
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
    allow = (book.get('id') == STRATEGY_ID and bool(base.get('allow'))
             and temporal.get('ready') is True and temporal.get('acceleration_confirmed') is True
             and not temporal.get('veto') and votes >= 4 and final >= .50)
    return {**base, 'allow': allow, 'final_score': round(clamp(final), 4),
            'votes': int(votes), 'votes_required': 4, 'temporal': temporal, 'memory': memory,
            'target_win_rate_pct': TARGET_WIN_RATE_PCT, 'target_is_guarantee': False}


def candidate_notional_limit(meta: dict, balance: float, requested: float) -> float:
    """Keep low-cap PAPER experiments frequent without pretending they are low risk."""
    balance = max(0.0, number(balance)); requested = max(0.0, number(requested))
    market_cap = number(meta.get('market_cap_usd'))
    final = number(meta.get('final_score'))
    if market_cap <= 50_000:
        fraction, absolute = LOW_CAP_MAX_BALANCE_FRACTION, LOW_CAP_MAX_NOTIONAL_USD
    elif market_cap <= 100_000:
        fraction, absolute = .20, 100.0
    else:
        fraction, absolute = .30, requested
    if market_cap > 100_000 and final >= .72:
        fraction = min(.35, fraction + .05)
    return max(0.0, min(requested, absolute, balance * fraction))
