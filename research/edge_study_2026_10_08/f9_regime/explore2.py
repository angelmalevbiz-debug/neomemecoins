"""F9 exploration 2: scan gaps (minutes with few points) and simulate() timing. Read-only."""
import sys, time
sys.path.insert(0, (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r'')
import harness as H

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
series, meta = H.load()
T0, T1 = meta['t0'], meta['t1']
nm = int((T1 - T0) // 60000) + 1
cnt = [0] * nm
for p, s in series.items():
    for t in s['t']:
        cnt[int((t - T0) // 60000)] += 1
low = [m for m in range(nm) if cnt[m] < 50]
print('minutes', nm, 'minutes with <50 points', len(low))
# contiguous gap runs
runs, start = [], None
for m in range(nm):
    if cnt[m] < 50:
        if start is None:
            start = m
    else:
        if start is not None:
            runs.append((start, m - start))
            start = None
if start is not None:
    runs.append((start, nm - start))
print('low runs (start_min, len) with len>=3:', [(a, l, round(a / 60, 2)) for a, l in runs if l >= 3])
sp = sorted(cnt)
print('points/min p10 p50 p90', sp[nm // 10], sp[nm // 2], sp[nm * 9 // 10])


def U(P):
    if H.interim_rug_risk(P):
        return False
    return P('liq') >= 50_000


t = time.time()
tr = H.simulate(lambda P: P('liq') >= 50_000 and H.hashed_coin(P.static('pair'), P.t, 0.004) and not H.interim_rug_risk(P), stop=-5, tp=10, hold_min=60)
print('sim s', round(time.time() - t, 1), 'n', len(tr))
ev = H.evaluate(tr)
keep = ('n', 'pairs', 'win_rate', 'mean_pct', 'median_pct', 'sum_usd', 'pf', 'ci95_mean_usd', 'trades_per_hour', 'top_pair_share', 'exits')
for part in ('train', 'holdout', 'train_model', 'holdout_model'):
    print(part, {k: ev[part].get(k) for k in keep})
