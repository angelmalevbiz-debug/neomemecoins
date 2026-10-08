"""TRAIN ONLY: rank IC (Spearman) of tape-flow vs DexScreener features with forward returns, in two train halves."""
import math, pickle, sys
sys.path.insert(0, (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r'')
import harness as H

series, meta = H.load()
mid = meta['t0'] + 0.3 * (meta['t1'] - meta['t0'])
rows = pickle.load(open((__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r'/f8_tape/samples.pkl', 'rb'))
rows = [r for r in rows if r['train'] and not r['rug']]
for r in rows:
    su = r['su'] or 0
    liq = r['liq'] or 0
    r['netliq_60'] = r['net_60'] * su / liq * 100 if liq and su else float('nan')
    r['netliq_300'] = r['net_300'] * su / liq * 100 if liq and su else float('nan')
    r['mxbliq_300'] = r['mxb_300'] * su / liq * 100 if liq and su else float('nan')
    r['dsb'] = r['b5'] / (r['b5'] + r['s5']) if (r['b5'] + r['s5']) > 0 else float('nan')
    r['v5liq'] = r['v5'] / liq if liq else float('nan')
    r['ublog_300'] = math.log1p(r['ub_300'])


def rank(v):
    o = sorted(range(len(v)), key=lambda k: v[k])
    r = [0.0] * len(v)
    k = 0
    while k < len(o):
        m = k
        while m + 1 < len(o) and v[o[m + 1]] == v[o[k]]:
            m += 1
        for z in range(k, m + 1):
            r[o[z]] = (k + m) / 2
        k = m + 1
    return r


def ic(rs, f, h):
    pts = [(r[f], r[h]) for r in rs if r[f] is not None and r[f] == r[f] and r[h] is not None]
    if len(pts) < 100:
        return float('nan'), len(pts)
    x = rank([p[0] for p in pts])
    y = rank([p[1] for p in pts])
    n = len(x)
    mx, my = sum(x) / n, sum(y) / n
    c = sum((a - mx) * (b - my) for a, b in zip(x, y))
    vx = sum((a - mx) ** 2 for a in x)
    vy = sum((b - my) ** 2 for b in y)
    return c / math.sqrt(vx * vy), n


A1 = [r for r in rows if r['t'] < mid]
A2 = [r for r in rows if r['t'] >= mid]
TAPE = ['net_60', 'net_300', 'netliq_60', 'netliq_300', 'bshare_60', 'bshare_300', 'ub_60', 'ub_300', 'mxb_300', 'mxbliq_300',
        'n_300', 'rep_300', 'tmom_60', 'tmom_300', 'tmom_900']
DEX = ['pc5', 'pc1h', 'dsb', 'v5liq', 'b5', 'dmom_60', 'dmom_300', 'dmom_900']
print('samples A1', len(A1), 'A2', len(A2))
for h in ('f0_300', 'f0_900', 'f0_1800'):
    print('== horizon', h, '(IC A1 / IC A2 / IC all-train)')
    for grp, feats in (('TAPE', TAPE), ('DEX', DEX)):
        for f in feats:
            a, na = ic(A1, f, h)
            b, nb = ic(A2, f, h)
            c, nc = ic(rows, f, h)
            print('  %-4s %-11s %+.3f (%d) / %+.3f (%d) / %+.3f %s' % (grp, f, a, na, b, nb, c, 'SIGN-STABLE' if a * b > 0 and min(abs(a), abs(b)) >= 0.03 else ''))
