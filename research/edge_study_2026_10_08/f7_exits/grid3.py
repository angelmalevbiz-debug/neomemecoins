"""F7 step 4 (TRAIN ONLY): full exit grid (base + feature exits) with H.simulate POSITION SEMANTICS
(one position per pair, 300 s cooldown after the exit fill), per universe. Fixes the event-multiplicity
bias of grid.py / grid2.py (overlapping events of the same pump were all counted). Writes grid_train3.pkl."""
import pickle, sys, time
sys.path.insert(0, (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r'')
import harness as H
import exitlab as X
from common import UNIVERSES, size_rule

OUT = (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r'/f7_exits/grid_train3.pkl'
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
t0 = time.time()
data = X.load_events()
series, meta = H.load()
cut = H.split_t(meta)
mid_train = meta['t0'] + 0.5 * (cut - meta['t0'])
UNIS = dict(UNIVERSES)
UNIS['hiall'] = ('hi', 'hi20')
grid = dict(X.policy_grid())
grid.update(X.feature_grid())
print('policies per cell', len(grid))
exec(open((__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r'/f7_exits/aggfmt.py').read())

# ---- validation of the non-overlap replay against H.simulate (random events, all universes, full span)
rnd = data['events']['rnd']
lab = X.Lab(rnd)
order = sorted(range(len(rnd)), key=lambda i: rnd[i]['t'])
want = {(e['pair'], e['i']) for e in rnd}
pol = (('S', -8), ('T', 20), ('H', 120))
mine = lab.run_nonoverlap(pol, order)
tr = H.simulate(lambda P: (P.static('pair'), P.i) in want, stop=-8, tp=20, hold_min=120, cooldown_s=300, size_fn=size_rule)
a = sorted((rnd[i]['pair'], rnd[i]['t'], round(r50, 6)) for i, _, r50 in mine)
b = sorted((x['pair'], x['entry_t'], round(x['net50'], 6)) for x in tr)
print('validation non-overlap: lab trades', len(a), 'simulate trades', len(b), 'identical', a == b)

results = {}
for sig, evs in data['events'].items():
    tr_idx = sorted((i for i, e in enumerate(evs) if e['t'] < cut), key=lambda i: evs[i]['t'])
    sub = [evs[i] for i in tr_idx]
    lab = X.Lab(sub)
    for uname, tags in UNIS.items():
        idxs = [i for i in range(len(sub)) if sub[i]['tag'] in tags]
        if not idxs:
            continue
        for name, pol in grid.items():
            res = lab.run_nonoverlap(pol, idxs)
            rows = [(r0, r50, r50 * sub[i]['size'] / 100, sub[i]['pair'], sub[i]['t']) for i, r0, r50 in res]
            a = agg(rows)
            if a:
                results[(sig, uname, name)] = a
    print('signal', sig, 'train events', len(sub), 'secs', round(time.time() - t0, 1))

with open(OUT, 'wb') as fh:
    pickle.dump(results, fh, protocol=pickle.HIGHEST_PROTOCOL)
print('configs evaluated (signal x universe x policy):', len(results))

for sig in data['events']:
    for uname in UNIS:
        cell = {k[2]: v for k, v in results.items() if k[0] == sig and k[1] == uname}
        if not cell:
            continue
        nmax = max(v['n'] for v in cell.values())
        if nmax < 30:
            print('#### %s / %s: n=%d too few train trades, skipped' % (sig, uname, nmax))
            continue
        print('#### %s / %s (max train n=%d)' % (sig, uname, nmax))
        ranked = sorted(cell.items(), key=lambda kv: -kv[1]['mean50'])
        for name, a in ranked[:5]:
            print('  top ', fmt(name, a))
        fams = {}
        for name, a in ranked:
            fams.setdefault(X.family(name), (name, a))
        for f, (name, a) in sorted(fams.items(), key=lambda kv: -kv[1][1]['mean50']):
            print('  fam ', fmt(name, a))
        rob = [(n_, a) for n_, a in ranked if a['meanA'] > 0 and a['meanB'] > 0 and a['mean_xtop'] > 0 and a['mean50'] > 0
               and a['n'] >= 30 and a['pairs'] >= 8 and a['top_share'] <= 0.35]
        print('  robust-positive (A>0,B>0,ex-top-pair>0,n>=30,pairs>=8,topsh<=.35):', len(rob), 'of', len(ranked))
        for name, a in rob[:5]:
            print('  rob ', fmt(name, a))
        for ref in ('FIX sNone tNone h60', 'FIX sNone tNone h240', 'FIX s-5 t10 h60'):
            if ref in cell:
                print('  ref ', fmt(ref, cell[ref]))
print('secs', round(time.time() - t0, 1))
