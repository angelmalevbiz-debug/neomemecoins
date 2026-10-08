import collections, os, pickle, sys
from rugcommon import HERE, _series, T1, CUT
lab = {r['pair']: r for r in pickle.load(open(os.path.join(HERE, 'labels.pkl'), 'rb'))['rows']}
for th in (5, 10, 30, 60):
    c = collections.Counter()
    for p, s in _series.items():
        if s['dex'] != 'pumpswap' or s['quote_sol'] != 1:
            continue
        k0 = next((i for i in range(len(s['t'])) if s['liq'][i] >= 20_000 and s['mcap'][i] > 0 and s['liq'][i] / s['mcap'][i] >= 1.0), None)
        if k0 is None:
            continue
        r = lab[p]
        terminal = r['drain_t'] is not None and not all(r['events'][k][2] for k in r['kinds'])
        end_t = r['drain_t'] if terminal else s['t'][-1]
        if (end_t - s['t'][k0]) / 60000 < th and not terminal:
            continue
        # drained within th minutes counts too (it was observed until the drain)
        fate = 'drained' if terminal else ('vanished' if r['vanished'] else 'alive_at_end')
        c[fate] += 1
    print('LP-signature pools observed >= %d min after first signal (or drained):' % th, dict(c))
