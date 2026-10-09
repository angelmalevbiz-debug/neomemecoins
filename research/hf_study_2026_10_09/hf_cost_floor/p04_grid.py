"""p04: TRAIN-only grid (entries in [t0, cut)) for the HF cost floor. PAPER research only.

usage: p04_grid.py <part>   part in {base, rules, heatoff}
"""
import json, os, sys, time
sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hfsim as S

part = sys.argv[1]
SPAN = (S.CUT - S.T0) / 3.6e6
SPECS = {'time60': ('time', 60), 'time120': ('time', 120), 'time300': ('time', 300),
         'net1': ('net', 1.0, 600), 'net2': ('net', 2.0, 600), 'net3': ('net', 3.0, 600),
         'move1': ('move', 1.0, 600), 'move2': ('move', 2.0, 600), 'move3': ('move', 3.0, 600)}
out = open(os.path.join(S.C.HERE, 'p04_%s.jsonl' % part), 'w', encoding='utf-8')
t0 = time.time()


def emit(row):
    out.write(json.dumps(row) + '\n')
    out.flush()


def one(univ, sname, slots, size, rule, **kw):
    trades, info = S.run_book(univ, SPECS[sname], slots, size, t_from=S.T0, t_to=S.CUT, **kw)
    sm = S.summarize(trades, info, SPAN, size)
    row = {'part': part, 'univ': univ, 'spec': sname, 'slots': slots, 'size': size, 'rule': rule}
    row.update(sm)
    emit(row)
    return row


if part == 'base':
    for univ in ('a', 'b', 'c'):
        for sname in SPECS:
            for slots in (1, 3, 5, 10):
                for size in (25, 50, 100, 200):
                    r = one(univ, sname, slots, size, 'R0')
            print(univ, sname, 'done', round(time.time() - t0, 1), 's', flush=True)
elif part == 'rules':
    RULES = {'CD60': dict(cooldown_s=60), 'CD300': dict(cooldown_s=300), 'CD900': dict(cooldown_s=900),
             'CD1800': dict(cooldown_s=1800), 'LM': dict(loss_memory=True),
             'LM+CD300': dict(loss_memory=True, cooldown_s=300)}
    for univ in ('a', 'b', 'c'):
        for sname in ('time60', 'time120', 'time300', 'move2'):
            for slots in (1, 3, 5, 10):
                for size in (25, 100):
                    for rule, kw in RULES.items():
                        one(univ, sname, slots, size, rule, **kw)
            print(univ, sname, 'done', round(time.time() - t0, 1), 's', flush=True)
elif part == 'heatoff':
    for univ in ('a', 'b', 'c'):
        for sname in ('time60', 'time300'):
            for slots in (1, 3, 5, 10):
                for size in (25, 100):
                    one(univ, sname, slots, size, 'R0_HEATOFF', heat=False)
                    one(univ, sname, slots, size, 'R0_p0.05', p=0.05)
        print(univ, 'done', round(time.time() - t0, 1), 's', flush=True)
print('total secs', round(time.time() - t0, 1))
