"""TRAIN-only univariate screen of context features inside the core dip set (dd900 -8..-25, clean)."""
import sys
sys.path.insert(0, (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r'/f2_meanrev')
from ana_common import *

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
cs = load_cands()
core = [c for c in cs if not c['rug'] and -0.25 <= c['dd900'] <= -0.08]
for c in core:
    c['br5'] = c['b5'] / (c['b5'] + c['s5']) if c['b5'] + c['s5'] > 0 else float('nan')
    c['b5chg'] = c['b5'] - nz(c['b5_60'], c['b5'])
    c['s5chg'] = c['s5'] - nz(c['s5_60'], c['s5'])
    c['br1h'] = c['b1h'] / (c['b1h'] + c['s1h']) if c['b1h'] + c['s1h'] > 0 else float('nan')
    c['v5_v1h'] = c['v5'] / (c['v1h'] / 12) if c['v1h'] > 0 else float('nan')
    c['logage'] = c['age']
    c['boosted'] = 1.0 if (int(c['src']) & 3) or nz(c['boost'], 0) > 0 else 0.0
    c['liq_mcap'] = c['liq'] / c['mcap'] if c['mcap'] > 0 else float('nan')
EX = [(15, -20, 60), (10, -12, 30)]
NCFG = 0
FEATS = ['liq', 'fee', 'age', 'mcap', 'liq_mcap', 'dd300', 'dd1800', 'dd3600', 'tmax900', 'tlow5', 'bounce5', 'p_ret60',
         'pre_trend', 'pc1h', 'pc6h', 'pc24', 'br5', 'b5', 's5', 'b5chg', 's5chg', 'br1h', 'v5', 'v5_v1h', 'score', 'risk', 'boosted']
for f in FEATS:
    vals = sorted(c[f] for c in core if c[f] == c[f])
    if len(vals) < 100:
        print(f, 'too few values', len(vals))
        continue
    t1, t2 = vals[len(vals) // 3], vals[2 * len(vals) // 3]
    print('== %s  tertile cuts %.4g / %.4g' % (f, t1, t2))
    groups = [('low', [c for c in core if c[f] == c[f] and c[f] <= t1]),
              ('mid', [c for c in core if c[f] == c[f] and t1 < c[f] <= t2]),
              ('high', [c for c in core if c[f] == c[f] and c[f] > t2])]
    for g, sub in groups:
        line = []
        for ex in EX:
            NCFG += 1
            st = stats(sub, *ex)
            if st:
                line.append('ex%s mean %6.2f med %6.2f pm %6.2f win %4.1f' % (ex[0], st['mean'], st['med'], st['pairmean'], st['win']))
        print('   %-5s n %5d pairs %3d | %s' % (g, len(sub), len({c['pair'] for c in sub}), ' | '.join(line)))
print('configs counted', NCFG)
