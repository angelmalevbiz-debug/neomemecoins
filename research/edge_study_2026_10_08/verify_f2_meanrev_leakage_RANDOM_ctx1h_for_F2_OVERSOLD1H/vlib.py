"""Independent re-implementation (verifier) of the RANDOM_ctx1h_for_F2_OVERSOLD1H_STAB rule and of the trade
mechanics it runs through, written from the plain description - it does NOT import harness.py / harness_final.py.
PAPER research only. Reads series.pkl (read-only).

Rule (plain description): PumpSwap SOL-quoted pool, liquidity >= $50k, DexScreener pc1h <= -12 %, interim rug screen
passes, [variant rug_guard_v1: rug_guard_v1 passes], and a deterministic hashed coin (blake2b of 'salt|pair|int(t)')
< p (p = 0.005, salt 'f2r1').  Buy $200 at the next DexScreener refresh; exit on modeled net PnL +20 % / -25 % / 90 min,
filled at the next refresh after the trigger; 300 s cooldown per pair after an exit.
"""
import sys
sys.dont_write_bytecode = True
import array, hashlib, math, pickle, random

try:
    import ctypes
    ctypes.windll.kernel32.SetPriorityClass(ctypes.windll.kernel32.GetCurrentProcess(), 0x00004000)
except Exception:
    pass

