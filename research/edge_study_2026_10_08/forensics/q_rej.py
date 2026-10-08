"""Read-only: distribution of engine rejection reasons in obs.sqlite3 over time."""
import sqlite3, sys, collections, datetime
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
DB = (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r'/obs.sqlite3'
con = sqlite3.connect('file:%s?mode=ro' % DB, uri=True)
print(con.execute('select count(*), min(t), max(t) from o').fetchone())
# overall first-reason distribution
tot = collections.Counter()
by_hour = collections.defaultdict(collections.Counter)
modes = collections.Counter()
for t, rej, mode, safe in con.execute('select t, rej, mode, safe from o'):
    reasons = (rej or '').split('|') if rej else ['<none>']
    h = datetime.datetime.fromtimestamp(t / 1000).strftime('%m-%d %H')
    for r in reasons:
        tot[r] += 1
        by_hour[h][r] += 1
    modes[(mode, safe)] += 1
print('reasons (row-level, a row can carry several):')
for r, c in tot.most_common(40):
    print('  %-45s %d' % (r, c))
print('mode/safe:', modes.most_common(10))
keys = [r for r, _ in tot.most_common(12)]
print('hour', ' '.join('%s' % k[:14] for k in keys))
for h in sorted(by_hour):
    c = by_hour[h]
    print(h, ' '.join('%d' % c.get(k, 0) for k in keys))
