"""Fact-1 base rates (forward 60 min net, $200, 1 sample/min/pair) with and without the dropped samples.

forward_net drops samples whose pair series ends before +60 min. Here they are kept and valued at the
pair's last point (-10% vanish haircut when the pair left the feed >10 min before the dataset end) with
the uncapped constant-product exit (liquidity 0 -> proceeds 0). Also shown: the same with rug_guard_v1.
"""
import bisect, collections, sys
from rugcommon import H, _series, META
from econ import exit_value_real
import rug_guard as G

sys.stdout.reconfigure(encoding='utf-8', errors='replace')


def bucket(s, i):
    fee, liq = H.fee_bps(s, i), s['liq'][i]
    fb = 'fee<=50' if fee <= 50 else ('fee55-95' if fee <= 95 else 'fee100-125')
    lb = 'liq>=250k' if liq >= 250_000 else ('liq50-250k' if liq >= 50_000 else 'liq<50k')
    return fb + ' ' + lb


acc = collections.defaultdict(lambda: {'fwd': [], 'all': [], 'drop': 0, 'pairs': set()})
for pair, s in _series.items():
    if s['dex'] != 'pumpswap' or s['quote_sol'] != 1:
        continue
    ts = s['t']
    last = -1e18
    for i in range(len(ts) - 1):
        if ts[i] - last < 60_000:
            continue
        last = ts[i]
        if ts[i] + 3_600_000 > META['t1']:
            continue  # horizon beyond dataset end: not a survivorship case
        if ts[i + 1] - ts[i] > H.MAX_ENTRY_LAG_MS:
            continue
        f = H.entry_fill(s, i + 1, 200.0)
        if f is None:
            continue
        b = bucket(s, i)
        guarded = G.rug_guard_v1(H.Past(s, i))
        k = bisect.bisect_left(ts, ts[i] + 3_600_000, i + 2)
        v = H.forward_net(s, i, 3600)
        if k < len(ts):
            real = 100 * (exit_value_real(s, k, f[0]) - 200 - f[1]) / 200
        else:
            px = s['price'][-1] * 0.9
            real = 100 * (exit_value_real(s, len(ts) - 1, f[0], 0.0, px) - 200 - f[1]) / 200
        for key in ((b, 'none'),) + (((b, 'G1'),) if not guarded else ()):
            a = acc[key]
            a['all'].append(real)
            a['pairs'].add(pair)
            if v is None:
                a['drop'] += 1
            else:
                a['fwd'].append(v)


def st(xs):
    if not xs:
        return 'n=0'
    xs = sorted(xs)
    return 'n=%5d mean %7.2f median %6.2f win%% %5.1f p10 %7.1f' % (len(xs), sum(xs) / len(xs), xs[len(xs) // 2], 100 * sum(1 for x in xs if x > 0) / len(xs), xs[len(xs) // 10])


for key in sorted(acc):
    a = acc[key]
    print('%-22s %-4s pairs %4d | forward_net only: %s | all samples incl. %4d ended: %s' % (key[0], key[1], len(a['pairs']), st(a['fwd']), a['drop'], st(a['all'])))
