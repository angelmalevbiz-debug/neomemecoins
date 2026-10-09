"""hf_synthesis HOLDOUT (run once). PAPER research only.

Pre-registered (PREREG_holdout.json, written with this file's sha256 before the run): three configs under the shared
book rules chosen on TRAIN by the frozen v2 rule (train/chosen_v2.json):
  shared: E95 universe (engine-equivalent STRUCTURAL_RUG_GUARD_V1, PumpSwap SOL, liq >= $50k, fee <= 95 bps),
          HEAT_VETO_STACK enforced, decisions at refresh events only, $25 notional, time exit 120 s from the
          decision, 3 slots, one position per pool, slot busy until the exit fill, 120 s pool cooldown after the exit
          fill, <= 50 orders per trailing 60 min, <= 1 order per 2-s refresh, no loss brake (HF_POOL_RULE_V1).
  1 HF_RND_E95   random control: hashed coin p = 0.25 per refresh event, 5 salts (hfA..hfE) reported as mean and
                 per salt; salt hfA is the paired control for gap CIs.
  2 HF_QUIET_E95 v5/liq <= 0.002 and |refresh step| <= 0.6 %.
  3 HF_DIP15_E95 price <= 1.002 x its 15-min low (NEAR_15M_LOW).
Overlays on the same configs (no new selection): UTC-day booked loss cap $100 on a $1,000 book with a 50 % floor;
POOL_LOSS_MEMORY_V1 emulation (2 booked losses -> 6 h) to document why HF replaces it.
"""
import json, os, sys, time
import syn_lib as L

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
T0 = time.time()
d = L.S.events()
rows, cut, meta = d['rows'], d['cut'], d['meta']
t1 = meta['t1']
uni = L.UNIVERSES['E95']
LIVE = L.live_hours(rows, uni, cut, t1)
h0, h1 = int(-(-cut // L.HOUR)), int(t1 // L.HOUR)
print('holdout %.2f h, full hours %d, live hours %d' % ((t1 - cut) / L.HOUR, h1 - h0, len(LIVE)), flush=True)
SHARED = dict(slots=3, hold_s=120, cooldown_s=120, pacing=('roll', 3600, 50), size=25.0, heat_on=True)
SALTS = ('hfA', 'hfB', 'hfC', 'hfD', 'hfE')
BOOKS = [('HF_RND_E95', None), ('HF_QUIET_E95', 'QUIET'), ('HF_DIP15_E95', 'NEAR15LOW')]
OUT = {'meta': {'cut': cut, 't1': t1, 'shared': {**SHARED, 'pacing': list(SHARED['pacing'])}}, 'books': {}}


def run(pred, **kw):
    return L.run_book(rows, uni, pred, t_from=cut, **{**SHARED, **kw})


def fee_split(tr):
    out = {}
    for name, f in (('fee<=50', lambda x: x['fee_bps'] <= 50), ('fee55-95', lambda x: x['fee_bps'] > 50)):
        xs = [x for x in tr if f(x)]
        out[name] = {'n': len(xs), 'mean_net50': round(L.mean(x['net50'] for x in xs), 3) if xs else None,
                     'mean_gross': round(L.mean(x['gross'] for x in xs), 3) if xs else None}
    return out


def per_day(tr):
    out = {}
    for x in tr:
        dd = int(x['decision_t'] // L.DAY)
        o = out.setdefault(dd, {'n': 0, 'booked': 0.0, 'usd50': 0.0, 'first': x['decision_t'], 'last': x['decision_t']})
        o['n'] += 1
        o['booked'] += x['usdc']
        o['usd50'] += x['usd50']
        o['last'] = x['decision_t']
    return {str(k): {'n': v['n'], 'booked_usd': round(v['booked'], 2), 'usd50': round(v['usd50'], 2),
                     'active_h': round((v['last'] - v['first']) / L.HOUR, 2)} for k, v in sorted(out.items())}


trades = {}
for name, sig in BOOKS:
    if sig is None:
        per, trs = [], []
        for salt in SALTS:
            tr, c = run(L.rnd(0.25, salt))
            st = L.stats(tr, cut, t1, ci=True, live=LIVE)
            st['counters'] = c
            per.append(st)
            trs.append(tr)
        trades[name] = trs[0]
        keys = ('tph', 'tph_live', 'live_hour_p10', 'live_hour_median', 'live_hours_ge40', 'max_hour', 'mean_net50',
                'median_net50', 'mean_booked', 'mean_net0', 'mean_gross', 'mean_dp50', 'usd50_per_trade',
                'booked_usd_per_trade', 'usd50_per_hour', 'booked_usd_per_hour', 'usd0_per_hour', 'win50', 'win_booked',
                'win0', 'pairs', 'top_pair_share', 'fee_le50_share', 'median_liq', 'reentry_le60s_share',
                'reentry_le300s_share', 'mean_cycle_s', 'mean_entry_lag_s', 'mean_exit_lag_s', 'entry_quiet_share',
                'mdd_booked_usd', 'mdd_net50_usd')
        m = {k: round(sum(p[k] for p in per) / len(per), 3) for k in keys}
        OUT['books'][name] = {'mean_of_salts': m, 'per_salt': per, 'fee_split_hfA': fee_split(trs[0]),
                              'per_day_hfA': per_day(trs[0])}
        st = per[0]
    else:
        tr, c = run(L.PREDICATES[sig])
        trades[name] = tr
        st = L.stats(tr, cut, t1, ci=True, live=LIVE)
        st['counters'] = c
        OUT['books'][name] = {'stats': st, 'fee_split': fee_split(tr), 'per_day': per_day(tr)}
        m = st
    print('== %s: n %s tph %.1f live %.1f (p10 %s, median %s, ge40 %.2f, max %s) net50 %+.3f (median %+.3f) booked %+.3f net0 %+.3f gross %+.3f dp50 %+.3f | $/trade net50 %+.3f booked %+.3f | $/h net50 %+.2f booked %+.2f net0 %+.2f | win50 %.2f win0 %.2f | pairs %s top %.3f le50 %.2f re300 %.2f cycle %.0fs entry lag %.1fs exit lag %.1fs | mdd booked $%.0f' % (
        name, m.get('n', '-'), m['tph'], m['tph_live'], m['live_hour_p10'], m['live_hour_median'], m['live_hours_ge40'],
        m['max_hour'], m['mean_net50'], m['median_net50'], m['mean_booked'], m['mean_net0'], m['mean_gross'], m['mean_dp50'],
        m['usd50_per_trade'], m['booked_usd_per_trade'], m['usd50_per_hour'], m['booked_usd_per_hour'],
        m['usd0_per_hour'], m['win50'], m['win0'], m['pairs'], m['top_pair_share'], m['fee_le50_share'],
        m['reentry_le300s_share'], m['mean_cycle_s'], m['mean_entry_lag_s'], m['mean_exit_lag_s'], m['mdd_booked_usd']),
        flush=True)
    if sig is None:
        print('   salts net50', [p['mean_net50'] for p in per], 'tph', [p['tph'] for p in per],
              'ci net50 (hfA)', per[0]['ci95_net50_pct_pair'], 'ci usd50 (hfA)', per[0]['ci95_usd50_pair'])
    else:
        print('   ci net50', st['ci95_net50_pct_pair'], 'ci usd50', st['ci95_usd50_pair'], 'best pair removed',
              st['best_pair_removed_net50'], 'exits', st['exits'])
    print('   fee split', OUT['books'][name].get('fee_split') or OUT['books'][name].get('fee_split_hfA'))
    print('   per day', OUT['books'][name].get('per_day') or OUT['books'][name].get('per_day_hfA'))

ctrl = trades['HF_RND_E95']
OUT['gaps'] = {}
for name in ('HF_QUIET_E95', 'HF_DIP15_E95'):
    g = {'net50_point_vs_salt_mean': round(OUT['books'][name]['stats']['mean_net50']
                                           - OUT['books']['HF_RND_E95']['mean_of_salts']['mean_net50'], 3),
         'gross_point_vs_salt_mean': round(OUT['books'][name]['stats']['mean_gross']
                                           - OUT['books']['HF_RND_E95']['mean_of_salts']['mean_gross'], 3),
         'net50_ci_vs_hfA': L.gap_ci(trades[name], ctrl, 'net50'), 'gross_ci_vs_hfA': L.gap_ci(trades[name], ctrl, 'gross')}
    # stratified (within fee bucket) net50 gap: removes the pool-cost selection effect
    strat = {}
    for b, f in (('fee<=50', lambda x: x['fee_bps'] <= 50), ('fee55-95', lambda x: x['fee_bps'] > 50)):
        a = [x for x in trades[name] if f(x)]
        c = [x for x in ctrl if f(x)]
        if a and c:
            strat[b] = {'gap_net50': round(L.mean(x['net50'] for x in a) - L.mean(x['net50'] for x in c), 3),
                        'ci': L.gap_ci(a, c, 'net50'), 'n': [len(a), len(c)]}
    g['within_fee_bucket'] = strat
    OUT['gaps'][name] = g
    print('GAP %s vs HF_RND_E95: %s' % (name, json.dumps(g)), flush=True)

# ------------------------------------------------------------------ overlays (same configs)
OUT['cap_overlay'] = {}
for name, sig in BOOKS:
    pred = L.rnd(0.25, 'hfA') if sig is None else L.PREDICATES[sig]
    tr, c = run(pred, cap_usd=100.0)
    days = per_day(tr)
    st = L.stats(tr, cut, t1, live=LIVE)
    OUT['cap_overlay'][name] = {'n': len(tr), 'cap_blocked': c['cap'], 'days': days,
                                'booked_total': round(sum(x['usdc'] for x in tr), 2),
                                'net50_total': round(sum(x['usd50'] for x in tr), 2),
                                'mdd_booked_usd': st.get('mdd_booked_usd'), 'tph': st.get('tph')}
    print('CAP $100/UTC day %s: n %d, days %s, booked %.2f net50 %.2f mdd booked %s' % (
        name, len(tr), days, OUT['cap_overlay'][name]['booked_total'], OUT['cap_overlay'][name]['net50_total'],
        st.get('mdd_booked_usd')), flush=True)
OUT['plm_overlay'] = {}
for name, sig in BOOKS:
    pred = L.rnd(0.25, 'hfA') if sig is None else L.PREDICATES[sig]
    tr, c = run(pred, brake=(2, 6 * L.HOUR))
    st = L.stats(tr, cut, t1, live=LIVE)
    OUT['plm_overlay'][name] = {'n': len(tr), 'tph': st.get('tph'), 'mean_net50': st.get('mean_net50'),
                                'blocked': c['brake']}
    print('POOL_LOSS_MEMORY_V1 emulation %s: n %d tph %s net50 %s blocked %d' % (
        name, len(tr), st.get('tph'), st.get('mean_net50'), c['brake']), flush=True)
json.dump(OUT, open(os.path.join(L.HERE, 'holdout_results.json'), 'w'), indent=1, default=str)
print('secs', round(time.time() - T0, 1))
