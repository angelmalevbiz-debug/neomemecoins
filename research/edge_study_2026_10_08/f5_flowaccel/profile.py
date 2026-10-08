"""F5 profiling: feature coverage and distributions (read-only)."""
import sys, time, math
sys.path.insert(0, (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r'')
import harness as H

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
t0 = time.time()
series, meta = H.load()
print('loaded', meta, round(time.time() - t0, 1))
cut = H.split_t(meta)
fin = lambda x: x == x

tot = 0
cov = {}
buck = {}
pairs_b = {}
feat_cols = ['v5', 'v1h', 'v6h', 'b5', 's5', 'b1h', 's1h', 'hp_uw5', 'hp_rb5', 'vf_trades', 'vf_buy', 'vf_wallets', 'sf_buy', 'sf_wallets', 'pc5', 'conv', 'score']
npairs = 0
for pair, s in series.items():
    if s['dex'] != 'pumpswap' or s['quote_sol'] != 1:
        continue
    npairs += 1
    n = len(s['t'])
    for i in range(n):
        tot += 1
        for c in feat_cols:
            v = s[c][i]
            if fin(v) and v != 0:
                cov[c] = cov.get(c, 0) + 1
        fee = H.fee_bps(s, i)
        liq = s['liq'][i]
        fb = 'fee<=50' if fee <= 50 else ('fee55-95' if fee <= 95 else 'fee100-125')
        lb = 'liq>=250k' if liq >= 250_000 else ('liq50-250k' if liq >= 50_000 else ('liq20-50k' if liq >= 20_000 else 'liq<20k'))
        k = (fb, lb)
        buck[k] = buck.get(k, 0) + 1
        pairs_b.setdefault(k, set()).add(pair)
print('pumpswap SOL pairs', npairs, 'points', tot)
for c in feat_cols:
    print('  nonzero-finite', c, cov.get(c, 0), round(100 * cov.get(c, 0) / tot, 1), '%')
for k in sorted(buck):
    print('  bucket', k, 'points', buck[k], 'pairs', len(pairs_b[k]))

# distribution of acceleration ratios at 1/min sampling
def q(xs, ps=(0.05, 0.25, 0.5, 0.75, 0.9, 0.95, 0.99)):
    xs = sorted(xs)
    n = len(xs)
    return [round(xs[min(n - 1, int(p * n))], 3) for p in ps] if n else []

r_v = []; r_b = []; bs = []; uw = []; vfw = []; vft = []; liqs = []; dt = []
for pair, s in series.items():
    if s['dex'] != 'pumpswap' or s['quote_sol'] != 1:
        continue
    ts = s['t']
    last = -1e18
    for i in range(1, len(ts)):
        dt.append(ts[i] - ts[i - 1])
        if ts[i] - last < 60_000:
            continue
        last = ts[i]
        v5, v1h, b5, s5, b1h = s['v5'][i], s['v1h'][i], s['b5'][i], s['s5'][i], s['b1h'][i]
        if v1h > 0 and fin(v5):
            r_v.append(v5 / (v1h / 12))
        if b1h > 0 and fin(b5):
            r_b.append(b5 / (b1h / 12))
        if fin(b5) and fin(s5) and b5 + s5 > 0:
            bs.append(b5 / (b5 + s5))
        if fin(s['hp_uw5'][i]):
            uw.append(s['hp_uw5'][i])
        if fin(s['vf_wallets'][i]):
            vfw.append(s['vf_wallets'][i])
        if fin(s['vf_trades'][i]):
            vft.append(s['vf_trades'][i])
print('v5/(v1h/12) quantiles', len(r_v), q(r_v))
print('b5/(b1h/12) quantiles', len(r_b), q(r_b))
print('buy share b5/(b5+s5)', len(bs), q(bs))
print('hp_uw5', len(uw), q(uw))
print('vf_wallets', len(vfw), q(vfw))
print('vf_trades', len(vft), q(vft))
print('dt between points ms', q(dt))
print('secs', round(time.time() - t0, 1))
