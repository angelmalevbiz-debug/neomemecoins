"""Why so few holdout trades? Young-pool arrivals and feed persistence per hour of the dataset."""
import sys
sys.path.insert(0, (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r'/f3_newpools')
import strategies as S
H = S.H
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
series, meta = H.load()
T0, CUT = meta['t0'], H.split_t(meta)
u24 = S.universe(1440)
hours = {}
for p, s in series.items():
    if s['dex'] != 'pumpswap' or s['quote_sol'] != 1:
        continue
    ts = s['t']
    h0 = int((ts[0] - T0) // 3.6e6)
    d = hours.setdefault(h0, {'new_age<60': 0, 'stay>=10m': 0, 'univ24_pairs': set(), 'univ24_points': 0, 'src8': 0, 'src4': 0})
    if s['age'][0] < 60:
        d['new_age<60'] += 1
        if ts[-1] - ts[0] >= 600_000:
            d['stay>=10m'] += 1
        if int(s['src'][0]) & 8:
            d['src8'] += 1
        if int(s['src'][0]) & 4:
            d['src4'] += 1
    for i in range(len(ts) - 1):
        if s['age'][i] >= 1440:
            continue
        P = H.Past(s, i)
        if u24(P):
            hh = int((ts[i] - T0) // 3.6e6)
            dd = hours.setdefault(hh, {'new_age<60': 0, 'stay>=10m': 0, 'univ24_pairs': set(), 'univ24_points': 0, 'src8': 0, 'src4': 0})
            dd['univ24_pairs'].add(p)
            dd['univ24_points'] += 1
print('split at hour %.2f' % ((CUT - T0) / 3.6e6))
for h in sorted(hours):
    d = hours[h]
    print('hour %2d new_young=%3d (gecko %3d, latest %3d) stayed>=10m=%2d | F3-24h universe: pairs=%2d points=%5d' % (
        h, d['new_age<60'], d['src8'], d['src4'], d['stay>=10m'], len(d['univ24_pairs']), d['univ24_points']))
