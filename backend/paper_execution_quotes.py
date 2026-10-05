#!/usr/bin/env python3
"""Compatibility facade for the shared, read-only PAPER quote model.

All account consumers use the same transport and accounting rules. Lazy imports
avoid a cycle because engine_execution also imports these mint/route constants.
No transaction building, signing, submission or confirmation API exists here.
"""
import os
from typing import Any
import honest_quote_transport as transport

USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
SLIPPAGE_BPS = int(os.getenv("NEO_JUPITER_SLIPPAGE_BPS", "100"))


def _compact_route(data: dict[str, Any]) -> list[dict[str, Any]]:
    return [{"ammKey": (leg.get("swapInfo") or {}).get("ammKey"),
             "label": (leg.get("swapInfo") or {}).get("label"),
             "percent": leg.get("percent"),
             "inputMint": (leg.get("swapInfo") or {}).get("inputMint"),
             "outputMint": (leg.get("swapInfo") or {}).get("outputMint"),
             "feeAmount": (leg.get("swapInfo") or {}).get("feeAmount"),
             "feeMint": (leg.get("swapInfo") or {}).get("feeMint")}
            for leg in data.get("routePlan") or []]


def route_uses_pair(data: dict[str, Any], pair_address: str) -> bool:
    return bool(pair_address) and any((leg.get("swapInfo") or {}).get("ammKey") == pair_address
                                     for leg in data.get("routePlan") or [])


def quote(input_mint: str, output_mint: str, amount_raw: int, *, purpose: str = "entry"):
    data = transport.quote(input_mint,output_mint,amount_raw,purpose=purpose,slippage_bps=SLIPPAGE_BPS)
    if data:
        data["_compactRoute"] = _compact_route(data)
    return data


def entry_quote(token_mint: str,pair_address: str,notional_usd: float):
    import engine_execution
    return engine_execution.entry_quote(token_mint,pair_address,notional_usd)


def exit_quote(token_mint: str,token_raw_amount: int):
    import engine_execution
    return engine_execution.exit_quote(token_mint,token_raw_amount)


def position_mark(position: dict[str,Any],coin: dict[str,Any],network_fee_usd: float,force: bool=False):
    import engine_execution
    return engine_execution.position_mark(position,coin,network_fee_usd,force=force)
