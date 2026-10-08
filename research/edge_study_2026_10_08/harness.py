"""Shared, look-ahead-safe PAPER backtest harness over the recorded observation dataset.

Fixed rules (decided before any hypothesis is tested; do not change them per hypothesis):
- Points: per pair, rows in first-observation order. A row is kept when the DexScreener
  updatedAt strictly increases, or 15 s passed since the last kept row (flow-only refresh).
- Signals see only points <= i through the Past view (indexing the future raises LookAhead).
- Entry fills at the next point j = i + 1 (one scan of latency); skipped if t_j - t_i > 60 s.
- Costs per leg mirror backend/paper_market_feasibility.modeled_roundtrip: PumpSwap fee tier from
  market cap in SOL (engine table), constant-product impact 2*N/L (cap 20%), 10 bps slippage +
  10 bps latency buffer, 0.0001 SOL network fee. Exit decisions use this model (stress 0).
  Every trade is also re-priced with +50 bps per leg (net50) - the acceptance stress.
- Exits are checked at each later point on modeled net PnL; the fill happens at the NEXT point
  after the trigger (latency, gap-through risk included). A trigger at the last point of a pair
  closes at that price minus VANISH_HAIRCUT_PCT when the dataset continued for 10+ minutes
  (pair vanished from the feed: flagged VANISHED), or at that price when the dataset ended (END).
- One position per pair at a time; cooldown after each exit.
- Train/holdout: entries before T0 + 60% of the span are train, the rest holdout. Choose
  parameters on train only; report holdout for at most 3 pre-chosen configs per family.
"""
import array, bisect, hashlib, math, os, pickle, random, sqlite3, sys, time

DEEP = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(DEEP, 'obs.sqlite3')
CACHE = os.path.join(DEEP, 'series.pkl')
sys.path.insert(0, (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '.'))) + '/../../backend' + '')
from paper_market_feasibility import PUMP_FEE_TIERS  # engine fee table, read-only import

try:  # research must never starve the live PAPER engines on this machine
    import ctypes
    ctypes.windll.kernel32.SetPriorityClass(ctypes.windll.kernel32.GetCurrentProcess(), 0x00004000)  # BELOW_NORMAL
except Exception:
    pass

NAN = float('nan')
NUM = ['t', 'price', 'pnative', 'liq', 'mcap', 'v5', 'v1h', 'v6h', 'v24', 'pc5', 'pc1h', 'pc6h', 'pc24',
       'b5', 's5', 'b1h', 's1h', 'b6h', 's6h', 'age', 'boost', 'score', 'risk',
       'vf_trades', 'vf_buy', 'vf_sell', 'vf_wallets', 'vf_age_ms', 'sf_buy', 'sf_sell', 'sf_wallets',
       'hp_uw5', 'hp_rb5', 'conv', 'src']
# src bit flags: 1 boosted, 2 boosted-latest, 4 latest, 8 gecko-new-pools, 16 pumpswap-address-catalog
SRC_BITS = {'boosted': 1, 'boosted-latest': 2, 'latest': 4, 'gecko-new-pools': 8, 'pumpswap-address-catalog': 16}
VANISH_HAIRCUT_PCT = 10.0
MAX_ENTRY_LAG_MS = 60_000
BASE_BPS = 20.0          # 10 bps slippage + 10 bps latency buffer, per leg
STRESS_BPS = 50.0        # acceptance stress, per leg
NETWORK_SOL = 0.0001


class LookAhead(Exception):
    pass


def _f(x):
    try:
        v = float(x)
        return v if math.isfinite(v) else NAN
    except (TypeError, ValueError):
        return NAN


