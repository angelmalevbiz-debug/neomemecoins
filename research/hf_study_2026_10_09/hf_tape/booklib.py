"""Multi-slot PAPER book simulator over precomputed decision points (points.pkl). PAPER research only.

Rules: decisions processed in time order; a position occupies one slot from its decision time to its exit fill
time; one open position per pool; per-pool re-entry cooldown after the exit; optional UTC-day realized loss cap
(no new entries for the rest of the UTC day once realized day P&L <= -cap). P&L is realized at the exit time."""
import hashlib, heapq, os, pickle, random
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
_D = None


def data():
    global _D
    if _D is None:
        _D = pickle.load(open(os.path.join(HERE, 'points.pkl'), 'rb'))
    return _D


def coin(pair, t, prob, salt):
    h = hashlib.blake2b(('%s|%s|%d' % (salt, pair, int(t))).encode(), digest_size=8).digest()
    return int.from_bytes(h, 'big') / 2 ** 64 < prob


def day(t):
    return int(t // 86_400_000)


def book(cands, model, hold, slots=4, cooldown_s=0, cap_usd=None, notional=100.0, key='net50'):
    """cands: decision points sorted by t. Returns list of trade dicts."""
    open_ = {}
    heap = []
    last_exit = {}
    dpnl = defaultdict(float)
    trades = []
    skipped = defaultdict(int)
    for p in cands:
        o = p[model][hold]
        if o is None:
            skipped['no_fill'] += 1
            continue
        t = p['t']
        while heap and heap[0][0] <= t:
            tx, pr, usd = heapq.heappop(heap)
            open_.pop(pr, None)
            last_exit[pr] = tx
            dpnl[day(tx)] += usd
        pr = p['pair']
        if pr in open_:
            skipped['pool_open'] += 1
            continue
        if cooldown_s and t < last_exit.get(pr, -1e18) + cooldown_s * 1000:
            skipped['cooldown'] += 1
            continue
        if len(open_) >= slots:
            skipped['slots_full'] += 1
            continue
        if cap_usd is not None and dpnl[day(t)] <= -cap_usd:
            skipped['day_cap'] += 1
            continue
        usd = notional * o[key] / 100
        open_[pr] = o['tx']
        heapq.heappush(heap, (o['tx'], pr, usd))
        trades.append({'pair': pr, 't': t, 'te': o['te'], 'tx': o['tx'], 'net0': o['net0'], 'net50': o['net50'],
                       'netcal': o['netcal'], 'usd50': notional * o['net50'] / 100, 'usd0': notional * o['net0'] / 100,
                       'flag': o['flag'], 'fee': o['fee'], 'liq': o['liq']})
    return trades, dict(skipped)


def _boot_pair(trades, key, reps=1000, seed=7):
    cl = defaultdict(list)
    for x in trades:
        cl[x['pair']].append(x[key])
    g = list(cl.values())
    if len(g) < 3:
        return None, None
    rnd = random.Random(seed)
    ms = []
    for _ in range(reps):
        tot = cnt = 0
        for _ in range(len(g)):
            v = g[rnd.randrange(len(g))]
            tot += sum(v)
            cnt += len(v)
        ms.append(tot / cnt)
    ms.sort()
    return round(ms[int(.025 * reps)], 3), round(ms[int(.975 * reps)], 3)


def summ(trades, span_h, capital=1000.0, boot=True):
    if not trades:
        return {'n': 0, 'tph': 0.0}
    n = len(trades)
    pairs = defaultdict(int)
    for x in trades:
        pairs[x['pair']] += 1
    m50 = sum(x['net50'] for x in trades) / n
    m0 = sum(x['net0'] for x in trades) / n
    mc = sum(x['netcal'] for x in trades) / n
    u50 = sum(x['usd50'] for x in trades)
    # realized equity curve in exit order
    bal = peak = capital
    mdd = 0.0
    mdd_usd = 0.0
    for x in sorted(trades, key=lambda r: r['tx']):
        bal += x['usd50']
        peak = max(peak, bal)
        mdd_usd = max(mdd_usd, peak - bal)
        mdd = max(mdd, (peak - bal) / peak * 100 if peak > 0 else 100.0)
    out = {'n': n, 'tph': round(n / span_h, 2), 'mean_net50': round(m50, 3), 'mean_net0': round(m0, 3),
           'mean_netcal': round(mc, 3),
           'mean_usd50': round(u50 / n, 3), 'usd50_per_h': round(u50 / span_h, 2), 'sum_usd50': round(u50, 2),
           'win50': round(100 * sum(1 for x in trades if x['net50'] > 0) / n, 1),
           'win0': round(100 * sum(1 for x in trades if x['net0'] > 0) / n, 1),
           'pairs': len(pairs), 'top_pair_share': round(max(pairs.values()) / n, 3),
           'maxdd_usd': round(mdd_usd, 2), 'maxdd_pct_of_1000': round(100 * mdd_usd / capital, 1),
           'fb_share': round(sum(1 for x in trades if x['flag'] == 'fb') / n, 3),
           'gap_share': round(sum(1 for x in trades if x['flag'] in ('GAP', 'VANISHED')) / n, 3),
           'avg_hold_s': round(sum(x['tx'] - x['t'] for x in trades) / n / 1000, 1)}
    if boot:
        out['ci95_net50_pair'] = _boot_pair(trades, 'net50')
    return out
