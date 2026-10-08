"""TRAIN-ONLY simulate grid (entries restricted to t < train cut). Holdout is never touched here."""
import json, sys, time
sys.path.insert(0, (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r'/f2_meanrev')
from sigs import *

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
series, meta = H.load()
CUT = H.split_t(meta)
OUT = (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r'/f2_meanrev/grid1.jsonl'

SIGNALS = {
    'dip_any_50k': dict(),
    'knife_50k': dict(mode='knife'),
    'stab_50k': dict(mode='stab'),
    'stab_sellsfall_50k': dict(mode='stab', sells_falling=True),
    'oversold6h_50k': dict(pc6h_max=-15),
    'oversold1h_50k': dict(pc1h_max=-12),
    'dip12_18_50k': dict(dd_lo=-0.18, dd_hi=-0.12),
    'dip_any_100k': dict(liq_min=100_000),
    'dip_any_250k': dict(liq_min=250_000),
    'bigpool_dip5': dict(liq_min=250_000, fee_max=60, dd_lo=-0.25, dd_hi=-0.05),
    'oversold6h_stab_50k': dict(pc6h_max=-15, mode='stab'),
    'knife_100k': dict(mode='knife', liq_min=100_000),
}
EXITS = {
    'e5_8_15': dict(tp=5, stop=-8, hold_min=15),
    'e10_12_30': dict(tp=10, stop=-12, hold_min=30),
    'e15_20_60': dict(tp=15, stop=-20, hold_min=60),
    'e3_5_10': dict(tp=3, stop=-5, hold_min=10),
    'retr50_15_60': 'retrace',
    'trail6_4_12_60': dict(tp=None, trail_arm=6, trail=4, stop=-12, hold_min=60),
}


def pairmean(tr):
    d = {}
    for x in tr:
        d.setdefault(x['pair'], []).append(x['net50'])
    return round(sum(sum(v) / len(v) for v in d.values()) / len(d), 2) if d else None


n_cfg = 0
t0 = time.time()
with open(OUT, 'a', encoding='utf-8') as fh:
    for sname, sk in SIGNALS.items():
        for ename, ek in EXITS.items():
            if ek == 'retrace':
                kw = dict(tp=None, stop=-15, hold_min=60, exit_fn=retrace_exit(0.5))
            else:
                kw = dict(ek)
            tr = H.simulate(dip_signal(**sk), t_to=CUT, **kw)
            n_cfg += 1
            st = H.summarize(tr)
            st0 = H.summarize(tr, 'usd0')
            rec = {'grid': 1, 'signal': sname, 'exit': ename, 'train': st, 'train_model_mean_pct': st0.get('mean_pct'), 'pairmean50': pairmean(tr)}
            fh.write(json.dumps(rec) + '\n')
            if st['n']:
                print('%-22s %-15s n %4d pairs %3d win %5.1f mean %6.2f med %6.2f pm %6.2f pf %s ci %s top %.2f tph %.2f m0 %6.2f | %s' % (
                    sname, ename, st['n'], st['pairs'], st['win_rate'], st['mean_pct'], st['median_pct'], rec['pairmean50'] or 0,
                    st['pf'], st['ci95_mean_usd'], st['top_pair_share'], st['trades_per_hour'], st0['mean_pct'], st['exits']), flush=True)
            else:
                print(sname, ename, 'n=0', flush=True)
print('configs', n_cfg, 'secs', round(time.time() - t0, 1))
