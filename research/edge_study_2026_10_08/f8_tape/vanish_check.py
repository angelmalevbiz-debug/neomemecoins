"""How far is the series' LAST price (used by the harness vanish rule: -10%) from the on-chain price for pairs
that vanished from the feed while the tape still covered them? Diagnostic for the integrator."""
import sys
sys.path.insert(0, (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r'')
sys.path.insert(0, (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r'/f8_tape')
import harness as H
import tape_load as TL
import onchain as OC

series, meta = H.load()
rows = []
for pair in TL.load()['tape']:
    s = series.get(pair)
    if s is None or s['dex'] != 'pumpswap' or s['quote_sol'] != 1:
        continue
    ts = s['t']
    k = len(ts) - 1
    if meta['t1'] - ts[k] <= 600_000:
        continue
    oc = None
    for dt in (0, 150_000, 300_000, 450_000, 600_000):   # latest swap up to 10 min after the feed went silent
        v = OC.onchain_price_usd(s, k, ts[k] + dt, 900)
        if v is not None:
            oc = v
    if oc is None or not (s['price'][k] > 0):
        continue
    rows.append((oc / s['price'][k] - 1) * 100)
rows.sort()
n = len(rows)
print('vanished tape pairs with on-chain price', n)
if n:
    print('on-chain vs series-last price %%: q10 %.1f q25 %.1f q50 %.1f q75 %.1f q90 %.1f' % tuple(rows[min(n - 1, int(p * n))] for p in (.1, .25, .5, .75, .9)))
    print('share below -10%% (harness haircut too mild): %.2f; below -50%%: %.2f' % (sum(1 for r in rows if r < -10) / n, sum(1 for r in rows if r < -50) / n))
