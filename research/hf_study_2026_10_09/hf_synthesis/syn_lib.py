"""hf_synthesis: one consistent multi-slot HF book simulator for the final LAB_HIGH_FREQUENCY_V1 book set.
PAPER research only. Read-only on the research data and on the sibling study folders; writes only into hf_synthesis/.

Basis (identical to hf_signals/hfsim.py, which reproduced H.simulate exactly on 1,500 trades):
  * decisions only at DexScreener REFRESH events (hf_signals/events.pkl: price changed vs the previous point, feed
    contiguous < 60 s, H.rug_guard_v1 passed, liq >= $20k; engine-equivalent STRUCTURAL_RUG_GUARD_V1 flag 'eg' =
    rug_guard_v1 + fake mcap at 2 % + ticker reuse across mints; heat flag = HEAT_VETO_STACK approximation);
  * entry fills at H.fill_index (next refresh <= 60 s, else next point), exit trigger at the first point >= decision +
    hold, exit fill at H.fill_index after the trigger; feed-gap (>10 min, 'min' valuation), VANISHED -10 %, drained
    pool = 0 - all copied from hfsim.trade (harness_final rules);
  * booked  = model + CALIB_V1 per-leg extra (what the Lab books: calibrated_entry/exit_execution);
    net50   = model + CALIB_V1 + 50 bps/leg (+200 bps STOP / +100 bps TRAIL; time exits carry none);
    net0    = model only; dp50 = net50 if both legs had filled at the DECISION / TRIGGER print (stale-print shadow).
Additions for the final spec (all past-only):
  * slot occupancy until the EXIT FILL (the HF container books the sell at the next refresh, so the slot is busy
    until then: hfsim occ='fill');
  * pacing (per book): 'none', ('gap', s) = >= s seconds between orders, ('bucket', s, cap) = token bucket refilled
    one token per s seconds, capacity cap;
  * at most one new order per book per 2-s Lab refresh;
  * HF_POOL_RULE: per-pool cooldown after the exit fill + loss brake (n consecutive BOOKED losses -> block ms);
  * daily loss cap on BOOKED realized P&L per UTC day (realized at the exit fill time; open marks ignored here).
"""
import bisect, heapq, json, math, os, random, sys

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
SIG_DIR = os.path.normpath(os.path.join(HERE, '..', 'hf_signals'))
sys.path.insert(0, SIG_DIR)
import hfsim as S                      # noqa: E402  (loads harness_final, binds sys.modules['harness'])
from common import H, fin, ab          # noqa: E402
from signals import SIGS               # noqa: E402

SERIES, META = S.SERIES, S.META
NAN = float('nan')
HOUR = 3_600_000
DAY = 86_400_000

UNIVERSES = {
    'E95': lambda r: r['eg'] and r['liq'] >= 50_000 and r['fee'] <= 95,
    'E50': lambda r: r['eg'] and r['liq'] >= 50_000 and r['fee'] <= 50,
    'L50': lambda r: r['eg'] and r['liq'] >= 250_000 and r['fee'] <= 50,
}


def rnd(p, salt):
    return lambda r: H.hashed_coin(r['pair'], r['t'], p, salt)


PREDICATES = {
    'QUIET': SIGS['QUIET'],
    'NEAR15LOW': SIGS['NEAR_15M_LOW'],
}

_TC = {}


