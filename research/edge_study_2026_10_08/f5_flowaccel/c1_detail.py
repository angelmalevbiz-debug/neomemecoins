import sys, os
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import strategies as S
H = S.H
series, meta = H.load()
CUT = H.split_t(meta)
c = S.CONFIGS[0]
tr = H.simulate(c['signal'], **c['kwargs'])
for part, sub in (('train', [x for x in tr if x['entry_t'] < CUT]), ('holdout', [x for x in tr if x['entry_t'] >= CUT])):
    xs = sorted(sub, key=lambda x: -x['net50'])
    print(part, 'top5', [(x['sym'], x['pair'][:8], round(x['net50'], 1), x['reason'], round(x['hold_s'] / 60, 1)) for x in xs[:5]])
    print(part, 'bottom5', [(x['sym'], x['pair'][:8], round(x['net50'], 1), x['reason'], round(x['hold_s'] / 60, 1)) for x in xs[-5:]])
    print(part, 'TP fills > +15%:', sum(1 for x in sub if x['reason'] == 'TP' and x['net50'] > 15), 'STOP fills < -15%:', sum(1 for x in sub if x['reason'] == 'STOP' and x['net50'] < -15))
    print(part, 'fee mix', sorted({x['fee_bps'] for x in sub}), 'mean size $', round(sum(x['size'] for x in sub) / len(sub), 1), 'mean rt_cost%', round(sum(x['rt_cost'] or 0 for x in sub) / len(sub), 2))
    ho_wo_top = None
    by = {}
    for x in sub:
        by.setdefault(x['pair'], []).append(x['net50'])
    top = max(by, key=lambda p: len(by[p]))
    rest = [x['net50'] for x in sub if x['pair'] != top]
    print(part, 'top pair', top[:8], 'n', len(by[top]), 'mean', round(sum(by[top]) / len(by[top]), 2), '| rest n', len(rest), 'mean', round(sum(rest) / len(rest), 2))
