import time
import harness as H
from smoke_common import cost_first, rnd

series, meta = H.load()
tr = H.simulate(lambda P: cost_first(P) and rnd(0.01)(P), stop=-5, tp=10, hold_min=60,
                size_fn=lambda P: min(200.0, P('liq') * 0.001), tag='random_cf')
tr.sort(key=lambda x: x['net0'])
print('worst trades:')
for x in tr[:12]:
    s = series[x['pair']]
    ts = s['t']
    import bisect
    a = bisect.bisect_left(ts, x['entry_t'])
    b = bisect.bisect_left(ts, x['exit_t'])
    path = [round(s['price'][k] / s['price'][a] * 100 - 100, 2) for k in range(a, min(b + 1, a + 400), max(1, (b - a) // 12 or 1))]
    print(x['sym'], x['pair'][:8], 'net0', round(x['net0'], 2), x['reason'], x['flag'], 'hold_min', round(x['hold_s'] / 60, 1),
          'liq', round(x['liq']), 'liq_exit', round(s['liq'][b]), 'fee', x['fee_bps'], 'mae', round(x['mae'], 2), 'path%', path)
print('best trades:')
for x in tr[-5:]:
    print(x['sym'], x['pair'][:8], 'net0', round(x['net0'], 2), x['reason'], 'mfe', round(x['mfe'], 2))
