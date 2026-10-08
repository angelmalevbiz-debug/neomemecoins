"""INDEPENDENT re-implementation (verifier) of RANDOM_ctx1h_for_F2_OVERSOLD1H_STAB from its plain description.

Rule (plain description): PumpSwap SOL-quoted pools; at a scan point, enter with deterministic probability 0.005
(blake2b('f2r1|pair|t') < p) when liquidity >= $50k, the interim rug screen passes (mcap >= $20M & liq/mcap < 2% &
age < 14 d -> block; a young token whose normalized ticker was already seen on another pair -> block) and DexScreener
pc1h <= -12%. Exit +20% / -25% on modeled net PnL, or after 90 min. $200 notional, 300 s cooldown, one position per pair.

Execution model re-written here from the harness_final DOCSTRING (not imported): fill at the next DexScreener refresh
(first later point within 60 s whose price differs, else the next point; no entry if the next point is > 60 s away),
feed-gap (> 10 min) closes valued at min(pre-gap, return price) and realized at the return time, pairs that never return
closed at last price -10% (VANISHED) when the dataset continued 10+ min, else at the last price (END). Costs: PumpSwap
fee tier by market cap in SOL, impact 2N/L per leg (cap 20%), 20 bps slip+latency per leg, 0.0001 SOL network fee;
net50 = + calibrated extra per leg (CALIB_V1, engine basis, conservative) + 50 bps per leg, + 200 bps on STOP exit legs.

Only the data container (series.pkl, the shared point series) is shared with the harness. stdlib only.
"""
import sys
sys.dont_write_bytecode = True
import bisect, hashlib, math, pickle, random

