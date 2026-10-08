"""Exploration 0: data shape for the dip-buy family (no outcomes looked at here)."""
import sys, time
sys.path.insert(0, (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r'')
import harness as H

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
t0 = time.time()
series, meta = H.load()
print('load s', round(time.time() - t0, 1), meta)
cut = H.split_t(meta)
cnt = {}
gaps = []
npairs = 0
for pair, s in series.items():
    if s['dex'] != 'pumpswap' or s['quote_sol'] != 1:
        continue
    npairs += 1
    ts = s['t']
    for i in range(len(ts)):
        liq = s['liq'][i]
        fee = H.fee_bps(s, i)
        lb = 'liq>=250k' if liq >= 250e3 else ('liq100-250k' if liq >= 100e3 else ('liq50-100k' if liq >= 50e3 else 'liq<50k'))
        fb = 'fee<=50' if fee <= 50 else ('fee55-95' if fee <= 95 else 'fee100-125')
        k = (lb, fb)
        cnt[k] = cnt.get(k, 0) + 1
        if i and liq >= 50e3:
            gaps.append(ts[i] - ts[i - 1])
print('pumpswap SOL pairs', npairs)
for k in sorted(cnt):
    print(k, cnt[k])
gaps.sort()
n = len(gaps)
print('gap ms quantiles (liq>=50k):', [gaps[int(n * q)] for q in (.1, .25, .5, .75, .9, .99)])
print('total s', round(time.time() - t0, 1))
# timing of a trivial simulate
t1 = time.time()
tr = H.simulate(lambda P: False)
print('trivial simulate s', round(time.time() - t1, 1))
