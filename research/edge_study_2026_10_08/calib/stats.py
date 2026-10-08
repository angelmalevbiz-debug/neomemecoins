import math, random


def ok(x):
    return isinstance(x, (int, float)) and math.isfinite(x)


def pct(sorted_xs, q):
    if not sorted_xs:
        return float('nan')
    k = (len(sorted_xs) - 1) * q
    lo, hi = int(math.floor(k)), int(math.ceil(k))
    return sorted_xs[lo] + (sorted_xs[hi] - sorted_xs[lo]) * (k - lo)


def cluster_ci(vals_by_cluster, reps=2000, seed=11):
    groups = [g for g in vals_by_cluster.values() if g]
    if len(groups) < 3:
        return (float('nan'), float('nan'))
    rnd = random.Random(seed)
    means = []
    for _ in range(reps):
        tot = cnt = 0
        for _ in range(len(groups)):
            g = groups[rnd.randrange(len(groups))]
            tot += sum(g)
            cnt += len(g)
        means.append(tot / cnt)
    means.sort()
    return (means[int(reps * .025)], means[int(reps * .975)])


def desc(rows, f, cluster=lambda r: (r.get('pair8'), int((r.get('opened_at') or 0) // 3_600_000))):
    xs = [(f(r), r) for r in rows]
    xs = [(v, r) for v, r in xs if ok(v)]
    if not xs:
        return {'n': 0}
    v = sorted(x for x, _ in xs)
    cl = {}
    for x, r in xs:
        cl.setdefault(cluster(r), []).append(x)
    lo, hi = cluster_ci(cl)
    return {'n': len(v), 'clusters': len(cl), 'mean': sum(v) / len(v), 'median': pct(v, .5),
            'p10': pct(v, .1), 'p25': pct(v, .25), 'p75': pct(v, .75), 'p90': pct(v, .9),
            'min': v[0], 'max': v[-1], 'ci95': (lo, hi)}


def fmt(d, keys=('n', 'clusters', 'mean', 'median', 'p10', 'p90', 'ci95')):
    if not d or d.get('n', 0) == 0:
        return 'n=0'
    out = []
    for k in keys:
        v = d.get(k)
        if isinstance(v, tuple):
            out.append('%s=[%+.3f,%+.3f]' % (k, v[0], v[1]))
        elif isinstance(v, float):
            out.append('%s=%+.3f' % (k, v))
        else:
            out.append('%s=%s' % (k, v))
    return ' '.join(out)
