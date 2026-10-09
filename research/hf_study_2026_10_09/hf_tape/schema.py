import sqlite3, json
D = r"C:/Users/Chavd/AppData/Local/Temp/claude/C--Users-Chavd-Documents-ChatGPT-memcoin/c0ef97d9-7323-486d-8d3d-c013406e7ed0/scratchpad/hf/research/edge_study_2026_10_08/tape_snapshot.sqlite3"
con = sqlite3.connect('file:%s?mode=ro' % D, uri=True)
for (n, s) in con.execute("select name, sql from sqlite_master"):
    print(n, s)
for t in ['events','signatures','pairs']:
    try:
        print(t, con.execute('select count(*) from %s' % t).fetchone())
    except Exception as e: print(t, e)
print(con.execute('select min(event_time), max(event_time), min(available), max(available), count(distinct pair) from events').fetchone())
for r in con.execute('select * from events limit 3'):
    print(r)
for r in con.execute('select * from pairs limit 2'):
    print(r)
for r in con.execute('select * from signatures limit 2'):
    print(r)
