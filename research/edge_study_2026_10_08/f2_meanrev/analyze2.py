"""TRAIN-only: feature distributions and stabilization buckets inside the clean dip set."""
import sys
sys.path.insert(0, (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r'/f2_meanrev')
from ana_common import *

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
cs = load_cands()
ok = [c for c in cs if not c['rug']]


def q(xs, qs=(.01, .1, .25, .5, .75, .9, .99)):
    xs = sorted(x for x in xs if x == x)
    if not xs:
        return 'empty'
    return [round(xs[int(len(xs) * p)], 3) for p in qs] + ['n=%d' % len(xs)]


for f in ('lp15', 'lp5', 'dd300', 'dd900', 'dd1800', 'dd3600', 'bounce5', 'tlow5', 'p_ret30', 'p_ret60', 'pre_trend', 'age', 'pc5', 'b5', 's5', 'vf_buy', 'sf_buy', 'hp_uw5'):
    print(f, q([c[f] for c in ok]))

EXITS = [(5, -8, 15), (10, -12, 30), (15, -20, 60)]
NCFG = 0


def show(label, sub):
    global NCFG
    print('--', label)
    for ex in EXITS:
        NCFG += 1
        print('   exit tp=%s stop=%s hold=%s  ' % ex, fmt(stats(sub, *ex)))


# depth of the 15-min drawdown
for lo, hi in ((-0.08, -0.06), (-0.12, -0.08), (-0.18, -0.12), (-0.25, -0.18), (-0.40, -0.25), (-1, -0.40)):
    show('dd900 in (%s, %s]' % (lo, hi), [c for c in ok if lo < c['dd900'] <= hi])
# falling knife vs stabilised (within dd900 -8..-25)
core = [c for c in ok if -0.25 <= c['dd900'] <= -0.08]
show('core dd900 -8..-25 ALL', core)
show('KNIFE: at the 5m low (tlow5 < 10 s)', [c for c in core if c['tlow5'] < 10])
show('low 10-60 s old', [c for c in core if 10 <= c['tlow5'] < 60])
show('low 60-180 s old', [c for c in core if 60 <= c['tlow5'] < 180])
show('low >= 180 s old', [c for c in core if c['tlow5'] >= 180])
show('bounce5 < 1%', [c for c in core if c['bounce5'] < 0.01])
show('bounce5 1-3%', [c for c in core if 0.01 <= c['bounce5'] < 0.03])
show('bounce5 3-6%', [c for c in core if 0.03 <= c['bounce5'] < 0.06])
show('bounce5 >= 6%', [c for c in core if c['bounce5'] >= 0.06])
show('sells falling (s5 < s5_60)', [c for c in core if c['s5'] < nz(c['s5_60'], -1)])
show('sells not falling', [c for c in core if not (c['s5'] < nz(c['s5_60'], -1))])
show('buy ratio5 >= 0.5', [c for c in core if c['b5'] + c['s5'] > 0 and c['b5'] / (c['b5'] + c['s5']) >= 0.5])
show('buy ratio5 < 0.5', [c for c in core if c['b5'] + c['s5'] > 0 and c['b5'] / (c['b5'] + c['s5']) < 0.5])
show('pre_trend >= +20% (dip after a pump)', [c for c in core if nz(c['pre_trend'], 0) >= 0.2])
show('pre_trend 0..20%', [c for c in core if 0 <= nz(c['pre_trend'], 0) < 0.2])
show('pre_trend < 0 (downtrend)', [c for c in core if nz(c['pre_trend'], 0) < 0])
show('pc24 > 0', [c for c in core if nz(c['pc24'], 0) > 0])
show('pc24 <= 0', [c for c in core if nz(c['pc24'], 0) <= 0])
print('configs counted', NCFG)
