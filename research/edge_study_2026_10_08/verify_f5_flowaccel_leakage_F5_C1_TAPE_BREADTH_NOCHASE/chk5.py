import sys
sys.argv = ['x']
src = open('run5.py', encoding='utf-8').read()
src = src.split("ho = [x for x in c1 if x['entry_t'] >= CUT]")[0]
src = src.replace("for mins in (15, 30):", "for mins in ():")
exec(compile(src, 'run5_part', 'exec'))
ho = [x for x in c1 if x['entry_t'] >= CUT]
for nm, w, ex in (('20e', 20000, False), ('20', 20000, True), ('60', 60000, True)):
    v = reprice(ho, w, w, ex)
    print(nm, [round(a, 2) for a in v], round(sum(v), 3))
print('orig', [round(x['usd50'], 2) for x in ho], round(sum(x['usd50'] for x in ho), 3))
