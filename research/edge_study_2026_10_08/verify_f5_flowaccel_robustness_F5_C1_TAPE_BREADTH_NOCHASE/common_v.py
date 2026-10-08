"""Shared setup for the adversarial robustness verification of F5_C1_TAPE_BREADTH_NOCHASE (PAPER research only).

Binds `harness` to the FINAL integrator harness (leaderboard/harness_final.py) exactly as run_all.py does, loads the
f5 family module unchanged, and exposes a parametrised replica of sig_c1 whose default parameters reproduce the
original signal trade-for-trade (checked in stage 'base').
"""
import sys
sys.dont_write_bytecode = True
import importlib.util, os, pickle, random

DEEP = (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r''
LB = DEEP + '/leaderboard'
HERE = DEEP + '/verify_f5_flowaccel_robustness_F5_C1_TAPE_BREADTH_NOCHASE'
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass


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
for p in (DEEP, DEEP + '/f5_flowaccel'):
    if p not in sys.path:
        sys.path.insert(0, p)
S = _load_module('f5_strategies_v', DEEP + '/f5_flowaccel/strategies.py')
TF = S.TF

BASE = dict(ub=10, newb=5, top=0.3, nc_lo=-2.0, nc_hi=5.0, liq=20_000.0, fee_lo=52.5, fee_hi=125.0, live=600.0,
            win=300.0, nc_win=300.0, stop=-10.0, tp=6.0, hold=30.0, cooldown=300.0, size_frac=0.002, size_cap=200.0,
            rug='interim')


def _fin(x):
    return x == x and x not in (float('inf'), float('-inf'))


def make_signal(p):
    """Replica of S.sig_c1 with every threshold exposed (same evaluation order)."""
    ub, newb, top = p['ub'], p['newb'], p['top']
    nc_lo, nc_hi, liq_min = p['nc_lo'], p['nc_hi'], p['liq']
    fee_lo, fee_hi, live, win, nc_win = p['fee_lo'], p['fee_hi'], p['live'], p['win'], p['nc_win']
    rug = p.get('rug', 'interim')

    def rugf(P):
        if rug == 'interim':
            return H.interim_rug_risk(P)
        if rug == 'train_only':
            return H.rug_risk_train_only(P)
        if rug == 'interim+guard':
            return H.interim_rug_risk(P) or H.rug_guard_v1(P)
        raise ValueError(rug)

    def sig(P):
        if not (P('liq') >= liq_min):
            return False
        if not (fee_lo <= P.fee_bps() <= fee_hi):
            return False
        if rugf(P):
            return False
        pair = P.static('pair')
        age = TF.last_available_age_s(pair, P.t)
        if age is None or age > live:
            return False
        p0, p1 = P('price'), P.ago('price', nc_win)
        if not (_fin(p1) and p1 > 0 and _fin(p0)):
            return False
        r = 100 * (p0 / p1 - 1)
        if not (nc_lo <= r <= nc_hi):
            return False
        f = TF.flow(pair, P.t, win)
        return (f is not None and f['ub'] >= ub and f['newb'] >= newb and _fin(f['top_share'])
                and f['top_share'] < top and f['net_sol'] > 0)
    return sig


def make_universe_nochase(p):
    """The baseline universe: everything in C1 except the tape-breadth flow condition."""
    liq_min, fee_lo, fee_hi, live = p['liq'], p['fee_lo'], p['fee_hi'], p['live']
    nc_lo, nc_hi, nc_win = p['nc_lo'], p['nc_hi'], p['nc_win']

    def u(P):
        if not (P('liq') >= liq_min):
            return False
        if not (fee_lo <= P.fee_bps() <= fee_hi):
            return False
        if H.interim_rug_risk(P):
            return False
        age = TF.last_available_age_s(P.static('pair'), P.t)
        if age is None or age > live:
            return False
        p0, p1 = P('price'), P.ago('price', nc_win)
        if not (_fin(p1) and p1 > 0 and _fin(p0)):
            return False
        r = 100 * (p0 / p1 - 1)
        return nc_lo <= r <= nc_hi
    return u


def make_kwargs(p):
    cap, frac = p['size_cap'], p['size_frac']

    def size(P):
        return min(cap, frac * P('liq'))
    return dict(stop=p['stop'], tp=p['tp'], hold_min=p['hold'], size_fn=size, cooldown_s=p['cooldown'])


def run(p, signal=None):
    sig = signal if signal is not None else make_signal(p)
    return H.simulate(sig, **make_kwargs(p))


# ------------------------------------------------------------------ stats helpers
def meta():
    return H.load()[1]


def split(trades, frac=0.6):
    cut = H.split_t(meta(), frac)
    return [x for x in trades if x['entry_t'] < cut], [x for x in trades if x['entry_t'] >= cut]


def mean(xs):
    return sum(xs) / len(xs) if xs else float('nan')


def brief(trades, key='usd50'):
    if not trades:
        return {'n': 0}
    pk = 'net' + key[3:]
    xs = [x[key] for x in trades]
    pct = [x[pk] for x in trades]
    pairs = {}
    for x in trades:
        pairs[x['pair']] = pairs.get(x['pair'], 0) + 1
    lo, hi, _ = H.cluster_ci_pair(trades, key)
    return {'n': len(xs), 'pairs': len(pairs), 'mean_usd': round(mean(xs), 3), 'mean_pct': round(mean(pct), 3),
            'sum_usd': round(sum(xs), 2), 'win': round(100 * sum(1 for v in xs if v > 0) / len(xs), 1),
            'top_pair_share': round(max(pairs.values()) / len(xs), 3), 'ci_pair': [lo, hi]}


def save(name, obj):
    with open(os.path.join(HERE, name), 'wb') as fh:
        pickle.dump(obj, fh, protocol=pickle.HIGHEST_PROTOCOL)


def load_pk(name):
    with open(os.path.join(HERE, name), 'rb') as fh:
        return pickle.load(fh)
