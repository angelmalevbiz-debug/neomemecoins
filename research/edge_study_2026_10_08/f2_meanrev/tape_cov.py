"""Tape coverage of the dip-candidate pairs (read-only)."""
import sqlite3, sys, json
sys.path.insert(0, (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r'/f2_meanrev')
from ana_common import *

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
cs = load_cands()
pairs = {}
for c in cs:
    pairs.setdefault(c['pair'], [0, c['sym'], c['rug']])[0] += 1
TAPE = (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r'/tape_snapshot.sqlite3'
con = sqlite3.connect('file:%s?mode=ro' % TAPE, uri=True)
tot = 0
for p, (n, sym, rug) in sorted(pairs.items(), key=lambda x: -x[1][0]):
    r = con.execute('select count(*), min(event_time), max(event_time) from events where pair=?', (p,)).fetchone()
    lags = [a - e for e, a in con.execute('select event_time, available from events where pair=?', (p,))]
    lags.sort()
    med = lags[len(lags) // 2] / 1000 if lags else None
    s = series[p]
    print(p[:8], sym, 'cands', n, 'rug', rug, 'tape events', r[0], 'lag_med_s', med,
          'tape span h', round((r[2] - r[1]) / 3.6e6, 1) if r[0] else None,
          'series span h', round((s['t'][-1] - s['t'][0]) / 3.6e6, 1), 'liq_k', round(s['liq'][0] / 1e3), 'fee0', H.fee_bps(s, 0))
    tot += r[0]
print('total tape events on candidate pairs', tot)
