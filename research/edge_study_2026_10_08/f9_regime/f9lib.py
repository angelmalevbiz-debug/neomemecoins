"""F9 regime family: past-only market regime grid + simple base strategies (research only).

Regime grid: one row per wall-clock minute T (UTC ms, multiple of 60 000). Every value at T is
computed ONLY from dataset points with t <= T (each pair's latest point at or before T and its
own older points), so a signal at time t may read the row floor(t / 60 000) * 60 000 <= t.
"""
import bisect, math, sys
sys.path.insert(0, (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r'')
import harness as H

MIN = 60_000
ACTIVE_MS = 180_000          # a pair counts as active at T if it printed a point in the last 3 min
LAG_TOL_MS = 600_000         # the comparison point for a k-minute change must be at most 10 min staler


def _median(xs):
    if not xs:
        return float('nan')
    xs = sorted(xs)
    n = len(xs)
    return xs[n // 2] if n % 2 else 0.5 * (xs[n // 2 - 1] + xs[n // 2])


def build_grid(series=None, meta=None, min_liq=20_000.0):
    """Past-only market regime per minute. Returns dict with 'g0' and per-minute lists."""
    if series is None:
        series, meta = H.load()
    T0, T1 = meta['t0'], meta['t1']
    g0 = int(math.ceil(T0 / MIN) * MIN)
    M = int((T1 - g0) // MIN) + 1
    acc = [{'r15': [], 'r15n': [], 'r60': [], 'pc5': [], 'pc1h': [], 'bs': [], 'v5': [], 'sol': [], 'n': 0} for _ in range(M)]
    first_seen_young = [0] * M   # pairs first seen at minute m whose age at first sight < 60 min (all dexes)
    for pair, s in series.items():
        ts = s['t']
        n = len(ts)
        m_first = int(math.ceil((ts[0] - g0) / MIN))
        a0 = s['age'][0]
        if 0 <= m_first < M and a0 == a0 and a0 < 60:
            first_seen_young[m_first] += 1
        if s['dex'] != 'pumpswap' or s['quote_sol'] != 1:
            continue
        k = k15 = k60 = 0
        m_end = min(M - 1, int((ts[-1] + ACTIVE_MS - g0) // MIN))
        for m in range(max(0, m_first), m_end + 1):
            T = g0 + m * MIN
            while k + 1 < n and ts[k + 1] <= T:
                k += 1
            if ts[k] > T or T - ts[k] > ACTIVE_MS:
                continue
            liq = s['liq'][k]
            if not (liq >= min_liq):
                continue
            a = acc[m]
            a['n'] += 1
            p = s['price'][k]
            for key, lag, ptr in (('r15', 900_000, 'k15'), ('r60', 3_600_000, 'k60')):
                tt = T - lag
                kk = k15 if ptr == 'k15' else k60
                while kk + 1 < n and ts[kk + 1] <= tt:
                    kk += 1
                if ptr == 'k15':
                    k15 = kk
                else:
                    k60 = kk
                if ts[kk] <= tt and tt - ts[kk] <= LAG_TOL_MS and s['price'][kk] > 0 and p > 0:
                    a[key].append(p / s['price'][kk] - 1)
                    if key == 'r15':
                        pn, pn0 = s['pnative'][k], s['pnative'][kk]
                        if pn > 0 and pn0 > 0:
                            a['r15n'].append(pn / pn0 - 1)
            v = s['pc5'][k]
            if v == v:
                a['pc5'].append(v)
            v = s['pc1h'][k]
            if v == v:
                a['pc1h'].append(v)
            b, sl = s['b5'][k], s['s5'][k]
            if b == b and sl == sl and b + sl >= 5:
                a['bs'].append(b / (b + sl))
            v = s['v5'][k]
            if v == v and liq >= 50_000:
                a['v5'].append(v)
            su = H.sol_usd(s, k)
            if su > 0:
                a['sol'].append(su)
    g = {'g0': g0, 'M': M}
    cols = {c: [float('nan')] * M for c in ('n', 'br15', 'br60', 'med15', 'med15n', 'med60', 'pc5', 'pc1h', 'bs', 'v5', 'sol',
                                             'new60', 'sol60', 'v5z', 'med15_rank')}
    for m in range(M):
        a = acc[m]
        cols['n'][m] = a['n']
        if len(a['r15']) >= 8:
            cols['br15'][m] = sum(1 for x in a['r15'] if x > 0) / len(a['r15'])
            cols['med15'][m] = _median(a['r15']) * 100
        if len(a['r15n']) >= 8:
            cols['med15n'][m] = _median(a['r15n']) * 100
        if len(a['r60']) >= 8:
            cols['br60'][m] = sum(1 for x in a['r60'] if x > 0) / len(a['r60'])
            cols['med60'][m] = _median(a['r60']) * 100
        if len(a['pc5']) >= 8:
            cols['pc5'][m] = _median(a['pc5'])
        if len(a['pc1h']) >= 8:
            cols['pc1h'][m] = _median(a['pc1h'])
        if len(a['bs']) >= 8:
            cols['bs'][m] = _median(a['bs'])
        if len(a['v5']) >= 5:
            cols['v5'][m] = _median(a['v5'])
        if len(a['sol']) >= 5:
            cols['sol'][m] = _median(a['sol'])
        cols['new60'][m] = sum(first_seen_young[max(0, m - 59):m + 1])
    for m in range(M):
        if m >= 60 and cols['sol'][m] == cols['sol'][m] and cols['sol'][m - 60] == cols['sol'][m - 60]:
            cols['sol60'][m] = (cols['sol'][m] / cols['sol'][m - 60] - 1) * 100
        # volume level vs its own trailing 3 h (past-only): log ratio to trailing median
        if m >= 60 and cols['v5'][m] == cols['v5'][m] and cols['v5'][m] > 0:
            hist = [x for x in cols['v5'][max(0, m - 180):m] if x == x and x > 0]
            if len(hist) >= 30:
                cols['v5z'][m] = math.log(cols['v5'][m] / _median(hist))
        # adaptive market-dip measure: rank of med15 now among the previous 6 h of med15 values (past-only)
        v = cols['med15'][m]
        if v == v:
            hist = [x for x in cols['med15'][max(0, m - 360):m] if x == x]
            if len(hist) >= 60:
                cols['med15_rank'][m] = sum(1 for x in hist if x < v) / len(hist)
    g.update(cols)
    return g


def add_tide(g, series=None, universe=None, horizon_s=1800, window_min=60):
    """'Tide': mean model-net % of random-time 30-min holds in the universe that COMPLETED within
    the last `window_min` minutes before T (outcomes known at T: exit point time <= T)."""
    if series is None:
        series, _ = H.load()
    g0, M = g['g0'], g['M']
    done = [[] for _ in range(M)]   # by completion minute (ceil)
    for pair, s in series.items():
        if s['dex'] != 'pumpswap' or s['quote_sol'] != 1:
            continue
        ts = s['t']
        n = len(ts)
        last = -1e18
        for i in range(n - 1):
            if ts[i] - last < MIN:
                continue
            P = H.Past(s, i)
            if universe is not None and not universe(P):
                continue
            last = ts[i]
            v = H.forward_net(s, i, horizon_s)
            if v is None:
                continue
            kx = bisect.bisect_left(ts, ts[i] + horizon_s * 1000, i + 2)
            te = ts[kx]
            mc = int(math.ceil((te - g0) / MIN))
            if 0 <= mc < M:
                done[mc].append(v)
    tide = [float('nan')] * M
    tide_n = [0] * M
    for m in range(M):
        xs = []
        for mm in range(max(0, m - window_min + 1), m + 1):
            xs.extend(done[mm])
        tide_n[m] = len(xs)
        if len(xs) >= 10:
            tide[m] = sum(xs) / len(xs)
    g['tide'] = tide
    g['tide_n'] = tide_n
    return g


def at(g, col, t_ms):
    """Regime value for a decision at time t_ms: the row at floor(t / 1 min) (uses only data <= that minute)."""
    m = int((t_ms - g['g0']) // MIN)
    if m < 0 or m >= g['M']:
        return float('nan')
    return g[col][m]


# ---------------------------------------------------------------- universes and base signals
def U_liquid(P):
    """Rug-screened liquid PumpSwap universe: liq >= $50k and the interim rug screen passes."""
    if not (P('liq') >= 50_000):
        return False
    return not H.interim_rug_risk(P)


def sig_random(prob, salt='r'):
    return lambda P: U_liquid(P) and H.hashed_coin(P.static('pair'), P.t, prob, salt)


def sig_momentum(P):
    """Simple momentum (a priori): DexScreener 5m change >= +3%, more buys than sells in 5m,
    own price up >= 2% over the last 5 minutes."""
    if not U_liquid(P):
        return False
    pc5, b5, s5 = P('pc5'), P('b5'), P('s5')
    if not (pc5 >= 3 and b5 > s5):
        return False
    p, p5 = P('price'), P.ago('price', 300)
    return p > 0 and p5 > 0 and p / p5 - 1 >= 0.02


def sig_dip(P):
    """Simple dip (a priori): own price down >= 10% over 15 min, liquidity not pulled (>= 85% of
    15 min ago), 24h change > -50%."""
    if not U_liquid(P):
        return False
    p, p15 = P('price'), P.ago('price', 900)
    if not (p > 0 and p15 > 0 and p / p15 - 1 <= -0.10):
        return False
    l, l15 = P('liq'), P.ago('liq', 900)
    if not (l15 > 0 and l / l15 >= 0.85):
        return False
    pc24 = P('pc24')
    return pc24 == pc24 and pc24 > -50


# ---------------------------------------------------------------- reporting
KEEP = ('n', 'pairs', 'clusters', 'win_rate', 'mean_pct', 'median_pct', 'sum_usd', 'pf', 'ci95_mean_usd',
        'trades_per_hour', 'top_pair_share', 'exits')


def brief(sm):
    return {k: sm.get(k) for k in KEEP}


def report(name, trades, out=print):
    ev = H.evaluate(trades)
    out('== %s  total n=%d' % (name, len(trades)))
    for part in ('train', 'holdout', 'train_model', 'holdout_model'):
        out('   %-13s %s' % (part, brief(ev[part])))
    cut = H.split_t(H.load()[1])
    ho = [x for x in trades if x['entry_t'] >= cut]
    out('   portfolio(holdout, slots=3) %s' % H.portfolio(ho, 3))
    return ev
