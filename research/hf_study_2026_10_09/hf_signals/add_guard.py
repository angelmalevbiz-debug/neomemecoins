"""Adds the engine-equivalent structural guard to every event row (past-only). PAPER research only.

eg = passes STRUCTURAL_RUG_GUARD_V1 as the engine runs it (backend/structural_rug_guard.py), approximated with the
dataset: H.rug_guard_v1 (already true for every row) AND NOT fake market cap at 2 % (mcap >= $20M, liq/mcap < 2 %,
age < 14 d) AND NOT ticker reuse (age < 14 d and the normalized ticker was first seen on another pool with a
DIFFERENT mint at or before the decision time). Limitation: the dataset's ticker memory starts at the dataset start,
the engine registry remembers 14 days, so the engine would block at least as much (the registry-warming rule is
treated as satisfied: the engine registries are seeded and vouch)."""
import os, pickle, sys, time
from common import H, HERE
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
T0 = time.time()
series, meta = H.load()
idx = {}
for pair, s in series.items():
    idx.setdefault(H.norm_ticker(s['sym']), []).append((s['t'][0], pair, s['mint']))
for v in idx.values():
    v.sort()
path = os.path.join(HERE, 'events.pkl')
d = pickle.load(open(path, 'rb'))
cnt = {'fake2': 0, 'reuse': 0, 'eg_fail': 0}
for r in d['rows']:
    s = series[r['pair']]
    young = not (r['age'] >= 14 * 1440)
    fake2 = r['mcap'] >= 20e6 and r['liq'] / r['mcap'] < 0.02 and young
    reuse = False
    if young:
        tk = H.norm_ticker(s['sym'])
        reuse = (not tk) or any(t0 <= r['t'] and p != r['pair'] and m != s['mint'] for t0, p, m in idx.get(tk, []))
    r['fake2'], r['reuse'] = bool(fake2), bool(reuse)
    r['eg'] = not (fake2 or reuse)
    cnt['fake2'] += fake2
    cnt['reuse'] += reuse
    cnt['eg_fail'] += not r['eg']
print(cnt, 'rows', len(d['rows']))
for name, f in (('U50', lambda r: r['liq'] >= 50_000 and r['fee'] <= 50), ('U95', lambda r: r['liq'] >= 50_000 and r['fee'] <= 95)):
    U = [r for r in d['rows'] if f(r)]
    E = [r for r in U if r['eg']]
    blocked = {}
    for r in U:
        if not r['eg']:
            blocked[r['pair']] = blocked.get(r['pair'], 0) + 1
    print(name, 'events', len(U), 'engine-guard pass', len(E), 'pairs', len({r['pair'] for r in U}), '->',
          len({r['pair'] for r in E}), '| blocked pairs:',
          ', '.join('%s %s %d' % (p[:8], series[p]['sym'], n) for p, n in sorted(blocked.items(), key=lambda kv: -kv[1])[:12]))
pickle.dump(d, open(path, 'wb'), protocol=pickle.HIGHEST_PROTOCOL)
print('secs', round(time.time() - T0, 1))
