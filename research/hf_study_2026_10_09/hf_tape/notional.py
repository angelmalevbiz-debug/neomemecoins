"""notional: zero-move round-trip cost (model net0 / calibrated netcal / acceptance net50) of a scalp at different
notionals, measured at holdout decision points of the C3/C4 universes (guarded, heat-vetoed, fee <= 95 / <= 50,
liq >= $50k). Gross 60 s moves average ~0, so this is the per-trade loss floor. Also: how many trades a $100/day
loss cap allows. PAPER research only."""
import json, os, random, sys

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hfcommon as C
H = C.H
import booklib as B

D = B.data()
rnd = random.Random(9)
for name, feemax in (('C3_le95_veto', 95), ('C4_le50_veto', 50)):
    U = [p for p in D['points'] if p['t'] >= D['cut'] and p['liq'] >= 50_000 and p['fee'] <= feemax and not p['heat']]
    U = rnd.sample(U, min(3000, len(U)))
    print('==', name, 'holdout points sampled', len(U))
    for N in (10, 25, 50, 100, 200, 500):
        acc = [0.0, 0.0, 0.0]
        n = 0
        for p in U:
            s = C.series[p['pair']]
            j = C.sidx(s, p['t'] + 2000)
            if j < 0:
                continue
            old = H.calib_extra_bps_per_leg
            # legs() takes the notional from the caller
            r = C.legs(s, j, float(N), 'HOLD', s['price'][j], j, s['price'][j])
            if r is None:
                continue
            for k, key in enumerate(('net0', 'netcal', 'net50')):
                acc[k] += r[key]
            n += 1
        m0, mc, m50 = (a / n for a in acc)
        usd50 = -N * m50 / 100
        print('  $%4d  net0 %6.2f%%  netcal %6.2f%%  net50 %6.2f%%  -> $ loss/trade net50 %.3f (net0 %.3f); trades allowed by a $100/day cap: %d; $/hour at 50 trades/h: %.1f' % (
            N, m0, mc, m50, usd50, -N * m0 / 100, int(100 / usd50), 50 * usd50))
