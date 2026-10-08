import harness as H
from smoke_common import cost_first, rnd
keep = ('n', 'pairs', 'win_rate', 'mean_pct', 'median_pct', 'sum_usd', 'pf', 'ci95_mean_usd', 'exits')
for label, guard in (('no guard', lambda P: True), ('interim rug guard', lambda P: not H.interim_rug_risk(P))):
    tr = H.simulate(lambda P: cost_first(P) and guard(P) and rnd(0.01)(P), stop=-5, tp=10, hold_min=60,
                    size_fn=lambda P: min(200.0, P('liq') * 0.001))
    ev = H.evaluate(tr)
    print('==', label, 'total n', len(tr), 'trades <= -50%:', sum(1 for x in tr if x['net0'] <= -50))
    for part in ('train', 'holdout'):
        print('  ', part, {k: ev[part].get(k) for k in keep})
