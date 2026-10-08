"""Why are tape-covered pools DEGRADED (no verified flow) so often?

For a deterministic sample of obs rows whose fast-flow quality is COMPLETE or DEGRADED (i.e. the
pool held a tape seat), look up the tape events of that exact pool in the 30 s window ending at
the observation and classify:
  FLAGGED_EVENT   - at least one event in the window carries quality_flags or usd_amount <= 0
                    (market_monitor.live_flow marks the whole window DEGRADED)
  NO_FLAG_FOUND   - no flagged event in the snapshot: warm-up (complete_since < 30 s ago),
                    pagination/backlog, or event timing checks.
Read-only on obs.sqlite3 and tape_snapshot.sqlite3.
"""
import sys, os, json, sqlite3, collections, hashlib
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
DEEP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
obs = sqlite3.connect('file:%s?mode=ro' % os.path.join(DEEP, 'obs.sqlite3'), uri=True)
tape = sqlite3.connect('file:%s?mode=ro' % os.path.join(DEEP, 'tape_snapshot.sqlite3'), uri=True)


def keep(pair, t, rate=0.15):
    h = hashlib.blake2b(('%s|%d' % (pair, t)).encode(), digest_size=8).digest()
    return int.from_bytes(h, 'big') / 2 ** 64 < rate


res = collections.Counter()
flags = collections.Counter()
nev = collections.Counter()
by_pair = collections.defaultdict(collections.Counter)
q = "SELECT t, pair, sym, ff_q FROM o WHERE ff_q IN ('COMPLETE','DEGRADED')"
for t, pair, sym, ffq in obs.execute(q):
    if not keep(pair, t):
        continue
    evs = tape.execute('SELECT available, payload FROM events WHERE pair=? AND event_time>=? AND event_time<=?',
                       (pair, t - 30000, t)).fetchall()
    flagged = False
    n_av = 0
    for av, p in evs:
        if av > t:
            continue
        n_av += 1
        d = json.loads(p)
        fl = d.get('quality_flags') or []
        if fl or (d.get('usd_amount') or 0) <= 0:
            flagged = True
            for f in fl or ['USD_AMOUNT_NONPOSITIVE']:
                flags[(ffq, f)] += 1
    cls = 'FLAGGED_EVENT' if flagged else 'NO_FLAG_FOUND'
    res[(ffq, cls)] += 1
    nev[(ffq, 'events>=3' if n_av >= 3 else 'events<3')] += 1
    by_pair[(sym or '')[:12] + '|' + pair[:8]][(ffq, cls)] += 1
tot = sum(res.values())
print('sampled rows', tot)
for k, v in sorted(res.items()):
    print('  ', k, v, round(100 * v / tot, 1), '%')
print('flags', flags.most_common())
print('events available in window', sorted(nev.items()))
print('top pairs by DEGRADED share (>=200 sampled rows):')
rows = []
for p, c in by_pair.items():
    n = sum(c.values())
    if n >= 200:
        d = sum(v for (q_, cl), v in c.items() if q_ == 'DEGRADED')
        fl = sum(v for (q_, cl), v in c.items() if q_ == 'DEGRADED' and cl == 'FLAGGED_EVENT')
        rows.append((round(100 * d / n, 1), round(100 * fl / n, 1), n, p))
for r in sorted(rows, reverse=True)[:25]:
    print('   degraded%', r[0], 'flag-caused%', r[1], 'n', r[2], r[3])
json.dump({'res': {'|'.join(k): v for k, v in res.items()}, 'flags': {'|'.join(k): v for k, v in flags.items()},
           'pairs': rows}, open(os.path.join(DEEP, 'forensics', 'degraded.json'), 'w'), indent=1)
