"""Profile the on-chain swap tape: lags, coverage vs the observation window, payload fields. Writes tape_events.pkl
(compact per-pair arrays: event_time, available, dir(+1 buy/-1 sell), sol amount, wallet id int)."""
import sys, os, sqlite3, json, pickle, time
sys.path.insert(0, (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r'')
import harness as H
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
HERE = os.path.dirname(os.path.abspath(__file__))
t0 = time.time()
series, meta = H.load()
DB = (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r'/tape_snapshot.sqlite3'
con = sqlite3.connect('file:%s?mode=ro' % DB, uri=True)
wid = {}
per = {}
dirs, flags, lags = {}, {}, []
keys = {}
n = 0
for eid, pair, et, av, payload in con.execute('SELECT event_id, pair, event_time, available, payload FROM events'):
    n += 1
    p = json.loads(payload)
    for k in p:
        keys[k] = keys.get(k, 0) + 1
    d = p.get('direction')
    dirs[d] = dirs.get(d, 0) + 1
    for f in p.get('quality_flags') or []:
        flags[f] = flags.get(f, 0) + 1
    lags.append(av - et)
    w = p.get('wallet') or ''
    if w not in wid:
        wid[w] = len(wid)
    sgn = 1 if d == 'BUY' else (-1 if d == 'SELL' else 0)
    q = p.get('quote_amount')
    try:
        q = float(q)
    except Exception:
        q = float('nan')
    per.setdefault(pair, []).append((et, av, sgn, q, wid[w], 1 if p.get('confirmed_swap') else 0))
print('events', n, 'pairs', len(per), 'wallets', len(wid), 'secs', round(time.time() - t0, 1))
print('payload keys', keys)
print('directions', dirs)
print('quality flags', flags)
lags.sort()
print('lag available-event_time ms quantiles', [lags[int(len(lags) * q)] for q in (0.05, 0.25, 0.5, 0.75, 0.9, 0.95, 0.99)])
# coverage vs observation window
inwin = sum(1 for v in per.values() for e in v if meta['t0'] <= e[0] <= meta['t1'])
print('events with event_time inside obs window', inwin)
fresh = sum(1 for v in per.values() for e in v if meta['t0'] <= e[0] <= meta['t1'] and e[1] - e[0] <= 30_000)
print('... of which available within 30 s', fresh)
inser = [p for p in per if p in series]
print('tape pairs that are in the series', len(inser))
for v in per.values():
    v.sort()
out = {p: v for p, v in per.items()}
with open(os.path.join(HERE, 'tape_events.pkl'), 'wb') as fh:
    pickle.dump(out, fh, protocol=pickle.HIGHEST_PROTOCOL)
# coverage per pair within series: fraction of the pair's observed span with at least one tape event in 5 min
cov = []
for p in inser:
    s = series[p]
    ev = out[p]
    span = (s['t'][-1] - s['t'][0]) / 3.6e6
    m = sum(1 for e in ev if s['t'][0] <= e[0] <= s['t'][-1])
    cov.append((m, span, p))
cov.sort(reverse=True)
print('top tape-covered series pairs (events in span, span h, fee at start, liq at start):')
for m, span, p in cov[:25]:
    s = series[p]
    print('  ', p[:8], s['sym'], m, round(span, 2), H.fee_bps(s, 0), round(s['liq'][0]))
print('pairs with >=50 events in span', sum(1 for c in cov if c[0] >= 50), '>=200', sum(1 for c in cov if c[0] >= 200))
