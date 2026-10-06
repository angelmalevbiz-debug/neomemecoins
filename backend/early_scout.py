#!/usr/bin/env python3
"""Ultra-early PAPER momentum detector.

Detects *acceleration* before a large move using only information available at
that instant. It never bypasses rug, price, sell-route or execution-cost checks.
"""
from __future__ import annotations

import math
import os
import time
from typing import Any

VERSION = "EARLY_SCOUT_V1"
ENABLED = os.getenv("NEO_EARLY_SCOUT_ENABLED", "0").strip().lower() in {"1", "true", "yes", "on"}
MIN_MARKET_CAP = float(os.getenv("NEO_EARLY_SCOUT_MIN_MARKET_CAP", "5000"))
MAX_MARKET_CAP = float(os.getenv("NEO_EARLY_SCOUT_MAX_MARKET_CAP", "60000"))
MAX_AGE_MINUTES = float(os.getenv("NEO_EARLY_SCOUT_MAX_AGE_MINUTES", "45"))
MIN_SCORE = float(os.getenv("NEO_EARLY_SCOUT_MIN_SCORE", "0.72"))
MIN_CONFIRMATIONS = max(3, int(os.getenv("NEO_EARLY_SCOUT_MIN_CONFIRMATIONS", "5")))

if MIN_MARKET_CAP < 0 or MAX_MARKET_CAP <= MIN_MARKET_CAP or MAX_AGE_MINUTES <= 0:
    raise ValueError("invalid EARLY_SCOUT market band")
if not 0 < MIN_SCORE <= 1:
    raise ValueError("invalid EARLY_SCOUT score")


def _num(value: Any, default: float = 0.0) -> float:
    try:
        out = float(value)
        return out if math.isfinite(out) else default
    except Exception:
        return default


def _clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, value))


def _point_at_or_before(points: list[dict[str, Any]], stamp: int) -> dict[str, Any] | None:
    candidate = None
    for point in points:
        ts = int(point.get("ts") or 0)
        if ts <= stamp and (candidate is None or ts > int(candidate.get("ts") or 0)):
            candidate = point
    return candidate


def _return_pct(points: list[dict[str, Any]], now_ms: int, window_ms: int, latest_price: float) -> tuple[float | None, int | None]:
    point = _point_at_or_before(points, now_ms - window_ms)
    if not point:
        return None, None
    old = _num(point.get("price"))
    if old <= 0 or latest_price <= 0:
        return None, None
    return (latest_price / old - 1.0) * 100.0, int(point.get("ts") or 0)


def _growth(points: list[dict[str, Any]], now_ms: int, window_ms: int, field: str, latest: float) -> float | None:
    point = _point_at_or_before(points, now_ms - window_ms)
    if not point:
        return None
    old = _num(point.get(field))
    if old <= 0:
        return None
    return latest / old - 1.0