def trade(pair, i, hold_s, size):
    """Time-exit trade decided at refresh point i (copy of hfsim.trade with tp=sl=None plus booked and dp50)."""
    key = (pair, i, hold_s, size)
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
        fc = H.entry_fill(s, j, size, cx)
        fdp = H.entry_fill(s, i, size, H.STRESS_BPS + cx)
        if f0 is not None and f50 is not None and fc is not None:
            qty0, netfee = f0
            qty50, qtyc = f50[0], fc[0]
            pj = px[j]
            t_start = ts[i]
            trig, gap_close, k = None, False, j
            for k in range(j + 1, n):
                if H.FEED_GAP_MS is not None and ts[k] - ts[k - 1] > H.FEED_GAP_MS:
                    gap_close = True
                    k -= 1
                    break
                if ts[k] - t_start >= hold_s * 1000:
                    trig = 'HOLD'
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
            v0 = H.exit_value(s, ev, qty0, 0.0, pxe)
            v50 = H.exit_value(s, ev, qty50, H.STRESS_BPS + cx, pxe)
            vc = H.exit_value(s, ev, qtyc, cx, pxe)
            net0 = 100 * ((v0 or 0.0) - size - netfee) / size
            net50 = 100 * ((v50 or 0.0) - size - netfee) / size
            netc = 100 * ((vc or 0.0) - size - netfee) / size
            dp50 = NAN
            if fdp is not None and not flag:
                vdp = H.exit_value(s, trig_k, fdp[0], H.STRESS_BPS + cx, px[trig_k])
                if vdp is not None:
                    dp50 = 100 * (vdp - size - fdp[1]) / size
            res = {'pair': pair, 'i': i, 'decision_t': ts[i], 'entry_t': ts[j], 'trig_t': ts[trig_k], 'exit_t': t_exit,
                   'reason': trig, 'flag': flag, 'net0': net0, 'net50': net50, 'netc': netc, 'dp50': dp50,
                   'usd0': size * net0 / 100, 'usd50': size * net50 / 100, 'usdc': size * netc / 100,
                   'size': size, 'fee_bps': fee_j, 'liq': s['liq'][j],
                   'gross': 100 * (pxe / pj - 1) if pj > 0 else NAN, 'cx': cx,
                   'entry_quiet': px[j] == px[i], 'exit_quiet': (not flag) and pxe == px[trig_k],
                   'entry_lag_s': (ts[j] - ts[i]) / 1000, 'exit_lag_s': (t_exit - ts[trig_k]) / 1000}
    _TC[key] = res
    return res


