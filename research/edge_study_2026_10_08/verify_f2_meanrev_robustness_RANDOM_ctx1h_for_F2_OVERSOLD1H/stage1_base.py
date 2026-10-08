"""Stage 1: reproduce the row, then split shifts, rolling 3 h blocks, cost doubling, drop best pair/hour,
concentration, and harness-assumption sensitivities (fill rule, gap valuation, holdout-blind rug screen)."""
import pickle, time
from vcommon import *

t0 = time.time()
H.load()
res = {}

# ---- 1. reproduction against the leaderboard cache
with open(DEEP + '/leaderboard/trades/f2_meanrev.pkl', 'rb') as fh:
    cached = {(r['name'], r['variant']): r['trades'] for r in pickle.load(fh)['rows']}
ref_g = cached[('RANDOM_ctx1h_for_F2_OVERSOLD1H_STAB', 'rug_guard_v1')]
ref_o = cached[('RANDOM_ctx1h_for_F2_OVERSOLD1H_STAB', 'orig')]
tg = sim(make_rand(guard=True))
to = sim(make_rand(guard=False))
keys = ('pair', 'entry_t', 'exit_t', 'reason', 'net0', 'net50')
same_g = len(tg) == len(ref_g) and all(all(a[k] == b[k] for k in keys) for a, b in zip(tg, ref_g))
same_o = len(to) == len(ref_o) and all(all(a[k] == b[k] for k in keys) for a, b in zip(to, ref_o))
res['reproduction'] = {'guarded_identical': same_g, 'n_guarded': len(tg), 'orig_identical': same_o, 'n_orig': len(to)}
print('reproduction', res['reproduction'], flush=True)

# ---- 2. headline numbers (guarded = verified row; orig for reference)
res['headline_guarded'] = evaluate(tg)
res['headline_guarded_model'] = evaluate(tg, key='usd0')
res['headline_guarded_calonly'] = evaluate(tg, key='usdcal')
res['headline_guarded_v2stress'] = evaluate(tg, key='usd50_v2')
res['headline_orig'] = evaluate(to)
_, ho, _, _ = split(tg)
res['portfolio_holdout_slots3'] = H.portfolio(ho, slots=3)
res['portfolio_full_slots3'] = H.portfolio(tg, slots=3)
print('guarded ', short(res['headline_guarded']))
print('orig    ', short(res['headline_orig']))
print('model   ', short(res['headline_guarded_model']))
print('cal-only', short(res['headline_guarded_calonly']))

# ---- 3. split shifts
res['split'] = {}
for fr in (0.5, 0.6, 0.7):
    e = evaluate(tg, fr)
    res['split'][str(fr)] = e
    print('split %.1f  ' % fr, short(e))

# ---- 4. rolling 3 h blocks (full span)
res['blocks_3h'] = blocks(tg, 3.0)
res['blocks_3h_model'] = blocks(tg, 3.0, 'usd0')
for b in res['blocks_3h']:
    print('block', b)

# ---- 5. drop best pair / best hour (holdout) + concentration
res['drop_best_holdout'] = drop_best(ho)
res['drop_best_full'] = drop_best(tg)
print('drop best (holdout)', res['drop_best_holdout'])

# ---- 6. double the cost stress (+100 bps per leg instead of +50; calibration kept)
H.STRESS_BPS = 100.0
t100 = sim(make_rand(guard=True))
H.STRESS_BPS = 50.0
same_tr = [a['entry_t'] for a in t100] == [b['entry_t'] for b in tg]
res['stress100'] = {'same_trades_as_base': same_tr, 'eval': evaluate(t100)}
print('stress100', same_tr, short(res['stress100']['eval']))

# ---- 7. harness-assumption sensitivities
res['sens'] = {}
H.FILL_RULE = 'next_point'
res['sens']['fill_next_point_v1'] = evaluate(sim(make_rand(guard=True)))
H.FILL_RULE = 'next_refresh'
for gv in ('pre', 'return'):
    H.GAP_VALUATION = gv
    res['sens']['gap_' + gv] = evaluate(sim(make_rand(guard=True)))
H.GAP_VALUATION = 'min'
res['sens']['rug_train_only_screen'] = evaluate(sim(make_rand(rug='train_only', guard=True)))
res['sens']['rug_interim_only_no_guard'] = res['headline_orig']
res['sens']['no_rug_screen_at_all'] = evaluate(sim(make_rand(rug='none', guard=False)))
H.CALIB_EXIT_REASON = False
res['sens']['no_stop_exit_extra'] = evaluate(sim(make_rand(guard=True)))
H.CALIB_EXIT_REASON = True
for k, v in res['sens'].items():
    print('sens %-28s' % k, short(v))

res['simulate_calls'] = CALLS['simulate']
res['seconds'] = round(time.time() - t0, 1)
dump('stage1_base.json', res)
print('done', res['seconds'], 's, simulate calls', CALLS['simulate'])
