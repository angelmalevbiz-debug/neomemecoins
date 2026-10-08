"""Stage 2: one-at-a-time threshold perturbations (+-20 % and +-30 %) of every parameter.

For each perturbation: (a) the exact original draw (salt f2r1, p=0.005), (b) 10 seeds pooled (f2r1 + rob00..rob08),
(c) the dense version (p=1: enter whenever eligible after the cooldown). Usage: stage2.py part1|part2|base
"""
import sys, time
sys.dont_write_bytecode = True
sys.path.insert(0, (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r'/verify_f2_meanrev_robustness_RANDOM_ctx1h_for_F2_OVERSOLD1H')
import rcommon as R
H = R.H

BASEP = dict(pc1h_max=-12.0, liq_min=50_000.0, tp=20.0, stop=-25.0, hold_min=90.0)
MULTS = (0.7, 0.8, 1.2, 1.3)
PARAMS = {'part1': ['pc1h_max', 'liq_min', 'tp'], 'part2': ['stop', 'hold_min'], 'base': []}
SEEDS = ['f2r1'] + ['rob%02d' % k for k in range(9)]

part = sys.argv[1]
T0 = time.time()
H.load()
rows = []
todo = [('BASE', 1.0, dict(BASEP))] if part == 'base' else []
for name in PARAMS[part]:
    for m in MULTS:
        p = dict(BASEP)
        p[name] = round(BASEP[name] * m, 4)
        todo.append((name, m, p))
for name, m, p in todo:
    t1 = time.time()
    seeds = []
    pool = []
    for s in SEEDS:
        tt = R.run(prob=0.005, salt=s, **p)
        tr, ho = R.split(tt)
        seeds.append({'salt': s, 'train_mean_usd': R.stats(tr).get('mean_usd'), 'holdout_n': len(ho),
                      'holdout_pairs': len({x['pair'] for x in ho}), 'holdout_mean_usd': R.stats(ho).get('mean_usd')})
        if s == 'f2r1':
            hh, _ = R.drop_best_hour(ho)
            hp, _ = R.drop_best_pair(ho)
            st = R.stats(ho)
            orig = {'train': R.stats(tr), 'holdout': st, 'holdout_drop_best_hour_mean_usd': R.stats(hh).get('mean_usd'),
                    'holdout_drop_best_pair_mean_usd': R.stats(hp).get('mean_usd'),
                    'ho50_mean_usd': R.stats(R.split(tt, 0.5)[1]).get('mean_usd'),
                    'ho30_mean_usd': R.stats(R.split(tt, 0.7)[1]).get('mean_usd'),
                    'blocks_pos': sum(1 for b in R.blocks(tt) if (b['mean_usd'] or -1) > 0),
                    'gate_shape': bool(st.get('n', 0) >= 20 and (st.get('pairs') or 0) >= 8 and (st.get('mean_usd') or -1) > 0
                                       and (R.stats(tr).get('mean_usd') or -1) > 0 and (st.get('top_pair_share') or 1) <= 0.35)}
        pool.extend(tt)
    ptr, pho = R.split(pool)
    dense = R.run(prob=1.0, salt='f2r1', **p)
    dtr, dho = R.split(dense)
    row = {'param': name, 'mult': m, 'params': p, 'orig_draw': orig,
           'seeds10': {'train_mean_usd': R.stats(ptr).get('mean_usd'), 'holdout_mean_usd': R.stats(pho).get('mean_usd'),
                       'holdout_model_mean_usd': R.stats(pho, 'usd0').get('mean_usd'),
                       'share_holdout_pos': round(sum(1 for r in seeds if (r['holdout_mean_usd'] or -1) > 0) / len(seeds), 2),
                       'share_train_and_holdout_pos': round(sum(1 for r in seeds if (r['holdout_mean_usd'] or -1) > 0 and (r['train_mean_usd'] or -1) > 0) / len(seeds), 2),
                       'per_seed': seeds},
           'dense_p1': {'train': R.stats(dtr), 'holdout': R.stats(dho),
                        'blocks_pos': sum(1 for b in R.blocks(dense) if (b['mean_usd'] or -1) > 0)}}
    rows.append(row)
    o = orig
    print('%-9s x%.1f %-9s | f2r1 tr %8s ho n%2d p%2d %8s (-bestHr %7s) gate %s | 10 seeds tr %8s ho %8s pos %.2f | dense tr %8s ho %8s n%d  [%.0fs]' % (
        name, m, p[name] if name != 'BASE' else '', o['train'].get('mean_usd'), o['holdout'].get('n', 0), o['holdout'].get('pairs') or 0,
        o['holdout'].get('mean_usd'), o['holdout_drop_best_hour_mean_usd'], o['gate_shape'],
        row['seeds10']['train_mean_usd'], row['seeds10']['holdout_mean_usd'], row['seeds10']['share_holdout_pos'],
        row['dense_p1']['train'].get('mean_usd'), row['dense_p1']['holdout'].get('mean_usd'), row['dense_p1']['holdout'].get('n', 0),
        time.time() - t1), flush=True)
R.save('stage2_%s.json' % part, rows)
print('done %.1fs' % (time.time() - T0))
