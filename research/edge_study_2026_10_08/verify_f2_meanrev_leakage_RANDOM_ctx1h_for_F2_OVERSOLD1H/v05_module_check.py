"""Run this folder's strategies.py under leaderboard/harness_final.py exactly as run_all.py binds it."""
import sys
sys.dont_write_bytecode = True
import importlib.util, pickle, time
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass
DEEP = (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r''
OUT = DEEP + '/verify_f2_meanrev_leakage_RANDOM_ctx1h_for_F2_OVERSOLD1H'


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


t0 = time.time()
H = load('harness_final', DEEP + '/leaderboard/harness_final.py')
sys.modules['harness'] = H
S = load('verify_strats', OUT + '/strategies.py')
with open(OUT + '/lb_trades.pkl', 'rb') as fh:
    lb = pickle.load(fh)['orig']
_, meta = H.load()
cut = H.split_t(meta)
for c in S.CONFIGS + S.BASELINES:
    trs = H.simulate(c['signal'], **c['kwargs'])
    ev = H.evaluate(trs)
    ho = [x for x in trs if x['entry_t'] >= cut]
    t, h, hm = ev['train'], ev['holdout'], ev['holdout_model']
    print('==', c['name'])
    print('   train  n %s mean $ %s mean %% %s' % (t.get('n'), t.get('mean_usd'), t.get('mean_pct')))
    print('   holdout n %s pairs %s clusters %s win %s mean $ %s mean %% %s median %% %s PF %s sum $ %s CI95 %s CI95pair %s top %s tph %s exits %s' % (
        h.get('n'), h.get('pairs'), h.get('clusters'), h.get('win_rate'), h.get('mean_usd'), h.get('mean_pct'),
        h.get('median_pct'), h.get('pf'), h.get('sum_usd'), h.get('ci95_mean_usd'), h.get('ci95_mean_usd_pair'),
        h.get('top_pair_share'), h.get('trades_per_hour'), h.get('exits')))
    print('   holdout model mean %% %s   portfolio(holdout, 3 slots) %s' % (hm.get('mean_pct'), H.portfolio(ho, slots=3)))
    if c['name'].startswith('VERIFY_REIMPL'):
        same = [(x['pair'], x['entry_t'], x['exit_t'], x['reason'], x['net50']) for x in trs] == \
               [(x['pair'], x['entry_t'], x['exit_t'], x['reason'], x['net50']) for x in lb]
        print('   identical to leaderboard trades:', same, len(trs), len(lb))
print('secs', round(time.time() - t0, 1))
