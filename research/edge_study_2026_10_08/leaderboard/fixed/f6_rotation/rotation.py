"""F6 cross-sectional rotation simulator built only on harness primitives (Past, entry_fill, exit_value,
interim_rug_risk, fee_bps, roundtrip_cost_pct, load, MAX_ENTRY_LAG_MS, STRESS_BPS, VANISH_HAIRCUT_PCT).

Mechanics (fixed before any result was seen):
- Rebalance ticks every `every_min` minutes from t0 + phase. At tick T a pool is ALIVE when its last point
  i with t_i <= T is at most 60 s old. Universe filter and factors are evaluated on Past(s, i) only.
- Candidates are ranked by score (single factor value, or weighted sum of cross-sectional percentile ranks,
  or a deterministic hashed uniform for random controls). Higher score = better.
- A held position is KEPT while its pool ranks inside the top K + buffer (and is alive and eligible);
  otherwise it is sold at the first point after T (point i+1): reason ROTATE (or MAXHOLD after
  max_hold_min). A held pool with no fresh point at T is kept (no price to trade on) unless its series has
  ended: then it closes at the last point, minus the harness vanish haircut when the dataset continued 10+
  minutes (VANISHED) - the same rule as H.simulate.
- Between ticks every point of a held pool is checked against the net stop (modeled net PnL, as H.simulate);
  the stop fills at the NEXT point after the trigger (gap risk real).
- Empty slots are filled in rank order; entry fills at point i+1 and is skipped (slot stays empty until the
  next tick) when t_{i+1} - t_i > 60 s. A pool that was just exited cannot be re-entered for `cool_ticks`.
- Every trade carries net0 (engine cost model) and net50 (+50 bps per leg), like H.simulate.
"""
import bisect, hashlib, math

NAN = float('nan')


def ok(x):
    return x is not None and x == x and not (isinstance(x, float) and math.isinf(x))


def unif(pair, T, salt):
    h = hashlib.blake2b(('%s|%s|%d' % (salt, pair, int(T))).encode(), digest_size=8).digest()
    return int.from_bytes(h, 'big') / 2 ** 64


# ------------------------------------------------------------------ universes (P, H) -> bool
def universe(min_liq=50_000, max_rt=4.0, fee_min=None, fee_max=None, rug_screen=True, notional=200.0, min_age=None):
    def f(P, H):
        liq = P('liq')
        if not (liq >= min_liq):
            return False
        if min_age is not None and not (P('age') >= min_age):
            return False
        fee = P.fee_bps()
        if fee_min is not None and fee < fee_min:
            return False
        if fee_max is not None and fee > fee_max:
            return False
        if rug_screen and H.interim_rug_risk(P):
            return False
        rt = P.rt_cost_pct(notional)
        return rt is not None and rt <= max_rt
    return f


# ------------------------------------------------------------------ factors (P) -> float (higher = better)
def _rel(a, b):
    return a / b - 1 if (ok(a) and ok(b) and b > 0) else NAN


def _hist_s(P):
    # LEADERBOARD MECHANICAL FIX (2026-10-08): the audited harness refuses P.static() on time-series columns (F4).
    # The original read P.static('t')[0] = the pair's FIRST observation time, which is past data; the identical
    # value is read here through the past-only view (back = current index -> point 0). Logic unchanged.
    return (P.t - P('t', back=P.history_len() - 1)) / 1000


def f_liq(P):
    return P('liq')


def f_neg_pc1h(P):
    v = P('pc1h')
    return -v if ok(v) else NAN


def f_neg_pc6h(P):
    v = P('pc6h')
    return -v if ok(v) else NAN


def f_pc5(P):
    return P('pc5')


def f_ret15(P):
    return _rel(P('price'), P.ago('price', 900)) if _hist_s(P) >= 900 else NAN


def f_neg_ret15(P):
    v = f_ret15(P)
    return -v if ok(v) else NAN


def f_vacc(P):
    v5, v1h = P('v5'), P('v1h')
    return v5 * 12 / v1h if (ok(v5) and ok(v1h) and v1h > 0) else NAN


