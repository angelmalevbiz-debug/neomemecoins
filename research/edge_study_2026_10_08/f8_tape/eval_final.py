"""ONE-SHOT evaluation of the 3 pre-selected configs (selection frozen on train) + baselines, train and holdout."""
import json, sys, time
sys.path.insert(0, (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r'')
sys.path.insert(0, (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r'/f8_tape')
import harness as H
import strategies as ST
import tsim, sig as S, tape_load as TL

series, meta = H.load()
cut = H.split_t(meta)
out = {}
t0 = time.time()


def pack(trades):
    ev = H.evaluate(trades)
    ho = [x for x in trades if x['entry_t'] >= cut]
    tr = [x for x in trades if x['entry_t'] < cut]
    return {'train': ev['train'], 'holdout': ev['holdout'], 'train_model': ev['train_model'], 'holdout_model': ev['holdout_model'],
            'portfolio_holdout': H.portfolio(ho, slots=3), 'portfolio_train': H.portfolio(tr, slots=3),
            'portfolio_all': H.portfolio(trades, slots=3)}


for cfg, base in zip(ST.CONFIGS, ST.BASELINES):
    tr = cfg['run'](H)
    res = pack(tr)
    # same frozen config under the harness's series-priced fills (diagnostic: stale-quote effect)
    fac = {'F8_WHALE_FOLLOW_5SOL': lambda: S.whale_signal(min_sol=5.0, side=1, **ST.COMMON),
           'F8_BIGSELL_DIPBUY_5SOL': lambda: S.whale_signal(min_sol=5.0, side=-1, **ST.COMMON),
           'F8_BIGSELL_DIPBUY_3SOL': lambda: S.whale_signal(min_sol=3.0, side=-1, **ST.COMMON)}[cfg['name']]
    trs = H.simulate(fac(), pairs=set(TL.load()['tape'].keys()), **cfg['kwargs'])
    ser = pack(trs)
    bt = base['run'](H)
    bres = pack(bt)
    out[cfg['name']] = {'onchain_fills': res, 'series_fills_diagnostic': {'train': ser['train'], 'holdout': ser['holdout']},
                        'baseline': bres,
                        'holdout_trades': [{k: (round(v, 3) if isinstance(v, float) else v) for k, v in x.items() if k in (
                            'pair', 'sym', 'entry_t', 'reason', 'flag', 'net50', 'net0', 'mfe', 'mae', 'hold_s', 'fee_bps', 'liq')}
                            for x in tr if x['entry_t'] >= cut]}
    for x in out[cfg['name']]['holdout_trades']:
        x['pair'] = x['pair'][:8]
    print('=====', cfg['name'])
    for part in ('train', 'holdout', 'holdout_model'):
        print('  %-14s' % part, json.dumps(res[part]))
    print('  portfolio holdout', res['portfolio_holdout'], 'train', res['portfolio_train'])
    print('  series-fill diag train mean %s n %s | holdout mean %s n %s' % (ser['train'].get('mean_pct'), ser['train'].get('n'),
                                                                            ser['holdout'].get('mean_pct'), ser['holdout'].get('n')))
    print('  BASELINE', base['name'])
    for part in ('train', 'holdout', 'holdout_model'):
        print('  %-14s' % part, json.dumps(bres[part]))
    print('  baseline portfolio holdout', bres['portfolio_holdout'])
    sys.stdout.flush()
json.dump(out, open((__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r'/f8_tape/results.json', 'w'),
          indent=1, default=str)
print('secs', round(time.time() - t0, 1))
