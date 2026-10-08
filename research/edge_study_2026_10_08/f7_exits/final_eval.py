"""THE ONE HOLDOUT LOOK: run the pre-registered CONFIGS and BASELINES of strategies.py over the full span with
H.simulate, split train/holdout with H.evaluate, and add portfolio, tail-share and vanish-haircut sensitivity."""
import json, sys, time
sys.path.insert(0, (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r'')
sys.path.insert(0, (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r'/f7_exits')
import harness as H
import strategies as S

OUT = (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r'/f7_exits/final_eval.json'
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
series, meta = H.load()
cut = H.split_t(meta)


def tail(trades, key='usd50'):
    if not trades:
        return {}
    usd = sorted(x[key] for x in trades)
    k = max(1, int(round(0.05 * len(usd))))
    gains = sum(u for u in usd if u > 0)
    return {'top5pct_trades': k, 'top5pct_sum_usd': round(sum(usd[-k:]), 2),
            'top5pct_share_of_gross_gains': round(sum(usd[-k:]) / gains, 3) if gains > 0 else None,
            'sum_usd_without_top5pct': round(sum(usd[:-k]), 2)}


def run(entry):
    t0 = time.time()
    trades = H.simulate(entry['signal'], **entry['kwargs'])
    ev = H.evaluate(trades)
    tr = [x for x in trades if x['entry_t'] < cut]
    ho = [x for x in trades if x['entry_t'] >= cut]
    res = {'name': entry['name'], 'n_total': len(trades), 'eval': ev,
           'portfolio_train': H.portfolio(tr, slots=3), 'portfolio_holdout': H.portfolio(ho, slots=3),
           'portfolio_holdout_model': H.portfolio(ho, slots=3, key='usd0'),
           'tail_train': tail(tr), 'tail_holdout': tail(ho),
           'vanished_share_holdout': round(sum(1 for x in ho if x['flag'] == 'VANISHED') / len(ho), 3) if ho else None}
    sens = {}
    orig = H.VANISH_HAIRCUT_PCT
    try:
        for hc in (0.0, 30.0):
            H.VANISH_HAIRCUT_PCT = hc
            t2 = H.simulate(entry['signal'], **entry['kwargs'])
            h2 = [x for x in t2 if x['entry_t'] >= cut]
            r2 = [x for x in t2 if x['entry_t'] < cut]
            sens['haircut_%d' % hc] = {'train_mean_pct50': H.summarize(r2).get('mean_pct'),
                                       'holdout_mean_pct50': H.summarize(h2).get('mean_pct')}
    finally:
        H.VANISH_HAIRCUT_PCT = orig
    res['vanish_sensitivity'] = sens
    res['secs'] = round(time.time() - t0, 1)
    return res


keep = ('n', 'pairs', 'clusters', 'win_rate', 'mean_pct', 'median_pct', 'sum_usd', 'pf', 'ci95_mean_usd',
        'trades_per_hour', 'avg_hold_min', 'top_pair_share', 'exits')
results = {'configs': [], 'baselines': []}
for group, entries in (('configs', S.CONFIGS), ('baselines', S.BASELINES)):
    for e in entries:
        r = run(e)
        results[group].append(r)
        print('==', group, r['name'], 'n_total', r['n_total'], 'secs', r['secs'])
        for part in ('train', 'holdout', 'holdout_model'):
            print('   %-14s' % part, json.dumps({k: r['eval'][part].get(k) for k in keep}))
        print('   portfolio train', r['portfolio_train'], '| holdout', r['portfolio_holdout'], '| holdout model', r['portfolio_holdout_model'])
        print('   tail train', r['tail_train'], '| tail holdout', r['tail_holdout'])
        print('   vanished share holdout', r['vanished_share_holdout'], '| vanish haircut sensitivity', r['vanish_sensitivity'])

for c, b in zip(results['configs'], results['baselines']):
    tr, ho, bho = c['eval']['train'], c['eval']['holdout'], b['eval']['holdout']
    checks = {
        'holdout_n>=20': ho.get('n', 0) >= 20,
        'holdout_pairs>=8': ho.get('pairs', 0) >= 8,
        'holdout_mean50>0': (ho.get('mean_pct') or -1e9) > 0,
        'train_mean50>0': (tr.get('mean_pct') or -1e9) > 0,
        'top_pair_share<=0.35': (ho.get('top_pair_share') or 1) <= 0.35,
        'beats_random_baseline_holdout': (ho.get('mean_pct') or -1e9) > (bho.get('mean_pct') or -1e9),
    }
    c['candidate_checks'] = checks
    c['is_candidate'] = all(checks.values())
    print('CANDIDATE?', c['name'], c['is_candidate'], checks, '| baseline holdout mean%', bho.get('mean_pct'))
with open(OUT, 'w', encoding='utf-8') as fh:
    json.dump(results, fh, indent=1, default=str)
