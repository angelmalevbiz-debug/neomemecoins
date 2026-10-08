"""Smart-wallet persistence test, TRAIN ONLY (train split into A1 / A2 halves).

For every tape BUY, the follower outcome = what we would have got buying at the first harness point
at/after the event became AVAILABLE (decision i, fill i+1) and selling H seconds later
(H.forward_net, stressed and model). Wallet skill in A1 is compared with the same wallets' follower
outcomes in A2. If skill does not persist inside train, hypothesis (a) is dead before holdout.
"""
import bisect, math, pickle, sys, time
from collections import defaultdict
sys.path.insert(0, (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r'')
import harness as H
import tape_load as TL

t0 = time.time()
series, meta = H.load()
T = TL.load()
cut = H.split_t(meta)
mid = meta['t0'] + 0.3 * (meta['t1'] - meta['t0'])   # A1 = [t0, mid), A2 = [mid, cut)
HZ = (60, 300, 900)
buys = []   # (wallet, pair, et, av, sol, i_decision, gross{hz}, net50{hz}, net0{hz}, rug)
for pair, d in T['tape'].items():
    s = series.get(pair)
    if s is None or s['dex'] != 'pumpswap' or s['quote_sol'] != 1:
        continue
    ts = s['t']
    for et, av, side, w, sol, tok in zip(d['et'], d['av'], d['side'], d['w'], d['sol'], d['tok']):
        if side <= 0 or not (sol == sol) or et < meta['t0'] or et > meta['t1']:
            continue
        i = bisect.bisect_left(ts, av)          # first point at/after availability
        if i >= len(ts) - 1 or ts[i] - av > 30_000:
            continue
        rec = {'w': w, 'pair': pair, 'et': et, 'av': av, 'sol': sol, 'i': i, 't': ts[i]}
        p1 = s['price'][i + 1]
        for hz in HZ:
            k = bisect.bisect_left(ts, ts[i] + hz * 1000, i + 2)
            rec['g%d' % hz] = (s['price'][k] / p1 - 1) * 100 if (k < len(ts) and p1 > 0) else None
            rec['n%d' % hz] = H.forward_net(s, i, hz, extra_bps=H.STRESS_BPS)
        rec['rug'] = H.interim_rug_risk(H.Past(s, i))
        rec['fee'] = H.fee_bps(s, i)
        rec['liq'] = s['liq'][i]
        buys.append(rec)
print('buys with labels', len(buys), round(time.time() - t0, 1), 's')
pickle.dump(buys, open((__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r'/f8_tape/buys.pkl', 'wb'),
            protocol=pickle.HIGHEST_PROTOCOL)

A1 = [b for b in buys if b['t'] < mid]
A2 = [b for b in buys if mid <= b['t'] < cut]
print('A1 buys', len(A1), 'A2 buys', len(A2))


def wstats(bs, key, minsol=0.0):
    d = defaultdict(list)
    pools = defaultdict(set)
    for b in bs:
        v = b[key]
        if v is None or b['sol'] < minsol:
            continue
        d[b['w']].append(v)
        pools[b['w']].add(b['pair'])
    return d, pools


for key in ('g60', 'g300', 'g900', 'n300'):
    for minsol in (0.0, 0.5):
        d1, p1 = wstats(A1, key, minsol)
        d2, p2 = wstats(A2, key, minsol)
        for minn, minpools in ((3, 1), (5, 2), (10, 3)):
            ws = [w for w in d1 if len(d1[w]) >= minn and len(p1[w]) >= minpools and w in d2]
            if len(ws) < 20:
                print(key, 'minsol', minsol, 'minn', minn, 'minpools', minpools, 'wallets in both', len(ws), '(too few)')
                continue
            sc = sorted(ws, key=lambda w: sum(d1[w]) / len(d1[w]))
            q = 5
            parts = []
            for k in range(q):
                grp = sc[k * len(sc) // q:(k + 1) * len(sc) // q]
                a1 = [sum(d1[w]) / len(d1[w]) for w in grp]
                a2 = [x for w in grp for x in d2[w]]
                parts.append('A1 %6.2f -> A2 %6.2f (n%d, w%d)' % (sum(a1) / len(a1), sum(a2) / len(a2), len(a2), len(grp)))
            # rank correlation of wallet means
            x = [sum(d1[w]) / len(d1[w]) for w in ws]
            y = [sum(d2[w]) / len(d2[w]) for w in ws]

            def rank(v):
                o = sorted(range(len(v)), key=lambda k: v[k])
                r = [0] * len(v)
                for pos, k in enumerate(o):
                    r[k] = pos
                return r
            rx, ry = rank(x), rank(y)
            n = len(ws)
            mx, my = sum(rx) / n, sum(ry) / n
            cov = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
            vx = sum((a - mx) ** 2 for a in rx)
            vy = sum((b - my) ** 2 for b in ry)
            rho = cov / math.sqrt(vx * vy) if vx > 0 and vy > 0 else float('nan')
            print(key, 'minsol', minsol, 'minn', minn, 'minpools', minpools, 'wallets', n, 'spearman %.3f' % rho)
            print('     ', ' | '.join(parts))
print('secs', round(time.time() - t0, 1))