def run_book(rows, uni, pred, *, slots, hold_s=60, cooldown_s=120, pacing=('none',), brake=None, cap_usd=None,
             t_from=None, t_to=None, size=25.0, heat_on=True, refresh_ms=2000, gross_brake=None, cap_day_ms=DAY,
             cap_offset_ms=0):
    """rows sorted by (t, pair). Returns (trades, counters). Slot busy from the decision to the exit fill.
    pacing ('roll', window_s, n): at most n orders in the trailing window_s seconds.
    gross_brake (pct, ms): block a pool for ms after a close whose gross price move (exit fill / entry fill - 1)
    is <= pct (a real adverse move, not the round-trip cost). Known at the exit fill time.
    cap windows: index = (t - cap_offset_ms) // cap_day_ms (UTC day by default)."""
    held = set()
    rel = []           # (release_t, pair)
    pend = []          # (exit_t, usdc, pair, gross)
    day_booked = {}
    last_exit = {}
    streak = {}        # pair -> [consecutive booked losses, last loss t]
    gb_until = {}
    roll = []
    trades = []
    last_order_t = -1e18
    last_refresh_bin = None
    tokens = None
    tok_t = None
    c = {'signals': 0, 'cap': 0, 'held': 0, 'cool': 0, 'brake': 0, 'gbrake': 0, 'slots': 0, 'pace': 0, 'refresh': 0,
         'nofill': 0}
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
            rt, uc, pp, gr = heapq.heappop(pend)
            d = int((rt - cap_offset_ms) // cap_day_ms)
            day_booked[d] = day_booked.get(d, 0.0) + uc
            st = streak.setdefault(pp, [0, None])
            if uc < 0:
                st[0] += 1
                st[1] = rt
            else:
                st[0], st[1] = 0, None
            if gross_brake is not None and fin(gr) and gr <= gross_brake[0]:
                gb_until[pp] = rt + gross_brake[1]
        if not uni(r):
            continue
        if heat_on and r['heat']:
            continue
        if not pred(r):
            continue
        c['signals'] += 1
        if cap_usd is not None and day_booked.get(int((t - cap_offset_ms) // cap_day_ms), 0.0) <= -cap_usd:
            c['cap'] += 1
            continue
        p = r['pair']
        if p in held:
            c['held'] += 1
            continue
        if t < last_exit.get(p, -1e18) + cooldown_s * 1000:
            c['cool'] += 1
            continue
        if brake is not None:
            st = streak.get(p)
            if st and st[0] >= brake[0] and st[1] is not None and t < st[1] + brake[1]:
                c['brake'] += 1
                continue
        if t < gb_until.get(p, -1e18):
            c['gbrake'] += 1
            continue
        if len(held) >= slots:
            c['slots'] += 1
            continue
        rb = int(t // refresh_ms)
        if rb == last_refresh_bin:
            c['refresh'] += 1
            continue
        if pacing[0] == 'gap':
            if t < last_order_t + pacing[1] * 1000:
                c['pace'] += 1
                continue
        elif pacing[0] == 'bucket':
            interval, cap = pacing[1] * 1000.0, float(pacing[2])
            if tokens is None:
                tokens, tok_t = cap, t
            tokens = min(cap, tokens + (t - tok_t) / interval)
            tok_t = t
            if tokens < 1.0:
                c['pace'] += 1
                continue
        elif pacing[0] == 'roll':
            lo = t - pacing[1] * 1000
            while roll and roll[0] <= lo:
                roll.pop(0)
            if len(roll) >= pacing[2]:
                c['pace'] += 1
                continue
        tr = trade(p, r['i'], hold_s, size)
        if tr is None:
            c['nofill'] += 1
            continue
        if pacing[0] == 'bucket':
            tokens -= 1.0
        if pacing[0] == 'roll':
            roll.append(t)
        last_order_t = t
        last_refresh_bin = rb
        release = max(tr['exit_t'], t + 1)
        held.add(p)
        heapq.heappush(rel, (release, p))
        heapq.heappush(pend, (tr['exit_t'], tr['usdc'], p, tr['gross']))
        last_exit[p] = release
        trades.append(tr)
    return trades, c


def pair_ci(trades, key='net50', reps=1000, seed=7):
    g = {}
    for x in trades:
        g.setdefault(x['pair'], []).append(x[key])
    groups = list(g.values())
    if len(groups) < 3:
        return [None, None]
    rng = random.Random(seed)
    ms = []
    for _ in range(reps):
        tot = cnt = 0
        for _ in range(len(groups)):
            gg = groups[rng.randrange(len(groups))]
            tot += sum(gg)
            cnt += len(gg)
        ms.append(tot / cnt)
    ms.sort()
    return [round(ms[int(reps * .025)], 3), round(ms[int(reps * .975)], 3)]


def gap_ci(a, b, key='net50', reps=1000, seed=11):
    """Pair-bootstrap CI of mean(a) - mean(b), resampling the pairs of each book independently."""
    def groups(tr):
        g = {}
        for x in tr:
            g.setdefault(x['pair'], []).append(x[key])
        return list(g.values())
    ga, gb = groups(a), groups(b)
    if len(ga) < 3 or len(gb) < 3:
        return [None, None]
    rng = random.Random(seed)
    out = []
    for _ in range(reps):
        def m(gs):
            tot = cnt = 0
            for _ in range(len(gs)):
                gg = gs[rng.randrange(len(gs))]
                tot += sum(gg)
                cnt += len(gg)
            return tot / cnt
        out.append(m(ga) - m(gb))
    out.sort()
    return [round(out[int(reps * .025)], 3), round(out[int(reps * .975)], 3)]


def mean(xs):
    xs = [x for x in xs if fin(x)]
    return sum(xs) / len(xs) if xs else NAN


def stats(trades, t_from, t_to, start=1000.0, ci=False, live=None):
    hours = (t_to - t_from) / HOUR
    if not trades:
        return {'n': 0, 'tph': 0.0}
    n = len(trades)
    cnt, usd_pair = {}, {}
    for x in trades:
        cnt[x['pair']] = cnt.get(x['pair'], 0) + 1
        usd_pair[x['pair']] = usd_pair.get(x['pair'], 0.0) + x['usd50']
    best = max(usd_pair, key=lambda p: usd_pair[p])
    rest = [x for x in trades if x['pair'] != best]
    # hourly counts over FULL clock hours inside the window (zero hours included)
    h0, h1 = int(math.ceil(t_from / HOUR)), int(t_to // HOUR)
    hc = {h: 0 for h in range(h0, h1)}
    for x in trades:
        h = int(x['decision_t'] // HOUR)
        if h in hc:
            hc[h] += 1
    hv = sorted(hc.values())
    # equity (booked and net50) on a $start book, realized at exit fill time
    def mdd(key):
        eq = peak = start
        dd = 0.0
        for x in sorted(trades, key=lambda r: r['exit_t']):
            eq += x[key]
            peak = max(peak, eq)
            dd = max(dd, peak - eq)
        return round(dd, 2), round(eq, 2)
    # same-pool re-entry gaps
    by_pair = {}
    for x in sorted(trades, key=lambda r: r['decision_t']):
        by_pair.setdefault(x['pair'], []).append(x)
    gaps = []
    for xs in by_pair.values():
        for a, b in zip(xs, xs[1:]):
            gaps.append((b['decision_t'] - a['exit_t']) / 1000)
    fee50 = sum(1 for x in trades if x['fee_bps'] <= 50) / n
    liqs = sorted(x['liq'] for x in trades)
    reasons = {}
    for x in trades:
        lab = x['reason'] + ('/' + x['flag'] if x['flag'] else '')
        reasons[lab] = reasons.get(lab, 0) + 1
    out = {'n': n, 'hours': round(hours, 2), 'tph': round(n / hours, 2),
           'hour_p10': hv[int(0.1 * (len(hv) - 1))] if hv else None, 'hour_median': hv[len(hv) // 2] if hv else None,
           'hours_ge40': round(sum(1 for v in hv if v >= 40) / len(hv), 3) if hv else None,
           'hours_ge45': round(sum(1 for v in hv if v >= 45) / len(hv), 3) if hv else None,
           'pairs': len(cnt), 'top_pair_share': round(max(cnt.values()) / n, 3),
           'best_pair_removed_net50': round(mean(x['net50'] for x in rest), 3) if rest else None,
           'mean_net50': round(mean(x['net50'] for x in trades), 3),
           'median_net50': round(sorted(x['net50'] for x in trades)[n // 2], 3),
           'mean_booked': round(mean(x['netc'] for x in trades), 3),
           'mean_net0': round(mean(x['net0'] for x in trades), 3),
           'mean_gross': round(mean(x['gross'] for x in trades), 3),
           'mean_dp50': round(mean(x['dp50'] for x in trades), 3),
           'usd50_per_trade': round(sum(x['usd50'] for x in trades) / n, 3),
           'booked_usd_per_trade': round(sum(x['usdc'] for x in trades) / n, 3),
           'usd50_per_hour': round(sum(x['usd50'] for x in trades) / hours, 2),
           'booked_usd_per_hour': round(sum(x['usdc'] for x in trades) / hours, 2),
           'usd0_per_hour': round(sum(x['usd0'] for x in trades) / hours, 2),
           'win50': round(100 * sum(1 for x in trades if x['usd50'] > 0) / n, 2),
           'win_booked': round(100 * sum(1 for x in trades if x['usdc'] > 0) / n, 2),
           'win0': round(100 * sum(1 for x in trades if x['usd0'] > 0) / n, 2),
           'fee_le50_share': round(fee50, 3), 'median_liq': round(liqs[n // 2]),
           'reentry_le60s_share': round(sum(1 for g in gaps if g <= 60) / n, 3),
           'reentry_le300s_share': round(sum(1 for g in gaps if g <= 300) / n, 3),
           'entry_quiet_share': round(sum(1 for x in trades if x['entry_quiet']) / n, 3),
           'mean_entry_lag_s': round(mean(x['entry_lag_s'] for x in trades), 1),
           'mean_exit_lag_s': round(mean(x['exit_lag_s'] for x in trades), 1),
           'mean_cycle_s': round(mean((x['exit_t'] - x['decision_t']) / 1000 for x in trades), 1),
           'mdd_booked_usd': mdd('usdc')[0], 'end_equity_booked': mdd('usdc')[1],
           'mdd_net50_usd': mdd('usd50')[0], 'end_equity_net50': mdd('usd50')[1],
           'exits': reasons}
    if live is not None:
        lv = sorted(hc[h] for h in hc if h in live)
        out['live_hours'] = len(lv)
        out['tph_live'] = round(sum(lv) / len(lv), 2) if lv else 0.0
        out['live_hour_p10'] = lv[int(0.1 * (len(lv) - 1))] if lv else None
        out['live_hour_median'] = lv[len(lv) // 2] if lv else None
        out['live_hours_ge40'] = round(sum(1 for v in lv if v >= 40) / len(lv), 3) if lv else None
        out['max_hour'] = max(hv) if hv else None
    if ci:
        out['ci95_net50_pct_pair'] = pair_ci(trades, 'net50')
        out['ci95_usd50_pair'] = pair_ci(trades, 'usd50')
    return out


def live_hours(rows, uni, t_from, t_to, min_pools=3):
    """Full clock hours in which the universe had >= min_pools distinct heat-passing pools refreshing (any book could
    trade). Past-only data; used only as a denominator."""
    pools = {}
    for r in rows:
        if r['t'] < t_from or r['t'] >= t_to or r['heat'] or not uni(r):
            continue
        pools.setdefault(int(r['t'] // HOUR), set()).add(r['pair'])
    h0, h1 = int(math.ceil(t_from / HOUR)), int(t_to // HOUR)
    return {h for h in range(h0, h1) if len(pools.get(h, ())) >= min_pools}