DEEP = (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r''
OUT = DEEP + '/verify_f2_meanrev_leakage_RANDOM_ctx1h_for_F2_OVERSOLD1H'

# engine fee table (copied by value from backend/paper_market_feasibility.PUMP_FEE_TIERS; mcap in SOL -> bps)
FEE_TIERS = ((420, 125.0), (1470, 120.0), (2460, 115.0), (3440, 110.0), (4420, 105.0), (9820, 100.0),
             (14740, 95.0), (19650, 90.0), (24560, 85.0), (29470, 80.0), (34380, 75.0), (39300, 70.0),
             (44210, 65.0), (49120, 60.0), (54030, 55.0), (58940, 52.5), (63860, 50.0), (68770, 47.5),
             (73681, 45.0), (78590, 42.5), (83500, 40.0), (88400, 37.5), (93330, 35.0), (98240, 32.5))
NAN = float('nan')

_DATA = None


def load():
    global _DATA
    if _DATA is None:
        with open(DEEP + '/series.pkl', 'rb') as fh:
            d = pickle.load(fh)
        _DATA = (d['series'], d['meta'])
    return _DATA


# ------------------------------------------------------------------ costs (engine model, own code)
def sol_usd(s, k):
    p, n = s['price'][k], s['pnative'][k]
    if s['quote_sol'] != 1 or not (p > 0 and n > 0):
        return 0.0
    return p / n


def fee_bps(s, k):
    if s['dex'] != 'pumpswap':
        return 30.0
    su, mc = sol_usd(s, k), s['mcap'][k]
    if su <= 0 or not (mc > 0):
        return 125.0
    m = mc / su
    for lim, fee in FEE_TIERS:
        if m < lim:
            return fee
    return 30.0


def buy(s, j, usd, extra=0.0):
    p, liq, su = s['price'][j], s['liq'][j], sol_usd(s, j)
    if not (p > 0 and liq > 0 and su > 0):
        return None
    imp = min(20.0, 200.0 * usd / liq)                 # % impact, 2N/L
    pen = (imp + (20.0 + extra) / 100.0) / 100.0       # 10 bps slippage + 10 bps latency + extra
    return usd * (1 - fee_bps(s, j) / 1e4) / (p * (1 + pen)), 0.0001 * su


def sell(s, k, qty, extra=0.0, px=None):
    p = s['price'][k] if px is None else px
    liq, su = s['liq'][k], sol_usd(s, k)
    if not (p > 0):
        return None
    if liq == 0:
        return 0.0
    mv = qty * p
    imp = 20.0 if not (liq > 0) else min(20.0, 200.0 * mv / liq)
    pen = (imp + (20.0 + extra) / 100.0) / 100.0
    return max(0.0, mv * (1 - pen) * (1 - fee_bps(s, k) / 1e4) - 0.0001 * su)


def calib_leg(fee, liq, usd):
    """CALIB_V1 engine/conservative extra bps per leg (from the integrator's documented rule)."""
    x = 22.0 if fee <= 50 else (-5.0 if fee <= 95 else -55.0)
    x = max(x, 10.0)
    if not (liq >= 50_000 and usd > 0 and 100.0 * usd / liq <= 0.30):
        x += 25.0
    x += 1e4 * (0.185 / 2 + (0.03 - 0.012)) / usd
    return x


def reason_extra(reason):
    r = (reason or '').upper()
    return 200.0 if r.startswith('STOP') else (100.0 if r.startswith('TRAIL') else 0.0)


# ------------------------------------------------------------------ eligibility
def norm(sym):
    return ''.join(c for c in (sym or '') if c.isalnum()).casefold()


_TIDX = None


def ticker_index():
    global _TIDX
    if _TIDX is None:
        series, _ = load()
        idx = {}
        for pair, s in series.items():
            idx.setdefault(norm(s['sym']), []).append((s['t'][0], pair))
        for v in idx.values():
            v.sort()
        _TIDX = idx
    return _TIDX


def reused_ticker(s, t):
    lst = ticker_index().get(norm(s['sym']), [])
    return sum(1 for t0, p in lst if t0 <= t and p != s['pair']) >= 1


def interim_screen(s, i, variant='interim'):
    """True = blocked. variant: 'interim' (holdout-informed $20M / 2 %), 'trainonly' ($200M / 1 %), 'none'."""
    if variant == 'none':
        return False
    mc, liq, age = s['mcap'][i], s['liq'][i], s['age'][i]
    young = not (age >= 14 * 1440)
    mc_min, ratio = (20e6, 0.02) if variant == 'interim' else (200e6, 0.01)
    if mc >= mc_min and liq > 0 and liq / mc < ratio and young:
        return True
    if young and reused_ticker(s, s['t'][i]):
        return True
    return False


def guard_v1(s, i):
    """rug_guard_v1: True = blocked (LP_PULLABLE liq/mcap>=1, YOUNG_POOL age<720 min, FAKE_MCAP; fail closed)."""
    liq, mc, age = s['liq'][i], s['mcap'][i], s['age'][i]
    if not (liq == liq and mc == mc and age == age and liq > 0 and mc > 0 and age >= 0):
        return True
    r = liq / mc
    return r >= 1.0 or age < 720.0 or (mc >= 20e6 and r < 0.01 and age < 14 * 1440.0)


def context_ok(s, i, pc1h_max=-12.0, liq_min=50_000.0, screen='interim', guard=True):
    if not (s['liq'][i] >= liq_min):
        return False
    if interim_screen(s, i, screen):
        return False
    if not (s['pc1h'][i] <= pc1h_max):
        return False
    if guard and guard_v1(s, i):
        return False
    return True


def coin(pair, t, prob, salt):
    h = hashlib.blake2b(('%s|%s|%d' % (salt, pair, int(t))).encode(), digest_size=8).digest()
    return int.from_bytes(h, 'big') / 2 ** 64 < prob


def tradable_pairs():
    series, _ = load()
    return [(p, s) for p, s in series.items() if s['dex'] == 'pumpswap' and s['quote_sol'] == 1]


def eligible_points(**kw):
    """{pair: [decision indices i (i < n-1) where the context holds]}."""
    out = {}
    for pair, s in tradable_pairs():
        n = len(s['t'])
        lst = [i for i in range(n - 1) if context_ok(s, i, **kw)]
        if lst:
            out[pair] = lst
    return out


# ------------------------------------------------------------------ simulator (own code)
def fill_at(s, i, rule='next_refresh', lag=60_000):
    ts, pr = s['t'], s['price']
    n = len(ts)
    if i + 1 >= n or ts[i + 1] - ts[i] > 60_000:
        return None
    if rule == 'next_point':
        return i + 1
    m = i + 1
    while m < n and ts[m] - ts[i] <= lag:
        if pr[m] != pr[i]:
            return m
        m += 1
    return i + 1


def gap_val_idx(s, a):
    if a + 1 >= len(s['t']):
        return a
    pa, pb = s['price'][a], s['price'][a + 1]
    if s['liq'][a + 1] == 0 or (pb > 0 and pb < pa):
        return a + 1
    return a


def close_px(s, e, t_end, haircut=10.0):
    if t_end - s['t'][e] > 600_000:
        return 'VANISHED', s['price'][e] * (1 - haircut / 100)
    return 'END', s['price'][e]


def sim_pair(s, chosen, t_end, tp=20.0, stop=-25.0, hold_min=90.0, usd=200.0, cooldown_s=300, rule='next_refresh',
             gap_ms=600_000, calib=True, haircut=10.0, stats=None):
    """chosen: sorted decision indices where the (stateless) signal is true. Returns trades."""
    ts, n = s['t'], len(s['t'])
    out = []
    cur, next_ok = 0, -1.0
    for i in chosen:
        if i < cur or i >= n - 1 or ts[i] < next_ok:
            continue
        j = fill_at(s, i, rule)
        if j is None:
            if stats is not None:
                stats['skip_nofill'] = stats.get('skip_nofill', 0) + 1
            continue
        cx = calib_leg(fee_bps(s, j), s['liq'][j], usd) if calib else 0.0
        f0 = buy(s, j, usd)
        f50 = buy(s, j, usd, 50.0 + cx)
        if f0 is None or f50 is None:
            if stats is not None:
                stats['skip_noinputs'] = stats.get('skip_noinputs', 0) + 1
            continue
        qty, netfee = f0
        qty50 = f50[0]
        trig, k, gap = None, j, False
        mfe, mae = -1e9, 1e9
        for k in range(j + 1, n):
            if gap_ms is not None and ts[k] - ts[k - 1] > gap_ms:
                gap = True
                k -= 1
                break
            v = sell(s, k, qty)
            if v is None:
                continue
            net = 100 * (v - usd - netfee) / usd
            mfe, mae = max(mfe, net), min(mae, net)
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
            ev = gap_val_idx(s, e)
            px = s['price'][ev]
        elif trig:
            e = fill_at(s, k, rule)
            if e is not None:
                flag, px = '', s['price'][e]
            elif k + 1 < n and gap_ms is not None and ts[k + 1] - ts[k] > gap_ms:
                e, flag = k, 'GAP'
                ev = gap_val_idx(s, e)
                px = s['price'][ev]
            elif k + 1 < n:
                e = k + 1
                flag, px = '', s['price'][e]
            else:
                e = k
                flag, px = close_px(s, e, t_end, haircut)
        else:
            e, trig = n - 1, 'OPEN_AT_END'
            flag, px = close_px(s, e, t_end, haircut)
        ev = e if ev is None else ev
        t_exit = ts[e + 1] if (flag == 'GAP' and e + 1 < n) else ts[e]
        rx = reason_extra(trig) if calib else 0.0
        v0 = sell(s, ev, qty, 0.0, px)
        v50 = sell(s, ev, qty50, 50.0 + cx + rx, px)
        net0 = 100 * ((v0 or 0.0) - usd - netfee) / usd
        net50 = 100 * ((v50 or 0.0) - usd - netfee) / usd
        out.append({'pair': s['pair'], 'sym': s['sym'], 'decision_t': ts[i], 'entry_t': ts[j], 'exit_t': t_exit,
                    'reason': trig, 'flag': flag, 'net0': net0, 'net50': net50, 'usd0': usd * net0 / 100,
                    'usd50': usd * net50 / 100, 'size': usd, 'fee_bps': fee_bps(s, j), 'liq': s['liq'][j],
                    'mfe': mfe if mfe > -1e8 else 0.0, 'mae': mae if mae < 1e8 else 0.0,
                    'hold_s': (t_exit - ts[j]) / 1000})
        next_ok = t_exit + cooldown_s * 1000
        cur = e + 1
    return out


def run(chosen_by_pair, **kw):
    series, meta = load()
    out = []
    for pair, ch in chosen_by_pair.items():
        out.extend(sim_pair(series[pair], ch, meta['t1'], **kw))
    out.sort(key=lambda x: (x['entry_t'], x['pair']))
    return out


def choose(elig, prob, salt):
    series, _ = load()
    out = {}
    for pair, lst in elig.items():
        ts = series[pair]['t']
        ch = [i for i in lst if coin(pair, ts[i], prob, salt)]
        if ch:
            out[pair] = ch
    return out


# ------------------------------------------------------------------ statistics (own code)
def cut_t(frac=0.6):
    _, meta = load()
    return meta['t0'] + frac * (meta['t1'] - meta['t0'])


def boot(groups, reps=2000, seed=11):
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
    return round(ms[int(.025 * reps)], 2), round(ms[int(.975 * reps)], 2)


def summ(tr, key='usd50'):
    if not tr:
        return {'n': 0}
    xs = [x[key] for x in tr]
    pk = 'net50' if key == 'usd50' else 'net0'
    ps = sorted(x[pk] for x in tr)
    n = len(ps)
    med = ps[n // 2] if n % 2 else (ps[n // 2 - 1] + ps[n // 2]) / 2
    pairs = {}
    ph = {}
    for x in tr:
        pairs.setdefault(x['pair'], []).append(x[key])
        ph.setdefault((x['pair'], int(x['entry_t'] // 3_600_000)), []).append(x[key])
    g = sum(v for v in xs if v > 0)
    l = -sum(v for v in xs if v < 0)
    reasons = {}
    for x in tr:
        lab = x['reason'] + ('/' + x['flag'] if x['flag'] else '')
        reasons[lab] = reasons.get(lab, 0) + 1
    return {'n': n, 'pairs': len(pairs), 'clusters': len(ph), 'mean_usd': round(sum(xs) / n, 3),
            'mean_pct': round(sum(x[pk] for x in tr) / n, 3), 'median_pct': round(med, 3),
            'win': round(100 * sum(1 for v in xs if v > 0) / n, 1), 'pf': round(g / l, 3) if l > 0 else None,
            'sum_usd': round(sum(xs), 2), 'ci_pairhour': boot(list(ph.values())), 'ci_pair': boot(list(pairs.values())),
            'top_pair_share': round(max(len(v) for v in pairs.values()) / n, 3), 'exits': reasons}


def split(tr, frac=0.6):
    c = cut_t(frac)
    return [x for x in tr if x['entry_t'] < c], [x for x in tr if x['entry_t'] >= c]


def portfolio(trades, slots=3, start=1000.0, key='usd50'):
    active, bal, peak, mdd, taken = [], start, start, 0.0, 0

    def realize(upto):
        nonlocal bal, peak, mdd, active
        for a in sorted((a for a in active if a['exit_t'] <= upto), key=lambda r: r['exit_t']):
            bal += a[key]
            peak = max(peak, bal)
            mdd = max(mdd, (peak - bal) / peak * 100 if peak > 0 else 0.0)
        active = [a for a in active if a['exit_t'] > upto]
    for x in sorted(trades, key=lambda r: r['entry_t']):
        realize(x['entry_t'])
        if len(active) >= slots or bal - sum(a['size'] for a in active) < x['size']:
            continue
        active.append(x)
        taken += 1
    realize(float('inf'))
    return {'taken': taken, 'final': round(bal, 2), 'mdd_pct': round(mdd, 2)}
