"""hfsim: multi-slot PAPER book simulator on harness_final primitives (PAPER research only).

Fill / valuation rules are copied from harness_final._run_pair (audited v2 + I2 calibration):
  * decision at scan point i; entry fills at H.fill_index(s, i) (next DexScreener refresh within 60 s, else i+1);
  * exits are evaluated on every later point; the exit fills at H.fill_index(s, trigger) with the harness fallbacks;
  * a feed absence > 10 min while held closes the trade (FEED_GAP, valued at the lower of pre-gap / return price,
    known only at the return time); a pair that never returns is VANISHED (last price -10 %);
  * net0 = engine model; netcal = model + CALIB_V1 per leg; net50 = model + CALIB_V1 + 50 bps/leg + 200 bps on STOP
    exits (+100 on TRAIL) -- identical arithmetic to simulate().
Book: `slots` concurrent positions, one position per pool, a slot is busy from the decision time to the exit time
(t_exit as in the harness), random entries by H.hashed_coin(pair, t, p, salt) on universe-eligible scan points,
optional per-pool cooldown, POOL_LOSS_MEMORY (2 consecutive losing closes -> pool blocked 6 h after the last loss),
optional cash limit (start balance; a trade needs free cash >= notional) and optional UTC-day loss cap.
"""
import bisect, heapq, math, os, pickle, sys
sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hfc_common as C

H = C.harness()
SERIES, META = H.load()
E = pickle.load(open(os.path.join(C.HERE, 'elig.pkl'), 'rb'))
PAIRS = E['pairs']
SER = [SERIES[p] for p in PAIRS]
CUT = E['cut']
T0, T1 = META['t0'], META['t1']
B = E['bitnames']
G1, GE, HEAT = B['G1'], B['GE'], B['HEAT']

UNIVERSES = {
    'a': ('fee<=50 & liq>=250k', lambda f, l: f <= 50 and l >= 250_000),
    'b': ('fee<=95 & liq>=100k', lambda f, l: f <= 95 and l >= 100_000),
    'c': ('all PumpSwap liq>=50k', lambda f, l: l >= 50_000),
}

_STREAMS = {}


def stream(univ, heat=True, guard='eng'):
    key = (univ, heat, guard)
    if key in _STREAMS:
        return _STREAMS[key]
    uf = UNIVERSES[univ][1]
    mask = (G1 | GE) if guard == 'eng' else G1
    if heat:
        mask |= HEAT
    rows = []
    et, ep, ei, ef, el, eb = E['t'], E['pid'], E['i'], E['fee'], E['liq'], E['bits']
    for k in range(len(et)):
        if eb[k] & mask:
            continue
        if not uf(ef[k], el[k]):
            continue
        rows.append((et[k], ep[k], ei[k]))
    rows.sort()
    st = [r[0] for r in rows]
    sp = [r[1] for r in rows]
    si = [r[2] for r in rows]
    _STREAMS[key] = (st, sp, si)
    return _STREAMS[key]


# ------------------------------------------------------------------ trade paths
_PATH = {}


