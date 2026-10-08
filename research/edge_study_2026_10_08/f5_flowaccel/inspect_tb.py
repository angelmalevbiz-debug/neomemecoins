"""TRAIN-only inspection of the tape-breadth trades: tail dependence, per-pair contribution, hour profile."""
import sys, os
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from f5sig import tb, U, H

series, meta = H.load()
CUT = H.split_t(meta)
tr = H.simulate(tb(), stop=-10, tp=6, hold_min=30, t_to=CUT)
print('n', len(tr))
xs = sorted(tr, key=lambda x: x['net50'])
print('worst 8:', [(x['sym'], round(x['net50'], 1), x['reason'], x['flag'], round(x['hold_s'] / 60, 1)) for x in xs[:8]])
print('best 12:', [(x['sym'], round(x['net50'], 1), x['reason'], x['flag'], round(x['hold_s'] / 60, 1), round(x['mfe'], 1)) for x in xs[-12:]])
tot = sum(x['net50'] for x in tr)
print('sum net50 %', round(tot, 1), 'without top3', round(sum(x['net50'] for x in xs[:-3]) / (len(xs) - 3), 2), 'without top5', round(sum(x['net50'] for x in xs[:-5]) / (len(xs) - 5), 2))
print('without VANISHED', round(sum(x['net50'] for x in tr if x['flag'] != 'VANISHED') / max(1, sum(1 for x in tr if x['flag'] != 'VANISHED')), 2))
by = {}
for x in tr:
    by.setdefault(x['sym'] + ':' + x['pair'][:8], []).append(x['net50'])
print('per pair (n, mean):')
for k, v in sorted(by.items(), key=lambda kv: -len(kv[1])):
    print('   ', k, len(v), round(sum(v) / len(v), 2))
hrs = {}
for x in tr:
    h = int((x['entry_t'] - meta['t0']) // 3.6e6)
    hrs.setdefault(h, []).append(x['net50'])
print('by hour since start:', {h: (len(v), round(sum(v) / len(v), 2)) for h, v in sorted(hrs.items())})
fees = {}
for x in tr:
    fees.setdefault(x['fee_bps'], []).append(x['net50'])
print('by fee:', {f: (len(v), round(sum(v) / len(v), 2)) for f, v in sorted(fees.items())})
# winning trades: how much of TP exits filled well above target (gap)
tp = [x for x in tr if x['reason'] == 'TP']
print('TP exits n', len(tp), 'mean net50', round(sum(x['net50'] for x in tp) / max(1, len(tp)), 2), 'max', round(max(x['net50'] for x in tp), 1) if tp else None)
st = [x for x in tr if x['reason'] == 'STOP']
print('STOP exits n', len(st), 'mean net50', round(sum(x['net50'] for x in st) / max(1, len(st)), 2), 'min', round(min(x['net50'] for x in st), 1) if st else None)
