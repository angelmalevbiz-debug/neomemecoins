"""AUDITED shared PAPER backtest harness (v2). Same public API and names as ../harness.py (v1).

Audit fixes relative to v1 (see audit/REPORT.md for the evidence and measured impact):
 F1 FILLS AT THE NEXT DEXSCREENER REFRESH. The engine polls every ~5 s but DexScreener refreshes a pair's data only
    every ~30 s (90% of kept points repeat the previous price exactly; 'upd' is the engine batch time, not a
    DexScreener update time). v1 filled entries at i+1 and exits at k+1, i.e. at the SAME stale price as the decision in
    ~94% of fills (zero effective latency, gap-through and single-print spikes monetized). v2 fills at the first later
    point whose price differs from the decision point's price, within 60 s (else at i+1 / k+1 as before). Against the
    on-chain tape this cut the mean absolute fill error from 188 to 114 bps and removed most context-dependent bias.
    FILL_RULE = 'next_point' restores v1 fills for sensitivity checks.
 F2 FEED-GAP CLOSES. A held pair that is absent from the feed for > FEED_GAP_MS (10 min) and later returns is closed
    and valued at the LOWER of the pre-gap and return prices (GAP_VALUATION='min'; flag GAP, reason FEED_GAP); exit_t
    is the return time, when that value becomes known (no realization before the information exists). v1 held
    through the gap (median 53 min, p90 111 min) and exited at the reappearance price: hold limits
    overshot by up to 87 min, stops/targets could not act during the gap, and the exit price carried the re-listing
    tails (p90 +25% vs +7.6% for continuously observed controls). 'min' keeps drains that happen during a gap and does
    not credit re-listing pumps (conservative). Pairs that never return keep the v1 VANISHED rule (last price minus
    VANISH_HAIRCUT_PCT). FEED_GAP_MS = None restores v1; GAP_VALUATION 'pre'/'return' for sensitivity.
 F3 forward_net NO LONGER DROPS VANISHED PAIRS (survivorship). v1 returned None when the pair had no point after the
    horizon although the dataset continued (13.5% of 60-min samples); those were mostly drained/delisted pools. v2
    values them at the last observed price minus VANISH_HAIRCUT_PCT, applies the F1 fill rule and the F2 gap rule.
    Returns None only when the pair is still in the feed at the dataset end and the horizon lies beyond it
    (right-censored), or inputs are missing.
 F4 Past.static() refuses time-series columns (v1 returned the whole column, future included).
 F5 TIME-ORDERED SIMULATION. v1 ran pair after pair, so any state a signal/exit_fn keeps across pairs saw other
    pairs' future. v2 interleaves all pairs and invokes every callback (signal, size_fn, exit_fn) in global
    non-decreasing time order. Per-pair results are unchanged for stateless callbacks.
 F6 A pool reported with liquidity exactly 0 (drained; all such points are terminal) is worth 0 on exit (v1 applied a
    20% impact cap and credited 80% of the price).
 F7 Statistics: true median; trades_per_hour over the evaluated window; pair-level cluster bootstrap CI added
    ('ci95_mean_usd_pair' - the (pair, hour) CI is close to iid and anti-conservative); censored/vanished/gap counts;
    portfolio() is cash-limited (v1 let the balance go negative and reported drawdowns above 100%).
 F8 load() never rebuilds the shared cache implicitly (build_cache writes only to an explicit path).
Unchanged (verified): cost model == backend/paper_market_feasibility.modeled_roundtrip (0 differences on 135k rows),
Past.__call__/ago/window bounds, ticker index, stressed net50 path (qty50 at the same fill), cooldown/restart logic,
train/holdout split by entry time (3.2% of train trades exit after the cut - not embargoed).
Known limits that code cannot fix: DexScreener prices are last-trade prints (+-50 bps bid/ask bounce, ~20 s lag);
interim_rug_risk thresholds were chosen after seeing rugs that drained in the holdout window (see rug_risk_train_only);
22.8 h of data -> minimum detectable effect ~2-3.5 %/trade.
"""
import array, bisect, hashlib, heapq, math, os, pickle, random, sqlite3, sys, time

