"""Audit step 0: schemas of the tape snapshot and the observation DB (read-only)."""
import json, sqlite3, sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
DEEP = (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r''

con = sqlite3.connect('file:%s/tape_snapshot.sqlite3?mode=ro' % DEEP, uri=True)
for (name, sql) in con.execute("select name, sql from sqlite_master"):
    print(name, '::', sql)
for tb in ('pairs', 'signatures', 'events', 'shadow_events'):
    try:
        print(tb, con.execute('select count(*) from %s' % tb).fetchone())
    except Exception as e:
        print(tb, 'ERR', e)
print('--- pairs sample')
for r in con.execute('select * from pairs limit 3'):
    print([str(x)[:120] for x in r])
print('--- events sample')
for r in con.execute('select event_id, signature, pair, event_time, available, payload from events limit 3'):
    print(r[0], str(r[1])[:8], str(r[2])[:8], r[3], r[4])
    print(json.dumps(json.loads(r[5]), indent=1)[:3000])
print('--- event_time range', con.execute('select min(event_time), max(event_time) from events').fetchone())
print('--- shadow sample')
for r in con.execute('select * from shadow_events limit 2'):
    print([str(x)[:300] for x in r])
con.close()

con = sqlite3.connect('file:%s/obs.sqlite3?mode=ro' % DEEP, uri=True)
print(con.execute("select sql from sqlite_master where name='o'").fetchone())
for r in con.execute('select t, upd, created, age, price, pnative, sol_usd, fee_bps, mode from o limit 5'):
    print(r)
print('modes', con.execute('select mode, count(*) from o group by mode').fetchall())
con.close()
