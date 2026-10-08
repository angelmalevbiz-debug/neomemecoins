"""Uncapped constant-product exit valuation (for drains), built only on harness primitives.

H.exit_value uses impact = min(20%, 2*N/L) and 20% when L <= 0. For a constant-product pool the exact
loss of selling value N into liquidity L (both sides, USD) is x/(1+x) with x = 2N/L, which goes to 100%
as L -> 0. Here: impact = max(min(x, 20%), x/(1+x)) - identical to the harness for x <= 20%, continuous,
and 100% when liquidity is 0 (an LP pull leaves nothing to sell into).
"""
import bisect, sys

sys.path.insert(0, (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r'')
import harness as H  # noqa: E402


def exit_value_real(s, k, qty, extra_bps=0.0, price=None):
    p = s['price'][k] if price is None else price
    liq, su = s['liq'][k], H.sol_usd(s, k)
    if not (p > 0):
        return 0.0
    mv = qty * p
    x = (2 * mv / liq) if liq > 0 else float('inf')
    impact = 1.0 if x == float('inf') else max(min(x, 0.2), x / (1 + x))
    pen = impact + (H.BASE_BPS + extra_bps) / 1e4
    return max(0.0, mv * (1 - pen) * (1 - H.fee_bps(s, k) / 1e4) - H.NETWORK_SOL * su)


def revalue(trades):
    """Adds net50r / usd50r / net0r to each harness trade (same entry/exit points, uncapped exit impact)."""
    series, _ = H.load()
    for x in trades:
        s = series[x['pair']]
        ts = s['t']
        j = bisect.bisect_left(ts, x['entry_t'])
        e = bisect.bisect_left(ts, x['exit_t'])
        f50 = H.entry_fill(s, j, x['size'], H.STRESS_BPS)
        f0 = H.entry_fill(s, j, x['size'])
        px = s['price'][e] * (1 - H.VANISH_HAIRCUT_PCT / 100) if x['flag'] == 'VANISHED' else s['price'][e]
        x['net50r'] = 100 * (exit_value_real(s, e, f50[0], H.STRESS_BPS, px) - x['size'] - f50[1]) / x['size']
        x['usd50r'] = x['size'] * x['net50r'] / 100
        x['net0r'] = 100 * (exit_value_real(s, e, f0[0], 0.0, px) - x['size'] - f0[1]) / x['size']
    return trades
