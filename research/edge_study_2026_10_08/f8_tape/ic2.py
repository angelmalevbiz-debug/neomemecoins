"""TRAIN ONLY: are the short-horizon tape-flow ICs real, or catch-up of a stale DexScreener quote?
Forward GROSS return measured from (a) series price at the fill point, (b) on-chain marginal price at the fill point."""
import bisect, math, pickle, sys
sys.path.insert(0, (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r'')
sys.path.insert(0, (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r'/f8_tape')
import harness as H
import onchain as OC
from ic import rank, ic  # noqa: reuse (ic.py prints on import; acceptable)

series, meta = H.load()
rows = pickle.load(open((__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r'/f8_tape/samples.pkl', 'rb'))
rows = [r for r in rows if r['train'] and not r['rug']]
for r in rows:
    s = series[r['pair']]
    ts = s['t']
    j = r['i'] + 1
    ps = s['price'][j]
    oc = OC.onchain_price_usd(s, j, ts[j], 30)
    r['prem'] = (oc / ps - 1) * 100 if (oc and ps > 0) else None
    for hz in (300, 900):
        k = bisect.bisect_left(ts, ts[r['i']] + hz * 1000, j + 1)
        pk = s['price'][k] if k < len(ts) else None
        r['gs_%d' % hz] = (pk / ps - 1) * 100 if (pk and ps > 0) else None
        r['go_%d' % hz] = (pk / oc - 1) * 100 if (pk and oc) else None
print('\n\n==== stale-quote check: IC vs gross fwd from series fill (gs) and from on-chain fill (go)')
xs = sorted(r['prem'] for r in rows if r['prem'] is not None)
print('on-chain premium at fill over series, q10/50/90: %.2f %.2f %.2f n %d' % (xs[len(xs) // 10], xs[len(xs) // 2], xs[9 * len(xs) // 10], len(xs)))
sub = [r for r in rows if r['prem'] is not None]
for f in ('net_60', 'bshare_60', 'ub_60', 'mxb_300', 'tmom_60', 'bshare_300'):
    out = []
    for h in ('gs_300', 'go_300', 'gs_900', 'go_900'):
        c, n = ic(sub, f, h)
        out.append('%s %+.3f' % (h, c))
    cp, n = ic(sub, f, 'prem')
    print('  %-11s %s | IC with on-chain premium %+.3f (n %d)' % (f, '  '.join(out), cp, n))

print('\n==== on-chain premium at the fill (%), by quintile of series/tape features (how much a series-priced fill flatters entries)')
for f in ('dmom_60', 'dmom_300', 'pc5', 'tmom_60', 'net_60', 'bshare_60'):
    v = sorted([r for r in sub if r[f] is not None and r[f] == r[f]], key=lambda r: r[f])
    parts = []
    for q in range(5):
        g = v[q * len(v) // 5:(q + 1) * len(v) // 5]
        ps = sorted(r['prem'] for r in g)
        parts.append('[%.3g..%.3g] mean %+.2f med %+.2f' % (g[0][f], g[-1][f], sum(ps) / len(ps), ps[len(ps) // 2]))
    print('  %-10s' % f, ' | '.join(parts))
