"""TRAIN-ONLY evaluation of the 3 families x 3 flow variants (9 configs). Holdout is NOT printed.
Trades are simulated only with t_to = train cut so holdout outcomes are never computed here."""
import sys, os, json, time
sys.dont_write_bytecode = True
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import families as F
H = F.H

fams = sys.argv[1].split(',') if len(sys.argv) > 1 else list(F.FAMILIES)
series, meta = H.load()
cut = H.split_t(meta)
keep = ('n', 'pairs', 'clusters', 'win_rate', 'mean_pct', 'median_pct', 'pf', 'sum_usd', 'ci95_mean_usd', 'top_pair_share', 'trades_per_hour', 'exits')
out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'train_stage.json')
res = json.load(open(out_path)) if os.path.exists(out_path) else {}
for fam in fams:
    spec = F.FAMILIES[fam]
    for variant in ('VF', 'PROXY', 'NONE'):
        t0 = time.time()
        tr = H.simulate(spec['signal'](variant), size_fn=spec['size_fn'], t_to=cut, tag='%s_%s' % (fam, variant), **F.EXITS)
        # entries only before the cut; an exit may land after the cut (the trade belongs to train)
        s50 = H.summarize(tr)
        s0 = H.summarize(tr, 'usd0')
        res['%s_%s' % (fam, variant)] = {'train_net50': {k: s50.get(k) for k in keep}, 'train_net0': {k: s0.get(k) for k in ('n', 'mean_pct', 'win_rate', 'pf')}}
        print('%s_%s' % (fam, variant), 'secs', round(time.time() - t0, 1))
        print('   net50', {k: s50.get(k) for k in keep})
        print('   net0 ', {k: s0.get(k) for k in ('n', 'mean_pct', 'win_rate', 'pf')})
json.dump(res, open(out_path, 'w'), indent=1)
