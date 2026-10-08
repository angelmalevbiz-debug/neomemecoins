"""TRAIN crosstabs: drain-within-2h rate by bucket (points and pairs). Diagnostic only (no thresholds chosen here)."""
import collections, sys
from rugcommon import load_points, _series

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
pts = [x for x in load_points() if x['part'] == 'train']


def tab(name, keyf, flt=lambda x: True):
    c = collections.defaultdict(lambda: [0, 0, 0, set(), set()])
    for x in pts:
        if not flt(x):
            continue
        k = keyf(x)
        e = c[k]
        if x['y2h']:
            e[0] += 1; e[3].add(x['pair'])
        elif x['clean_neg']:
            e[1] += 1; e[4].add(x['pair'])
        elif x['van2h']:
            e[2] += 1
    print('==', name)
    for k in sorted(c, key=str):
        e = c[k]
        n = e[0] + e[1]
        print('   %-28s pos %5d (%3d pairs) neg %6d (%3d pairs) van %5d  drain-rate %.3f' % (k, e[0], len(e[3]), e[1], len(e[4]), e[2], e[0] / n if n else float('nan')))


def lb(x):
    l = x['liq']
    return '0 <10k' if l < 10e3 else '1 10-50k' if l < 50e3 else '2 50-250k' if l < 250e3 else '3 >=250k'


def ab(x):
    a = x['age']
    return '0 <15m' if a < 15 else '1 15-60m' if a < 60 else '2 1-6h' if a < 360 else '3 6-24h' if a < 1440 else '4 1-14d' if a < 20160 else '5 >=14d'


def lmcb(x):
    v = x['lmc']
    return 'nan' if v != v else '0 <0.02' if v < 0.02 else '1 0.02-0.05' if v < 0.05 else '2 0.05-0.3' if v < 0.3 else '3 0.3-0.6' if v < 0.6 else '4 0.6-1.0' if v < 1.0 else '5 >=1.0'


tab('liq bucket', lb)
tab('age bucket', ab)
tab('age bucket | liq>=50k', ab, lambda x: x['liq'] >= 50e3)
tab('lmc bucket', lmcb)
tab('lmc bucket | liq>=50k', lmcb, lambda x: x['liq'] >= 50e3)
tab('lmc bucket x pump | liq>=20k', lambda x: (lmcb(x), x['pump']), lambda x: x['liq'] >= 20e3)
tab('reuse>=1 x age<14d | liq>=50k', lambda x: (min(x['reuse'], 3), x['age'] < 20160), lambda x: x['liq'] >= 50e3)
tab('bs5>5 (engine unnatural buy imbalance) | liq>=50k', lambda x: x['bs5'] > 5, lambda x: x['liq'] >= 50e3)
tab('lmc<0.03 (engine weak liq/MC) | liq>=50k', lambda x: x['lmc'] < 0.03, lambda x: x['liq'] >= 50e3)
tab('src | liq>=50k', lambda x: x['src'], lambda x: x['liq'] >= 50e3)
tab('fee | liq>=50k', lambda x: x['fee'], lambda x: x['liq'] >= 50e3)

# who are the clean negatives with lmc >= 1 ?
print('== clean negatives with lmc >= 1.0 (train), by pair')
c = collections.defaultdict(list)
for x in pts:
    if x['clean_neg'] and x['lmc'] >= 1.0:
        c[x['pair']].append(x)
for p, v in sorted(c.items(), key=lambda kv: -len(kv[1]))[:25]:
    x = v[-1]
    print('   ', (_series[p]['sym'] or '')[:10], p[:8], 'pts', len(v), 'liq %.0f mcap %.0f lmc %.2f age %.0f pump %d fee %d reuse %d vliq1h %.3g src %d' % (
        x['liq'], x['mcap'], x['lmc'], x['age'], x['pump'], x['fee'], x['reuse'], x['vliq1h'], x['src']))
