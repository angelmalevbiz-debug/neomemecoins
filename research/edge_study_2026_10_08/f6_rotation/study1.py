"""F6 study 1 (TRAIN ticks only): cross-sectional factor screen.

For every train tick, universe, factor, direction and K: mean forward net50 of the top-K pools vs the
universe mean (= expected value of a random-K pick) at horizons 5/15/30/60 min. No stops, no hysteresis:
this is the raw ranking power after round-trip costs. Every (universe, factor, direction, K, horizon)
cell counts as one configuration tried.
"""
import sys, math, random
from panel import load_panel, ok

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
d = load_panel()
cut = d['cut']
t0 = d['meta']['t0']
rows = [r for r in d['rows'] if r['T'] < cut]
print('train rows', len(rows))


def sfnet(r):
    b, s = r['sf_buy'], r['sf_sell']
    return (b - s) / (b + s) if ok(b) and ok(s) and b + s > 0 else float('nan')


def txn5(r):
    return r['b5'] + r['s5'] if ok(r['b5']) and ok(r['s5']) else float('nan')


def bnet1h(r):
    return (r['b1h'] - r['s1h']) / (r['b1h'] + r['s1h']) if ok(r['b1h']) and ok(r['s1h']) and r['b1h'] + r['s1h'] > 0 else float('nan')


FACT = {k: (lambda r, k=k: r[k]) for k in ('ret1', 'ret5', 'ret15', 'ret60', 'pc5', 'pc1h', 'pc6h', 'pc24', 'vacc', 'vacc1h',
                                             'turn1h', 'bshare5', 'bshare1h', 'liqg5', 'liqg15', 'liqg60', 'vol15', 'uw5',
                                             'rb5', 'liq', 'age', 'score', 'v1h')}
FACT['sfnet'] = sfnet
FACT['txn5'] = txn5

UNIV = {
    'L50': lambda r: (not r['rug']) and r['liq'] >= 50_000 and ok(r['rt']) and r['rt'] <= 4.0,
    'L50_mid': lambda r: (not r['rug']) and r['liq'] >= 50_000 and r['fee'] > 50 and ok(r['rt']) and r['rt'] <= 4.0,
    'L100': lambda r: (not r['rug']) and r['liq'] >= 100_000 and ok(r['rt']) and r['rt'] <= 4.0,
}

by_tick = {}
for r in rows:
    by_tick.setdefault(r['T'], []).append(r)
ticks = sorted(by_tick)
HZ = (300, 900, 1800, 3600)
configs = 0
res = []
for uname, uf in UNIV.items():
    sizes = []
    base = {h: [] for h in HZ}
    tick_u = {}
    for T in ticks:
        u = [r for r in by_tick[T] if uf(r)]
        tick_u[T] = u
        sizes.append(len(u))
    sz = sorted(sizes)
    print('universe', uname, 'size median', sz[len(sz) // 2], 'p10', sz[len(sz) // 10], 'p90', sz[int(len(sz) * .9)])
    for h in HZ:
        xs = []
        for T in ticks:
            v = [r['fw'][h][1] for r in tick_u[T] if r['fw'][h] is not None]
            if len(v) >= 4:
                xs.append(sum(v) / len(v))
        print('   h', h, 'universe mean net50 per tick-avg %.2f over %d ticks' % (sum(xs) / max(1, len(xs)), len(xs)))
    for fname, ff in FACT.items():
        for K in (1, 3):
            for sign in (1, -1):
                for h in HZ:
                    configs += 1
                    top, uni, ex, hits = [], [], [], 0
                    halves = {0: [], 1: []}
                    for T in ticks:
                        u = [r for r in tick_u[T] if r['fw'][h] is not None and ok(ff(r))]
                        if len(u) < max(4, 2 * K + 1):
                            continue
                        u.sort(key=lambda r: -sign * ff(r))
                        tk = [r['fw'][h][1] for r in u[:K]]
                        um = sum(r['fw'][h][1] for r in u) / len(u)
                        tm = sum(tk) / K
                        top.append(tm)
                        uni.append(um)
                        ex.append(tm - um)
                        hits += tm > um
                        halves[0 if T < t0 + 0.5 * (cut - t0) else 1].append(tm)
                    if len(top) < 60:
                        continue
                    res.append({'u': uname, 'f': fname, 'K': K, 'dir': '+' if sign > 0 else '-', 'h': h, 'ticks': len(top),
                                'top': sum(top) / len(top), 'uni': sum(uni) / len(uni), 'excess': sum(ex) / len(ex),
                                'hit': hits / len(top),
                                'h1': sum(halves[0]) / max(1, len(halves[0])), 'h2': sum(halves[1]) / max(1, len(halves[1])),
                                'n1': len(halves[0]), 'n2': len(halves[1])})
print('configs (screen cells) evaluated', configs)
res.sort(key=lambda x: -x['top'])
print('TOP 40 by top-K mean net50 (train):')
for x in res[:40]:
    print('%-8s %-9s K%d %s h%4d ticks %4d topK %6.2f uni %6.2f excess %6.2f hit %.2f | half1 %6.2f (%d) half2 %6.2f (%d)' % (
        x['u'], x['f'], x['K'], x['dir'], x['h'], x['ticks'], x['top'], x['uni'], x['excess'], x['hit'], x['h1'], x['n1'], x['h2'], x['n2']))
print('TOP 30 by excess over universe (train), K=3:')
for x in sorted([x for x in res if x['K'] == 3], key=lambda x: -x['excess'])[:30]:
    print('%-8s %-9s K%d %s h%4d ticks %4d topK %6.2f uni %6.2f excess %6.2f hit %.2f | half1 %6.2f half2 %6.2f' % (
        x['u'], x['f'], x['K'], x['dir'], x['h'], x['ticks'], x['top'], x['uni'], x['excess'], x['hit'], x['h1'], x['h2']))
# positive in both halves
print('cells with topK > 0 in BOTH train halves:')
for x in res:
    if x['h1'] > 0 and x['h2'] > 0:
        print('%-8s %-9s K%d %s h%4d ticks %4d topK %6.2f excess %6.2f half1 %6.2f half2 %6.2f' % (
            x['u'], x['f'], x['K'], x['dir'], x['h'], x['ticks'], x['top'], x['excess'], x['h1'], x['h2']))
