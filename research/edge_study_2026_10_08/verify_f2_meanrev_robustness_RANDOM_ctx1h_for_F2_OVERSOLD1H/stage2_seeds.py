"""Stage 2: is the row's positive holdout a property of the context or of the particular coin draw?
(a) the 5 required alternative salts, (b) a 100-salt null distribution of the same random procedure,
(c) the dense population estimator (enter whenever eligible, p=1) and p=0.05,
(d) the same random procedure WITHOUT the pc1h<=-12% context (universe-only), 30 salts."""
import time
from vcommon import *

t0 = time.time()
H.load()
res = {'named': {}, 'dist': [], 'dense': {}, 'universe_only': []}

ORIG = 'f2r1'
for salt in (ORIG, 'vA', 'vB', 'vC', 'vD', 'vE'):
    tr = sim(make_rand(salt=salt))
    e = evaluate(tr)
    _, ho, _, _ = split(tr)
    e['portfolio_holdout'] = H.portfolio(ho, slots=3)
    e['drop_best_holdout'] = drop_best(ho)
    res['named'][salt] = e
    print('salt %-5s' % salt, short(e), flush=True)

for k in range(100):
    salt = 'null%03d' % k
    tr = sim(make_rand(salt=salt))
    tr_, ho, _, _ = split(tr)
    res['dist'].append({'salt': salt, 'train_n': len(tr_), 'train_mean_usd': mean_usd(tr_), 'holdout_n': len(ho),
                        'holdout_pairs': len({x['pair'] for x in ho}), 'holdout_mean_usd': mean_usd(ho),
                        'holdout_mean_usd_model': mean_usd(ho, 'usd0'), 'all_mean_usd': mean_usd(tr)})
print('null dist done', round(time.time() - t0, 1), flush=True)

for p in (1.0, 0.05):
    tr = sim(make_rand(prob=p))
    e = evaluate(tr)
    e['model'] = evaluate(tr, key='usd0')
    e['blocks_3h'] = blocks(tr, 3.0)
    e['split_0.5'] = evaluate(tr, 0.5)
    e['split_0.7'] = evaluate(tr, 0.7)
    _, ho, _, _ = split(tr)
    e['drop_best_holdout'] = drop_best(ho)
    e['portfolio_holdout'] = H.portfolio(ho, slots=3)
    res['dense'][str(p)] = e
    print('dense p=%s' % p, short(e), flush=True)
    print('   model', short(e['model']))
    print('   split .5', short(e['split_0.5']))
    print('   split .7', short(e['split_0.7']))
    for b in e['blocks_3h']:
        print('   block', b)

for k in range(30):
    salt = 'uni%03d' % k
    tr = sim(make_rand(prob=0.0005, salt=salt, pc1h_max=None))
    tr_, ho, _, _ = split(tr)
    res['universe_only'].append({'salt': salt, 'train_n': len(tr_), 'train_mean_usd': mean_usd(tr_),
                                 'holdout_n': len(ho), 'holdout_mean_usd': mean_usd(ho)})
print('universe-only done', round(time.time() - t0, 1), flush=True)


def dsum(rows, f):
    v = sorted(r[f] for r in rows if r[f] is not None)
    if not v:
        return {}
    n = len(v)
    return {'n': n, 'mean': round(sum(v) / n, 3), 'median': round(v[n // 2], 3), 'p10': round(v[int(n * .1)], 3),
            'p90': round(v[int(n * .9)], 3), 'min': round(v[0], 3), 'max': round(v[-1], 3),
            'share_positive': round(sum(1 for x in v if x > 0) / n, 3)}


ROW_HO = 5.974
res['dist_summary'] = {
    'holdout_mean_usd': dsum(res['dist'], 'holdout_mean_usd'),
    'train_mean_usd': dsum(res['dist'], 'train_mean_usd'),
    'all_mean_usd': dsum(res['dist'], 'all_mean_usd'),
    'holdout_n': dsum(res['dist'], 'holdout_n'),
    'share_salts_holdout_ge_row': round(sum(1 for r in res['dist'] if (r['holdout_mean_usd'] or -1e9) >= ROW_HO)
                                        / len(res['dist']), 3),
    'share_salts_train_pos_and_holdout_pos': round(sum(1 for r in res['dist'] if (r['holdout_mean_usd'] or -1) > 0
                                                       and (r['train_mean_usd'] or -1) > 0) / len(res['dist']), 3),
    'share_salts_pass_n_pairs': round(sum(1 for r in res['dist'] if r['holdout_n'] >= 20 and r['holdout_pairs'] >= 8)
                                      / len(res['dist']), 3),
}
res['universe_only_summary'] = {'holdout_mean_usd': dsum(res['universe_only'], 'holdout_mean_usd'),
                                'train_mean_usd': dsum(res['universe_only'], 'train_mean_usd'),
                                'holdout_n': dsum(res['universe_only'], 'holdout_n')}
print(json.dumps(res['dist_summary'], indent=1))
print(json.dumps(res['universe_only_summary'], indent=1))
res['simulate_calls'] = CALLS['simulate']
res['seconds'] = round(time.time() - t0, 1)
dump('stage2_seeds.json', res)
print('done', res['seconds'], 's, simulate calls', CALLS['simulate'])
