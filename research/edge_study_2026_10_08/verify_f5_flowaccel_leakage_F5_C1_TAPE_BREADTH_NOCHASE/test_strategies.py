import sys
sys.dont_write_bytecode = True
import importlib.util, json
DEEP = (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r''
def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path); m = importlib.util.module_from_spec(spec)
    sys.modules[name] = m; spec.loader.exec_module(m); return m
H = _load('harness_final', DEEP + '/leaderboard/harness_final.py'); sys.modules['harness'] = H
S = _load('verify_strats', DEEP + '/verify_f5_flowaccel_leakage_F5_C1_TAPE_BREADTH_NOCHASE/strategies.py')
assert S.H is H
for c in S.CONFIGS + S.BASELINES:
    tr = H.simulate(c['signal'], **c['kwargs'])
    ev = H.evaluate(tr)
    h, t = ev['holdout'], ev['train']
    _, meta = H.load(); cut = H.split_t(meta)
    print(c['name'], 'train', t.get('n'), t.get('mean_usd'), t.get('mean_pct'), '| holdout', h.get('n'), h.get('pairs'), h.get('mean_usd'), h.get('mean_pct'), h.get('median_pct'), h.get('win_rate'), h.get('pf'), h.get('ci95_mean_usd'), h.get('ci95_mean_usd_pair'), h.get('top_pair_share'), h.get('trades_per_hour'), h.get('exits'), '| model', ev['holdout_model'].get('mean_pct'), '| pf3', H.portfolio([x for x in tr if x['entry_t'] >= cut], slots=3))