def path(pid, i, spec, size_for_net=None):
    """Exit path of a position decided at point i: (j, e, ev, trig, flag, px, t_exit, trig_t) or None.
    spec = ('time', hold_s) | ('net', X, cap_s) | ('move', X, cap_s). For 'net' the trigger uses the model net at
    size_for_net (harness semantics: net includes the round-trip cost)."""
    key = (pid, i, spec, size_for_net if spec[0] == 'net' else None)
    r = _PATH.get(key, 0)
    if r != 0:
        return r
    s = SER[pid]
    ts, px, lq = s['t'], s['price'], s['liq']
    n = len(ts)
    j = H.fill_index(s, i)
    if j is None or not (px[j] > 0 and lq[j] > 0 and H.sol_usd(s, j) > 0):
        _PATH[key] = None
        return None
    kind = spec[0]
    hold_ms = spec[1] * 1000 if kind == 'time' else spec[2] * 1000
    X = None if kind == 'time' else spec[1]
    if kind == 'net':
        qty, netfee = H.entry_fill(s, j, size_for_net)
    p0 = px[j]
    trig, gap_close, k = None, False, j
    gap_ms = H.FEED_GAP_MS
    for k in range(j + 1, n):
        if gap_ms is not None and ts[k] - ts[k - 1] > gap_ms:
            gap_close = True
            k -= 1
            break
        p = px[k]
        if not (p > 0):
            continue
        if kind == 'net':
            v = H.exit_value(s, k, qty)
            net = 100 * (v - size_for_net - netfee) / size_for_net
            if net <= -X:
                trig = 'STOP'
            elif net >= X:
                trig = 'TP'
        elif kind == 'move':
            if lq[k] == 0:
                trig = 'STOP'
            else:
                mv = 100 * (p / p0 - 1)
                if mv <= -X:
                    trig = 'STOP'
                elif mv >= X:
                    trig = 'TP'
        if trig is None and ts[k] - ts[j] >= hold_ms:
            trig = 'HOLD'
        if trig:
            break
    trig_k = k
    ev = None
    if gap_close:
        e = k
        trig = 'FEED_GAP'
        ev = H._gap_value_index(s, e)
        flag, xp = 'GAP', px[ev] * (1 - H.GAP_HAIRCUT_PCT / 100)
    elif trig:
        e = H.fill_index(s, k)
        if e is not None:
            flag, xp = '', px[e]
        elif k + 1 < n and gap_ms is not None and ts[k + 1] - ts[k] > gap_ms:
            e = k
            ev = H._gap_value_index(s, e)
            flag, xp = 'GAP', px[ev] * (1 - H.GAP_HAIRCUT_PCT / 100)
        elif k + 1 < n:
            e = k + 1
            flag, xp = '', px[e]
        else:
            e = k
            flag, xp = H._close_px(s, e, T1)
    else:
        e = n - 1
        trig = 'OPEN_AT_END'
        flag, xp = H._close_px(s, e, T1)
    ev = e if ev is None else ev
    t_exit = ts[e + 1] if (flag == 'GAP' and H.GAP_VALUATION != 'pre' and e + 1 < n) else ts[e]
    r = (j, e, ev, trig, flag, xp, t_exit, ts[trig_k])
    _PATH[key] = r
    return r


_PRICE = {}


def price(pid, i, spec, size):
    """Trade dict at notional `size` (net0 / netcal / net50 and $), or None."""
    key = (pid, i, spec, size)
    r = _PRICE.get(key, 0)
    if r != 0:
        return r
    pth = path(pid, i, spec, size)
    if pth is None:
        _PRICE[key] = None
        return None
    j, e, ev, trig, flag, xp, t_exit, trig_t = pth
    s = SER[pid]
    fee_j, liq_j = H.fee_bps(s, j), s['liq'][j]
    cx = H.calib_extra_bps_per_leg(fee_j, liq_j, size)
    f0 = H.entry_fill(s, j, size)
    f50 = H.entry_fill(s, j, size, H.STRESS_BPS + cx)
    fcal = H.entry_fill(s, j, size, cx)
    if f0 is None or f50 is None or fcal is None:
        _PRICE[key] = None
        return None
    netfee = f0[1]
    rx = H.calib_exit_reason_extra_bps(trig)
    v0 = H.exit_value(s, ev, f0[0], 0.0, xp)
    v50 = H.exit_value(s, ev, f50[0], H.STRESS_BPS + cx + rx, xp)
    vcal = H.exit_value(s, ev, fcal[0], cx + rx, xp)
    net0 = 100 * ((v0 or 0.0) - size - netfee) / size
    net50 = 100 * ((v50 or 0.0) - size - netfee) / size
    netcal = 100 * ((vcal or 0.0) - size - netfee) / size
    ts = s['t']
    r = {'pid': pid, 'pair': PAIRS[pid], 'dec_t': ts[i], 'entry_t': ts[j], 'trig_t': trig_t, 'exit_t': t_exit,
         'reason': trig, 'flag': flag, 'fee_bps': fee_j, 'liq': liq_j, 'size': size,
         'net0': net0, 'net50': net50, 'netcal': netcal,
         'usd0': size * net0 / 100, 'usd50': size * net50 / 100, 'usdcal': size * netcal / 100,
         'rt_cost': H.roundtrip_cost_pct(s, j, size), 'hold_s': (t_exit - ts[j]) / 1000,
         'entry_px': s['price'][j], 'exit_px': xp}
    _PRICE[key] = r
    return r


