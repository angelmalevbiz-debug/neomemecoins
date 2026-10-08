"""F1 exploration step 3 (TRAIN ONLY): interactions, market regime, breakout events.

(a) pc1h x r300 terciles (trend-with-pullback vs trend-with-spike)
(b) market regime: past-only cross-sectional median 30-min return over active PumpSwap pairs (per minute bucket)
(c) breakout EVENTS (first new 30-min high by >= X% after >= 20 min without one), forward net at 5/15/30/60 min
All forward values are model-cost net % (stress = minus 1.0 more), vanish-aware, entries and windows before the cut.
"""
import bisect, sys, time
sys.path.insert(0, (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r'')
import harness as H
import features as F

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
series, meta = H.load()
feats = F.load()
cut = H.split_t(meta)
HZ = (300, 900, 1800, 3600)


def fwd(s, i, seconds, notional=200.0):
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


def universe(fee, liq):
    if fee <= 50 and liq >= 250_000:
        return 'LO'
    if 55 <= fee <= 95 and liq >= 100_000:
        return 'MID'
    if fee >= 100 and liq >= 50_000:
        return 'HI50'
    if fee >= 100 and liq >= 30_000:
        return 'HI30'
    return None


# ---- (b) market regime index: per minute, median r1800 over pairs with a point in that minute and liq >= 30k
t0m = int(meta['t0'] // 60000)
buckets = {}
for pair, f in feats.items():
    s = series[pair]
    ts = s['t']
    lastm = None
    for i in range(len(ts)):
        m = int(ts[i] // 60000)
        if m == lastm:
            continue
        lastm = m
        if s['liq'][i] >= 30_000 and f['r1800'][i] == f['r1800'][i] and f['rug'][i] == 0:
            buckets.setdefault(m, []).append(f['r1800'][i])
regime = {}
for m, xs in buckets.items():
    xs.sort()
    regime[m] = (xs[len(xs) // 2], sum(1 for x in xs if x > 0) / len(xs), len(xs))
# past-only use: the regime value at minute m is computed from points inside minute m; use minute m-1 for a decision in m
print('regime minutes', len(regime))

rows = []
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
        m = int(ts[i] // 60000) - 1
        rg = regime.get(m)
        rows.append({'pair': pair, 'u': u, 'fw': fw, 'pc1h': s['pc1h'][i], 'pc6h': s['pc6h'][i], 'r300': f['r300'][i],
                     'r1800': f['r1800'][i], 'reg_med': rg[0] if rg else None, 'reg_br': rg[1] if rg else None})
print('samples', len(rows))


def stat(rs, h):
    xs = [r['fw'][h] for r in rs]
    if not xs:
        return '-'
    bp = {}
    for r in rs:
        bp.setdefault(r['pair'], []).append(r['fw'][h])
    pm = sum(sum(v) / len(v) for v in bp.values()) / len(bp)
    return 'n%d p%d m%.2f pm%.2f' % (len(xs), len(bp), sum(xs) / len(xs), pm)


def terciles(rs, key):
    v = sorted(r[key] for r in rs if r[key] == r[key])
    return v[len(v) // 3], v[2 * len(v) // 3]


print('\n(a) pc1h tercile x r300 tercile -> fwd 15m | 60m')
for u in ('HI30', 'HI50', 'MID', 'LO'):
    rs = [r for r in rows if r['u'] == u and r['pc1h'] == r['pc1h'] and r['r300'] == r['r300']]
    a1, a2 = terciles(rs, 'pc1h')
    b1, b2 = terciles(rs, 'r300')
    print('##', u, 'pc1h cuts', round(a1, 2), round(a2, 2), 'r300 cuts', round(b1, 2), round(b2, 2))
    for ia, (alo, ahi) in enumerate(((-1e9, a1), (a1, a2), (a2, 1e9))):
        line = []
        for ib, (blo, bhi) in enumerate(((-1e9, b1), (b1, b2), (b2, 1e9))):
            seg = [r for r in rs if alo <= r['pc1h'] < ahi and blo <= r['r300'] < bhi]
            line.append('%s || %s' % (stat(seg, 1), stat(seg, 3)))
        print('  pc1h T%d:' % ia, ' ### '.join(line))

print('\n(b) market regime (median r1800 of active pairs, prior minute) terciles x own r300 tercile -> fwd 60m')
for u in ('HI30', 'HI50', 'MID', 'LO'):
    rs = [r for r in rows if r['u'] == u and r['reg_med'] is not None and r['r300'] == r['r300']]
    a1, a2 = terciles(rs, 'reg_med')
    b1, b2 = terciles(rs, 'r300')
    print('##', u, 'regime cuts', round(a1, 2), round(a2, 2))
    for ia, (alo, ahi) in enumerate(((-1e9, a1), (a1, a2), (a2, 1e9))):
        line = []
        for ib, (blo, bhi) in enumerate(((-1e9, b1), (b1, b2), (b2, 1e9))):
            seg = [r for r in rs if alo <= r['reg_med'] < ahi and blo <= r['r300'] < bhi]
            line.append('%s || %s' % (stat(seg, 1), stat(seg, 3)))
        print('  regime T%d:' % ia, ' ### '.join(line))

# ---- (c) breakout events
print('\n(c) breakout events: new 30-min high by >= X% (hi1800 >= X) after >= 20 min with hi1800 < 0; fwd 5/15/30/60')
for X in (0.5, 3.0, 8.0):
    for cond_name, cond in (('any', lambda s, f, i: True),
                            ('b5>=20&bsh5>=.55', lambda s, f, i: s['b5'][i] >= 20 and f['bsh5'][i] >= 0.55),
                            ('vacc>=1.5', lambda s, f, i: f['vacc'][i] >= 1.5),
                            ('r1800>=10', lambda s, f, i: f['r1800'][i] >= 10)):
        ev = {}
        for pair, f in feats.items():
            s = series[pair]
            ts = s['t']
            last_new_high = -1e18
            for i in range(len(ts) - 1):
                if ts[i] + 3600_000 > cut:
                    break
                h = f['hi1800'][i]
                if not (h == h):
                    continue
                if h >= 0:
                    fresh = ts[i] - last_new_high >= 20 * 60_000
                    last_new_high = ts[i]
                    if not fresh or h < X or f['rug'][i] > 0:
                        continue
                    if ts[i] - ts[0] < 30 * 60_000:
                        continue   # need a real 30-min history
                    u = universe(f['fee'][i], s['liq'][i])
                    if u is None or not cond(s, f, i):
                        continue
                    fw = [fwd(s, i, hz) for hz in HZ]
                    if fw[-1] is None:
                        continue
                    ev.setdefault(u, []).append({'pair': pair, 'fw': fw})
        for u in ('HI30', 'HI50', 'MID', 'LO'):
            rs = ev.get(u, [])
            if not rs:
                continue
            print('  X=%.1f %-18s %-4s %s' % (X, cond_name, u, ' | '.join(stat(rs, h) for h in range(4))))
