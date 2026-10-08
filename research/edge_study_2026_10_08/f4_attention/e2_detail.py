"""F4 detail on TRAIN events: what do attention events look like (age, size, pre-event run-up, path)?"""
import sys, os, pickle, bisect
sys.path.insert(0, (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r'')
import harness as H

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
HERE = os.path.dirname(os.path.abspath(__file__))
d = pickle.load(open(os.path.join(HERE, 'events.pkl'), 'rb'))
series, meta = H.load()
CUT = d['cut']
ev = [e for e in d['events'] if e['t'] < CUT]


def q(xs, f):
    xs = sorted(x for x in xs if x == x)
    return round(xs[int(f * (len(xs) - 1))], 1) if xs else None


def path(e, mins=(1, 3, 5, 10, 20, 30, 45, 60)):
    s = series[e['pair']]
    ts = s['t']
    i = e['i']
    p0 = s['price'][i]
    out = []
    for m in mins:
        k = bisect.bisect_left(ts, ts[i] + m * 60000, i + 1)
        if k >= len(ts):
            out.append(None)
        else:
            out.append(round(100 * (s['price'][k] / p0 - 1), 0))
    return out


for ty in ('FIRST_latest', 'FIRSTPAIR_latest', 'ONSET1_latest', 'BOOST_ON', 'ONSET1_boosted-latest', 'ONSETRE_boosted-latest', 'FIRST_gecko-new-pools'):
    rows = [e for e in ev if ty in e['types'] and e['dex'] == 'pumpswap']
    print('\n=====', ty, 'n', len(rows), 'rug-flagged', sum(1 for e in rows if e['rug']))
    for f in ('age', 'liq', 'mcap', 'pc5', 'pc1h', 'v5', 'b5', 's5', 'fee', 'life_min', 'score', 'same_ticker'):
        print('   %-10s p10 %s  p50 %s  p90 %s' % (f, q([e[f] for e in rows], .1), q([e[f] for e in rows], .5), q([e[f] for e in rows], .9)))
    if ty != 'FIRST_gecko-new-pools':
        for e in rows[:40]:
            print('    %-10s %s rug=%d age=%6.0fm liq=%7.0f mc=%9.0f pc5=%6.1f pc1h=%7.1f boost=%s life=%5.0fm path%%(1,3,5,10,20,30,45,60m)=%s' % (
                (e['sym'] or '')[:10], e['pair'][:8], e['rug'], e['age'], e['liq'], e['mcap'], e['pc5'], e['pc1h'], e['boost'], e['life_min'], path(e)))

# How long do gecko-new pools stay observed, and how many later come back via other sources?
rows = [e for e in ev if 'FIRST_gecko-new-pools' in e['types'] and e['dex'] == 'pumpswap']
print('\ngecko-new life minutes p25/50/75/90', q([e['life_min'] for e in rows], .25), q([e['life_min'] for e in rows], .5), q([e['life_min'] for e in rows], .75), q([e['life_min'] for e in rows], .9))
lives = sorted(e['life_min'] for e in rows)
print('gecko-new observed >= 10 min:', sum(1 for x in lives if x >= 10), '>= 30 min:', sum(1 for x in lives if x >= 30), 'of', len(lives))

# Pumpfun 'latest' (bonding curve) - descriptive gross only (harness fee model is not valid for pump.fun curve)
rows = [e for e in ev if 'FIRST_latest' in e['types'] and e['dex'] == 'pumpfun']
print('\npumpfun FIRST_latest n', len(rows))
for h in (60, 180, 300, 600, 900, 1800, 3600):
    xs = [e['g'][h] for e in rows if e['g'][h] is not None]
    print('   gross %ds n %d mean %.2f median %s win %.0f%%' % (h, len(xs), sum(xs) / max(1, len(xs)), q(xs, .5), 100 * sum(1 for x in xs if x > 0) / max(1, len(xs))))
