"""seats: achievable trade rate vs number of tape seats (4 / 8 / 16). PAPER research only.

1. Calibrate on SEATED pool-minutes (TRAIN only): trades per seated pool-minute of a single-pool 60 s time-exit book
   (unlimited slots, one position per pool) as a function of the pool's DexScreener activity txns5 = b5 + s5.
2. For every minute, rank the eligible universe pools (guarded, PumpSwap SOL, fee <= 95, liq >= $50k, optional
   heat veto) by txns5 (what an engine-aware seat scheduler could do), take the top N, and sum the calibrated rates.
3. Validate: the same prediction applied to the pools that were actually seated vs the achieved book rate."""
import bisect, json, os, sys, time
from collections import defaultdict

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
t00 = time.time()
import hfcommon as C
H = C.H
import booklib as B

ser = C.series
BUCKETS = (0, 5, 15, 30, 60, 120, 1e18)


def bk(x):
    for k in range(len(BUCKETS) - 1):
        if BUCKETS[k] <= x < BUCKETS[k + 1]:
            return k
    return 0


# ---------------------------------------------------------------- per (pool, minute) universe state from the series
m0, m1 = int(C.T0 // 60000), int(C.T1 // 60000)
state = defaultdict(dict)   # minute -> pool -> (txns5, heat_bool)
for pair, s in ser.items():
    if s['dex'] != 'pumpswap' or s['quote_sol'] != 1:
        continue
    ts = s['t']
    last_m = None
    for i in range(len(ts)):
        m = int(ts[i] // 60000)
        if m == last_m:
            continue
        last_m = m
        liq = s['liq'][i]
        if not liq >= 50_000:
            continue
        P = H.Past(s, i)
        if P.fee_bps() > 95 or H.rug_guard_v1(P):
            continue
        b5, s5 = s['b5'][i], s['s5'][i]
        tx = (b5 if b5 == b5 else 0) + (s5 if s5 == s5 else 0)
        state[m][pair] = (tx, bool(C.heat_flags(P)))
print('universe state built', len(state), 'minutes', round(time.time() - t00, 1), 's', flush=True)

# seated pools per minute (real-time ingested events)
seated = defaultdict(set)
for p, d in C.TAPE.items():
    for et, av in zip(d['et'], d['av']):
        if av - et <= 60_000 and C.T0 <= av < C.T1:
            seated[int(av // 60000)].add(p)

# ---------------------------------------------------------------- calibration from book trades (TRAIN)
D = B.data()
PTS = sorted(D['points'], key=lambda p: (p['t'], p['pair']))


def sig_c1(p):
    f = p['f60']
    return f is not None and f['ub'] >= 2 and f['net_sol'] > 0 and p['tret60'] <= 2.0


designs = {
    'ALL_heatoff': (lambda p: True, False),
    'ALL_heatveto': (lambda p: True, True),
    'C1_burst_heatoff': (sig_c1, False),
}
res = {}
for name, (sf, veto) in designs.items():
    U = [p for p in PTS if p['liq'] >= 50_000 and p['fee'] <= 95 and (not veto or not p['heat']) and sf(p)]
    tr, _ = B.book(U, 'B2', 60, slots=1000)
    per_pm = defaultdict(int)
    for x in tr:
        per_pm[(x['pair'], int(x['t'] // 60000))] += 1
    # calibration table on TRAIN seated pool-minutes in the universe
    num = defaultdict(float)
    den = defaultdict(int)
    for m in range(m0, int(C.CUT // 60000)):
        for pair in seated.get(m, ()):
            st = state.get(m, {}).get(pair)
            if st is None or (veto and st[1]):
                continue
            k = bk(st[0])
            num[k] += per_pm.get((pair, m), 0)
            den[k] += 1
    rate = {k: (num[k] / den[k] if den[k] else 0.0) for k in range(len(BUCKETS) - 1)}
    print('\n==', name, 'calibration (TRAIN seated pool-minutes): trades per pool-minute by txns5 bucket')
    for k in range(len(BUCKETS) - 1):
        print('   txns5 [%s, %s): pool-minutes %d  trades/pool-min %.3f' % (BUCKETS[k], BUCKETS[k + 1], den[k], rate[k]))

    def project(lo, hi):
        out = {}
        mins = [m for m in range(m0, m1) if lo <= m * 60000 < hi]
        for N in (4, 8, 16, 32):
            tot = 0.0
            for m in mins:
                el = [(st[0], pair) for pair, st in state.get(m, {}).items() if not (veto and st[1])]
                el.sort(reverse=True)
                tot += sum(rate[bk(x[0])] for x in el[:N])
            out['top%d' % N] = round(60 * tot / len(mins), 1)
        # validation: predicted for the actually seated universe pools vs achieved
        pred = ach = 0.0
        for m in mins:
            for pair in seated.get(m, ()):
                st = state.get(m, {}).get(pair)
                if st is None or (veto and st[1]):
                    continue
                pred += rate[bk(st[0])]
                ach += per_pm.get((pair, m), 0)
        out['seated_pred'] = round(60 * pred / len(mins), 1)
        out['seated_achieved_unlimited_slots'] = round(60 * ach / len(mins), 1)
        # eligible pool counts
        cnt = defaultdict(list)
        for m in mins:
            el = [st[0] for pair, st in state.get(m, {}).items() if not (veto and st[1])]
            cnt['eligible'].append(len(el))
            cnt['txns5>=15'].append(sum(1 for x in el if x >= 15))
            cnt['txns5>=60'].append(sum(1 for x in el if x >= 60))
        out['mean_eligible_pools'] = {k: round(sum(v) / len(v), 2) for k, v in cnt.items()}
        return out
    res[name] = {'rate': rate, 'train': project(C.T0, C.CUT), 'holdout': project(C.CUT, C.T1)}
    print('   projection trades/hour (60 s hold, slots = seats):')
    print('   TRAIN  ', json.dumps(res[name]['train']))
    print('   HOLDOUT', json.dumps(res[name]['holdout']))
json.dump(res, open(os.path.join(C.HERE, 'seats.json'), 'w'), indent=1)
print('done', round(time.time() - t00, 1), 's')
