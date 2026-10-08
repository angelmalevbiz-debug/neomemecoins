"""Step 5: (a) how long had the tape been watching the pool when C1 fired (is 'first-time buyer' a coverage-start
artifact?); (b) holdout re-priced at on-chain prices: entry-only adjustment (truth at decision + 2 s, 20 s window) and
entry+exit with a 60 s truth window; (c) the C1 rule restricted to pools watched for >= 15 / 30 min."""
import sys
sys.dont_write_bytecode = True
import bisect, importlib.util, json, math, os, pickle, sqlite3, time

DEEP = (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r''
OUT = DEEP + '/verify_f5_flowaccel_leakage_F5_C1_TAPE_BREADTH_NOCHASE'
sys.stdout.reconfigure(encoding='utf-8', errors='replace')


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    sys.modules[name] = m
    spec.loader.exec_module(m)
    return m


H = _load('harness_final', DEEP + '/leaderboard/harness_final.py')
sys.modules['harness'] = H
sys.path.insert(0, OUT)
import indep as I  # noqa: E402

series, meta = H.load()
CUT = H.split_t(meta)
T = I.load_tape()
c1 = pickle.load(open(os.path.join(OUT, 'trades_orig_rerun.pkl'), 'rb'))
RES = {}


def watch_age_min(pair, t, lag=0):
    """Minutes since the earliest block time among tape events of this pair ingested by t (coverage age)."""
    d = T.get(pair)
    k = bisect.bisect_right(d['avs'], t - lag)
    if k == 0:
        return None
    # earliest et among events with av <= t: prefix min over the av-sorted order
    order_et = sorted(zip(d['av'], d['et']))
    m = min(e for a, e in order_et[:k])
    return (t - m) / 60000.0


rows = []
for x in c1:
    wa = watch_age_min(x['pair'], x['decision_t'])
    fl = I.flow(x['pair'], x['decision_t'], 300, 0, 'event')
    rows.append({'ho': x['entry_t'] >= CUT, 'sym': x['sym'], 'pair': x['pair'][:8], 'watch_min': wa, 'ub': fl['ub'],
                 'newb': fl['newb'], 'net50': x['net50'], 'usd50': x['usd50']})
for part in ('train', 'holdout'):
    sel = [r for r in rows if r['ho'] == (part == 'holdout')]
    b = {'<5': [], '5-15': [], '15-60': [], '>=60': []}
    for r in sel:
        w = r['watch_min']
        k = '<5' if w < 5 else '5-15' if w < 15 else '15-60' if w < 60 else '>=60'
        b[k].append(r)
    RES['watch_age_' + part] = {k: {'n': len(v), 'mean_usd': round(sum(r['usd50'] for r in v) / len(v), 2) if v else None,
                                    'mean_pct': round(sum(r['net50'] for r in v) / len(v), 2) if v else None,
                                    'newb_eq_ub_share': round(sum(1 for r in v if r['newb'] >= r['ub']) / len(v), 2) if v else None}
                                for k, v in b.items()}
    print(part, json.dumps(RES['watch_age_' + part]))
RES['watch_rows_holdout'] = [(r['sym'], r['pair'], round(r['watch_min'], 1), r['ub'], r['newb'], round(r['net50'], 2)) for r in rows if r['ho']]
print('holdout rows (sym, pair, watch_min, ub, newb, net50):')
for r in RES['watch_rows_holdout']:
    print('  ', r)

# (c) restrict to pools watched >= 15 / 30 min (past-only: coverage age at t)
for mins in (15, 30):
    def extra(P, mins=mins):
        w = watch_age_min(P.static('pair'), P.t)
        return w is not None and w >= mins
    tr = H.simulate(I.make_signal(H, newdef='event', extra=extra), **I.EXITS)
    ev = H.evaluate(tr)
    h, t_ = ev['holdout'], ev['train']
    RES['watched_ge_%d' % mins] = {'train': {k: t_.get(k) for k in ('n', 'pairs', 'mean_usd', 'mean_pct')},
                                    'holdout': {k: h.get(k) for k in ('n', 'pairs', 'mean_usd', 'mean_pct', 'median_pct', 'win_rate', 'top_pair_share', 'ci95_mean_usd_pair')}}
    print('watched >=', mins, 'min', json.dumps(RES['watched_ge_%d' % mins]), flush=True)

# (b) on-chain re-pricing of the holdout
WSOL = 'So11111111111111111111111111111111111111112'
con = sqlite3.connect('file:%s?mode=ro' % (DEEP + '/tape_snapshot.sqlite3'), uri=True)
PR = {}
for pair, et, payload in con.execute('SELECT pair, event_time, payload FROM events'):
    p = json.loads(payload)
    if not p.get('confirmed_swap') or p.get('quote_asset') != WSOL or p.get('direction') not in ('BUY', 'SELL'):
        continue
    try:
        q, a = float(p['quote_amount']), float(p['token_amount'])
    except (TypeError, ValueError, KeyError):
        continue
    if q > 0 and a > 0:
        PR.setdefault(pair, []).append((int(et), p['direction'] == 'BUY', q / a))
con.close()
for v in PR.values():
    v.sort()


def truth(pair, t, win):
    v = PR.get(pair)
    if not v:
        return None
    ets = [e for e, _, _ in v]
    k = bisect.bisect_right(ets, t) - 1
    lb = ls = None
    j = k
    while j >= 0 and t - v[j][0] <= win and (lb is None or ls is None):
        if v[j][1] and lb is None:
            lb = v[j][2]
        if (not v[j][1]) and ls is None:
            ls = v[j][2]
        j -= 1
    if lb and ls:
        return math.sqrt(lb * ls)
    return lb or ls


def reprice(trades, win_e, win_x, exit_too):
    out = []
    for x in trades:
        s = series[x['pair']]
        j = bisect.bisect_left(s['t'], x['entry_t'])
        g = 1.0
        te = truth(x['pair'], x['decision_t'] + 2000, win_e)
        if te:
            g *= s['pnative'][j] / te
        if exit_too and x['flag'] not in ('GAP', 'VANISHED'):
            k = bisect.bisect_left(s['t'], x['exit_t'])
            tx = truth(x['pair'], x['trigger_t'] + 2000, win_x)
            if tx and k < len(s['t']) and s['pnative'][k] > 0:
                g *= tx / s['pnative'][k]
        n = 100 * ((1 + x['net50'] / 100) * g - 1)
        out.append(x['size'] * n / 100)
    return out


ho = [x for x in c1 if x['entry_t'] >= CUT]
tr = [x for x in c1 if x['entry_t'] < CUT]
for nm, we, wx, ex in (('entry_only_20s', 20_000, 20_000, False), ('entry_exit_20s', 20_000, 20_000, True),
                       ('entry_exit_60s', 60_000, 60_000, True)):
    vh = reprice(ho, we, wx, ex)
    vt = reprice(tr, we, wx, ex)
    RES['reprice_' + nm] = {'holdout_mean_usd': round(sum(vh) / len(vh), 3), 'holdout_sum_usd': round(sum(vh), 2),
                            'holdout_mean_usd_wo_top1': round((sum(vh) - max(vh)) / (len(vh) - 1), 3),
                            'train_mean_usd': round(sum(vt) / len(vt), 3)}
    print('reprice', nm, RES['reprice_' + nm])
with open(os.path.join(OUT, 'run5.json'), 'w') as fh:
    json.dump(RES, fh, indent=1, default=str)
print('done')
