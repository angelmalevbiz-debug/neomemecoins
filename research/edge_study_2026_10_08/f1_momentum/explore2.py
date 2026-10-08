"""F1 exploration step 2 (TRAIN ONLY): does past momentum predict forward net return, per universe?

Samples 1 point / 30 s / pair with entry and the whole 60-min forward window before the train cut.
Forward = H.forward_net (model costs, $200). Stressed = minus 1.0 pct-point (50 bps per leg).
"""
import bisect, math, sys, time
sys.path.insert(0, (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r'')
import harness as H
import features as F

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
series, meta = H.load()
feats = F.load()
cut = H.split_t(meta)
HZ = (300, 900, 1800, 3600)


def universe(fee, liq):
    if fee <= 50 and liq >= 250_000:
        return 'LO fee<=50 liq>=250k'
    if 55 <= fee <= 95 and liq >= 100_000:
        return 'MID fee55-95 liq>=100k'
    if fee >= 100 and liq >= 100_000:
        return 'HI fee100+ liq>=100k'
    if fee >= 100 and liq >= 50_000:
        return 'HI fee100+ liq50-100k'
    if fee >= 100 and liq >= 30_000:
        return 'HI fee100+ liq30-50k'
    return None


def fwd(s, i, seconds, notional=200.0):
    """Like H.forward_net, but a pair that vanished before the horizon exits at last price -10%% (no survivorship)."""
    ts = s['t']
    if i + 1 >= len(ts) or ts[i + 1] - ts[i] > H.MAX_ENTRY_LAG_MS:
        return None
    f = H.entry_fill(s, i + 1, notional)
    if f is None:
        return None
    k = bisect.bisect_left(ts, ts[i] + seconds * 1000, i + 2)
    if k >= len(ts):
        k = len(ts) - 1
        if meta['t1'] - ts[k] <= 600_000:
            return None
        v = H.exit_value(s, k, f[0], 0.0, s['price'][k] * 0.9)
    else:
        v = H.exit_value(s, k, f[0])
    return None if v is None else 100 * (v - notional - f[1]) / notional


rows = []
t0 = time.time()
for pair, f in feats.items():
    s = series[pair]
    ts = s['t']
    last = -1e18
    for i in range(len(ts) - 1):
        if ts[i] - last < 30_000:
            continue
        if ts[i] + 3600_000 > cut:
            break
        last = ts[i]
        if f['rug'][i] > 0:
            continue
        u = universe(f['fee'][i], s['liq'][i])
        if u is None:
            continue
        fw = [fwd(s, i, h) for h in HZ]
        if fw[-1] is None:
            continue
        # price path max/min over next 60 min relative to entry point price (i+1)
        j = i + 1
        k = bisect.bisect_left(ts, ts[i] + 3600_000, j)
        seg = s['price'][j:k + 1]
        pj = s['price'][j]
        mx = 100 * (max(seg) / pj - 1) if pj > 0 and seg else 0.0
        mn = 100 * (min(seg) / pj - 1) if pj > 0 and seg else 0.0
        rec = {'pair': pair, 't': ts[i], 'u': u, 'fw': fw, 'mx': mx, 'mn': mn,
               'pc5': s['pc5'][i], 'pc1h': s['pc1h'][i], 'pc6h': s['pc6h'][i], 'age': s['age'][i],
               'hp_uw5': s['hp_uw5'][i], 'b5': s['b5'][i], 'liq': s['liq'][i]}
        for name in ('r60', 'r180', 'r300', 'r600', 'r1800', 'hi600', 'hi1800', 'lo1800', 'vacc', 'bsh5', 'bsh1h',
                     'liqg300', 'liqg1800', 'ntick', 'v5acc_prev'):
            rec[name] = f[name][i]
        rows.append(rec)
print('samples', len(rows), round(time.time() - t0, 1), 's')


def stat(rs, hz_idx):
    xs = [r['fw'][hz_idx] for r in rs]
    if not xs:
        return None
    n = len(xs)
    bypair = {}
    for r in rs:
        bypair.setdefault(r['pair'], []).append(r['fw'][hz_idx])
    pm = sum(sum(v) / len(v) for v in bypair.values()) / len(bypair)
    return n, len(bypair), round(sum(xs) / n, 2), round(sorted(xs)[n // 2], 2), round(pm, 2), round(100 * sum(1 for x in xs if x > 0) / n, 1)


us = sorted(set(r['u'] for r in rows))
print('\n== universe base rates (train, rug-screened); per horizon: n, pairs, mean, median, pair-mean, win%')
for u in us:
    rs = [r for r in rows if r['u'] == u]
    mx = sorted(r['mx'] for r in rs)
    print(u, [stat(rs, h) for h in range(4)], 'p50 max60', round(mx[len(mx) // 2], 1), 'p75 max60', round(mx[int(.75 * len(mx))], 1))

FEATS = ('r60', 'r180', 'r300', 'r600', 'r1800', 'pc5', 'pc1h', 'hi600', 'hi1800', 'lo1800', 'vacc', 'bsh5', 'bsh1h',
         'liqg300', 'liqg1800', 'age', 'hp_uw5', 'b5')
print('\n== quintiles of each feature within universe: (lo..hi edges) and mean fwd net 15m / 60m, pair-mean 60m, pairs')
for u in us:
    rs = [r for r in rows if r['u'] == u]
    print('\n###', u, 'n', len(rs))
    for fn in FEATS:
        vals = [r for r in rs if r[fn] == r[fn]]
        if len(vals) < 200:
            continue
        vals.sort(key=lambda r: r[fn])
        q = 5
        parts = []
        for b in range(q):
            seg = vals[b * len(vals) // q:(b + 1) * len(vals) // q]
            s15 = stat(seg, 1)
            s60 = stat(seg, 3)
            parts.append('[%.3g..%.3g] %s/%s/%s pm%s p%d' % (seg[0][fn], seg[-1][fn], stat(seg, 0)[2], s15[2], s60[2], s60[4], s60[1]))
        print(' %-9s' % fn, ' | '.join(parts))
