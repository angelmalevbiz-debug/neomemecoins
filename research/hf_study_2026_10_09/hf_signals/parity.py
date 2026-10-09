"""Parity: hfsim.trade (time exit, hold from the entry fill) vs H.simulate on the same decision points."""
import sys, time, random
from common import H
import hfsim as S
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
T0 = time.time()
ev = S.events()['rows']
rnd = random.Random(3)
# one decision per pair per 40 min, guarded fee<=95 liq>=50k events
chosen, last = {}, {}
for r in ev:
    if r['liq'] < 50_000 or r['fee'] > 95:
        continue
    if r['t'] - last.get(r['pair'], -1e18) < 2_400_000:
        continue
    if rnd.random() < 0.3:
        last[r['pair']] = r['t']
        chosen[(r['pair'], r['t'])] = r['i']
print('decision points', len(chosen))
for hold_s, tp_net in ((120, None), (300, None)):
    keys = set(chosen)
    trades = H.simulate(lambda P: (P.static('pair'), P.t) in keys, stop=-1e9, tp=None, hold_min=hold_s / 60.0,
                        cooldown_s=0, dexes=('pumpswap',))
    by = {(x['pair'], x['decision_t']): x for x in trades}
    diffs50, diffs0, miss, n = [], [], 0, 0
    for (p, t), i in chosen.items():
        a = by.get((p, t))
        b = S.trade(p, i, hold_s, None, None, 200.0, hold_from='fill')
        if a is None or b is None:
            miss += (a is None) != (b is None)
            continue
        n += 1
        diffs50.append(abs(a['net50'] - b['net50']))
        diffs0.append(abs(a['net0'] - b['net0']))
        if abs(a['net50'] - b['net50']) > 1e-9 and len(diffs50) < 400 and sum(1 for d in diffs50 if d > 1e-9) <= 3:
            print('  diff', p[:8], a['reason'], b['reason'], a['net50'], b['net50'], a['exit_t'], b['exit_t'])
    print('hold %d: matched %d, presence mismatches %d, max |dnet50| %.2e, max |dnet0| %.2e, n>1e-9 %d' % (
        hold_s, n, miss, max(diffs50), max(diffs0), sum(1 for d in diffs50 if d > 1e-9)))
print('secs', round(time.time() - T0, 1))
