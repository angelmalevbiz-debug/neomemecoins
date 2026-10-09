"""build_points: every tape decision point (ingestion moment of a real-time swap) in seated PumpSwap SOL pools that
pass the structural rug guard (H.rug_guard_v1), with past-only features and the forward outcome of a time-exit
scalp under two fill models. PAPER research only.

Decision point: a pool's ingestion moment (av, grouped per second) of at least one swap whose block time is <= 60 s
older than its ingestion. Decision time t = that av (decision >= ingestion time by construction).
Features (past-only): tape flow over (t-W, t] for W = 30 s / 60 s counting only swaps ingested by t; tape mid change
over 60 s from ingested swaps; DexScreener heat-veto flags, fee tier, liquidity, interim screen at the last series
point <= t.
Outcomes, notional $100, time exit after HOLD seconds (no stop/target, so no stop/trail cost extras):
  A  DexScreener: entry at H.fill_index(s, sidx(t)) (next refresh), exit decision at entry fill + HOLD, exit at the
     next refresh after that (harness gap / vanish rules).
  B2/B5  on-chain: entry at the chain mid at t + L (L = 2 s / 5 s), exit decision at t + L + HOLD, exit fill at the
     chain mid L later. If the tape shows no swap within 60 s before and 120 s after the exit fill time (seat lost),
     the exit falls back to the DexScreener next-refresh price (flag 'fb').
Costs: harness entry_fill/exit_value at the series point for fee/liquidity/SOL price + CALIB_V1 (+50 bps/leg stress
for net50).
"""
import bisect, json, os, pickle, sys, time

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
t00 = time.time()
import hfcommon as C
H = C.H
C.set_half_spread(json.load(open(os.path.join(C.HERE, 'half_spread.json'))))
NOTIONAL = 100.0
HOLDS = (60, 120, 180, 300)
LATS = (2, 5)
ser = C.series


def outcome_A(s, t, hold):
    ts, n = s['t'], len(s['t'])
    i = C.sidx(s, t)
    if i < 0 or t - ts[i] > 60_000:
        return None
    j = H.fill_index(s, i)
    if j is None:
        return None
    x = ts[j] + hold * 1000
    k = C.sidx(s, x)
    flag = ''
    # feed gap while holding (> FEED_GAP_MS between j and the exit decision)
    for m in range(j + 1, k + 1):
        if ts[m] - ts[m - 1] > H.FEED_GAP_MS:
            k = m - 1
            flag = 'GAP'
            break
    if flag == 'GAP':
        ev = H._gap_value_index(s, k)
        px, t_exit = s['price'][ev], ts[k + 1]
    else:
        e = H.fill_index(s, k)
        if e is not None:
            ev, px, t_exit = e, s['price'][e], ts[e]
        elif k + 1 < n and ts[k + 1] - ts[k] > H.FEED_GAP_MS:
            ev = H._gap_value_index(s, k)
            px, t_exit, flag = s['price'][ev], ts[k + 1], 'GAP'
        elif k + 1 < n:
            ev, px, t_exit = k + 1, s['price'][k + 1], ts[k + 1]
        else:
            flag, px = H._close_px(s, k, C.T1)
            ev, t_exit = k, max(ts[k], x)
    r = C.legs(s, j, NOTIONAL, 'HOLD', s['price'][j], ev, px)
    if r is None:
        return None
    r.update({'te': ts[j], 'tx': t_exit, 'flag': flag, 'fee': H.fee_bps(s, j), 'liq': s['liq'][j]})
    return r


def outcome_B(s, d, t, hold, L):
    ts = s['t']
    xe = t + L * 1000
    cm = C.chain_mid_sol(s, d, xe)
    jb = C.sidx(s, xe)
    if cm is None or jb < 0 or xe - ts[jb] > 120_000:
        return None
    su = H.sol_usd(s, jb)
    if not su > 0:
        return None
    mid_e = cm[0] * su
    xx = xe + hold * 1000
    xf = xx + L * 1000
    kb = C.sidx(s, xf)
    et = d['et']
    a = bisect.bisect_right(et, xf - 60_000)
    b = bisect.bisect_right(et, xf + 120_000)
    flag = ''
    if b > a:      # seat still live around the exit: true chain mid
        cm2 = C.chain_mid_sol(s, d, xf)
        su2 = H.sol_usd(s, kb)
        if cm2 is None or not su2 > 0:
            return None
        ev, px, t_exit = kb, cm2[0] * su2, xf
    else:          # seat lost: DexScreener next refresh after the exit decision (fallback)
        k = C.sidx(s, xx)
        e = H.fill_index(s, k)
        if e is None:
            e = k + 1 if k + 1 < len(ts) else k
        ev, px, t_exit, flag = e, s['price'][e], max(ts[e], xf), 'fb'
    r = C.legs(s, jb, NOTIONAL, 'HOLD', mid_e, ev, px)
    if r is None:
        return None
    r.update({'te': xe, 'tx': t_exit, 'flag': flag, 'fee': H.fee_bps(s, jb), 'liq': s['liq'][jb]})
    return r


points = []
pump = [p for p in C.TAPE if p in ser and ser[p]['dex'] == 'pumpswap' and ser[p]['quote_sol'] == 1]
stat = {'decisions': 0, 'no_series': 0, 'guard_block': 0, 'kept': 0}
for pn, pair in enumerate(pump):
    s, d = ser[pair], C.TAPE[pair]
    et, av = d['et'], d['av']
    # decision moments: ingestion seconds of real-time swaps inside the dataset window
    dts = sorted({(a // 1000) * 1000 + 999 for e, a in zip(et, av) if a - e <= 60_000 and C.T0 <= a < C.T1})
    for t in dts:
        stat['decisions'] += 1
        i = C.sidx(s, t)
        if i < 0 or t - s['t'][i] > 60_000:
            stat['no_series'] += 1
            continue
        P = H.Past(s, i)
        rr = H.rug_reasons(P)
        if rr:
            stat['guard_block'] += 1
            continue
        f30 = C.flow(pair, t, 30, d)
        f60 = C.flow(pair, t, 60, d)
        m_now = C.ingested_mid_sol(s, d, t)
        m_60 = C.ingested_mid_sol(s, d, t - 60_000)
        tret60 = (m_now[0] / m_60[0] - 1) * 100 if (m_now and m_60 and m_60[0] > 0) else float('nan')
        su = H.sol_usd(s, i)
        row = {'pair': pair, 't': t, 'fee': P.fee_bps(), 'liq': P('liq'), 'su': su,
               'heat': C.heat_flags(P), 'interim': H.interim_rug_risk(P),
               'f30': f30, 'f60': f60, 'tret60': tret60,
               'mid_age': (t - m_now[1]) / 1000 if m_now else None,
               'A': {}, 'B2': {}, 'B5': {}}
        for hold in HOLDS:
            row['A'][hold] = outcome_A(s, t, hold)
            for L in LATS:
                row['B%d' % L][hold] = outcome_B(s, d, t, hold, L)
        points.append(row)
        stat['kept'] += 1
    if pn % 50 == 0:
        print('pool', pn, len(pump), 'points', len(points), round(time.time() - t00, 1), 's', flush=True)
print(stat)
with open(os.path.join(C.HERE, 'points.pkl'), 'wb') as fh:
    pickle.dump({'points': points, 'stat': stat, 'notional': NOTIONAL, 'holds': HOLDS, 'lats': LATS,
                 'cut': C.CUT, 't0': C.T0, 't1': C.T1}, fh, protocol=pickle.HIGHEST_PROTOCOL)
print('saved', len(points), round(time.time() - t00, 1), 's')
