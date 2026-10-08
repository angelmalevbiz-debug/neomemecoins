"""Audit step 9: what happens to a pair after it vanishes from the scan feed (tape), feed gaps inside positions,
forward_net survivorship impact on base rates, and the rug-screen components for holdout rugs."""
import bisect, collections, math, sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.path.insert(0, (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r'')
sys.path.insert(0, (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r'/audit')
import harness as H
from tapelib import load_tape

series, meta = H.load()
ps = {p for p, s in series.items() if s['dex'] == 'pumpswap' and s['quote_sol'] == 1}
tape = load_tape(ps, min_events=1)
q = lambda xs, p: xs[min(len(xs) - 1, int(p * len(xs)))] if xs else None

# 1) vanished pairs: last DexScreener point > 10 min before dataset end
van = [p for p in ps if meta['t1'] - series[p]['t'][-1] > 600_000]
print('pumpswap-SOL pairs', len(ps), 'vanished (>10 min before end)', len(van))
res = collections.defaultdict(list)
cnt_tape_after = 0
for p in van:
    s = series[p]
    if p not in tape:
        continue
    ts, px, buy, qa = tape[p]
    tl = s['t'][-1]
    a = bisect.bisect_right(ts, tl)
    if a >= len(ts):
        res['no_trades_after'].append(0)
        continue
    cnt_tape_after += 1
    base = s['pnative'][-1]
    for mins in (10, 30, 60):
        b = bisect.bisect_right(ts, tl + mins * 60_000) - 1
        if b >= a and ts[b] >= tl + (mins - 10) * 60_000:
            res[mins].append(100 * (px[b] / base - 1))
    res['trades_after_count'].append(len(ts) - a)
print('vanished pairs with any tape: %d; with tape trades after the last DexScreener point: %d; without: %d' % (
    sum(1 for p in van if p in tape), cnt_tape_after, len(res['no_trades_after'])))
for mins in (10, 30, 60):
    xs = sorted(res[mins])
    if xs:
        print('  price change last DexScreener -> tape at +%d min: n %d mean %.1f%% median %.1f%% p10 %.1f%% p90 %.1f%%' % (
            mins, len(xs), sum(xs) / len(xs), q(xs, .5), q(xs, .1), q(xs, .9)))
# last-observed liquidity of vanished pairs (were they drained?)
liq_last = sorted(series[p]['liq'][-1] for p in van if series[p]['liq'][-1] == series[p]['liq'][-1])
print('  vanished pairs last liquidity $: p10 %.0f p50 %.0f p90 %.0f' % (q(liq_last, .1), q(liq_last, .5), q(liq_last, .9)))

# 2) forward_net survivorship: base rates by bucket with vanished samples dropped (v1) vs included at last price - 10%
buck = collections.defaultdict(lambda: [[], []])
for p in ps:
    s = series[p]
    ts = s['t']
    last = -1e18
    for i in range(len(ts) - 1):
        if ts[i] - last < 60_000:
            continue
        last = ts[i]
        fee = H.fee_bps(s, i); liq = s['liq'][i]
        fb = 'fee<=50' if fee <= 50 else ('fee55-95' if fee <= 95 else 'fee100-125')
        lb = 'liq>=250k' if liq >= 250_000 else ('liq50-250k' if liq >= 50_000 else 'liq<50k')
        v = H.forward_net(s, i, 3600)
        if v is not None:
            buck[(fb, lb)][0].append(v); buck[(fb, lb)][1].append(v)
            continue
        if ts[i] + 3600_000 <= meta['t1'] and ts[-1] < ts[i] + 3600_000 and ts[i + 1] - ts[i] <= 60_000:
            f = H.entry_fill(s, i + 1, 200.0)
            if f is None:
                continue
            e = len(ts) - 1
            val = H.exit_value(s, e, f[0], 0.0, s['price'][e] * (1 - H.VANISH_HAIRCUT_PCT / 100))
            if val is not None:
                buck[(fb, lb)][1].append(100 * (val - 200.0 - f[1]) / 200.0)
print('forward 60 min net % : survivors only (v1 forward_net) vs including vanished at last price -10%')
for k in sorted(buck):
    a, b = buck[k]
    if len(b) < 50:
        continue
    print('  %-12s %-11s n %6d -> %6d  mean %7.2f -> %7.2f' % (k[0], k[1], len(a), len(b), sum(a) / len(a), sum(b) / len(b)))

# 3) feed gaps inside positions (random entries): trades whose holding period contains a gap > 5 min
from smoke_common import rnd
tr = H.simulate(lambda P: rnd(0.002)(P), stop=-5, tp=10, hold_min=60)
g_tr, ng_tr = [], []
for x in tr:
    s = series[x['pair']]
    ts = s['t']
    a = bisect.bisect_left(ts, x['entry_t']); b = bisect.bisect_left(ts, x['exit_t'])
    mg = max((ts[k + 1] - ts[k] for k in range(a, b)), default=0)
    (g_tr if mg > 300_000 else ng_tr).append(x['net50'])
print('random trades with an in-position feed gap > 5 min: %d of %d, mean net50 %.2f vs %.2f others' % (
    len(g_tr), len(tr), sum(g_tr) / max(1, len(g_tr)), sum(ng_tr) / max(1, len(ng_tr))))

# 4) rug-screen components on the 7 holdout rugs at their first cost-first point
for pref in ('8TSAcyiP', 'AzfHgMbz', 'BTSpnpim', 'Bmc7xtro', 'DpenfQSG', 'HYZCigd7', 'VFy2QcKA', '3oKvxwy5', 'EpakUVUm', 'FeCGDeqW'):
    p = next(pp for pp in series if pp.startswith(pref))
    s = series[p]
    i = next(i for i in range(len(s['t'])) if s['liq'][i] >= 250_000 and H.fee_bps(s, i) <= 50)
    P = H.Past(s, i)
    mc, liq, age = P('mcap'), P('liq'), P('age')
    print('  %s age_d %.2f mcap_m %.1f liq/mcap %.2f%% ticker_reuse_before %d first_cf_hour %.1f' % (
        pref, age / 1440, mc / 1e6, 100 * liq / mc, H.other_pairs_same_ticker_before(P), (s['t'][i] - meta['t0']) / 3.6e6))
