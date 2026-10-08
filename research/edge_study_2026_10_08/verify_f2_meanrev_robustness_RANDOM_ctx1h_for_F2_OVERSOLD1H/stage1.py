"""Stage 1: reproduce the row, concentration / drop tests, split shifts, rolling 3 h blocks, doubled cost, seeds, dense."""
import sys, time
sys.dont_write_bytecode = True
sys.path.insert(0, (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r'/verify_f2_meanrev_robustness_RANDOM_ctx1h_for_F2_OVERSOLD1H')
import rcommon as R
H = R.H

T0 = time.time()
H.load()
print('loaded %.1fs' % (time.time() - T0), flush=True)
res = {}


def full_report(trades, label):
    tr, ho = R.split(trades, 0.6)
    d = {'n_total': len(trades), 'train': R.stats(tr), 'holdout': R.stats(ho),
         'train_model': R.stats(tr, 'usd0'), 'holdout_model': R.stats(ho, 'usd0'),
         'portfolio_holdout': H.portfolio(ho, slots=3), 'portfolio_full': H.portfolio(trades, slots=3)}
    for frac in (0.5, 0.7):
        a, b = R.split(trades, frac)
        d['split_%d_%d' % (round(frac * 100), round(100 - frac * 100))] = {'train': R.stats(a), 'holdout': R.stats(b)}
    d['blocks3h'] = R.blocks(trades)
    d['blocks3h_model'] = R.blocks(trades, key='usd0')
    ex = {}
    for x in ho:
        k = x['reason'] + ('/' + x['flag'] if x['flag'] else '')
        ex[k] = ex.get(k, 0) + 1
    d['holdout_exits'] = ex
    # drop tests (holdout)
    hb, best_pair = R.drop_best_pair(ho)
    hh, best_hour = R.drop_best_hour(ho)
    d['holdout_drop_best_pair'] = {'dropped': best_pair, 'stats': R.stats(hb)}
    d['holdout_drop_best_hour'] = {'dropped_hour_utc': (time.strftime('%m-%d %H:00', time.gmtime(best_hour[0] * 3600)), best_hour[1]) if best_hour else None,
                                   'stats': R.stats(hh)}
    hbh, _ = R.drop_best_hour(hb)
    d['holdout_drop_best_pair_and_hour'] = R.stats(hbh)
    d['holdout_drop_top1'] = R.stats(R.drop_top_k(ho, 1))
    d['holdout_drop_top3'] = R.stats(R.drop_top_k(ho, 3))
    # doubled cost stress approx
    for x in trades:
        x['net100a'] = R.net100(x)
        x['usd100a'] = x['size'] * x['net100a'] / 100
    d['train_net100_approx'] = R.stats(tr, 'usd100a')
    d['holdout_net100_approx'] = R.stats(ho, 'usd100a')
    print('== %s: n=%d  train n=%s mean$=%s | holdout n=%s pairs=%s mean$=%s mean%%=%s CI=%s' % (
        label, len(trades), d['train']['n'], d['train'].get('mean_usd'), d['holdout']['n'], d['holdout'].get('pairs'),
        d['holdout'].get('mean_usd'), d['holdout'].get('mean_pct'), d['holdout'].get('ci95')), flush=True)
    return d


# ---------------------------------------------------------------- 1. reproduce with the family's own module
fam = R._load_module('f2fam_verify', R.DEEP + '/f2_meanrev/strategies.py')
bl = [b for b in fam.BASELINES if b['name'] == 'RANDOM_ctx1h_for_F2_OVERSOLD1H_STAB'][0]
t_f = H.simulate(bl['signal'], **bl['kwargs'])
t_m = R.run()
same = len(t_f) == len(t_m) and all(a['pair'] == b['pair'] and a['entry_t'] == b['entry_t'] and a['net50'] == b['net50']
                                    for a, b in zip(t_f, t_m))
res['reproduce'] = {'family_module_trades': len(t_f), 'my_reimpl_trades': len(t_m), 'identical': same}
print('reproduce', res['reproduce'], flush=True)
res['main'] = full_report(t_m, 'MAIN f2r1 p=0.005')
res['main_trades'] = [{'pair': x['pair'][:8], 'sym': (x['sym'] or '')[:12],
                       'entry_utc': time.strftime('%m-%d %H:%M', time.gmtime(x['entry_t'] / 1000)),
                       'reason': x['reason'], 'flag': x['flag'], 'net0': round(x['net0'], 2),
                       'net50': round(x['net50'], 2), 'usd50': round(x['usd50'], 2), 'fee_bps': x['fee_bps'],
                       'liq': round(x['liq']), 'hold_min': round(x['hold_s'] / 60, 1),
                       'holdout': x['entry_t'] >= R.cut(0.6)} for x in t_m]

# exact doubled stress (+100 bps per leg instead of +50) vs the post-hoc approximation
t_100 = R.run(stress_bps=100.0)
same_tr = len(t_100) == len(t_m) and all(a['entry_t'] == b['entry_t'] and a['pair'] == b['pair'] for a, b in zip(t_100, t_m))
err = [abs(a['net50'] - b['net100a']) for a, b in zip(t_100, t_m)] if same_tr else []
tr100, ho100 = R.split(t_100)
res['main_net100_exact'] = {'same_trades': same_tr, 'approx_max_abs_err_pct': round(max(err), 4) if err else None,
                            'train': R.stats(tr100), 'holdout': R.stats(ho100)}
print('net100 exact holdout', res['main_net100_exact']['holdout'].get('mean_usd'), 'train',
      res['main_net100_exact']['train'].get('mean_usd'), 'approx err', res['main_net100_exact']['approx_max_abs_err_pct'], flush=True)

# rug_guard_v1 and train-only rug screen variants of the same draw
t_g = R.run(guard=H.rug_guard_v1)
res['main_rug_guard_v1'] = full_report(t_g, 'MAIN + rug_guard_v1')

# ---------------------------------------------------------------- 2. 50 random seeds (the strategy IS a random draw)
salts = ['f2r1'] + ['rob%02d' % k for k in range(49)]
seeds = []
pool = []
for s in salts:
    tt = R.run(salt=s)
    tr, ho = R.split(tt, 0.6)
    a50, b50 = R.split(tt, 0.5)
    a70, b70 = R.split(tt, 0.7)
    for x in tt:
        x['net100a'] = R.net100(x)
        x['usd100a'] = x['size'] * x['net100a'] / 100
        x['salt'] = s
    hb, _ = R.drop_best_pair(ho)
    hh, _ = R.drop_best_hour(ho)
    st_h = R.stats(ho)
    row = {'salt': s, 'n': len(tt), 'train_n': len(tr), 'train_mean_usd': R.stats(tr).get('mean_usd'),
           'holdout_n': len(ho), 'holdout_pairs': st_h.get('pairs'), 'holdout_mean_usd': st_h.get('mean_usd'),
           'holdout_top_pair_share': st_h.get('top_pair_share'),
           'holdout_model_mean_usd': R.stats(ho, 'usd0').get('mean_usd'),
           'holdout_net100_mean_usd': R.stats(ho, 'usd100a').get('mean_usd'),
           'ho50_mean_usd': R.stats(b50).get('mean_usd'), 'tr50_mean_usd': R.stats(a50).get('mean_usd'),
           'ho30_mean_usd': R.stats(b70).get('mean_usd'), 'tr70_mean_usd': R.stats(a70).get('mean_usd'),
           'holdout_drop_best_pair_mean_usd': R.stats(hb).get('mean_usd'),
           'holdout_drop_best_hour_mean_usd': R.stats(hh).get('mean_usd'),
           'full_mean_usd': R.stats(tt).get('mean_usd'),
           'blocks': [b['mean_usd'] for b in R.blocks(tt)],
           'passes_gate_shape': bool(st_h.get('n', 0) >= 20 and (st_h.get('pairs') or 0) >= 8 and
                                     (st_h.get('mean_usd') or -1) > 0 and (R.stats(tr).get('mean_usd') or -1) > 0 and
                                     (st_h.get('top_pair_share') or 1) <= 0.35)}
    seeds.append(row)
    pool.extend(tt)
    print('seed %-5s n=%3d train %4d %8s | holdout %3d %8s pairs %s' % (s, len(tt), len(tr), row['train_mean_usd'],
                                                                     len(ho), row['holdout_mean_usd'], row['holdout_pairs']), flush=True)
res['seeds'] = seeds
hos = [r['holdout_mean_usd'] for r in seeds]
srt = sorted(hos, reverse=True)
res['seed_summary'] = {
    'n_seeds': len(seeds),
    'f2r1_holdout_rank_of_50 (1=best)': srt.index(seeds[0]['holdout_mean_usd']) + 1,
    'holdout_mean_usd_median_over_seeds': sorted(hos)[len(hos) // 2],
    'holdout_mean_usd_mean_over_seeds': R.mean(hos),
    'holdout_mean_usd_min_max': [min(hos), max(hos)],
    'share_seeds_holdout_pos': round(sum(1 for v in hos if v > 0) / len(hos), 3),
    'share_seeds_train_pos': round(sum(1 for r in seeds if (r['train_mean_usd'] or -1) > 0) / len(seeds), 3),
    'share_seeds_train_and_holdout_pos': round(sum(1 for r in seeds if (r['train_mean_usd'] or -1) > 0 and r['holdout_mean_usd'] > 0) / len(seeds), 3),
    'share_seeds_pass_gate_shape': round(sum(1 for r in seeds if r['passes_gate_shape']) / len(seeds), 3),
    'share_seeds_ho50_pos': round(sum(1 for r in seeds if (r['ho50_mean_usd'] or -1) > 0) / len(seeds), 3),
    'share_seeds_ho30_pos': round(sum(1 for r in seeds if (r['ho30_mean_usd'] or -1) > 0) / len(seeds), 3),
    'share_seeds_holdout_net100_pos': round(sum(1 for r in seeds if (r['holdout_net100_mean_usd'] or -1) > 0) / len(seeds), 3),
    'share_seeds_holdout_drop_best_pair_pos': round(sum(1 for r in seeds if (r['holdout_drop_best_pair_mean_usd'] or -1) > 0) / len(seeds), 3),
    'share_seeds_holdout_drop_best_hour_pos': round(sum(1 for r in seeds if (r['holdout_drop_best_hour_mean_usd'] or -1) > 0) / len(seeds), 3),
    'first5_other_seeds': [{k: r[k] for k in ('salt', 'train_mean_usd', 'holdout_n', 'holdout_pairs', 'holdout_mean_usd')} for r in seeds[1:6]],
}
nb = len(seeds[0]['blocks'])
res['seed_blocks'] = [{'block': b, 'mean_over_seeds_usd': R.mean([r['blocks'][b] for r in seeds]),
                       'share_seeds_pos': round(sum(1 for r in seeds if (r['blocks'][b] or -1) > 0) / len(seeds), 2)} for b in range(nb)]
# pooled (seed-averaged expectation of the random strategy)
ptr, pho = R.split(pool)
res['pooled_50_seeds'] = {'train': R.stats(ptr), 'holdout': R.stats(pho), 'holdout_model': R.stats(pho, 'usd0'),
                          'holdout_net100': R.stats(pho, 'usd100a'), 'blocks3h': R.blocks(pool)}
print('seed summary', res['seed_summary'], flush=True)
print('pooled train', res['pooled_50_seeds']['train'].get('mean_usd'), 'holdout', res['pooled_50_seeds']['holdout'].get('mean_usd'), flush=True)

# ---------------------------------------------------------------- 3. dense versions of the same context
for p in (0.05, 1.0):
    tt = R.run(prob=p, salt='f2r1')
    res['dense_p%g' % p] = full_report(tt, 'DENSE p=%g' % p)

R.save('stage1.json', res)
print('done %.1fs' % (time.time() - T0))
