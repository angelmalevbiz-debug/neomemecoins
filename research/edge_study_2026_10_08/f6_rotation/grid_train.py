"""F6 grid on TRAIN only: entries restricted to decision ticks before the 60% cut; every config counted.
Holdout trades are never generated here."""
import sys, time, json, os
sys.path.insert(0, (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r'')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harness as H
import rotation as R

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
series, meta = H.load()
cut = H.split_t(meta)
mid = meta['t0'] + 0.5 * (cut - meta['t0'])
UNIV = {'U50': R.universe(50_000), 'U100': R.universe(100_000), 'U250': R.universe(250_000)}
SPECS = [('factor', n) for n in ('liq', 'neg_pc1h', 'neg_pc6h', 'pc5', 'ret15', 'neg_ret15', 'vacc', 'liqg15', 'bshare5',
                                 'uw5', 'neg_vol15', 'neg_turn1h')]
SPECS += [('rank', (('liq', 1), ('neg_pc1h', 1))), ('rank', (('liq', 1), ('neg_vol15', 1))), ('random', 'r1')]


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
        print('prep', uname, every, 'ticks', len(prep), 'train universe size med', sizes[len(sizes) // 2], 'p90',
              sizes[int(len(sizes) * .9)], 'secs', round(time.time() - t_start, 1), flush=True)
        for sp in SPECS:
            for K in (1, 3):
                for buf in (0, 2):
                    for stop in (None, -8.0):
                        configs += 1
                        tr = R.run(H, prep, sp, K=K, buffer=buf, stop=stop, max_hold_min=120, entry_to=cut)
                        tr = [x for x in tr if x['entry_t'] < cut]
                        if not tr:
                            continue
                        sm = H.summarize(tr)
                        s0 = H.summarize(tr, 'usd0')
                        h1 = [x['net50'] for x in tr if x['entry_t'] < mid]
                        h2 = [x['net50'] for x in tr if x['entry_t'] >= mid]
                        results.append({'u': uname, 'every': every, 'spec': sname(sp), 'K': K, 'buf': buf, 'stop': stop,
                                        'n': sm['n'], 'pairs': sm['pairs'], 'mean50': sm['mean_pct'], 'med50': sm['median_pct'],
                                        'win': sm['win_rate'], 'pf': sm['pf'], 'sum50': sm['sum_usd'], 'top': sm['top_pair_share'],
                                        'ci': sm['ci95_mean_usd'], 'mean0': s0['mean_pct'], 'hold': sm['avg_hold_min'],
                                        'h1': round(sum(h1) / len(h1), 2) if h1 else None, 'n1': len(h1),
                                        'h2': round(sum(h2) / len(h2), 2) if h2 else None, 'n2': len(h2), 'exits': sm['exits']})
print('configs tried in grid', configs, 'secs', round(time.time() - t_start, 1))
with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'grid_train.json'), 'w') as fh:
    json.dump(results, fh)
fmt = '%-4s e%-2d %-24s K%d b%d stop%-5s n%4d pairs%3d top%.2f mean50 %6.2f med %6.2f win %5.1f pf %-6s sum %8.1f mean0 %6.2f hold %5.1f | h1 %6s (%d) h2 %6s (%d)'


def show(x):
    print(fmt % (x['u'], x['every'], x['spec'], x['K'], x['buf'], x['stop'], x['n'], x['pairs'], x['top'], x['mean50'], x['med50'],
                 x['win'], x['pf'], x['sum50'], x['mean0'], x['hold'], x['h1'], x['n1'], x['h2'], x['n2']))


print('=== random controls (same mechanics) ===')
for x in results:
    if x['spec'].startswith('random'):
        show(x)
print('=== top 40 by train mean net50 (n>=20) ===')
for x in sorted([x for x in results if x['n'] >= 20], key=lambda x: -x['mean50'])[:40]:
    show(x)
print('=== train mean net50 > 0, n >= 30, pairs >= 8, top <= 0.35 ===')
for x in sorted([x for x in results if x['n'] >= 30 and x['pairs'] >= 8 and x['top'] <= 0.35 and x['mean50'] > 0], key=lambda x: -x['mean50']):
    show(x)
# factor-level average across mechanics (robustness view)
agg = {}
for x in results:
    agg.setdefault((x['u'], x['spec']), []).append(x['mean50'])
print('=== average train mean50 across all mechanics, by universe x spec ===')
for k in sorted(agg, key=lambda k: -sum(agg[k]) / len(agg[k])):
    v = agg[k]
    print('%-4s %-26s avg %6.2f min %6.2f max %6.2f (%d cfgs)' % (k[0], k[1], sum(v) / len(v), min(v), max(v), len(v)))
