"""F1 exploration step 4 (TRAIN ONLY): 'quality' momentum variants and longer-horizon trend.

- volatility-normalised momentum z300 = r300 / stdev(60s returns over past 60 min) / sqrt(5)
- organic momentum: unique wallets (hp_uw5), repeat-buy share (hp_rb5/hp_uw5), tape flow (vf_*), 5-min flow (sf_*)
- long trend: pc6h, pc24 (runners) with fwd up to 120 min
Forward = model-cost net % (vanish-aware); stressed = minus ~1.0.
"""
import bisect, math, sys
sys.path.insert(0, (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r'')
import harness as H
import features as F

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
series, meta = H.load()
feats = F.load()
cut = H.split_t(meta)
HZ = (300, 900, 3600, 7200)


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


def vol60(s, i):
    """stdev of ~60 s log returns over the past 60 minutes (past-only)."""
    ts, px = s['t'], s['price']
    rets = []
    t = ts[i]
    k_prev = None
    for m in range(0, 61):
        k = bisect.bisect_right(ts, t - m * 60_000, 0, i + 1) - 1
        if k < 0:
            break
        if k_prev is not None and px[k] > 0 and px[k_prev] > 0:
            rets.append(math.log(px[k_prev] / px[k]))
        k_prev = k
    if len(rets) < 20:
        return None
    mu = sum(rets) / len(rets)
    return math.sqrt(sum((r - mu) ** 2 for r in rets) / (len(rets) - 1))


rows = []
for pair, f in feats.items():
    s = series[pair]
    ts = s['t']
    last = -1e18
    for i in range(len(ts) - 1):
        if ts[i] - last < 60_000:
            continue
        if ts[i] + 3600_000 > cut:
            break
        last = ts[i]
        if f['rug'][i] > 0:
            continue
        u = universe(f['fee'][i], s['liq'][i])
        if u is None:
            continue
        fw = [fwd(s, i, h) if ts[i] + h * 1000 <= cut else None for h in HZ]
        if fw[2] is None:
            continue
        sd = vol60(s, i)
        r300 = f['r300'][i]
        z = (r300 / 100) / (sd * math.sqrt(5)) if sd and sd > 0 and r300 == r300 else float('nan')
        uw, rb = s['hp_uw5'][i], s['hp_rb5'][i]
        rows.append({'pair': pair, 'u': u, 'fw': fw, 'z300': z, 'sd': sd if sd else float('nan'),
                     'uw5': uw, 'rbshare': rb / uw if uw > 0 else float('nan'),
                     'vf_net': (s['vf_buy'][i] - s['vf_sell'][i]) / (s['vf_buy'][i] + s['vf_sell'][i]) if s['vf_buy'][i] + s['vf_sell'][i] > 0 else float('nan'),
                     'vf_wallets': s['vf_wallets'][i],
                     'sf_net': (s['sf_buy'][i] - s['sf_sell'][i]) / (s['sf_buy'][i] + s['sf_sell'][i]) if s['sf_buy'][i] + s['sf_sell'][i] > 0 else float('nan'),
                     'sf_wallets': s['sf_wallets'][i],
                     'pc6h': s['pc6h'][i], 'pc24': s['pc24'][i], 'r300': r300, 'r1800': f['r1800'][i],
                     'conv': s['conv'][i], 'score': s['score'][i], 'boost': s['boost'][i]})
print('samples', len(rows))


def stat(rs, h):
    xs = [r['fw'][h] for r in rs if r['fw'][h] is not None]
    if not xs:
        return '-'
    bp = {}
    for r in rs:
        if r['fw'][h] is not None:
            bp.setdefault(r['pair'], []).append(r['fw'][h])
    pm = sum(sum(v) / len(v) for v in bp.values()) / len(bp)
    return 'n%d p%d m%.2f pm%.2f' % (len(xs), len(bp), sum(xs) / len(xs), pm)


print('\ncoverage (share of samples with a finite / positive value)')
for u in ('HI30', 'HI50', 'MID', 'LO'):
    rs = [r for r in rows if r['u'] == u]
    cov = {k: round(sum(1 for r in rs if r[k] == r[k] and r[k] > 0) / len(rs), 3) for k in ('uw5', 'vf_wallets', 'sf_wallets', 'conv', 'score', 'boost')}
    cov.update({k + '_finite': round(sum(1 for r in rs if r[k] == r[k]) / len(rs), 3) for k in ('vf_net', 'sf_net', 'z300')})
    print(u, len(rs), cov)

print('\nquintiles -> fwd 5m | 15m | 60m | 120m')
for u in ('HI30', 'HI50', 'MID', 'LO'):
    rs = [r for r in rows if r['u'] == u]
    print('\n###', u, len(rs))
    for fn in ('z300', 'sd', 'uw5', 'rbshare', 'vf_net', 'vf_wallets', 'sf_net', 'sf_wallets', 'pc6h', 'pc24', 'conv', 'score'):
        vals = [r for r in rs if r[fn] == r[fn]]
        if len(vals) < 150:
            continue
        vals.sort(key=lambda r: r[fn])
        parts = []
        for b in range(5):
            seg = vals[b * len(vals) // 5:(b + 1) * len(vals) // 5]
            parts.append('[%.3g..%.3g] %s' % (seg[0][fn], seg[-1][fn], ' / '.join(stat(seg, h).split(' m')[1] if stat(seg, h) != '-' else '-' for h in range(4)) + ' p%d' % len(set(r['pair'] for r in seg))))
        print(' %-10s' % fn, ' | '.join(parts))

print('\ncombos: momentum (r300>=2) x organic (uw5>=10 & rbshare<=0.3) -> fwd 5|15|60|120')
for u in ('HI30', 'HI50', 'MID', 'LO'):
    rs = [r for r in rows if r['u'] == u]
    for name, cond in (('mom', lambda r: r['r300'] >= 2),
                       ('mom&organic', lambda r: r['r300'] >= 2 and r['uw5'] >= 10 and r['rbshare'] <= 0.3),
                       ('organic', lambda r: r['uw5'] >= 10 and r['rbshare'] <= 0.3),
                       ('mom&vfbuy', lambda r: r['r300'] >= 2 and r['vf_net'] >= 0.3),
                       ('z300>=2', lambda r: r['z300'] >= 2),
                       ('z300>=2&organic', lambda r: r['z300'] >= 2 and r['uw5'] >= 10),
                       ('runner pc6h>=50&r1800>=0', lambda r: r['pc6h'] >= 50 and r['r1800'] >= 0),
                       ('runner pc24>=100&pc6h>=0', lambda r: r['pc24'] >= 100 and r['pc6h'] >= 0)):
        seg = [r for r in rs if cond(r)]
        print(' %-4s %-26s %s' % (u, name, ' | '.join(stat(seg, h) for h in range(4))))
