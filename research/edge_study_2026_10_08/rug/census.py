"""Step (a) detail: per-drain characteristics for PumpSwap pools (read-only)."""
import bisect, collections, os, pickle, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import harness as H

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
series, meta = H.load()
lab = pickle.load(open(os.path.join(HERE, 'labels.pkl'), 'rb'))
t0, t1 = meta['t0'], meta['t1']
cut = H.split_t(meta)


def hr(t):
    return round((t - t0) / 3.6e6, 2)


def fmt(x, d=0):
    return 'nan' if x != x else round(x, d)


out = []
for r in lab['rows']:
    if r['dex'] != 'pumpswap':
        continue
    s = series[r['pair']]
    if r['drain_k'] is None:
        continue
    k = r['drain_k']
    ts = s['t']
    # state just before the event and 10 min / 60 min before
    b = max(0, k - 1)
    kb10 = max(0, bisect.bisect_right(ts, ts[k] - 600_000) - 1)
    P = H.Past(s, b)
    mc, liq = s['mcap'][b], s['liq'][b]
    after = min(len(ts) - 1, k + 3)
    reuse = H.other_pairs_same_ticker_before(P)
    out.append({
        'sym': (s['sym'] or '')[:10], 'pair': r['pair'][:8], 'qsol': s['quote_sol'], 'pump': r['mint_pump'],
        'kinds': '+'.join(x[0] + x[x.index('_') + 1] for x in r['kinds']),
        'h': hr(ts[k]), 'seen_min': round((ts[k] - ts[0]) / 60000, 1), 'age_min': fmt(s['age'][b], 1),
        'liq_b': fmt(liq / 1e3, 1), 'liq_peak_k': fmt(max(s['liq'][:k + 1]) / 1e3, 1), 'liq_a': fmt(s['liq'][after] / 1e3, 1),
        'liq10_k': fmt(s['liq'][kb10] / 1e3, 1),
        'mc_m': fmt(mc / 1e6, 2), 'lmc%': fmt(100 * liq / mc, 2) if mc > 0 else 'nan',
        'p_drop%': fmt(100 * (s['price'][after] / max(s['price'][:k + 1]) - 1), 1),
        'fee': H.fee_bps(s, b), 'reuse': reuse, 'src': int(s['src'][b]), 'boost': s['boost'][b],
        'b5': s['b5'][b], 's5': s['s5'][b], 'score': s['score'][b], 'rec': all(r['events'][x][2] for x in r['kinds']),
        'van': r['vanished'], 'pts_after': len(ts) - 1 - k,
    })
out.sort(key=lambda x: (x['kinds'], x['h']))
cols = list(out[0].keys())
print('\t'.join(cols))
for x in out:
    print('\t'.join(str(x[c]) for c in cols))

# group summaries
print()
c = collections.Counter()
for x in out:
    lb = x['liq_b']
    lbk = 'liq<10k' if lb < 10 else 'liq10-50k' if lb < 50 else 'liq50-250k' if lb < 250 else 'liq>=250k'
    c[(x['kinds'], lbk)] += 1
for k in sorted(c):
    print(k, c[k])
