"""F7 step 3 (TRAIN ONLY): feature-based exits (momentum decay, sell dominance, armed momentum decay,
pool-invariant drop = LP pull, liquidity drop) on every cell, plus the base grid on the 'hiall' universe
(hi + hi20). Same aggregation as grid.py. Writes grid_train2.pkl."""
import pickle, sys, time
sys.path.insert(0, (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r'')
sys.argv = sys.argv[:1]
import harness as H
import exitlab as X
from common import UNIVERSES

OUT = (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r'/f7_exits/grid_train2.pkl'
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
t0 = time.time()
data = X.load_events()
series, meta = H.load()
cut = H.split_t(meta)
mid_train = meta['t0'] + 0.5 * (cut - meta['t0'])
fgrid = X.feature_grid()
bgrid = X.policy_grid()
UNIS = dict(UNIVERSES)
UNIS['hiall'] = ('hi', 'hi20')
print('feature policies per cell', len(fgrid), 'base policies (hiall only)', len(bgrid))

exec(open((__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r'/f7_exits/aggfmt.py').read())

results = {}
for sig, evs in data['events'].items():
    tr = [i for i, e in enumerate(evs) if e['t'] < cut]
    sub = [evs[i] for i in tr]
    lab = X.Lab(sub)
    idxs = list(range(len(sub)))
    jobs = [(name, pol, list(UNIS)) for name, pol in fgrid.items()] + [(name, pol, ['hiall']) for name, pol in bgrid.items()]
    for name, pol, unames in jobs:
        res = lab.run(pol, idxs)
        for uname in unames:
            tags = UNIS[uname]
            rows = [(r[0], r[1], r[1] * sub[i]['size'] / 100, sub[i]['pair'], sub[i]['t'])
                    for r, i in zip(res, idxs) if sub[i]['tag'] in tags]
            a = agg(rows)
            if a:
                results[(sig, uname, name)] = a
    print('signal', sig, 'train events', len(sub), 'secs', round(time.time() - t0, 1))

with open(OUT, 'wb') as fh:
    pickle.dump(results, fh, protocol=pickle.HIGHEST_PROTOCOL)
print('configs evaluated (signal x universe x policy):', len(results))

base = pickle.load(open(OUT.replace('grid_train2', 'grid_train'), 'rb'))
for sig in data['events']:
    for uname in UNIS:
        cell = {k[2]: v for k, v in results.items() if k[0] == sig and k[1] == uname}
        if not cell:
            continue
        nmax = max(v['n'] for v in cell.values())
        if nmax < 30:
            print('#### %s / %s: n=%d too few train events, skipped' % (sig, uname, nmax))
            continue
        bcell = {k[2]: v for k, v in base.items() if k[0] == sig and k[1] == uname}
        bbest = max(bcell.items(), key=lambda kv: kv[1]['mean50']) if bcell else None
        print('#### %s / %s (train n=%d)%s' % (sig, uname, nmax, ('  base-grid best: ' + fmt(*bbest)) if bbest else ''))
        ranked = sorted(cell.items(), key=lambda kv: -kv[1]['mean50'])
        for name, a in ranked[:6]:
            print('  top ', fmt(name, a))
        fams = {}
        for name, a in ranked:
            fams.setdefault(X.family(name), (name, a))
        for f, (name, a) in sorted(fams.items(), key=lambda kv: -kv[1][1]['mean50']):
            print('  fam ', fmt(name, a))
        rob = [(n_, a) for n_, a in ranked if a['meanA'] > 0 and a['meanB'] > 0 and a['mean_xtop'] > 0 and a['mean50'] > 0]
        print('  robust-positive policies (A>0, B>0, ex-top-pair>0):', len(rob), 'of', len(ranked))
        for name, a in rob[:6]:
            print('  rob ', fmt(name, a))
print('secs', round(time.time() - t0, 1))
