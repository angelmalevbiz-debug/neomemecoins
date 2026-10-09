"""Census of the guarded universe for high-frequency books. PAPER research only."""
import random, time, json, os, sys
from common import H, fin, guard_ok_arrays, HERE
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
T0 = time.time()
series, meta = H.load()
print('loaded', meta, round(time.time() - T0, 1))
cut = H.split_t(meta, 0.6)
print('cut', cut, 'train h', (cut - meta['t0']) / 3.6e6, 'holdout h', (meta['t1'] - cut) / 3.6e6)

# 1) verify guard array form on random points
rnd = random.Random(1)
pairs = [p for p, s in series.items() if s['dex'] == 'pumpswap' and s['quote_sol'] == 1]
print('pumpswap SOL pairs', len(pairs))
mism = 0
for _ in range(20000):
    p = pairs[rnd.randrange(len(pairs))]
    s = series[p]
    i = rnd.randrange(len(s['t']))
    P = H.Past(s, i)
    a = H.rug_guard_v1(P)
    b = not guard_ok_arrays(s['liq'][i], s['mcap'][i], s['age'][i])
    mism += (a != b)
print('guard mismatches', mism)

# 2) guarded universe census
MIN = 60_000
g0 = int(meta['t0'] // MIN)
M = int(meta['t1'] // MIN) - g0 + 1
act = {k: [set() for _ in range(M)] for k in ('all', 'g', 'g50', 'g50_f50', 'g250_f50', 'g50_f95')}
npts = {'all': 0, 'g': 0}
same_price = 0
chg_b5_noprice = 0
chg_price = 0
gaps = []
rt_by_fee = {}
for p in pairs:
    s = series[p]
    ts, px, lq, mc, ag, b5, s5, v5 = (s[c] for c in ('t', 'price', 'liq', 'mcap', 'age', 'b5', 's5', 'v5'))
    n = len(ts)
    last_change_t = None
    for i in range(n):
        m = int(ts[i] // MIN) - g0
        act['all'][m].add(p)
        npts['all'] += 1
        ok = guard_ok_arrays(lq[i], mc[i], ag[i])
        if i > 0:
            if px[i] == px[i - 1]:
                same_price += 1
                if b5[i] != b5[i - 1] or s5[i] != s5[i - 1]:
                    chg_b5_noprice += 1
            else:
                chg_price += 1
                if ok and last_change_t is not None and ts[i] - ts[i - 1] < 60_000:
                    gaps.append(ts[i] - last_change_t)
                last_change_t = ts[i]
        if not ok:
            continue
        npts['g'] += 1
        act['g'][m].add(p)
        fee = H.fee_bps(s, i)
        if lq[i] >= 50_000:
            act['g50'][m].add(p)
            if fee <= 50:
                act['g50_f50'][m].add(p)
            if fee <= 95:
                act['g50_f95'][m].add(p)
            if lq[i] >= 250_000 and fee <= 50:
                act['g250_f50'][m].add(p)
            if i % 50 == 0:
                rt = H.roundtrip_cost_pct(s, i, 200.0)
                cx = H.calib_extra_bps_per_leg(fee, lq[i], 200.0)
                if rt is not None:
                    key = 'fee<=50' if fee <= 50 else ('fee55-95' if fee <= 95 else 'fee100+')
                    rt_by_fee.setdefault(key, []).append((rt, rt + 2 * (50 + cx) / 100))
print('points', npts, 'same-price share', round(same_price / max(1, same_price + chg_price), 3),
      'b5/s5 change without price change share of same-price', round(chg_b5_noprice / max(1, same_price), 4))
gaps.sort()
if gaps:
    q = lambda f: gaps[int(f * (len(gaps) - 1))] / 1000
    print('guarded price-change interval s: p10 %.1f p50 %.1f p90 %.1f n %d' % (q(.1), q(.5), q(.9), len(gaps)))
for k, v in act.items():
    cnt = sorted(len(x) for x in v)
    tr = [len(x) for j, x in enumerate(v) if (g0 + j) * MIN < cut]
    ho = [len(x) for j, x in enumerate(v) if (g0 + j) * MIN >= cut]
    uniq = set().union(*v)
    print('active per minute %-10s median %3d p10 %3d p90 %3d | train mean %.1f holdout mean %.1f | pairs %d' % (
        k, cnt[len(cnt) // 2], cnt[len(cnt) // 10], cnt[9 * len(cnt) // 10], sum(tr) / len(tr), sum(ho) / len(ho), len(uniq)))
for k, v in sorted(rt_by_fee.items()):
    v.sort()
    print('rt cost %% at $200 %-9s n %5d model median %.2f | net50 leg-stress+calib median %.2f p10 %.2f p90 %.2f' % (
        k, len(v), v[len(v) // 2][0], sorted(x[1] for x in v)[len(v) // 2], sorted(x[1] for x in v)[len(v) // 10],
        sorted(x[1] for x in v)[9 * len(v) // 10]))
print('secs', round(time.time() - T0, 1))
