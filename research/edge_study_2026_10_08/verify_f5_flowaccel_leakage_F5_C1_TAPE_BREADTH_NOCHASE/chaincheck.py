"""Second pass: on-chain cross-check of the holdout fills, guard/screen ablations, tape coverage over time."""
import sys
sys.dont_write_bytecode = True
import bisect, importlib.util, json, os, pickle, time

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
OUT = (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r'/verify_f5_flowaccel_leakage_F5_C1_TAPE_BREADTH_NOCHASE'
sys.path.insert(0, OUT)
import reimpl as R   # re-runs reimpl.py top to bottom (cheap, ~50 s) and exposes its objects

H, series, TAPE, CUT, meta = R.H, R.series, R.TAPE, R.CUT, R.meta
mine_g = pickle.load(open(OUT + '/reimpl_trades.pkl', 'rb'))['guard']
mine_o = pickle.load(open(OUT + '/reimpl_trades.pkl', 'rb'))['orig']
res = {}


def tape_slice(pair, a, b):
    d = TAPE.get(pair)
    if d is None:
        return []
    et = d['et']
    lo, hi = bisect.bisect_left(et, a), bisect.bisect_right(et, b)
    return [(et[k], d['av'][k], d['sgn'][k], d['q'][k], d['tok'][k]) for k in range(lo, hi)]


def px_near(pair, t, before_s, after_s):
    ev = [e for e in tape_slice(pair, t - before_s * 1000, t + after_s * 1000) if e[4] and e[4] > 0 and e[3] > 0]
    if not ev:
        return None, 0
    # nearest-in-time swap prices (median of the 5 closest)
    ev.sort(key=lambda e: abs(e[0] - t))
    px = sorted(e[3] / e[4] for e in ev[:5])
    return px[len(px) // 2], len(ev)


rows = []
for x in [y for y in mine_g if y['entry_t'] >= CUT]:
    pair = x['pair']
    s = series[pair]
    d = TAPE.get(pair)
    j = bisect.bisect_left(s['t'], x['entry_t'])
    e = bisect.bisect_left(s['t'], x['exit_t'])
    during = tape_slice(pair, x['entry_t'], x['exit_t'])
    pe, ne = px_near(pair, x['entry_t'], 300, 300)
    px_, nx = px_near(pair, x['exit_t'], 300, 300)
    ds_e, ds_x = s['pnative'][j], s['pnative'][e]
    # last tape event (by block time) at or before exit, and first after entry
    lastev = None
    if d:
        k = bisect.bisect_right(d['et'], x['exit_t']) - 1
        lastev = (x['exit_t'] - d['et'][k]) / 1000 if k >= 0 else None
    rows.append({'pair': pair[:8], 'sym': x['sym'], 'reason': x['reason'], 'net50': round(x['net50'], 2),
                 'hold_min': round(x['hold_s'] / 60, 1),
                 'tape_swaps_during_trade': len(during),
                 'tape_last_swap_before_exit_s': round(lastev, 1) if lastev is not None else None,
                 'ds_entry_vs_tape_pct': round(100 * (ds_e / pe - 1), 2) if pe else None,
                 'ds_exit_vs_tape_pct': round(100 * (ds_x / px_ - 1), 2) if px_ else None,
                 'ds_gross_ret_pct': round(100 * (ds_x / ds_e - 1), 2),
                 'tape_gross_ret_pct': round(100 * (px_ / pe - 1), 2) if (pe and px_) else None,
                 'tape_n_entry_win': ne, 'tape_n_exit_win': nx})
res['holdout_onchain'] = rows
for r in rows:
    print('CHAIN', r)

# tape coverage per hour (events by block time) and tape-live pools per hour among C1-universe pools
T0, T1 = meta['t0'], meta['t1']
hours = int((T1 - T0) // 3_600_000) + 1
cov = [0] * hours
pools_h = [set() for _ in range(hours)]
for pair, d in TAPE.items():
    for et in d['et']:
        if T0 <= et <= T1:
            h = int((et - T0) // 3_600_000)
            cov[h] += 1
            pools_h[h].add(pair)
res['tape_events_per_hour'] = cov
res['tape_pools_per_hour'] = [len(p) for p in pools_h]
print('events/h', cov)
print('pools/h', [len(p) for p in pools_h])
print('cut hour', (CUT - T0) / 3.6e6)

# ingestion lag distribution (available - event_time) by train/holdout
import statistics
lag_tr, lag_ho = [], []
for pair, d in TAPE.items():
    for et, av in zip(d['et'], d['av']):
        if T0 <= et < CUT:
            lag_tr.append((av - et) / 1000)
        elif CUT <= et <= T1:
            lag_ho.append((av - et) / 1000)


def q(xs, p):
    xs = sorted(xs)
    return xs[int(p * (len(xs) - 1))] if xs else None


res['ingest_lag_s'] = {'train': {'n': len(lag_tr), 'p50': q(lag_tr, .5), 'p90': q(lag_tr, .9), 'p99': q(lag_tr, .99)},
                       'holdout': {'n': len(lag_ho), 'p50': q(lag_ho, .5), 'p90': q(lag_ho, .9), 'p99': q(lag_ho, .99)}}
print('ingest lag', res['ingest_lag_s'])

# ablations: what the screens do to C1 trades
abl = {}
sig_noint = R.make_signal(guard=True)
orig_interim = H.interim_rug_risk
H.interim_rug_risk = lambda P: False
try:
    tr = H.simulate(sig_noint, **R.EX)
finally:
    H.interim_rug_risk = orig_interim
abl['guard_without_interim_screen'] = {'train': R.brief(R.split(tr)[0]), 'holdout': R.brief(R.split(tr)[1])}
print('ablation no interim', abl['guard_without_interim_screen']['holdout'].get('n'),
      abl['guard_without_interim_screen']['holdout'].get('mean_usd'),
      'train', abl['guard_without_interim_screen']['train'].get('n'), abl['guard_without_interim_screen']['train'].get('mean_usd'))
# guard reasons for the orig trades the guard removed
removed = []
gk = {(x['pair'], x['entry_t']) for x in mine_g}
for x in mine_o:
    if (x['pair'], x['entry_t']) in gk:
        continue
    s = series[x['pair']]
    i = bisect.bisect_left(s['t'], x['decision_t'])
    P = H.Past(s, i)
    removed.append({'pair': x['pair'][:8], 'sym': x['sym'], 'part': 'train' if x['entry_t'] < CUT else 'holdout',
                    'reasons': H.rug_reasons(P), 'age_min': round(P('age'), 1), 'liq_mcap': round(P('liq') / P('mcap'), 3),
                    'usd50': round(x['usd50'], 2)})
res['guard_removed'] = removed
agg = {}
for r in removed:
    k = (r['part'], tuple(r['reasons']))
    a = agg.setdefault(k, [0, 0.0])
    a[0] += 1
    a[1] += r['usd50']
for k, v in sorted(agg.items()):
    print('REMOVED', k, 'n', v[0], 'sum usd50', round(v[1], 2))
res['guard_removed_agg'] = {'%s|%s' % (k[0], '+'.join(k[1])): {'n': v[0], 'sum_usd50': round(v[1], 2)} for k, v in agg.items()}
# pure YOUNG_POOL variant and structural-only variant, for context (no selection made on these)
for name, g in (('structural_only', H.rug_guard_structural),):
    def sg(P, base=R.make_signal(guard=False), g=g):
        return base(P) and not g(P)
    tr = H.simulate(sg, **R.EX)
    abl[name] = {'train': R.brief(R.split(tr)[0]), 'holdout': R.brief(R.split(tr)[1])}
    print('ablation', name, 'holdout', abl[name]['holdout'].get('n'), abl[name]['holdout'].get('mean_usd'),
          'train', abl[name]['train'].get('n'), abl[name]['train'].get('mean_usd'))
res['ablations'] = abl

# perturbation stability: small threshold changes around the frozen rule (NOT selection; reported as a sensitivity map)
pert = []
base_kw = dict(ub=10, newb=5, top=0.3, lo=-2.0, hi=5.0)


def make_var(ub, newb, top, lo, hi):
    st = R.FlowState()

    def sig(P):
        liq = P('liq')
        if not (liq >= 20_000) or not (52.5 <= P.fee_bps() <= 125) or H.interim_rug_risk(P):
            return False
        ft = st.features(P.static('pair'), P.t)
        if ft is None or ft['age_s'] > 600:
            return False
        p0, p1 = P('price'), R.price_ago(P, 300)
        if not (R.fin(p0) and R.fin(p1) and p1 > 0 and lo <= 100 * (p0 / p1 - 1) <= hi):
            return False
        if not (ft['ub'] >= ub and ft['newb'] >= newb and R.fin(ft['top_share']) and ft['top_share'] < top
                and ft['net_sol'] > 0):
            return False
        return not H.rug_guard_v1(P)
    return sig


variants = [dict(base_kw, ub=8), dict(base_kw, ub=12), dict(base_kw, newb=3), dict(base_kw, newb=7),
            dict(base_kw, top=0.25), dict(base_kw, top=0.35), dict(base_kw, lo=-3.0), dict(base_kw, hi=4.0),
            dict(base_kw, hi=6.0)]
for v in variants:
    tr = H.simulate(make_var(**v), **R.EX)
    trn, ho = R.split(tr)
    row = {'params': v, 'train_n': len(trn), 'train_mean_usd': R.brief(trn).get('mean_usd'),
           'holdout_n': len(ho), 'holdout_pairs': R.brief(ho).get('pairs'), 'holdout_mean_usd': R.brief(ho).get('mean_usd')}
    pert.append(row)
    print('PERT', row)
# exit perturbations
for kw in (dict(stop=-8.0, tp=6.0, hold_min=30.0), dict(stop=-10.0, tp=5.0, hold_min=30.0),
           dict(stop=-10.0, tp=8.0, hold_min=30.0), dict(stop=-10.0, tp=6.0, hold_min=20.0),
           dict(stop=-10.0, tp=6.0, hold_min=45.0)):
    tr = H.simulate(R.make_signal(guard=True), size_fn=R.size_fn, **kw)
    trn, ho = R.split(tr)
    row = {'exits': kw, 'train_n': len(trn), 'train_mean_usd': R.brief(trn).get('mean_usd'),
           'holdout_n': len(ho), 'holdout_mean_usd': R.brief(ho).get('mean_usd')}
    pert.append(row)
    print('PERT', row)
res['perturbations'] = pert
with open(OUT + '/chaincheck_results.json', 'w', encoding='utf-8') as fh:
    json.dump(res, fh, indent=1, default=str)
print('done')
