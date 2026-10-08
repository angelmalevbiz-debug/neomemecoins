"""Step 1: (a) re-run the original C1 module under harness_final (as run_all does) and compare with the leaderboard
trades; (b) run the independent re-implementation (both new-buyer semantics) and compare trade sets and metrics."""
import sys
sys.dont_write_bytecode = True
import importlib.util, json, os, pickle, time

DEEP = (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r''
OUT = DEEP + '/verify_f5_flowaccel_leakage_F5_C1_TAPE_BREADTH_NOCHASE'
sys.stdout.reconfigure(encoding='utf-8', errors='replace')


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    sys.modules[name] = m
    spec.loader.exec_module(m)
    return m


H = _load('harness_final', DEEP + '/leaderboard/harness_final.py')
sys.modules['harness'] = H
sys.path.insert(0, OUT)
import indep as I  # noqa: E402

t0 = time.time()
series, meta = H.load()
CUT = H.split_t(meta)
print('loaded', round(time.time() - t0, 1), 's; cut', CUT)
I.load_tape()
print('tape', I.STATS, round(time.time() - t0, 1), 's')


def brief(trades):
    ev = H.evaluate(trades)
    ho = [x for x in trades if x['entry_t'] >= CUT]
    keep = ('n', 'pairs', 'clusters', 'win_rate', 'mean_usd', 'mean_pct', 'median_pct', 'sum_usd', 'pf',
            'ci95_mean_usd', 'ci95_mean_usd_pair', 'top_pair_share', 'trades_per_hour', 'exits')
    return {'train': {k: ev['train'].get(k) for k in keep}, 'holdout': {k: ev['holdout'].get(k) for k in keep},
            'holdout_model': {k: ev['holdout_model'].get(k) for k in ('n', 'mean_usd', 'mean_pct', 'median_pct')},
            'train_model': {k: ev['train_model'].get(k) for k in ('n', 'mean_usd', 'mean_pct')},
            'portfolio_holdout': H.portfolio(ho, slots=3)}


def key(x):
    return (x['pair'], int(x['entry_t']))


res = {}
# (a) original module
orig = _load('f5_orig', DEEP + '/f5_flowaccel/strategies.py')
c1 = [c for c in orig.CONFIGS if c['name'] == 'F5_C1_TAPE_BREADTH_NOCHASE'][0]
t1 = time.time()
tr_orig = H.simulate(c1['signal'], **c1['kwargs'])
print('orig rerun n', len(tr_orig), round(time.time() - t1, 1), 's')
lb = pickle.load(open(DEEP + '/leaderboard/trades/f5_flowaccel.pkl', 'rb'))
lbt = [r for r in lb['rows'] if r['name'] == 'F5_C1_TAPE_BREADTH_NOCHASE' and r['variant'] == 'orig'][0]['trades']
same = len(lbt) == len(tr_orig) and all(key(a) == key(b) and abs(a['net50'] - b['net50']) < 1e-9 for a, b in zip(lbt, tr_orig))
res['orig_rerun_identical_to_leaderboard'] = same
res['orig'] = brief(tr_orig)
print('orig identical to leaderboard:', same)
print(json.dumps(res['orig']['holdout']))

# (b) independent
for nd in ('event', 'wallet'):
    t1 = time.time()
    tr = H.simulate(I.make_signal(H, newdef=nd), **I.EXITS)
    ko = set(map(key, tr_orig))
    ki = set(map(key, tr))
    res['indep_' + nd] = brief(tr)
    res['indep_' + nd]['overlap'] = {'orig_n': len(ko), 'indep_n': len(ki), 'common': len(ko & ki),
                                     'only_orig': len(ko - ki), 'only_indep': len(ki - ko)}
    print('indep', nd, 'n', len(tr), round(time.time() - t1, 1), 's', json.dumps(res['indep_' + nd]['overlap']))
    print('   train', json.dumps(res['indep_' + nd]['train']))
    print('   holdout', json.dumps(res['indep_' + nd]['holdout']))
    print('   holdout_model', res['indep_' + nd]['holdout_model'], 'portfolio', res['indep_' + nd]['portfolio_holdout'])
    with open(os.path.join(OUT, 'trades_indep_%s.pkl' % nd), 'wb') as fh:
        pickle.dump(tr, fh)
    sys.stdout.flush()
with open(os.path.join(OUT, 'trades_orig_rerun.pkl'), 'wb') as fh:
    pickle.dump(tr_orig, fh)
with open(os.path.join(OUT, 'run1.json'), 'w') as fh:
    json.dump(res, fh, indent=1, default=str)
print('done', round(time.time() - t0, 1), 's')
