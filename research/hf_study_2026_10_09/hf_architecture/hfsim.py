"""Multi-slot PAPER book simulator on harness_final primitives (hf_architecture study, read-only).

One book, N concurrent slots, at most one slot per pool, per-pool re-entry cooldown, optional HF loss-streak
brake or POOL_LOSS_MEMORY_V1 emulation, UTC-day loss cap (booked basis, realized + negative open marks,
latched until the next UTC day, never touching open exits) and an equity kill floor.

Fills follow harness_final F1 exactly: an order decided at point d fills at the first later point within
60 s whose price differs from the decision print, else (quiet) at the first later point within 60 s; an entry
with no later point within 60 s is cancelled (harness skips it). An exit with no later point within 60 s fills
at the next point whenever it arrives (harness k+1), a > 10 min feed gap closes at min(pre-gap, return)
(F2), and a held pool unseen for 10 min closes VANISHED at its last price -10% (Lab close policy).
Valuation is the harness _run_pair formula: net0 (model), net50 (model + CALIB_V1 + 50 bps/leg + STOP 200 /
TRAIL 100 bps exit extra) and a Lab-like booked basis (model + CALIB_V1 per leg, no stress).
"""
import math

DAY = 86_400_000


class Book:
    def __init__(self, H, series, elig, cfg, events, t_from, t_to):
        self.H, self.series, self.elig, self.cfg = H, series, elig, cfg
        self.events, self.t_from, self.t_to = events, t_from, t_to

    def run(self):
        H, cfg = self.H, self.cfg
        series, elig = self.series, self.elig
        N, size, stop, tp = cfg['slots'], cfg['notional'], cfg['stop'], cfg['tp']
        hold_ms = cfg['hold_min'] * 60_000
        cool_ms = cfg['cooldown_s'] * 1000
        tier, heat_on, prob, salt = cfg['tier'], cfg['heat'], cfg['prob'], cfg.get('salt', 'hf')
        cap_usd = cfg.get('daily_cap_usd')
        start = cfg.get('start', 1000.0)
        kf = cfg.get('kill_floor')
        kill_floor = -math.inf if kf is None else kf * start
        brake = cfg.get('brake')            # (losses, block_ms) HF loss-streak brake within the UTC day
        plm = cfg.get('plm')                # POOL_LOSS_MEMORY_V1 emulation: (2, 6 h)
        slots = {}
        held = set()
        cool = {}
        streak = {}
        block = {}
        lastt, lasti = {}, {}
        day_real = {}
        paused_days = set()
        pause_at = {}
        killed_at = None
        balance = start          # booked basis
        trades = []
        entries = cancels = 0
        seq = 0
        t_end_entries = self.t_to

        def mtm(pos, s, k):
            v = H.exit_value(s, k, pos['qtyb'], pos['cx'])
            return 0.0 if v is None else v - pos['size'] - pos['netfee']

        def close(pos, ev, px, trig, flag, t_exit):
            nonlocal balance
            s = series[pos['pair']]
            cx = pos['cx']
            rx = H.calib_exit_reason_extra_bps(trig)
            v0 = H.exit_value(s, ev, pos['qty'], 0.0, px)
            v50 = H.exit_value(s, ev, pos['qty50'], H.STRESS_BPS + cx + rx, px)
            vb = H.exit_value(s, ev, pos['qtyb'], cx, px)
            sz, nf = pos['size'], pos['netfee']
            net0 = 100 * ((v0 or 0.0) - sz - nf) / sz
            net50 = 100 * ((v50 or 0.0) - sz - nf) / sz
            netb = 100 * ((vb or 0.0) - sz - nf) / sz
            usdb = sz * netb / 100
            balance += usdb
            d = int(t_exit // DAY)
            day_real[d] = day_real.get(d, 0.0) + usdb
            p = pos['pair']
            trades.append({'pair': p, 'decision_t': pos['t_d'], 'entry_t': pos['t_j'], 'exit_t': t_exit,
                           'reason': trig, 'flag': flag, 'net0': net0, 'net50': net50, 'netb': netb,
                           'usd0': sz * net0 / 100, 'usd50': sz * net50 / 100, 'usdb': usdb, 'size': sz,
                           'hold_s': (t_exit - pos['t_j']) / 1000, 'fee_bps': H.fee_bps(s, pos['j'])})
            held.discard(p)
            slots.pop(pos['id'], None)
            cool[p] = t_exit + cool_ms
            if usdb < 0:
                streak[p] = streak.get(p, 0) + 1
            else:
                streak[p] = 0
            if brake and streak.get(p, 0) >= brake[0]:
                block[p] = t_exit + brake[1]
                streak[p] = 0
            if plm and streak.get(p, 0) >= plm[0]:
                block[p] = t_exit + plm[1]

        def fill_entry(pos, j):
            nonlocal cancels
            s = series[pos['pair']]
            cx = H.calib_extra_bps_per_leg(H.fee_bps(s, j), s['liq'][j], size)
            f = H.entry_fill(s, j, size)
            f50 = H.entry_fill(s, j, size, H.STRESS_BPS + cx)
            fb = H.entry_fill(s, j, size, cx)
            if f is None or f50 is None or fb is None:
                cancels += 1
                held.discard(pos['pair'])
                slots.pop(pos['id'], None)
                return False
            pos.update(state='open', j=j, t_j=s['t'][j], qty=f[0], netfee=f[1], qty50=f50[0], qtyb=fb[0], cx=cx,
                       size=size)
            return True

        def exit_check(pos, k):
            s = series[pos['pair']]
            v = H.exit_value(s, k, pos['qty'])
            if v is None:
                return None
            net = 100 * (v - pos['size'] - pos['netfee']) / pos['size']
            if net <= -stop:
                return 'STOP'
            if tp is not None and net >= tp:
                return 'TP'
            if s['t'][k] - pos['t_j'] >= hold_ms:
                return 'HOLD'
            return None

        def resolve_time(t):
            # time-based resolution of pending legs and vanished pools (any pair's event advances time)
            for pos in list(slots.values()):
                p = pos['pair']
                s = series[p]
                if pos['state'] == 'pending_entry':
                    if t - pos['t_d'] > 60_000:
                        if pos['first'] is None:
                            nonlocal_cancel(pos)
                        else:
                            if fill_entry(pos, pos['first']):
                                pass
                elif pos['state'] == 'pending_exit':
                    if t - pos['t_k'] > 60_000 and pos['first'] is not None:
                        e = pos['first']
                        close(pos, e, s['price'][e], pos['trig'], '', s['t'][e] if s['t'][e] > pos['t_k'] else t)
                if pos['id'] in slots and pos['state'] in ('open', 'pending_exit'):
                    lt = lastt.get(p)
                    if lt is not None and t - lt > 600_000:
                        li = lasti[p]
                        close(pos, li, s['price'][li] * 0.9, pos.get('trig') or 'VANISHED', 'VANISHED', lt + 600_000)

        def nonlocal_cancel(pos):
            nonlocal cancels
            cancels += 1
            held.discard(pos['pair'])
            slots.pop(pos['id'], None)

        last_sweep = 0
        for (t, p, i) in self.events:
            if t < self.t_from - 3_600_000:
                lastt[p], lasti[p] = t, i
                continue
            if t >= t_end_entries and not slots:
                break
            if t - last_sweep >= 2_000:
                resolve_time(t)
                last_sweep = t
            s = series[p]
            pr = s['price']
            gap = p in lastt and t - lastt[p] > 600_000
            # positions on this pool
            if p in held:
                pos = next(x for x in slots.values() if x['pair'] == p)
                if pos['state'] == 'pending_entry' and i > pos['d']:
                    if t - pos['t_d'] <= 60_000:
                        if pr[i] != pos['p_d']:
                            fill_entry(pos, i)
                        elif pos['first'] is None:
                            pos['first'] = i
                    else:
                        if pos['first'] is None:
                            nonlocal_cancel(pos)
                        else:
                            fill_entry(pos, pos['first'])
                if pos['id'] in slots and pos['state'] == 'open' and i > pos['j']:
                    if gap:
                        e = i - 1
                        ev = H._gap_value_index(s, e)
                        close(pos, ev, pr[ev], 'FEED_GAP', 'GAP', t)
                    else:
                        trig = exit_check(pos, i)
                        if trig:
                            pos.update(state='pending_exit', trig=trig, k=i, t_k=t, p_k=pr[i], first=None)
                elif pos['id'] in slots and pos['state'] == 'pending_exit' and i > pos['k']:
                    if gap:
                        e = i - 1
                        ev = H._gap_value_index(s, e)
                        close(pos, ev, pr[ev], pos['trig'], 'GAP', t)
                    elif t - pos['t_k'] <= 60_000:
                        if pr[i] != pos['p_k']:
                            close(pos, i, pr[i], pos['trig'], '', t)
                        elif pos['first'] is None:
                            pos['first'] = i
                    else:
                        e = pos['first'] if pos['first'] is not None else i
                        close(pos, e, pr[e], pos['trig'], '', t)
            lastt[p], lasti[p] = t, i
            # entry decision
            if (t < self.t_from or t >= t_end_entries or killed_at is not None or len(slots) >= N or p in held
                    or t < cool.get(p, 0) or t < block.get(p, 0)):
                continue
            el = elig.get(p)
            if el is None or not el[tier][i] or (heat_on and el['heat'][i]):
                continue
            if not H.hashed_coin(s['pair'], t, prob, salt):
                continue
            d = int(t // DAY)
            if d in paused_days:
                continue
            open_neg = 0.0
            committed = 0.0
            for x in slots.values():
                committed += size
                if x['state'] in ('open', 'pending_exit'):
                    xs = series[x['pair']]
                    open_neg += min(0.0, mtm(x, xs, lasti[x['pair']]))
            equity = balance + open_neg
            if equity <= kill_floor:
                killed_at = t
                continue
            if cap_usd is not None and day_real.get(d, 0.0) + open_neg <= -cap_usd:
                paused_days.add(d)
                pause_at[d] = t
                continue
            if balance - committed < size + 1:
                continue
            seq += 1
            entries += 1
            slots[seq] = {'id': seq, 'pair': p, 'state': 'pending_entry', 'd': i, 't_d': t, 'p_d': pr[i],
                          'first': None}
            held.add(p)
        # force-close leftovers at last price (rare; flagged END)
        for pos in list(slots.values()):
            s = series[pos['pair']]
            if pos['state'] in ('open', 'pending_exit'):
                li = lasti[pos['pair']]
                close(pos, li, s['price'][li], pos.get('trig') or 'END', 'END', lastt[pos['pair']])
        return {'trades': trades, 'entries': entries, 'cancels': cancels, 'paused_days': sorted(paused_days),
                'pause_at': pause_at, 'killed_at': killed_at, 'final_balance_booked': balance}


def summarize(H, res, t_from, t_to, start=1000.0):
    tr = [x for x in res['trades'] if t_from <= x['entry_t'] < t_to]
    hours = (t_to - t_from) / 3.6e6
    out = {'hours': round(hours, 2), 'entries': res['entries'], 'cancelled_entries': res['cancels']}
    if not tr:
        out['n'] = 0
        return out
    n = len(tr)
    pairs = {}
    for x in tr:
        pairs[x['pair']] = pairs.get(x['pair'], 0) + 1
    usd50 = [x['usd50'] for x in tr]
    lo, hi, ng = H.cluster_ci_pair(tr, 'usd50')
    # portfolio drawdown on net50 and booked bases, in exit-time order
    def dd(key):
        bal = peak = start
        worst = 0.0
        for x in sorted(tr, key=lambda r: r['exit_t']):
            bal += x[key]
            peak = max(peak, bal)
            worst = max(worst, (peak - bal) / peak * 100 if peak > 0 else 0)
        return round(worst, 2), round(bal - start, 2)
    dd50, tot50 = dd('usd50')
    ddb, totb = dd('usdb')
    reasons = {}
    for x in tr:
        k = x['reason'] + ('/' + x['flag'] if x['flag'] else '')
        reasons[k] = reasons.get(k, 0) + 1
    out.update({'n': n, 'trades_per_hour': round(n / hours, 2),
                'mean_net50_pct': round(sum(x['net50'] for x in tr) / n, 3),
                'mean_net0_pct': round(sum(x['net0'] for x in tr) / n, 3),
                'mean_booked_pct': round(sum(x['netb'] for x in tr) / n, 3),
                'mean_usd50': round(sum(usd50) / n, 4), 'usd50_per_hour': round(sum(usd50) / hours, 2),
                'mean_usd0': round(sum(x['usd0'] for x in tr) / n, 4),
                'win_rate_net50': round(100 * sum(1 for v in usd50 if v > 0) / n, 1),
                'win_rate_net0': round(100 * sum(1 for x in tr if x['usd0'] > 0) / n, 1),
                'pairs': len(pairs), 'top_pair_share': round(max(pairs.values()) / n, 3),
                'ci95_mean_usd50_pair': [lo, hi],
                'max_drawdown_pct_net50': dd50, 'total_usd50': tot50,
                'max_drawdown_pct_booked': ddb, 'total_usd_booked': totb,
                'avg_hold_s': round(sum(x['hold_s'] for x in tr) / n, 1),
                'avg_entry_lag_s': round(sum((x['entry_t'] - x['decision_t']) for x in tr) / n / 1000, 1),
                'exits': reasons, 'paused_days': res['paused_days'],
                'pause_at_utc_hour': {d: round((t % DAY) / 3.6e6, 2) for d, t in res['pause_at'].items()},
                'killed_after_h': None if res['killed_at'] is None else round((res['killed_at'] - t_from) / 3.6e6, 2)})
    return out
