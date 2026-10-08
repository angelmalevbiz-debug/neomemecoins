"""F7 exploration step 1: load timing + universe sizes (pair-minutes) in train vs holdout. Read-only."""
import sys, time
sys.path.insert(0, (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r'')
import harness as H

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
t0 = time.time()
series, meta = H.load()
print('load secs', round(time.time() - t0, 1), meta)
cut = H.split_t(meta)


def ubucket(s, i):
    fee = H.fee_bps(s, i)
    liq = s['liq'][i]
    fb = 'fee<=50' if fee <= 50 else ('fee55-95' if fee <= 95 else 'fee100-125')
    lb = 'liq>=250k' if liq >= 250_000 else ('liq50-250k' if liq >= 50_000 else ('liq20-50k' if liq >= 20_000 else 'liq<20k'))
    return fb, lb


stats = {}
t0 = time.time()
npairs = 0
for pair, s in series.items():
    if s['dex'] != 'pumpswap' or s['quote_sol'] != 1:
        continue
    npairs += 1
    ts = s['t']
    last = -1e18
    for i in range(len(ts)):
        if ts[i] - last < 60_000:
            continue
        last = ts[i]
        P = H.Past(s, i)
        rug = H.interim_rug_risk(P)
        b = ubucket(s, i) + ('rug' if rug else 'ok', 'train' if ts[i] < cut else 'hold')
        d = stats.setdefault(b, [0, set()])
        d[0] += 1
        d[1].add(pair)
print('pumpswap SOL pairs', npairs, 'scan secs', round(time.time() - t0, 1))
for k in sorted(stats):
    print(k, 'pair-minutes', stats[k][0], 'pairs', len(stats[k][1]))