def f_liqg15(P):
    return _rel(P('liq'), P.ago('liq', 900)) if _hist_s(P) >= 900 else NAN


def f_bshare5(P):
    b, s = P('b5'), P('s5')
    return b / (b + s) if (ok(b) and ok(s) and b + s > 0) else NAN


def f_uw5(P):
    return P('hp_uw5')


def f_neg_vol15(P):
    w = P.window('price', 900)
    lr = [math.log(b / a) for a, b in zip(w, w[1:]) if a > 0 and b > 0]
    if len(lr) < 5:
        return NAN
    return -math.sqrt(sum(x * x for x in lr))


def f_neg_turn1h(P):
    v, l = P('v1h'), P('liq')
    return -(v / l) if (ok(v) and ok(l) and l > 0) else NAN


FACTORS = {'liq': f_liq, 'neg_pc1h': f_neg_pc1h, 'neg_pc6h': f_neg_pc6h, 'pc5': f_pc5, 'ret15': f_ret15,
           'neg_ret15': f_neg_ret15, 'vacc': f_vacc, 'liqg15': f_liqg15, 'bshare5': f_bshare5, 'uw5': f_uw5,
           'neg_vol15': f_neg_vol15, 'neg_turn1h': f_neg_turn1h}


# ------------------------------------------------------------------ preparation (per universe / tick grid)
def prepare(H, univ, every_min=15, phase_s=60, factor_names=None, t_from=None, t_to=None):
    """[(T, [(pair, i, {factor: value})])] for every tick; only alive, eligible pools."""
    series, meta = H.load()
    names = list(factor_names or FACTORS)
    pool = [s for s in series.values() if s['dex'] == 'pumpswap' and s['quote_sol'] == 1]
    step = int(every_min * 60_000)
    t0, t1 = int(meta['t0']), int(meta['t1'])
    out = []
    for T in range(t0 + int(phase_s * 1000), t1, step):
        if t_from is not None and T < t_from:
            continue
        if t_to is not None and T >= t_to:
            break
        c = []
        for s in pool:
            ts = s['t']
            if ts[0] > T or ts[-1] < T - 60_000:
                continue
            i = bisect.bisect_right(ts, T) - 1
            if i < 0 or T - ts[i] > 60_000:
                continue
            P = H.Past(s, i)
            if not univ(P, H):
                continue
            c.append((s['pair'], i, {n: FACTORS[n](P) for n in names}))
        out.append((T, c))
    return out


# ------------------------------------------------------------------ market gates (cands) -> bool
def gate_breadth15(th=0.5, min_n=4):
    def g(cands):
        v = [x[2]['ret15'] for x in cands if ok(x[2].get('ret15'))]
        return len(v) >= min_n and sum(1 for a in v if a > 0) / len(v) >= th
    return g


