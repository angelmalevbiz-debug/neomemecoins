"""Synthesis step 1: run the FROZEN synthesis/strategies.py (3 Lab hypotheses + 3 random baselines) on the 22.76 h
dataset under leaderboard/harness_final.py, exactly as the integrator would (sys.modules['harness'] = harness_final).
Descriptive only: these configs are recombinations of family results, so the whole dataset is IN-SAMPLE for them.
No parameter is chosen here (strategies.py was hashed into prereg.sha256 before this ran)."""
import json, os, sys, time
sys.dont_write_bytecode = True
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
DEEP = (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r''
HERE = os.path.join(DEEP, 'synthesis')
sys.path.insert(0, os.path.join(DEEP, 'leaderboard'))
import harness_final as H
sys.modules['harness'] = H
sys.path.insert(0, HERE)
import strategies as S

out = {}
for c in S.CONFIGS + S.BASELINES:
    t0 = time.time()
    tr = H.simulate(c['signal'], **c['kwargs'])
    ev = H.evaluate(tr)
    pf = H.portfolio([x for x in tr if x['entry_t'] >= H.split_t(H.load()[1])], slots=3)
    out[c['name']] = {'train': ev['train'], 'holdout': ev['holdout'], 'holdout_model_pct': ev['holdout_model'].get('mean_pct'),
                      'train_model_pct': ev['train_model'].get('mean_pct'), 'portfolio_holdout': pf, 'n_all': len(tr)}
    for part in ('train', 'holdout'):
        sm = ev[part]
        print('%-24s %-7s n=%s pairs=%s mean%%=%s median%%=%s win=%s mean$=%s CI$=%s CIpair=%s top=%s tr/h=%s exits=%s' % (
            c['name'], part, sm.get('n'), sm.get('pairs'), sm.get('mean_pct'), sm.get('median_pct'), sm.get('win_rate'),
            sm.get('mean_usd'), sm.get('ci95_mean_usd'), sm.get('ci95_mean_usd_pair'), sm.get('top_pair_share'),
            sm.get('trades_per_hour'), sm.get('exits')))
    print('   model%% train %s holdout %s | holdout portfolio %s | %.0fs' % (
        ev['train_model'].get('mean_pct'), ev['holdout_model'].get('mean_pct'), pf, time.time() - t0))
with open(os.path.join(HERE, 'insample_results.json'), 'w') as fh:
    json.dump(out, fh, indent=1, default=str)
print('wrote insample_results.json')
