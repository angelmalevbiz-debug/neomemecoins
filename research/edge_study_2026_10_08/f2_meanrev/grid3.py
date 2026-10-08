"""TRAIN-ONLY grid 3: robustness neighbourhood of the 'oversold dip' idea (pc1h / pc6h already negative + 15-min dip)."""
import json, sys, time
sys.path.insert(0, (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r'/f2_meanrev')
from sigs import *

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
series, meta = H.load()
CUT = H.split_t(meta)
OUT = (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r'/f2_meanrev/grid3.jsonl'


def combo_signal(pc1h_max=-12, pc6h_max=-15, **kw):
    a = dip_signal(pc1h_max=pc1h_max, **kw)
    b = dip_signal(pc6h_max=pc6h_max, **kw)
    return lambda P: a(P) or b(P)


SIGNALS = {}
for x in (-8, -12, -20):
    for mode in ('stab', 'any'):
        SIGNALS['os1h%d_%s' % (x, mode)] = (dip_signal, dict(pc1h_max=x, mode=mode))
for x in (-10, -15, -25):
    SIGNALS['os6h%d_stab' % x] = (dip_signal, dict(pc6h_max=x, mode='stab'))
SIGNALS['combo_1h12_6h15_stab'] = (combo_signal, dict(mode='stab'))
EXITS = {
    'e20_25_90': dict(tp=20, stop=-25, hold_min=90),
    'e15_20_60': dict(tp=15, stop=-20, hold_min=60),
    'e25_30_120': dict(tp=25, stop=-30, hold_min=120),
}
n_cfg = 0
t0 = time.time()
with open(OUT, 'a', encoding='utf-8') as fh:
    for sname, (fac, sk) in SIGNALS.items():
        for ename, ek in EXITS.items():
            tr = H.simulate(fac(**sk), t_to=CUT, **ek)
            n_cfg += 1
            st = H.summarize(tr)
            st0 = H.summarize(tr, 'usd0')
            d = {}
            for x in tr:
                d.setdefault(x['pair'], []).append(x['net50'])
            pm = round(sum(sum(v) / len(v) for v in d.values()) / len(d), 2) if d else None
            fh.write(json.dumps({'grid': 3, 'signal': sname, 'exit': ename, 'train': st, 'pairmean50': pm}) + '\n')
            if st['n']:
                print('%-22s %-11s n %4d pairs %3d win %5.1f mean %6.2f med %6.2f pm %6.2f pf %s ci %s top %.2f m0 %6.2f | %s' % (
                    sname, ename, st['n'], st['pairs'], st['win_rate'], st['mean_pct'], st['median_pct'], pm or 0,
                    st['pf'], st['ci95_mean_usd'], st['top_pair_share'], st0['mean_pct'], st['exits']), flush=True)
            else:
                print(sname, ename, 'n=0', flush=True)
print('configs', n_cfg, 'secs', round(time.time() - t0, 1))
