"""Audit family: no trading CONFIGS are proposed (the auditor's job is the harness). BASELINES are the random-entry
references used in the audit, so the integrator can reproduce them under v1 and v2 (see rerun_with_v2.py)."""
import sys
sys.path.insert(0, (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r'')
import harness as H


def _rnd(prob, salt='r'):
    return lambda P: H.hashed_coin(P.static('pair'), P.t, prob, salt)


def _cost_first(P):
    liq = P('liq')
    if not (liq >= 250_000) or P.fee_bps() > 50:
        return False
    rt = P.rt_cost_pct(min(200.0, liq * 0.001))
    return rt is not None and rt <= 1.2


_r01, _r002, _r004 = _rnd(0.01), _rnd(0.002), _rnd(0.004)

CONFIGS = []

BASELINES = [
    {'name': 'random_cost_first_guard', 'description': 'smoke3: random entries (p=0.01/point) in the live cost-first universe with the interim rug guard, -5/+10/60, size min($200, 0.1% liq)',
     'signal': lambda P: _cost_first(P) and not H.interim_rug_risk(P) and _r01(P),
     'kwargs': {'stop': -5, 'tp': 10, 'hold_min': 60, 'size_fn': lambda P: min(200.0, P('liq') * 0.001)}},
    {'name': 'random_all_pumpswap', 'description': 'smoke: random entries (p=0.002/point) on all PumpSwap SOL pairs, -5/+10/60, $200',
     'signal': lambda P: _r002(P), 'kwargs': {'stop': -5, 'tp': 10, 'hold_min': 60}},
    {'name': 'random_mid_high_fee_guard', 'description': 'random entries (p=0.004/point), fee 52.5-125 bps, liq >= $50k, interim rug guard, -5/+10/60, $200',
     'signal': lambda P: 50 < P.fee_bps() <= 125 and P('liq') >= 50_000 and not H.interim_rug_risk(P) and _r004(P),
     'kwargs': {'stop': -5, 'tp': 10, 'hold_min': 60}},
]
