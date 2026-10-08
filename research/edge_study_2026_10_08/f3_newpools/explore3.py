"""F3 exploration 3: tape coverage of young pools relative to the DexScreener observation window.
Question: after a young pool leaves the scanner feed (VANISHED in the harness), what does the
on-chain tape say about its price? This calibrates the harness's flat -10% vanish haircut."""
import sys, json, sqlite3, bisect
sys.path.insert(0, (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r'')
import harness as H
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
DEEP = (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r''
series, meta = H.load()
T1 = meta['t1']
con = sqlite3.connect('file:' + DEEP + '/tape_snapshot.sqlite3?mode=ro', uri=True)
cur = con.cursor()
rng = {}
for pair, n, a, b in cur.execute('select pair, count(*), min(event_time), max(event_time) from events group by pair'):
    rng[pair] = (n, a, b)
print('events time range overall', min(v[1] for v in rng.values()), max(v[2] for v in rng.values()), 'obs', meta['t0'], meta['t1'])
# pairs table reasons / metadata sample
for r in cur.execute('select reason, count(*) from pairs group by reason order by 2 desc limit 15'):
    print('pairs.reason', r)
r = cur.execute('select metadata, complete_since, last_poll from pairs limit 2').fetchall()
for x in r:
    print('pairs.metadata sample', x[0][:600], x[1], x[2])
young = [p for p, s in series.items() if s['dex'] == 'pumpswap' and s['quote_sol'] == 1 and s['age'][0] < 60]
cov = []
for p in young:
    if p not in rng:
        continue
    s = series[p]
    n, a, b = rng[p]
    o0, o1 = s['t'][0], s['t'][-1]
    after = cur.execute('select count(*) from events where pair=? and event_time>?', (p, o1 + 60_000)).fetchone()[0]
    cov.append((p, n, (a - o0) / 60000, (b - o1) / 60000, after))
cov.sort(key=lambda x: -x[4])
print('young pools on tape', len(cov), 'with >=20 events >1 min after last obs:', sum(1 for c in cov if c[4] >= 20))
for c in cov[:25]:
    s = series[c[0]]
    print(c[0][:8], (s['sym'] or '')[:10], 'events', c[1], 'tape_start-obs_start min %.1f' % c[2], 'tape_end-obs_end min %.1f' % c[3], 'events after obs end', c[4],
          'obs dur %.1f' % ((s['t'][-1] - s['t'][0]) / 60000))
# event payload keys variety
keys = {}
for (pl,) in cur.execute('select payload from events limit 2000'):
    d = json.loads(pl)
    for k in d:
        keys[k] = keys.get(k, 0) + 1
print('payload keys', keys)
dirs = {}
for (pl,) in cur.execute('select payload from events limit 20000'):
    d = json.loads(pl)
    dirs[d.get('direction')] = dirs.get(d.get('direction'), 0) + 1
print('directions', dirs)