def build_cache():
    con = sqlite3.connect(DB)
    head = ['pair', 'mint', 'dex', 'quote_sol', 'sym', 'created', 'upd', 'sources']
    body = [c for c in NUM if c != 'src']
    q = 'SELECT %s FROM o ORDER BY pair, t' % ','.join(head + body)
    data, cur, last_upd, last_t = {}, None, None, None
    kept = total = 0
    nb = len(head)
    for r in con.execute(q):
        total += 1
        pair = r[0]
        if pair != cur:
            cur = pair
            s = {c: array.array('d') for c in NUM}
            s.update({'pair': r[0], 'mint': r[1], 'dex': (r[2] or '').lower(), 'quote_sol': r[3],
                      'sym': r[4], 'created': r[5]})
            data[pair] = s
            last_upd, last_t = None, None
        upd, t = r[6], r[nb]
        newer = upd is not None and (last_upd is None or upd > last_upd)
        if not (last_t is None or newer or (t - last_t >= 15_000)):
            continue
        if newer:
            last_upd = upd
        last_t = t
        kept += 1
        flags = 0
        for name in (r[7] or '').split('|'):
            flags |= SRC_BITS.get(name, 0)
        for c, v in zip(body, r[nb:]):
            s[c].append(_f(v))
        s['src'].append(float(flags))
    con.close()
    meta = {'rows': total, 'points': kept, 'pairs': len(data),
            't0': min(x['t'][0] for x in data.values()), 't1': max(x['t'][-1] for x in data.values())}
    with open(CACHE, 'wb') as fh:
        pickle.dump({'meta': meta, 'series': data}, fh, protocol=pickle.HIGHEST_PROTOCOL)
    return meta


_LOADED = None


def load():
    """Returns (series_by_pair, meta). Builds the cache once."""
    global _LOADED
    if _LOADED is None:
        if not os.path.exists(CACHE):
            build_cache()
        with open(CACHE, 'rb') as fh:
            d = pickle.load(fh)
        _LOADED = (d['series'], d['meta'])
    return _LOADED


def split_t(meta, frac=0.6):
    return meta['t0'] + frac * (meta['t1'] - meta['t0'])


# ---------------------------------------------------------------- costs
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
    mcs = mc / su
    return next((fee for limit, fee in PUMP_FEE_TIERS if mcs < limit), 30.0)


def entry_fill(s, j, notional, extra_bps=0.0):
    """(quantity bought at point j, network fee USD), or None when the model lacks inputs."""
    p, liq, su = s['price'][j], s['liq'][j], sol_usd(s, j)
    if not (p > 0 and liq > 0 and su > 0):
        return None
    fee = fee_bps(s, j) / 1e4
    impact = min(20.0, 2 * notional / liq * 100)
    pen = (impact + (BASE_BPS + extra_bps) / 100) / 100
    return notional * (1 - fee) / (p * (1 + pen)), NETWORK_SOL * su


def exit_value(s, k, qty, extra_bps=0.0, price=None):
    """Net USD proceeds of selling qty at point k (optionally at an explicit price)."""
    p = s['price'][k] if price is None else price
    liq, su = s['liq'][k], sol_usd(s, k)
    if not (p > 0):
        return None
    mv = qty * p
    impact = 20.0 if not (liq > 0) else min(20.0, 2 * mv / liq * 100)
    pen = (impact + (BASE_BPS + extra_bps) / 100) / 100
    return max(0.0, mv * (1 - pen) * (1 - fee_bps(s, k) / 1e4) - NETWORK_SOL * su)


def roundtrip_cost_pct(s, k, notional):
    """Modeled immediate round-trip loss in % at point k (positive number), or None."""
    e = entry_fill(s, k, notional)
    if e is None:
        return None
    qty, net = e
    v = exit_value(s, k, qty)
    return None if v is None else 100 * (notional + net - v) / notional


