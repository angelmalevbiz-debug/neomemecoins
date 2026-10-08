"""TRAIN-only checks for the pre-selected configs: P-signal reproduces the grid, robustness, baseline calibration."""
import sys, time
sys.path.insert(0, (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r'')
import harness as H
import strategies as S

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
series, meta = H.load()
cut = H.split_t(meta)
mid = meta['t0'] + 0.5 * (cut - meta['t0'])


def brief(tr):
    sm = H.summarize(tr)
    return {k: sm.get(k) for k in ('n', 'pairs', 'win_rate', 'mean_pct', 'median_pct', 'pf', 'ci95_mean_usd', 'top_pair_share', 'exits')}


for cfg, base in zip(S.CONFIGS, S.BASELINES):
    t0 = time.time()
    tr = [x for x in H.simulate(cfg['signal'], t_to=cut, **cfg['kwargs']) if x['entry_t'] < cut]
    print('==', cfg['name'], round(time.time() - t0, 1), 's')
    print('  train', brief(tr))
    print('  train model mean', H.summarize(tr, 'usd0').get('mean_pct'))
    print('  train 1st half', brief([x for x in tr if x['entry_t'] < mid]))
    print('  train 2nd half', brief([x for x in tr if x['entry_t'] >= mid]))
    bp = {}
    for x in tr:
        bp[x['pair']] = bp.get(x['pair'], 0) + x['usd50']
    top = sorted(bp.items(), key=lambda kv: -kv[1])
    print('  top pairs by $', [(p[:8], round(v, 1)) for p, v in top[:5]], 'worst', [(p[:8], round(v, 1)) for p, v in top[-3:]])
    for drop in (1, 3):
        keep = set(p for p, _ in top[drop:])
        sub = [x for x in tr if x['pair'] in keep]
        print('  drop top-%d pairs: n %d mean50 %.3f' % (drop, len(sub), sum(x['net50'] for x in sub) / max(1, len(sub))))
    btr = [x for x in H.simulate(base['signal'], t_to=cut, **base['kwargs']) if x['entry_t'] < cut]
    print('  baseline', base['name'], 'train', brief(btr))
