"""F5 final evaluation: the 3 pre-selected CONFIGS + BASELINES, train and HOLDOUT (first and only holdout look)."""
import sys, os, json, time
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import strategies as S
H = S.H
series, meta = H.load()
CUT = H.split_t(meta)


def robust(tr):
    xs = sorted(x['net50'] for x in tr)
    n = len(xs)
    if n < 6:
        return {'n': n}
    k = max(1, int(n * 0.05))
    trim = xs[k:n - k]
    nv = [x['net50'] for x in tr if x['flag'] != 'VANISHED']
    return {'trim5_mean_pct': round(sum(trim) / len(trim), 3), 'mean_wo_top3_pct': round(sum(xs[:-3]) / (n - 3), 3),
            'mean_wo_vanished_pct': round(sum(nv) / len(nv), 3) if nv else None}


def per_pair(tr):
    by = {}
    for x in tr:
        by.setdefault(x['sym'] + ':' + x['pair'][:8], []).append(x['net50'])
    return sorted(((k, len(v), round(sum(v) / len(v), 2)) for k, v in by.items()), key=lambda z: -z[1])[:10]


out = {}
for c in S.CONFIGS + S.BASELINES:
    t0 = time.time()
    tr = H.simulate(c['signal'], tag=c['name'], **c['kwargs'])
    ev = H.evaluate(tr)
    ho = [x for x in tr if x['entry_t'] >= CUT]
    trn = [x for x in tr if x['entry_t'] < CUT]
    res = {'train': ev['train'], 'holdout': ev['holdout'], 'train_model': ev['train_model'], 'holdout_model': ev['holdout_model'],
           'holdout_portfolio3': H.portfolio(ho, slots=3), 'train_portfolio3': H.portfolio(trn, slots=3),
           'holdout_robust': robust(ho), 'train_robust': robust(trn), 'holdout_pairs_top': per_pair(ho)}
    out[c['name']] = res
    print('=====', c['name'], round(time.time() - t0, 1), 's')
    for part in ('train', 'holdout', 'holdout_model'):
        print('  ', part, json.dumps(res[part]))
    print('   holdout portfolio3', res['holdout_portfolio3'], 'train portfolio3', res['train_portfolio3'])
    print('   robust train', res['train_robust'], 'holdout', res['holdout_robust'])
    print('   holdout per pair', res['holdout_pairs_top'])
    sys.stdout.flush()
# extra random salts for a stabler baseline (holdout only reported)
for name, base in (('tape-live', lambda P: S._universe(P)), ('tape-live+nochase', lambda P: S._universe(P) and S._nochase(P))):
    for salt in ('x1', 'x2', 'x3', 'x4'):
        r = S._rnd(0.006, salt)
        tr = H.simulate(lambda P: base(P) and r(P), **S.EXITS)
        ev = H.evaluate(tr)
        h = ev['holdout']
        print('RANDOM', name, salt, 'holdout n', h.get('n'), 'pairs', h.get('pairs'), 'mean', h.get('mean_pct'), 'median', h.get('median_pct'),
              'win', h.get('win_rate'), '| train mean', ev['train'].get('mean_pct'))
        out.setdefault('random_salts', []).append({'universe': name, 'salt': salt, 'holdout': h, 'train_mean': ev['train'].get('mean_pct')})
with open(os.path.join(HERE, 'final_results.json'), 'w') as fh:
    json.dump(out, fh, indent=1, default=str)
print('saved final_results.json')