def evaluate(
    coin: dict[str, Any], points: list[dict[str, Any]], fast: dict[str, Any], slow: dict[str, Any], *,
    now_ms: int | None = None,
) -> dict[str, Any]:
    stamp = int(now_ms or time.time() * 1000)
    if not ENABLED:
        return {"version": VERSION, "enabled": False, "allow": False, "score": 0.0, "confirmations": 0, "vetoes": ["disabled"]}

    market_cap = _num(coin.get("marketCap") or coin.get("fdv"))
    liquidity = _num(coin.get("liquidityUsd"))
    age = _num(coin.get("ageMinutes"), 999999.0)
    price = _num(coin.get("priceUsd"))
    m5 = _num((coin.get("priceChange") or {}).get("m5"))

    clean_points = [p for p in points if 0 < int(p.get("ts") or 0) <= stamp and _num(p.get("price")) > 0]
    clean_points.sort(key=lambda p: int(p.get("ts") or 0))
    r9, _ = _return_pct(clean_points, stamp, 9_000, price)
    r30, _ = _return_pct(clean_points, stamp, 30_000, price)
    r90, _ = _return_pct(clean_points, stamp, 90_000, price)
    liq30 = _growth(clean_points, stamp, 30_000, "liquidity", liquidity)
    volume_h1 = _num((coin.get("volume") or {}).get("h1"))
    vol30 = _growth(clean_points, stamp, 30_000, "volumeH1", volume_h1)

    fast_trades = _num(fast.get("trades")); slow_trades = _num(slow.get("trades"))
    fast_buys = _num(fast.get("buys")); fast_sells = _num(fast.get("sells"))
    fast_ratio = _num(fast.get("buy_sell_usd_ratio"), fast_buys / max(1.0, fast_sells))
    slow_ratio = _num(slow.get("buy_sell_usd_ratio"), 1.0)
    fast_rate = fast_trades / max(1.0, _num(fast.get("seconds"), 30.0))
    slow_rate = slow_trades / max(1.0, _num(slow.get("seconds"), 300.0))
    tape_acceleration = fast_rate / max(0.01, slow_rate)
    buyers = _num(fast.get("buyer_wallets"))
    unique = _num(fast.get("unique_wallets"))
    repeat_buyers = _num(fast.get("repeat_buy_wallets"))
    whale_buy = _num(fast.get("whale_buy_usd")); whale_sell = _num(fast.get("whale_sell_usd"))

    # Price acceleration: reward positive early movement, but not an already vertical candle.
    short_impulse = 0.35 if r9 is None else _clamp((r9 - 0.10) / 2.4)
    mid_impulse = 0.35 if r30 is None else _clamp((r30 - 0.20) / 5.0)
    if r9 is not None and r30 is not None:
        short_rate = r9 / 9.0
        mid_rate = r30 / 30.0
        price_accel = _clamp((short_rate - mid_rate + 0.02) / 0.25)
    else:
        price_accel = 0.35

    flow_ratio_score = _clamp((fast_ratio - 1.05) / 1.8)
    flow_accel_score = _clamp((tape_acceleration - 1.05) / 2.5)
    wallet_burst = _clamp((buyers - 1.0) / 6.0) * 0.65 + _clamp(repeat_buyers / 3.0) * 0.35
    liquidity_health = 0.55 if liq30 is None else _clamp((liq30 + 0.08) / 0.20)
    volume_accel = 0.45 if vol30 is None else _clamp((vol30 + 0.01) / 0.12)
    low_cap_early = _clamp((MAX_MARKET_CAP - market_cap) / max(MAX_MARKET_CAP - MIN_MARKET_CAP, 1.0))
    tape_quality = 1.0 if str(fast.get("quality") or "").upper() == "COMPLETE" else (0.55 if fast_trades > 0 else 0.20)

    components = {
        "short_impulse": short_impulse,
        "mid_impulse": mid_impulse,
        "price_acceleration": price_accel,
        "flow_ratio": flow_ratio_score,
        "flow_acceleration": flow_accel_score,
        "wallet_burst": wallet_burst,
        "liquidity_health": liquidity_health,
        "volume_acceleration": volume_accel,
        "low_cap_early": low_cap_early,
        "tape_quality": tape_quality,
    }
    weights = {
        "short_impulse": 0.10, "mid_impulse": 0.07, "price_acceleration": 0.16,
        "flow_ratio": 0.15, "flow_acceleration": 0.15, "wallet_burst": 0.12,
        "liquidity_health": 0.08, "volume_acceleration": 0.07,
        "low_cap_early": 0.05, "tape_quality": 0.05,
    }
    score = sum(components[k] * weights[k] for k in weights)

    confirmation_flags = {
        "price_turning_up": (r9 is not None and r9 >= 0.15) or (r30 is not None and r30 >= 0.35),
        "price_accelerating": price_accel >= 0.55,
        "buy_flow_dominant": fast_ratio >= 1.35 and fast_buys >= 2,
        "trade_rate_accelerating": tape_acceleration >= 1.45 and fast_trades >= 3,
        "wallet_burst": buyers >= 3 and unique >= 3,
        "repeat_buyers": repeat_buyers >= 1,
        "liquidity_stable": liq30 is None or liq30 >= -0.08,
        "volume_rising": vol30 is None or vol30 >= 0.0,
    }
    confirmations = sum(bool(v) for v in confirmation_flags.values())

    vetoes = []
    if not (MIN_MARKET_CAP <= market_cap <= MAX_MARKET_CAP): vetoes.append("market_cap_outside_early_band")
    if age > MAX_AGE_MINUTES: vetoes.append("too_old_for_early_scout")
    if price <= 0 or liquidity <= 0: vetoes.append("missing_market_data")
    if len(clean_points) < 3: vetoes.append("insufficient_micro_history")
    if m5 > 25 or (r9 is not None and r9 > 8) or (r30 is not None and r30 > 15): vetoes.append("already_overextended")
    if fast_trades >= 3 and fast_ratio < 0.80: vetoes.append("sell_flow_dominant")
    if whale_sell >= max(750.0, whale_buy * 1.4): vetoes.append("whale_sell_pressure")
    if liq30 is not None and liq30 < -0.20: vetoes.append("liquidity_collapsing")
    if confirmations < MIN_CONFIRMATIONS: vetoes.append("insufficient_early_confirmations")
    if score < MIN_SCORE: vetoes.append("early_score_below_threshold")

    return {
        "version": VERSION, "enabled": True, "allow": not vetoes,
        "score": round(score, 4), "min_score": MIN_SCORE,
        "confirmations": confirmations, "min_confirmations": MIN_CONFIRMATIONS,
        "confirmation_flags": confirmation_flags,
        "components": {k: round(v, 4) for k, v in components.items()},
        "features": {
            "market_cap_usd": round(market_cap, 2), "liquidity_usd": round(liquidity, 2),
            "age_minutes": round(age, 2), "m5_pct": round(m5, 3),
            "return_9s_pct": None if r9 is None else round(r9, 4),
            "return_30s_pct": None if r30 is None else round(r30, 4),
            "return_90s_pct": None if r90 is None else round(r90, 4),
            "liquidity_growth_30s": None if liq30 is None else round(liq30, 4),
            "volume_h1_growth_30s": None if vol30 is None else round(vol30, 4),
            "fast_buy_sell_ratio": round(fast_ratio, 4), "slow_buy_sell_ratio": round(slow_ratio, 4),
            "tape_acceleration": round(tape_acceleration, 4), "fast_trades": int(fast_trades),
            "buyer_wallets": int(buyers), "unique_wallets": int(unique),
            "repeat_buy_wallets": int(repeat_buyers),
        },
        "vetoes": vetoes,
    }
