"""Audit step 8: statistics helpers (cluster CI, summarize, portfolio), cluster structure, sample adequacy, rug-screen timing."""
import collections, math, random, sys, time, importlib
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.path.insert(0, (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r'')
H = importlib.import_module(sys.argv[1] if len(sys.argv) > 1 else 'harness')
from smoke_common import cost_first, rnd

series, meta = H.load()
cut = H.split_t(meta)

# 1) summarize / portfolio edge cases
one = [{'pair': 'A', 'entry_t': 0, 'exit_t': 10, 'usd50': 5.0, 'usd0': 6.0, 'net50': 2.5, 'net0': 3.0, 'hold_s': 10, 'reason': 'TP', 'flag': ''}]
print('summarize n=1:', H.summarize(one))
two = one + [{'pair': 'B', 'entry_t': 5, 'exit_t': 20, 'usd50': -1.0, 'usd0': -0.5, 'net50': -0.5, 'net0': -0.25, 'hold_s': 15, 'reason': 'STOP', 'flag': ''}]
print('summarize n=2 median_pct (true median 1.0):', H.summarize(two)['median_pct'])
# portfolio: slots=1 must skip the overlapping trade B; balance 1000+5
print('portfolio slots=1 (expect taken 1, final 1005):', H.portfolio(two, slots=1))
# drawdown check: +10 then -20 then +5 -> peak 1010, trough 990 -> mdd 1.98%
seq = [{'pair': c, 'entry_t': k * 100, 'exit_t': k * 100 + 50, 'usd50': v} for k, (c, v) in enumerate([('A', 10), ('B', -20), ('C', 5)])]
print('portfolio mdd (expect 1.98):', H.portfolio(seq, slots=3))

# 2) cluster CI: compare (pair, hour) clusters with pair-level clusters on real random-entry trades
def pair_ci(trades, key='usd50', reps=2000, seed=7):
    cl = collections.defaultdict(list)
    for x in trades:
        cl[x['pair']].append(x[key])
    g = list(cl.values())
    r = random.Random(seed)
    ms = []
    for _ in range(reps):
        tot = cnt = 0
        for _ in range(len(g)):
            v = g[r.randrange(len(g))]
            tot += sum(v); cnt += len(v)
        ms.append(tot / cnt)
    ms.sort()
    return round(ms[int(reps * .025)], 3), round(ms[int(reps * .975)], 3), len(g)


def iid_se(xs):
    n = len(xs); m = sum(xs) / n
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (n - 1) / n)


for name, sig, extra in (
        ('random_all_ps', lambda P: rnd(0.002)(P), {}),
        ('random_mid_fee', lambda P: 50 < P.fee_bps() <= 125 and P('liq') >= 50_000 and not H.interim_rug_risk(P) and rnd(0.004)(P), {})):
    tr = H.simulate(sig, stop=-5, tp=10, hold_min=60, **extra)
    for part, sub in (('train', [x for x in tr if x['entry_t'] < cut]), ('holdout', [x for x in tr if x['entry_t'] >= cut])):
        xs = [x['usd50'] for x in sub]
        m = sum(xs) / len(xs)
        lo, hi, ncl = H.cluster_ci(sub)
        plo, phi, npc = pair_ci(sub)
        se = iid_se(xs)
        sd = math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))
        print('%s %s n %d mean $%.2f sd $%.2f | iid CI [%.2f, %.2f] | (pair,hour) CI [%s, %s] clusters %d | pair CI [%s, %s] pairs %d' % (
            name, part, len(xs), m, sd, m - 1.96 * se, m + 1.96 * se, lo, hi, ncl, plo, phi, npc))
        # minimum detectable mean (80% power, two-sided 5%) at this sd and n, with design effect from pair clustering
        width_pair = (phi - plo) / 2 / 1.96 if phi is not None else None
        deff = (width_pair / se) ** 2 if width_pair else None
        print('    design effect (pair) %.2f -> effective n %.0f; MDE80 ~ $%.2f per $200 trade (%.2f%%)' % (
            deff, len(xs) / deff, 2.8 * width_pair, 2.8 * width_pair / 2))

# 3) regime drift between train and holdout for the same random universe
tr = H.simulate(lambda P: rnd(0.002)(P), stop=-5, tp=10, hold_min=60)
by_h = collections.defaultdict(list)
for x in tr:
    by_h[int((x['entry_t'] - meta['t0']) // 3.6e6)].append(x['net50'])
print('random all-PumpSwap mean net50 by hour since start:', {h: (len(v), round(sum(v) / len(v), 1)) for h, v in sorted(by_h.items())})

# 4) rug-screen timing: when did the rug-family pools drain (train or holdout)?
rows = []
for pair, s in series.items():
    if s['dex'] != 'pumpswap' or s['quote_sol'] != 1:
        continue
    first_cf = next((i for i in range(len(s['t'])) if s['liq'][i] >= 250_000 and H.fee_bps(s, i) <= 50), None)
    if first_cf is None:
        continue
    liq = s['liq']
    peak = max(liq[first_cf:])
    if not (liq[-1] < 0.1 * peak):
        continue
    drain_t = next((s['t'][k] for k in range(first_cf, len(liq)) if liq[k] < 0.1 * peak), None)
    P = H.Past(s, first_cf)
    rows.append(((s['sym'] or '')[:10], pair[:8], 'drain', 'train' if drain_t < cut else 'holdout',
                 'flag_at_first_cf', H.interim_rug_risk(P), 'mcap_m', round(s['mcap'][first_cf] / 1e6, 1),
                 'liq/mcap%', round(100 * s['liq'][first_cf] / s['mcap'][first_cf], 2) if s['mcap'][first_cf] > 0 else None))
for r in sorted(rows, key=lambda r: r[3]):
    print('  rug', r)
print('rugs drained in train', sum(1 for r in rows if r[3] == 'train'), 'holdout', sum(1 for r in rows if r[3] == 'holdout'))
