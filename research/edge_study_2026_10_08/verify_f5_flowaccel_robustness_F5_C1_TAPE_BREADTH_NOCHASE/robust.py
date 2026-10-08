"""Robustness battery for F5_C1_TAPE_BREADTH_NOCHASE (rug_guard_v1 variant primary, orig secondary).

Stages (python -B robust.py <stage>):
  repro     reproduce the leaderboard trades with the parametrized signal
  perturb   one-at-a-time +/-20-30% threshold and exit perturbations (guarded + orig), plus ablations
  base      split shifts, rolling 3 h blocks, cost stress x2, fill/gap sensitivities, best pair / best hour removal,
            concentration
  random    random-entry baselines: 20 salts in the same universe (calibrated to a similar trade count), noise floor
            of an 11-trade holdout mean, pair-matched random entries
Everything is written to results_<stage>.json in this folder.
"""
import json, os, pickle, random, sys, time
sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common as C  # noqa: E402

H, TF = C.H, C.TF
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass


def run(sig_over=None, exit_over=None):
    return H.simulate(C.make_signal(**(sig_over or {})), **C.make_kwargs(**(exit_over or {})))


def line(tag, trades):
    tr, ho = C.split(trades)
    a, b, f = C.stats(tr), C.stats(ho), C.stats(trades)
    print('%-34s full n=%3d mean$ %7s | train n=%3d p=%2d mean$ %7s | holdout n=%3d p=%2d mean$ %7s pct %7s top %s' % (
        tag, f['n'], f['mean_usd'], a['n'], a['pairs'], a['mean_usd'], b['n'], b['pairs'], b['mean_usd'],
        b['mean_pct'], b['top_share']), flush=True)
    return {'tag': tag, 'full': f, 'train': a, 'holdout': b}


def save(stage, obj):
    with open(os.path.join(C.OUT, 'results_%s.json' % stage), 'w', encoding='utf-8') as fh:
        json.dump(obj, fh, indent=1, default=str)


def base_trades(guard=True):
    p = os.path.join(C.OUT, 'base_%s.pkl' % ('guard' if guard else 'orig'))
    if os.path.exists(p):
        with open(p, 'rb') as fh:
            return pickle.load(fh)
    t = run({'guard': guard})
    with open(p, 'wb') as fh:
        pickle.dump(t, fh)
    return t


# ------------------------------------------------------------------ stages
def stage_repro():
    with open(C.LB + '/trades/f5_flowaccel.pkl', 'rb') as fh:
        d = pickle.load(fh)
    ref = {r['variant']: r['trades'] for r in d['rows'] if r['name'] == 'F5_C1_TAPE_BREADTH_NOCHASE'}
    out = {}
    for guard, var in ((True, 'rug_guard_v1'), (False, 'orig')):
        mine = base_trades(guard)
        keys = ('pair', 'entry_t', 'exit_t', 'reason', 'net0', 'net50')
        same = len(mine) == len(ref[var]) and all(all(a[k] == b[k] for k in keys) for a, b in zip(mine, ref[var]))
        out[var] = {'mine': len(mine), 'leaderboard': len(ref[var]), 'identical': same}
        line('repro ' + var, mine)
    print(out)
    save('repro', out)


