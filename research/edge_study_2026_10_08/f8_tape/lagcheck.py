"""Does the DexScreener series price at our simulated fill already reflect the whale's on-chain impact?
Compares series priceNative around each large tape BUY with on-chain executed prices (SOL/token) from the tape."""
import bisect, sys
sys.path.insert(0, (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r'')
sys.path.insert(0, (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r'/f8_tape')
import harness as H
import tape_load as TL

series, meta = H.load()
T = TL.load()


def q(xs, ps=(0.1, 0.25, 0.5, 0.75, 0.9)):
    xs = sorted(xs)
    return [round(xs[min(len(xs) - 1, int(p * len(xs)))], 2) for p in ps] if xs else []


rows = []
for pair, d in T['tape'].items():
    s = series.get(pair)
    if s is None or s['dex'] != 'pumpswap' or s['quote_sol'] != 1:
        continue
    ts, pn = s['t'], s['pnative']
    et_l = d['et']
    for k, (et, av, side, w, sol, tok) in enumerate(zip(d['et'], d['av'], d['side'], d['w'], d['sol'], d['tok'])):
        if side <= 0 or not (sol >= 5) or tok <= 0 or et < meta['t0'] or et > meta['t1']:
            continue
        if av - et > 60_000:
            continue
        px = sol / tok   # executed SOL per token (average over the swap, incl. fee)
        i = bisect.bisect_left(ts, av)
        if i + 1 >= len(ts):
            continue
        # on-chain post-trade prices: swaps in (et, et+30s] (any availability; this is a diagnostic)
        post = [d['sol'][m] / d['tok'][m] for m in range(k + 1, min(len(et_l), k + 200))
                if et_l[m] <= et + 30_000 and d['tok'][m] > 0 and d['sol'][m] == d['sol'][m]]
        jb = bisect.bisect_right(ts, et - 5_000) - 1   # series point >= 5 s before the swap
        rows.append({'pre': pn[jb] / px if jb >= 0 and pn[jb] > 0 else None,
                     'dec': pn[i] / px if pn[i] > 0 else None,
                     'fill': pn[i + 1] / px if pn[i + 1] > 0 else None,
                     'fill_vs_post': (pn[i + 1] / (sorted(post)[len(post) // 2])) if post and pn[i + 1] > 0 else None,
                     'lag_dec_s': (ts[i] - et) / 1000, 'lag_fill_s': (ts[i + 1] - et) / 1000,
                     'imp': 2 * sol * (s['price'][i] / pn[i] if pn[i] > 0 else 0) / s['liq'][i] * 100 if s['liq'][i] > 0 else None})
print('large buys', len(rows))
for key in ('pre', 'dec', 'fill', 'fill_vs_post', 'lag_dec_s', 'lag_fill_s', 'imp'):
    xs = [r[key] for r in rows if r[key] is not None]
    print(key, 'n', len(xs), 'q10/25/50/75/90', q(xs))
# share of fills priced below the whale's own executed average price by more than half the modeled impact
bad = [r for r in rows if r['fill'] is not None and r['imp'] is not None and r['fill'] < 1 - 0.25 * r['imp'] / 100]
print('fills clearly below whale execution price (stale pre-impact quote):', len(bad), 'of', len(rows))

print('--- big-impact whales (imp >= 2%): series price change pre->dec and pre->fill vs modeled impact')
for r in rows:
    pass
big = [r for r in rows if r['imp'] is not None and r['imp'] >= 2 and r['pre'] and r['fill'] and r['dec']]
for r in sorted(big, key=lambda r: r['imp'])[:60]:
    print('imp %5.2f%%  dec/pre %+6.2f%%  fill/pre %+6.2f%%  lag_dec %5.1fs lag_fill %5.1fs' % (
        r['imp'], (r['dec'] / r['pre'] - 1) * 100, (r['fill'] / r['pre'] - 1) * 100, r['lag_dec_s'], r['lag_fill_s']))
xs = [(r['fill'] / r['pre'] - 1) * 100 / r['imp'] for r in big]
print('ratio (fill/pre move) / impact, q10/25/50/75/90:', q(xs), 'n', len(xs))
