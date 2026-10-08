"""F1 exploration step 1: universe sizes and simulate() timing. Read-only."""
import sys, time
sys.path.insert(0, (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r'')
import harness as H

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
t0 = time.time()
series, meta = H.load()
cut = H.split_t(meta)
print('loaded', round(time.time() - t0, 1), 's', meta)
print('cut hour', (cut - meta['t0']) / 3.6e6)

cnt = {}
pairs = {}
for pair, s in series.items():
    if s['dex'] != 'pumpswap' or s['quote_sol'] != 1:
        continue
    n = len(s['t'])
    for i in range(n):
        fee = H.fee_bps(s, i)
        liq = s['liq'][i]
        fb = 'fee<=50' if fee <= 50 else ('fee55-95' if fee <= 95 else 'fee100-125')
        lb = 'liq>=250k' if liq >= 250_000 else ('liq100-250k' if liq >= 100_000 else ('liq50-100k' if liq >= 50_000 else ('liq30-50k' if liq >= 30_000 else 'liq<30k')))
        part = 'tr' if s['t'][i] < cut else 'ho'
        key = (fb, lb, part)
        cnt[key] = cnt.get(key, 0) + 1
        pairs.setdefault(key, set()).add(pair)
for k in sorted(cnt):
    print(k, 'points', cnt[k], 'pairs', len(pairs[k]))

# timing of a cheap signal
t0 = time.time()
tr = H.simulate(lambda P: H.hashed_coin(P.static('pair'), P.t, 0.001), stop=-5, tp=10, hold_min=60)
print('random sim', len(tr), 'trades', round(time.time() - t0, 1), 's')
