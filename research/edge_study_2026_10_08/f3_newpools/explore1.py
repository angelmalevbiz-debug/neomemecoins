"""F3 exploration 1: census of young pools, survival curves, forward returns by age at observation.
Read-only over the shared dataset. Uses the full span (descriptive only, no parameter choice here
except via the train-only sections in later scripts)."""
import sys, time, math, bisect
sys.path.insert(0, (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r'')
import harness as H
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

t0 = time.time()
series, meta = H.load()
print('load secs', round(time.time() - t0, 1), meta)
cut = H.split_t(meta)
T1 = meta['t1']


def q(xs, p):
    xs = sorted(xs)
    if not xs:
        return None
    return round(xs[min(len(xs) - 1, int(p * len(xs)))], 2)


def desc(xs):
    if not xs:
        return 'n=0'
    n = len(xs)
    return 'n=%d mean=%.2f med=%.2f p10=%.2f p90=%.2f win=%.1f%%' % (
        n, sum(xs) / n, q(xs, .5), q(xs, .1), q(xs, .9), 100 * sum(1 for x in xs if x > 0) / n)


# ---- census
dex_counts = {}
rows = []
for pair, s in series.items():
    dex_counts[(s['dex'], s['quote_sol'])] = dex_counts.get((s['dex'], s['quote_sol']), 0) + 1
    if s['dex'] != 'pumpswap' or s['quote_sol'] != 1:
        continue
    n = len(s['t'])
    a0 = s['age'][0]
    rows.append((pair, a0, n))
print('dex/quote counts', dex_counts)
print('pumpswap SOL pairs', len(rows))
for lim in (5, 15, 30, 60, 120, 240, 1440):
    sub = [r for r in rows if r[1] < lim]
    print('first seen at age < %d min: %d pairs' % (lim, len(sub)))
ages = [r[1] for r in rows if r[1] == r[1]]
print('age at first obs quantiles (min):', [q(ages, p) for p in (.05, .1, .25, .5, .75, .9)])

# source flags at first obs for young pools
src_c = {}
for pair, a0, n in rows:
    if not (a0 < 60):
        continue
    s = series[pair]
    f = int(s['src'][0])
    src_c[f] = src_c.get(f, 0) + 1
print('src flags at first obs for age<60 pools:', sorted(src_c.items(), key=lambda x: -x[1]))
# src flags ever seen
src_any = {}
for pair, a0, n in rows:
    if not (a0 < 60):
        continue
    s = series[pair]
    f = 0
    for v in s['src']:
        f |= int(v)
    src_any[f] = src_any.get(f, 0) + 1
print('src flags ever (OR) for age<60 pools:', sorted(src_any.items(), key=lambda x: -x[1]))

# ---- survival of young pools from first observation
print('\n== survival from first observation, pools first seen at age < A minutes')
for A in (15, 30, 60, 120):
    st = {'n': 0}
    for pair, a0, n in rows:
        if not (a0 < A):
            continue
        s = series[pair]
        ts = s['t']
        if T1 - ts[0] < 2 * 3600_000:  # need 2 h of possible follow-up
            continue
        st['n'] += 1
        p0, l0 = s['price'][0], s['liq'][0]
        for hz in (900, 1800, 3600, 7200):
            k_end = bisect.bisect_right(ts, ts[0] + hz * 1000) - 1
            pmin = min(s['price'][:k_end + 1])
            lmin = min(s['liq'][:k_end + 1])
            last_t = ts[-1]
            vanished = (last_t < ts[0] + hz * 1000) and (T1 - last_t > 600_000)
            drained = lmin < 0.2 * l0 if l0 > 0 else False
            halved = pmin < 0.5 * p0 if p0 > 0 else False
            # value at horizon
            for key, cond in (('vanish', vanished), ('drain80', drained), ('half', halved),
                              ('dead_any', vanished or drained or halved)):
                st[(hz, key)] = st.get((hz, key), 0) + (1 if cond else 0)
    n = st['n']
    print(' A<%d: n=%d' % (A, n), ' '.join('%dm:%s' % (hz // 60, ','.join('%s=%.0f%%' % (k, 100 * st.get((hz, k), 0) / max(1, n)) for k in ('vanish', 'drain80', 'half', 'dead_any'))) for hz in (900, 1800, 3600, 7200)))

# ---- forward net returns from points sampled 1/min/pair by age bucket
print('\n== forward net %% ($100, model costs) sampled 1/min/pair by age bucket; train-period entries only')
buckets = {}
for pair, a0, n in rows:
    s = series[pair]
    ts = s['t']
    last = -1e18
    for i in range(len(ts) - 1):
        if ts[i] >= cut:
            break
        if ts[i] - last < 60_000:
            continue
        last = ts[i]
        age = s['age'][i]
        if not (age == age):
            continue
        ab = '<15' if age < 15 else '<30' if age < 30 else '<60' if age < 60 else '<120' if age < 120 else '<360' if age < 360 else '<1440' if age < 1440 else '>=1d'
        liq = s['liq'][i]
        lb = 'L<20k' if liq < 20_000 else 'L20-50k' if liq < 50_000 else 'L50-150k' if liq < 150_000 else 'L>=150k'
        for hz in (900, 3600):
            v = H.forward_net(s, i, hz, notional=100.0)
            if v is None:
                # pair vanished before horizon: count as -100 if vanished long before end? use last price -10%
                k = len(ts) - 1
                if T1 - ts[k] > 600_000 and ts[k] < ts[i] + hz * 1000 and i + 1 < len(ts):
                    f = H.entry_fill(s, i + 1, 100.0)
                    if f:
                        vv = H.exit_value(s, k, f[0], 0.0, s['price'][k] * 0.9)
                        if vv is not None:
                            v = 100 * (vv - 100 - f[1]) / 100
            if v is None:
                continue
            buckets.setdefault((ab, hz), []).append(v)
            buckets.setdefault((ab, lb, hz), []).append(v)
order = ['<15', '<30', '<60', '<120', '<360', '<1440', '>=1d']
for ab in order:
    for hz in (900, 3600):
        print(' age%s h=%dm %s' % (ab, hz // 60, desc(buckets.get((ab, hz), []))))
for ab in order[:5]:
    for lb in ('L<20k', 'L20-50k', 'L50-150k', 'L>=150k'):
        print(' age%s %s h=60m %s' % (ab, lb, desc(buckets.get((ab, lb, 3600), []))))
print('secs', round(time.time() - t0, 1))
