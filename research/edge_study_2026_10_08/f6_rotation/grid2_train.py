"""F6 grid 2 on TRAIN only: established-pool universes, no max hold, market-level gates. No stops
(grid 1 showed the -8% net stop hurt every factor through shake-outs and gap-through fills)."""
import sys, time, json, os
sys.path.insert(0, (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r'')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harness as H
import rotation as R

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
series, meta = H.load()
cut = H.split_t(meta)
mid = meta['t0'] + 0.5 * (cut - meta['t0'])
UNIV = {'U50': R.universe(50_000), 'U100': R.universe(100_000),
        'U50old': R.universe(50_000, min_age=1440), 'U100old': R.universe(100_000, min_age=1440)}
SPECS = [('factor', 'liq'), ('factor', 'neg_pc6h'), ('factor', 'neg_vol15'), ('rank', (('liq', 1), ('neg_pc1h', 1))),
         ('rank', (('liq', 1), ('neg_vol15', 1))), ('random', 'r1')]
GATES = {'none': None, 'breadth15': R.gate_breadth15(0.5), 'medpc1h': R.gate_median_pc1h(0.0)}


def sname(sp):
    if sp[0] == 'rank':
        return 'rank:' + '+'.join(n for n, w in sp[1])
    return sp[0] + ':' + str(sp[1])


t_start = time.time()
results = []
configs = 0
for uname, uf in UNIV.items():
    for every in (5, 15):
        prep = R.prepare(H, uf, every_min=every)
        sizes = sorted(len(c) for T, c in prep if T < cut)
        print('prep', uname, every, 'train universe size med', sizes[len(sizes) // 2], 'secs', round(time.time() - t_start, 1), flush=True)
        for sp in SPECS:
            for mh in (120, None):
                for gname, g in GATES.items():
                    configs += 1
                    tr = R.run(H, prep, sp, K=3, buffer=2, stop=None, max_hold_min=mh, entry_to=cut, gate=g)
                    tr = [x for x in tr if x['entry_t'] < cut]
                    if not tr:
                        continue
                    sm = H.summarize(tr)
                    s0 = H.summarize(tr, 'usd0')
                    h1 = [x['net50'] for x in tr if x['entry_t'] < mid]
                    h2 = [x['net50'] for x in tr if x['entry_t'] >= mid]
                    results.append({'u': uname, 'every': every, 'spec': sname(sp), 'mh': mh, 'gate': gname,
                                    'n': sm['n'], 'pairs': sm['pairs'], 'mean50': sm['mean_pct'], 'med50': sm['median_pct'],
                                    'win': sm['win_rate'], 'pf': sm['pf'], 'sum50': sm['sum_usd'], 'top': sm['top_pair_share'],
                                    'ci': sm['ci95_mean_usd'], 'mean0': s0['mean_pct'], 'hold': sm['avg_hold_min'],
                                    'h1': round(sum(h1) / len(h1), 2) if h1 else None, 'n1': len(h1),
                                    'h2': round(sum(h2) / len(h2), 2) if h2 else None, 'n2': len(h2), 'exits': sm['exits']})
print('configs tried in grid2', configs, 'secs', round(time.time() - t_start, 1))
with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'grid2_train.json'), 'w') as fh:
    json.dump(results, fh)
fmt = '%-7s e%-2d %-24s mh%-4s gate %-9s n%4d pairs%3d top%.2f mean50 %6.2f med %6.2f win %5.1f pf %-6s sum %8.1f mean0 %6.2f hold %6.1f ci %s | h1 %6s (%d) h2 %6s (%d)'


def show(x):
    print(fmt % (x['u'], x['every'], x['spec'], x['mh'], x['gate'], x['n'], x['pairs'], x['top'], x['mean50'], x['med50'],
                 x['win'], x['pf'], x['sum50'], x['mean0'], x['hold'], x['ci'], x['h1'], x['n1'], x['h2'], x['n2']))


for x in sorted(results, key=lambda x: (x['u'], x['every'], x['spec'], str(x['mh']), x['gate'])):
    show(x)
print('=== train mean net50 > 0, n >= 20, pairs >= 8, top <= 0.35 ===')
for x in sorted([x for x in results if x['n'] >= 20 and x['pairs'] >= 8 and x['top'] <= 0.35 and x['mean50'] > 0], key=lambda x: -x['mean50']):
    show(x)
agg = {}
for x in results:
    agg.setdefault(('gate', x['gate']), []).append(x['mean50'])
    agg.setdefault(('mh', str(x['mh'])), []).append(x['mean50'])
    agg.setdefault(('spec', x['spec']), []).append(x['mean50'])
    agg.setdefault(('u', x['u']), []).append(x['mean50'])
print('=== marginal averages ===')
for k in sorted(agg):
    v = agg[k]
    print(k, 'avg %.2f (%d)' % (sum(v) / len(v), len(v)))
