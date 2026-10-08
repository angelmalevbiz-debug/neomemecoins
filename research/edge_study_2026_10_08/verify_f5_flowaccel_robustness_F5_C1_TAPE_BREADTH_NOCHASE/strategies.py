"""Verifier copy of F5_C1_TAPE_BREADTH_NOCHASE (rug_guard_v1 variant) plus 5 random-entry baselines (5 salts).

Verdict of the robustness check: REFUTED as a positive-expectancy strategy (see REPORT.md). Exposed only so the
integrator can re-run the exact verified rule and its baselines. PAPER research only.
Past-only: DexScreener fields through the harness Past view; tape flow through f5_flowaccel/tapefeat.py (events with
block time <= t AND ingestion time <= t).
"""
import sys
sys.path.insert(0, (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r'')
import harness as H

_F5 = (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r'/f5_flowaccel'
if _F5 not in sys.path:
    sys.path.insert(0, _F5)
import tapefeat as TF  # noqa: E402


def _fin(x):
    return x == x and x not in (float('inf'), float('-inf'))


def _guard(P):
    g = getattr(H, 'rug_guard_v1', None)   # present in the final integrator harness; absent in harness v1
    return bool(g(P)) if g is not None else False


def _universe_nc(P):
    if not (P('liq') >= 20_000):
        return False
    if not (52.5 <= P.fee_bps() <= 125):
        return False
    if H.interim_rug_risk(P):
        return False
    age = TF.last_available_age_s(P.static('pair'), P.t)
    if age is None or age > 600:
        return False
    p0, p1 = P('price'), P.ago('price', 300)
    if not (_fin(p1) and p1 > 0 and _fin(p0)):
        return False
    if not (-2 <= 100 * (p0 / p1 - 1) <= 5):
        return False
    return not _guard(P)


def sig_c1_guarded(P):
    if not _universe_nc(P):
        return False
    f = TF.flow(P.static('pair'), P.t, 300)
    return (f is not None and f['ub'] >= 10 and f['newb'] >= 5 and _fin(f['top_share']) and f['top_share'] < 0.3
            and f['net_sol'] > 0)


def _size(P):
    return min(200.0, 0.002 * P('liq'))


EXITS = dict(stop=-10.0, tp=6.0, hold_min=30.0, size_fn=_size)

CONFIGS = [
    {'name': 'F5_C1_TAPE_BREADTH_NOCHASE__RUG_GUARD_V1__VERIFIED',
     'description': 'Exact F5_C1 rule (tape-live PumpSwap pool, fee 52.5-125 bps, liq>=$20k, interim rug screen, '
                    '>=10 distinct buyers, >=5 first-time buyers, top buyer <30% of buy SOL, net SOL inflow >0 over 5 '
                    'min, 5-min price change in [-2%, +5%]) with rug_guard_v1 ANDed in; exits -10/+6 net, 30 min, '
                    'size min($200, 0.2% liq). Reproduces the leaderboard rug_guard_v1 trades exactly (66 trades). '
                    'REFUTED by the robustness check.',
     'signal': sig_c1_guarded, 'kwargs': dict(EXITS)},
]


def _rnd(salt, prob=0.002):
    return lambda P: _universe_nc(P) and H.hashed_coin(P.static('pair'), P.t, prob, salt)


BASELINES = [
    {'name': 'F5C1_VERIFY_RANDOM_%d' % k,
     'description': 'Random entries (hashed coin p=0.002/point, salt vrob%d) in the same guarded tape-live + no-chase '
                    'universe, same exits and sizing (~50-66 trades over the span, like C1).' % k,
     'signal': _rnd('vrob%d' % k), 'kwargs': dict(EXITS)}
    for k in range(5)
]
