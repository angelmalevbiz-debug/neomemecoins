"""Step 4 (TRAIN ONLY): the 'market-wide dip' (contrarian) regime in detail.
(a) market-level: median forward 30-min gross return of active pools vs med15 bin (train minutes);
(b) panel of 1/min random samples in U_liquid: forward net50 by med15 bin with hour-block bootstrap CI;
(c) strategy variants with alternative dip measures (native-price, adaptive rank, breadth)."""
import os, pickle, sys, math, random, bisect
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import f9lib as L
H = L.H

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
series, meta = H.load()
cut = H.split_t(meta)
g = L.build_grid(series, meta)
L.add_tide(g, series, L.U_liquid)
with open(os.path.join(HERE, 'grid.pkl'), 'wb') as fh:
    pickle.dump(g, fh)
T0 = meta['t0']


def hb_ci(rows, key, reps=1000, seed=3):
    """mean and 95% CI with bootstrap over hour blocks"""
    blocks = {}
    for r in rows:
        blocks.setdefault(int((r['t'] - T0) // 3.6e6), []).append(r[key])
    bl = list(blocks.values())
    if len(bl) < 3:
        return float('nan'), None, None, len(bl)
    rnd = random.Random(seed)
    ms = []
    for _ in range(reps):
        tot = cnt = 0
        for _ in range(len(bl)):
            b = bl[rnd.randrange(len(bl))]
            tot += sum(b)
            cnt += len(b)
        ms.append(tot / cnt)
    ms.sort()
    allv = [v for b in bl for v in b]
    return sum(allv) / len(allv), ms[int(reps * .025)], ms[int(reps * .975)], len(bl)


BINS = [(-99, -0.5), (-0.5, -0.2), (-0.2, 0.0), (0.0, 0.3), (0.3, 99)]

# (a) market-level forward median gross 30-min return per minute
print('== (a) market level, TRAIN minutes: median fwd 30-min gross % of active liquid pools, by med15 bin')
fwd = []
for m in range(g['M']):
    T = g['g0'] + m * L.MIN
    if T + 1_800_000 >= cut:
        break
    v = g['med15'][m]
    if v != v:
        continue
    rets = []
    for pair, s in series.items():
        if s['dex'] != 'pumpswap' or s['quote_sol'] != 1:
            continue
        ts = s['t']
        if ts[0] > T or ts[-1] < T + 1_800_000:
            continue
        k = bisect.bisect_right(ts, T) - 1
        if k < 0 or T - ts[k] > L.ACTIVE_MS or not (s['liq'][k] >= 50_000):
            continue
        k2 = bisect.bisect_left(ts, T + 1_800_000)
        if k2 >= len(ts) or ts[k2] - (T + 1_800_000) > 300_000:
            continue
        if s['price'][k] > 0:
            rets.append((s['price'][k2] / s['price'][k] - 1) * 100)
    if len(rets) >= 5:
        fwd.append({'t': T, 'med15': v, 'f': L._median(rets)})
    if m % 5:
        pass
for lo, hi in BINS:
    rows = [r for r in fwd if lo <= r['med15'] < hi]
    mu, a, b, nb = hb_ci(rows, 'f')
    print('   med15 in [%5.1f,%5.1f) minutes=%4d hours=%2d  median-pool fwd30 gross %% mean=%6.3f ci=[%s, %s]' % (lo, hi, len(rows), nb, mu, a and round(a, 3), b and round(b, 3)))

# (b) panel
print('== (b) panel U_liquid, TRAIN: 1/min random samples, forward net50 (%), by med15 bin, hour-block CI')
rows = []
for pair, s in series.items():
    if s['dex'] != 'pumpswap' or s['quote_sol'] != 1:
        continue
    ts = s['t']
    last = -1e18
    for i in range(len(ts) - 1):
        if ts[i] - last < 60_000 or ts[i] >= cut:
            continue
        P = H.Past(s, i)
        if not L.U_liquid(P):
            continue
        last = ts[i]
        f30 = H.forward_net(s, i, 1800, 200.0, 50.0)
        f60 = H.forward_net(s, i, 3600, 200.0, 50.0)
        if f30 is None or f60 is None:
            continue
        rows.append({'t': ts[i], 'pair': pair, 'f30': f30, 'f60': f60, 'med15': L.at(g, 'med15', ts[i]),
                     'med15n': L.at(g, 'med15n', ts[i]), 'rank': L.at(g, 'med15_rank', ts[i]), 'fee': H.fee_bps(s, i)})
for col, bins in (('med15', BINS), ('med15n', BINS), ('rank', [(0, 0.1), (0.1, 0.25), (0.25, 0.5), (0.5, 0.75), (0.75, 1.01)])):
    for lo, hi in bins:
        sub = [r for r in rows if r[col] == r[col] and lo <= r[col] < hi]
        m30, a30, b30, nb = hb_ci(sub, 'f30')
        m60, a60, b60, _ = hb_ci(sub, 'f60')
        print('   %-6s [%5.2f,%5.2f) n=%5d hours=%2d pairs=%2d  f30=%6.2f [%s,%s]  f60=%6.2f [%s,%s]' % (
            col, lo, hi, len(sub), nb, len({r['pair'] for r in sub}), m30, a30 and round(a30, 2), b30 and round(b30, 2),
            m60, a60 and round(a60, 2), b60 and round(b60, 2)))
# by fee class within med15<-0.2
for fl, fh_ in ((0, 60), (60, 100), (100, 200)):
    sub = [r for r in rows if fl <= r['fee'] < fh_]
    sub2 = [r for r in sub if r['med15'] == r['med15'] and r['med15'] < -0.2]
    if sub:
        print('   fee[%d,%d) all n=%d f30=%.2f f60=%.2f | med15<-0.2 n=%d f30=%.2f f60=%.2f' % (
            fl, fh_, len(sub), sum(r['f30'] for r in sub) / len(sub), sum(r['f60'] for r in sub) / len(sub),
            len(sub2), sum(r['f30'] for r in sub2) / max(1, len(sub2)), sum(r['f60'] for r in sub2) / max(1, len(sub2))))

# (c) strategy variants
configs = 0


def filt(base, col, lo=None, hi=None):
    def f(P):
        if not base(P):
            return False
        v = L.at(g, col, P.t)
        if v != v:
            return False
        if lo is not None and v < lo:
            return False
        if hi is not None and v >= hi:
            return False
        return True
    return f


def line(tr):
    if not tr:
        return 'n=0'
    sm = H.summarize(tr)
    return 'n=%3d pairs=%2d mean50=%6.2f med=%6.2f win=%4.1f pf=%s top=%.2f ci=%s' % (
        sm['n'], sm['pairs'], sm['mean_pct'], sm['median_pct'], sm['win_rate'], sm['pf'], sm['top_pair_share'], sm['ci95_mean_usd'])


E1 = dict(stop=-5, tp=10, hold_min=60)
E2 = dict(stop=-15, tp=20, hold_min=60)
E3 = dict(stop=-99, tp=None, hold_min=30)
print('== (c) strategy variants, TRAIN entries only')
for bn, base in (('RANDOM', L.sig_random(0.004)), ('MOMO', L.sig_momentum), ('DIP', L.sig_dip)):
    for en, kw in (('E2', E2), ('E3', E3)):
        for fname, col, lo, hi in (('med15n<-0.2', 'med15n', None, -0.2), ('rank<0.2', 'med15_rank', None, 0.2),
                                   ('br15<0.35', 'br15', None, 0.35), ('med15<-0.5', 'med15', None, -0.5)):
            tr = H.simulate(filt(base, col, lo, hi), t_to=cut, **kw)
            configs += 1
            print('  %-6s %s %-12s %s' % (bn, en, fname, line(tr)))
tr = H.simulate(filt(L.sig_dip, 'med15', None, -0.2), t_to=cut, **E1)
configs += 1
print('  DIP    E1 med15<-0.2   ', line(tr))
print('configs_tried step4:', configs)
