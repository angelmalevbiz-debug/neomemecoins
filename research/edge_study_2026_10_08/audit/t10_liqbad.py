"""Audit step 10: are missing-liquidity points glitches or drained pools? Do repeated-price rows carry new flow data?"""
import collections, math, sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.path.insert(0, (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r'')
import harness as H

series, meta = H.load()
c = collections.Counter()
ex = []
for p, s in series.items():
    if s['dex'] != 'pumpswap' or s['quote_sol'] != 1:
        continue
    liq, pr = s['liq'], s['price']
    n = len(liq)
    for k in range(n):
        if liq[k] > 0:
            continue
        prev = next((liq[m] for m in range(k - 1, -1, -1) if liq[m] > 0), None)
        nxt = next((liq[m] for m in range(k + 1, n) if liq[m] > 0), None)
        kind = 'nan' if liq[k] != liq[k] else 'zero_or_neg'
        if nxt is None:
            c[kind + ':no_valid_liq_after (terminal)'] += 1
        elif prev is not None and nxt > 0.5 * prev:
            c[kind + ':glitch (liq back to >50% of previous)'] += 1
        else:
            c[kind + ':other'] += 1
        if len(ex) < 6:
            ex.append((p[:8], k, n, prev, liq[k], nxt, pr[k - 1] if k else None, pr[k]))
print(dict(c))
for e in ex:
    print('  ', e)
# repeated-price rows: do flow fields change?
tot = same_px = same_px_flow_changed = 0
for p, s in series.items():
    if s['dex'] != 'pumpswap' or s['quote_sol'] != 1:
        continue
    pr, vb, vt = s['price'], s['vf_buy'], s['vf_trades']
    for k in range(1, len(pr)):
        tot += 1
        if pr[k] == pr[k - 1]:
            same_px += 1
            a, b = vt[k], vt[k - 1]
            if (a == a or b == b) and not (a == b) or (vb[k] == vb[k] and vb[k] != vb[k - 1]):
                same_px_flow_changed += 1
print('points', tot, 'same price as previous', same_px, 'of which verified-flow fields changed', same_px_flow_changed)
