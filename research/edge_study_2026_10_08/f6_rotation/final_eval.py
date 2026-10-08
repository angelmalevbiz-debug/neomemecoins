"""F6 final evaluation: the 3 pre-selected configs on the full span (train + holdout), once.
Controls per config: matched-random (5 salts), random-K rotation with identical mechanics (5 salts) and
'hold the K largest pools' with identical mechanics."""
import sys, os, json, time
sys.path.insert(0, (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r'')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harness as H
import strategies as S

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
R = S.R
series, meta = H.load()
cut = H.split_t(meta)
t_start = time.time()
KEEP = ('n', 'pairs', 'clusters', 'win_rate', 'mean_pct', 'median_pct', 'pf', 'sum_usd', 'ci95_mean_usd', 'trades_per_hour',
        'avg_hold_min', 'top_pair_share', 'exits')


def part(tr, lo, hi):
    return [x for x in tr if lo <= x['entry_t'] < hi]


def brief(sm):
    return {k: sm.get(k) for k in KEEP}


out = {}
for cfg, d in zip(S.CONFIGS, S._DEFS):
    name = cfg['name']
    tr = cfg['run'](H)
    ev = H.evaluate(tr)
    tr_tr, tr_ho = part(tr, 0, cut), part(tr, cut, 1e20)
    res = {'train': brief(ev['train']), 'holdout': brief(ev['holdout']),
           'train_model': brief(ev['train_model']), 'holdout_model': brief(ev['holdout_model']),
           'portfolio_holdout': H.portfolio(tr_ho, slots=3), 'portfolio_train': H.portfolio(tr_tr, slots=3),
           'portfolio_all': H.portfolio(tr, slots=3)}
    prep = S._prep(H, d['u'], d['every'])
    # matched random
    mr = []
    for salt in ('m1', 'm2', 'm3', 'm4', 'm5'):
        b = R.matched_random(H, prep, tr, salt=salt)
        e = H.evaluate(b)
        mr.append({'salt': salt, 'train': brief(e['train']), 'holdout': brief(e['holdout']),
                   'holdout_model': brief(e['holdout_model']), 'portfolio_holdout': H.portfolio(part(b, cut, 1e20), slots=3)})
    res['matched_random'] = mr
    rk = []
    for salt in ('r1', 'r2', 'r3', 'r4', 'r5'):
        b = R.run(H, prep, ('random', salt), K=d['K'], buffer=d['buffer'], stop=None, max_hold_min=d['mh'])
        e = H.evaluate(b)
        rk.append({'salt': salt, 'train': brief(e['train']), 'holdout': brief(e['holdout']),
                   'portfolio_holdout': H.portfolio(part(b, cut, 1e20), slots=3)})
    res['random_k'] = rk
    b = R.run(H, prep, ('factor', 'liq'), K=d['K'], buffer=d['buffer'], stop=None, max_hold_min=d['mh'])
    e = H.evaluate(b)
    res['largest_k'] = {'train': brief(e['train']), 'holdout': brief(e['holdout']),
                        'portfolio_holdout': H.portfolio(part(b, cut, 1e20), slots=3)}
    # per-pair contribution in holdout
    pp = {}
    for x in tr_ho:
        a = pp.setdefault(x['sym'] + ' ' + x['pair'][:8], [0, 0.0])
        a[0] += 1
        a[1] += x['usd50']
    res['holdout_by_pair'] = sorted(([k, v[0], round(v[1], 2)] for k, v in pp.items()), key=lambda r: r[2])
    out[name] = res
    print('==', name, 'secs', round(time.time() - t_start, 1))
    for k in ('train', 'holdout', 'holdout_model'):
        print('  ', k, json.dumps(res[k]))
    print('   portfolio_holdout', res['portfolio_holdout'], 'portfolio_train', res['portfolio_train'])
    print('   matched_random holdout mean_pct', [m['holdout'].get('mean_pct') for m in mr], 'n', [m['holdout'].get('n') for m in mr])
    print('   random_k holdout mean_pct', [m['holdout'].get('mean_pct') for m in rk], 'n', [m['holdout'].get('n') for m in rk])
    print('   largest_k holdout', json.dumps({k: res['largest_k']['holdout'].get(k) for k in ('n', 'pairs', 'mean_pct', 'median_pct', 'sum_usd')}))
    print('   holdout by pair', res['holdout_by_pair'])
with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'final_eval.json'), 'w') as fh:
    json.dump(out, fh, indent=1)
print('done secs', round(time.time() - t_start, 1))
