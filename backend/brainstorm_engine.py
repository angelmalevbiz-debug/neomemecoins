#!/usr/bin/env python3
"""Adaptive PAPER-only meta decision layer.

This module does not place orders. It combines independent evidence streams into
an auditable confidence score and veto decision. The score is a ranking signal,
not a guaranteed probability of profit.
"""
from __future__ import annotations

import json
import math
import os
import threading
import time
from pathlib import Path
from typing import Any

VERSION = "BRAINSTORM_META_V1"
ENABLED = os.getenv("NEO_BRAINSTORM_ENABLED", "0").strip().lower() in {"1", "true", "yes", "on"}
TARGET_WIN_RATE = float(os.getenv("NEO_BRAINSTORM_TARGET_WIN_RATE", "0.70"))
MIN_CONFIDENCE = float(os.getenv("NEO_BRAINSTORM_MIN_CONFIDENCE", "0.70"))
MIN_MATCHES = max(1, int(os.getenv("NEO_BRAINSTORM_MIN_MATCHES", "3")))
MIN_FAMILIES = max(1, int(os.getenv("NEO_BRAINSTORM_MIN_FAMILIES", "2")))
HISTORY_WINDOW = max(20, int(os.getenv("NEO_BRAINSTORM_HISTORY_WINDOW", "200")))
LAB_PATH = Path(os.getenv("NEO_STRATEGY_LAB_PATH", "/var/lib/neo-market/strategy_lab.json"))

if not 0.0 < TARGET_WIN_RATE < 1.0 or not 0.0 < MIN_CONFIDENCE <= 1.0:
    raise ValueError("invalid brainstorm configuration")

_LAB_CACHE: dict[str, Any] = {"mtime": None, "loaded_at": 0.0, "stats": {}}
_LAB_LOCK = threading.Lock()


def _num(value: Any, default: float = 0.0) -> float:
    try:
        out = float(value)
        return out if math.isfinite(out) else default
    except Exception:
        return default


def _clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, value))


def _family(name: str) -> str:
    n = name.upper()
    if any(k in n for k in ("FLOW", "ORDER", "BUY_PRESSURE", "SELL_WALL")):
        return "flow"
    if any(k in n for k in ("MOMENTUM", "BREAKOUT", "TREND", "VOLUME")):
        return "momentum"
    if any(k in n for k in ("LIQ", "MATURE", "YOUNG")):
        return "liquidity"
    if any(k in n for k in ("REVERSAL", "PULLBACK", "SECOND_WAVE")):
        return "reversal"
    if any(k in n for k in ("PRECISION", "EARLY", "SCALPER", "CONFLUENCE", "CLEAN")):
        return "precision"
    return "other"


def _lab_stats() -> dict[str, dict[str, float]]:
    now = time.time()
    try:
        mtime = LAB_PATH.stat().st_mtime
    except Exception:
        return {}
    with _LAB_LOCK:
        if _LAB_CACHE["mtime"] == mtime and now - _LAB_CACHE["loaded_at"] < 5.0:
            return _LAB_CACHE["stats"]
        try:
            state = json.loads(LAB_PATH.read_text(encoding="utf-8"))
        except Exception:
            return {}
        stats: dict[str, dict[str, float]] = {}
        for name, book in (state.get("books") or {}).items():
            history = book.get("history") or []
            wins = sum(1 for trade in history if _num(trade.get("pnl_usd")) > 0)
            losses = sum(1 for trade in history if _num(trade.get("pnl_usd")) < 0)
            n = wins + losses
            stats[str(name)] = {"sample": float(n), "wins": float(wins), "losses": float(losses)}
        _LAB_CACHE.update({"mtime": mtime, "loaded_at": now, "stats": stats})
        return stats


def _actual_stats(history: list[dict[str, Any]], matches: list[str], now_ms: int) -> dict[str, Any]:
    relevant = []
    ordered = sorted(history, key=lambda trade: int(trade.get("closed_at") or 0), reverse=True)
    for trade in ordered[:HISTORY_WINDOW]:
        decision = trade.get("brainstorm")
        closed_at = int(trade.get("closed_at") or 0)
        if not isinstance(decision, dict) or not closed_at or closed_at > now_ms:
            continue
        relevant.append(trade)
    wins = sum(1 for t in relevant if _num(t.get("pnl_usd")) > 0)
    losses = sum(1 for t in relevant if _num(t.get("pnl_usd")) < 0)
    sample = wins + losses
    global_mean = (wins + 2.0) / (sample + 4.0)

    per_strategy: dict[str, dict[str, float]] = {}
    for name in matches:
        rows = [t for t in relevant if name in (t.get("strategy_matches") or [])]
        sw = sum(1 for t in rows if _num(t.get("pnl_usd")) > 0)
        sl = sum(1 for t in rows if _num(t.get("pnl_usd")) < 0)
        sn = sw + sl
        per_strategy[name] = {"sample": sn, "posterior": (sw + 2.0) / (sn + 4.0)}
    return {
        "sample": sample,
        "wins": wins,
        "losses": losses,
        "posterior": global_mean,
        "per_strategy": per_strategy,
    }