DEEP = (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r''
DB = os.path.join(DEEP, 'obs.sqlite3')
CACHE = os.path.join(DEEP, 'series.pkl')
sys.path.insert(0, (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + '/../../backend' + '')
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
STATIC_KEYS = ('pair', 'mint', 'dex', 'quote_sol', 'sym', 'created')
SRC_BITS = {'boosted': 1, 'boosted-latest': 2, 'latest': 4, 'gecko-new-pools': 8, 'pumpswap-address-catalog': 16}
VANISH_HAIRCUT_PCT = 10.0     # pair never returns to the feed (mostly drained pools)
GAP_HAIRCUT_PCT = 0.0         # F2: pair absent > FEED_GAP_MS while held but returns later (returns median +0.5%,
                              # matched continuous controls 0.0%; liq>=250k +1.4%, no drains) -> no extra haircut
GAP_VALUATION = 'min'         # F2: 'min' (conservative: lower of pre-gap / return price), 'pre', or 'return' (v1-like)
MAX_ENTRY_LAG_MS = 60_000
MAX_FILL_LAG_MS = 60_000      # F1: how far ahead the next DexScreener refresh is searched
FILL_RULE = 'next_refresh'    # F1: 'next_refresh' (v2) or 'next_point' (v1)
FEED_GAP_MS = 600_000         # F2: absence that closes a held position; None disables (v1)
TIME_ORDERED = True           # F5: global time-ordered callbacks; False = v1 pair-by-pair order
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


def build_cache(path):
    """Rebuilds a series cache from obs.sqlite3 into an EXPLICIT path (never the shared series.pkl implicitly).

    Note (audit): column 'upd' is the engine's batch timestamp (37,771 distinct values over 3.1M rows), not the
    DexScreener per-pair update time, so the 'upd increases' rule keeps one row per engine scan (~5 s) and ~90% of the
    kept points repeat the previous price. Fills therefore use the next refresh (F1)."""
    con = sqlite3.connect('file:%s?mode=ro' % DB, uri=True)
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
    with open(path, 'wb') as fh:
        pickle.dump({'meta': meta, 'series': data}, fh, protocol=pickle.HIGHEST_PROTOCOL)
    return meta


_LOADED = None


def load():
    """Returns (series_by_pair, meta) from the shared cache (read-only)."""
    global _LOADED
    if _LOADED is None:
        if not os.path.exists(CACHE):
            raise FileNotFoundError('series cache missing: %s (v2 never rebuilds it implicitly)' % CACHE)
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
    """Net USD proceeds of selling qty at point k (optionally at an explicit price). 0 when the pool is drained (F6)."""
    p = s['price'][k] if price is None else price
    liq, su = s['liq'][k], sol_usd(s, k)
    if not (p > 0):
        return None
    if liq == 0:
        return 0.0
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


def fill_index(s, i, max_lag_ms=None):
    """F1: index of the point at which an order decided at point i fills, or None.

    'next_refresh': the first later point (within max_lag_ms) whose price differs from the price at i - the next
    DexScreener refresh; if the price does not change within the window, the next point (quiet market).
    'next_point' (v1): i + 1. Both require the next point to exist within MAX_ENTRY_LAG_MS."""
    ts, pr = s['t'], s['price']
    n = len(ts)
    lag = MAX_FILL_LAG_MS if max_lag_ms is None else max_lag_ms
    if i + 1 >= n or ts[i + 1] - ts[i] > MAX_ENTRY_LAG_MS:
        return None
    if FILL_RULE == 'next_point':
        return i + 1
    p0, t0 = pr[i], ts[i]
    m = i + 1
    while m < n and ts[m] - t0 <= lag:
        if pr[m] != p0:
            return m
        m += 1
    return i + 1


_GAPS = {}


def _gap_starts(s):
    """Sorted indices m where the feed was absent for more than FEED_GAP_MS after point m (cached per series)."""
    key = (id(s['t']), FEED_GAP_MS)
    g = _GAPS.get(key)
    if g is None or g[0] is not s['t']:
        ts = s['t']
        lst = [m - 1 for m in range(1, len(ts)) if ts[m] - ts[m - 1] > FEED_GAP_MS]
        g = (ts, lst)
        _GAPS[key] = g
    return g[1]


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
        """Values of f over (now - seconds, now]. Note: ~90% of consecutive points repeat the previous price."""
        ts = self.s['t']
        lo = bisect.bisect_right(ts, ts[self.i] - seconds * 1000, 0, self.i + 1)
        return self.s[f][lo:self.i + 1]

    def static(self, f):
        """Static pair attributes only (pair, mint, dex, quote_sol, sym, created). F4: time series raise LookAhead."""
        v = self.s[f]
        if isinstance(v, array.array):
            raise LookAhead('static(%r) is a time series; use P(%r) / P.ago / P.window' % (f, f))
        return v

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
    """INTERIM past-only screen for the serial fake-market-cap rug family found on 2026-10-08 (unchanged from v1).

    AUDIT NOTE: the $20M / 2% thresholds were chosen after seeing all 10 drained pools, 7 of which drained inside the
    holdout window (DAWS: $22.9M, 1.89% sits just inside them). Holdout results that rely on this screen are not fully
    out-of-sample; use rug_risk_train_only(P) as the sensitivity check.
    """
    mc, liq, age = P('mcap'), P('liq'), P('age')
    young = not (age >= 14 * 1440)
    if mc >= 20e6 and liq > 0 and liq / mc < 0.02 and young:
        return True
    if young and other_pairs_same_ticker_before(P) >= 1:
        return True
    return False


def rug_risk_train_only(P):
    """Sensitivity variant built only from the 3 rugs that drained in the train window (IOF x2, SARP: market cap
    $242-853M, liquidity/market cap 0.31-0.58%, all reusing a ticker): mcap >= $200M & liq/mcap < 1% & young, or a
    young token reusing a ticker."""
    mc, liq, age = P('mcap'), P('liq'), P('age')
    young = not (age >= 14 * 1440)
    if mc >= 200e6 and liq > 0 and liq / mc < 0.01 and young:
        return True
    if young and other_pairs_same_ticker_before(P) >= 1:
        return True
    return False


def hashed_coin(pair, t, prob, salt='r'):
    """Deterministic pseudo-random draw for random-entry baselines."""
    h = hashlib.blake2b(('%s|%s|%d' % (salt, pair, int(t))).encode(), digest_size=8).digest()
    return int.from_bytes(h, 'big') / 2 ** 64 < prob


# ---------------------------------------------------------------- simulation
def _gap_value_index(s, a):
    """F2 valuation point for a position whose pair left the feed after point a and returned at a + 1.
    'min' (default, conservative): the lower of the pre-gap and return prices (a drain during the gap is kept, a
    re-listing pump is not credited); 'pre': pre-gap price; 'return': return price (v1-like)."""
    if GAP_VALUATION == 'pre' or a + 1 >= len(s['t']):
        return a
    if GAP_VALUATION == 'return':
        return a + 1
    pa, pb = s['price'][a], s['price'][a + 1]
    if s['liq'][a + 1] == 0 or (pb > 0 and pb < pa):
        return a + 1
    return a


def _close_px(s, e, t_end):
    """(flag, price) for a position that could not be filled after its trigger / at the end of its series."""
    if t_end - s['t'][e] > 600_000:
        return 'VANISHED', s['price'][e] * (1 - VANISH_HAIRCUT_PCT / 100)
    return 'END', s['price'][e]


def _run_pair(pair, s, signal, out, t_end, stop, tp, trail_arm, trail, hold_min, exit_fn, notional, size_fn,
              cooldown_s, t_from, t_to, tag):
    """Per-pair state machine. Yields the time of every user callback BEFORE making it (time-ordered scheduler)."""
    ts, n = s['t'], len(s['t'])
    gap_on = FEED_GAP_MS is not None
    i, next_ok = 0, -1.0
    while i < n - 1:
        ti = ts[i]
        if ti < next_ok or (t_from is not None and ti < t_from) or (t_to is not None and ti >= t_to):
            i += 1
            continue
        yield ti
        P = Past(s, i)
        if not signal(P):
            i += 1
            continue
        j = fill_index(s, i)
        if j is None:
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
        trig, k, gap_close = None, j, False
        for k in range(j + 1, n):
            if gap_on and ts[k] - ts[k - 1] > FEED_GAP_MS:
                gap_close = True
                k -= 1
                break
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
                yield ts[k]
                trig = exit_fn(Past(s, k), pos)
            if trig:
                break
        trig_k = k
        ev = None  # valuation point when it differs from the exit-time point e
        if gap_close:
            e = k
            trig = 'FEED_GAP'
            ev = _gap_value_index(s, e)
            flag, px = 'GAP', s['price'][ev] * (1 - GAP_HAIRCUT_PCT / 100)
        elif trig:
            e = fill_index(s, k)
            if e is not None:
                flag, px = '', s['price'][e]
            elif k + 1 < n and gap_on and ts[k + 1] - ts[k] > FEED_GAP_MS:
                e = k  # the pair left the feed right after the trigger and came back later
                ev = _gap_value_index(s, e)
                flag, px = 'GAP', s['price'][ev] * (1 - GAP_HAIRCUT_PCT / 100)
            elif k + 1 < n:
                e = k + 1
                flag, px = '', s['price'][e]
            else:
                e = k
                flag, px = _close_px(s, e, t_end)
        else:
            e = n - 1
            trig = 'OPEN_AT_END'
            flag, px = _close_px(s, e, t_end)
        ev = e if ev is None else ev
        # a GAP close is only known (and its value only determined) when the pair returns to the feed
        t_exit = ts[e + 1] if (flag == 'GAP' and GAP_VALUATION != 'pre' and e + 1 < n) else ts[e]
        v0 = exit_value(s, ev, qty, 0.0, px)
        v50 = exit_value(s, ev, qty50, STRESS_BPS, px)
        net0 = 100 * ((v0 or 0.0) - size - netfee) / size
        net50 = 100 * ((v50 or 0.0) - size - netfee) / size
        out.append({'pair': pair, 'sym': s['sym'], 'entry_t': ts[j], 'exit_t': t_exit, 'reason': trig,
                    'flag': flag, 'net0': net0, 'net50': net50, 'usd0': size * net0 / 100,
                    'usd50': size * net50 / 100, 'size': size,
                    'mfe': mfe if mfe > -1e8 else 0.0, 'mae': mae if mae < 1e8 else 0.0,
                    'hold_s': (t_exit - ts[j]) / 1000, 'fee_bps': fee_bps(s, j), 'liq': s['liq'][j],
                    'rt_cost': roundtrip_cost_pct(s, j, size), 'tag': tag,
                    'decision_t': ti, 'trigger_t': ts[trig_k], 'entry_px': s['price'][j], 'exit_px': px})
        next_ok = t_exit + cooldown_s * 1000
        i = e + 1


def simulate(signal, *, stop=-5.0, tp=10.0, trail_arm=None, trail=None, hold_min=60.0, exit_fn=None,
             notional=200.0, size_fn=None, cooldown_s=300, dexes=('pumpswap',), pairs=None,
             t_from=None, t_to=None, tag=''):
    """Run one configuration over every pair. Returns a list of trade dicts sorted by entry time.

    signal(P) -> truthy to enter (P is a Past view at the decision point; it cannot see the future).
    exit_fn(P, pos) -> reason string or None; P is a Past view at the evaluated point.
    size_fn(P) -> notional USD (defaults to `notional`).
    tp=None disables the fixed target; trail_arm/trail enable a net trailing stop.
    v2: fills at the next DexScreener refresh (F1), feed-gap closes (F2), callbacks in global time order (F5).
    Extra trade keys: decision_t, trigger_t, entry_px, exit_px.
    """
    series, meta = load()
    t_end = meta['t1']
    out = []
    gens = []
    for pair, s in series.items():
        if pairs is not None and pair not in pairs:
            continue
        if dexes is not None and s['dex'] not in dexes:
            continue
        if s['quote_sol'] != 1:
            continue
        gens.append(_run_pair(pair, s, signal, out, t_end, stop, tp, trail_arm, trail, hold_min, exit_fn, notional,
                              size_fn, cooldown_s, t_from, t_to, tag))
    if TIME_ORDERED:
        heap = []
        for seq, g in enumerate(gens):
            t = next(g, None)
            if t is not None:
                heap.append((t, seq, g))
        heapq.heapify(heap)
        while heap:
            _, seq, g = heap[0]
            t = next(g, None)
            if t is None:
                heapq.heappop(heap)
            else:
                heapq.heapreplace(heap, (t, seq, g))
    else:
        for g in gens:
            for _ in g:
                pass
    out.sort(key=lambda x: (x['entry_t'], x['pair']))
    return out


# ---------------------------------------------------------------- statistics
def _pf(xs):
    g = sum(x for x in xs if x > 0)
    l = -sum(x for x in xs if x < 0)
    return round(g / l, 3) if l > 0 else (float('inf') if g > 0 else None)


def _boot(groups, reps, seed):
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
    return round(means[int(reps * .025)], 3), round(means[min(reps - 1, int(reps * .975))], 3), len(groups)


def cluster_ci(trades, key='usd50', reps=2000, seed=7):
    """95% CI of the mean per-trade value, bootstrap over (pair, hour) clusters (v1 definition)."""
    if not trades:
        return None, None, 0
    cl = {}
    for x in trades:
        cl.setdefault((x['pair'], int(x['entry_t'] // 3_600_000)), []).append(x[key])
    return _boot(list(cl.values()), reps, seed)


def cluster_ci_pair(trades, key='usd50', reps=2000, seed=7):
    """95% CI of the mean per-trade value, bootstrap over pairs (F7: conservative; trades on one token are correlated
    across hours, which the (pair, hour) clusters ignore)."""
    if not trades:
        return None, None, 0
    cl = {}
    for x in trades:
        cl.setdefault(x['pair'], []).append(x[key])
    return _boot(list(cl.values()), reps, seed)


def _median(xs):
    s = sorted(xs)
    n = len(s)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2


def summarize(trades, key='usd50', span_h=None):
    """span_h: length of the evaluated window in hours (trades_per_hour); defaults to the entry-time span."""
    if not trades:
        return {'n': 0}
    xs = [x[key] for x in trades]
    pct = [x['net50' if key == 'usd50' else 'net0'] for x in trades]
    lo, hi, ncl = cluster_ci(trades, key)
    plo, phi, npr = cluster_ci_pair(trades, key)
    if span_h is None:
        span_h = max(1.0, (max(x['entry_t'] for x in trades) - min(x['entry_t'] for x in trades)) / 3.6e6)
    reasons = {}
    for x in trades:
        label = x['reason'] + (('/' + x['flag']) if x['flag'] else '')
        reasons[label] = reasons.get(label, 0) + 1
    counts = {}
    for x in trades:
        counts[x['pair']] = counts.get(x['pair'], 0) + 1
    return {'n': len(xs), 'pairs': len(counts), 'clusters': ncl,
            'win_rate': round(100 * sum(1 for v in xs if v > 0) / len(xs), 1),
            'mean_usd': round(sum(xs) / len(xs), 3), 'mean_pct': round(sum(pct) / len(pct), 3),
            'median_pct': round(_median(pct), 3), 'sum_usd': round(sum(xs), 2), 'pf': _pf(xs),
            'ci95_mean_usd': [lo, hi], 'ci95_mean_usd_pair': [plo, phi],
            'trades_per_hour': round(len(xs) / max(span_h, 1e-9), 2),
            'avg_hold_min': round(sum(x['hold_s'] for x in trades) / len(trades) / 60, 1),
            'top_pair_share': round(max(counts.values()) / len(trades), 3),
            'censored_end': sum(1 for x in trades if x['flag'] == 'END'),
            'vanished': sum(1 for x in trades if x['flag'] == 'VANISHED'),
            'gap_closed': sum(1 for x in trades if x['flag'] == 'GAP'),
            'exits': reasons}


def evaluate(trades, frac=0.6):
    """Train/holdout summaries at net50 (stressed, the acceptance basis) and net0 (model)."""
    _, meta = load()
    cut = split_t(meta, frac)
    tr = [x for x in trades if x['entry_t'] < cut]
    ho = [x for x in trades if x['entry_t'] >= cut]
    sh_tr, sh_ho = (cut - meta['t0']) / 3.6e6, (meta['t1'] - cut) / 3.6e6
    return {'train': summarize(tr, span_h=sh_tr), 'holdout': summarize(ho, span_h=sh_ho),
            'train_model': summarize(tr, 'usd0', span_h=sh_tr), 'holdout_model': summarize(ho, 'usd0', span_h=sh_ho)}


def portfolio(trades, slots=3, start=1000.0, key='usd50'):
    """Capacity- and cash-limited replay in entry order (F7: v1 let the balance go negative, drawdown > 100%).

    A trade is taken when a slot is free and realized cash minus capital committed to open positions covers its size;
    PnL is realized at exit. Trades are otherwise independent (sizes are not rescaled)."""
    active, taken = [], []
    bal = peak = start
    mdd = 0.0
    no_cash = 0

    def realize(upto):
        nonlocal bal, peak, mdd, active
        done = sorted((a for a in active if a['exit_t'] <= upto), key=lambda r: r['exit_t'])
        for a in done:
            bal += a[key]
            peak = max(peak, bal)
            mdd = max(mdd, (peak - bal) / peak * 100 if peak > 0 else 0.0)
        active = [a for a in active if a['exit_t'] > upto]

    for x in sorted(trades, key=lambda r: r['entry_t']):
        realize(x['entry_t'])
        if len(active) >= slots:
            continue
        if bal - sum(a.get('size', 0.0) for a in active) < x.get('size', 0.0):
            no_cash += 1
            continue
        active.append(x)
        taken.append(x)
    realize(float('inf'))
    return {'taken': len(taken), 'final_balance': round(bal, 2), 'max_drawdown_pct': round(mdd, 2),
            'skipped_no_cash': no_cash}


def forward_net(s, i, seconds, notional=200.0, extra_bps=0.0):
    """Net % of buying at the fill point after i and selling at the fill point after the first point >= t_i + seconds.

    F3: a pair that leaves the feed for good (last point > 10 min before the dataset end and before the horizon) is
    valued at its last observed price minus VANISH_HAIRCUT_PCT instead of being dropped; a feed gap > FEED_GAP_MS
    before the horizon closes at the pre-gap price minus GAP_HAIRCUT_PCT (as in simulate). None only when the pair is
    still in the feed at the dataset end and the horizon lies beyond it (right-censored), or inputs are missing."""
    ts = s['t']
    n = len(ts)
    j = fill_index(s, i)
    if j is None:
        return None
    f = entry_fill(s, j, notional, extra_bps)
    if f is None:
        return None
    target = ts[i] + seconds * 1000
    _, meta = load()
    k = bisect.bisect_left(ts, target, j + 1)
    close_at, cut = None, 0.0
    if FEED_GAP_MS is not None:
        gs = _gap_starts(s)
        a = bisect.bisect_left(gs, j)
        if a < len(gs) and gs[a] < k:  # the pair left the feed (gap > FEED_GAP_MS) before the horizon, came back
            close_at, cut = _gap_value_index(s, gs[a]), GAP_HAIRCUT_PCT
    if close_at is None and k >= n:
        if meta['t1'] - ts[n - 1] > 600_000:
            close_at, cut = n - 1, VANISH_HAIRCUT_PCT   # vanished for good
        elif meta['t1'] < target:
            return None                                 # right-censored by the dataset end
        else:
            close_at, cut = n - 1, 0.0                  # dataset ended within 10 min of the last point
    if close_at is not None:
        v = exit_value(s, close_at, f[0], extra_bps, s['price'][close_at] * (1 - cut / 100))
    else:
        e = k if FILL_RULE == 'next_point' else (fill_index(s, k) or k)  # v1 sold at k itself
        v = exit_value(s, e, f[0], extra_bps)
    return None if v is None else 100 * (v - notional - f[1]) / notional


if __name__ == '__main__':
    t0 = time.time()
    series, meta = load()
    print('loaded', meta, round(time.time() - t0, 1), 's')