def gate_median_pc1h(th=0.0, min_n=4):
    def g(cands):
        v = sorted(x[2]['neg_pc1h'] for x in cands if ok(x[2].get('neg_pc1h')))
        return len(v) >= min_n and -v[len(v) // 2] > th
    return g


def _scores(cands, spec, T):
    """spec: ('factor', name) | ('rank', [(name, weight), ...]) | ('random', salt)."""
    kind = spec[0]
    if kind == 'random':
        return {p: unif(p, T, spec[1]) for p, i, v in cands}
    if kind == 'factor':
        return {p: v[spec[1]] for p, i, v in cands if ok(v[spec[1]])}
    parts = spec[1]
    good = [(p, v) for p, i, v in cands if all(ok(v[n]) for n, w in parts)]
    if not good:
        return {}
    sc = {p: 0.0 for p, v in good}
    m = len(good)
    for n, w in parts:
        order = sorted(good, key=lambda x: x[1][n])
        for r, (p, v) in enumerate(order):
            sc[p] += w * ((r + 0.5) / m)
    return sc


# ------------------------------------------------------------------ mechanics
def _close(H, s, pos, trig_k, reason, t_end, notional_key='size'):
    ts = s['t']
    n = len(ts)
    if trig_k is not None and trig_k + 1 < n:
        e, flag, px = trig_k + 1, '', s['price'][trig_k + 1]
    else:
        e = n - 1
        if t_end - ts[e] > 600_000:
            flag, px = 'VANISHED', s['price'][e] * (1 - H.VANISH_HAIRCUT_PCT / 100)
        else:
            flag, px = 'END', s['price'][e]
    size = pos['size']
    v0 = H.exit_value(s, e, pos['qty'], 0.0, px)
    v50 = H.exit_value(s, e, pos['qty50'], H.STRESS_BPS, px)
    net0 = 100 * ((v0 or 0.0) - size - pos['netfee']) / size
    net50 = 100 * ((v50 or 0.0) - size - pos['netfee']) / size
    j = pos['entry_i']
    return {'pair': s['pair'], 'sym': s['sym'], 'entry_t': ts[j], 'exit_t': ts[e], 'reason': reason, 'flag': flag,
            'net0': net0, 'net50': net50, 'usd0': size * net0 / 100, 'usd50': size * net50 / 100, 'size': size,
            'mfe': pos['mfe'] if pos['mfe'] > -1e8 else 0.0, 'mae': pos['mae'] if pos['mae'] < 1e8 else 0.0,
            'hold_s': (ts[e] - ts[j]) / 1000, 'fee_bps': H.fee_bps(s, j), 'liq': s['liq'][j],
            'rt_cost': H.roundtrip_cost_pct(s, j, size), 'tag': pos['tag'],
            'decision_t': pos['decision_t'], 'exit_decision_t': pos.get('exit_decision_t', ts[e]),
            'score_rank': pos.get('rank')}


def run(H, prep, spec, *, K=3, buffer=0, stop=None, max_hold_min=None, notional=200.0, cool_ticks=1,
        entry_from=None, entry_to=None, tag='', min_cands=1, gate=None):
    """Run the rotation over prepared ticks. Returns trades (same dict shape as H.simulate).
    gate(cands) -> bool: market-level switch evaluated on the tick's eligible cross-section (past-only);
    when closed, every fresh held position is sold (reason GATE) and nothing is bought."""
    series, meta = H.load()
    t_end = meta['t1']
    held, trades, cool = {}, [], {}

    def monitor(pair, pos, T):
        s = series[pair]
        ts = s['t']
        kmax = bisect.bisect_right(ts, T) - 1
        for k in range(pos['last_k'] + 1, kmax + 1):
            v = H.exit_value(s, k, pos['qty'])
            if v is None:
                continue
            net = 100 * (v - pos['size'] - pos['netfee']) / pos['size']
            pos['mfe'] = max(pos['mfe'], net)
            pos['mae'] = min(pos['mae'], net)
            if stop is not None and net <= stop:
                pos['exit_decision_t'] = ts[k]
                return k
        pos['last_k'] = max(pos['last_k'], kmax)
        return None

    for ti, (T, cands) in enumerate(prep):
        # 1. stops between ticks
        for pair in list(held):
            k = monitor(pair, held[pair], T)
            if k is not None:
                trades.append(_close(H, series[pair], held.pop(pair), k, 'STOP', t_end))
                cool[pair] = ti + cool_ticks
        # 2. rank
        gate_open = gate is None or gate(cands)
        sc = _scores(cands, spec, T) if (len(cands) >= min_cands and gate_open) else {}
        order = sorted(sc, key=lambda p: -sc[p])
        rank = {p: r for r, p in enumerate(order)}
        idx = {p: i for p, i, v in cands}
        # 3. exits
        for pair in list(held):
            pos = held[pair]
            s = series[pair]
            ts = s['t']
            i = bisect.bisect_right(ts, T) - 1
            fresh = i >= 0 and T - ts[i] <= 60_000
            if not fresh:
                if ts[-1] <= T:  # series ended: harness vanish rule
                    pos['exit_decision_t'] = T
                    trades.append(_close(H, s, held.pop(pair), None, 'OPEN_AT_END', t_end))
                continue  # stale but continues later: keep holding
            too_old = max_hold_min is not None and T - pos['decision_t'] >= max_hold_min * 60_000
            if pair in rank and rank[pair] < K + buffer and not too_old:
                continue
            pos['exit_decision_t'] = T
            why = 'GATE' if not gate_open else ('MAXHOLD' if (too_old and pair in rank and rank[pair] < K + buffer) else 'ROTATE')
            trades.append(_close(H, s, held.pop(pair), i, why, t_end))
            cool[pair] = ti + cool_ticks
        # 4. entries
        if (entry_from is not None and T < entry_from) or (entry_to is not None and T >= entry_to):
            continue
        slots = K - len(held)
        for pair in order:
            if slots <= 0:
                break
            if pair in held or cool.get(pair, -1) > ti:
                continue
            if rank[pair] >= K:  # enter only from the top K; keep while inside top K + buffer
                break
            slots -= 1
            s = series[pair]
            ts = s['t']
            i = idx[pair]
            j = i + 1
            if j >= len(ts) or ts[j] - ts[i] > H.MAX_ENTRY_LAG_MS:
                continue
            f0 = H.entry_fill(s, j, notional)
            f5 = H.entry_fill(s, j, notional, H.STRESS_BPS)
            if f0 is None or f5 is None:
                continue
            held[pair] = {'entry_i': j, 'qty': f0[0], 'qty50': f5[0], 'netfee': f0[1], 'size': notional,
                          'last_k': j, 'mfe': -1e9, 'mae': 1e9, 'tag': tag, 'decision_t': T, 'rank': rank[pair]}
    # end of data: manage remaining positions with stops, then close at the end
    for pair in list(held):
        k = monitor(pair, held[pair], t_end + 1)
        if k is not None:
            trades.append(_close(H, series[pair], held.pop(pair), k, 'STOP', t_end))
        else:
            trades.append(_close(H, series[pair], held.pop(pair), None, 'OPEN_AT_END', t_end))
    trades.sort(key=lambda x: x['entry_t'])
    return trades


def matched_random(H, prep, trades, *, salt='m1', stop=None, notional=200.0, tag='matched_random'):
    """Selection control: for every strategy trade, buy a uniformly drawn (hashed) pool from the SAME
    eligible universe at the SAME decision tick, and sell it at the first point after the strategy trade's
    exit decision time (own net stop applied the same way). Same count, timing and holding horizon."""
    series, meta = H.load()
    t_end = meta['t1']
    by_T = {T: c for T, c in prep}
    out = []
    for n_, x in enumerate(trades):
        cands = by_T.get(x['decision_t'])
        if not cands:
            continue
        order = sorted(cands, key=lambda c: unif(c[0], x['decision_t'] + n_, salt))
        pair, i, _ = order[0]
        s = series[pair]
        ts = s['t']
        j = i + 1
        if j >= len(ts) or ts[j] - ts[i] > H.MAX_ENTRY_LAG_MS:
            continue
        f0 = H.entry_fill(s, j, notional)
        f5 = H.entry_fill(s, j, notional, H.STRESS_BPS)
        if f0 is None or f5 is None:
            continue
        pos = {'entry_i': j, 'qty': f0[0], 'qty50': f5[0], 'netfee': f0[1], 'size': notional, 'mfe': -1e9, 'mae': 1e9,
               'tag': tag, 'decision_t': x['decision_t']}
        end_T = x['exit_decision_t']
        trig, reason = None, None
        kmax = bisect.bisect_right(ts, end_T) - 1
        for k in range(j + 1, kmax + 1):
            v = H.exit_value(s, k, pos['qty'])
            if v is None:
                continue
            net = 100 * (v - notional - pos['netfee']) / notional
            pos['mfe'] = max(pos['mfe'], net)
            pos['mae'] = min(pos['mae'], net)
            if stop is not None and net <= stop:
                trig, reason = k, 'STOP'
                break
        if trig is None:
            if ts[-1] <= end_T:
                trig, reason = None, 'OPEN_AT_END'
            else:
                trig, reason = max(kmax, j), ('ROTATE' if x['reason'] != 'OPEN_AT_END' else 'OPEN_AT_END')
        pos['exit_decision_t'] = end_T
        out.append(_close(H, s, pos, trig, reason, t_end))
    out.sort(key=lambda r: r['entry_t'])
    return out
