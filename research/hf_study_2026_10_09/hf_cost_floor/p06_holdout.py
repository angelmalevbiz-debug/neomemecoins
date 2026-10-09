"""p06: HOLDOUT for the 3 pre-registered configs (PREREG_holdout.json) + non-selected sensitivities. Run once."""
import collections, datetime, json, os, sys
sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hfsim as S
H = S.H

SPAN_TR = (S.CUT - S.T0) / 3.6e6
SPAN_HO = (S.T1 - S.CUT) / 3.6e6
CFG = {
    'HF1_CHEAPEST_REENTRY': ('a', ('time', 120), 5, 25.0, {}),
    'HF2_CHEAPEST_COOLDOWN300': ('c', ('time', 120), 3, 25.0, {'cooldown_s': 300}),
    'HF3_HF2_WITH_POOL_LOSS_MEMORY': ('c', ('time', 120), 3, 25.0, {'cooldown_s': 300, 'loss_memory': True}),
}
out = {}


def utc(t):
    return datetime.datetime.fromtimestamp(t / 1000, datetime.timezone.utc).strftime('%m-%d %H:%M')


def stats(tr, info, span, size):
    sm = S.summarize(tr, info, span, size)
    if tr:
        sm['ci95_mean_usd50_pair'] = H.cluster_ci_pair(tr, 'usd50')[:2]
        sm['ci95_mean_usd50_pairhour'] = H.cluster_ci(tr, 'usd50')[:2]
        v = sorted(x['net50'] for x in tr)
        k = max(1, len(v) // 100)
        sm['trimmed_mean_net50'] = round(sum(v[k:-k]) / len(v[k:-k]), 3) if len(v) > 2 * k else None
        g = [100 * (x['exit_px'] / x['entry_px'] - 1) for x in tr]
        sm['gross_move_mean'] = round(sum(g) / len(g), 3)
        sm['zero_move_share'] = round(sum(1 for x in tr if x['exit_px'] == x['entry_px']) / len(tr), 3)
        sm['calib_cost_pp'] = round(sum(x['net0'] - x['netcal'] for x in tr) / len(tr), 3)
        sm['stress_stop_cost_pp'] = round(sum(x['netcal'] - x['net50'] for x in tr) / len(tr), 3)
        # best pair removed
        cnt = collections.Counter(x['pair'] for x in tr)
        best = max(cnt, key=lambda p: sum(x['usd50'] for x in tr if x['pair'] == p))
        rest = [x for x in tr if x['pair'] != best]
        sm['mean_net50_best_pair_removed'] = round(sum(x['net50'] for x in rest) / len(rest), 3) if rest else None
        hrs = collections.Counter(int(x['dec_t'] // 3_600_000) for x in tr)
        sm['per_hour'] = [hrs.get(h, 0) for h in range(int(min(x['dec_t'] for x in tr) // 3_600_000),
                                                         int(max(x['dec_t'] for x in tr) // 3_600_000) + 1)]
    sm.pop('reasons', None)
    return sm


for name, (univ, spec, slots, size, kw) in CFG.items():
    res = {}
    for part, (a, b, span) in (('train', (S.T0, S.CUT, SPAN_TR)), ('holdout', (S.CUT, S.T1, SPAN_HO))):
        tr, info = S.run_book(univ, spec, slots, size, t_from=a, t_to=b, **kw)
        res[part] = stats(tr, info, span, size)
        if part == 'holdout':
            res['holdout']['exits'] = dict(collections.Counter(x['reason'] + ('/' + x['flag'] if x['flag'] else '') for x in tr))
    # exact $500 cash-limited book, holdout window and full span
    for part, (a, b) in (('cash500_holdout', (S.CUT, S.T1)), ('cash500_full', (S.T0, S.T1))):
        tr, info = S.run_book(univ, spec, slots, size, t_from=a, t_to=b, cash=500.0, **kw)
        pnl = sum(x['usd50'] for x in tr)
        mdd = S.max_drawdown(tr, 500.0)
        res[part] = {'n': len(tr), 'pnl_usd50': round(pnl, 2), 'dead_at': utc(info['dead_t']) if info['dead_t'] else None,
                     'hours_alive': round(((info['dead_t'] or b) - a) / 3.6e6, 2),
                     'final_free_cash': round(info['final_free'], 2), 'max_dd_usd': mdd[0], 'max_dd_pct': mdd[1]}
    # UTC-day loss caps on booked (usdcal) P&L, $500 cash-limited, full span
    for cap in (25.0, 50.0):
        tr, info = S.run_book(univ, spec, slots, size, t_from=S.T0, t_to=S.T1, cash=500.0, day_cap=cap, **kw)
        days = collections.defaultdict(list)
        for x in tr:
            days[int(x['dec_t'] // 86_400_000)].append(x)
        rows = {}
        for d, xs in sorted(days.items()):
            rows[datetime.datetime.fromtimestamp(d * 86400, datetime.timezone.utc).strftime('%Y-%m-%d')] = {
                'trades': len(xs), 'first': utc(min(x['dec_t'] for x in xs)), 'last': utc(max(x['dec_t'] for x in xs)),
                'active_h': round((max(x['exit_t'] for x in xs) - min(x['dec_t'] for x in xs)) / 3.6e6, 2),
                'usd50': round(sum(x['usd50'] for x in xs), 2), 'usdcal': round(sum(x['usdcal'] for x in xs), 2)}
        res['daycap_%d' % cap] = {'days': rows, 'capped_days': len(info['capped_days']), 'n': len(tr)}
    out[name] = res
    print(name, json.dumps(res['holdout'])[:1500], flush=True)

# non-selected sensitivity: notional scaling of HF1/HF2 holdout entries
scal = {}
for name in ('HF1_CHEAPEST_REENTRY', 'HF2_CHEAPEST_COOLDOWN300'):
    univ, spec, slots, _, kw = CFG[name]
    for size in (10.0, 25.0, 50.0, 100.0, 200.0):
        for part, (a, b, span) in (('train', (S.T0, S.CUT, SPAN_TR)), ('holdout', (S.CUT, S.T1, SPAN_HO))):
            tr, info = S.run_book(univ, spec, slots, size, t_from=a, t_to=b, **kw)
            sm = S.summarize(tr, info, span, size)
            scal['%s|%s|%d' % (name, part, size)] = {k: sm[k] for k in (
                'n', 'tph', 'mean_net50', 'mean_netcal', 'mean_net0', 'median_net50', 'mean_usd50', 'usd50_per_h',
                'usdcal_per_h', 'hours_to_bust_500', 'mean_rt_cost_model')}
            if part == 'holdout':
                print('scale', name, size, scal['%s|%s|%d' % (name, part, size)], flush=True)
out['notional_scaling'] = scal
json.dump(out, open(os.path.join(S.C.HERE, 'p06_holdout.json'), 'w'), indent=1)
print('done')
