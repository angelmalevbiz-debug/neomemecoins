"""THE single holdout evaluation of the 3 pre-selected configs + random baselines. Run once; no iteration."""
import json, sys, time
sys.path.insert(0, (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r'/f2_meanrev')
import strategies as S
H = S.H
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
OUT = (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r'/f2_meanrev/holdout_results.json'
series, meta = H.load()
CUT = H.split_t(meta)
KEEP = ('n', 'pairs', 'clusters', 'win_rate', 'mean_usd', 'mean_pct', 'median_pct', 'sum_usd', 'pf', 'ci95_mean_usd',
        'trades_per_hour', 'avg_hold_min', 'top_pair_share', 'exits')
res = {}
t0 = time.time()


def run(c):
    tr = H.simulate(c['signal'], **c['kwargs'])
    ev = H.evaluate(tr)
    ho = [x for x in tr if x['entry_t'] >= CUT]
    out = {k: {kk: ev[k].get(kk) for kk in KEEP} for k in ev}
    out['portfolio_holdout_slots3'] = H.portfolio(ho, slots=3)
    out['portfolio_holdout_slots3_model'] = H.portfolio(ho, slots=3, key='usd0')
    out['portfolio_all_slots3'] = H.portfolio(tr, slots=3)
    out['holdout_trades'] = [{'sym': x['sym'], 'pair': x['pair'][:8], 'h': round((x['entry_t'] - meta['t0']) / 3.6e6, 2),
                              'net50': round(x['net50'], 2), 'net0': round(x['net0'], 2), 'reason': x['reason'], 'flag': x['flag'],
                              'hold_min': round(x['hold_s'] / 60, 1), 'fee': x['fee_bps'], 'liq': round(x['liq'])} for x in ho]
    return out


for c in S.CONFIGS + S.BASELINES:
    res[c['name']] = run(c)
    r = res[c['name']]
    print('==', c['name'])
    for part in ('train', 'holdout', 'holdout_model'):
        print('   ', part, r[part])
    print('    portfolio holdout slots3', r['portfolio_holdout_slots3'], 'model', r['portfolio_holdout_slots3_model'])
    for x in r['holdout_trades']:
        print('      ', x)
    print('    secs', round(time.time() - t0, 1), flush=True)

# extra random seeds for baseline stability (holdout means only)
extra = {}
for name, kw, ex in (('ctx1h', dict(pc1h_max=-12.0), S.EXIT_A), ('ctxcombo', dict(pc1h_max=-12.0, pc6h_max=-15.0), S.EXIT_B),
                     ('universe', dict(), S.EXIT_A)):
    prob = {'ctx1h': 0.005, 'ctxcombo': 0.004, 'universe': 0.0005}[name]
    rows = []
    for salt in ('f2r2', 'f2r3', 'f2r4', 'f2r5', 'f2r6'):
        tr = H.simulate(S.make_random(prob, salt, **kw), **ex)
        ev = H.evaluate(tr)
        rows.append({'salt': salt, 'train_mean': ev['train'].get('mean_pct'), 'holdout_n': ev['holdout'].get('n'),
                     'holdout_mean': ev['holdout'].get('mean_pct'), 'holdout_median': ev['holdout'].get('median_pct'),
                     'holdout_pairs': ev['holdout'].get('pairs')})
    extra[name] = rows
    hm = [r['holdout_mean'] for r in rows if r['holdout_mean'] is not None]
    print('extra random', name, rows, 'avg holdout mean', round(sum(hm) / len(hm), 3) if hm else None, flush=True)
res['extra_random_seeds'] = extra
with open(OUT, 'w', encoding='utf-8') as fh:
    json.dump(res, fh, indent=1, default=str)
print('done secs', round(time.time() - t0, 1))
