import collections, sys
sys.path.insert(0, (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + '/calib')
import ledgers as L

tr = L.load_account_trades()
print('unique account trades', len(tr))
by = collections.Counter((k[0], t.get('session_id', '')[:30]) for k, (t, p) in tr.items())
for k, v in sorted(by.items()):
    print(' ', k, v)
keys = collections.Counter()
for k, (t, p) in tr.items():
    for kk in t:
        keys[kk] += 1
print(len(keys))
print(sorted([(k, v) for k, v in keys.items() if v < len(tr)], key=lambda kv: kv[1]))
reasons = collections.Counter(t.get('exit_reason') for t, p in tr.values())
print(reasons)
syms = collections.Counter((t.get('symbol'), L.ab(t.get('pairAddress'))) for t, p in tr.values())
print(syms.most_common(40))
dex = collections.Counter((t.get('coin_snapshot') or {}).get('dexId') for t, p in tr.values())
print(dex)
pf = collections.Counter(len(t.get('partial_fills') or []) for t, p in tr.values())
print('partial fills', pf)
lab = L.load_lab_trades()
print('unique lab trades', len(lab))
jt = [t for t, p in lab.values() if (t.get('price_crosscheck') or {}).get('jupiter_entry_price')]
print('lab with jupiter probe', len(jt))
gk = [t for t, p in lab.values() if (t.get('price_crosscheck') or {}).get('reference_price')]
print('lab with gecko ref', len(gk))
fk = collections.Counter()
for t, p in lab.values():
    for kk in (t.get('entry_features') or {}):
        fk[kk] += 1
print(fk.most_common(80))
print(collections.Counter(t.get('execution_mode') for t, p in lab.values()))
