"""Step (b) rule search on TRAIN points only (bitset grid search, every configuration counted).

Rule form (fixed before searching): flag = OR of up to five atoms, each atom on/off with its own thresholds
  LP     lmc >= L_hi                                   (pool holds >= ~half the supply: creator-seeded, LP pullable)
  FAKE   lmc < L_lo and mcap >= M and age < 14 d        (fake market cap, supply outside the pool)
  REUSE  ticker reused by >= R other pools so far and age < 14 d
  YOUNG  age < A minutes
  IMBAL  buys/sells 1h >= B and age < 14 d             (engine 'unnatural buy imbalance' analogue)
Selection objective (fixed before searching):
  v_struct : maximise event recall (mean share of pre-drain points flagged, LIQ20 events) subject to
             per-pool false-positive share <= 5% on clean negatives in LIQ50, no YOUNG atom.
  v_young  : same objective with the FPR cap at 20%, YOUNG allowed.
  ties -> fewer atoms, then lower FPR.
Only train points are used; holdout is evaluated later for the pre-selected configs only.
"""
import itertools, json, os, sys
from rugcommon import load_points, HERE

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
pts = [x for x in load_points() if x['part'] == 'train']
N = len(pts)
D14 = 14 * 1440


def mask(pred):
    m = 0
    for k, x in enumerate(pts):
        try:
            if pred(x):
                m |= 1 << k
        except TypeError:
            pass
    return m


POS = mask(lambda x: x['y2h'] == 1)
NEG = mask(lambda x: x['clean_neg'])
U20 = mask(lambda x: x['liq'] >= 20_000)
U50 = mask(lambda x: x['liq'] >= 50_000)
FAST = mask(lambda x: x['y2h'] == 1 and ('LIQ_FAST' in x['kind'] or 'PRICE_FAST' in x['kind']))

# per-event masks (LIQ20 universe) and per-pool negative masks (LIQ50)
ev, evf, pools = {}, {}, {}
for k, x in enumerate(pts):
    b = 1 << k
    if x['y2h'] and x['liq'] >= 20_000:
        ev[x['pair']] = ev.get(x['pair'], 0) | b
        if FAST & b:
            evf[x['pair']] = evf.get(x['pair'], 0) | b
    if x['clean_neg'] and x['liq'] >= 50_000:
        pools[x['pair']] = pools.get(x['pair'], 0) | b
EV = list(ev.values()); EVF = list(evf.values()); POOLS = list(pools.values())
print('train points', N, 'events LIQ20', len(EV), 'fast events LIQ20', len(EVF), 'neg pools LIQ50', len(POOLS))


def score(m):
    rec = sum((m & e).bit_count() / e.bit_count() for e in EV) / len(EV)
    recf = sum((m & e).bit_count() / e.bit_count() for e in EVF) / len(EVF)
    anyr = sum(1 for e in EV if m & e) / len(EV)
    pfpr = sum((m & p).bit_count() / p.bit_count() for p in POOLS) / len(POOLS)
    pts_fpr50 = (m & NEG & U50).bit_count() / (NEG & U50).bit_count()
    prec50 = (m & POS & U50).bit_count() / max(1, (m & (POS | NEG) & U50).bit_count())
    return {'ev_recall_mean': rec, 'fast_ev_recall_mean': recf, 'ev_recall_any': anyr, 'pool_fpr50': pfpr,
            'pt_fpr50': pts_fpr50, 'pt_prec50': prec50}


atoms = {
    'LP': {None: 0, **{L: mask(lambda x, L=L: x['lmc'] >= L) for L in (0.6, 0.8, 1.0, 1.2)}},
    'FAKE': {None: 0, **{(L, M): mask(lambda x, L=L, M=M: x['lmc'] < L and x['mcap'] >= M and x['age'] < D14)
                         for L in (0.01, 0.02, 0.03) for M in (5e6, 20e6)}},
    'REUSE': {None: 0, **{R: mask(lambda x, R=R: x['reuse'] >= R and x['age'] < D14) for R in (1, 3, 6)}},
    'YOUNG': {None: 0, **{A: mask(lambda x, A=A: x['age'] < A) for A in (15, 30, 60, 120, 360, 720, 1440)}},
    'IMBAL': {None: 0, **{B: mask(lambda x, B=B: x['bs1h'] >= B and x['age'] < D14) for B in (5, 10, 20)}},
}
names = list(atoms)
results = []
for combo in itertools.product(*[list(atoms[n].items()) for n in names]):
    m = 0
    cfg = {}
    for n, (th, am) in zip(names, combo):
        cfg[n] = th
        m |= am
    sc = score(m)
    sc['n_atoms'] = sum(1 for n in names if cfg[n] is not None)
    results.append((cfg, sc))
print('configs_tried', len(results))


def pick(cap, allow_young):
    ok = [(c, s) for c, s in results if s['pool_fpr50'] <= cap and (allow_young or c['YOUNG'] is None)]
    ok.sort(key=lambda cs: (-round(cs[1]['ev_recall_mean'], 3), cs[1]['n_atoms'], cs[1]['pool_fpr50']))
    return ok[:8]


out = {'configs_tried': len(results)}
for label, cap, young in (('v_struct', 0.05, False), ('v_young', 0.20, True)):
    top = pick(cap, young)
    print('==', label, 'cap', cap)
    for c, s in top:
        print('  ', json.dumps({k: v for k, v in c.items()}, default=str), {k: round(v, 3) for k, v in s.items()})
    out[label] = [[{k: v for k, v in c.items()}, s] for c, s in top]

# single-atom reference lines
print('== single atoms (train)')
for n in names:
    for th, am in atoms[n].items():
        if th is None:
            continue
        s = score(am)
        print('  ', n, th, {k: round(v, 3) for k, v in s.items()})
interim = mask(lambda x: x['interim'] == 1)
print('== interim_rug_risk (pre-existing, not selected here)', {k: round(v, 3) for k, v in score(interim).items()})
json.dump(out, open(os.path.join(HERE, 'search_train.json'), 'w'), default=str, indent=1)
