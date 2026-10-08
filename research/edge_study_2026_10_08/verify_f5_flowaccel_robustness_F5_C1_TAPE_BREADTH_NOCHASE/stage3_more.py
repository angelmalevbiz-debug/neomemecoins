"""Stage 3: (a) harness/fill-model sensitivity (does the train edge reported by the family exist only under v1 fills?),
(b) matched same-pair +/-60 min random control (timing value net of pair/regime), (c) tape coverage by hour and
C1 universe eligibility by hour (why there is a 12-15 h hole and what regime the holdout is), (d) per-trade sign test.
Saves stage3.pkl."""
import sys, time, json, math, random
sys.path.insert(0, (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r'/verify_f5_flowaccel_robustness_F5_C1_TAPE_BREADTH_NOCHASE')
import common_v as C

H, S, TF = C.H, C.S, C.TF
T0 = time.time()
series, meta = H.load()
st1 = C.load_pk('stage1.pkl')
base = st1['runs']['BASE']['trades']
t0 = meta['t0']
cut = H.split_t(meta)
res = {}


def pr(*a):
    print(*a, flush=True)


# ---------------------------------------------------------------- (a) fill model / calibration sensitivity
pr('== harness sensitivity (same C1 signal)')
saved = (H.FILL_RULE, H.CALIB_IN_STRESS, H.FEED_GAP_MS)
variants = [('v1-like: next_point fills, no calib, no feed-gap close', 'next_point', False, None),
            ('next_point fills + calib + feed-gap', 'next_point', True, 600_000),
            ('next_refresh fills, no calib (= audited v2 +50bps)', 'next_refresh', False, 600_000),
            ('FINAL (next_refresh + calib + feed-gap)', 'next_refresh', True, 600_000)]
res['harness'] = {}
try:
    for lab, fr, cal, gap in variants:
        H.FILL_RULE, H.CALIB_IN_STRESS, H.FEED_GAP_MS = fr, cal, gap
        tr = C.run(dict(C.BASE))
        a, b = C.split(tr)
        res['harness'][lab] = {'train': C.brief(a), 'holdout': C.brief(b), 'train_model': C.brief(a, 'usd0'), 'holdout_model': C.brief(b, 'usd0')}
        pr('  %-55s train n%3d net50 %7.3f$ (%6.2f%%) model %7.3f$ | holdout n%3d net50 %7.3f$ (%6.2f%%) model %7.3f$' % (
            lab, len(a), C.brief(a)['mean_usd'], C.brief(a)['mean_pct'], C.brief(a, 'usd0')['mean_usd'],
            len(b), C.brief(b)['mean_usd'], C.brief(b)['mean_pct'], C.brief(b, 'usd0')['mean_usd']))
finally:
    H.FILL_RULE, H.CALIB_IN_STRESS, H.FEED_GAP_MS = saved

# ---------------------------------------------------------------- (b) matched same-pair +/-60 min random control
pr('== matched control: random entries on the SAME pair within +/-60 min of each C1 decision (universe+no-chase, same exits)')
U = C.make_universe_nochase(C.BASE)
pairs = {x['pair'] for x in base}
ctrl = []
for seed in range(1, 9):
    salt = 'vf5mc%d' % seed
    sig = (lambda s_: (lambda P: P.static('pair') in pairs and U(P) and H.hashed_coin(P.static('pair'), P.t, 0.02, s_)))(salt)
    ctrl.extend(C.run(dict(C.BASE), signal=sig))
pr('  control trades pooled over 8 seeds:', len(ctrl))
by = {}
for x in ctrl:
    by.setdefault(x['pair'], []).append(x)
diffs = {'train': [], 'holdout': []}
for x in base:
    cands = [y for y in by.get(x['pair'], []) if abs(y['decision_t'] - x['decision_t']) <= 3_600_000]
    if not cands:
        continue
    m = C.mean([y['usd50'] for y in cands])
    part = 'train' if x['entry_t'] < cut else 'holdout'
    diffs[part].append((x['pair'], x['usd50'], m, len(cands)))
res['matched'] = {}
for part, d in diffs.items():
    if not d:
        continue
    dd = [a - b for _, a, b, _ in d]
    # pair-cluster bootstrap of the mean difference
    cl = {}
    for (p, a, b, _), v in zip(d, dd):
        cl.setdefault(p, []).append(v)
    groups = list(cl.values())
    rnd = random.Random(11)
    ms = []
    for _ in range(2000):
        tot = cnt = 0
        for _ in range(len(groups)):
            g = groups[rnd.randrange(len(groups))]
            tot += sum(g)
            cnt += len(g)
        ms.append(tot / cnt)
    ms.sort()
    r = {'n_matched': len(d), 'c1_mean': round(C.mean([a for _, a, _, _ in d]), 3), 'control_mean': round(C.mean([b for _, _, b, _ in d]), 3),
         'mean_diff': round(C.mean(dd), 3), 'ci95_pair': [round(ms[50], 3), round(ms[1949], 3)],
         'share_trades_beating_control': round(sum(1 for v in dd if v > 0) / len(dd), 3)}
    res['matched'][part] = r
    pr('  ', part, json.dumps(r))

# ---------------------------------------------------------------- (c) tape coverage and eligibility by hour
pr('== tape coverage by hour (events ingested, distinct pools) and C1 universe eligibility')
T = TF.tape()
ev_h, pools_h = {}, {}
for pair, d in T.items():
    for et in d['et']:
        h = int((et - t0) // 3.6e6)
        if 0 <= h <= 23:
            ev_h[h] = ev_h.get(h, 0) + 1
            pools_h.setdefault(h, set()).add(pair)
elig_h, fire_h, ptsh = {}, {}, {}
sig = C.make_signal(C.BASE)
for pair, s in series.items():
    if s['dex'] != 'pumpswap' or s['quote_sol'] != 1 or pair not in T:
        continue
    ts = s['t']
    for i in range(0, len(ts), 6):   # every 6th point (~30 s) for speed
        P = H.Past(s, i)
        h = int((ts[i] - t0) // 3.6e6)
        if U(P):
            elig_h.setdefault(h, set()).add(pair)
            ptsh[h] = ptsh.get(h, 0) + 1
            if sig(P):
                fire_h[h] = fire_h.get(h, 0) + 1
rows = []
for h in range(0, 23):
    rows.append((h, ev_h.get(h, 0), len(pools_h.get(h, ())), len(elig_h.get(h, ())), ptsh.get(h, 0), fire_h.get(h, 0),
                 sum(1 for x in base if int((x['entry_t'] - t0) // 3.6e6) == h)))
res['hourly'] = rows
pr('  hour | tape events | tape pools | eligible pools (universe+nochase) | eligible pts (1/6 sampled) | signal fires (1/6) | C1 trades')
for r in rows:
    pr('  %4d | %7d | %4d | %4d | %6d | %5d | %3d' % r)
pr('  split cut at hour %.2f' % ((cut - t0) / 3.6e6))

# ---------------------------------------------------------------- (d) simple per-trade sign tests
pr('== sign tests (holdout, net50): wins vs losses')
tr, ho = C.split(base)
for nm, part in (('train', tr), ('holdout', ho)):
    w = sum(1 for x in part if x['usd50'] > 0)
    n = len(part)
    # one-sided binomial P(X>=w | p=0.5)
    pval = sum(math.comb(n, k) for k in range(w, n + 1)) / 2 ** n
    pr('  %s wins %d / %d  P(X>=w | p=.5) = %.3f' % (nm, w, n, pval))
    res.setdefault('sign', {})[nm] = {'wins': w, 'n': n, 'p_one_sided': pval}

C.save('stage3.pkl', res)
pr('done %.1fs' % (time.time() - T0))