PERTURB = [
    # (label, signal overrides, exit overrides)
    ('liq_min 15k (-25%)', {'liq_min': 15_000}, None),
    ('liq_min 25k (+25%)', {'liq_min': 25_000}, None),
    ('fee_lo 40 (-24%)', {'fee_lo': 40.0}, None),
    ('fee_lo 65 (+24%)', {'fee_lo': 65.0}, None),
    ('fee_hi 100 (-20%)', {'fee_hi': 100.0}, None),
    ('live_s 450 (-25%)', {'live_s': 450.0}, None),
    ('live_s 750 (+25%)', {'live_s': 750.0}, None),
    ('nochase win 225s (-25%)', {'nc_win': 225.0}, None),
    ('nochase win 375s (+25%)', {'nc_win': 375.0}, None),
    ('nochase lo -1.5 (-25%)', {'nc_lo': -1.5}, None),
    ('nochase lo -2.5 (+25%)', {'nc_lo': -2.5}, None),
    ('nochase hi 3.75 (-25%)', {'nc_hi': 3.75}, None),
    ('nochase hi 6.25 (+25%)', {'nc_hi': 6.25}, None),
    ('flow win 225s (-25%)', {'fl_win': 225.0}, None),
    ('flow win 375s (+25%)', {'fl_win': 375.0}, None),
    ('ub >= 8 (-20%)', {'ub': 8}, None),
    ('ub >= 12 (+20%)', {'ub': 12}, None),
    ('newb >= 4 (-20%)', {'newb': 4}, None),
    ('newb >= 6 (+20%)', {'newb': 6}, None),
    ('top < 0.225 (-25%)', {'top': 0.225}, None),
    ('top < 0.375 (+25%)', {'top': 0.375}, None),
    ('stop -7.5 (-25%)', None, {'stop': -7.5}),
    ('stop -12.5 (+25%)', None, {'stop': -12.5}),
    ('tp 4.5 (-25%)', None, {'tp': 4.5}),
    ('tp 7.5 (+25%)', None, {'tp': 7.5}),
    ('hold 22.5 min (-25%)', None, {'hold_min': 22.5}),
    ('hold 37.5 min (+25%)', None, {'hold_min': 37.5}),
    ('size 0.15% liq (-25%)', None, {'size_frac': 0.0015}),
    ('size 0.25% liq (+25%)', None, {'size_frac': 0.0025}),
    ('cooldown 225s (-25%)', None, {'cooldown_s': 225}),
    ('cooldown 375s (+25%)', None, {'cooldown_s': 375}),
]
ABLATE = [
    ('ablate: no newb', {'use_newb': False}, None),
    ('ablate: no top_share', {'use_top': False}, None),
    ('ablate: no net_sol', {'use_net': False}, None),
    ('ablate: no flow at all (ub>=0)', {'ub': 0, 'use_newb': False, 'use_top': False, 'use_net': False}, None),
]


