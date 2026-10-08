"""Stage 2: reproduce the leaderboard row with the independent implementation (vlib) and diff trade by trade."""
import sys
sys.dont_write_bytecode = True
sys.path.insert(0, (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r'/verify_f2_meanrev_leakage_RANDOM_ctx1h_for_F2_OVERSOLD1H')
import json, pickle, time
import vlib as V
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

t0 = time.time()
series, meta = V.load()
print('loaded', meta, round(time.time() - t0, 1), 's', flush=True)
with open(V.OUT + '/lb_trades.pkl', 'rb') as fh:
    LB = pickle.load(fh)

res = {}
for variant, guard in (('orig', False), ('rug_guard_v1', True)):
    t1 = time.time()
    elig = V.eligible_points(guard=guard)
    ne = sum(len(v) for v in elig.values())
    ch = V.choose(elig, 0.005, 'f2r1')
    tr = V.run(ch)
    trn, ho = V.split(tr)
    lb = LB[variant]
    key = lambda x: (x['pair'], x['entry_t'])
    mine = {key(x): x for x in tr}
    theirs = {key(x): x for x in lb}
    common = set(mine) & set(theirs)
    only_m = sorted(set(mine) - set(theirs))
    only_t = sorted(set(theirs) - set(mine))
    dmax = max((abs(mine[k]['net50'] - theirs[k]['net50']) for k in common), default=None)
    dmax0 = max((abs(mine[k]['net0'] - theirs[k]['net0']) for k in common), default=None)
    rmis = sum(1 for k in common if (mine[k]['reason'], mine[k]['flag'], mine[k]['exit_t']) !=
               (theirs[k]['reason'], theirs[k]['flag'], theirs[k]['exit_t']))
    r = {'eligible_points': ne, 'eligible_pairs': len(elig), 'chosen_points': sum(len(v) for v in ch.values()),
         'n_mine': len(tr), 'n_lb': len(lb), 'common': len(common), 'only_mine': [(k[0][:8], k[1]) for k in only_m],
         'only_lb': [(k[0][:8], k[1]) for k in only_t], 'max_abs_diff_net50': dmax, 'max_abs_diff_net0': dmax0,
         'reason_flag_exit_mismatch': rmis,
         'train': V.summ(trn), 'holdout': V.summ(ho), 'holdout_model': V.summ(ho, 'usd0'),
         'portfolio_holdout': V.portfolio(ho), 'secs': round(time.time() - t1, 1)}
    res[variant] = r
    print('==', variant, json.dumps({k: v for k, v in r.items()}, default=str), flush=True)

with open(V.OUT + '/v02_repro.json', 'w', encoding='utf-8') as fh:
    json.dump(res, fh, indent=1, default=str)
print('total', round(time.time() - t0, 1), 's')
