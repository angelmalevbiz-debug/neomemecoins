"""F4 extension: ORGANIC attention surges (DexScreener 5-min buy-count / volume jumps vs the 1-h pace).
TRAIN-ONLY event study, PumpSwap SOL pairs, with a same-ticker-different-mint rug screen variant."""
import sys, os, bisect, pickle, time
sys.path.insert(0, (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r'')
import harness as H

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
HERE = os.path.dirname(os.path.abspath(__file__))
HZ = (60, 180, 300, 600, 900, 1800, 3600)
t0 = time.time()
series, meta = H.load()
T1 = meta['t1']
CUT = H.split_t(meta)


def fwd(s, i, sec, extra):
    ts = s['t']
    n = len(ts)
    if i + 1 >= n or ts[i + 1] - ts[i] > H.MAX_ENTRY_LAG_MS:
        return None
    f = H.entry_fill(s, i + 1, 200.0, extra)
    if f is None:
        return None
    k = bisect.bisect_left(ts, ts[i] + sec * 1000, i + 2)
    if k < n:
        v = H.exit_value(s, k, f[0], extra)
    else:
        last = n - 1
        if T1 - ts[last] > 600_000 and ts[last] < ts[i] + sec * 1000:
            v = H.exit_value(s, last, f[0], extra, s['price'][last] * 0.9)
        else:
            return None
    return None if v is None else 100 * (v - 200.0 - f[1]) / 200.0


def fee_b(f):
    return 'fee<=50' if f <= 50 else ('fee55-95' if f <= 95 else 'fee100-125')


def liq_b(l):
    return 'liq>=250k' if l >= 250_000 else ('liq50-250k' if l >= 50_000 else 'liq<50k')


def surge_b(s, k, mult):
    b5, b1h = s['b5'][k], s['b1h'][k]
    return b5 >= 30 and b1h > 0 and b5 >= mult * b1h / 12


def surge_v(s, k, mult):
    v5, v1h = s['v5'][k], s['v1h'][k]
    return v5 >= 2000 and v1h > 0 and v5 >= mult * v1h / 12


rows = []
for pair, s in series.items():
    if s['dex'] != 'pumpswap' or s['quote_sol'] != 1:
        continue
    ts = s['t']
    n = len(ts)
    for i in range(1, n - 1):
        if ts[i] >= CUT:
            break
        if not (s['age'][i] >= 60):
            continue
        types = []
        for mult in (3, 5):
            if surge_b(s, i, mult) and not surge_b(s, i - 1, mult):
                types.append('SURGE_B%d' % mult)
            if surge_v(s, i, mult) and not surge_v(s, i - 1, mult):
                types.append('SURGE_V%d' % mult)
        if not types:
            continue
        P = H.Past(s, i)
        rows.append({'pair': pair, 't': ts[i], 'types': types, 'fee': H.fee_bps(s, i), 'liq': s['liq'][i],
                     'pc5': s['pc5'][i], 'rug': H.interim_rug_risk(P), 'src': int(s['src'][i]),
                     'f50': {h: fwd(s, i, h, 50.0) for h in HZ}})
print('train surge events', len(rows), 'secs', round(time.time() - t0, 1))


def line(rs, label):
    out = []
    for h in HZ:
        xs = sorted(r['f50'][h] for r in rs if r['f50'][h] is not None)
        if xs:
            out.append('%ds:n%d m%+.2f med%+.2f w%d%%' % (h, len(xs), sum(xs) / len(xs), xs[len(xs) // 2], 100 * sum(1 for x in xs if x > 0) / len(xs)))
    print('  %-44s pairs %3d | %s' % (label, len({r['pair'] for r in rs}), ' '.join(out)))


for ty in ('SURGE_B3', 'SURGE_B5', 'SURGE_V3', 'SURGE_V5'):
    print('==', ty, '(rug-screened, TRAIN, NET50)')
    for fb in ('fee<=50', 'fee55-95', 'fee100-125'):
        for lb in ('liq>=250k', 'liq50-250k', 'liq<50k'):
            for dirn in ('up', 'down'):
                rs = [r for r in rows if ty in r['types'] and not r['rug'] and fee_b(r['fee']) == fb and liq_b(r['liq']) == lb
                      and ((r['pc5'] > 0) == (dirn == 'up'))]
                if len(rs) >= 15:
                    line(rs, '%s %s pc5 %s' % (fb, lb, dirn))
    rs = [r for r in rows if ty in r['types'] and not r['rug'] and (r['src'] & 7)]
    if len(rs) >= 10:
        line(rs, 'with attention list flag (any bucket)')
print('done', round(time.time() - t0, 1))