def _history_brain(history: list[dict[str, Any]], matches: list[str], now_ms: int) -> tuple[float, dict[str, Any]]:
    actual = _actual_stats(history, matches, now_ms)
    lab = _lab_stats()
    estimates = []
    evidence = 0.0
    details = {}
    for name in matches:
        a = actual["per_strategy"].get(name, {"sample": 0, "posterior": 0.5})
        l = lab.get(name, {"sample": 0.0, "wins": 0.0, "losses": 0.0})
        # Lab evidence is deliberately capped so small historical samples cannot dominate live PAPER evidence.
        lab_n = min(10.0, float(l.get("sample", 0.0)))
        lab_w = min(lab_n, float(l.get("wins", 0.0)))
        actual_n = float(a.get("sample", 0))
        actual_p = float(a.get("posterior", 0.5))
        prior_wins = 2.0 + lab_w * 0.35
        prior_losses = 2.0 + max(0.0, lab_n - lab_w) * 0.35
        actual_wins_est = actual_p * (actual_n + 4.0) - 2.0 if actual_n else 0.0
        posterior = (prior_wins + max(0.0, actual_wins_est)) / max(1e-9, prior_wins + prior_losses + actual_n)
        estimates.append(posterior)
        evidence += actual_n + lab_n * 0.35
        details[name] = {"posterior": round(posterior, 4), "actual_sample": int(actual_n), "lab_sample_used": round(lab_n, 2)}
    mean = sum(estimates) / len(estimates) if estimates else 0.5
    # Sparse evidence stays neutral rather than pretending certainty.
    reliability = _clamp(evidence / 80.0)
    score = 0.5 + (mean - 0.5) * reliability
    return _clamp(score), {
        "score": round(_clamp(score), 4),
        "evidence_weight": round(reliability, 4),
        "actual": {k: v for k, v in actual.items() if k != "per_strategy"},
        "strategies": details,
    }


