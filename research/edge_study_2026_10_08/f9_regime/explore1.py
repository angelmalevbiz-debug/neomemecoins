"""F9 exploration 1: timeline coverage, universe sizes, SOL/USD path. Read-only."""
import sys, time, math
sys.path.insert(0, (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r'')
import harness as H

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
t0 = time.time()
series, meta = H.load()
print('load s', round(time.time() - t0, 1), meta)
T0, T1 = meta['t0'], meta['t1']
cut = H.split_t(meta)
print('span h', (T1 - T0) / 3.6e6, 'cut h', (cut - T0) / 3.6e6)
import datetime
print('T0 utc', datetime.datetime.utcfromtimestamp(T0 / 1000), 'T1 utc', datetime.datetime.utcfromtimestamp(T1 / 1000), 'cut utc', datetime.datetime.utcfromtimestamp(cut / 1000))

dexes = {}
for p, s in series.items():
    dexes[(s['dex'], s['quote_sol'])] = dexes.get((s['dex'], s['quote_sol']), 0) + 1
print('dex mix', dexes)

# hourly coverage: active pairs, points, pumpswap pairs by liq bucket
nh = int((T1 - T0) // 3.6e6) + 1
act = [set() for _ in range(nh)]
pts = [0] * nh
liqb = [{} for _ in range(nh)]
newp = [0] * nh
solpx = [[] for _ in range(nh)]
for p, s in series.items():
    ts = s['t']
    h0 = int((ts[0] - T0) // 3.6e6)
    newp[h0] += 1
    if s['dex'] != 'pumpswap' or s['quote_sol'] != 1:
        continue
    for i in range(0, len(ts)):
        h = int((ts[i] - T0) // 3.6e6)
        pts[h] += 1
        if p not in act[h]:
            act[h].add(p)
            liq = s['liq'][i]
            fee = H.fee_bps(s, i)
            k = ('L' if liq >= 250e3 else ('M' if liq >= 50e3 else 'S')) + ('lo' if fee <= 50 else ('mid' if fee <= 95 else 'hi'))
            liqb[h][k] = liqb[h].get(k, 0) + 1
        if i % 50 == 0:
            su = H.sol_usd(s, i)
            if su > 0:
                solpx[h].append(su)
for h in range(nh):
    sp = sorted(solpx[h])
    print('h%02d' % h, 'utc', datetime.datetime.utcfromtimestamp((T0 + h * 3.6e6) / 1000).strftime('%H:%M'), 'active_ps', len(act[h]), 'pts', pts[h], 'firstseen_all', newp[h],
          'sol', round(sp[len(sp) // 2], 2) if sp else None, dict(sorted(liqb[h].items())))
