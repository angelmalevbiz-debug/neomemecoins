"""analyze1: TRAIN-only descriptive look at the decision points (no book, every point = one hypothetical entry).
Unconditional outcome per fee bucket / hold / fill model, feature quantiles, and within-pool timing contrast of
buy-burst points vs other points (pair-bootstrap CI). PAPER research only."""
import math, os, pickle, random, sys, time
from collections import defaultdict

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
D = pickle.load(open(os.path.join(HERE, 'points.pkl'), 'rb'))
PTS, CUT = D['points'], D['cut']
TR = [p for p in PTS if p['t'] < CUT]
HO = [p for p in PTS if p['t'] >= CUT]
print('points', len(PTS), 'train', len(TR), 'holdout', len(HO))


def fb(fee):
    return 'le50' if fee <= 50 else ('55_95' if fee <= 95 else '100_125')


def q(xs, ps=(0.1, 0.25, 0.5, 0.75, 0.9, 0.99)):
    xs = sorted(xs)
    return [round(xs[min(len(xs) - 1, int(p * len(xs)))], 3) for p in ps] if xs else []


def mean(xs):
    return sum(xs) / len(xs) if xs else float('nan')


print('\n== unconditional (every guarded decision point), TRAIN, mean net0 / net50 %; n; fallback share')
for b in ('le50', '55_95', '100_125'):
    sub = [p for p in TR if fb(p['fee']) == b and p['liq'] >= 50_000]
    print('bucket', b, 'liq>=50k points', len(sub), 'pools', len({p['pair'] for p in sub}))
    for m in ('A', 'B2', 'B5'):
        line = []
        for h in (60, 120, 180, 300):
            o = [p[m][h] for p in sub if p[m][h] is not None]
            line.append('%ds n%d %.2f/%.2f fb%.2f' % (h, len(o), mean([x['net0'] for x in o]), mean([x['net50'] for x in o]),
                                                       mean([1.0 if x['flag'] == 'fb' else 0.0 for x in o])))
        print('   ', m, ' | '.join(line))

print('\n== feature quantiles (TRAIN, guarded, liq>=50k, fee<=95)')
sub = [p for p in TR if p['fee'] <= 95 and p['liq'] >= 50_000]
for k in ('ub', 'nb', 'ns', 'net_sol', 'bsol', 'top_share'):
    for w in ('f30', 'f60'):
        xs = [p[w][k] for p in sub if p[w] and p[w][k] == p[w][k]]
        print('  ', w, k, q(xs))
print('   tret60', q([p['tret60'] for p in sub if p['tret60'] == p['tret60']]))
print('   heat any share', round(mean([1.0 if p['heat'] else 0.0 for p in sub]), 3))
hc = defaultdict(int)
for p in sub:
    for f in p['heat']:
        hc[f] += 1
print('   heat flags', dict(hc))
print('   mid_age', q([p['mid_age'] for p in sub if p['mid_age'] is not None]))


def pair_boot_diff(rows, reps=1000, seed=3):
    """rows: list of (pair, signal_bool, value). Within-pair difference of means (signal - other), pooled by pair
    weights = number of signal rows; bootstrap over pairs."""
    by = defaultdict(lambda: ([], []))
    for pr, sg, v in rows:
        by[pr][0 if sg else 1].append(v)
    items = [(pr, a, b) for pr, (a, b) in by.items() if a and b]
    if len(items) < 3:
        return None

    def est(its):
        num = den = 0.0
        for _, a, b in its:
            w = len(a)
            num += w * (mean(a) - mean(b))
            den += w
        return num / den if den else float('nan')
    e = est(items)
    rnd = random.Random(seed)
    bs = sorted(est([items[rnd.randrange(len(items))] for _ in items]) for _ in range(reps))
    return round(e, 3), round(bs[int(.025 * reps)], 3), round(bs[int(.975 * reps)], 3), len(items), sum(len(a) for _, a, _ in items)


print('\n== within-pool timing contrast (TRAIN, guarded, liq>=50k, fee<=95): burst points minus other points, net0 %')
burst_defs = {
    'ub30>=3&net>0&top<.5': lambda f: f['f30']['ub'] >= 3 and f['f30']['net_sol'] > 0 and f['f30']['top_share'] < 0.5,
    'ub30>=4&net>0&top<.5': lambda f: f['f30']['ub'] >= 4 and f['f30']['net_sol'] > 0 and f['f30']['top_share'] < 0.5,
    'ub60>=5&net>0&top<.5': lambda f: f['f60']['ub'] >= 5 and f['f60']['net_sol'] > 0 and f['f60']['top_share'] < 0.5,
    'ub30>=2&net>=1': lambda f: f['f30']['ub'] >= 2 and f['f30']['net_sol'] >= 1.0,
    'net30<=-1 (sell burst)': lambda f: f['f30']['net_sol'] <= -1.0,
}
for name, fn in burst_defs.items():
    for m in ('B2', 'B5', 'A'):
        out = []
        for h in (60, 120, 300):
            rows = [(p['pair'], bool(fn(p)), p[m][h]['net0']) for p in sub if p[m][h] is not None]
            share = mean([1.0 if r[1] else 0.0 for r in rows])
            out.append('%ds %s sig%.3f' % (h, pair_boot_diff(rows), share))
        print('  %-26s %-3s %s' % (name, m, ' | '.join(out)))
