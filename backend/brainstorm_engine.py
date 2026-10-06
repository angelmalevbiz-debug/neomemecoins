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

VERSION = "BRAINSTORM_META_V2"
ENABLED = os.getenv("NEO_BRAINSTORM_ENABLED", "0").strip().lower() in {"1", "true", "yes", "on"}
TARGET_WIN_RATE = float(os.getenv("NEO_BRAINSTORM_TARGET_WIN_RATE", "0.70"))
MIN_CONFIDENCE = float(os.getenv("NEO_BRAINSTORM_MIN_CONFIDENCE", "0.70"))
MIN_MATCHES = max(1, int(os.getenv("NEO_BRAINSTORM_MIN_MATCHES", "3")))
MIN_FAMILIES = max(1, int(os.getenv("NEO_BRAINSTORM_MIN_FAMILIES", "2")))
HISTORY_WINDOW = max(20, int(os.getenv("NEO_BRAINSTORM_HISTORY_WINDOW", "200")))
RECENT_WINDOW = max(10, int(os.getenv("NEO_BRAINSTORM_RECENT_WINDOW", "24")))
CALIBRATION_MIN_SAMPLE = max(8, int(os.getenv("NEO_BRAINSTORM_CALIBRATION_MIN_SAMPLE", "12")))
MAX_DYNAMIC_THRESHOLD = float(os.getenv("NEO_BRAINSTORM_MAX_CONFIDENCE", "0.88"))
LAB_PATH = Path(os.getenv("NEO_STRATEGY_LAB_PATH", "/var/lib/neo-market/strategy_lab.json"))

if not 0.0 < TARGET_WIN_RATE < 1.0 or not 0.0 < MIN_CONFIDENCE <= 1.0 or not MIN_CONFIDENCE <= MAX_DYNAMIC_THRESHOLD <= 0.99:
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



def _closed_brainstorm(history: list[dict[str, Any]], now_ms: int) -> list[dict[str, Any]]:
    rows = []
    for trade in sorted(history, key=lambda t: int(t.get("closed_at") or 0), reverse=True):
        closed_at = int(trade.get("closed_at") or 0)
        if not closed_at or closed_at > now_ms:
            continue
        if not (isinstance(trade.get("brainstorm"), dict) or str(trade.get("brainstorm_version") or "").startswith("BRAINSTORM_META_")):
            continue
        if not isinstance(trade.get("pnl_usd"), (int, float)):
            continue
        rows.append(trade)
        if len(rows) >= HISTORY_WINDOW:
            break
    return rows

def _regime(coin: dict[str, Any], flow: dict[str, Any] | None = None) -> dict[str, Any]:
    pc = coin.get("priceChange") or {}
    vol = coin.get("volume") or {}
    m5 = _num(pc.get("m5")); h1 = _num(pc.get("h1"))
    liquidity = _num(coin.get("liquidityUsd")); volume_h1 = _num(vol.get("h1"))
    tx = (coin.get("txns") or {}).get("m5") or {}
    buys = _num(tx.get("buys")); sells = _num(tx.get("sells")); ratio = buys / max(1.0, sells)
    flow_ratio = _num((flow or {}).get("buy_sell_usd_ratio"), ratio)
    velocity = volume_h1 / max(liquidity, 1.0)
    if abs(m5) >= 25 or abs(h1) >= 100:
        name = "chaotic"
    elif m5 >= 5 and h1 >= 8 and max(ratio, flow_ratio) >= 1.15:
        name = "breakout_trend"
    elif m5 <= -4 and h1 > -25 and max(ratio, flow_ratio) >= 1.05:
        name = "pullback_recovery"
    elif abs(m5) <= 3 and velocity < 0.8:
        name = "quiet"
    else:
        name = "mixed"
    return {"name": name, "m5": round(m5,3), "h1": round(h1,3), "velocity_h1": round(velocity,4), "buy_sell_ratio": round(max(ratio,flow_ratio),4)}

def _regime_brain(regime: str, families: list[str]) -> float:
    fam=set(families)
    preferred={
        "breakout_trend": {"momentum","flow","precision"},
        "pullback_recovery": {"reversal","flow","precision"},
        "quiet": {"precision","liquidity"},
        "mixed": {"flow","momentum","precision","liquidity","reversal"},
        "chaotic": {"precision","liquidity"},
    }.get(regime,{"precision"})
    overlap=len(fam & preferred)
    score=_clamp(overlap / max(2.0, min(4.0, len(preferred))))
    if regime == "chaotic": score *= 0.65
    return score

def _posterior_lcb(wins: int, losses: int) -> tuple[float,float]:
    # Beta(2,2) shrinkage with a conservative normal lower bound.
    a=2.0+wins; b=2.0+losses; n=a+b
    mean=a/n
    var=(a*b)/(n*n*(n+1.0))
    lcb=_clamp(mean - 1.28*math.sqrt(max(0.0,var)))
    return mean,lcb

def _champion_brain(history: list[dict[str, Any]], matches: list[str], now_ms: int) -> tuple[float,dict[str,Any]]:
    rows=_closed_brainstorm(history,now_ms)
    table=[]
    for name in matches:
        rel=[t for t in rows if name in (t.get("strategy_matches") or [])]
        w=sum(1 for t in rel if _num(t.get("pnl_usd"))>0); l=sum(1 for t in rel if _num(t.get("pnl_usd"))<0)
        mean,lcb=_posterior_lcb(w,l)
        table.append((lcb,mean,w+l,name))
    table.sort(reverse=True)
    top=table[:3]
    if not top: return 0.5,{"leaders":[],"score":0.5}
    raw=sum(x[0] for x in top)/len(top)
    evidence=_clamp(sum(x[2] for x in top)/45.0)
    score=0.5+(raw-0.5)*evidence
    return _clamp(score),{"score":round(_clamp(score),4),"leaders":[{"strategy":x[3],"sample":x[2],"posterior":round(x[1],4),"lcb":round(x[0],4)} for x in top]}

