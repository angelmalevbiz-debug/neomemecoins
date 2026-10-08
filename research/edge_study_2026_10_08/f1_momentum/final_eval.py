"""Holdout evaluation of the (fixed, train-selected) F1 configs and their random baselines. Run ONCE."""
import bisect, json, os, sys, time
sys.path.insert(0, (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r'')
import harness as H
import strategies as S

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
HERE = os.path.dirname(os.path.abspath(__file__))
series, meta = H.load()
cut = H.split_t(meta)


def gross(tr):
    """Mean pre-cost price move entry->exit fill (%), to separate 'no signal' from 'signal eaten by costs'."""
    xs = []
    for x in tr:
        s = series[x['pair']]
        ts = s['t']
        a = bisect.bisect_left(ts, x['entry_t'])
        b = bisect.bisect_left(ts, x['exit_t'])
        pa, pb = s['price'][a], s['price'][b]
        if pa > 0 and pb > 0:
            xs.append(100 * (pb / pa - 1) - (H.VANISH_HAIRCUT_PCT if x['flag'] == 'VANISHED' else 0))
    return round(sum(xs) / len(xs), 3) if xs else None


def pack(sm):
    return {k: sm.get(k) for k in ('n', 'pairs', 'clusters', 'win_rate', 'mean_usd', 'mean_pct', 'median_pct', 'sum_usd', 'pf',
                                   'ci95_mean_usd', 'trades_per_hour', 'avg_hold_min', 'top_pair_share', 'exits')}


out = {}
for group in ('CONFIGS', 'BASELINES'):
    for cfg in getattr(S, group):
        t0 = time.time()
        tr = cfg['run'](H) if 'run' in cfg else H.simulate(cfg['signal'], **cfg['kwargs'])
        ev = H.evaluate(tr)
        ho = [x for x in tr if x['entry_t'] >= cut]
        trn = [x for x in tr if x['entry_t'] < cut]
        rec = {'group': group, 'train': pack(ev['train']), 'holdout': pack(ev['holdout']),
               'train_model': pack(ev['train_model']), 'holdout_model': pack(ev['holdout_model']),
               'gross_move_pct_train': gross(trn), 'gross_move_pct_holdout': gross(ho),
               'portfolio_holdout_slots3_net50': H.portfolio(ho, slots=3),
               'portfolio_holdout_slots3_net0': H.portfolio(ho, slots=3, key='usd0'),
               'portfolio_all_slots3_net50': H.portfolio(tr, slots=3),
               'secs': round(time.time() - t0, 1)}
        bp = {}
        for x in ho:
            bp[x['pair']] = bp.get(x['pair'], 0) + x['usd50']
        top = sorted(bp.items(), key=lambda kv: -kv[1])
        rec['holdout_top_pairs_usd50'] = [(p[:8], round(v, 1)) for p, v in top[:3]]
        if top:
            keep = set(p for p, _ in top[1:])
            sub = [x for x in ho if x['pair'] in keep]
            rec['holdout_mean_pct_drop_top1'] = round(sum(x['net50'] for x in sub) / len(sub), 3) if sub else None
        out[cfg['name']] = rec
        print(json.dumps({cfg['name']: rec}), flush=True)
with open(os.path.join(HERE, 'final_eval.json'), 'w', encoding='utf-8') as fh:
    json.dump(out, fh, indent=1)
