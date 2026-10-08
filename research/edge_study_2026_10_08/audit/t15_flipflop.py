"""Audit step 15: A-B-A price flip-flops (stale cache echoes) in kept points; train/holdout straddle share."""
import collections, sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
DEEP = (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r''
sys.path.insert(0, DEEP)
import harness as H
from smoke_common import rnd

series, meta = H.load()
c = collections.Counter()
for p, s in series.items():
    if s['dex'] != 'pumpswap' or s['quote_sol'] != 1:
        continue
    pr, ts = s['price'], s['t']
    hist = []  # distinct price runs: (price, start_t)
    for k in range(len(pr)):
        if not hist or pr[k] != hist[-1][0]:
            hist.append((pr[k], ts[k]))
            if len(hist) >= 3:
                c['changes'] += 1
                a, b, cc = hist[-3], hist[-2], hist[-1]
                if cc[0] == a[0]:
                    c['A-B-A'] += 1
                    if cc[1] - b[1] <= 15_000:
                        c['A-B-A within 15 s'] += 1
print(dict(c), 'A-B-A share of changes %.4f' % (c['A-B-A'] / max(1, c['changes'])))
cut = H.split_t(meta)
tr = H.simulate(lambda P: rnd(0.002)(P), stop=-5, tp=10, hold_min=60)
train = [x for x in tr if x['entry_t'] < cut]
print('train trades exiting after the train/holdout cut: %d of %d (%.1f%%)' % (
    sum(1 for x in train if x['exit_t'] >= cut), len(train), 100 * sum(1 for x in train if x['exit_t'] >= cut) / len(train)))
