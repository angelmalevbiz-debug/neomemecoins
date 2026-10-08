"""Audit step 13: feed gaps (> 10 min absent, pair returns). Is the return price selected on the future?
Compare the price change across each gap with a matched control: other pairs in the same liquidity bucket that were
observed continuously (a point within 60 s of both the gap start and the gap end), over the same clock interval.
Also check the tape where it covers the gap."""
import bisect, collections, math, random, sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
DEEP = (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r''
sys.path.insert(0, DEEP); sys.path.insert(0, DEEP + '/audit')
import harness as H
from tapelib import load_tape

series, meta = H.load()
ps = [p for p, s in series.items() if s['dex'] == 'pumpswap' and s['quote_sol'] == 1]
lb = lambda liq: 'liq>=250k' if liq >= 250_000 else ('liq50-250k' if liq >= 50_000 else 'liq<50k')
gaps = []
for p in ps:
    s = series[p]
    ts = s['t']
    for m in range(1, len(ts)):
        if ts[m] - ts[m - 1] > 600_000 and s['price'][m - 1] > 0 and s['price'][m] > 0:
            gaps.append((p, m - 1, m))
print('gap events (>10 min, pair returns):', len(gaps))
by_bucket = collections.defaultdict(list)
for p in ps:
    by_bucket[lb(series[p]['liq'][len(series[p]['t']) // 2])].append(p)
rnd = random.Random(3)


def price_at(s, t, tol=60_000):
    ts = s['t']
    k = bisect.bisect_right(ts, t) - 1
    if k < 0 or t - ts[k] > tol:
        return None, None
    return s['price'][k], k


res = collections.defaultdict(lambda: {'gap': [], 'ctl': []})
tape = load_tape(set(ps), min_events=50)
tape_cmp = []
for p, a, b in gaps:
    s = series[p]
    t0, t1 = s['t'][a], s['t'][b]
    r = math.log(s['price'][b] / s['price'][a])
    bucket = lb(s['liq'][a])
    res[bucket]['gap'].append(r)
    res['ALL']['gap'].append(r)
    # matched controls: up to 20 random continuously observed pairs from the same bucket
    cands = by_bucket.get(bucket, [])
    got = 0
    for _ in range(200):
        if got >= 20 or not cands:
            break
        q = rnd.choice(cands)
        if q == p:
            continue
        sq = series[q]
        pa, ka = price_at(sq, t0)
        pb, kb = price_at(sq, t1)
        if pa and pb and pa > 0 and pb > 0 and abs(math.log(sq['liq'][ka] / max(1.0, s['liq'][a]))) < 2.5:
            ts_q = sq['t']
            if max((ts_q[m + 1] - ts_q[m] for m in range(ka, kb)), default=0) <= 600_000:
                c = math.log(pb / pa)
                res[bucket]['ctl'].append(c); res['ALL']['ctl'].append(c)
                got += 1
    if p in tape:
        tts, tpx, tb, tq = tape[p]
        i0 = bisect.bisect_right(tts, t0) - 1
        i10 = bisect.bisect_right(tts, t0 + 600_000) - 1
        if i0 >= 0 and i10 > i0 and t0 - tts[i0] < 60_000 and t0 + 600_000 - tts[i10] < 120_000:
            tape_cmp.append((math.log(tpx[i10] / tpx[i0]), r))


def st(xs):
    xs = sorted(xs)
    n = len(xs)
    if not n:
        return 'n 0'
    m = sum(xs) / n
    return 'n %5d mean %+6.1f%% median %+6.1f%% p10 %+6.1f%% p90 %+6.1f%% share<-50%% %.3f' % (
        n, 100 * (math.exp(m) - 1), 100 * (math.exp(xs[n // 2]) - 1), 100 * (math.exp(xs[int(.1 * n)]) - 1), 100 * (math.exp(xs[int(.9 * n)]) - 1),
        sum(1 for x in xs if x < math.log(.5)) / n)


for bkt in ('ALL', 'liq>=250k', 'liq50-250k', 'liq<50k'):
    print(bkt)
    print('  across gap (return price / last pre-gap price):', st(res[bkt]['gap']))
    print('  matched continuous controls, same interval  :', st(res[bkt]['ctl']))
dur = sorted((series[p]['t'][b] - series[p]['t'][a]) / 60000 for p, a, b in gaps)
print('gap duration min: p10 %.0f p50 %.0f p90 %.0f' % (dur[int(.1 * len(dur))], dur[len(dur) // 2], dur[int(.9 * len(dur))]))
print('tape-covered gaps (tape price 10 min into the gap vs gap start; DexScreener return vs start):', len(tape_cmp))
if tape_cmp:
    print('  tape +10min:', st([x[0] for x in tape_cmp]))
    print('  dex return :', st([x[1] for x in tape_cmp]))
