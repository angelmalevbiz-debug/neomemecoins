"""Rug study: CONFIGS = random entries with rug_guard_v1 applied, per universe; BASELINES = the same
random draws without the guard (paired: hashed_coin with the same salt, so each guarded run is the
unguarded run minus the flagged entries).

These are NOT edge strategies. The guard is a risk filter; it removes drain losses but does not create
positive expectancy (see REPORT.md: every universe stays negative at net50 with or without it).
Note for the integrator: H.exit_value caps impact at 20% and books liquidity 0 as 20% impact, so an
LP pull (liquidity -> 0, price unchanged) costs ~-22..-32% in the harness instead of ~-100%.
realistic_exit.revalue(trades) re-prices exits with uncapped constant-product impact (adds net50r/usd50r).
"""
import os, sys

sys.path.insert(0, (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r'')
import harness as H  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from rug_guard import rug_guard_v1  # noqa: E402

SALT = 'rug'
EXITS = dict(stop=-5, tp=10, hold_min=60)


def _rnd(P, prob):
    return H.hashed_coin(P.static('pair'), P.t, prob, SALT)


def _cost_first(P):
    """Live COST_FIRST_UNIVERSE_V1 physical screen: fee <= 50 bps, liq >= $250k, fee+impact RT <= 1.2%."""
    liq = P('liq')
    if not (liq >= 250_000) or P.fee_bps() > 50:
        return False
    rt = P.rt_cost_pct(min(200.0, liq * 0.001))
    return rt is not None and rt <= 1.2


def _cf_size(P):
    return min(200.0, P('liq') * 0.001)


CONFIGS = [
    {'name': 'rug_cf_random_g1',
     'description': 'Cost-first universe, random entries (p=0.03 per point), skip when rug_guard_v1 fires; -5/+10/60, size min($200, liq*0.1%).',
     'signal': lambda P: _cost_first(P) and _rnd(P, 0.03) and not rug_guard_v1(P),
     'kwargs': dict(EXITS, size_fn=_cf_size)},
    {'name': 'rug_liq20_random_g1',
     'description': 'All PumpSwap SOL pools with liq >= $20k, random entries (p=0.006), skip when rug_guard_v1 fires; -5/+10/60, $200.',
     'signal': lambda P: P('liq') >= 20_000 and _rnd(P, 0.006) and not rug_guard_v1(P),
     'kwargs': dict(EXITS)},
    {'name': 'rug_hf50_random_g1',
     'description': 'Fee 100-125 bps pools with liq >= $50k, random entries (p=0.012), skip when rug_guard_v1 fires; -5/+10/60, $200.',
     'signal': lambda P: P('liq') >= 50_000 and P.fee_bps() >= 100 and _rnd(P, 0.012) and not rug_guard_v1(P),
     'kwargs': dict(EXITS)},
]

BASELINES = [
    {'name': 'rug_cf_random_noguard',
     'description': 'Same draws as rug_cf_random_g1 without the guard.',
     'signal': lambda P: _cost_first(P) and _rnd(P, 0.03),
     'kwargs': dict(EXITS, size_fn=_cf_size)},
    {'name': 'rug_liq20_random_noguard',
     'description': 'Same draws as rug_liq20_random_g1 without the guard.',
     'signal': lambda P: P('liq') >= 20_000 and _rnd(P, 0.006),
     'kwargs': dict(EXITS)},
    {'name': 'rug_hf50_random_noguard',
     'description': 'Same draws as rug_hf50_random_g1 without the guard.',
     'signal': lambda P: P('liq') >= 50_000 and P.fee_bps() >= 100 and _rnd(P, 0.012),
     'kwargs': dict(EXITS)},
]

if __name__ == '__main__':
    import json
    from realistic_exit import revalue
    for c in CONFIGS + BASELINES:
        tr = revalue(H.simulate(c['signal'], **c['kwargs']))
        ev = H.evaluate(tr)
        ho = [x for x in tr if x['entry_t'] >= H.split_t(H.load()[1])]
        print(c['name'], json.dumps({p: {k: ev[p].get(k) for k in ('n', 'pairs', 'mean_pct', 'median_pct', 'win_rate', 'pf', 'sum_usd', 'top_pair_share')} for p in ('train', 'holdout')}),
              'holdout mean net50 realistic %.3f' % (sum(x['net50r'] for x in ho) / max(1, len(ho))))