def stage_perturb():
    res = {'guard': [], 'orig': []}
    for guard in (True, False):
        key = 'guard' if guard else 'orig'
        print('==== variant', key)
        res[key].append(line('BASE', base_trades(guard)))
        for lab, so, eo in PERTURB + ABLATE:
            so = dict(so or {})
            so['guard'] = guard
            t0 = time.time()
            r = line(lab, run(so, eo))
            r['sec'] = round(time.time() - t0, 1)
            res[key].append(r)
    for key in ('guard', 'orig'):
        rows =[r for r in res[key][1:] if not r['tag'].startswith('ablate')]
        ho_pos = sum(1 for r in rows if (r['holdout']['mean_usd'] or -1) > 0)
        tr_pos = sum(1 for r in rows if (r['train']['mean_usd'] or -1) > 0)
        both = sum(1 for r in rows if (r['holdout']['mean_usd'] or -1) > 0 and (r['train']['mean_usd'] or -1) > 0)
        full_pos = sum(1 for r in rows if (r['full']['mean_usd'] or -1) > 0)
        gate_like = sum(1 for r in rows if (r['holdout']['mean_usd'] or -1) > 0 and (r['train']['mean_usd'] or -1) > 0
                        and r['holdout']['n'] >= 20 and r['holdout']['pairs'] >= 8 and r['holdout']['top_share'] <= .35)
        hm = sorted(r['holdout']['mean_usd'] for r in rows if r['holdout']['mean_usd'] is not None)
        res[key + '_summary'] = {'perturbations': len(rows), 'holdout_mean_pos': ho_pos, 'train_mean_pos': tr_pos,
                                 'both_pos': both, 'full_mean_pos': full_pos, 'gate_like_pass': gate_like,
                                 'holdout_mean_usd_min': hm[0] if hm else None,
                                 'holdout_mean_usd_median': hm[len(hm) // 2] if hm else None,
                                 'holdout_mean_usd_max': hm[-1] if hm else None}
        print(key, res[key + '_summary'])
    save('perturb', res)


def _hour(x, t0):
    return int((x['entry_t'] - t0) // 3_600_000)


def stage_base():
    _, meta = H.load()
    t0, t1 = meta['t0'], meta['t1']
    res = {}
    for guard in (True, False):
        key = 'guard' if guard else 'orig'
        T = base_trades(guard)
        r = {}
        # split shifts
        for frac in (0.5, 0.6, 0.7):
            tr, ho = C.split(T, frac)
            r['split_%d_%d' % (round(frac * 100), round(100 - frac * 100))] = {'train': C.stats(tr), 'holdout': C.stats(ho)}
        # rolling 3 h blocks
        blocks = []
        b = 0
        while t0 + b * 3 * 3_600_000 < t1:
            lo, hi = t0 + b * 3 * 3_600_000, t0 + (b + 1) * 3 * 3_600_000
            bt = [x for x in T if lo <= x['entry_t'] < hi]
            s = C.stats(bt)
            s['block'] = '%d-%dh' % (3 * b, 3 * (b + 1))
            s['pairs_list'] = sorted(set(C.short(x) for x in bt))
            blocks.append(s)
            b += 1
        r['blocks_3h'] = blocks
        act = [s for s in blocks if s['n'] >= 3]
        r['blocks_summary'] = {'blocks': len(blocks), 'blocks_n>=3': len(act),
                               'positive_mean_blocks_n>=3': sum(1 for s in act if s['mean_usd'] > 0),
                               'empty_blocks': sum(1 for s in blocks if s['n'] == 0)}
        # cost variants on the same trades (exit decisions use the model, so trades are identical)
        tr, ho = C.split(T)
        r['cost_bases_holdout'] = {k: C.stats(ho, k) for k in ('usd0', 'usdcal', 'usd50_v2', 'usd50')}
        r['cost_bases_train'] = {k: C.stats(tr, k) for k in ('usd0', 'usdcal', 'usd50_v2', 'usd50')}
        H.STRESS_BPS = 100.0
        try:
            T2 = run({'guard': guard})
        finally:
            H.STRESS_BPS = 50.0
        tr2, ho2 = C.split(T2)
        same = [(a['pair'], a['entry_t'], a['exit_t']) for a in T2] == [(a['pair'], a['entry_t'], a['exit_t']) for a in T]
        r['stress_100bps_per_leg'] = {'same_trades': same, 'train': C.stats(tr2), 'holdout': C.stats(ho2),
                                      'full': C.stats(T2)}
        # harness sensitivities: v1 next-point fills; v1-like gap valuation
        for name, attr, val in (('fill_next_point_v1', 'FILL_RULE', 'next_point'),
                                ('gap_valuation_return', 'GAP_VALUATION', 'return'),
                                ('no_exit_reason_extra', 'CALIB_EXIT_REASON', False)):
            old = getattr(H, attr)
            setattr(H, attr, val)
            try:
                Ts = run({'guard': guard})
            finally:
                setattr(H, attr, old)
            a, b2 = C.split(Ts)
            r['sens_' + name] = {'train': C.stats(a), 'holdout': C.stats(b2), 'full': C.stats(Ts)}
        # remove best pair / best hour
        for scope, S in (('holdout', ho), ('full', T)):
            if not S:
                continue
            pp, hh = {}, {}
            for x in S:
                pp[x['pair']] = pp.get(x['pair'], 0.0) + x['usd50']
                hh[_hour(x, t0)] = hh.get(_hour(x, t0), 0.0) + x['usd50']
            bp = max(pp, key=pp.get)
            bh = max(hh, key=hh.get)
            wo_p = [x for x in S if x['pair'] != bp]
            wo_h = [x for x in S if _hour(x, t0) != bh]
            wo_both = [x for x in S if x['pair'] != bp and _hour(x, t0) != bh]
            srt = sorted((x['usd50'] for x in S), reverse=True)
            tot = sum(srt)
            gross_pos = sum(v for v in srt if v > 0)
            cnt = {}
            for x in S:
                cnt[x['pair']] = cnt.get(x['pair'], 0) + 1
            r['concentration_' + scope] = {
                'best_pair': C.short(next(x for x in S if x['pair'] == bp)), 'best_pair_usd': round(pp[bp], 2),
                'best_hour': bh, 'best_hour_usd': round(hh[bh], 2),
                'all': C.stats(S), 'without_best_pair': C.stats(wo_p), 'without_best_hour': C.stats(wo_h),
                'without_best_pair_and_hour': C.stats(wo_both),
                'top_pair_share_trades': round(max(cnt.values()) / len(S), 3),
                'top_pair_share_of_gross_profit': round(max(max(v, 0) for v in pp.values()) / gross_pos, 3) if gross_pos > 0 else None,
                'top5_trades_usd': round(sum(srt[:5]), 2), 'total_usd': round(tot, 2),
                'top5_share_of_net_pnl': round(sum(srt[:5]) / tot, 3) if tot > 0 else 'net<=0',
                'net_without_top5_usd': round(tot - sum(srt[:5]), 2),
                'per_pair_usd': sorted(((C.short(next(x for x in S if x['pair'] == p)), cnt[p], round(v, 2))
                                        for p, v in pp.items()), key=lambda z: -z[2])}
        r['portfolio_holdout'] = H.portfolio(ho, slots=3) if ho else None
        r['portfolio_full'] = H.portfolio(T, slots=3)
        res[key] = r
        print('====', key)
        print(json.dumps({k: v for k, v in r.items() if k != 'blocks_3h'}, default=str)[:6000])
        for s in blocks:
            print('   block', s['block'], s['n'], s['pairs'], s['mean_usd'], s['sum_usd'], s['pairs_list'])
    save('base', res)


def stage_random():
    _, meta = H.load()
    target = len(base_trades(True))
    cut = H.split_t(meta)
    u = C.universe_nc(dict(C.DEFAULTS))
    kw = C.make_kwargs()
    res = {'target_full_n': target}

    def rsim(prob, salt, pairs=None, t_from=None):
        sig = lambda P: u(P) and H.hashed_coin(P.static('pair'), P.t, prob, salt)
        return H.simulate(sig, pairs=pairs, t_from=t_from, **kw)

    # calibrate prob on one salt to a similar FULL trade count
    cal = {}
    for prob in (0.002, 0.0035, 0.005):
        cal[prob] = len(rsim(prob, 'cal'))
        print('calib prob', prob, cal[prob], flush=True)
    prob = min(cal, key=lambda p: abs(cal[p] - target))
    res['calibration'] = {str(k): v for k, v in cal.items()}
    res['prob'] = prob
    seeds = []
    pooled_ho = []
    for k in range(20):
        T = rsim(prob, 'vrob%d' % k)
        tr, ho = C.split(T)
        seeds.append({'salt': 'vrob%d' % k, 'full': C.stats(T), 'train': C.stats(tr), 'holdout': C.stats(ho)})
        pooled_ho.append(ho)
        print('seed', k, seeds[-1]['full']['n'], 'train', seeds[-1]['train']['mean_usd'], 'holdout n',
              seeds[-1]['holdout']['n'], seeds[-1]['holdout']['mean_usd'], flush=True)
    res['seeds'] = seeds
    ref = 6.106
    hm = [s['holdout']['mean_usd'] for s in seeds if s['holdout']['mean_usd'] is not None]
    res['seed_summary'] = {'seeds': len(seeds), 'holdout_mean_usd_sorted': sorted(hm),
                           'seeds_holdout_mean_ge_C1': sum(1 for v in hm if v >= ref),
                           'seeds_holdout_mean_pos': sum(1 for v in hm if v > 0),
                           'first5': [s['holdout']['mean_usd'] for s in seeds[:5]]}
    # noise floor: 11-trade random subsets of each seed's holdout trades
    rng = random.Random(11)
    hits = tot = 0
    for ho in pooled_ho:
        if len(ho) < 11:
            continue
        for _ in range(2000):
            sub = rng.sample(ho, 11)
            tot += 1
            hits += (sum(x['usd50'] for x in sub) / 11) >= ref
    res['noise_floor_11_trade_subsets'] = {'draws': tot, 'share_mean_ge_6.106': round(hits / tot, 4) if tot else None}
    # pair-matched random (diagnostic, uses hindsight of which pairs C1 traded in holdout)
    base = base_trades(True)
    ho_pairs = sorted(set(x['pair'] for x in base if x['entry_t'] >= cut))
    pm = []
    for k in range(20):
        T = rsim(0.01, 'vpm%d' % k, pairs=set(ho_pairs), t_from=cut)
        pm.append(C.stats(T))
    pmm = [s['mean_usd'] for s in pm if s['mean_usd'] is not None]
    res['pair_matched_random_holdout'] = {'pairs': [p[:8] for p in ho_pairs], 'runs': pm,
                                          'mean_of_means': round(sum(pmm) / len(pmm), 3) if pmm else None,
                                          'runs_mean_pos': sum(1 for v in pmm if v > 0),
                                          'runs_ge_C1': sum(1 for v in pmm if v >= ref)}
    print('pair-matched', res['pair_matched_random_holdout']['mean_of_means'],
          res['pair_matched_random_holdout']['runs_mean_pos'], res['pair_matched_random_holdout']['runs_ge_C1'])
    print(json.dumps(res['seed_summary']), json.dumps(res['noise_floor_11_trade_subsets']))
    save('random', res)


if __name__ == '__main__':
    t = time.time()
    H.load()
    st = sys.argv[1] if len(sys.argv) > 1 else 'repro'
    {'repro': stage_repro, 'perturb': stage_perturb, 'base': stage_base, 'random': stage_random}[st]()
    print('done %.1fs' % (time.time() - t))
