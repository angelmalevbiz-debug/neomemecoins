# exec()-ed by grid scripts; needs mid_train in scope.
def agg(rows):
    """rows: list of (net0, net50, usd50, pair, t)."""
    n = len(rows)
    if n == 0:
        return None
    m50 = sum(r[1] for r in rows) / n
    m0 = sum(r[0] for r in rows) / n
    a = [r[1] for r in rows if r[4] < mid_train]
    b = [r[1] for r in rows if r[4] >= mid_train]
    by_pair = {}
    for r in rows:
        by_pair.setdefault(r[3], []).append(r)
    contrib = {p: sum(x[2] for x in v) for p, v in by_pair.items()}
    top_pair = max(contrib, key=lambda p: contrib[p])
    rest = [r[1] for r in rows if r[3] != top_pair]
    cnt_top = max(len(v) for v in by_pair.values())
    usd = sorted(r[2] for r in rows)
    k = max(1, int(round(0.05 * n)))
    gains = sum(u for u in usd if u > 0)
    g = sum(r[1] for r in rows if r[1] > 0)
    l = -sum(r[1] for r in rows if r[1] < 0)
    sx = sorted(r[1] for r in rows)
    return {'n': n, 'pairs': len(by_pair), 'mean50': m50, 'mean0': m0, 'med50': sx[n // 2],
            'win': 100 * sum(1 for r in rows if r[1] > 0) / n, 'pf': g / l if l > 0 else 99.0,
            'meanA': sum(a) / len(a) if a else float('nan'), 'nA': len(a),
            'meanB': sum(b) / len(b) if b else float('nan'), 'nB': len(b),
            'mean_xtop': sum(rest) / len(rest) if rest else float('nan'),
            'top_share': cnt_top / n, 'sum50': sum(usd),
            'top5_share_gains': sum(usd[-k:]) / gains if gains > 0 else 0.0,
            'sum_wo_top5': sum(usd[:-k])}


def fmt(name, a):
    return ('%-44s n=%4d pr=%3d m50=%7.2f m0=%7.2f med=%6.2f win=%5.1f pf=%5.2f | A=%7.2f(%d) B=%7.2f(%d) xtop=%7.2f '
            'topsh=%.2f sum$=%8.0f top5%%gain=%.2f' % (name, a['n'], a['pairs'], a['mean50'], a['mean0'], a['med50'], a['win'],
                                                     a['pf'], a['meanA'], a['nA'], a['meanB'], a['nB'], a['mean_xtop'],
                                                     a['top_share'], a['sum50'], a['top5_share_gains']))
