"""Calibration: DexScreener series price vs tape-implied on-chain marginal price at the same moment."""
import sys
sys.path.insert(0, (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r'')
sys.path.insert(0, (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r'/f8_tape')
import harness as H
import tape_load as TL
import onchain as OC

series, meta = H.load()
T = TL.load()


def q(xs, ps=(0.05, 0.1, 0.25, 0.5, 0.75, 0.9, 0.95)):
    xs = sorted(xs)
    return [round(xs[min(len(xs) - 1, int(p * len(xs)))], 4) for p in ps] if xs else []


by_fee = {}
allr = []
for pair in T['tape']:
    s = series.get(pair)
    if s is None or s['dex'] != 'pumpswap' or s['quote_sol'] != 1:
        continue
    ts = s['t']
    last = -1e18
    for k in range(len(ts)):
        if ts[k] - last < 30_000:
            continue
        p = OC.onchain_price_usd(s, k, ts[k], 30)
        if p is None or not (s['price'][k] > 0):
            continue
        last = ts[k]
        r = s['price'][k] / p
        allr.append(r)
        fee = H.fee_bps(s, k)
        by_fee.setdefault('fee<=50' if fee <= 50 else ('fee55-95' if fee <= 95 else 'fee100+'), []).append(r)
print('series/onchain ratio all n', len(allr), q(allr))
for kf, v in by_fee.items():
    print(kf, 'n', len(v), q(v))
