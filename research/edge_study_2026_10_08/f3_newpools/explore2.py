"""F3 exploration 2: how long are young pools observed; gaps; who stays in the feed; tape coverage."""
import sys, time, sqlite3, bisect
sys.path.insert(0, (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r'')
import harness as H
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
DEEP = (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r''
series, meta = H.load()
T1 = meta['t1']


def q(xs, p):
    xs = sorted(xs)
    return round(xs[min(len(xs) - 1, int(p * len(xs)))], 2) if xs else None


young = []
for pair, s in series.items():
    if s['dex'] != 'pumpswap' or s['quote_sol'] != 1:
        continue
    if not (s['age'][0] < 60):
        continue
    young.append(pair)
dur = [(series[p]['t'][-1] - series[p]['t'][0]) / 60000 for p in young]
npts = [len(series[p]['t']) for p in young]
print('young pools', len(young))
print('observed duration min quantiles', [q(dur, x) for x in (.1, .25, .5, .75, .9, .95, .99)])
print('points quantiles', [q(npts, x) for x in (.1, .25, .5, .75, .9, .95, .99)])
# max gap within series
gaps = []
for p in young:
    ts = series[p]['t']
    g = max((ts[i + 1] - ts[i]) for i in range(len(ts) - 1)) / 60000 if len(ts) > 1 else 0
    gaps.append(g)
print('max internal gap min quantiles', [q(gaps, x) for x in (.5, .75, .9, .95, .99)])
long = [p for p, d in zip(young, dur) if d >= 60]
print('young pools observed >= 60 min:', len(long))
liq0 = [series[p]['liq'][0] for p in young]
print('liq at first obs quantiles', [q(liq0, x) for x in (.1, .25, .5, .75, .9, .95, .99)])
mc0 = [series[p]['mcap'][0] for p in young]
print('mcap at first obs quantiles', [q(mc0, x) for x in (.1, .25, .5, .75, .9, .95, .99)])
# how do long-observed ones differ? liq at first obs and src
for label, grp in (('long', long), ('short', [p for p, d in zip(young, dur) if d < 60])):
    l0 = [series[p]['liq'][0] for p in grp]
    print(label, 'n', len(grp), 'liq0 med', q(l0, .5), 'p90', q(l0, .9), 'src flags (OR):',
          sorted({int(max(series[p]['src'])) for p in grp})[:20])
# examples of long-observed young pools
print('\nexamples long-observed young pools')
for p in long[:60]:
    s = series[p]
    ts = s['t']
    print(p[:8], (s['sym'] or '')[:12], 'age0 %.1f' % s['age'][0], 'dur %.0f' % ((ts[-1] - ts[0]) / 60000), 'pts', len(ts),
          'liq0 %.0f liqmax %.0f liqlast %.0f' % (s['liq'][0], max(s['liq']), s['liq'][-1]),
          'p last/first %.2f max/first %.2f' % (s['price'][-1] / s['price'][0], max(s['price']) / s['price'][0]),
          'src', sorted({int(v) for v in s['src']}))

# tape coverage of young pools
con = sqlite3.connect('file:' + DEEP + '/tape_snapshot.sqlite3?mode=ro', uri=True)
cur = con.cursor()
print('\ntape tables:')
for r in cur.execute("select name, sql from sqlite_master where type in ('table','index')"):
    print(r[0], (r[1] or '')[:300])
tp = set(r[0] for r in cur.execute('select distinct pair from events'))
print('tape pairs', len(tp))
yt = [p for p in young if p in tp]
print('young pools with tape events', len(yt))
row = cur.execute('select payload from events limit 1').fetchone()
print('payload sample', row[0][:1500] if row else None)
