"""How bad is 'VANISHED' really? For pools that left the DexScreener feed (>10 min before the dataset end),
compare on-chain tape swap prices (SOL per token, median of swaps) shortly before the last feed point with
tape prices 0-15 / 15-60 / 60-180 min after it. Read-only over the tape snapshot."""
import json, sqlite3, sys
sys.path.insert(0, (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r'')
import harness as H
import exitlab as X

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
series, meta = H.load()
t_end = meta['t1']
data = X.load_events()
tags = {}
for sig, evs in data['events'].items():
    for e in evs:
        tags.setdefault(e['pair'], set()).add(e['tag'])
con = sqlite3.connect('file:' + H.DEEP.replace('\\', '/') + '/tape_snapshot.sqlite3?mode=ro', uri=True)
tape_pairs = {r[0] for r in con.execute('select distinct pair from events')}
rows = []
nvan = 0
for pair, tg in tags.items():
    s = series[pair]
    t_last = s['t'][-1]
    if t_end - t_last <= 600_000:
        continue
    nvan += 1
    if pair not in tape_pairs:
        continue
    px = []
    for (payload,) in con.execute('select payload from events where pair=? and event_time between ? and ? order by event_time',
                                  (pair, int(t_last - 900_000), int(t_last + 3 * 3_600_000))):
        d = json.loads(payload)
        try:
            ta, qa = float(d.get('token_amount') or 0), float(d.get('quote_amount') or 0)
        except (TypeError, ValueError):
            continue
        if ta > 0 and qa > 0:
            px.append((d['event_time'], qa / ta))

    def med(a, b):
        v = sorted(p for t, p in px if t_last + a * 60_000 <= t < t_last + b * 60_000)
        return (v[len(v) // 2], len(v)) if v else (None, 0)
    before, nb = med(-15, 0)
    if not before:
        continue
    r = {'pair': pair[:8], 'tags': '/'.join(sorted(tg)), 'n_before': nb, 'last_liq': round(s['liq'][-1])}
    for a, b in ((0, 15), (15, 60), (60, 180)):
        m, n = med(a, b)
        r['%d-%d' % (a, b)] = round(100 * (m / before - 1), 1) if m else None
        r['n%d' % a] = n
    rows.append(r)
print('pairs with universe events', len(tags), 'vanished (>10 min before end)', nvan, 'in tape', sum(1 for p in tags if p in tape_pairs),
      'vanished with tape swaps before vanish', len(rows))
for r in rows:
    print(json.dumps(r))
for key in ('0-15', '15-60', '60-180'):
    v = sorted(r[key] for r in rows if r[key] is not None)
    if v:
        print(key, 'n', len(v), 'median %', v[len(v) // 2], 'mean %', round(sum(v) / len(v), 1), 'share below -10%:',
              round(sum(1 for x in v if x < -10) / len(v), 2))
