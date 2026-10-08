import collections, json, os, re, sqlite3, sys
HERE = os.path.dirname(os.path.abspath(__file__))
DEEP = os.path.dirname(HERE)
B58 = re.compile(r'[1-9A-HJ-NP-Za-km-z]{32,48}')
ab = lambda s: B58.sub(lambda m: m.group(0)[:8], str(s))
con = sqlite3.connect('file:%s?mode=ro' % os.path.join(DEEP, 'tape_snapshot.sqlite3').replace('\\', '/'), uri=True)
keys = collections.Counter()
flags = collections.Counter()
srcs = collections.Counter()
orient = collections.Counter()
n = 0
for (p,) in con.execute('SELECT payload FROM events LIMIT 200000'):
    d = json.loads(p)
    n += 1
    for k in d:
        keys[k] += 1
    for f in d.get('quality_flags') or []:
        flags[f] += 1
    srcs[d.get('usd_valuation_source')] += 1
    orient[(d.get('pool_orientation'), d.get('direction'), d.get('onchain_direction'))] += 1
print(n)
print(sorted(keys.items(), key=lambda kv: -kv[1]))
print(flags.most_common())
print(srcs.most_common())
print(orient.most_common(20))
# one full recent event
for (p,) in con.execute("SELECT payload FROM events WHERE payload LIKE '%pool_orientation%' LIMIT 1"):
    print(ab(p)[:3000])
