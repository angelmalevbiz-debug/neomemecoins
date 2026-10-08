import json, time, math
import harness as H

series, meta = H.load()
cut = H.split_t(meta)
print('span hours', round((meta['t1'] - meta['t0']) / 3.6e6, 2), 'split at hour', round((cut - meta['t0']) / 3.6e6, 2))


def cost_first(P):
    liq = P('liq')
    if not (liq >= 250_000):
        return False
    if P.fee_bps() > 50:
        return False
    size = min(200.0, liq * 0.001)
    rt = P.rt_cost_pct(size)
    return rt is not None and rt <= 1.2


def rnd(prob):
    return lambda P: H.hashed_coin(P.static('pair'), P.t, prob)


def show(name, trades):
    ev = H.evaluate(trades)
    keep = ('n', 'pairs', 'win_rate', 'mean_pct', 'median_pct', 'sum_usd', 'pf', 'ci95_mean_usd', 'trades_per_hour', 'avg_hold_min', 'top_pair_share', 'exits')
    print('==', name)
    for part in ('train', 'holdout', 'holdout_model'):
        print('  ', part, {k: ev[part].get(k) for k in keep})


t0 = time.time()
tr = H.simulate(lambda P: rnd(0.002)(P), stop=-5, tp=10, hold_min=60, tag='random_all')
print('sim secs', round(time.time() - t0, 1))
show('RANDOM entries, all PumpSwap, -5/+10/60', tr)
t0 = time.time()
tr = H.simulate(lambda P: cost_first(P) and rnd(0.01)(P), stop=-5, tp=10, hold_min=60, size_fn=lambda P: min(200.0, P('liq') * 0.001), tag='random_cf')
print('sim secs', round(time.time() - t0, 1))
show('RANDOM entries inside COST_FIRST universe, -5/+10/60', tr)

# base rates: forward net returns by fee bucket and horizon, sampled every ~60 s per pair
buckets = {}
for pair, s in series.items():
    if s['dex'] != 'pumpswap' or s['quote_sol'] != 1:
        continue
    ts = s['t']
    last = -1e18
    for i in range(len(ts) - 1):
        if ts[i] - last < 60_000:
            continue
        last = ts[i]
        fee = H.fee_bps(s, i)
        liq = s['liq'][i]
        fb = 'fee<=50' if fee <= 50 else ('fee55-95' if fee <= 95 else 'fee100-125')
        lb = 'liq>=250k' if liq >= 250_000 else ('liq50-250k' if liq >= 50_000 else 'liq<50k')
        for hz in (300, 900, 1800, 3600):
            v = H.forward_net(s, i, hz)
            if v is None:
                continue
            b = buckets.setdefault((fb, lb, hz), [])
            b.append(v)
print('== base rate: forward net % (model costs, $200), sampled 1/min/pair')
for key in sorted(buckets):
    xs = sorted(buckets[key])
    if len(xs) < 50:
        continue
    n = len(xs)
    mean = sum(xs) / n
    print('  ', key, 'n', n, 'mean', round(mean, 2), 'median', round(xs[n // 2], 2), 'p10', round(xs[int(n * .1)], 2), 'p90', round(xs[int(n * .9)], 2), 'win%', round(100 * sum(1 for x in xs if x > 0) / n, 1))
