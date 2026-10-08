"""Helpers for TRAIN-only analysis of the candidate table (exits replicate harness.simulate logic)."""
import math, pickle, sys
sys.path.insert(0, (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r'')
import harness as H

CANDS = (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r'/f2_meanrev/cands_train.pkl'
series, meta = H.load()
T_END = meta['t1']


def load_cands(path=CANDS):
    with open(path, 'rb') as fh:
        return pickle.load(fh)


def outcome(c, tp, stop, hold):
    s = series[c['pair']]
    n = c['n']
    ks = []
    if stop is not None and c['st_k'].get(stop) is not None:
        ks.append((c['st_k'][stop], 0, 'STOP'))
    if tp is not None and c['tp_k'].get(tp) is not None:
        ks.append((c['tp_k'][tp], 1, 'TP'))
    if c['hd_k'].get(hold) is not None:
        ks.append((c['hd_k'][hold], 2, 'HOLD'))
    if ks:
        k, _, reason = min(ks)
        if k + 1 < n:
            e, px, flag = k + 1, s['price'][k + 1], ''
        else:
            e = k
            flag = 'VANISHED' if T_END - s['t'][e] > 600_000 else 'END'
            px = s['price'][e] * (0.9 if flag == 'VANISHED' else 1.0)
    else:
        e = n - 1
        reason = 'OPEN_AT_END'
        flag = 'VANISHED' if T_END - s['t'][e] > 600_000 else 'END'
        px = s['price'][e] * (0.9 if flag == 'VANISHED' else 1.0)
    v0 = H.exit_value(s, e, c['qty'], 0.0, px) or 0.0
    v50 = H.exit_value(s, e, c['qty50'], H.STRESS_BPS, px) or 0.0
    return 100 * (v0 - 200 - c['netfee']) / 200, 100 * (v50 - 200 - c['netfee']) / 200, reason


def stats(cs, tp, stop, hold, key=1):
    if not cs:
        return None
    xs, pairs, cl = [], {}, {}
    for c in cs:
        r = outcome(c, tp, stop, hold)
        v = r[key]
        xs.append(v)
        pairs.setdefault(c['pair'], []).append(v)
        cl.setdefault((c['pair'], int(c['t'] // 3_600_000)), []).append(v)
    n = len(xs)
    sx = sorted(xs)
    g = sum(x for x in xs if x > 0)
    l = -sum(x for x in xs if x < 0)
    pm = [sum(v) / len(v) for v in pairs.values()]
    top = max(len(v) for v in pairs.values()) / n
    return {'n': n, 'pairs': len(pairs), 'cl': len(cl), 'mean': round(sum(xs) / n, 2), 'med': round(sx[n // 2], 2),
            'win': round(100 * sum(1 for x in xs if x > 0) / n, 1), 'pf': round(g / l, 2) if l > 0 else None,
            'pairmean': round(sum(pm) / len(pm), 2), 'top': round(top, 2)}


def fmt(st):
    if st is None:
        return 'n=0'
    return 'n=%(n)5d pairs=%(pairs)4d cl=%(cl)4d mean=%(mean)7.2f med=%(med)7.2f win=%(win)5.1f pf=%(pf)s pairmean=%(pairmean)6.2f top=%(top).2f' % st


def nz(x, d=0.0):
    return x if (x is not None and x == x) else d