DEEP = (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r''
NAN = float('nan')

# PumpSwap fee tiers (market cap in SOL upper bound, bps) - transcribed from backend/paper_market_feasibility.py
TIERS = ((420, 125.0), (1470, 120.0), (2460, 115.0), (3440, 110.0), (4420, 105.0), (9820, 100.0), (14740, 95.0),
         (19650, 90.0), (24560, 85.0), (29470, 80.0), (34380, 75.0), (39300, 70.0), (44210, 65.0), (49120, 60.0),
         (54030, 55.0), (58940, 52.5), (63860, 50.0), (68770, 47.5), (73681, 45.0), (78590, 42.5), (83500, 40.0),
         (88400, 37.5), (93330, 35.0), (98240, 32.5))

SER = None
META = None


def load():
    global SER, META
    if SER is None:
        with open(DEEP + '/series.pkl', 'rb') as fh:
            d = pickle.load(fh)
        SER = d['series']
        t0 = min(s['t'][0] for s in SER.values())
        t1 = max(s['t'][-1] for s in SER.values())
        META = {'t0': t0, 't1': t1, 'meta_pkl': d['meta']}
    return SER, META


def cut_time(frac=0.6):
    load()
    return META['t0'] + frac * (META['t1'] - META['t0'])


# ------------------------------------------------------------------ costs
def solusd(s, k):
    p, q = s['price'][k], s['pnative'][k]
    if s['quote_sol'] != 1 or not (p > 0 and q > 0):
        return 0.0
    return p / q


def fee(s, k):
    if s['dex'] != 'pumpswap':
        return 30.0
    su, mc = solusd(s, k), s['mcap'][k]
    if su <= 0 or not (mc > 0):
        return 125.0
    m = mc / su
    for lim, f in TIERS:
        if m < lim:
            return f
    return 30.0


def buy(s, j, N, extra=0.0):
    p, L, su = s['price'][j], s['liq'][j], solusd(s, j)
    if not (p > 0 and L > 0 and su > 0):
        return None
    imp = min(20.0, 200.0 * N / L)
    pen = (imp + (20.0 + extra) / 100.0) / 100.0
    return N * (1 - fee(s, j) / 1e4) / (p * (1 + pen)), 1e-4 * su


def sell(s, k, q, extra=0.0, px=None):
    p = s['price'][k] if px is None else px
    L, su = s['liq'][k], solusd(s, k)
    if not (p > 0):
        return None
    if L == 0:
        return 0.0
    mv = q * p
    imp = 20.0 if not (L > 0) else min(20.0, 200.0 * mv / L)
    pen = (imp + (20.0 + extra) / 100.0) / 100.0
    return max(0.0, mv * (1 - pen) * (1 - fee(s, k) / 1e4) - 1e-4 * su)


def calib_leg(fee_bps, liq, N):
    x = 22.0 if fee_bps <= 50 else (-5.0 if fee_bps <= 95 else -55.0)
    x = max(x, 10.0)
    if not (liq >= 50_000 and N > 0 and 100.0 * N / liq <= 0.30):
        x += 25.0
    x += 1e4 * (0.185 / 2 + (0.03 - 0.012)) / N
    return x


def calib_reason(reason):
    r = (reason or '').upper()
    if r.startswith('STOP'):
        return 200.0
    if r.startswith('TRAIL'):
        return 100.0
    return 0.0


# ------------------------------------------------------------------ signal pieces
def coin(salt, pair, t, prob):
    h = hashlib.blake2b(('%s|%s|%d' % (salt, pair, int(t))).encode(), digest_size=8).digest()
    return int.from_bytes(h, 'big') / 2 ** 64 < prob


def nt(sym):
    return ''.join(ch for ch in (sym or '') if ch.isalnum()).casefold()


_FIRST_OTHER = None


def first_other_same_ticker():
    """pair -> earliest first-observation time of ANOTHER pair with the same normalized ticker (inf if none)."""
    global _FIRST_OTHER
    if _FIRST_OTHER is None:
        ser, _ = load()
        by = {}
        for p, s in ser.items():
            by.setdefault(nt(s['sym']), []).append((s['t'][0], p))
        out = {}
        for lst in by.values():
            lst.sort()
            for t0, p in lst:
                others = [a for a, q in lst if q != p]
                out[p] = min(others) if others else float('inf')
        _FIRST_OTHER = out
    return _FIRST_OTHER


def screen_interim(s, i, fo):
    mc, L, age = s['mcap'][i], s['liq'][i], s['age'][i]
    young = not (age >= 14 * 1440)
    if mc >= 20e6 and L > 0 and L / mc < 0.02 and young:
        return True
    return young and s['t'][i] >= fo


def screen_train_only(s, i, fo):
    mc, L, age = s['mcap'][i], s['liq'][i], s['age'][i]
    young = not (age >= 14 * 1440)
    if mc >= 200e6 and L > 0 and L / mc < 0.01 and young:
        return True
    return young and s['t'][i] >= fo


def guard_v1(s, i):
    L, mc, age = s['liq'][i], s['mcap'][i], s['age'][i]
    if not (L == L and mc == mc and age == age and L > 0 and mc > 0 and age >= 0):
        return True
    r = L / mc
    return r >= 1.0 or age < 720.0 or (mc >= 20e6 and r < 0.01 and age < 14 * 1440.0)


SCREENS = {
    'interim': lambda s, i, fo: screen_interim(s, i, fo),
    'train_only': lambda s, i, fo: screen_train_only(s, i, fo),
    'none': lambda s, i, fo: False,
    'interim+guard_v1': lambda s, i, fo: screen_interim(s, i, fo) or guard_v1(s, i),
}


def eligible_index(liq_min=50_000.0, pc1h_max=-12.0, screen='interim'):
    """pair -> sorted list of point indices (excluding the last point) where the deterministic part of the rule passes.
    Uses only values at the point itself (and first-seen times <= that point for the ticker check)."""
    ser, _ = load()
    fo = first_other_same_ticker()
    scr = SCREENS[screen]
    out = {}
    for p, s in ser.items():
        if s['dex'] != 'pumpswap' or s['quote_sol'] != 1:
            continue
        liq, pc = s['liq'], s['pc1h']
        n = len(s['t'])
        f = fo[p]
        idx = []
        for i in range(n - 1):
            if not (liq[i] >= liq_min):
                continue
            if pc1h_max is not None and not (pc[i] <= pc1h_max):
                continue
            if scr(s, i, f):
                continue
            idx.append(i)
        if idx:
            out[p] = idx
    return out


# ------------------------------------------------------------------ execution
def fill_at(s, i):
    ts, pr = s['t'], s['price']
    n = len(ts)
    if i + 1 >= n or ts[i + 1] - ts[i] > 60_000:
        return None
    m = i + 1
    while m < n and ts[m] - ts[i] <= 60_000:
        if pr[m] != pr[i]:
            return m
        m += 1
    return i + 1


def gap_val(s, a):
    if a + 1 >= len(s['t']):
        return a
    pa, pb = s['price'][a], s['price'][a + 1]
    if s['liq'][a + 1] == 0 or (pb > 0 and pb < pa):
        return a + 1
    return a


def trade(pair, s, i, t_end, tp=20.0, stop=-25.0, hold_min=90.0, N=200.0, stress=50.0, end_haircut=0.0, exit_mode='next_refresh'):
    """Simulate one trade decided at point i. Returns (trade dict, e) or (None, None) when no fill."""
    ts, pr = s['t'], s['price']
    n = len(ts)
    j = fill_at(s, i)
    if j is None:
        return None, None
    cx = calib_leg(fee(s, j), s['liq'][j], N)
    b0 = buy(s, j, N)
    b50 = buy(s, j, N, stress + cx)
    if b0 is None or b50 is None:
        return None, None
    q0, nf = b0
    q50 = b50[0]
    trig, k, gap = None, j, False
    mfe = -1e9
    for k in range(j + 1, n):
        if ts[k] - ts[k - 1] > 600_000:
            gap = True
            k -= 1
            break
        v = sell(s, k, q0)
        if v is None:
            continue
        net = 100 * (v - N - nf) / N
        mfe = max(mfe, net)
        if net <= stop:
            trig = 'STOP'
        elif net >= tp:
            trig = 'TP'
        elif ts[k] - ts[j] >= hold_min * 60_000:
            trig = 'HOLD'
        if trig:
            break
    ev = None
    if gap:
        e, trig, flag = k, 'FEED_GAP', 'GAP'
        ev = gap_val(s, e)
        px = pr[ev]
    elif trig:
        e = fill_at(s, k)
        if e is not None:
            flag, px = '', (pr[e] if exit_mode == 'next_refresh' else (pr[k] if exit_mode == 'trigger' else min(pr[e], pr[k])))
        elif k + 1 < n and ts[k + 1] - ts[k] > 600_000:
            e, flag = k, 'GAP'
            ev = gap_val(s, e)
            px = pr[ev]
        elif k + 1 < n:
            e, flag, px = k + 1, '', pr[k + 1]
        else:
            e = k
            flag, px = ('VANISHED', pr[e] * 0.9) if t_end - ts[e] > 600_000 else ('END', pr[e] * (1 - end_haircut / 100))
    else:
        e, trig = n - 1, 'OPEN_AT_END'
        flag, px = ('VANISHED', pr[e] * 0.9) if t_end - ts[e] > 600_000 else ('END', pr[e] * (1 - end_haircut / 100))
    ev = e if ev is None else ev
    t_exit = ts[e + 1] if (flag == 'GAP' and e + 1 < n) else ts[e]
    rx = calib_reason(trig)
    v0 = sell(s, ev, q0, 0.0, px)
    v50 = sell(s, ev, q50, stress + cx + rx, px)
    net0 = 100 * ((v0 or 0.0) - N - nf) / N
    net50 = 100 * ((v50 or 0.0) - N - nf) / N
    return {'pair': pair, 'sym': s['sym'], 'decision_t': ts[i], 'entry_t': ts[j], 'exit_t': t_exit, 'reason': trig,
            'flag': flag, 'net0': net0, 'net50': net50, 'usd0': N * net0 / 100, 'usd50': N * net50 / 100, 'size': N,
            'hold_s': (t_exit - ts[j]) / 1000, 'fee_bps': fee(s, j), 'liq': s['liq'][j]}, e


def run(elig, salt='f2r1', prob=0.005, cooldown_s=300, end_haircut=0.0, count_skips=None, **kw):
    """Random entries at eligible points (hashed coin), one position per pair, cooldown after exit."""
    ser, meta = load()
    t_end = meta['t1']
    out = []
    for p, idx in elig.items():
        s = ser[p]
        ts = s['t']
        next_ok, nxt_i = -1.0, 0
        for i in idx:
            if i < nxt_i or ts[i] < next_ok:
                continue
            if prob < 1.0 and not coin(salt, p, ts[i], prob):
                continue
            tr, e = trade(p, s, i, t_end, end_haircut=end_haircut, **kw)
            if tr is None:
                if count_skips is not None:
                    count_skips.append((p, i))
                continue
            out.append(tr)
            next_ok = tr['exit_t'] + cooldown_s * 1000
            nxt_i = e + 1
    out.sort(key=lambda x: (x['entry_t'], x['pair']))
    return out


# ------------------------------------------------------------------ statistics
def _boot(groups, reps=2000, seed=7):
    if len(groups) < 3:
        return None, None
    r = random.Random(seed)
    ms = []
    for _ in range(reps):
        tot = cnt = 0
        for _ in range(len(groups)):
            g = groups[r.randrange(len(groups))]
            tot += sum(g)
            cnt += len(g)
        ms.append(tot / cnt)
    ms.sort()
    return round(ms[int(reps * .025)], 3), round(ms[min(reps - 1, int(reps * .975))], 3)


def med(xs):
    s = sorted(xs)
    n = len(s)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2


def summ(trs, key='usd50', span_h=None):
    if not trs:
        return {'n': 0}
    pk = 'net50' if key == 'usd50' else 'net0'
    xs = [x[key] for x in trs]
    pc = [x[pk] for x in trs]
    cl, cp = {}, {}
    for x in trs:
        cl.setdefault((x['pair'], int(x['entry_t'] // 3_600_000)), []).append(x[key])
        cp.setdefault(x['pair'], []).append(x[key])
    g = sum(v for v in xs if v > 0)
    l = -sum(v for v in xs if v < 0)
    ex = {}
    for x in trs:
        lab = x['reason'] + ('/' + x['flag'] if x['flag'] else '')
        ex[lab] = ex.get(lab, 0) + 1
    return {'n': len(xs), 'pairs': len(cp), 'clusters': len(cl), 'win': round(100 * sum(v > 0 for v in xs) / len(xs), 1),
            'mean_usd': round(sum(xs) / len(xs), 3), 'mean_pct': round(sum(pc) / len(pc), 3),
            'median_pct': round(med(pc), 3), 'sum_usd': round(sum(xs), 2),
            'pf': round(g / l, 3) if l > 0 else None, 'ci95_pairhour': _boot(list(cl.values())),
            'ci95_pair': _boot(list(cp.values())),
            'top_pair_share': round(max(len(v) for v in cp.values()) / len(xs), 3),
            'tph': round(len(xs) / span_h, 2) if span_h else None, 'exits': ex}


def split(trs, frac=0.6):
    c = cut_time(frac)
    return [x for x in trs if x['entry_t'] < c], [x for x in trs if x['entry_t'] >= c]


def portfolio(trs, slots=3, start=1000.0, key='usd50'):
    active, bal, peak, mdd, taken = [], start, start, 0.0, 0

    def realize(upto):
        nonlocal bal, peak, mdd, active
        for a in sorted((a for a in active if a['exit_t'] <= upto), key=lambda r: r['exit_t']):
            bal += a[key]
            peak = max(peak, bal)
            mdd = max(mdd, (peak - bal) / peak * 100 if peak > 0 else 0.0)
        active = [a for a in active if a['exit_t'] > upto]
    for x in sorted(trs, key=lambda r: r['entry_t']):
        realize(x['entry_t'])
        if len(active) >= slots or bal - sum(a['size'] for a in active) < x['size']:
            continue
        active.append(x)
        taken += 1
    realize(float('inf'))
    return {'taken': taken, 'final': round(bal, 2), 'mdd_pct': round(mdd, 2)}
