"""F5 simulation screen 5 (TRAIN entries only): sizing rule for the 3 shortlisted configs ($200 fixed vs
min($200, 0.2% of pool liquidity)), full train evaluate + per-pair breakdown. Holdout NOT looked at here."""
import sys, os, time, json
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from f5sig import H, fin, U, tb, rnd
from simscreen3 import robust, nochase, ds

series, meta = H.load()
CUT = H.split_t(meta)
LOG = os.path.join(HERE, 'configs_log.jsonl')
EX = dict(stop=-10, tp=6, hold_min=30)
small = lambda P: min(200.0, 0.002 * P('liq'))
SHORT = [('C1 tape breadth nochase', tb(extra=nochase)), ('C2 tape breadth', tb()), ('C3 DS twin nochase', ds(nochase))]
KEEP = ('n', 'pairs', 'clusters', 'win_rate', 'mean_pct', 'median_pct', 'pf', 'sum_usd', 'ci95_mean_usd', 'top_pair_share', 'trades_per_hour', 'exits')
for name, sig in SHORT:
    for sname, sz in (('$200', None), ('min(200,.002liq)', small)):
        tr = H.simulate(sig, t_to=CUT, size_fn=sz, **EX)
        sm = H.summarize(tr)
        print('%-26s %-18s robust %s' % (name, sname, json.dumps(robust(tr))))
        print('      ', json.dumps({k: sm.get(k) for k in KEEP}), 'net0 mean', H.summarize(tr, 'usd0').get('mean_pct'))
        with open(LOG, 'a') as fh:
            fh.write(json.dumps({'stage': 'simscreen5', 'name': name, 'size': sname, 'part': 'train', 'robust': robust(tr)}) + '\n')
        if sname == '$200':
            by = {}
            for x in tr:
                by.setdefault(x['sym'] + ':' + x['pair'][:8], []).append(x['net50'])
            print('       per pair:', sorted(((k, len(v), round(sum(v) / len(v), 1)) for k, v in by.items()), key=lambda z: -z[1])[:12])
        sys.stdout.flush()
print('configs counted in this screen: 3 (sizing alternatives)')
