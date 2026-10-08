"""ONE-TIME holdout evaluation of the 3 pre-selected F3 configs and their random baselines.
Configs were frozen in strategies.py before this script was run. Writes results.json."""
import sys, json, time
sys.path.insert(0, (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r'/f3_newpools')
import strategies as S
import f3lib as L
H = S.H
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
OUT = (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r'/f3_newpools/results.json'
series, meta = H.load()
CUT = H.split_t(meta)
res = {}


def run(entry, pooled_salts=None):
    t0 = time.time()
    kw = dict(entry['kwargs'])
    tr = H.simulate(entry['signal'], **kw)
    ev = H.evaluate(tr)
    tr_tr = [x for x in tr if x['entry_t'] < CUT]
    tr_ho = [x for x in tr if x['entry_t'] >= CUT]
    hold = kw.get('hold_min', 60)
    out = {'train': ev['train'], 'holdout': ev['holdout'], 'train_model': ev['train_model'], 'holdout_model': ev['holdout_model'],
           'robust_train': L.robust_view(tr_tr, hold), 'robust_holdout': L.robust_view(tr_ho, hold),
           'portfolio_holdout_slots3': H.portfolio(tr_ho, 3), 'portfolio_all_slots3': H.portfolio(tr, 3),
           'portfolio_holdout_slots3_model': H.portfolio(tr_ho, 3, key='usd0'),
           'holdout_trades': [{'pair': x['pair'][:8], 'sym': x['sym'], 'net50': round(x['net50'], 2), 'net0': round(x['net0'], 2),
                               'reason': x['reason'], 'flag': x['flag'], 'hold_min': round(x['hold_s'] / 60, 1),
                               'mfe': round(x['mfe'], 1), 'fee': x['fee_bps'], 'liq': round(x['liq'])} for x in tr_ho]}
    print('==', entry['name'], 'secs %.0f' % (time.time() - t0))
    for part in ('train', 'holdout', 'holdout_model'):
        print('  ', part, json.dumps(L.brief(out[part])))
    print('   robust_train', out['robust_train'], 'robust_holdout', out['robust_holdout'])
    print('   portfolio holdout', out['portfolio_holdout_slots3'], 'all', out['portfolio_all_slots3'])
    for x in out['holdout_trades']:
        print('      ', x)
    sys.stdout.flush()
    return out


for c in S.CONFIGS:
    res[c['name']] = run(c)
for b in S.BASELINES:
    res[b['name']] = run(b)

# pooled random reference: 10 salts at the same probability, same universe and exits (stable random mean)
for name, age, prob, ek in (('POOLED10_RANDOM_U24H_TRAIL20', 1440, 0.002, S.E1), ('POOLED10_RANDOM_U24H_WIDETRAIL40', 1440, 0.002, S.E2),
                            ('POOLED10_RANDOM_U2H_TRAIL20', 120, 0.005, S.E1)):
    allt = []
    for k in range(10):
        allt += H.simulate(S.random_in(age, prob, 'pool%d' % k), exit_fn=S.drain_exit(0.6), size_fn=S.size_fn, **ek)
    ev = H.evaluate(allt)
    res[name] = {'train': ev['train'], 'holdout': ev['holdout'], 'holdout_model': ev['holdout_model']}
    print('==', name)
    for part in ('train', 'holdout', 'holdout_model'):
        print('  ', part, json.dumps(L.brief(ev[part])))
with open(OUT, 'w', encoding='utf-8') as fh:
    json.dump(res, fh, indent=1, ensure_ascii=False, default=str)
print('saved')
