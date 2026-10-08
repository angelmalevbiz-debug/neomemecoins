"""Look at lab-trade fields (read-only copy) and the tape payload schema."""
import json, re, sqlite3, sys, collections
B58 = re.compile(r'[1-9A-HJ-NP-Za-km-z]{32,48}')


def ab(s):
    return B58.sub(lambda m: m.group(0)[:8] + '..', s)


def flat(o, pre=''):
    if isinstance(o, dict):
        for k, v in o.items():
            yield from flat(v, pre + '.' + str(k) if pre else str(k))
    elif isinstance(o, list):
        if len(o) and all(not isinstance(x, (dict, list)) for x in o):
            yield pre, o[:6]
        else:
            for i, x in enumerate(o[:3]):
                yield from flat(x, pre + '[%d]' % i)
    else:
        yield pre, o


which = sys.argv[1]
if which == 'lab':
    p = 'C:/Users/Chavd/neomemecoins/.runtime/release-backup-20261008-101156/.runtime/accounts/strategy_lab.json'
    d = json.load(open(p, encoding='utf-8'))
    keyc = collections.Counter()
    n = 0
    for name, b in d['books'].items():
        for t in b.get('history', []):
            n += 1
            for k in t:
                keyc[k] += 1
    print('lab trades', n)
    print(sorted(keyc.items(), key=lambda kv: -kv[1])[:400])
    for name, b in d['books'].items():
        if b.get('history'):
            t = b['history'][-1]
            for k, v in flat(t):
                if re.search(sys.argv[2] if len(sys.argv) > 2 else '.', k, re.I):
                    print(ab(k), '=', ab(json.dumps(v, ensure_ascii=False))[:160])
            break
elif which == 'tape':
    con = sqlite3.connect('file:' + __import__('os').path.join(__import__('os').path.dirname(__import__('os').path.dirname(__import__('os').path.abspath(__file__))), 'tape_snapshot.sqlite3').replace(chr(92), '/') + '?mode=ro', uri=True)
    for r in con.execute("select name, sql from sqlite_master"):
        print(r[0], (r[1] or '')[:300])
    for r in con.execute('select count(*), min(event_time), max(event_time) from events'):
        print(r)
    for r in con.execute('select * from events limit 3'):
        print([ab(str(x))[:1500] for x in r])
    for r in con.execute('select * from pairs limit 2'):
        print([ab(str(x))[:600] for x in r])
    for r in con.execute('select * from shadow_events limit 2'):
        print([ab(str(x))[:800] for x in r])
