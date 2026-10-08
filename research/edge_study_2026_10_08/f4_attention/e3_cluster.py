"""F4: are boost/list onsets genuine or discovery-outage artifacts (many pairs flipping in the same minute)?
Also counts (no outcomes) of events per type in TRAIN vs HOLDOUT, to know what sample sizes are feasible."""
import sys, os, pickle
sys.path.insert(0, (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r'')
import harness as H

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
HERE = os.path.dirname(os.path.abspath(__file__))
d = pickle.load(open(os.path.join(HERE, 'events.pkl'), 'rb'))
CUT, T0 = d['cut'], d['T0']
ev = [e for e in d['events'] if e['dex'] == 'pumpswap']

by_min = {}
for e in ev:
    for ty in e['types']:
        if ty.startswith('ONSET') or ty.startswith('BOOST'):
            by_min.setdefault(int(e['t'] // 60000), []).append((ty, (e['sym'] or '')[:8]))
print('minutes with >=3 onset/boost events (pumpswap):')
for m in sorted(by_min):
    if len(by_min[m]) >= 3:
        print('  hour %.2f' % ((m * 60000 - T0) / 3.6e6), len(by_min[m]), by_min[m][:8])

# discovery outage detection: global count of points carrying any list flag per minute
series, meta = H.load()
flag_pairs_min = {}
for p, s in series.items():
    ts, src = s['t'], s['src']
    for i in range(len(ts)):
        if int(src[i]) & 7:
            flag_pairs_min.setdefault(int(ts[i] // 60000), set()).add(p)
mins = sorted(flag_pairs_min)
gaps = []
for a, b in zip(mins, mins[1:]):
    if b - a > 2:
        gaps.append((round((a * 60000 - T0) / 3.6e6, 2), b - a))
print('minutes with no flagged pairs at all (gaps > 2 min):', gaps[:40])
cnts = [len(flag_pairs_min[m]) for m in mins]
print('flagged pairs per minute p10/50/90', sorted(cnts)[len(cnts) // 10], sorted(cnts)[len(cnts) // 2], sorted(cnts)[9 * len(cnts) // 10])

print('\nevent counts train / holdout (no outcomes):')
cnt = {}
for e in ev:
    for ty in e['types']:
        c = cnt.setdefault(ty, [0, 0, set(), set()])
        if e['t'] < CUT:
            c[0] += 1; c[2].add(e['pair'])
        else:
            c[1] += 1; c[3].add(e['pair'])
for ty, c in sorted(cnt.items(), key=lambda x: -x[1][0]):
    print('  %-32s train %4d (%3d pairs)  holdout %4d (%3d pairs)' % (ty, c[0], len(c[2]), c[1], len(c[3])))
