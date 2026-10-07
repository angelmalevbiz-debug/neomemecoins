"""Transparent planning estimates; an estimate never authorizes an entry.

The fee tiers and modeled fills are shared with the PAPER Lab. The optimistic
cost floor excludes impact, network fees and rent. Real main-account route
quotes remain authoritative, including when their actual fees beat this model.
"""
import math


SOL_QUOTE_MINT = 'So11111111111111111111111111111111111111112'
PUMP_FEE_TIERS = (
    (420, 125.0), (1470, 120.0), (2460, 115.0), (3440, 110.0),
    (4420, 105.0), (9820, 100.0), (14740, 95.0), (19650, 90.0),
    (24560, 85.0), (29470, 80.0), (34380, 75.0), (39300, 70.0),
    (44210, 65.0), (49120, 60.0), (54030, 55.0), (58940, 52.5),
    (63860, 50.0), (68770, 47.5), (73681, 45.0), (78590, 42.5),
    (83500, 40.0), (88400, 37.5), (93330, 35.0), (98240, 32.5),
)


def number(value, default=0.0):
    try:
        result = float(value)
        return result if math.isfinite(result) else default
    except (TypeError, ValueError, OverflowError):
        return default


def quote_token_address(coin):
    normalized = coin.get('quoteTokenAddress')
    raw_token = coin.get('quoteToken')
    if raw_token is not None and not isinstance(raw_token, dict):
        return None
    raw = (raw_token or {}).get('address')
    if normalized is not None and raw is not None and normalized != raw:
        return None
    return normalized if normalized is not None else raw


def sol_usd_from_coin(coin):
    if quote_token_address(coin) != SOL_QUOTE_MINT:
        return 0.0
    price, native = number(coin.get('priceUsd')), number(coin.get('priceNative'))
    value = price / native if price > 0 and native > 0 else 0.0
    return value if math.isfinite(value) and value > 0 else 0.0


def pumpswap_fee_bps(coin, generic_dex_fee_bps=30.0):
    if str(coin.get('dexId') or '').lower() != 'pumpswap':
        return generic_dex_fee_bps
    sol_usd = sol_usd_from_coin(coin)
    market_cap = number(coin.get('marketCap') or coin.get('fdv'))
    if sol_usd <= 0 or market_cap <= 0:
        return 125.0
    market_cap_sol = market_cap / sol_usd
    return next((fee for limit, fee in PUMP_FEE_TIERS if market_cap_sol < limit), 30.0)


def execution_feasibility(coin, max_cost_pct=1.5, *, base_slippage_bps=10.0,
                          latency_buffer_bps=10.0, generic_dex_fee_bps=30.0):
    """Whether the optimistic PAPER model could fit a declared cost budget.

    False means this model's fixed costs already exceed the budget. True still
    requires all variable costs and valid quotes. Unknown inputs remain unknown.
    The result is only a discovery/scheduling hint for the main account.
    """
    result = {'basis': 'OPTIMISTIC_PAPER_FEE_AND_BUFFER_MODEL',
              'is_execution_quote': False, 'profitability_proven': False,
              'max_roundtrip_cost_pct': max_cost_pct,
              'model_cost_feasible': None, 'minimum_model_roundtrip_cost_pct': None,
              'dex_fee_bps': None, 'reason': 'model_inputs_unavailable'}
    if sol_usd_from_coin(coin) <= 0:
        result['reason'] = 'network_price_unknown'
        return result
    if (str(coin.get('dexId') or '').lower() == 'pumpswap'
            and number(coin.get('marketCap') or coin.get('fdv')) <= 0):
        return result
    budget = number(max_cost_pct, -1)
    fee = pumpswap_fee_bps(coin, generic_dex_fee_bps) / 10_000
    penalty = (base_slippage_bps + latency_buffer_bps) / 10_000
    if budget < 0 or not 0 <= fee < 1 or not 0 <= penalty < 1:
        return result
    # Entry and exit both charge a fee. The same modeled market mark is used;
    # no favorable future price movement is assumed to erase entry friction.
    cost = 100 * (1 - (1 - fee) ** 2 * (1 - penalty) / (1 + penalty))
    feasible = cost <= budget
    result.update(dex_fee_bps=fee * 10_000,
                  minimum_model_roundtrip_cost_pct=round(cost, 6),
                  model_cost_feasible=feasible,
                  reason='variable_costs_and_quotes_required' if feasible else 'model_fixed_cost_above_limit')
    return result


def modeled_roundtrip(coin, notional, *, generic_dex_fee_bps=30.0,
                      base_slippage_bps=10.0, latency_buffer_bps=10.0,
                      network_fee_sol=.0001, max_price_impact_pct=20.0):
    """Current Lab spot/cost model, with no claim of a real executed route."""
    sol_usd = sol_usd_from_coin(coin)
    market, notional = number(coin.get('priceUsd')), number(notional)
    liquidity = number(coin.get('liquidityUsd') or (coin.get('liquidity') or {}).get('usd'))
    if sol_usd <= 0 or market <= 0 or notional <= 0 or liquidity <= 0:
        return {'basis': 'PAPER_SPOT_MODELED_COSTS', 'is_execution_quote': False,
                'status': 'unavailable', 'reason': 'network_price_unknown' if sol_usd <= 0 else 'model_inputs_unavailable'}
    fee = pumpswap_fee_bps(coin, generic_dex_fee_bps) / 10_000
    network = network_fee_sol * sol_usd
    entry_impact = min(max_price_impact_pct, 2 * notional / liquidity * 100)
    entry_penalty = (entry_impact + (base_slippage_bps + latency_buffer_bps) / 100) / 100
    quantity = notional * (1 - fee) / (market * (1 + entry_penalty))
    market_value = quantity * market
    exit_impact = min(max_price_impact_pct, 2 * market_value / liquidity * 100)
    exit_penalty = (exit_impact + (base_slippage_bps + latency_buffer_bps) / 100) / 100
    net_proceeds = max(0.0, market_value * (1 - exit_penalty) * (1 - fee) - network)
    committed = notional + network
    return {'basis': 'PAPER_SPOT_MODELED_COSTS', 'is_execution_quote': False,
            'status': 'estimate', 'notional_usd': notional,
            'initial_pnl_pct': 100 * (net_proceeds - committed) / notional,
            'capital_committed_usd': committed, 'net_proceeds_usd': net_proceeds,
            'quantity': quantity, 'dex_fee_bps': fee * 10_000}
