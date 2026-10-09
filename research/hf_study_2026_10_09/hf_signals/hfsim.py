"""Multi-slot high-frequency PAPER book simulator on harness_final primitives. PAPER research only.

Fill rules are the audited harness rules (leaderboard/harness_final.py):
  * entry decided at a refresh event i fills at H.fill_index(s, i) (the next DexScreener refresh, <= 60 s);
  * exits trigger on DexScreener points after the entry fill; the sell fills at H.fill_index(s, k) after the trigger
    point k, with the harness feed-gap (> 10 min, valued at the lower of pre-gap / return price), vanish (-10 %)
    and end-of-data rules copied from H._run_pair;
  * net50 = model + CALIB_V1 per-leg extra (fee bucket, liquidity, size, engine fixed costs) + 50 bps/leg, plus
    +200 bps on the exit leg of STOP exits and +100 bps on TRAIL exits (H.calib_exit_reason_extra_bps);
    net0 = model only.
Brackets are on the PRICE move from the entry fill price (gross), checked at every DexScreener point; the hold limit
is measured from the decision time (the engine executes at decision time; the next-refresh print is the proxy for the
chain price then). A slot is busy from the decision time until the sell trigger (occ='trigger', the engine's real
occupancy) or until the exit fill print (occ='fill', conservative). P&L is realized (for the daily loss cap) at the
exit fill print time, when its valuation exists.
Book rules: at most `slots` concurrent positions, one position per pair, per-pair re-entry cooldown after the sell,
UTC-day realized loss cap (no new entries for the rest of that UTC day once realized net50 P&L <= -cap).
"""
import bisect, heapq, math, os, pickle, random
from common import H, fin, HERE, NAN

SERIES, META = H.load()
_EV = None


def events():
    global _EV
    if _EV is None:
        d = pickle.load(open(os.path.join(HERE, 'events.pkl'), 'rb'))
        _EV = d
    return _EV


_TC = {}