# ------------------------------------------------------------------ book
def run_book(univ, spec, slots, size, *, t_from, t_to, p=0.2, salt='hf', cooldown_s=0, loss_memory=False,
             lm_losses=2, lm_block_s=6 * 3600, cash=None, day_cap=None, cap_basis='usdcal', heat=True, guard='eng',
             loss_basis='usdcal'):
    """Returns (trades, info). Entries only at decision times in [t_from, t_to)."""
    st, sp, si = stream(univ, heat, guard)
    N = len(st)
    k = bisect.bisect_left(st, t_from)
    heap = []
    held = set()
    last_exit = {}
    streak = {}
    block_until = {}
    trades = []
    free = cash
    day_pnl = {}
    capped_days = set()
    reentry_gaps = []
    dead_t = None
    seq = 0
    coin = H.hashed_coin
    while k < N:
        t = st[k]
        if t >= t_to:
            break
        while heap and heap[0][0] <= t:
            et, _, pid0, tr = heapq.heappop(heap)
            held.discard(pid0)
            last_exit[pid0] = et
            if free is not None:
                free += size + tr['usd50']
            if day_cap is not None:
                d = int(et // 86_400_000)
                day_pnl[d] = day_pnl.get(d, 0.0) + tr[cap_basis]
                if day_pnl[d] <= -day_cap:
                    capped_days.add(d)
            if loss_memory:
                v = tr[loss_basis]
                if v < 0:
                    streak[pid0] = streak.get(pid0, 0) + 1
                    if streak[pid0] >= lm_losses:
                        block_until[pid0] = et + lm_block_s * 1000
                else:
                    streak[pid0] = 0
        if len(held) >= slots:
            k = bisect.bisect_left(st, heap[0][0], k + 1)
            continue
        if free is not None and free < size:
            if not heap:
                dead_t = t
                break
            k = bisect.bisect_left(st, heap[0][0], k + 1)
            continue
        if day_cap is not None and int(t // 86_400_000) in capped_days:
            nd = (int(t // 86_400_000) + 1) * 86_400_000
            k = bisect.bisect_left(st, nd, k + 1)
            continue
        pid = sp[k]
        if pid in held:
            k += 1
            continue
        if cooldown_s and pid in last_exit and t - last_exit[pid] < cooldown_s * 1000:
            k += 1
            continue
        if loss_memory and block_until.get(pid, -1) > t:
            k += 1
            continue
        if not coin(PAIRS[pid], t, p, salt):
            k += 1
            continue
        tr = price(pid, si[k], spec, size)
        if tr is None:
            k += 1
            continue
        if pid in last_exit:
            reentry_gaps.append((t - last_exit[pid]) / 1000)
        else:
            reentry_gaps.append(None)
        trades.append(tr)
        held.add(pid)
        seq += 1
        heapq.heappush(heap, (tr['exit_t'], seq, pid, tr))
        if free is not None:
            free -= size
        k += 1
    info = {'reentry_gaps': reentry_gaps, 'dead_t': dead_t, 'capped_days': sorted(capped_days),
            'final_free': free}
    return trades, info


# ------------------------------------------------------------------ statistics
def _q(xs, f):
    if not xs:
        return None
    xs = sorted(xs)
    return xs[int(f * (len(xs) - 1))]


def max_drawdown(trades, start=500.0, key='usd50'):
    """Max drawdown in $ and % of the running peak on realized P&L in exit order (unlimited cash replay)."""
    bal = peak = start
    mdd = mddp = 0.0
    for x in sorted(trades, key=lambda r: r['exit_t']):
        bal += x[key]
        peak = max(peak, bal)
        mdd = max(mdd, peak - bal)
        if peak > 0:
            mddp = max(mddp, (peak - bal) / peak * 100)
    return round(mdd, 2), round(min(mddp, 100.0), 1), round(bal - start, 2)


def time_to_loss(trades, loss, key='usd50'):
    """Hours from the first entry until realized cumulative P&L first reaches -loss (None if never)."""
    if not trades:
        return None
    t0 = min(x['dec_t'] for x in trades)
    cum = 0.0
    for x in sorted(trades, key=lambda r: r['exit_t']):
        cum += x[key]
        if cum <= -loss:
            return round((x['exit_t'] - t0) / 3.6e6, 2)
    return None


def summarize(trades, info, span_h, size):
    n = len(trades)
    if not n:
        return {'n': 0, 'tph': 0.0}
    m = lambda k: sum(x[k] for x in trades) / n
    counts = {}
    for x in trades:
        counts[x['pair']] = counts.get(x['pair'], 0) + 1
    per_hour = {}
    for x in trades:
        h = int(x['dec_t'] // 3_600_000)
        per_hour[h] = per_hour.get(h, 0) + 1
    hours = [per_hour.get(h, 0) for h in range(int(min(x['dec_t'] for x in trades) // 3_600_000),
                                                 int(max(x['dec_t'] for x in trades) // 3_600_000) + 1)]
    gaps = [g for g in info['reentry_gaps'] if g is not None]
    reasons = {}
    for x in trades:
        lab = x['reason'] + ('/' + x['flag'] if x['flag'] else '')
        reasons[lab] = reasons.get(lab, 0) + 1
    usd50_h = sum(x['usd50'] for x in trades) / span_h
    mdd, mddp, pnl = max_drawdown(trades)
    return {
        'n': n, 'tph': round(n / span_h, 2),
        'tph_hour_p10': _q(hours, .1), 'tph_hour_p50': _q(hours, .5), 'hours_ge45': round(sum(1 for h in hours if h >= 45) / len(hours), 3),
        'mean_net50': round(m('net50'), 3), 'mean_netcal': round(m('netcal'), 3), 'mean_net0': round(m('net0'), 3),
        'median_net50': round(_q([x['net50'] for x in trades], .5), 3),
        'mean_usd50': round(m('usd50'), 4), 'usd50_per_h': round(usd50_h, 2),
        'usdcal_per_h': round(sum(x['usdcal'] for x in trades) / span_h, 2),
        'usd0_per_h': round(sum(x['usd0'] for x in trades) / span_h, 2),
        'win50': round(100 * sum(1 for x in trades if x['usd50'] > 0) / n, 1),
        'win0': round(100 * sum(1 for x in trades if x['usd0'] > 0) / n, 1),
        'pairs': len(counts), 'top_pair_share': round(max(counts.values()) / n, 3),
        'mean_rt_cost_model': round(sum((x['rt_cost'] or 0) for x in trades) / n, 3),
        'mean_hold_s': round(m('hold_s'), 1),
        'mean_cycle_s': round(sum((x['exit_t'] - x['dec_t']) for x in trades) / n / 1000, 1),
        'mean_entry_lag_s': round(sum((x['entry_t'] - x['dec_t']) for x in trades) / n / 1000, 1),
        'mean_exit_lag_s': round(sum((x['exit_t'] - x['trig_t']) for x in trades) / n / 1000, 1),
        'reentry_share_le60s': round(sum(1 for g in gaps if g <= 60) / n, 3),
        'reentry_share_le300s': round(sum(1 for g in gaps if g <= 300) / n, 3),
        'reentry_share_le1800s': round(sum(1 for g in gaps if g <= 1800) / n, 3),
        'trades_per_pair_per_h_top': round(max(counts.values()) / span_h, 2),
        'mdd_usd_500': mdd, 'mdd_pct_500': mddp, 'pnl_usd50': pnl,
        'hours_to_bust_500': (round((500 - size) / -usd50_h, 1) if usd50_h < 0 else None),
        'gap_closed': sum(1 for x in trades if x['flag'] == 'GAP'),
        'vanished': sum(1 for x in trades if x['flag'] == 'VANISHED'),
        'reasons': reasons,
    }
