"""F6 exploration 1: how many tradable pools are alive at each rebalance tick, by universe (no returns looked at)."""
import sys, time, bisect, math
sys.path.insert(0, (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r'')
import harness as H

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
t0 = time.time()
series, meta = H.load()
print('load secs', round(time.time() - t0, 1), meta)
ps = [s for s in series.values() if s['dex'] == 'pumpswap' and s['quote_sol'] == 1]
print('pumpswap SOL pairs', len(ps), 'of', len(series))
dexes = {}
for s in series.values():
    dexes[s['dex']] = dexes.get(s['dex'], 0) + 1
print('dexes', dexes)

# cadence: median gap between points per pair
gaps = []
for s in ps:
    ts = s['t']
    if len(ts) > 20:
        g = sorted(ts[k + 1] - ts[k] for k in range(len(ts) - 1))
        gaps.append(g[len(g) // 2] / 1000)
gaps.sort()
print('median per-pair gap s: p10 %.1f p50 %.1f p90 %.1f' % (gaps[len(gaps) // 10], gaps[len(gaps) // 2], gaps[int(len(gaps) * .9)]))

R = 300_000
ticks = list(range(int(meta['t0']) + R, int(meta['t1']) - R, R))
print('ticks', len(ticks))
cnt = {k: [] for k in ('alive', 'liq>=50k', 'liq>=50k_screened', 'cf', 'cf_screened', 'mid', 'mid_screened', 'hi_big')}
for T in ticks:
    c = {k: 0 for k in cnt}
    for s in ps:
        ts = s['t']
        if ts[0] > T or ts[-1] < T - 60_000:
            continue
        i = bisect.bisect_right(ts, T) - 1
        if i < 0 or T - ts[i] > 60_000:
            continue
        c['alive'] += 1
        liq = s['liq'][i]
        P = H.Past(s, i)
        fee = H.fee_bps(s, i)
        rug = H.interim_rug_risk(P)
        if liq >= 50_000:
            c['liq>=50k'] += 1
            if not rug:
                c['liq>=50k_screened'] += 1
        if liq >= 250_000 and fee <= 50:
            rt = H.roundtrip_cost_pct(s, i, 200)
            if rt is not None and rt <= 1.2:
                c['cf'] += 1
                if not rug:
                    c['cf_screened'] += 1
        if 50_000 <= liq and fee > 50:
            c['mid'] += 1
            if not rug:
                c['mid_screened'] += 1
        if liq >= 250_000 and fee >= 100:
            c['hi_big'] += 1
    for k in cnt:
        cnt[k].append(c[k])
for k, v in cnt.items():
    sv = sorted(v)
    print('%-20s min %4d p10 %4d med %4d p90 %4d max %4d' % (k, sv[0], sv[len(sv) // 10], sv[len(sv) // 2], sv[int(len(sv) * .9)], sv[-1]))
# by time
for q in range(0, len(ticks), 24):
    print('hour %5.1f' % ((ticks[q] - meta['t0']) / 3.6e6), {k: cnt[k][q] for k in cnt})
print('secs', round(time.time() - t0, 1))