def trade(pair, i, hold_s, tp, sl, size=200.0, hold_from='decision'):
    """One trade decided at point i of `pair`. tp/sl: price-move % from the entry fill (None = off)."""
    key = (pair, i, hold_s, tp, sl, size, hold_from)
    if key in _TC:
        return _TC[key]
    s = SERIES[pair]
    ts, px = s['t'], s['price']
    n = len(ts)
    j = H.fill_index(s, i)
    res = None
    if j is not None:
        fee_j = H.fee_bps(s, j)
        cx = H.calib_extra_bps_per_leg(fee_j, s['liq'][j], size)
        f0 = H.entry_fill(s, j, size)
        f50 = H.entry_fill(s, j, size, H.STRESS_BPS + cx)
        if f0 is not None and f50 is not None:
            qty0, netfee = f0
            qty50 = f50[0]
            pj = px[j]
            t_start = ts[i] if hold_from == 'decision' else ts[j]
            up = pj * (1 + tp / 100) if tp is not None else None
            dn = pj * (1 - sl / 100) if sl is not None else None
            trig, gap_close, k = None, False, j
            for k in range(j + 1, n):
                if H.FEED_GAP_MS is not None and ts[k] - ts[k - 1] > H.FEED_GAP_MS:
                    gap_close = True
                    k -= 1
                    break
                p = px[k]
                if dn is not None and p <= dn:
                    trig = 'STOP'
                elif up is not None and p >= up:
                    trig = 'TP'
                elif ts[k] - t_start >= hold_s * 1000:
                    trig = 'HOLD'
                if trig:
                    break
            trig_k = k
            ev = None
            t_end = META['t1']
            if gap_close:
                e = k
                trig = 'FEED_GAP'
                ev = H._gap_value_index(s, e)
                flag, pxe = 'GAP', s['price'][ev] * (1 - H.GAP_HAIRCUT_PCT / 100)
            elif trig:
                e = H.fill_index(s, k)
                if e is not None:
                    flag, pxe = '', px[e]
                elif k + 1 < n and H.FEED_GAP_MS is not None and ts[k + 1] - ts[k] > H.FEED_GAP_MS:
                    e = k
                    ev = H._gap_value_index(s, e)
                    flag, pxe = 'GAP', s['price'][ev] * (1 - H.GAP_HAIRCUT_PCT / 100)
                elif k + 1 < n:
                    e = k + 1
                    flag, pxe = '', px[e]
                else:
                    e = k
                    flag, pxe = H._close_px(s, e, t_end)
            else:
                e = n - 1
                trig = 'OPEN_AT_END'
                flag, pxe = H._close_px(s, e, t_end)
            ev = e if ev is None else ev
            t_exit = ts[e + 1] if (flag == 'GAP' and H.GAP_VALUATION != 'pre' and e + 1 < n) else ts[e]
            rx = H.calib_exit_reason_extra_bps(trig) if (H.CALIB_IN_STRESS and H.CALIB_EXIT_REASON) else 0.0
            v0 = H.exit_value(s, ev, qty0, 0.0, pxe)
            v50 = H.exit_value(s, ev, qty50, H.STRESS_BPS + cx + rx, pxe)
            v50n = H.exit_value(s, ev, qty50, H.STRESS_BPS + cx, pxe)   # sensitivity: no exit-reason extra
            net0 = 100 * ((v0 or 0.0) - size - netfee) / size
            net50 = 100 * ((v50 or 0.0) - size - netfee) / size
            net50n = 100 * ((v50n or 0.0) - size - netfee) / size
            # a position whose pair left the feed cannot be sold before it returns: the slot stays busy until then
            sell_t = t_exit if flag in ('GAP', 'VANISHED') or trig == 'OPEN_AT_END' else ts[trig_k]
            res = {'pair': pair, 'i': i, 'decision_t': ts[i], 'entry_t': ts[j], 'sell_t': sell_t,
                   'exit_t': t_exit, 'reason': trig, 'flag': flag, 'net0': net0, 'net50': net50, 'net50_noreason': net50n,
                   'usd0': size * net0 / 100, 'usd50': size * net50 / 100, 'size': size, 'fee_bps': fee_j,
                   'liq': s['liq'][j], 'gross': 100 * (pxe / pj - 1) if pj > 0 else NAN, 'cx': cx, 'rx': rx}
    _TC[key] = res
    return res


