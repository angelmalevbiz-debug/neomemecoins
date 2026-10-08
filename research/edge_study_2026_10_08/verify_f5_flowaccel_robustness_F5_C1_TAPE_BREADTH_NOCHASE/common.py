"""Shared setup for the F5_C1 robustness verification (PAPER research only, read-only over all inputs).

Binds `harness` to the integrator's final audited harness (leaderboard/harness_final.py) exactly as run_all.py does,
and re-implements F5_C1_TAPE_BREADTH_NOCHASE as a parametrized, past-only signal (same tapefeat reader) so every
threshold can be perturbed. With DEFAULTS and guard=True it must reproduce the leaderboard's rug_guard_v1 trades.
"""
import sys
sys.dont_write_bytecode = True
import importlib.util, math, os

DEEP = (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r''
LB = DEEP + '/leaderboard'
F5DIR = DEEP + '/f5_flowaccel'
OUT = DEEP + '/verify_f5_flowaccel_robustness_F5_C1_TAPE_BREADTH_NOCHASE'


def _load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


if 'harness_final' in sys.modules:
    H = sys.modules['harness_final']
else:
    H = _load_module('harness_final', LB + '/harness_final.py')
sys.modules['harness'] = H
for p in (DEEP, F5DIR):
    if p not in sys.path:
        sys.path.insert(0, p)
import tapefeat as TF  # noqa: E402  (read-only: tape_cache.pkl already exists)


def fin(x):
    return x == x and x not in (float('inf'), float('-inf'))


DEFAULTS = dict(liq_min=20_000.0, fee_lo=52.5, fee_hi=125.0, live_s=600.0, nc_win=300.0, nc_lo=-2.0, nc_hi=5.0,
                fl_win=300.0, ub=10, newb=5, top=0.3, net_sol=0.0, use_newb=True, use_top=True, use_net=True,
                guard=True)
EXIT_DEFAULTS = dict(stop=-10.0, tp=6.0, hold_min=30.0, size_frac=0.002, size_cap=200.0, cooldown_s=300)


def universe_nc(p):
    """Universe + no-chase filter + optional rug_guard_v1 (no flow condition)."""
    def u(P):
        liq = P('liq')
        if not (liq >= p['liq_min']):
            return False
        f = P.fee_bps()
        if not (p['fee_lo'] <= f <= p['fee_hi']):
            return False
        if H.interim_rug_risk(P):
            return False
        age = TF.last_available_age_s(P.static('pair'), P.t)
        if age is None or age > p['live_s']:
            return False
        p0, p1 = P('price'), P.ago('price', p['nc_win'])
        if not (fin(p1) and p1 > 0 and fin(p0)):
            return False
        r = 100 * (p0 / p1 - 1)
        if not (p['nc_lo'] <= r <= p['nc_hi']):
            return False
        if p['guard'] and H.rug_guard_v1(P):
            return False
        return True
    return u


def make_signal(**over):
    p = dict(DEFAULTS)
    p.update(over)
    u = universe_nc(p)

    def sig(P):
        if not u(P):
            return False
        fl = TF.flow(P.static('pair'), P.t, p['fl_win'])
        if fl is None or fl['ub'] < p['ub']:
            return False
        if p['use_newb'] and fl['newb'] < p['newb']:
            return False
        if p['use_top'] and not (fin(fl['top_share']) and fl['top_share'] < p['top']):
            return False
        if p['use_net'] and not (fl['net_sol'] > p['net_sol']):
            return False
        return True
    return sig


def make_kwargs(**over):
    e = dict(EXIT_DEFAULTS)
    e.update(over)
    frac, cap = e.pop('size_frac'), e.pop('size_cap')

    def size(P):
        return min(cap, frac * P('liq'))
    e['size_fn'] = size
    return e


def stats(trades, key='usd50'):
    if not trades:
        return {'n': 0, 'pairs': 0, 'mean_usd': None, 'mean_pct': None, 'sum_usd': 0.0, 'win': None, 'top_share': None}
    pk = 'net' + key[3:]
    xs = [x[key] for x in trades]
    cnt = {}
    for x in trades:
        cnt[x['pair']] = cnt.get(x['pair'], 0) + 1
    return {'n': len(xs), 'pairs': len(cnt), 'mean_usd': round(sum(xs) / len(xs), 3),
            'mean_pct': round(sum(x[pk] for x in trades) / len(xs), 3), 'sum_usd': round(sum(xs), 2),
            'win': round(100 * sum(1 for v in xs if v > 0) / len(xs), 1),
            'top_share': round(max(cnt.values()) / len(xs), 3)}


def split(trades, frac=0.6):
    _, meta = H.load()
    cut = H.split_t(meta, frac)
    return [x for x in trades if x['entry_t'] < cut], [x for x in trades if x['entry_t'] >= cut]


def short(x):
    return '%s:%s' % ((x.get('sym') or '?')[:10], x['pair'][:8])
