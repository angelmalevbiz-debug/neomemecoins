"""Is the fact-1 base rate for fee100-125 & liq>=250k (+7.69% / 60 min) survivorship-biased?

H.forward_net returns None when the pair's series ends before t+60 min (pair drained / vanished),
so drained pools drop out of the sample. Here every 1/min sample is kept: when the horizon point does
not exist, the position is valued at the pair's LAST point (harness VANISH haircut -10%) with the
uncapped constant-product exit used in econ.py (liquidity 0 -> proceeds 0).
"""
import bisect, sys
from rugcommon import H, _series
from econ import exit_value_real
import rug_guard as G

sys.stdout.reconfigure(encoding='utf-8', errors='replace')


def slice_ok(s, i):
    return s['liq'][i] >= 250_000 and H.fee_bps(s, i) >= 100


for label, guard in (('no guard', None), ('rug_guard_v1', G.rug_guard_v1)):
    kept, dropped, vals_fwd, vals_all = 0, 0, [], []
    pairs = set()
    for pair, s in _series.items():
        if s['dex'] != 'pumpswap' or s['quote_sol'] != 1:
            continue
        ts = s['t']
        last = -1e18
        for i in range(len(ts) - 1):
            if ts[i] - last < 60_000 or not slice_ok(s, i):
                continue
            last = ts[i]
            if guard is not None and guard(H.Past(s, i)):
                continue
            pairs.add(pair)
            v = H.forward_net(s, i, 3600)
            if ts[i + 1] - ts[i] > H.MAX_ENTRY_LAG_MS:
                continue
            f = H.entry_fill(s, i + 1, 200.0)
            if f is None:
                continue
            k = bisect.bisect_left(ts, ts[i] + 3_600_000, i + 2)
            if k < len(ts):
                real = 100 * (exit_value_real(s, k, f[0]) - 200 - f[1]) / 200
            else:
                ended = H.load()[1]['t1'] - ts[-1] > 600_000
                px = s['price'][-1] * (0.9 if ended else 1.0)
                real = 100 * (exit_value_real(s, len(ts) - 1, f[0], 0.0, px) - 200 - f[1]) / 200
            vals_all.append(real)
            if v is None:
                dropped += 1
            else:
                kept += 1
                vals_fwd.append(v)
    def st(xs):
        if not xs:
            return 'n=0'
        xs = sorted(xs)
        return 'n=%d mean %.2f median %.2f win%% %.1f p10 %.1f' % (len(xs), sum(xs) / len(xs), xs[len(xs) // 2], 100 * sum(1 for x in xs if x > 0) / len(xs), xs[len(xs) // 10])
    print('==', label, 'pairs', len(pairs))
    print('   forward_net (drops pairs that end before +60m):', st(vals_fwd), '| dropped samples', dropped)
    print('   all samples, ended pairs valued at last point (uncapped CP):', st(vals_all))
