"""Audit step 3: DexScreener price vs on-chain tape - lag, level error, and fill bias by context (read-only)."""
import bisect, math, sys, time, collections
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.path.insert(0, (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r'')
sys.path.insert(0, (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r'/audit')
import harness as H
from tapelib import load_tape, TapePrice

t0 = time.time()
series, meta = H.load()
ps = {p for p, s in series.items() if s['dex'] == 'pumpswap' and s['quote_sol'] == 1}
tape = load_tape(ps, min_events=200)
print('tape pairs used', len(tape), 'events', sum(len(v[0]) for v in tape.values()), 'secs', round(time.time() - t0, 1))
q = lambda xs, p: xs[min(len(xs) - 1, int(p * len(xs)))] if xs else None


def med(xs):
    xs = sorted(xs)
    return xs[len(xs) // 2] if xs else None


# 0) fee convention: buys vs sells within the same block second (log ratio ~ 2*fee + impact if buy price includes fee)
rat = []
fee_seen = []
for p, (ts, px, buy, qa) in tape.items():
    s = series[p]
    for a in range(1, len(ts)):
        if ts[a] == ts[a - 1] and buy[a] != buy[a - 1]:
            b_, s_ = (px[a], px[a - 1]) if buy[a] else (px[a - 1], px[a])
            k = max(0, bisect.bisect_right(s['t'], ts[a]) - 1)
            rat.append(math.log(b_ / s_) * 1e4)
            fee_seen.append(H.fee_bps(s, k))
print('same-second buy/sell log ratio bps: n %d median %.1f p25 %.1f p75 %.1f ; median modeled fee bps %.1f' % (
    len(rat), med(rat), q(sorted(rat), .25), q(sorted(rat), .75), med(fee_seen)))

# 1) lag: error of DexScreener price at fresh points vs tape mid at t - L
LAGS = [0, 5, 10, 15, 20, 25, 30, 40, 50, 60, 90, 120]
err = {L: [] for L in LAGS}
err_raw = {L: [] for L in LAGS}
fresh_n = 0
for p, tp in tape.items():
    s = series[p]
    T = TapePrice(s, tp)
    pr, ts = s['pnative'], s['t']
    for k in range(1, len(ts)):
        if not (pr[k] > 0) or pr[k] == pr[k - 1]:
            continue
        fresh_n += 1
        for L in LAGS:
            m = T.mid(ts[k] - L * 1000, max_age_ms=5_000)
            if m:
                err[L].append(abs(math.log(pr[k] / m)) * 1e4)
            r = T.mid(ts[k] - L * 1000, max_age_ms=5_000, adjust=False)
            if r:
                err_raw[L].append(abs(math.log(pr[k] / r)) * 1e4)
print('fresh DexScreener points on tape pairs', fresh_n)
print('lag L s | n | median |err| bps fee-adjusted mid | p75 | median |err| raw last trade')
for L in LAGS:
    e = sorted(err[L]); r = sorted(err_raw[L])
    print('  %3d | %6d | %7.1f | %7.1f | %7.1f' % (L, len(e), med(e) or -1, q(e, .75) or -1, med(r) or -1))

# 2) fill bias: harness fills at DexScreener price of point j=i+1; reality ~ tape mid at t_i + 2 s.
#    bias_bps = log(real / harness_fill). For a BUY, >0 means the harness is optimistic. For a SELL, <0 means optimistic.
groups = collections.defaultdict(list)
groups_fresh = collections.defaultdict(list)
for p, tp in tape.items():
    s = series[p]
    T = TapePrice(s, tp)
    pr, ts = s['pnative'], s['t']
    last_fresh_px = None
    prev_fresh_px = None
    for i in range(1, len(ts) - 1):
        if pr[i] != pr[i - 1]:
            prev_fresh_px, last_fresh_px = last_fresh_px, pr[i - 1]
        j = i + 1
        if ts[j] - ts[i] > 60_000 or not (pr[j] > 0 and pr[i] > 0):
            continue
        real = T.mid(ts[i] + 2000, max_age_ms=5_000)
        if real is None:
            continue
        bias = math.log(real / pr[j]) * 1e4
        # context: last DexScreener move seen at i (current price vs the price before the last change)
        if last_fresh_px and last_fresh_px > 0:
            mv = math.log(pr[i] / last_fresh_px) * 100
        else:
            mv = 0.0
        b = 'dn>2%' if mv < -2 else 'dn0.5-2%' if mv < -0.5 else 'flat' if mv <= 0.5 else 'up0.5-2%' if mv <= 2 else 'up>2%'
        groups[b].append(bias)
        groups['ALL'].append(bias)
        if pr[i] != pr[i - 1]:
            groups_fresh[b].append(bias)
            groups_fresh['ALL'].append(bias)
print('fill bias bps = log(tape mid at t_i+2s / DexScreener fill price at j); context = last DexScreener move at i')
for name, g in (('all decision points', groups), ('decision at a FRESH point (price just changed)', groups_fresh)):
    print(' ', name)
    for b in ('dn>2%', 'dn0.5-2%', 'flat', 'up0.5-2%', 'up>2%', 'ALL'):
        xs = sorted(g.get(b, []))
        if not xs:
            continue
        print('    %-9s n %7d mean %7.1f median %7.1f p10 %7.1f p90 %7.1f' % (b, len(xs), sum(xs) / len(xs), med(xs), q(xs, .1), q(xs, .9)))

# 3) next-point staleness: share of i->j where DexScreener price is unchanged, and the true tape move over that gap
same = moved = 0
truemove = []
for p, tp in tape.items():
    s = series[p]
    T = TapePrice(s, tp)
    pr, ts = s['pnative'], s['t']
    for i in range(len(ts) - 1):
        if ts[i + 1] - ts[i] > 60_000:
            continue
        if pr[i + 1] == pr[i]:
            same += 1
            a, b = T.mid(ts[i], 5_000), T.mid(ts[i + 1], 5_000)
            if a and b:
                truemove.append(abs(math.log(b / a)) * 1e4)
        else:
            moved += 1
truemove.sort()
print('tape pairs: next point same DexScreener price share %.3f; when unchanged, |true tape move| over the gap bps: median %.1f p90 %.1f' % (
    same / max(1, same + moved), med(truemove) or -1, q(truemove, .9) or -1))
print('secs', round(time.time() - t0, 1))
