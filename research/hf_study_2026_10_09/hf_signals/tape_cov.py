"""Tape coverage of the guarded cheap universe (read-only)."""
import sqlite3, sys, os, time
from common import H, DEEP, HERE
import pickle
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
T0 = time.time()
con = sqlite3.connect('file:%s?mode=ro' % os.path.join(DEEP, 'tape_snapshot.sqlite3'), uri=True)
rows = list(con.execute('select pair, count(*), min(event_time), max(event_time) from events group by pair'))
con.close()
d = pickle.load(open(os.path.join(HERE, 'events.pkl'), 'rb'))
ev = d['rows']
u50 = {}
u95 = {}
for r in ev:
    if r['liq'] >= 50_000 and r['fee'] <= 95:
        u95[r['pair']] = u95.get(r['pair'], 0) + 1
        if r['fee'] <= 50:
            u50[r['pair']] = u50.get(r['pair'], 0) + 1
meta = d['meta']
print('tape pairs', len(rows), 'events', sum(r[1] for r in rows))
print('tape span', min(r[2] for r in rows), max(r[3] for r in rows), 'dataset', meta['t0'], meta['t1'])
tp = {r[0]: r for r in rows}
for name, U in (('U50', u50), ('U95', u95)):
    cov = [(p, n, tp[p][1], (tp[p][3] - tp[p][2]) / 3.6e6) for p, n in U.items() if p in tp]
    print(name, 'pairs', len(U), 'with tape', len(cov), 'events share covered %.2f' % (sum(c[1] for c in cov) / sum(U.values())))
    for p, n, ne, span in sorted(cov, key=lambda c: -c[1])[:12]:
        print('   %s ev %5d tape %6d span %.1f h' % (p[:8], n, ne, span))
print('secs', round(time.time() - T0, 1))
