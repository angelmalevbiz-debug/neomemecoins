"""Stage 2: split shifts, rolling 3-hour blocks, doubled cost stress, leave-best-pair/hour-out, concentration,
random baselines over 5 seeds (same universe + no-chase, same exits/sizing), a same-pairs random baseline (timing vs
pair selection), and rug-screen variants. Saves stage2.pkl and prints a summary."""
import sys, time, json, math
sys.path.insert(0, (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r'/verify_f5_flowaccel_robustness_F5_C1_TAPE_BREADTH_NOCHASE')
import common_v as C

H, S = C.H, C.S
T0 = time.time()
series, meta = H.load()
st1 = C.load_pk('stage1.pkl')
base = st1['runs']['BASE']['trades']
res = {}


def pr(*a):
    print(*a, flush=True)


def mu(tr, k='usd50'):
    return round(C.mean([x[k] for x in tr]), 3) if tr else None


# ---------------------------------------------------------------- 1. split shifts
pr('== split shifts (net50 calibrated stress; usd/trade)')
res['splits'] = {}
for frac in (0.5, 0.6, 0.7):
    tr, ho = C.split(base, frac)
    ev = H.evaluate(base, frac)
    res['splits'][frac] = {'train': C.brief(tr), 'holdout': C.brief(ho),
                           'holdout_model': C.brief(ho, 'usd0'), 'train_model': C.brief(tr, 'usd0')}
    pr('  frac %.1f  train %s' % (frac, json.dumps(C.brief(tr))))
    pr('            holdout %s' % json.dumps(C.brief(ho)))
    pr('            holdout model(net0) %s | train model %s' % (C.brief(ho, 'usd0')['mean_usd'], C.brief(tr, 'usd0')['mean_usd']))
pr('  full sample', json.dumps(C.brief(base)), '| model', C.brief(base, 'usd0')['mean_usd'],
   '| v2 stress (no calib)', mu(base, 'usd50_v2'), '| calib only', mu(base, 'usdcal'))
res['full'] = C.brief(base)

# ---------------------------------------------------------------- 2. rolling 3-hour blocks
pr('== rolling 3-hour blocks (by entry time)')
t0, t1 = meta['t0'], meta['t1']
blocks = []
b = 0
while t0 + b * 3 * 3.6e6 < t1:
    lo, hi = t0 + b * 3 * 3.6e6, min(t1 + 1, t0 + (b + 1) * 3 * 3.6e6)
    sub = [x for x in base if lo <= x['entry_t'] < hi]
    blocks.append({'block': b, 'hours': '%.0f-%.1f' % (b * 3, (hi - t0) / 3.6e6), 'n': len(sub), 'pairs': len({x['pair'] for x in sub}),
                   'mean_usd': mu(sub), 'sum_usd': round(sum(x['usd50'] for x in sub), 2), 'mean_usd_model': mu(sub, 'usd0')})
    b += 1
for r in blocks:
    pr('  ', json.dumps(r))
res['blocks'] = blocks
pr('  blocks with trades:', sum(1 for r in blocks if r['n']), 'positive (net50):', sum(1 for r in blocks if r['n'] and r['sum_usd'] > 0),
   'positive (model):', sum(1 for r in blocks if r['n'] and (r['mean_usd_model'] or 0) > 0))
# 3-hour blocks sliding by 1 hour
slid = []
h = 0
while t0 + (h + 3) * 3.6e6 <= t1 + 3.6e6:
    lo, hi = t0 + h * 3.6e6, t0 + (h + 3) * 3.6e6
    sub = [x for x in base if lo <= x['entry_t'] < hi]
    if sub:
        slid.append((h, len(sub), round(sum(x['usd50'] for x in sub), 2)))
    h += 1
res['sliding_3h'] = slid
pr('  sliding 3h windows (start_h, n, sum$):', slid)
pr('  sliding windows positive: %d / %d' % (sum(1 for s in slid if s[2] > 0), len(slid)))

# ---------------------------------------------------------------- 3. doubled cost stress (+100 bps per leg)
pr('== doubled stress: STRESS_BPS 50 -> 100 per leg (plus calibration, as in the acceptance path)')
H.STRESS_BPS = 100.0
try:
    t2 = C.run(dict(C.BASE))
finally:
    H.STRESS_BPS = 50.0
tr2, ho2 = C.split(t2)
res['stress100'] = {'train': C.brief(tr2), 'holdout': C.brief(ho2), 'full': C.brief(t2)}
pr('  same trade list as base:', [(x['pair'], x['entry_t']) for x in t2] == [(x['pair'], x['entry_t']) for x in base])
pr('  train', json.dumps(C.brief(tr2)))
pr('  holdout', json.dumps(C.brief(ho2)))
pr('  full', json.dumps(C.brief(t2)))

# ---------------------------------------------------------------- 4. leave-best-out and concentration
tr, ho = C.split(base)


def drop_best_pair(trs):
    by = {}
    for x in trs:
        by[x['pair']] = by.get(x['pair'], 0) + x['usd50']
    best = max(by, key=by.get)
    return best, [x for x in trs if x['pair'] != best]


def drop_best_hour(trs):
    by = {}
    for x in trs:
        hk = int((x['entry_t'] - t0) // 3.6e6)
        by[hk] = by.get(hk, 0) + x['usd50']
    best = max(by, key=by.get)
    return best, [x for x in trs if int((x['entry_t'] - t0) // 3.6e6) != best]


def top_pair_most_trades(trs):
    by = {}
    for x in trs:
        by[x['pair']] = by.get(x['pair'], 0) + 1
    p = max(by, key=by.get)
    return p, [x for x in trs if x['pair'] != p]


pr('== leave-best-out / concentration')
res['leave_out'] = {}
for nm, part in (('holdout', ho), ('train', tr), ('full', base)):
    bp, wo_p = drop_best_pair(part)
    bh, wo_h = drop_best_hour(part)
    tp_, wo_tp = top_pair_most_trades(part)
    pos = sorted((x['usd50'] for x in part), reverse=True)
    total = sum(pos)
    gross_pos = sum(v for v in pos if v > 0)
    top5 = sum(pos[:5])
    r = {'mean': mu(part), 'n': len(part),
         'wo_best_pair': {'pair': bp[:8], 'sym': next(x['sym'] for x in part if x['pair'] == bp), 'pair_sum_usd': round(sum(x['usd50'] for x in part if x['pair'] == bp), 2),
                          'n': len(wo_p), 'mean_usd': mu(wo_p), 'sum_usd': round(sum(x['usd50'] for x in wo_p), 2)},
         'wo_best_hour': {'hour': bh, 'n': len(wo_h), 'mean_usd': mu(wo_h), 'sum_usd': round(sum(x['usd50'] for x in wo_h), 2)},
         'wo_busiest_pair': {'pair': tp_[:8], 'n_removed': len(part) - len(wo_tp), 'mean_usd': mu(wo_tp)},
         'wo_best_pair_and_hour': None,
         'top5_trades_sum_usd': round(top5, 2), 'total_sum_usd': round(total, 2), 'gross_positive_usd': round(gross_pos, 2),
         'top5_share_of_gross_positive': round(top5 / gross_pos, 3) if gross_pos > 0 else None,
         'mean_wo_top5_trades': round(sum(pos[5:]) / max(1, len(pos) - 5), 3),
         'top_pair_share': C.brief(part)['top_pair_share']}
    _, w2 = drop_best_hour(wo_p)
    r['wo_best_pair_and_hour'] = {'n': len(w2), 'mean_usd': mu(w2)}
    res['leave_out'][nm] = r
    pr('  ', nm, json.dumps(r))
pp = {}
for x in ho:
    pp.setdefault((x['sym'], x['pair'][:8]), []).append(x['usd50'])
pr('  holdout per pair (sym, pair8, n, sum$, mean$):', sorted([(k[0], k[1], len(v), round(sum(v), 2), round(C.mean(v), 2)) for k, v in pp.items()], key=lambda z: -z[2]))
pr('  holdout trades:', [(x['sym'], round((x['entry_t'] - t0) / 3.6e6, 2), x['reason'], round(x['net50'], 2)) for x in ho])
res['holdout_per_pair'] = {'%s:%s' % k: v for k, v in pp.items()}

# ---------------------------------------------------------------- 5. random baselines, 5 seeds
pr('== random baselines (same universe + no-chase, same exits/sizing), 5 seeds x 2 probabilities')
U = C.make_universe_nochase(C.BASE)
res['random'] = []
for prob in (0.007, 0.004):
    for seed in range(1, 6):
        salt = 'vf5rob%d' % seed
        sig = (lambda pr_, s_: (lambda P: U(P) and H.hashed_coin(P.static('pair'), P.t, pr_, s_)))(prob, salt)
        rt = C.run(dict(C.BASE), signal=sig)
        a, b_ = C.split(rt)
        row = {'prob': prob, 'salt': salt, 'n': len(rt), 'train': C.brief(a), 'holdout': C.brief(b_), 'full': C.brief(rt)}
        res['random'].append(row)
        pr('  p=%.3f %s n=%3d train %7.3f (n%3d) holdout %7.3f (n%3d, pairs %2d, win %s) full %7.3f' % (
            prob, salt, len(rt), row['train'].get('mean_usd', float('nan')), row['train']['n'], row['holdout'].get('mean_usd', float('nan')),
            row['holdout']['n'], row['holdout'].get('pairs', 0), row['holdout'].get('win'), row['full']['mean_usd']))

# ---------------------------------------------------------------- 6. same-pairs random baseline (timing vs pair selection)
pr('== random entries restricted to the pairs C1 traded (diagnostic: is it timing or which pairs happened to rise?)')
ho_pairs = {x['pair'] for x in ho}
all_pairs = {x['pair'] for x in base}
res['same_pairs_random'] = []
for seed in range(1, 6):
    salt = 'vf5sp%d' % seed
    sig = (lambda s_: (lambda P: P.static('pair') in all_pairs and U(P) and H.hashed_coin(P.static('pair'), P.t, 0.01, s_)))(salt)
    rt = C.run(dict(C.BASE), signal=sig)
    a, b_ = C.split(rt)
    b_same = [x for x in b_ if x['pair'] in ho_pairs]
    row = {'salt': salt, 'train': C.brief(a), 'holdout': C.brief(b_), 'holdout_same_ho_pairs': C.brief(b_same)}
    res['same_pairs_random'].append(row)
    pr('  %s train %7.3f (n%3d) holdout %7.3f (n%3d) | holdout on C1-holdout pairs only %7.3f (n%3d)' % (
        salt, row['train'].get('mean_usd', float('nan')), row['train']['n'], row['holdout'].get('mean_usd', float('nan')), row['holdout']['n'],
        row['holdout_same_ho_pairs'].get('mean_usd', float('nan')), row['holdout_same_ho_pairs']['n']))

# ---------------------------------------------------------------- 7. rug-screen variants
pr('== rug-screen variants')
res['rug'] = {}
for rv in ('train_only', 'interim+guard'):
    p = dict(C.BASE)
    p['rug'] = rv
    rt = C.run(p)
    a, b_ = C.split(rt)
    res['rug'][rv] = {'train': C.brief(a), 'holdout': C.brief(b_)}
    pr('  %-14s train %s' % (rv, json.dumps(C.brief(a))))
    pr('  %-14s holdout %s' % ('', json.dumps(C.brief(b_))))

# ---------------------------------------------------------------- 8. stage-1 perturbation summary
pr('== perturbation summary (stage 1)')
oat = {k: v for k, v in st1['runs'].items() if k != 'BASE' and not k.startswith('JOINT')}
jnt = {k: v for k, v in st1['runs'].items() if k.startswith('JOINT')}
summ = {}
for lab, grp in (('one_at_a_time', oat), ('joint', jnt)):
    hos, trs, fulls, ok = [], [], [], 0
    for k, v in grp.items():
        a, b_ = C.split(v['trades'])
        hm, tm, fm = C.mean([x['usd50'] for x in b_]), C.mean([x['usd50'] for x in a]), C.mean([x['usd50'] for x in v['trades']])
        hos.append(hm)
        trs.append(tm)
        fulls.append(fm)
        bp = C.brief(b_)
        ok += (bp['n'] >= 20 and bp['pairs'] >= 8 and hm > 0 and tm > 0 and bp['top_pair_share'] <= 0.35)
    hs = sorted(hos)
    summ[lab] = {'variants': len(grp), 'holdout_pos': sum(1 for h in hos if h > 0), 'train_pos': sum(1 for t in trs if t > 0),
                 'full_pos': sum(1 for f in fulls if f > 0), 'holdout_median': round(hs[len(hs) // 2], 3),
                 'holdout_mean': round(C.mean(hos), 3), 'holdout_min': round(hs[0], 3), 'holdout_max': round(hs[-1], 3),
                 'train_max': round(max(trs), 3), 'full_max': round(max(fulls), 3), 'pass_gate_wo_baseline': ok}
    pr('  ', lab, json.dumps(summ[lab]))
res['perturb_summary'] = summ

C.save('stage2.pkl', res)
pr('done %.1fs' % (time.time() - T0))
