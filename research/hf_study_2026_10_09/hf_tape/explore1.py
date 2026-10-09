"""explore1: tape coverage vs the guarded universe, seat concurrency, decoder half-spread, ingestion lag.
PAPER research only. Output: explore1.out, half_spread.json"""
import bisect, json, math, os, sys, time
from collections import Counter, defaultdict

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
t00 = time.time()
import hfcommon as C
H = C.H
print('loaded', round(time.time() - t00, 1), 's; T0', C.T0, 'T1', C.T1, 'CUT', C.CUT, 'hours', round((C.T1 - C.T0) / 3.6e6, 2))


def q(xs, ps=(0.1, 0.25, 0.5, 0.75, 0.9)):
    xs = sorted(xs)
    return [round(xs[min(len(xs) - 1, int(p * len(xs)))], 4) for p in ps] if xs else []


ser = C.series
tp_pairs = list(C.TAPE.keys())
in_ser = [p for p in tp_pairs if p in ser]
pump = [p for p in in_ser if ser[p]['dex'] == 'pumpswap' and ser[p]['quote_sol'] == 1]
print('tape pools', len(tp_pairs), 'in series', len(in_ser), 'pumpswap SOL', len(pump))

# ---------------------------------------------------------------- half spread from adjacent opposite-side swaps
gaps = defaultdict(list)
for p in pump:
    s, d = ser[p], C.TAPE[p]
    et, sg = d['et'], d['sgn']
    for k in range(len(et) - 1):
        if sg[k] == sg[k + 1] or et[k + 1] - et[k] > 2000:
            continue
        if et[k] < C.T0 or et[k] > C.CUT:      # TRAIN window only
            continue
        a, b = C.post_price_sol(s, d, k), C.post_price_sol(s, d, k + 1)
        if not a or not b:
            continue
        pb, ps_ = (a, b) if sg[k] > 0 else (b, a)
        i = max(0, C.sidx(s, et[k]))
        fee = H.fee_bps(s, i)
        gaps[C.fee_bucket(fee)].append(math.log(pb / ps_))
        gaps['fee%d' % int(fee)].append(math.log(pb / ps_))
hs = {}
for kk in sorted(gaps):
    v = gaps[kk]
    print('adjacent buy/sell log gap', kk, 'n', len(v), 'q10/25/50/75/90', q(v))
    if kk in ('le50', '55_95', '100_125'):
        hs[kk] = max(0.0, sorted(v)[len(v) // 2] / 2)
print('half spread (median/2)', hs)
json.dump(hs, open(os.path.join(C.HERE, 'half_spread.json'), 'w'))

# ---------------------------------------------------------------- real-time ingestion lag
lags = []
for p in pump:
    d = C.TAPE[p]
    for et, av in zip(d['et'], d['av']):
        if C.T0 <= av <= C.T1 and av - et <= 120_000:
            lags.append((av - et) / 1000)
print('ingest lag (av-et<=120s, in window) n', len(lags), 'q10/25/50/75/90', q(lags), 'share<=5s',
      round(sum(1 for x in lags if x <= 5) / len(lags), 3))

# ---------------------------------------------------------------- seat concurrency per minute
m0 = int(C.T0 // 60000)
m1 = int(C.T1 // 60000)
seated = defaultdict(set)    # minute -> pools with a real-time event ingested in that minute
for p in tp_pairs:
    d = C.TAPE[p]
    for et, av in zip(d['et'], d['av']):
        if av - et <= 60_000 and C.T0 <= av < C.T1:
            seated[int(av // 60000)].add(p)
mins = list(range(m0, m1))
cnt_all, cnt_ps, cnt_guard, cnt_g50, cnt_cheap = [], [], [], [], []
fee_minutes = Counter()
pool_guard_minutes = Counter()
for m in mins:
    ps = seated.get(m, set())
    cnt_all.append(len(ps))
    n_ps = n_g = n_g50 = n_cheap = 0
    for p in ps:
        s = ser.get(p)
        if s is None or s['dex'] != 'pumpswap' or s['quote_sol'] != 1:
            continue
        i = C.sidx(s, m * 60000 + 59999)
        if i < 0 or (m * 60000 + 59999) - s['t'][i] > 120_000:
            continue
        n_ps += 1
        P = H.Past(s, i)
        if H.rug_guard_v1(P):
            continue
        n_g += 1
        fee = P.fee_bps()
        liq = P('liq')
        fee_minutes[(C.fee_bucket(fee), 'liq>=50k' if liq >= 50_000 else 'liq<50k')] += 1
        pool_guard_minutes[p] += 1
        if liq >= 50_000:
            n_g50 += 1
            if fee <= 95:
                n_cheap += 1
    cnt_ps.append(n_ps)
    cnt_guard.append(n_g)
    cnt_g50.append(n_g50)
    cnt_cheap.append(n_cheap)


def seg(vals, lo, hi):
    sub = [v for m, v in zip(mins, vals) if lo <= m * 60000 < hi]
    return round(sum(sub) / max(1, len(sub)), 3), Counter(min(v, 8) for v in sub)


for name, vals in (('seated(any)', cnt_all), ('seated pumpswapSOL fresh', cnt_ps), ('seated guarded', cnt_guard),
                   ('seated guarded liq>=50k', cnt_g50), ('seated guarded liq>=50k fee<=95', cnt_cheap)):
    a = seg(vals, C.T0, C.CUT)
    b = seg(vals, C.CUT, C.T1)
    print('%-34s train mean %.2f  holdout mean %.2f  train dist %s  holdout dist %s' % (
        name, a[0], b[0], dict(sorted(a[1].items())), dict(sorted(b[1].items()))))
print('guarded seated minutes by (fee bucket, liq):', dict(fee_minutes))
print('top guarded seated pools (minutes):')
for p, n in pool_guard_minutes.most_common(25):
    s = ser[p]
    print('  ', C.ab(p), s['sym'], n)
# hourly
print('hourly mean seated / guarded / guarded liq>=50k fee<=95:')
byh = defaultdict(list)
for m, a, g, c in zip(mins, cnt_all, cnt_guard, cnt_cheap):
    byh[m // 60].append((a, g, c))
for h in sorted(byh):
    v = byh[h]
    print('  ', time.strftime('%m-%d %H', time.gmtime(h * 3600)), 'seated %.2f guarded %.2f cheap %.2f' % (
        sum(x[0] for x in v) / len(v), sum(x[1] for x in v) / len(v), sum(x[2] for x in v) / len(v)))
print('done', round(time.time() - t00, 1), 's')
