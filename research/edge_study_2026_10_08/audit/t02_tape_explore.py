"""Audit step 2: tape coverage and price units vs the DexScreener series (read-only)."""
import json, sqlite3, sys, collections, math, bisect, time
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.path.insert(0, (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r'')
import harness as H
DEEP = H.DEEP
t0 = time.time()
series, meta = H.load()
print('loaded', round(time.time() - t0, 1), 's')
con = sqlite3.connect('file:%s/tape_snapshot.sqlite3?mode=ro' % DEEP, uri=True)
per = collections.defaultdict(lambda: {'n': 0, 'buy': 0, 'sell': 0, 'tmin': 9e18, 'tmax': 0, 'prog': collections.Counter(), 'qa': collections.Counter(), 'flags': collections.Counter()})
for pair, et, av, payload in con.execute('select pair, event_time, available, payload from events'):
    d = json.loads(payload)
    x = per[pair]
    x['n'] += 1
    x['buy' if d.get('direction') == 'BUY' else 'sell'] += 1
    x['tmin'] = min(x['tmin'], et); x['tmax'] = max(x['tmax'], et)
    x['prog'][d.get('program_id', '')[:6]] += 1
    x['qa'][(d.get('quote_asset') or '')[:4]] += 1
    for f in d.get('quality_flags') or []:
        x['flags'][f] += 1
    x.setdefault('lat', []).append(av - et)
print('tape pairs with events', len(per))
inser = [p for p in per if p in series]
ps = [p for p in inser if series[p]['dex'] == 'pumpswap' and series[p]['quote_sol'] == 1]
print('in series', len(inser), 'pumpswap-SOL in series', len(ps))
progs = collections.Counter(); qas = collections.Counter(); flags = collections.Counter()
for x in per.values():
    progs.update(x['prog']); qas.update(x['qa']); flags.update(x['flags'])
print('programs', progs.most_common(5)); print('quote assets', qas.most_common(5)); print('flags', flags.most_common(10))
lat = sorted(l for x in per.values() for l in x['lat'])
q = lambda xs, p: xs[min(len(xs) - 1, int(p * len(xs)))]
print('available - event_time ms: p10 %d p50 %d p90 %d p99 %d' % (q(lat, .1), q(lat, .5), q(lat, .9), q(lat, .99)))
inspan = [p for p in ps if per[p]['tmax'] >= meta['t0'] and per[p]['tmin'] <= meta['t1']]
print('pumpswap-SOL pairs with tape events inside the series span:', len(inspan))
top = sorted(inspan, key=lambda p: -per[p]['n'])[:15]
for p in top:
    s = series[p]
    x = per[p]
    print(p[:8], (s['sym'] or '')[:10], 'events', x['n'], 'buy/sell', x['buy'], x['sell'], 'tape span h', round((x['tmax'] - x['tmin']) / 3.6e6, 2),
          'series pts', len(s['t']), 'series span h', round((s['t'][-1] - s['t'][0]) / 3.6e6, 2))
# units check on the busiest pair: tape SOL/token vs DexScreener priceNative near the same time
p = top[0]
s = series[p]
rows = con.execute('select event_time, payload from events where pair=? order by event_time', (p,)).fetchall()
ev = []
for et, payload in rows:
    d = json.loads(payload)
    ta, qa = d.get('token_amount'), d.get('quote_amount')
    if ta and qa and ta > 0:
        ev.append((et, qa / ta, d.get('direction')))
ets = [e[0] for e in ev]
print('units check on', p[:8])
for k in range(0, len(s['t']), max(1, len(s['t']) // 12)):
    tk = s['t'][k]
    a = bisect.bisect_right(ets, tk) - 1
    if a < 0:
        continue
    print('  t', int(tk), 'dex pnative %.4e' % s['pnative'][k], 'last tape %.4e %s age_s %.1f' % (ev[a][1], ev[a][2], (tk - ev[a][0]) / 1000))
con.close()
