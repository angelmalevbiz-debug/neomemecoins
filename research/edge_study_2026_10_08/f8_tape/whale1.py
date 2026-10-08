"""TRAIN ONLY: whale-buy arrival episodes, deduplicated per pair (first qualifying buy, then 10-min quiet)."""
import bisect, pickle, sys
sys.path.insert(0, (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r'')
import harness as H

series, meta = H.load()
cut = H.split_t(meta)
buys = pickle.load(open((__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r'/f8_tape/buys.pkl', 'rb'))
buys.sort(key=lambda b: b['av'])
for b in buys:
    s = series[b['pair']]
    su = H.sol_usd(s, b['i'])
    b['usd'] = b['sol'] * su
    b['bliq'] = b['usd'] / b['liq'] * 100 if b['liq'] > 0 else float('nan')


def path(s, i, horizon_s=3600):
    """model-net path stats from fill at i+1: mfe, mae, first-hit for -5/+10 and -10/+20."""
    ts = s['t']
    if i + 1 >= len(ts):
        return None
    e = H.entry_fill(s, i + 1, 200.0)
    if e is None:
        return None
    qty, nf = e
    mfe, mae, h1, h2 = -1e9, 1e9, None, None
    for k in range(i + 2, len(ts)):
        if ts[k] - ts[i + 1] > horizon_s * 1000:
            break
        v = H.exit_value(s, k, qty)
        if v is None:
            continue
        net = 100 * (v - 200 - nf) / 200
        mfe, mae = max(mfe, net), min(mae, net)
        if h1 is None and net <= -5:
            h1 = 'S'
        if h1 is None and net >= 10:
            h1 = 'T'
        if h2 is None and net <= -10:
            h2 = 'S'
        if h2 is None and net >= 20:
            h2 = 'T'
    return round(mfe, 1), round(mae, 1), h1, h2


for thr_name, cond in (('sol>=5', lambda b: b['sol'] >= 5), ('usd>=1000', lambda b: b['usd'] >= 1000),
                       ('bliq>=1%', lambda b: b['bliq'] >= 1)):
    last = {}
    eps = []
    for b in buys:
        if b['t'] >= cut or b['rug'] or not cond(b):
            continue
        if b['av'] - last.get(b['pair'], -1e18) < 600_000:
            continue
        last[b['pair']] = b['av']
        eps.append(b)
    print('==', thr_name, 'episodes (train, rug-screened)', len(eps), 'pairs', len(set(b['pair'] for b in eps)))
    for h in ('n60', 'n300', 'n900'):
        xs = sorted(b[h] for b in eps if b[h] is not None)
        if xs:
            print('   ', h, 'n', len(xs), 'mean %.2f med %.2f win %.1f' % (sum(xs) / len(xs), xs[len(xs) // 2], 100 * sum(1 for x in xs if x > 0) / len(xs)))
    hits = {}
    for b in eps:
        p = path(series[b['pair']], b['i'])
        b['path'] = p
        if p:
            hits[p[2]] = hits.get(p[2], 0) + 1
    print('    -5/+10 first hits', hits)
    if thr_name == 'sol>=5':
        for b in eps:
            s = series[b['pair']]
            print('     %s %-10s h%5.2f sol %6.2f usd %6.0f bliq %5.2f fee %5.1f liq %7.0f age %7.0f n300 %7.2f n900 %7.2f path %s' % (
                b['pair'][:8], (s['sym'] or '')[:10], (b['t'] - meta['t0']) / 3.6e6, b['sol'], b['usd'], b['bliq'], b['fee'], b['liq'],
                s['age'][b['i']], b['n300'] if b['n300'] is not None else float('nan'),
                b['n900'] if b['n900'] is not None else float('nan'), b['path']))
