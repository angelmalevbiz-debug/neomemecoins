"""Stage 3: adversarial checks on RANDOM_ctx1h_for_F2_OVERSOLD1H_STAB (rug_guard_v1 variant primary).

A  salt sweep: the same random rule with 400 other salts -> where does salt 'f2r1' sit?
B  full context (p=1: enter at every eligible point, cooldown 300 s) -> does the CONTEXT itself carry edge?
C  rug-screen swap: interim (holdout-informed) vs train-only vs none
D  fill rule next_point (v1) vs next_refresh (v2)
E  split fraction 0.5 / 0.6 / 0.7
F  leave-one-pair-out and censored-trade drop on the f2r1 holdout
G  entry-skip survivorship (signals dropped because the next point is > 60 s away / last point never evaluated)
H  field leakage: age vs t-created, pc1h vs realized past / future 1 h price change
"""
import sys
sys.dont_write_bytecode = True
sys.path.insert(0, (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r'/verify_f2_meanrev_leakage_RANDOM_ctx1h_for_F2_OVERSOLD1H')
import bisect, json, math, time
import vlib as V
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

t0 = time.time()
series, meta = V.load()
R = {}


def tv(tr, frac=0.6):
    a, b = V.split(tr, frac)
    return V.summ(a), V.summ(b)


def short(sm):
    return {k: sm.get(k) for k in ('n', 'pairs', 'mean_usd', 'mean_pct', 'median_pct', 'win', 'pf', 'ci_pairhour',
                                   'ci_pair', 'top_pair_share', 'exits')}


ELIG = V.eligible_points(guard=True)
ELIG_O = V.eligible_points(guard=False)
print('eligible guard', sum(len(v) for v in ELIG.values()), 'pairs', len(ELIG),
      '| orig', sum(len(v) for v in ELIG_O.values()), 'pairs', len(ELIG_O), round(time.time() - t0, 1), flush=True)

# ---------------------------------------------------------------- A salt sweep
sweep = []
for k in range(400):
    salt = 'vx%03d' % k
    tr = V.run(V.choose(ELIG, 0.005, salt))
    a, b = V.split(tr)
    sweep.append({'salt': salt, 'tn': len(a), 'tm': (sum(x['usd50'] for x in a) / len(a)) if a else None,
                  'hn': len(b), 'hp': len({x['pair'] for x in b}),
                  'hm': (sum(x['usd50'] for x in b) / len(b)) if b else None,
                  'hm0': (sum(x['usd0'] for x in b) / len(b)) if b else None,
                  'htop': (max(sum(1 for x in b if x['pair'] == p) for p in {x['pair'] for x in b}) / len(b)) if b else None})
hm = sorted(x['hm'] for x in sweep if x['hm'] is not None)
tm = sorted(x['tm'] for x in sweep if x['tm'] is not None)
F2R1_H, F2R1_T = 5.974, -5.957


def pct_rank(xs, v):
    return round(100 * sum(1 for x in xs if x < v) / len(xs), 1)


def q(xs, p):
    return round(xs[min(len(xs) - 1, int(p * len(xs)))], 2)


gate_like = [x for x in sweep if x['hm'] is not None and x['tm'] is not None and x['hm'] > 0 and x['tm'] > 0
             and x['hn'] >= 20 and x['hp'] >= 8 and (x['htop'] or 1) <= 0.35]
R['A_salt_sweep'] = {
    'salts': len(sweep), 'prob': 0.005,
    'holdout_mean_usd_mean': round(sum(hm) / len(hm), 3), 'holdout_mean_usd_median': q(hm, 0.5),
    'holdout_mean_usd_p05_p95': [q(hm, 0.05), q(hm, 0.95)], 'holdout_min_max': [round(hm[0], 2), round(hm[-1], 2)],
    'share_holdout_positive': round(sum(1 for v in hm if v > 0) / len(hm), 3),
    'f2r1_holdout_percentile': pct_rank(hm, F2R1_H),
    'train_mean_usd_mean': round(sum(tm) / len(tm), 3), 'train_p05_p95': [q(tm, 0.05), q(tm, 0.95)],
    'f2r1_train_percentile': pct_rank(tm, F2R1_T),
    'share_train_and_holdout_positive_with_n_pairs_top_gates': round(len(gate_like) / len(sweep), 3),
    'holdout_n_mean': round(sum(x['hn'] for x in sweep) / len(sweep), 1),
    'corr_train_holdout_means': None,
}
pp = [(x['tm'], x['hm']) for x in sweep if x['hm'] is not None and x['tm'] is not None]
if len(pp) > 3:
    ma = sum(a for a, _ in pp) / len(pp)
    mb = sum(b for _, b in pp) / len(pp)
    cov = sum((a - ma) * (b - mb) for a, b in pp)
    va = sum((a - ma) ** 2 for a, _ in pp)
    vb = sum((b - mb) ** 2 for _, b in pp)
    R['A_salt_sweep']['corr_train_holdout_means'] = round(cov / math.sqrt(va * vb), 3)
print('A', json.dumps(R['A_salt_sweep']), round(time.time() - t0, 1), flush=True)

# ---------------------------------------------------------------- B full context (p = 1)
full = {}
for lab, el in (('guard', ELIG), ('orig', ELIG_O)):
    tr = V.run(el)
    a, b = V.split(tr)
    full[lab] = {'train': short(V.summ(a)), 'holdout': short(V.summ(b)), 'holdout_model': short(V.summ(b, 'usd0')),
                 'train_model': short(V.summ(a, 'usd0')), 'portfolio_holdout': V.portfolio(b)}
    if lab == 'guard':
        per = {}
        for x in b:
            per.setdefault('%s %s' % ((x['sym'] or '?')[:10], x['pair'][:8]), []).append(x['net50'])
        full[lab]['holdout_per_pair'] = sorted(([k, len(v), round(sum(v) / len(v), 2), round(sum(v), 1)]
                                                for k, v in per.items()), key=lambda r: -r[1])
        pert = {}
        for x in a:
            pert.setdefault('%s %s' % ((x['sym'] or '?')[:10], x['pair'][:8]), []).append(x['net50'])
        full[lab]['train_per_pair'] = sorted(([k, len(v), round(sum(v) / len(v), 2), round(sum(v), 1)]
                                              for k, v in pert.items()), key=lambda r: -r[1])
R['B_full_context_p1'] = full
print('B', json.dumps(full, default=str)[:3000], round(time.time() - t0, 1), flush=True)

# ---------------------------------------------------------------- C rug-screen swap, D fill rule, E split
cd = {}
for scr in ('interim', 'trainonly', 'none'):
    el = V.eligible_points(guard=True, screen=scr)
    for p, salt in ((0.005, 'f2r1'), (1.0, None)):
        ch = V.choose(el, p, salt) if salt else el
        tr = V.run(ch)
        a, b = V.split(tr)
        cd['screen=%s p=%s' % (scr, p)] = {'eligible_pairs': len(el), 'train': short(V.summ(a)), 'holdout': short(V.summ(b))}
for p, salt in ((0.005, 'f2r1'), (1.0, None)):
    ch = V.choose(ELIG, p, salt) if salt else ELIG
    tr = V.run(ch, rule='next_point')
    a, b = V.split(tr)
    cd['fill=next_point p=%s' % p] = {'train': short(V.summ(a)), 'holdout': short(V.summ(b))}
    tr = V.run(ch, calib=False)
    a, b = V.split(tr)
    cd['calib_off(v2 stress) p=%s' % p] = {'train': short(V.summ(a)), 'holdout': short(V.summ(b))}
    tr = V.run(ch, gap_ms=None)
    a, b = V.split(tr)
    cd['no_feed_gap_close p=%s' % p] = {'train': short(V.summ(a)), 'holdout': short(V.summ(b))}
TR1 = V.run(V.choose(ELIG, 0.005, 'f2r1'))
for frac in (0.5, 0.6, 0.7):
    a, b = V.split(TR1, frac)
    cd['f2r1 split=%.1f' % frac] = {'train': short(V.summ(a)), 'holdout': short(V.summ(b))}
R['C_D_E_sensitivity'] = cd
for k, v in cd.items():
    print('C/D/E', k, json.dumps({kk: (vv if kk == 'eligible_pairs' else {x: vv.get(x) for x in ('n', 'pairs', 'mean_usd', 'mean_pct', 'win')})
                                 for kk, vv in v.items()}), flush=True)

# ---------------------------------------------------------------- F leave-one-pair-out, censored drop
_, HO = V.split(TR1)
pairs = sorted({x['pair'] for x in HO})
lopo = []
for p in pairs:
    rest = [x for x in HO if x['pair'] != p]
    sym = next(x['sym'] for x in HO if x['pair'] == p)
    lopo.append([(sym or '?')[:10], p[:8], sum(1 for x in HO if x['pair'] == p),
                 round(sum(x['usd50'] for x in rest) / len(rest), 3)])
lopo.sort(key=lambda r: r[3])
best_k = sorted(HO, key=lambda x: -x['usd50'])
F = {'holdout_mean_usd': round(sum(x['usd50'] for x in HO) / len(HO), 3),
     'leave_one_pair_out_mean_usd (sym, pair, n_removed, mean_after)': lopo,
     'drop_top1_trade_mean_usd': round(sum(x['usd50'] for x in best_k[1:]) / (len(HO) - 1), 3),
     'drop_top2_trades_mean_usd': round(sum(x['usd50'] for x in best_k[2:]) / (len(HO) - 2), 3),
     'drop_top3_trades_mean_usd': round(sum(x['usd50'] for x in best_k[3:]) / (len(HO) - 3), 3),
     'drop_END_censored_mean_usd': round(sum(x['usd50'] for x in HO if x['flag'] != 'END') /
                                         max(1, sum(1 for x in HO if x['flag'] != 'END')), 3),
     'tp_exits_net50_pct': sorted(round(x['net50'], 2) for x in HO if x['reason'] == 'TP'),
     'gap_trades': [[(x['sym'] or '?')[:10], x['pair'][:8], round(x['net50'], 2)] for x in HO if x['flag'] == 'GAP']}
R['F_concentration'] = F
print('F', json.dumps(F), flush=True)

# ---------------------------------------------------------------- G entry-skip survivorship
G = {'eligible_points_guard': sum(len(v) for v in ELIG.values()), 'next_point_gt_60s': 0, 'pairs_with_skip': 0,
     'last_point_eligible_pairs': 0, 'skipped_examples': []}
for pair, lst in ELIG.items():
    s = series[pair]
    ts = s['t']
    n = len(ts)
    sk = [i for i in lst if ts[i + 1] - ts[i] > 60_000]
    G['next_point_gt_60s'] += len(sk)
    if sk:
        G['pairs_with_skip'] += 1
        for i in sk[:2]:
            # what happened: next point time gap and price change at reappearance
            G['skipped_examples'].append([(s['sym'] or '?')[:10], pair[:8], round((ts[i + 1] - ts[i]) / 1000),
                                          round(100 * (s['price'][i + 1] / s['price'][i] - 1), 2) if s['price'][i] > 0 else None,
                                          s['liq'][i + 1]])
    if V.context_ok(s, n - 1, guard=True):
        G['last_point_eligible_pairs'] += 1
stats = {}
for pair, ch in ELIG.items():
    V.sim_pair(series[pair], ch, meta['t1'], stats=stats)
G['p1_sim_skip_stats'] = stats
R['G_entry_skip'] = G
print('G', json.dumps(G), flush=True)

# ---------------------------------------------------------------- H field leakage
H = {}
cr = [series[p]['created'] for p in list(ELIG)[:5]]
H['created_samples'] = cr
diffs = []
for pair, lst in ELIG.items():
    s = series[pair]
    c = s['created']
    try:
        c = float(c)
    except (TypeError, ValueError):
        continue
    if c < 1e11:      # seconds -> ms
        c *= 1000
    for i in lst[::50]:
        diffs.append(s['age'][i] - (s['t'][i] - c) / 60_000)
diffs.sort()
if diffs:
    H['age_minus_(t-created)_min'] = {'n': len(diffs), 'p01': round(diffs[int(.01 * len(diffs))], 2),
                                       'median': round(diffs[len(diffs) // 2], 2),
                                       'p99': round(diffs[int(.99 * len(diffs)) - 1], 2)}


def corr(pts):
    if len(pts) < 10:
        return None
    ma = sum(a for a, _ in pts) / len(pts)
    mb = sum(b for _, b in pts) / len(pts)
    cov = sum((a - ma) * (b - mb) for a, b in pts)
    va = sum((a - ma) ** 2 for a, _ in pts)
    vb = sum((b - mb) ** 2 for _, b in pts)
    return round(cov / math.sqrt(va * vb), 3) if va > 0 and vb > 0 else None


past, fut = [], []
for pair, s in V.tradable_pairs():
    ts, pr, pc = s['t'], s['price'], s['pc1h']
    n = len(ts)
    for i in range(0, n, 25):
        if not (s['liq'][i] >= 50_000 and pc[i] == pc[i] and pr[i] > 0):
            continue
        a = bisect.bisect_right(ts, ts[i] - 3_600_000) - 1
        if a >= 0 and ts[i] - 3_600_000 - ts[a] < 120_000 and pr[a] > 0:
            past.append((pc[i], 100 * (pr[i] / pr[a] - 1)))
        b = bisect.bisect_left(ts, ts[i] + 3_600_000)
        if b < n and ts[b] - ts[i] - 3_600_000 < 120_000 and pr[b] > 0:
            fut.append((pc[i], 100 * (pr[b] / pr[i] - 1)))
H['pc1h_corr_with_realized_past_1h'] = {'n': len(past), 'r': corr(past)}
H['pc1h_corr_with_realized_future_1h'] = {'n': len(fut), 'r': corr(fut)}
# same restricted to the guard context
past_c, fut_c = [], []
for pair, lst in ELIG.items():
    s = series[pair]
    ts, pr, pc = s['t'], s['price'], s['pc1h']
    n = len(ts)
    for i in lst[::10]:
        a = bisect.bisect_right(ts, ts[i] - 3_600_000) - 1
        if a >= 0 and ts[i] - 3_600_000 - ts[a] < 120_000 and pr[a] > 0:
            past_c.append((pc[i], 100 * (pr[i] / pr[a] - 1)))
        b = bisect.bisect_left(ts, ts[i] + 3_600_000)
        if b < n and ts[b] - ts[i] - 3_600_000 < 120_000 and pr[b] > 0:
            fut_c.append((pc[i], 100 * (pr[b] / pr[i] - 1)))
H['context_pc1h_corr_past'] = {'n': len(past_c), 'r': corr(past_c)}
H['context_pc1h_corr_future'] = {'n': len(fut_c), 'r': corr(fut_c)}
R['H_field_leakage'] = H
print('H', json.dumps(H, default=str), flush=True)

with open(V.OUT + '/v03_adversarial.json', 'w', encoding='utf-8') as fh:
    json.dump(R, fh, indent=1, default=str)
print('total', round(time.time() - t0, 1), 's')
