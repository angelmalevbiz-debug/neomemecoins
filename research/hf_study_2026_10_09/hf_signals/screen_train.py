"""TRAIN-ONLY feature screen (deciles) on the refresh-event table. PAPER research only. Never reads holdout rows."""
import os, pickle, sys, math
from common import HERE, fin
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
d = pickle.load(open(os.path.join(HERE, 'events.pkl'), 'rb'))
rows = [r for r in d['rows'] if r['split'] == 'train']
for r in rows:
    r['dnet'] = (r['db5'] - r['ds5']) if fin(r['db5']) and fin(r['ds5']) else float('nan')
    r['dvl'] = r['dv5'] / r['liq'] if fin(r['dv5']) else float('nan')
    r['tot5'] = r['b5'] + r['s5'] if fin(r['b5']) and fin(r['s5']) else float('nan')

UNIS = {
    'U95 fee<=95 liq>=50k': lambda r: r['liq'] >= 50_000 and r['fee'] <= 95,
    'U50 fee<=50 liq>=50k': lambda r: r['liq'] >= 50_000 and r['fee'] <= 50,
}
FEATS = ['r1', 'r2', 'r60', 'r120', 'r300', 'hi15', 'lo15', 'db5', 'ds5', 'dnet', 'dvl', 'bshare', 'vacc', 'turn',
         'tot5', 'dt_prev', 'mkt_r1_60', 'mkt_r60_120', 'mkt_r300_120']
OUTS = ['g120', 'n0_60', 'n50_60', 'n50_120', 'n50_180', 'n50_300']


def mean(xs):
    xs = [x for x in xs if x is not None and fin(x)]
    return (sum(xs) / len(xs), len(xs)) if xs else (float('nan'), 0)


for uname, uf in UNIS.items():
    U = [r for r in rows if uf(r)]
    print('=' * 100)
    print(uname, 'train events', len(U), 'pairs', len({r['pair'] for r in U}),
          'events/h %.0f' % (len(U) / 25.8))
    for heat in (None, False):
        V = U if heat is None else [r for r in U if not r['heat']]
        line = '  heat=%s n=%d |' % ('any' if heat is None else 'kept', len(V))
        for o in OUTS:
            m, k = mean([r[o] for r in V])
            line += ' %s %+.3f' % (o, 100 * m if o == 'g120' else m)
        print(line)
    base = {o: mean([r[o] for r in U])[0] for o in OUTS}
    for f in FEATS:
        V = sorted([r for r in U if fin(r[f])], key=lambda r: r[f])
        if len(V) < 1000:
            continue
        print('-- %s (n=%d)  cols: lo..hi | n pairs | g120%% n0_60 n50_60 n50_120 n50_180 n50_300 | heat%%' % (f, len(V)))
        for q in range(10):
            B = V[q * len(V) // 10:(q + 1) * len(V) // 10]
            lo, hi = B[0][f], B[-1][f]
            vals = [mean([r[o] for r in B])[0] for o in OUTS]
            print('  d%d %+10.4f..%+10.4f | %5d %3d | %+6.3f %+6.2f %+6.2f %+6.2f %+6.2f %+6.2f | %3.0f' % (
                q, lo, hi, len(B), len({r['pair'] for r in B}), 100 * vals[0], vals[1], vals[2], vals[3], vals[4],
                vals[5], 100 * sum(r['heat'] for r in B) / len(B)))
