"""TRAIN ONLY: (1) realized-PnL smart-wallet persistence, (2) large-buy follow outcome by size bucket."""
import bisect, math, pickle, sys
from collections import defaultdict
sys.path.insert(0, (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r'')
import harness as H
import tape_load as TL

series, meta = H.load()
T = TL.load()
cut = H.split_t(meta)
mid = meta['t0'] + 0.3 * (meta['t1'] - meta['t0'])
buys = pickle.load(open((__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r'/f8_tape/buys.pkl', 'rb'))

# ---- (1) realized PnL per wallet from its own swaps (average cost), events with available < mid only
ev = []
for pair, d in T['tape'].items():
    for et, av, side, w, sol, tok in zip(d['et'], d['av'], d['side'], d['w'], d['sol'], d['tok']):
        if sol == sol and tok > 0:
            ev.append((et, av, pair, side, w, sol, tok))
ev.sort()
pos = {}
real = defaultdict(float)
closes = defaultdict(int)
wins = defaultdict(int)
pools = defaultdict(set)
spent = defaultdict(float)
for et, av, pair, side, w, sol, tok in ev:
    if av >= mid:
        continue
    key = (w, pair)
    q, c = pos.get(key, (0.0, 0.0))
    if side > 0:
        pos[key] = (q + tok, c + sol)
        spent[w] += sol
    else:
        if q <= 0:
            continue
        f = min(1.0, tok / q)
        pnl = sol * (min(tok, q) / tok) - c * f
        real[w] += pnl
        closes[w] += 1
        wins[w] += pnl > 0
        pools[w].add(pair)
        pos[key] = (q * (1 - f), c * (1 - f))
print('wallets with closes in A1', len(closes))
a2 = defaultdict(list)
for b in buys:
    if mid <= b['t'] < cut and b['n300'] is not None:
        a2[b['w']].append(b)
for minc, minpools in ((2, 1), (5, 1), (5, 2), (10, 2)):
    ws = [w for w in closes if closes[w] >= minc and len(pools[w]) >= minpools and w in a2]
    if len(ws) < 10:
        print('minc', minc, 'minpools', minpools, 'wallets', len(ws), 'too few')
        continue
    for label, keyf in (('realized SOL', lambda w: real[w]), ('ROI', lambda w: real[w] / spent[w] if spent[w] > 0 else 0),
                        ('winrate', lambda w: wins[w] / closes[w])):
        sc = sorted(ws, key=keyf)
        parts = []
        for k in range(4):
            grp = sc[k * len(sc) // 4:(k + 1) * len(sc) // 4]
            xs = [b['n300'] for w in grp for b in a2[w]]
            gs = [b['g300'] for w in grp for b in a2[w] if b['g300'] is not None]
            parts.append('q%d score %.3g..%.3g A2 n300 %6.2f g300 %6.2f (n%d w%d)' % (
                k + 1, keyf(grp[0]), keyf(grp[-1]), sum(xs) / max(1, len(xs)), sum(gs) / max(1, len(gs)), len(xs), len(grp)))
        print('minc', minc, 'minpools', minpools, label, 'wallets', len(ws))
        for p in parts:
            print('     ', p)

# ---- (2) large-buy follow (train, rug-screened), by absolute SOL and by SOL*usd/liq
tr = [b for b in buys if b['t'] < cut and not b['rug']]
print('\ntrain rug-screened buys', len(tr))
for lo, hi in ((0, 0.1), (0.1, 0.5), (0.5, 1), (1, 2), (2, 5), (5, 10), (10, 1e9)):
    g = [b for b in tr if lo <= b['sol'] < hi]
    for h in ('n60', 'n300', 'n900'):
        xs = [b[h] for b in g if b[h] is not None]
        if not xs:
            continue
        xs.sort()
        print('sol [%g,%g) %s n%6d pairs %3d mean %6.2f med %6.2f win %4.1f' % (lo, hi, h, len(xs), len(set(b['pair'] for b in g)),
              sum(xs) / len(xs), xs[len(xs) // 2], 100 * sum(1 for x in xs if x > 0) / len(xs)))
for b in tr:
    s = series[b['pair']]
    su = H.sol_usd(s, b['i'])
    b['bliq'] = b['sol'] * su / b['liq'] * 100 if b['liq'] > 0 and su > 0 else float('nan')
for lo, hi in ((0, 0.1), (0.1, 0.3), (0.3, 1), (1, 2), (2, 5), (5, 1e9)):
    g = [b for b in tr if lo <= b['bliq'] < hi]
    for h in ('n300', 'n900'):
        xs = [b[h] for b in g if b[h] is not None]
        if not xs:
            continue
        xs.sort()
        print('buy/liq%% [%g,%g) %s n%6d pairs %3d mean %6.2f med %6.2f win %4.1f' % (lo, hi, h, len(xs), len(set(b['pair'] for b in g)),
              sum(xs) / len(xs), xs[len(xs) // 2], 100 * sum(1 for x in xs if x > 0) / len(xs)))
