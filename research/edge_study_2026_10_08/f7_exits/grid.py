"""F7 step 2 (TRAIN ONLY): evaluate the full exit-policy grid per (signal, universe) on train events."""
import pickle, sys, time
sys.path.insert(0, (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r'')
import harness as H
import exitlab as X
from common import UNIVERSES

OUT = (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r'/f7_exits/grid_train.pkl'
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
t0 = time.time()
data = X.load_events()
series, meta = H.load()
cut = H.split_t(meta)
mid_train = meta['t0'] + 0.5 * (cut - meta['t0'])
grid = X.policy_grid()
print('policies per cell', len(grid), {f: sum(1 for k in grid if X.family(k) == f) for f in sorted({X.family(k) for k in grid})})


def agg(rows):
    """rows: list of (net0, net50, usd50, pair, t)."""
    n = len(rows)
    if n == 0:
        return None
    m50 = sum(r[1] for r in rows) / n
    m0 = sum(r[0] for r in rows) / n
    a = [r[1] for r in rows if r[4] < mid_train]
    b = [r[1] for r in rows if r[4] >= mid_train]
    by_pair = {}
    for r in rows:
        by_pair.setdefault(r[3], []).append(r)
    contrib = {p: sum(x[2] for x in v) for p, v in by_pair.items()}
    top_pair = max(contrib, key=lambda p: contrib[p])
    rest = [r[1] for r in rows if r[3] != top_pair]
    cnt_top = max(len(v) for v in by_pair.values())
    usd = sorted(r[2] for r in rows)
    k = max(1, int(round(0.05 * n)))
    gains = sum(u for u in usd if u > 0)
    g = sum(r[1] for r in rows if r[1] > 0)
    l = -sum(r[1] for r in rows if r[1] < 0)
    sx = sorted(r[1] for r in rows)
    return {'n': n, 'pairs': len(by_pair), 'mean50': m50, 'mean0': m0, 'med50': sx[n // 2],
            'win': 100 * sum(1 for r in rows if r[1] > 0) / n, 'pf': g / l if l > 0 else 99.0,
            'meanA': sum(a) / len(a) if a else float('nan'), 'nA': len(a),
            'meanB': sum(b) / len(b) if b else float('nan'), 'nB': len(b),
            'mean_xtop': sum(rest) / len(rest) if rest else float('nan'),
            'top_share': cnt_top / n, 'sum50': sum(usd),
            'top5_share_gains': sum(usd[-k:]) / gains if gains > 0 else 0.0,
            'sum_wo_top5': sum(usd[:-k])}


results = {}
for sig, evs in data['events'].items():
    tr = [i for i, e in enumerate(evs) if e['t'] < cut]
    sub = [evs[i] for i in tr]
    lab = X.Lab(sub)
    idxs = list(range(len(sub)))
    for name, pol in grid.items():
        res = lab.run(pol, idxs)
        for uname, tags in UNIVERSES.items():
            rows = [(r[0], r[1], r[1] * sub[i]['size'] / 100, sub[i]['pair'], sub[i]['t'])
                    for r, i in zip(res, idxs) if sub[i]['tag'] in tags]
            a = agg(rows)
            if a:
                results[(sig, uname, name)] = a
    print('signal', sig, 'train events', len(sub), 'secs', round(time.time() - t0, 1))

with open(OUT, 'wb') as fh:
    pickle.dump(results, fh, protocol=pickle.HIGHEST_PROTOCOL)
print('configs evaluated (signal x universe x policy):', len(results))


def fmt(name, a):
    return ('%-44s n=%4d pr=%3d m50=%7.2f m0=%7.2f med=%6.2f win=%5.1f pf=%5.2f | A=%7.2f(%d) B=%7.2f(%d) xtop=%7.2f '
            'topsh=%.2f sum$=%8.0f top5%%gain=%.2f' % (name, a['n'], a['pairs'], a['mean50'], a['mean0'], a['med50'], a['win'],
                                                     a['pf'], a['meanA'], a['nA'], a['meanB'], a['nB'], a['mean_xtop'],
                                                     a['top_share'], a['sum50'], a['top5_share_gains']))


for sig in data['events']:
    for uname in UNIVERSES:
        cell = {k[2]: v for k, v in results.items() if k[0] == sig and k[1] == uname}
        if not cell:
            continue
        nmax = max(v['n'] for v in cell.values())
        if nmax < 30:
            print('#### %s / %s: n=%d too few train events, skipped' % (sig, uname, nmax))
            continue
        print('#### %s / %s (train n=%d)' % (sig, uname, nmax))
        ranked = sorted(cell.items(), key=lambda kv: -kv[1]['mean50'])
        for name, a in ranked[:8]:
            print('  top ', fmt(name, a))
        fams = {}
        for name, a in ranked:
            fams.setdefault(X.family(name), (name, a))
        for f, (name, a) in sorted(fams.items(), key=lambda kv: -kv[1][1]['mean50']):
            print('  fam ', fmt(name, a))
        rob = [(n_, a) for n_, a in ranked if a['meanA'] > 0 and a['meanB'] > 0 and a['mean_xtop'] > 0 and a['mean50'] > 0]
        print('  robust-positive policies (A>0, B>0, ex-top-pair>0):', len(rob), 'of', len(ranked))
        for name, a in rob[:5]:
            print('  rob ', fmt(name, a))
        for ref in ('FIX sNone tNone h60', 'FIX sNone tNone h240', 'FIX s-5 t10 h60'):
            if ref in cell:
                print('  ref ', fmt(ref, cell[ref]))
print('secs', round(time.time() - t0, 1))
