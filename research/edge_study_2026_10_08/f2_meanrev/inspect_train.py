"""TRAIN-ONLY: trade list of the pre-selection shortlist + random-baseline probability calibration."""
import sys, time
sys.path.insert(0, (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r'/f2_meanrev')
from sigs import *

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
series, meta = H.load()
CUT = H.split_t(meta)
T0 = meta['t0']


def combo_signal(pc1h_max=-12, pc6h_max=-15, **kw):
    a = dip_signal(pc1h_max=pc1h_max, **kw)
    b = dip_signal(pc6h_max=pc6h_max, **kw)
    return lambda P: a(P) or b(P)


tr = H.simulate(dip_signal(pc1h_max=-12, mode='stab'), t_to=CUT, tp=20, stop=-25, hold_min=90)
for x in tr:
    print('%6.2fh %-10s %s fee %5.1f liq %7.0f net50 %7.2f %s hold %5.1f mfe %6.2f mae %6.2f' % (
        (x['entry_t'] - T0) / 3.6e6, x['sym'][:10], x['pair'][:8], x['fee_bps'], x['liq'], x['net50'], x['reason'], x['hold_s'] / 60, x['mfe'], x['mae']))
by = {}
for x in tr:
    by.setdefault(x['sym'], []).append(round(x['net50'], 1))
print(by)
# random baseline calibration (train only): match trade counts
for label, base in (('ctx1h', dict(pc1h_max=-12)), ('ctxcombo', None), ('universe', dict())):
    for prob in (0.002, 0.005, 0.01, 0.02):
        if base is None:
            a = random_signal(prob, 'cal', pc1h_max=-12)
            b = random_signal(prob, 'cal', pc6h_max=-15)
            sig = lambda P, a=a, b=b: a(P) or b(P)
        else:
            sig = random_signal(prob, 'cal', **base)
        t = H.simulate(sig, t_to=CUT, tp=20, stop=-25, hold_min=90)
        print(label, prob, 'n', len(t), 'pairs', len({x['pair'] for x in t}))
