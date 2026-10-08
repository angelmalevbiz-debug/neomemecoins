"""Sensitivity (NOT selection): frozen configs under alternative exit-fill model; MIMI trade anatomy."""
import bisect, sys
sys.path.insert(0, (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r'')
sys.path.insert(0, (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r'/f8_tape')
import harness as H
import strategies as ST
import sig as S, tsim, tape_load as TL, onchain as OC

series, meta = H.load()
cut = H.split_t(meta)
PAIRS = set(TL.load()['tape'].keys())
keep = ('n', 'pairs', 'win_rate', 'mean_pct', 'median_pct', 'sum_usd', 'pf', 'ci95_mean_usd', 'top_pair_share')
facs = [('F8_WHALE_FOLLOW_5SOL', lambda: S.whale_signal(min_sol=5.0, side=1, **ST.COMMON), ST.EXIT_TRAIL),
        ('F8_BIGSELL_DIPBUY_5SOL', lambda: S.whale_signal(min_sol=5.0, side=-1, **ST.COMMON), ST.EXIT_WIDE),
        ('F8_BIGSELL_DIPBUY_3SOL', lambda: S.whale_signal(min_sol=3.0, side=-1, **ST.COMMON), ST.EXIT_WIDE)]
for name, fac, ex in facs:
    for em in ('series', 'max_conservative'):
        mode = 'series' if em == 'series' else 'min'
        tr = tsim.simulate(fac(), pairs=PAIRS, entry_mode='onchain' if em == 'series' else 'max', exit_mode=mode, **ex)
        ev = H.evaluate(tr)
        print(name, 'entry', 'onchain' if em == 'series' else 'max(series,onchain)', 'exit', mode)
        print('   train  ', {k: ev['train'].get(k) for k in keep})
        print('   holdout', {k: ev['holdout'].get(k) for k in keep})
# anatomy of the MIMI dip-buy trade
tr = ST.CONFIGS[1]['run'](H)
for x in tr:
    if (x['sym'] or '').startswith('MIMI'):
        s = series[x['pair']]
        ts = s['t']
        a = bisect.bisect_left(ts, x['entry_t'])
        b = bisect.bisect_left(ts, x['exit_t'])
        print('MIMI', x['pair'][:8], 'entry series px %.3g used %.3g' % (x['entry_px_series'], x['entry_px_used']), 'reason', x['reason'],
              'net50 %.1f mfe %.1f mae %.1f hold %.0fs' % (x['net50'], x['mfe'], x['mae'], x['hold_s']))
        for k in range(max(a, b - 3), min(len(ts), b + 2)):
            oc = OC.onchain_price_usd(s, k, ts[k], 30)
            print('   t+%5.0fs series %.4g onchain %s liq %.0f' % ((ts[k] - x['entry_t']) / 1000, s['price'][k], ('%.4g' % oc) if oc else None, s['liq'][k]))
