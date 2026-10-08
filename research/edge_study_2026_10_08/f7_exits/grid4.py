"""F7 step 5 (TRAIN ONLY): volatility-scaled exits with H.simulate position semantics.
sigma_e = stdev of 5-minute % price changes over the 60 min before the decision point (past-only; needs >= 4
returns, else the event's pool-median fallback 8%). Levels are relative to the post-entry start n0 = net at the
first path point (~ minus the round-trip cost): stop at n0 - a*sigma, TP at n0 + b*sigma, trailing armed at
n0 + b1*sigma with width w*sigma. Writes grid_train4.pkl."""
import bisect, math, pickle, sys, time
sys.path.insert(0, (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r'')
import harness as H
import exitlab as X
from common import UNIVERSES

OUT = (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r'/f7_exits/grid_train4.pkl'
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
t0 = time.time()
data = X.load_events()
series, meta = H.load()
cut = H.split_t(meta)
mid_train = meta['t0'] + 0.5 * (cut - meta['t0'])
UNIS = dict(UNIVERSES)
UNIS['hiall'] = ('hi', 'hi20')
exec(open((__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r'/f7_exits/aggfmt.py').read())
VA = (1, 2, 3, 5)
VB = (1, 2, 4, 8)
VTR = tuple((b1, w) for b1 in (1, 2, 4) for w in (0.5, 1, 2))
FALLBACK = 8.0


def sigma_at(s, i):
    ts, px = s['t'], s['price']
    vals = []
    for m in range(0, 65, 5):
        k = bisect.bisect_right(ts, ts[i] - m * 60_000, 0, i + 1) - 1
        if k < 0:
            break
        vals.append(px[k])
    rets = [100 * (vals[q] / vals[q + 1] - 1) for q in range(len(vals) - 1) if vals[q] > 0 and vals[q + 1] > 0]
    if len(rets) < 4:
        return None
    mu = sum(rets) / len(rets)
    return math.sqrt(sum((r - mu) ** 2 for r in rets) / (len(rets) - 1))


def first(nets, start, cond):
    for p in range(start, len(nets)):
        if cond(nets[p]):
            return p
    return X.INF


def add_vol_components(lab):
    sig_all = []
    for idx, ev in enumerate(lab.events):
        sg = sigma_at(lab.series[ev['pair']], ev['i'])
        ev['sigma'] = sg
        sig_all.append(sg)
    for idx, ev in enumerate(lab.events):
        nets = ev['nets']
        fp = lab.fps[idx]
        sg = ev['sigma'] if ev['sigma'] is not None else FALLBACK
        sg = max(sg, 0.5)
        n0 = nets[0] if len(nets) else 0.0
        for a in VA:
            fp[('VS', a)] = first(nets, 0, lambda x, L=n0 - a * sg: x <= L)
        for b in VB:
            fp[('VT', b)] = first(nets, 0, lambda x, L=n0 + b * sg: x >= L)
        peak, pk = [], -1e18
        for x in nets:
            pk = max(pk, x)
            peak.append(pk)
        for b1, w in VTR:
            st = first(nets, 0, lambda x, L=n0 + b1 * sg: x >= L)
            r = X.INF
            if st < X.INF:
                for p in range(st, len(nets)):
                    if nets[p] <= peak[p] - w * sg:
                        r = p
                        break
            fp[('VTR', b1, w)] = r
    have = [x for x in sig_all if x is not None]
    have.sort()
    return len(have), len(sig_all), (have[len(have) // 2] if have else None)


grid = {}
for a in VA:
    for b in VB + (None,):
        for h in (60, 240):
            c = [('VS', a)] + ([('VT', b)] if b else []) + [('H', h)]
            grid['VFIX a%s b%s h%s' % (a, b, h)] = tuple(c)
for a in (2, 5):
    for b1, w in VTR:
        grid['VTRAIL a%s b1_%s w%s h240' % (a, b1, w)] = (('VS', a), ('VTR', b1, w), ('H', 240))
print('vol policies per cell', len(grid))

results = {}
for sig, evs in data['events'].items():
    tr_idx = sorted((i for i, e in enumerate(evs) if e['t'] < cut), key=lambda i: evs[i]['t'])
    sub = [evs[i] for i in tr_idx]
    lab = X.Lab(sub)
    print(sig, 'sigma available / events / median sigma %:', add_vol_components(lab))
    for uname, tags in UNIS.items():
        idxs = [i for i in range(len(sub)) if sub[i]['tag'] in tags]
        if not idxs:
            continue
        sg = sorted(sub[i]['sigma'] for i in idxs if sub[i]['sigma'] is not None)
        if sg:
            print('   ', uname, 'sigma p25/p50/p75 %.1f/%.1f/%.1f' % (sg[len(sg) // 4], sg[len(sg) // 2], sg[3 * len(sg) // 4]))
        for name, pol in grid.items():
            res = lab.run_nonoverlap(pol, idxs)
            rows = [(r0, r50, r50 * sub[i]['size'] / 100, sub[i]['pair'], sub[i]['t']) for i, r0, r50 in res]
            a = agg(rows)
            if a:
                results[(sig, uname, name)] = a
with open(OUT, 'wb') as fh:
    pickle.dump(results, fh, protocol=pickle.HIGHEST_PROTOCOL)
print('configs evaluated:', len(results), 'secs', round(time.time() - t0, 1))
for sig in data['events']:
    for uname in UNIS:
        cell = {k[2]: v for k, v in results.items() if k[0] == sig and k[1] == uname}
        if not cell or max(v['n'] for v in cell.values()) < 30:
            continue
        ranked = sorted(cell.items(), key=lambda kv: -kv[1]['mean50'])
        rob = [(n_, a) for n_, a in ranked if a['meanA'] > 0 and a['meanB'] > 0 and a['mean_xtop'] > 0 and a['mean50'] > 0
               and a['n'] >= 30 and a['pairs'] >= 8 and a['top_share'] <= 0.35]
        print('#### %s / %s  robust-positive: %d of %d' % (sig, uname, len(rob), len(ranked)))
        for name, a in ranked[:3]:
            print('  top ', fmt(name, a))
