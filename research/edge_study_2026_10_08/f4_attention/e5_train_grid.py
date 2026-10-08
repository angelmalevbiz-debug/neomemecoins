"""Run every GRID config; print TRAIN summaries only (holdout is not printed here)."""
import sys, os, time, json
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r'')
import harness as H
import strategies as S

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
series, meta = H.load()
cut = H.split_t(meta)
KEEP = ('n', 'pairs', 'clusters', 'win_rate', 'mean_pct', 'median_pct', 'sum_usd', 'pf', 'ci95_mean_usd',
        'trades_per_hour', 'avg_hold_min', 'top_pair_share', 'exits')
for cfg in S.GRID:
    t0 = time.time()
    tr = H.simulate(cfg['make'](), **cfg['kwargs'])
    train = [x for x in tr if x['entry_t'] < cut]
    s50 = H.summarize(train)
    s0 = H.summarize(train, 'usd0')
    print('==', cfg['name'], '|', cfg['description'], '| secs', round(time.time() - t0, 1))
    print('   TRAIN net50', json.dumps({k: s50.get(k) for k in KEEP}))
    print('   TRAIN net0 ', json.dumps({k: s0.get(k) for k in ('n', 'win_rate', 'mean_pct', 'median_pct', 'sum_usd', 'pf')}))
    print('   TRAIN portfolio(3)', H.portfolio(train, 3))
