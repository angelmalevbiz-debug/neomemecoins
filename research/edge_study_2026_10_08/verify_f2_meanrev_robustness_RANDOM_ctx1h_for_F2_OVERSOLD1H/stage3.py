"""Stage 3: is the pc1h<=-12% context informative at all? Compare it with random entries in the plain universe
(liq>=50k + interim rug screen, no context) with the same exits, 10 seeds pooled, by split and by 3 h block."""
import sys, time
sys.dont_write_bytecode = True
sys.path.insert(0, (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r'/verify_f2_meanrev_robustness_RANDOM_ctx1h_for_F2_OVERSOLD1H')
import rcommon as R
H = R.H

T0 = time.time()
_, meta = H.load()
res = {'t0_utc': time.strftime('%Y-%m-%d %H:%M', time.gmtime(meta['t0'] / 1000)),
       't1_utc': time.strftime('%Y-%m-%d %H:%M', time.gmtime(meta['t1'] / 1000)),
       'cut60_utc': time.strftime('%Y-%m-%d %H:%M', time.gmtime(R.cut(0.6) / 1000))}
SEEDS = ['f2r1'] + ['rob%02d' % k for k in range(9)]
pool = []
per = []
for s in SEEDS:
    tt = R.run(prob=0.0005, salt=s, pc1h_max=float('inf'))
    tr, ho = R.split(tt)
    per.append({'salt': s, 'train_mean_usd': R.stats(tr).get('mean_usd'), 'holdout_mean_usd': R.stats(ho).get('mean_usd'),
                'holdout_n': len(ho)})
    pool.extend(tt)
    print('universe seed', per[-1], flush=True)
ptr, pho = R.split(pool)
res['universe_random_10seeds'] = {'per_seed': per, 'train': R.stats(ptr), 'holdout': R.stats(pho),
                                  'blocks3h': R.blocks(pool)}
# dense universe sample (p=0.005) for a better block profile
dense = R.run(prob=0.005, salt='f2u', pc1h_max=float('inf'))
dtr, dho = R.split(dense)
res['universe_random_p0.005'] = {'train': R.stats(dtr), 'holdout': R.stats(dho), 'blocks3h': R.blocks(dense)}
for b in res['universe_random_10seeds']['blocks3h']:
    print('univ blk', b, flush=True)
print('universe pooled train', res['universe_random_10seeds']['train'].get('mean_usd'), 'holdout',
      res['universe_random_10seeds']['holdout'].get('mean_usd'))
print('universe p=0.005 train', res['universe_random_p0.005']['train'].get('mean_usd'), 'holdout',
      res['universe_random_p0.005']['holdout'].get('mean_usd'))
for b in res['universe_random_p0.005']['blocks3h']:
    print('univ p.005 blk', b, flush=True)
# where in clock time is the single best holdout hour of the original draw?
tt = R.run()
tr, ho = R.split(tt)
_, bh = R.drop_best_hour(ho)
res['best_hour_utc'] = time.strftime('%Y-%m-%d %H:00', time.gmtime(bh[0] * 3600))
res['best_hour_hours_from_t0'] = round((bh[0] * 3.6e6 - meta['t0']) / 3.6e6, 2)
res['best_hour_trades'] = [{'pair': x['pair'][:8], 'sym': (x['sym'] or '')[:12], 'entry_utc': time.strftime('%H:%M', time.gmtime(x['entry_t'] / 1000)),
                            'reason': x['reason'], 'usd50': round(x['usd50'], 2)} for x in ho if int(x['entry_t'] // 3_600_000) == bh[0]]
print(res['best_hour_utc'], res['best_hour_hours_from_t0'], res['best_hour_trades'])
R.save('stage3.json', res)
print('done %.1fs' % (time.time() - T0), res['t0_utc'], res['t1_utc'], res['cut60_utc'])