# ---------------------------------------------------------------- past-only view
class Past:
    __slots__ = ('s', 'i')

    def __init__(self, s, i):
        self.s, self.i = s, i

    def __call__(self, f, back=0):
        if back < 0:
            raise LookAhead(f)
        k = self.i - back
        return self.s[f][k] if k >= 0 else NAN

    @property
    def t(self):
        return self.s['t'][self.i]

    def ago(self, f, seconds):
        """Value at the latest point with t <= now - seconds (NaN if none)."""
        ts = self.s['t']
        k = bisect.bisect_right(ts, ts[self.i] - seconds * 1000, 0, self.i + 1) - 1
        return self.s[f][k] if k >= 0 else NAN

    def window(self, f, seconds):
        """Values of f over (now - seconds, now]."""
        ts = self.s['t']
        lo = bisect.bisect_right(ts, ts[self.i] - seconds * 1000, 0, self.i + 1)
        return self.s[f][lo:self.i + 1]

    def static(self, f):
        return self.s[f]

    def fee_bps(self):
        return fee_bps(self.s, self.i)

    def rt_cost_pct(self, notional):
        return roundtrip_cost_pct(self.s, self.i, notional)

    def history_len(self):
        return self.i + 1


_TICKERS = None


def norm_ticker(sym):
    return ''.join(ch for ch in (sym or '') if ch.isalnum()).casefold()


def _ticker_index():
    """ticker -> sorted list of (first_seen_t, pair). Built from first observations only (past-safe use below)."""
    global _TICKERS
    if _TICKERS is None:
        series, _ = load()
        idx = {}
        for pair, s in series.items():
            idx.setdefault(norm_ticker(s['sym']), []).append((s['t'][0], pair))
        for v in idx.values():
            v.sort()
        _TICKERS = idx
    return _TICKERS


def other_pairs_same_ticker_before(P):
    """How many OTHER pairs with the same normalized ticker were first seen at or before now."""
    lst = _ticker_index().get(norm_ticker(P.static('sym')), [])
    me, now = P.static('pair'), P.t
    return sum(1 for t0, p in lst if t0 <= now and p != me)


def interim_rug_risk(P):
    """INTERIM past-only screen for the serial fake-market-cap rug family found on 2026-10-08.

    10 of 64 pools that entered the cost-first universe were drained (liquidity -97..-99.9%,
    price -> 0) within 22.8 h; all had market cap >= $20M with liquidity/market cap < 2% on a
    token younger than 14 days, and most reused a ticker already seen on another pool.
    """
    mc, liq, age = P('mcap'), P('liq'), P('age')
    young = not (age >= 14 * 1440)
    if mc >= 20e6 and liq > 0 and liq / mc < 0.02 and young:
        return True
    if young and other_pairs_same_ticker_before(P) >= 1:
        return True
    return False


def hashed_coin(pair, t, prob, salt='r'):
    """Deterministic pseudo-random draw for random-entry baselines."""
    h = hashlib.blake2b(('%s|%s|%d' % (salt, pair, int(t))).encode(), digest_size=8).digest()
    return int.from_bytes(h, 'big') / 2 ** 64 < prob


