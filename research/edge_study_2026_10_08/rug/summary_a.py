"""Step (a) summary: drain counts, timing and pool characteristics (all PumpSwap pools)."""
import collections, os, pickle, sys
from rugcommon import HERE, _series, META, CUT

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
rows = pickle.load(open(os.path.join(HERE, 'labels.pkl'), 'rb'))['rows']
t0 = META['t0']


def q(xs, ps=(0.1, 0.25, 0.5, 0.75, 0.9)):
    xs = sorted(xs)
    return '/'.join('%.3g' % xs[min(len(xs) - 1, int(p * len(xs)))] for p in ps) if xs else '-'


def family(s, k):
    liq, mc, age = s['liq'][k], s['mcap'][k], s['age'][k]
    lmc = liq / mc if mc > 0 else float('nan')
    if mc >= 20e6 and lmc < 0.02 and age < 14 * 1440:
        return 'FAKE_MCAP'
    if lmc >= 1.0:
        return 'LP_PULLABLE'
    if age < 60:
        return 'YOUNG<60m'
    if age < 720:
        return 'YOUNG1-12h'
    return 'OLDER'


for qs in (1, 0):
    ps = [r for r in rows if r['dex'] == 'pumpswap' and r['quote_sol'] == qs]
    dr = [r for r in ps if r['drain_t'] is not None and not all(r['events'][k][2] for k in r['kinds'])]
    print('== PumpSwap quote_sol=%d pools %d, terminal drains %d (recovered/glitch %d)' % (
        qs, len(ps), len(dr), sum(1 for r in ps if r['drain_t'] is not None) - len(dr)))
    kinds = collections.Counter('+'.join(r['kinds']) for r in dr)
    print('   by first-trigger kinds:', dict(kinds))
    fast = [r for r in dr if 'LIQ_FAST' in r['kinds'] or 'PRICE_FAST' in r['kinds']]
    print('   fast (<=10 min) drains: %d, slow price collapses only: %d' % (len(fast), len(dr) - len(fast)))
    fam = collections.Counter()
    lb = collections.Counter()
    ages, seen, hours = [], [], collections.Counter()
    for r in dr:
        s = _series[r['pair']]
        k = max(0, r['drain_k'] - 1)
        fam[family(s, k)] += 1
        l = s['liq'][k]
        lb['<20k' if l < 20e3 else '20-50k' if l < 50e3 else '50-250k' if l < 250e3 else '>=250k'] += 1
        ages.append(s['age'][k])
        seen.append((r['drain_t'] - s['t'][0]) / 60000)
        hours[int((r['drain_t'] - t0) // (2 * 3.6e6)) * 2] += 1
    print('   pre-drain family signature:', dict(fam))
    print('   pre-drain liquidity bucket:', dict(lb))
    print('   pair age at drain (min) p10/25/50/75/90:', q(ages))
    print('   minutes observed before drain p10/25/50/75/90:', q(seen))
    print('   drains per 2-hour bin of the dataset (hour offset: count):', dict(sorted(hours.items())))
    print('   train/holdout drains:', sum(1 for r in dr if r['drain_t'] < CUT), sum(1 for r in dr if r['drain_t'] >= CUT))
    big = [r for r in dr if max(x for x in _series[r['pair']]['liq'][:r['drain_k'] + 1] if x == x) >= 250e3]
    print('   drained pools that had >= $250k liquidity: %d -> by signature %s' % (
        len(big), dict(collections.Counter(family(_series[r['pair']], max(0, r['drain_k'] - 1)) for r in big))))
