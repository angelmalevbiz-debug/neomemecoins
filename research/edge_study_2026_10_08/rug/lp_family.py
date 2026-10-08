"""Fates of pools that ever showed liq/mcap >= 1.0 with liq >= $20k (LP_PULLABLE signature), by mint suffix."""
import collections, os, pickle, sys
from rugcommon import HERE, _series, T1, CUT

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
lab = {r['pair']: r for r in pickle.load(open(os.path.join(HERE, 'labels.pkl'), 'rb'))['rows']}
c = collections.Counter()
obs_min = collections.defaultdict(list)
lastliq = collections.defaultdict(list)
examples = collections.defaultdict(list)
for p, s in _series.items():
    if s['dex'] != 'pumpswap' or s['quote_sol'] != 1:
        continue
    k0 = None
    for i in range(len(s['t'])):
        liq, mc = s['liq'][i], s['mcap'][i]
        if liq >= 20_000 and mc > 0 and liq / mc >= 1.0:
            k0 = i
            break
    if k0 is None:
        continue
    r = lab[p]
    terminal = r['drain_t'] is not None and not all(r['events'][k][2] for k in r['kinds'])
    fate = 'drained' if terminal else ('vanished' if r['vanished'] else 'alive_at_end')
    pump = (s['mint'] or '').endswith('pump')
    period = 'train' if s['t'][k0] < CUT else 'holdout'
    key = (period, 'pump-suffix' if pump else 'other-mint', fate)
    c[key] += 1
    obs_min[key].append((s['t'][-1] - s['t'][k0]) / 60000)
    lastliq[key].append(s['liq'][-1])
    if len(examples[key]) < 4:
        examples[key].append('%s/%s liq_first %.0f lmc %.2f age %.0f' % ((s['sym'] or '')[:10], p[:8], s['liq'][k0], s['liq'][k0] / s['mcap'][k0], s['age'][k0]))
for k in sorted(c):
    xs = sorted(obs_min[k]); ls = sorted(lastliq[k])
    print(k, c[k], 'observed-after-signal median %.1f min' % xs[len(xs) // 2], 'last liq median %.0f' % ls[len(ls) // 2], examples[k])
