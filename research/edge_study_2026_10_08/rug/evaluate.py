"""Train vs HOLDOUT precision / recall for the 3 pre-selected guards (+ references). Run once.

Pre-selected on train (before this script was run):
  G1 rug_guard_v1          LP_PULLABLE | YOUNG_POOL(<720 min) | FAKE_MCAP(lmc<1%, mcap>=$20M, age<14d)
  G2 rug_guard_structural  LP_PULLABLE | FAKE_MCAP
  G3 young_only            YOUNG_POOL(<720 min)
References (not selected by this study): interim_rug_risk (thresholds set on the full 22.8 h, so its
holdout is contaminated), engine flags 'unnatural buy imbalance' (b5/max(s5,1) > 5) and 'weak liquidity/MC'
(liq/mcap < 3%).
"""
import collections, json, os, pickle, sys
from rugcommon import load_points, metrics, fmt_metrics, UNIVERSES, _series, HERE, H
import rug_guard as G

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
pts = load_points()
for x in pts:
    P = H.Past(_series[x['pair']], x['i'])
    x['G1'] = G.rug_guard_v1(P)
    x['G2'] = G.rug_guard_structural(P)
    x['G3'] = G.young_only(P)
    x['INTERIM'] = bool(x['interim'])
    x['ENG_IMBAL'] = x['bs5'] > 5
    x['ENG_WEAKLMC'] = x['lmc'] < 0.03
    x['NONE'] = False
FLAGS = ['G1', 'G2', 'G3', 'INTERIM', 'ENG_IMBAL', 'ENG_WEAKLMC']
res = {}
for part in ('train', 'holdout'):
    for uni in UNIVERSES:
        for f in FLAGS:
            m = metrics(pts, lambda x, f=f: x[f], part, uni)
            res['%s|%s|%s' % (part, uni, f)] = m
            print(part, uni, f, fmt_metrics(m))
        print()

# pool-level 'ever' view: drained pools flagged at >= 1 point before the drain vs never-drained pools flagged
lab = {r['pair']: r for r in pickle.load(open(os.path.join(HERE, 'labels.pkl'), 'rb'))['rows']}
print('== pool-level (liq >= $50k points only), per part of the FIRST such point')
for part in ('train', 'holdout'):
    for f in FLAGS:
        dr = collections.defaultdict(list); nd = collections.defaultdict(list)
        for x in pts:
            if x['part'] != part or x['liq'] < 50_000:
                continue
            r = lab[x['pair']]
            terminal = r['drain_t'] is not None and not all(r['events'][k][2] for k in r['kinds'])
            (dr if terminal else nd)[x['pair']].append(x[f])
        a = sum(1 for v in dr.values() if any(v)); b = sum(1 for v in nd.values() if any(v))
        a_all = sum(1 for v in dr.values() if all(v)); b_mean = sum(sum(v) / len(v) for v in nd.values()) / max(1, len(nd))
        print('  %s %-11s drained pools %3d flagged-ever %3d flagged-always %3d | never-drained pools %3d flagged-ever %3d mean share flagged %.3f' % (
            part, f, len(dr), a, a_all, len(nd), b, b_mean))
json.dump(res, open(os.path.join(HERE, 'evaluate_results.json'), 'w'), indent=1)
pickle.dump([{k: x[k] for k in ('pair', 't', 'i', 'part', 'y2h', 'y1h', 'clean_neg', 'van2h', 'cens', 'liq', 'fee', 'kind', 'G1', 'G2', 'G3', 'INTERIM')} for x in pts],
            open(os.path.join(HERE, 'flags.pkl'), 'wb'))