def day_of(t):
    return int(t // 86_400_000)


def run_book(rows, uni, sig, *, heat_on, slots, hold_s, tp=None, sl=None, cooldown_s=60, cap_usd=None,
             t_from=None, t_to=None, size=200.0, occ='trigger', loss_memory=None):
    """rows: event dicts sorted by time. uni(r)/sig(r) -> bool. Returns (trades, counters).
    loss_memory=(n_losses, block_ms): engine POOL_LOSS_MEMORY_V1 emulation (a close with usd0 < 0 is a loss;
    usd0 = model cost, the most lenient basis), known at the exit print time."""
    held = set()
    rel = []       # (release_t, pair)
    pend = []      # (realize_t, usd50, pair, usd0)
    day_pnl = {}
    last_sell = {}
    streak = {}    # pair -> (consecutive losses, last loss time)
    trades = []
    c = {'signals': 0, 'cap_block': 0, 'held_block': 0, 'cool_block': 0, 'slot_block': 0, 'nofill': 0,
         'loss_mem_block': 0}
    for r in rows:
        t = r['t']
        if t_from is not None and t < t_from:
            continue
        if t_to is not None and t >= t_to:
            break
        while rel and rel[0][0] <= t:
            _, p = heapq.heappop(rel)
            held.discard(p)
        while pend and pend[0][0] <= t:
            rt, u, pp, u0 = heapq.heappop(pend)
            d = day_of(rt)
            day_pnl[d] = day_pnl.get(d, 0.0) + u
            if u0 < 0:
                streak[pp] = (streak.get(pp, (0, None))[0] + 1, rt)
            else:
                streak[pp] = (0, None)
        if not uni(r):
            continue
        if heat_on and r['heat']:
            continue
        if not sig(r):
            continue
        c['signals'] += 1
        if cap_usd is not None and day_pnl.get(day_of(t), 0.0) <= -cap_usd:
            c['cap_block'] += 1
            continue
        p = r['pair']
        if p in held:
            c['held_block'] += 1
            continue
        if t < last_sell.get(p, -1e18) + cooldown_s * 1000:
            c['cool_block'] += 1
            continue
        if loss_memory is not None:
            k_l, last_l = streak.get(p, (0, None))
            if k_l >= loss_memory[0] and last_l is not None and t < last_l + loss_memory[1]:
                c['loss_mem_block'] += 1
                continue
        if len(held) >= slots:
            c['slot_block'] += 1
            continue
        tr = trade(p, r['i'], hold_s, tp, sl, size)
        if tr is None:
            c['nofill'] += 1
            continue
        release = tr['sell_t'] if occ == 'trigger' else tr['exit_t']
        held.add(p)
        heapq.heappush(rel, (max(release, t + 1), p))
        heapq.heappush(pend, (tr['exit_t'], tr['usd50'], p, tr['usd0']))
        last_sell[p] = release
        trades.append(tr)
    return trades, c


def pair_ci(trades, key='net50', reps=1000, seed=7):
    g = {}
    for x in trades:
        g.setdefault(x['pair'], []).append(x[key])
    groups = list(g.values())
    if len(groups) < 3:
        return [None, None]
    rnd = random.Random(seed)
    ms = []
    for _ in range(reps):
        tot = cnt = 0
        for _ in range(len(groups)):
            gg = groups[rnd.randrange(len(groups))]
            tot += sum(gg)
            cnt += len(gg)
        ms.append(tot / cnt)
    ms.sort()
    return [round(ms[int(reps * .025)], 3), round(ms[int(reps * .975)], 3)]


def stats(trades, hours, start_capital, ci=False):
    if not trades:
        return {'n': 0, 'tph': 0.0}
    n = len(trades)
    cnt = {}
    for x in trades:
        cnt[x['pair']] = cnt.get(x['pair'], 0) + 1
    # equity curve of realized net50 $ at exit times
    eq = start_capital
    peak = eq
    mdd = 0.0
    for x in sorted(trades, key=lambda r: r['exit_t']):
        eq += x['usd50']
        peak = max(peak, eq)
        mdd = max(mdd, (peak - eq) / peak * 100 if peak > 0 else 100.0)
    hrs = {}
    for x in trades:
        h = int(x['decision_t'] // 3_600_000)
        hrs[h] = hrs.get(h, 0) + 1
    reasons = {}
    for x in trades:
        lab = x['reason'] + ('/' + x['flag'] if x['flag'] else '')
        reasons[lab] = reasons.get(lab, 0) + 1
    out = {'n': n, 'tph': round(n / hours, 2), 'pairs': len(cnt), 'top_pair_share': round(max(cnt.values()) / n, 3),
           'mean_net50': round(sum(x['net50'] for x in trades) / n, 3),
           'mean_net50_noreason': round(sum(x['net50_noreason'] for x in trades) / n, 3),
           'mean_net0': round(sum(x['net0'] for x in trades) / n, 3),
           'mean_gross': round(sum(x['gross'] for x in trades if fin(x['gross'])) / n, 3),
           'mean_usd50': round(sum(x['usd50'] for x in trades) / n, 3),
           'usd50_per_hour': round(sum(x['usd50'] for x in trades) / hours, 2),
           'usd0_per_hour': round(sum(x['usd0'] for x in trades) / hours, 2),
           'win50': round(100 * sum(1 for x in trades if x['usd50'] > 0) / n, 1),
           'win0': round(100 * sum(1 for x in trades if x['usd0'] > 0) / n, 1),
           'median_hold_s': sorted((x['sell_t'] - x['decision_t']) / 1000 for x in trades)[n // 2],
           'mdd_pct': round(mdd, 2), 'final_equity': round(eq, 2),
           'hours_ge40': round(sum(1 for v in hrs.values() if v >= 40) / max(1.0, hours), 3),
           'exits': reasons}
    if ci:
        out['ci95_net50_pair'] = pair_ci(trades)
    return out
