"""Shared helpers for the robustness verification of RANDOM_ctx1h_for_F2_OVERSOLD1H_STAB (PAPER research only).

Binds `harness` to the integrator's audited harness (leaderboard/harness_final.py) exactly as run_all.py does, then
re-implements the f2 random baseline with every threshold exposed as a parameter. With the default parameters the
signal is identical to f2_meanrev.strategies.make_random(0.005, 'f2r1', pc1h_max=-12.0) (checked in stage1).
"""
import sys
sys.dont_write_bytecode = True
import importlib.util, json, math, os, random

DEEP = (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r''
OUT = DEEP + '/verify_f2_meanrev_robustness_RANDOM_ctx1h_for_F2_OVERSOLD1H'
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


if 'harness' in sys.modules and getattr(sys.modules['harness'], 'CALIB_VERSION', None):
    H = sys.modules['harness']
else:
    H = _load_module('harness_final', DEEP + '/leaderboard/harness_final.py')
    sys.modules['harness'] = H
if DEEP not in sys.path:
    sys.path.insert(0, DEEP)

BASE = dict(prob=0.005, salt='f2r1', pc1h_max=-12.0, liq_min=50_000.0)
EXIT_A = dict(tp=20.0, stop=-25.0, hold_min=90.0)


def make_random(prob=0.005, salt='f2r1', pc1h_max=-12.0, liq_min=50_000.0, guard=None):
    """Random entries (hashed coin) in liq >= liq_min + interim rug screen + pc1h <= pc1h_max (same order as f2)."""
    def signal(P):
        if not H.hashed_coin(P.static('pair'), P.t, prob, salt):
            return False
        if not (P('liq') >= liq_min):
            return False
        if H.interim_rug_risk(P):
            return False
        if not (P('pc1h') <= pc1h_max):
            return False
        if guard is not None and guard(P):
            return False
        return True
    return signal


def run(prob=0.005, salt='f2r1', pc1h_max=-12.0, liq_min=50_000.0, tp=20.0, stop=-25.0, hold_min=90.0, guard=None,
        stress_bps=None):
    old = H.STRESS_BPS
    if stress_bps is not None:
        H.STRESS_BPS = stress_bps
    try:
        return H.simulate(make_random(prob, salt, pc1h_max, liq_min, guard), tp=tp, stop=stop, hold_min=hold_min)
    finally:
        H.STRESS_BPS = old


def meta():
    return H.load()[1]


def cut(frac=0.6):
    return H.split_t(meta(), frac)


def net100(x):
    """+50 bps per leg on top of net50 (multiplicative first-order; stage1 checks it against an exact rerun)."""
    return 100 * ((1 + x['net50'] / 100) * (1 - 50 / 1e4) ** 2 - 1)


def stats(trades, key='usd50'):
    """Compact summary of a trade list (stressed basis by default)."""
    if not trades:
        return {'n': 0}
    pk = 'net' + key[3:]
    xs = [x[key] for x in trades]
    pct = [x[pk] for x in trades] if all(pk in x for x in trades) else [100 * v / x['size'] for v, x in zip(xs, trades)]
    cnt, ppnl = {}, {}
    for x in trades:
        cnt[x['pair']] = cnt.get(x['pair'], 0) + 1
        ppnl[x['pair']] = ppnl.get(x['pair'], 0.0) + x[key]
    tot = sum(xs)
    top5 = sum(sorted(xs, reverse=True)[:5])
    g = sum(v for v in xs if v > 0)
    l = -sum(v for v in xs if v < 0)
    lo, hi, ncl = H.cluster_ci(trades, key)
    plo, phi, _ = H.cluster_ci_pair(trades, key)
    srt = sorted(pct)
    n = len(srt)
    med = srt[n // 2] if n % 2 else (srt[n // 2 - 1] + srt[n // 2]) / 2
    return {'n': n, 'pairs': len(cnt), 'clusters': ncl, 'mean_usd': round(tot / n, 3),
            'mean_pct': round(sum(pct) / n, 3), 'median_pct': round(med, 3),
            'win': round(100 * sum(1 for v in xs if v > 0) / n, 1), 'sum_usd': round(tot, 2),
            'pf': round(g / l, 3) if l > 0 else None, 'ci95': [lo, hi], 'ci95_pair': [plo, phi],
            'top_pair_share': round(max(cnt.values()) / n, 3),
            'top_pair_pnl_usd': round(max(ppnl.values()), 2),
            'top5_trades_usd': round(top5, 2),
            'top5_share_of_pnl': (round(top5 / tot, 3) if tot > 0 else None)}


def split(trades, frac=0.6):
    c = cut(frac)
    return [x for x in trades if x['entry_t'] < c], [x for x in trades if x['entry_t'] >= c]


def blocks(trades, hours=3.0, key='usd50'):
    m = meta()
    w = hours * 3.6e6
    nb = int(math.ceil((m['t1'] - m['t0']) / w))
    out = []
    for b in range(nb):
        a, z = m['t0'] + b * w, m['t0'] + (b + 1) * w
        sub = [x for x in trades if a <= x['entry_t'] < z]
        xs = [x[key] for x in sub]
        out.append({'block': b, 'h_from': round(b * hours, 1), 'h_to': round(min((b + 1) * hours, (m['t1'] - m['t0']) / 3.6e6), 2),
                    'n': len(xs), 'pairs': len({x['pair'] for x in sub}),
                    'mean_usd': round(sum(xs) / len(xs), 3) if xs else None, 'sum_usd': round(sum(xs), 2)})
    return out


def drop_best_pair(trades, key='usd50'):
    pp = {}
    for x in trades:
        pp[x['pair']] = pp.get(x['pair'], 0.0) + x[key]
    if not pp:
        return trades, None
    best = max(pp, key=pp.get)
    return [x for x in trades if x['pair'] != best], (best[:8], round(pp[best], 2))


def drop_best_hour(trades, key='usd50'):
    hh = {}
    for x in trades:
        h = int(x['entry_t'] // 3_600_000)
        hh[h] = hh.get(h, 0.0) + x[key]
    if not hh:
        return trades, None
    best = max(hh, key=hh.get)
    return [x for x in trades if int(x['entry_t'] // 3_600_000) != best], (best, round(hh[best], 2))


def drop_top_k(trades, k, key='usd50'):
    srt = sorted(trades, key=lambda x: -x[key])
    drop = set(id(x) for x in srt[:k])
    return [x for x in trades if id(x) not in drop]


def mean(xs):
    xs = [v for v in xs if v is not None]
    return round(sum(xs) / len(xs), 3) if xs else None


def save(name, obj):
    with open(os.path.join(OUT, name), 'w', encoding='utf-8') as fh:
        json.dump(obj, fh, indent=1, default=str)
