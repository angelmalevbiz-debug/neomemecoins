"""TRAIN events: features at the earliest and latest decision point in the 2 h pre-drain window."""
import sys
from rugcommon import load_points

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
pts = load_points()
part = sys.argv[1] if len(sys.argv) > 1 else 'train'
ev = {}
for x in pts:
    if x['part'] == part and x['y2h']:
        ev.setdefault(x['pair'], []).append(x)
F = ['liq', 'mcap', 'lmc', 'age', 'pump', 'fee', 'reuse', 'bs5', 'bs1h', 'tx1h', 'vliq1h', 'usd_per_tx1h', 'pc1h', 'pc6h', 'pc24', 'src', 'score', 'interim']


def g(v):
    if isinstance(v, float):
        if v != v:
            return 'nan'
        return '%.3g' % v
    return str(v)


print('sym pair kind npts mins_first..last | ' + ' '.join(F))
from rugcommon import _series
for pair, v in sorted(ev.items(), key=lambda kv: (kv[1][0]['kind'], -kv[1][0]['liq'])):
    v.sort(key=lambda z: z['t'])
    a, b = v[0], v[-1]
    sym = (_series[pair]['sym'] or '')[:10]
    print(sym, pair[:8], a['kind'].replace('LIQ_FAST', 'LF').replace('PRICE_FAST', 'PF').replace('PRICE_SLOW', 'PS'), len(v),
          '%.0f..%.0f' % (a['mins_to_drain'], b['mins_to_drain']), '|', ' '.join(g(b[f]) for f in F))
