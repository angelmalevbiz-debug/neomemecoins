"""Outliers and the observable-only (no vanish haircut) bound for young pools; train rows only."""
import sys, pickle, bisect
sys.path.insert(0, (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r'')
import harness as H
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
rows = pickle.load(open((__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r'/f3_newpools/samples.pkl', 'rb'))
series, meta = H.load()
TR = [r for r in rows if r['train'] and r['age'] < 240 and r.get('f3600') is not None]
TR.sort(key=lambda r: -r['f3600'])
print('top young train samples by f3600')
for r in TR[:15]:
    s = series[r['pair']]
    ts = s['t']
    i = bisect.bisect_left(ts, r['t'])
    print(r['pair'][:8], (s['sym'] or '')[:10], 'age %.1f' % r['age'], 'f3600 %.1f' % r['f3600'], 'van', r['van3600'], 'liq %.0f' % r['liq'],
          'price path', [round(s['price'][k] / s['price'][i], 3) for k in range(i, min(len(ts), i + 14))],
          'liq path', [round(s['liq'][k]) for k in range(i, min(len(ts), i + 6))], 'dt_s', [round((ts[k] - ts[i]) / 1000) for k in range(i, min(len(ts), i + 14))])

# observable-only upper bound: exit at the last observed point before the horizon, no haircut
print('\nobservable-only bound (exit at min(horizon, last observation), no vanish haircut), net50, $100')


def obs_only(s, i, hz):
    ts = s['t']
    if i + 1 >= len(ts) or ts[i + 1] - ts[i] > 60_000:
        return None, None
    f = H.entry_fill(s, i + 1, 100.0, H.STRESS_BPS)
    if f is None:
        return None, None
    k = bisect.bisect_left(ts, ts[i] + hz * 1000, i + 2)
    if k >= len(ts):
        k = len(ts) - 1
    v = H.exit_value(s, k, f[0], H.STRESS_BPS)
    return (None if v is None else 100 * (v - 100 - f[1]) / 100), (ts[k] - ts[i]) / 60000


for lo, hi in ((0, 3), (3, 15), (15, 60), (60, 240)):
    xs, held = [], []
    for r in TR:
        if not (lo <= r['age'] < hi):
            continue
        s = series[r['pair']]
        i = bisect.bisect_left(s['t'], r['t'])
        v, h = obs_only(s, i, 3600)
        if v is not None:
            xs.append(v)
            held.append(h)
    xs.sort()
    n = len(xs)
    print(' age [%d,%d) n=%d mean=%.2f med=%.2f p10=%.2f p90=%.2f win=%.1f%% mean_held_min=%.1f' % (
        lo, hi, n, sum(xs) / n, xs[n // 2], xs[int(n * .1)], xs[int(n * .9)], 100 * sum(1 for x in xs if x > 0) / n, sum(held) / n))