# ---------------------------------------------------------------- simulation
def simulate(signal, *, stop=-5.0, tp=10.0, trail_arm=None, trail=None, hold_min=60.0, exit_fn=None,
             notional=200.0, size_fn=None, cooldown_s=300, dexes=('pumpswap',), pairs=None,
             t_from=None, t_to=None, tag=''):
    """Run one configuration over every pair. Returns a list of trade dicts sorted by entry time.

    signal(P) -> truthy to enter (P is a Past view at the decision point; it cannot see the future).
    exit_fn(P, pos) -> reason string or None; P is a Past view at the evaluated point.
    size_fn(P) -> notional USD (defaults to `notional`).
    tp=None disables the fixed target; trail_arm/trail enable a net trailing stop.
    """
    series, meta = load()
    t_end = meta['t1']
    out = []
    for pair, s in series.items():
        if pairs is not None and pair not in pairs:
            continue
        if dexes is not None and s['dex'] not in dexes:
            continue
        if s['quote_sol'] != 1:
            continue
        ts, n = s['t'], len(s['t'])
        i, next_ok = 0, -1.0
        while i < n - 1:
            ti = ts[i]
            if ti < next_ok or (t_from is not None and ti < t_from) or (t_to is not None and ti >= t_to):
                i += 1
                continue
            P = Past(s, i)
            if not signal(P):
                i += 1
                continue
            j = i + 1
            if ts[j] - ti > MAX_ENTRY_LAG_MS:
                i += 1
                continue
            size = size_fn(P) if size_fn else notional
            if not size or size <= 0:
                i += 1
                continue
            fill = entry_fill(s, j, size)
            fill50 = entry_fill(s, j, size, STRESS_BPS)
            if fill is None or fill50 is None:
                i += 1
                continue
            qty, netfee = fill
            qty50 = fill50[0]
            peak, mfe, mae = -1e9, -1e9, 1e9
            pos = {'entry_t': ts[j], 'entry_i': j, 'size': size, 'qty': qty, 'net': 0.0, 'peak': 0.0}
            trig, k = None, j
            for k in range(j + 1, n):
                v = exit_value(s, k, qty)
                if v is None:
                    continue
                net = 100 * (v - size - netfee) / size
                mfe, mae = max(mfe, net), min(mae, net)
                peak = max(peak, net)
                pos['net'], pos['peak'] = net, peak
                if net <= stop:
                    trig = 'STOP'
                elif tp is not None and net >= tp:
                    trig = 'TP'
                elif trail_arm is not None and peak >= trail_arm and net <= peak - trail:
                    trig = 'TRAIL'
                elif (ts[k] - ts[j]) >= hold_min * 60_000:
                    trig = 'HOLD'
                elif exit_fn is not None:
                    trig = exit_fn(Past(s, k), pos)
                if trig:
                    break
            if trig and k + 1 < n:
                e, flag = k + 1, ''
                px = s['price'][e]
            else:
                e = k if trig else n - 1
                trig = trig or 'OPEN_AT_END'
                if t_end - ts[e] > 600_000:
                    flag, px = 'VANISHED', s['price'][e] * (1 - VANISH_HAIRCUT_PCT / 100)
                else:
                    flag, px = 'END', s['price'][e]
            v0 = exit_value(s, e, qty, 0.0, px)
            v50 = exit_value(s, e, qty50, STRESS_BPS, px)
            net0 = 100 * ((v0 or 0.0) - size - netfee) / size
            net50 = 100 * ((v50 or 0.0) - size - netfee) / size
            out.append({'pair': pair, 'sym': s['sym'], 'entry_t': ts[j], 'exit_t': ts[e], 'reason': trig,
                        'flag': flag, 'net0': net0, 'net50': net50, 'usd0': size * net0 / 100,
                        'usd50': size * net50 / 100, 'size': size,
                        'mfe': mfe if mfe > -1e8 else 0.0, 'mae': mae if mae < 1e8 else 0.0,
                        'hold_s': (ts[e] - ts[j]) / 1000, 'fee_bps': fee_bps(s, j), 'liq': s['liq'][j],
                        'rt_cost': roundtrip_cost_pct(s, j, size), 'tag': tag})
            next_ok = ts[e] + cooldown_s * 1000
            i = e + 1
    out.sort(key=lambda x: x['entry_t'])
    return out


# ---------------------------------------------------------------- statistics
def _pf(xs):
    g = sum(x for x in xs if x > 0)
    l = -sum(x for x in xs if x < 0)
    return round(g / l, 3) if l > 0 else (float('inf') if g > 0 else None)


