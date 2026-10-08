"""Second TRAIN search pass (same 4,480-config grid, counted again) with an economic objective.

Why a second pass: in pass 1 the 2 h label counted members of the fake-market-cap family that drained
later than 2 h (or were still alive at the end) as false positives, so a recall-vs-pool-FPR objective
could not admit the FAKE atom although every train drain in the cost-first slice is from that family.
Objective O2 (declared before running this pass):
  universe LIQ20 (liq >= $20k) train points. retention = share of clean 2 h negatives NOT flagged.
  residual = 2 h drain rate among UNFLAGGED points (positives / (positives + clean negatives)).
  C_struct : no YOUNG atom, retention >= 90%, minimise residual.
  C_young  : any atoms, retention >= 80%, minimise residual.
  ties (residual rounded to 4 dp) -> fewer atoms -> higher retention.
"""
import itertools, json, os, sys
from rugcommon import load_points, HERE

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
pts = [x for x in load_points() if x['part'] == 'train']
D14 = 14 * 1440


def mask(pred, base=None):
    m = 0
    for k, x in enumerate(pts):
        if pred(x):
            m |= 1 << k
    return m


U = mask(lambda x: x['liq'] >= 20_000)
POS = mask(lambda x: x['y2h'] == 1) & U
NEG = mask(lambda x: x['clean_neg']) & U
CF = mask(lambda x: x['liq'] >= 250_000 and x['fee'] <= 50)
npos, nneg = POS.bit_count(), NEG.bit_count()
print('LIQ20 train pos', npos, 'neg', nneg, 'base 2h drain rate %.4f' % (npos / (npos + nneg)))

atoms = {
    'LP': {None: 0, **{L: mask(lambda x, L=L: x['lmc'] >= L) for L in (0.6, 0.8, 1.0, 1.2)}},
    'FAKE': {None: 0, **{(L, M): mask(lambda x, L=L, M=M: x['lmc'] < L and x['mcap'] >= M and x['age'] < D14)
                         for L in (0.01, 0.02, 0.03) for M in (5e6, 20e6)}},
    'REUSE': {None: 0, **{R: mask(lambda x, R=R: x['reuse'] >= R and x['age'] < D14) for R in (1, 3, 6)}},
    'YOUNG': {None: 0, **{A: mask(lambda x, A=A: x['age'] < A) for A in (15, 30, 60, 120, 360, 720, 1440)}},
    'IMBAL': {None: 0, **{B: mask(lambda x, B=B: x['bs1h'] >= B and x['age'] < D14) for B in (5, 10, 20)}},
}
names = list(atoms)
res = []
for combo in itertools.product(*[list(atoms[n].items()) for n in names]):
    m, cfg = 0, {}
    for n, (th, am) in zip(names, combo):
        cfg[n] = th
        m |= am
    fp = (m & NEG).bit_count()
    tp = (m & POS).bit_count()
    keep_pos, keep_neg = npos - tp, nneg - fp
    residual = keep_pos / max(1, keep_pos + keep_neg)
    cf_pos = (m & POS & CF).bit_count(); cf_all_pos = (POS & CF).bit_count()
    res.append((cfg, {'retention': keep_neg / nneg, 'residual': residual, 'recall_pts': tp / npos,
                      'precision': tp / max(1, tp + fp), 'cf_recall': cf_pos / max(1, cf_all_pos),
                      'n_atoms': sum(1 for n in names if cfg[n] is not None)}))
print('configs_tried (this pass)', len(res), '; cumulative with pass 1:', 4480 + len(res))


def pick(min_ret, allow_young):
    ok = [(c, s) for c, s in res if s['retention'] >= min_ret and (allow_young or c['YOUNG'] is None)]
    ok.sort(key=lambda cs: (round(cs[1]['residual'], 4), cs[1]['n_atoms'], -cs[1]['retention']))
    return ok[:10]


out = {}
for label, ret, young in (('C_struct', 0.90, False), ('C_young', 0.80, True)):
    print('==', label, 'retention >=', ret)
    top = pick(ret, young)
    for c, s in top:
        print('  ', json.dumps(c, default=str), {k: round(v, 4) for k, v in s.items()})
    out[label] = [[c, s] for c, s in top]
# Pareto front for the report (retention vs residual)
front, best = [], 9
for c, s in sorted(res, key=lambda cs: -cs[1]['retention']):
    if s['residual'] < best - 1e-9:
        best = s['residual']
        front.append((c, s))
print('== Pareto front (retention desc, residual strictly improving)')
for c, s in front:
    print('  ', json.dumps(c, default=str), {k: round(v, 4) for k, v in s.items()})
json.dump(out, open(os.path.join(HERE, 'search2_train.json'), 'w'), default=str, indent=1)
