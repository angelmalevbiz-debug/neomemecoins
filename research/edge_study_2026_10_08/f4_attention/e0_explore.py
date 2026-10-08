"""F4 exploration: how often do attention flags / boosts appear and change? (read-only)"""
import sys, math, time
sys.path.insert(0, (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r'')
import harness as H

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
t0 = time.time()
series, meta = H.load()
print('loaded', meta, round(time.time() - t0, 1))

dex_pairs, dex_points = {}, {}
for p, s in series.items():
    k = (s['dex'], s['quote_sol'])
    dex_pairs[k] = dex_pairs.get(k, 0) + 1
    dex_points[k] = dex_points.get(k, 0) + len(s['t'])
print('pairs by (dex, quote_sol):', sorted(dex_pairs.items(), key=lambda x: -x[1]))
print('points by (dex, quote_sol):', sorted(dex_points.items(), key=lambda x: -x[1]))

BITS = [('boosted', 1), ('boosted-latest', 2), ('latest', 4), ('gecko', 8), ('catalog', 16)]
for scope in ('pumpswap_sol', 'all'):
    pts = {b: 0 for b, _ in BITS}
    prs = {b: 0 for b, _ in BITS}
    onsets = {b: 0 for b, _ in BITS}
    first_on = {b: 0 for b, _ in BITS}
    boost_pairs = 0
    boost_pts = 0
    boost_inc = 0
    boost_first = 0
    boost_vals = {}
    tot = 0
    for p, s in series.items():
        if scope == 'pumpswap_sol' and not (s['dex'] == 'pumpswap' and s['quote_sol'] == 1):
            continue
        src = s['src']
        n = len(src)
        tot += n
        for b, m in BITS:
            c = 0
            had = False
            for i in range(n):
                on = int(src[i]) & m
                if on:
                    c += 1
                    if i > 0 and not (int(src[i - 1]) & m):
                        onsets[b] += 1
                        if not had:
                            first_on[b] += 1
                    had = True
            pts[b] += c
            if c:
                prs[b] += 1
        bo = s['boost']
        anyb = False
        for i in range(n):
            v = bo[i]
            if v > 0:
                boost_pts += 1
                anyb = True
                boost_vals[v] = boost_vals.get(v, 0) + 1
                if i > 0:
                    pv = bo[i - 1]
                    if pv > 0 and v > pv:
                        boost_inc += 1
                    elif not (pv > 0):
                        boost_first += 1
        if anyb:
            boost_pairs += 1
    print('==', scope, 'points', tot)
    for b, _ in BITS:
        print('  flag', b, 'points', pts[b], 'pairs', prs[b], 'onsets(off->on)', onsets[b], 'first-onsets after off point', first_on[b])
    print('  boost>0 points', boost_pts, 'pairs', boost_pairs, 'increases', boost_inc, '0->pos transitions', boost_first)
    print('  top boost values', sorted(boost_vals.items(), key=lambda x: -x[1])[:25])

# how flickery is membership? run lengths (points) of the boosted / latest flags for pumpswap sol
for b, m in BITS[:3]:
    runs = []
    gaps = []
    for p, s in series.items():
        if not (s['dex'] == 'pumpswap' and s['quote_sol'] == 1):
            continue
        src, ts = s['src'], s['t']
        run_start = None
        last_end = None
        for i in range(len(src)):
            on = int(src[i]) & m
            if on and run_start is None:
                run_start = i
                if last_end is not None:
                    gaps.append((ts[i] - ts[last_end]) / 1000)
            elif not on and run_start is not None:
                runs.append((ts[i - 1] - ts[run_start]) / 1000)
                run_start = None
                last_end = i - 1
        if run_start is not None:
            runs.append((ts[-1] - ts[run_start]) / 1000)
    runs.sort(); gaps.sort()
    q = lambda xs, f: round(xs[int(f * (len(xs) - 1))], 1) if xs else None
    print('flag', b, 'runs', len(runs), 'run seconds p10/50/90', q(runs, .1), q(runs, .5), q(runs, .9),
          'off-gaps', len(gaps), 'gap seconds p10/50/90', q(gaps, .1), q(gaps, .5), q(gaps, .9))

# first-point sources: how pairs enter the dataset
first_src = {}
for p, s in series.items():
    if not (s['dex'] == 'pumpswap' and s['quote_sol'] == 1):
        continue
    v = int(s['src'][0])
    names = '|'.join(b for b, m in BITS if v & m) or 'none'
    first_src[names] = first_src.get(names, 0) + 1
print('pumpswap first-point source combos', sorted(first_src.items(), key=lambda x: -x[1]))