def cluster_ci(trades, key='usd50', reps=2000, seed=7):
    """95% CI of the mean per-trade value, bootstrap over (pair, hour) clusters."""
    if not trades:
        return None, None, 0
    cl = {}
    for x in trades:
        cl.setdefault((x['pair'], int(x['entry_t'] // 3_600_000)), []).append(x[key])
    groups = list(cl.values())
    if len(groups) < 3:
        return None, None, len(groups)
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
    return round(means[int(reps * .025)], 3), round(means[int(reps * .975)], 3), len(groups)


def summarize(trades, key='usd50'):
    if not trades:
        return {'n': 0}
    xs = [x[key] for x in trades]
    pct = [x['net50' if key == 'usd50' else 'net0'] for x in trades]
    lo, hi, ncl = cluster_ci(trades, key)
    span_h = max(1.0, (max(x['entry_t'] for x in trades) - min(x['entry_t'] for x in trades)) / 3.6e6)
    reasons = {}
    for x in trades:
        label = x['reason'] + (('/' + x['flag']) if x['flag'] else '')
        reasons[label] = reasons.get(label, 0) + 1
    counts = {}
    for x in trades:
        counts[x['pair']] = counts.get(x['pair'], 0) + 1
    sp = sorted(pct)
    return {'n': len(xs), 'pairs': len(counts), 'clusters': ncl,
            'win_rate': round(100 * sum(1 for v in xs if v > 0) / len(xs), 1),
            'mean_usd': round(sum(xs) / len(xs), 3), 'mean_pct': round(sum(pct) / len(pct), 3),
            'median_pct': round(sp[len(sp) // 2], 3), 'sum_usd': round(sum(xs), 2), 'pf': _pf(xs),
            'ci95_mean_usd': [lo, hi], 'trades_per_hour': round(len(xs) / span_h, 2),
            'avg_hold_min': round(sum(x['hold_s'] for x in trades) / len(trades) / 60, 1),
            'top_pair_share': round(max(counts.values()) / len(trades), 3),
            'exits': reasons}


def evaluate(trades, frac=0.6):
    """Train/holdout summaries at net50 (stressed, the acceptance basis) and net0 (model)."""
    _, meta = load()
    cut = split_t(meta, frac)
    tr = [x for x in trades if x['entry_t'] < cut]
    ho = [x for x in trades if x['entry_t'] >= cut]
    return {'train': summarize(tr), 'holdout': summarize(ho),
            'train_model': summarize(tr, 'usd0'), 'holdout_model': summarize(ho, 'usd0')}


def portfolio(trades, slots=3, start=1000.0, key='usd50'):
    """Capacity-limited replay in entry order. Trades are independent apart from slot use."""
    active, taken = [], []
    for x in sorted(trades, key=lambda r: r['entry_t']):
        active = [a for a in active if a['exit_t'] > x['entry_t']]
        if len(active) >= slots:
            continue
        active.append(x)
        taken.append(x)
    bal = peak = start
    mdd = 0.0
    for x in sorted(taken, key=lambda r: r['exit_t']):
        bal += x[key]
        peak = max(peak, bal)
        mdd = max(mdd, (peak - bal) / peak * 100)
    return {'taken': len(taken), 'final_balance': round(bal, 2), 'max_drawdown_pct': round(mdd, 2)}


def forward_net(s, i, seconds, notional=200.0, extra_bps=0.0):
    """Net % of buying at point i+1 and selling at the first point >= t_i + seconds (None if unavailable)."""
    ts = s['t']
    if i + 1 >= len(ts) or ts[i + 1] - ts[i] > MAX_ENTRY_LAG_MS:
        return None
    k = bisect.bisect_left(ts, ts[i] + seconds * 1000, i + 2)
    if k >= len(ts):
        return None
    f = entry_fill(s, i + 1, notional, extra_bps)
    if f is None:
        return None
    v = exit_value(s, k, f[0], extra_bps)
    return None if v is None else 100 * (v - notional - f[1]) / notional


if __name__ == '__main__':
    t0 = time.time()
    if '--build' in sys.argv or not os.path.exists(CACHE):
        print('cache', build_cache(), round(time.time() - t0, 1), 's')
    series, meta = load()
    print('loaded', meta, round(time.time() - t0, 1), 's')
