"""Harness cost model re-expressed on scalar inputs (identical formulas to harness.entry_fill /
harness.exit_value / paper_market_feasibility.modeled_roundtrip), so a ledger trade's own recorded
price, liquidity, market cap and SOL reference can be priced exactly as the harness would price them."""
import math, sys
sys.path.insert(0, (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + '/../../backend' + '')
from paper_market_feasibility import PUMP_FEE_TIERS  # read-only import

BASE_BPS = 20.0
NETWORK_SOL = 0.0001


def fnum(x, default=float('nan')):
    try:
        v = float(x)
        return v if math.isfinite(v) else default
    except (TypeError, ValueError):
        return default


def fee_bps(mcap_usd, sol_usd, dex='pumpswap'):
    if dex != 'pumpswap':
        return 30.0
    if not (sol_usd > 0) or not (mcap_usd > 0):
        return 125.0
    mcs = mcap_usd / sol_usd
    return next((fee for limit, fee in PUMP_FEE_TIERS if mcs < limit), 30.0)


def entry_fill(p, liq, sol_usd, fee, notional, base_bps=BASE_BPS, extra_bps=0.0):
    """(qty, network_usd) exactly as harness.entry_fill."""
    impact = min(20.0, 2 * notional / liq * 100)
    pen = (impact + (base_bps + extra_bps) / 100) / 100
    return notional * (1 - fee / 1e4) / (p * (1 + pen)), NETWORK_SOL * sol_usd


def exit_value(p, liq, sol_usd, fee, qty, base_bps=BASE_BPS, extra_bps=0.0, network=True):
    mv = qty * p
    impact = 20.0 if not (liq > 0) else min(20.0, 2 * mv / liq * 100)
    pen = (impact + (base_bps + extra_bps) / 100) / 100
    return max(0.0, mv * (1 - pen) * (1 - fee / 1e4) - (NETWORK_SOL * sol_usd if network else 0.0))


def entry_cost_pct(p, liq, fee, notional, base_bps=BASE_BPS):
    """Per-leg entry cost in % of notional vs the mark (no network fee)."""
    q, _ = entry_fill(p, liq, 1.0, fee, notional, base_bps)
    return 100 * (1 - q * p / notional)


def exit_cost_pct(p, liq, fee, qty, base_bps=BASE_BPS):
    mv = qty * p
    return 100 * (1 - exit_value(p, liq, 1.0, fee, qty, base_bps, network=False) / mv)


def roundtrip_pct(p, liq, sol_usd, fee, notional, base_bps=BASE_BPS, network=True):
    """Immediate round trip loss in % (positive), as harness.roundtrip_cost_pct."""
    q, nf = entry_fill(p, liq, sol_usd, fee, notional, base_bps)
    v = exit_value(p, liq, sol_usd, fee, q, base_bps, network=network)
    return 100 * (notional + (nf if network else 0.0) - v) / notional
