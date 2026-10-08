"""F5 screen 1 (TRAIN ONLY): forward stressed net returns by feature quintile inside universes."""
import sys, os, pickle, math
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
HERE = os.path.dirname(os.path.abspath(__file__))
d = pickle.load(open(os.path.join(HERE, 'samples.pkl'), 'rb'))
rows = [r for r in d['rows'] if r['train'] and not r['rug']]
print('train non-rug samples', len(rows))

fin = lambda x: x is not None and x == x and abs(x) != float('inf')

UNIV = {
    'LOWFEE_big': lambda r: r['fee'] <= 50 and r['liq'] >= 250_000,
    'MIDFEE_50k+': lambda r: 50 < r['fee'] <= 95 and r['liq'] >= 50_000,
    'HIFEE_50-250k': lambda r: r['fee'] > 95 and 50_000 <= r['liq'] < 250_000,
    'HIFEE_20-50k': lambda r: r['fee'] > 95 and 20_000 <= r['liq'] < 50_000,
}
FEATS = ['va1h', 'va6h', 'va1h6h', 'ba1h', 'bshare5', 'bshare1h', 'tx5', 'avg_tx_usd', 'dv5_60', 'dv5_120',
         'db5_60', 'db5_120', 'uw5', 'rb5', 'uw_per_tx', 'sf_w', 'sf_bshare', 'vf_w', 'vf_tr', 'vf_bshare',
         'pc5', 'pc1h', 'r60', 'r300', 'r900', 'dliq300', 'age']
SCREENS = 0


def stats(sub, key):
    xs = [r[key] for r in sub if fin(r[key])]
    if not xs:
        return None
    by = {}
    for r in sub:
        if fin(r[key]):
            by.setdefault(r['pair'], []).append(r[key])
    pm = sum(sum(v) / len(v) for v in by.values()) / len(by)
    return len(xs), len(by), sum(xs) / len(xs), pm, 100 * sum(1 for x in xs if x > 0) / len(xs)


def line(label, sub):
    a = stats(sub, 'f900')
    b = stats(sub, 'f1800')
    c = stats(sub, 'f3600')
    mfe = sorted(r['mfe30'] for r in sub if fin(r['mfe30']))
    med_mfe = mfe[len(mfe) // 2] if mfe else float('nan')
    if not b:
        return
    print('   %-26s n=%5d pr=%4d | f15 %6.2f | f30 %6.2f (pairavg %6.2f, win %4.1f) | f60 %s | mfe30med %5.1f' % (
        label, b[0], b[1], a[2] if a else float('nan'), b[2], b[3], b[4],
        ('%6.2f (pairavg %6.2f)' % (c[2], c[3])) if c else '  na', med_mfe))


for un, uf in UNIV.items():
    U = [r for r in rows if uf(r)]
    print('=== universe', un, 'samples', len(U), 'pairs', len({r['pair'] for r in U}))
    line('ALL', U)
    for f in FEATS:
        vals = sorted(r[f] for r in U if fin(r[f]))
        if len(vals) < 200:
            print('  ', f, 'coverage too low', len(vals))
            continue
        qs = [vals[int(len(vals) * p)] for p in (0.2, 0.4, 0.6, 0.8)]
        edges = [-float('inf')] + qs + [float('inf')]
        # collapse duplicate edges (mass points)
        e2 = [edges[0]]
        for e in edges[1:]:
            if e > e2[-1]:
                e2.append(e)
        print('  --', f, 'cov', len(vals), 'edges', [round(x, 3) for x in qs])
        for a, b in zip(e2[:-1], e2[1:]):
            sub = [r for r in U if fin(r[f]) and a < r[f] <= b]
            SCREENS += 1
            line('(%.3g, %.3g]' % (a, b), sub)
print('bins screened', SCREENS)
