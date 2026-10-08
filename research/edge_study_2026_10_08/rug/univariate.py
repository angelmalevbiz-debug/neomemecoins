"""TRAIN-only univariate look: feature quantiles for drain-within-2h points vs clean negatives."""
import collections, sys
from rugcommon import load_points, universe

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
pts = load_points()
print('parts', collections.Counter(x['part'] for x in pts))
tr = [x for x in pts if x['part'] == 'train']
print('train points', len(tr), 'pos', sum(x['y2h'] for x in tr), 'clean neg', sum(x['clean_neg'] for x in tr),
      'van2h', sum(x['van2h'] for x in tr), 'drain pairs', len({x['pair'] for x in tr if x['y2h']}))
FEATS = ['liq', 'mcap', 'lmc', 'age', 'seen', 'pump', 'fee', 'reuse', 'bs5', 'bs1h', 'bs6h', 'tx5', 'tx1h', 'v1h', 'vliq1h',
         'usd_per_tx5', 'usd_per_tx1h', 'pc5', 'pc1h', 'pc6h', 'pc24', 'src', 'boost', 'score', 'risk', 'uw5', 'rb5',
         'liq_chg10', 'liq_dd', 'mc_per_agemin']


def q(xs, p):
    xs = sorted(v for v in xs if v == v)
    if not xs:
        return float('nan')
    return xs[min(len(xs) - 1, int(p * len(xs)))]


for uni in ('ALL', 'LIQ50'):
    u = universe(uni)
    S = [x for x in tr if u(x)]
    pos = [x for x in S if x['y2h']]
    neg = [x for x in S if x['clean_neg']]
    van = [x for x in S if x['van2h']]
    print('\n=== universe', uni, 'pos', len(pos), 'pos pairs', len({x['pair'] for x in pos}), 'neg', len(neg),
          'neg pairs', len({x['pair'] for x in neg}), 'van', len(van))
    kinds = collections.Counter()
    for x in pos:
        kinds[x['kind']] += 1
    print('  pos kinds (points):', dict(kinds))
    for f in FEATS:
        def row(g):
            return '/'.join('%.3g' % q([x[f] for x in g], p) for p in (0.1, 0.25, 0.5, 0.75, 0.9))
        nan_pos = sum(1 for x in pos if x[f] != x[f]) / max(1, len(pos))
        nan_neg = sum(1 for x in neg if x[f] != x[f]) / max(1, len(neg))
        print('  %-13s pos %-40s neg %-40s van %-40s nan%% pos %.0f neg %.0f' % (f, row(pos), row(neg), row(van), 100 * nan_pos, 100 * nan_neg))
