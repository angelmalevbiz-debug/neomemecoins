"""TRAIN-only: GROSS forward price returns (no costs) after dips vs unconditional, to see if any reversion exists."""
import bisect, sys
sys.path.insert(0, (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r'/f2_meanrev')
from ana_common import *

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
cs = load_cands()
ok = [c for c in cs if not c['rug']]
cut = H.split_t(meta)
HZ = (60, 300, 900, 1800, 3600)


def fwd(s, j, sec):
    ts = s['t']
    k = bisect.bisect_left(ts, ts[j] + sec * 1000, j + 1)
    if k >= len(ts):
        return None
    return 100 * (s['price'][k] / s['price'][j] - 1)


def row(label, sub):
    out = []
    for h in HZ:
        xs = []
        for c in sub:
            s = series[c['pair']]
            v = fwd(s, c['j'], h)
            if v is not None:
                xs.append(max(-100, v))
        if xs:
            xs.sort()
            out.append('%ds: mean %6.2f med %6.2f (n %d)' % (h, sum(xs) / len(xs), xs[len(xs) // 2], len(xs)))
    pairs = len({c['pair'] for c in sub})
    print('%-48s pairs %3d | %s' % (label, pairs, ' | '.join(out)))


# unconditional baseline on the same clean pairs: every ~60 s, liq>=50k, entry at i+1
cpairs = {c['pair'] for c in ok}
base = []
for p in cpairs:
    s = series[p]
    ts = s['t']
    last = -1e18
    for i in range(len(ts) - 1):
        if ts[i] >= cut:
            break
        if ts[i] - last < 60_000 or not (s['liq'][i] >= 50_000):
            continue
        last = ts[i]
        base.append({'pair': p, 'j': i + 1})
row('UNCONDITIONAL same pairs (1/min)', base)
row('dip any (dd<=-6%)', ok)
for lo, hi in ((-0.12, -0.06), (-0.18, -0.12), (-0.25, -0.18), (-1, -0.25)):
    row('dd900 in (%s,%s]' % (lo, hi), [c for c in ok if lo < c['dd900'] <= hi])
for lo, hi in ((-0.12, -0.06), (-0.2, -0.12), (-1, -0.2)):
    row('dd300 in (%s,%s]' % (lo, hi), [c for c in ok if lo < c['dd300'] <= hi])
for lo, hi in ((-1, -0.08), (-0.08, -0.04), (-0.04, -0.01), (-0.01, 0.01), (0.01, 1)):
    row('p_ret60 in (%s,%s]' % (lo, hi), [c for c in ok if lo < c['p_ret60'] <= hi])
row('knife tlow5<10', [c for c in ok if c['tlow5'] < 10])
row('stab tlow5>=60 & bounce5 1-4%', [c for c in ok if c['tlow5'] >= 60 and 0.01 <= c['bounce5'] < 0.04])
