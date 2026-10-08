"""HOLDOUT for the 3 configs pre-selected on train (best train mean net50 per family with n >= 10):
WE_PROXY, OFA_VF, CF_PROXY. Random baselines: same family universe, same exits, hashed-coin
entries whose probability is chosen from a grid by matching the TRAIN trade count only
(outcome-blind). Prints full evaluate(), portfolio(slots=3) and exit mixes."""
import sys, os, json, time
sys.dont_write_bytecode = True
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import families as F
H = F.H

SELECTED = [('WE', 'PROXY'), ('OFA', 'VF'), ('CF', 'PROXY')]
series, meta = H.load()
cut = H.split_t(meta)
keep = ('n', 'pairs', 'clusters', 'win_rate', 'mean_pct', 'median_pct', 'pf', 'sum_usd', 'ci95_mean_usd',
        'top_pair_share', 'trades_per_hour', 'avg_hold_min', 'exits')
out = {}
for fam, variant in SELECTED:
    spec = F.FAMILIES[fam]
    name = '%s_%s' % (fam, variant)
    tr = H.simulate(spec['signal'](variant), size_fn=spec['size_fn'], tag=name, **F.EXITS)
    n_train = sum(1 for x in tr if x['entry_t'] < cut)
    # outcome-blind baseline probability: match the train trade count
    best = None
    for prob in (0.0005, 0.001, 0.002, 0.004, 0.008, 0.016, 0.03, 0.06, 0.12):
        mk = spec['market']
        bt = H.simulate(lambda P, mk=mk, prob=prob: mk(P) and F.rnd(prob, 'forensics')(P), size_fn=spec['size_fn'],
                        t_to=cut, tag='rnd', **F.EXITS)
        gap = abs(len(bt) - n_train)
        if best is None or gap < best[0]:
            best = (gap, prob, len(bt))
    prob = best[1]
    mk = spec['market']
    base = H.simulate(lambda P, mk=mk, prob=prob: mk(P) and F.rnd(prob, 'forensics')(P), size_fn=spec['size_fn'],
                      tag='RANDOM_' + fam, **F.EXITS)
    ev, evb = H.evaluate(tr), H.evaluate(base)
    out[name] = {
        'train': {k: ev['train'].get(k) for k in keep}, 'holdout': {k: ev['holdout'].get(k) for k in keep},
        'train_model': {k: ev['train_model'].get(k) for k in ('n', 'mean_pct', 'win_rate', 'pf')},
        'holdout_model': {k: ev['holdout_model'].get(k) for k in ('n', 'mean_pct', 'win_rate', 'pf', 'sum_usd')},
        'portfolio_slots3_net50': H.portfolio(tr, slots=3),
        'baseline_prob': prob,
        'baseline_train': {k: evb['train'].get(k) for k in keep},
        'baseline_holdout': {k: evb['holdout'].get(k) for k in keep},
        'baseline_holdout_model': {k: evb['holdout_model'].get(k) for k in ('n', 'mean_pct', 'win_rate', 'pf')},
        'baseline_portfolio_slots3_net50': H.portfolio(base, slots=3),
    }
    print('==', name, 'baseline prob', prob, '(train n config/baseline', n_train, best[2], ')')
    for k, v in out[name].items():
        print('   ', k, v)
json.dump(out, open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'holdout_stage.json'), 'w'), indent=1)