def _drift_and_threshold(history: list[dict[str, Any]], now_ms: int) -> dict[str, Any]:
    rows=_closed_brainstorm(history,now_ms)
    recent=rows[:RECENT_WINDOW]
    older=rows[RECENT_WINDOW:RECENT_WINDOW*3]
    def wr(group):
        w=sum(1 for t in group if _num(t.get("pnl_usd"))>0); l=sum(1 for t in group if _num(t.get("pnl_usd"))<0); n=w+l
        return ((w+2)/(n+4),n,w,l)
    rp,rn,rw,rl=wr(recent); op,on,ow,ol=wr(older)
    shortfall=max(0.0,TARGET_WIN_RATE-rp) if rn>=CALIBRATION_MIN_SAMPLE else 0.0
    drift=max(0.0,op-rp) if rn>=CALIBRATION_MIN_SAMPLE and on>=CALIBRATION_MIN_SAMPLE else 0.0
    penalty=min(0.14, shortfall*0.35 + drift*0.30)
    threshold=_clamp(MIN_CONFIDENCE+penalty,MIN_CONFIDENCE,MAX_DYNAMIC_THRESHOLD)
    return {"threshold":round(threshold,4),"penalty":round(penalty,4),"recent_sample":rn,"recent_posterior":round(rp,4),"older_sample":on,"older_posterior":round(op,4),"drift":round(drift,4)}

def _calibration_brain(history: list[dict[str, Any]], proposed_confidence: float, now_ms: int) -> tuple[float,dict[str,Any]]:
    rows=_closed_brainstorm(history,now_ms)
    bucket=max(0,min(9,int(proposed_confidence*10)))
    rel=[]
    for t in rows:
        c=_num(t.get("brainstorm_confidence"), _num((t.get("brainstorm") or {}).get("confidence"),-1))
        if c>=0 and max(0,min(9,int(c*10)))==bucket: rel.append(t)
    w=sum(1 for t in rel if _num(t.get("pnl_usd"))>0); l=sum(1 for t in rel if _num(t.get("pnl_usd"))<0); n=w+l
    mean,lcb=_posterior_lcb(w,l)
    reliability=_clamp(n/30.0)
    score=0.5+(mean-0.5)*reliability
    return _clamp(score),{"bucket":bucket,"sample":n,"posterior":round(mean,4),"lcb":round(lcb,4),"score":round(_clamp(score),4)}

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
    regime = _regime(coin, flow)
    regime_score = _regime_brain(regime["name"], families)
    champion_score, champion_detail = _champion_brain(history, matches, stamp)

    brains = {
        "consensus": consensus,
        "diversity": diversity,
        "market_score": score_brain,
        "liquidity": liquidity_brain,
        "flow": flow_brain,
        "momentum": momentum_brain,
        "structure": structure_brain,
        "history": history_score,
        "regime": regime_score,
        "champion": champion_score,
        "safety": safety_brain,
        "validation": validation_brain,
    }
    weights = {
        "consensus": 0.12, "diversity": 0.07, "market_score": 0.11,
        "liquidity": 0.07, "flow": 0.10, "momentum": 0.07,
        "structure": 0.06, "history": 0.08, "regime": 0.08, "champion": 0.08,
        "safety": 0.08, "validation": 0.08,
    }
    confidence = sum(brains[k] * weights[k] for k in weights)
    calibration_score, calibration_detail = _calibration_brain(history, confidence, stamp)
    confidence = 0.90 * confidence + 0.10 * calibration_score
    adaptive = _drift_and_threshold(history, stamp)
    dynamic_threshold = adaptive["threshold"]
    vetoes = []
    if len(matches) < MIN_MATCHES:
        vetoes.append("insufficient_strategy_consensus")
    if len(families) < MIN_FAMILIES:
        vetoes.append("insufficient_strategy_diversity")
    if safety_brain < 1.0:
        vetoes.append("safety_not_passed")
    if validation_brain < 1.0:
        vetoes.append("price_validation_not_passed")
    if regime["name"] == "chaotic" and len(matches) < MIN_MATCHES + 2:
        vetoes.append("chaotic_regime_needs_extra_consensus")
    if confidence < dynamic_threshold:
        vetoes.append("confidence_below_threshold")

    return {
        "version": VERSION,
        "enabled": True,
        "stage": "pre_quote",
        "allow": not vetoes,
        "confidence": round(confidence, 4),
        "min_confidence": dynamic_threshold,
        "base_min_confidence": MIN_CONFIDENCE,
        "target_win_rate": TARGET_WIN_RATE,
        "matches": list(matches),
        "match_count": len(matches),
        "families": families,
        "family_count": len(families),
        "brains": {k: round(v, 4) for k, v in brains.items()},
        "history_evidence": history_detail,
        "champion_evidence": champion_detail,
        "calibration": calibration_detail,
        "adaptive_threshold": adaptive,
        "regime": regime,
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
    threshold = _num(pre.get("min_confidence"), MIN_CONFIDENCE)
    if final_confidence < threshold and "confidence_below_threshold" not in vetoes:
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
