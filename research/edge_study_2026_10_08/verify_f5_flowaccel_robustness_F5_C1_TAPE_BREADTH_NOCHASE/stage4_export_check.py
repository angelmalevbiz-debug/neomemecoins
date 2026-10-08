"""Stage 4: load strategies.py the way run_all.py does (harness -> harness_final) and print the full metric set for the
re-exported config and its 5 baselines; portfolio(slots=3) on holdout and full sample."""
import sys, json, time, pickle
sys.path.insert(0, (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r'/verify_f5_flowaccel_robustness_F5_C1_TAPE_BREADTH_NOCHASE')
import common_v as C

H = C.H
M = C._load_module('vf5_export', C.HERE + '/strategies.py')
series, meta = H.load()
cut = H.split_t(meta)
base = C.load_pk('stage1.pkl')['runs']['BASE']['trades']
key = lambda x: (x['pair'], x['entry_t'], x['exit_t'], round(x['net50'], 9))
out = {}
for c in M.CONFIGS + M.BASELINES:
    tr = H.simulate(c['signal'], **c['kwargs'])
    ev = H.evaluate(tr)
    ho = [x for x in tr if x['entry_t'] >= cut]
    r = {'train': ev['train'], 'holdout': ev['holdout'], 'holdout_model': ev['holdout_model'],
         'portfolio_holdout': H.portfolio(ho, slots=3), 'portfolio_full': H.portfolio(tr, slots=3)}
    if c in M.CONFIGS:
        r['identical_to_original'] = [key(x) for x in tr] == [key(x) for x in base]
    out[c['name']] = r
    print('=====', c['name'], '| identical to original:', r.get('identical_to_original'))
    for k in ('train', 'holdout', 'holdout_model'):
        h = r[k]
        print('  %-14s n=%s pairs=%s clusters=%s win=%s mean$=%s mean%%=%s median%%=%s pf=%s sum$=%s ci(pair,hour)=%s ci(pair)=%s top_pair=%s tph=%s exits=%s' % (
            k, h.get('n'), h.get('pairs'), h.get('clusters'), h.get('win_rate'), h.get('mean_usd'), h.get('mean_pct'), h.get('median_pct'),
            h.get('pf'), h.get('sum_usd'), h.get('ci95_mean_usd'), h.get('ci95_mean_usd_pair'), h.get('top_pair_share'),
            h.get('trades_per_hour'), h.get('exits')))
    print('  portfolio holdout', r['portfolio_holdout'], '| full', r['portfolio_full'], flush=True)
# stress 100 portfolio
H.STRESS_BPS = 100.0
try:
    t2 = H.simulate(M.CONFIGS[0]['signal'], **M.CONFIGS[0]['kwargs'])
finally:
    H.STRESS_BPS = 50.0
ho2 = [x for x in t2 if x['entry_t'] >= cut]
out['stress100_portfolio_holdout'] = H.portfolio(ho2, slots=3)
out['stress100_portfolio_full'] = H.portfolio(t2, slots=3)
print('stress100 portfolio holdout', out['stress100_portfolio_holdout'], 'full', out['stress100_portfolio_full'])
with open(C.HERE + '/stage4.json', 'w', encoding='utf-8') as fh:
    json.dump(out, fh, indent=1, default=str)
