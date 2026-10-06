"""Bounded read-only route evidence for the isolated PAPER learner.

This only preflights exact-pool Jupiter routes. It never builds, signs, or
submits a transaction and never treats a quote as a fill.
"""
from __future__ import annotations

import math
import os
from decimal import Decimal

import engine_execution
import paper_execution_quotes
from paper_training import DEFAULT_CONFIG, USDC, number, training_candidate_signal

SOL = "So11111111111111111111111111111111111111112"


def collect_exact_pool_quotes(coin, flow, safety, validation, *, now,
                              quote=None, notional_usd=None,
                              max_impact_pct=None):
    """Return fresh two-way route proof, or a fail-closed reason code."""
    quote = quote or paper_execution_quotes.quote
    notional_usd = DEFAULT_CONFIG["notional"] if notional_usd is None else notional_usd
    max_impact_pct = DEFAULT_CONFIG["max_impact_pct"] if max_impact_pct is None else max_impact_pct
    mint, pair = str(coin.get("address") or ""), str(coin.get("pairAddress") or "")
    if not training_candidate_signal(coin, flow, now=now):
        return None, "candidate_gate"
    if (not isinstance(safety, dict) or safety.get("status") != "pass"
            or safety.get("provisional_early") or safety.get("mint") != mint
            or safety.get("pair") != pair
            or not 0 <= now-number(safety.get("checked_at"), -1) <= DEFAULT_CONFIG["evidence_ttl_ms"]):
        return None, "safety_not_fresh_pass"
    metrics = safety.get("metrics") or {}
    rent_lamports = number(metrics.get("token_account_rent_lamports"), math.nan)
    sol_usd = number(metrics.get("sol_usd"), math.nan)
    if (not math.isfinite(rent_lamports) or rent_lamports <= 0
            or not math.isfinite(sol_usd) or sol_usd <= 0):
        return None, "rent_or_sol_cost_unknown"
    rent_usd = rent_lamports/1_000_000_000*sol_usd
    reference_at = number((validation or {}).get("reference_received_at"), -1)
    if (not isinstance(validation, dict) or validation.get("status") != "pass"
            or validation.get("mint") != mint or validation.get("pair") != pair
            or number(validation.get("reference_price")) <= 0
            or not 0 <= now-reference_at <= DEFAULT_CONFIG["evidence_ttl_ms"]):
        return None, "independent_price_not_fresh_pass"
    try:
        notional = Decimal(str(notional_usd))
        if not notional.is_finite() or notional <= 0:
            return None, "invalid_notional"
        input_raw = int(notional*1_000_000)
        buy = quote(USDC, mint, input_raw, purpose="background")
        if (not buy or not engine_execution.valid(buy, USDC, mint, input_raw, now)
                or not engine_execution.same_token_pool(buy, mint, pair)):
            return None, "exact_pool_buy_unavailable"
        buy_impact = float(buy.get("priceImpactPct"))*100
        if not math.isfinite(buy_impact) or buy_impact > max_impact_pct:
            return None, "buy_impact_limit"
        # Use the provider's conservative minimum output for a sellability check.
        sell_input_raw = int(buy["otherAmountThreshold"])
        if sell_input_raw <= 0:
            return None, "invalid_buy_floor"
        sale = quote(mint, USDC, sell_input_raw, purpose="background")
        if (not sale or not engine_execution.valid(sale, mint, USDC, sell_input_raw, now)
                or not engine_execution.same_token_pool(sale, mint, pair)):
            return None, "exact_pool_sell_unavailable"
        sell_impact = float(sale.get("priceImpactPct"))*100
        if not math.isfinite(sell_impact) or sell_impact > max_impact_pct:
            return None, "sell_impact_limit"
        if int(sale.get("_received_at") or 0) < int(buy.get("_received_at") or 0):
            return None, "quote_time_inconsistent"
    except (ArithmeticError, KeyError, TypeError, ValueError, OverflowError):
        return None, "quote_schema_invalid"
    except Exception:
        # A quote-provider failure is a rejected probe, never a worker crash.
        return None, "quote_provider_unavailable"

    network_sol = number(os.getenv("NEO_EXEC_NETWORK_FEE_SOL", "0.0001"), math.nan)
    if not math.isfinite(network_sol) or network_sol < 0:
        return None, "network_fee_config_invalid"
    network_usd = max(.03, network_sol*sol_usd)
    return {
        "entry": {"raw_quote": buy, "quoted_at": int(buy["_received_at"]),
                  "network_fee_usd": network_usd,
                  "entry_account_reserve_usd": rent_usd},
        "exit": {"raw_quote": sale, "quoted_at": int(sale["_received_at"]),
                 "network_fee_usd": network_usd},
    }, "ok"