def evaluate_pre(
    coin: dict[str, Any], matches: list[str], history: list[dict[str, Any]], *,
    safety: dict[str, Any] | None = None, validation: dict[str, Any] | None = None,
    flow: dict[str, Any] | None = None, context: dict[str, Any] | None = None,
    now_ms: int | None = None,
) -> dict[str, Any]:
    stamp = int(now_ms or time.time() * 1000)
    if not ENABLED:
        return {"version": VERSION, "enabled": False, "allow": True, "confidence": 1.0, "vetoes": [], "brains": {}}

    families = sorted({_family(name) for name in matches})
    score = _num(coin.get("score"))
    liquidity = _num(coin.get("liquidityUsd"))
    market_cap = _num(coin.get("marketCap") or coin.get("fdv"))
    age = _num(coin.get("ageMinutes"), 999999.0)
    change_m5 = _num((coin.get("priceChange") or {}).get("m5"))
    tx = (coin.get("txns") or {}).get("m5") or {}
    buys = _num(tx.get("buys"))
    sells = _num(tx.get("sells"))
    ratio = buys / max(1.0, sells)
    liq_mc = liquidity / max(1.0, market_cap)

    consensus = _clamp((len(matches) - 1) / 7.0)
    diversity = _clamp((len(families) - 1) / 4.0)
    score_brain = _clamp((score - 65.0) / 30.0)
    liquidity_brain = _clamp((math.log10(max(liquidity, 1.0)) - 4.0) / 1.6)
    flow_brain = _clamp((ratio - 0.80) / 1.10) * _clamp((buys + sells) / 25.0)
    momentum_brain = 0.0 if change_m5 < -3.0 else (1.0 if 2.0 <= change_m5 <= 20.0 else _clamp(1.0 - abs(change_m5 - 10.0) / 35.0))
    structure_brain = 0.65 * _clamp(liq_mc / 0.12) + 0.35 * (1.0 if 3.0 <= age <= 360.0 else _clamp(1.0 - abs(age - 120.0) / 720.0))
    safety_brain = 1.0 if (safety or {}).get("status") == "pass" else 0.0
    validation_brain = 1.0 if (validation or {}).get("status") == "pass" else 0.0
    history_score, history_detail = _history_brain(history, matches, stamp)

    brains = {
        "consensus": consensus,
        "diversity": diversity,
        "market_score": score_brain,
        "liquidity": liquidity_brain,
        "flow": flow_brain,
        "momentum": momentum_brain,
        "structure": structure_brain,
        "history": history_score,
        "safety": safety_brain,
        "validation": validation_brain,
    }
    weights = {
        "consensus": 0.15, "diversity": 0.09, "market_score": 0.13,
        "liquidity": 0.09, "flow": 0.12, "momentum": 0.09,
        "structure": 0.07, "history": 0.08, "safety": 0.09, "validation": 0.09,
    }
    confidence = sum(brains[k] * weights[k] for k in weights)
    vetoes = []
    if len(matches) < MIN_MATCHES:
        vetoes.append("insufficient_strategy_consensus")
    if len(families) < MIN_FAMILIES:
        vetoes.append("insufficient_strategy_diversity")
    if safety_brain < 1.0:
        vetoes.append("safety_not_passed")
    if validation_brain < 1.0:
        vetoes.append("price_validation_not_passed")
    if confidence < MIN_CONFIDENCE:
        vetoes.append("confidence_below_threshold")

    return {
        "version": VERSION,
        "enabled": True,
        "stage": "pre_quote",
        "allow": not vetoes,
        "confidence": round(confidence, 4),
        "min_confidence": MIN_CONFIDENCE,
        "target_win_rate": TARGET_WIN_RATE,
        "matches": list(matches),
        "match_count": len(matches),
        "families": families,
        "family_count": len(families),
        "brains": {k: round(v, 4) for k, v in brains.items()},
        "history_evidence": history_detail,
        "features": {
            "score": round(score, 3), "liquidity_usd": round(liquidity, 2),
            "market_cap_usd": round(market_cap, 2), "age_minutes": round(age, 2),
            "change_m5_pct": round(change_m5, 3), "buys_m5": round(buys, 2),
            "sells_m5": round(sells, 2), "buy_sell_ratio": round(ratio, 4),
            "liquidity_market_cap_ratio": round(liq_mc, 4),
            "context_mode": (context or {}).get("mode"),
            "context_conviction": (context or {}).get("conviction"),
        },
        "vetoes": vetoes,
    }


def evaluate_post(pre: dict[str, Any], *, immediate_roundtrip_pct: float, worst_case_roundtrip_pct: float, impact_pct: float) -> dict[str, Any]:
    if not ENABLED:
        return dict(pre, stage="post_quote", allow=True, execution_score=1.0)
    expected_cost = max(0.0, -_num(immediate_roundtrip_pct))
    worst_cost = max(0.0, -_num(worst_case_roundtrip_pct))
    impact = abs(_num(impact_pct))
    cost_quality = _clamp(1.0 - expected_cost / 2.75)
    worst_quality = _clamp(1.0 - worst_cost / 4.0)
    impact_quality = _clamp(1.0 - impact / 1.75)
    execution_score = 0.50 * cost_quality + 0.30 * worst_quality + 0.20 * impact_quality
    final_confidence = 0.78 * _num(pre.get("confidence")) + 0.22 * execution_score
    vetoes = list(pre.get("vetoes") or [])
    if execution_score < 0.45:
        vetoes.append("weak_execution_quality")
    if final_confidence < MIN_CONFIDENCE and "confidence_below_threshold" not in vetoes:
        vetoes.append("confidence_below_threshold")
    return {
        **pre,
        "stage": "post_quote",
        "allow": not vetoes,
        "confidence": round(final_confidence, 4),
        "pre_quote_confidence": pre.get("confidence"),
        "execution_score": round(execution_score, 4),
        "execution": {
            "expected_roundtrip_pct": round(_num(immediate_roundtrip_pct), 4),
            "worst_case_roundtrip_pct": round(_num(worst_case_roundtrip_pct), 4),
            "impact_pct": round(_num(impact_pct), 4),
            "cost_quality": round(cost_quality, 4),
            "worst_quality": round(worst_quality, 4),
            "impact_quality": round(impact_quality, 4),
        },
        "vetoes": vetoes,
    }
