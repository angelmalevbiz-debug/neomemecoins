"""Third pass: dependence on the holdout-informed interim rug screen, and a timing-vs-random test in the same pools."""
import sys
sys.dont_write_bytecode = True
import bisect, importlib.util, json, os, pickle, random

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
DEEP = (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r''
OUT = DEEP + '/verify_f5_flowaccel_leakage_F5_C1_TAPE_BREADTH_NOCHASE'
sys.path.insert(0, OUT)
import reimpl as R

H, series, CUT = R.H, R.series, R.CUT
res = {}
orig_interim = H.interim_rug_risk


def run_with_screen(screen, guard=True):
    H.interim_rug_risk = screen
    try:
        return H.simulate(R.make_signal(guard=guard), **R.EX)
    finally:
        H.interim_rug_risk = orig_interim


# 1. which holdout trades the interim screen removes (guard on), and by which branch
no_int = run_with_screen(lambda P: False)
with_int = pickle.load(open(OUT + '/reimpl_trades.pkl', 'rb'))['guard']
kw = {(x['pair'], x['entry_t']) for x in with_int}
rem = []
for x in no_int:
    if (x['pair'], x['entry_t']) in kw:
        continue
    s = series[x['pair']]
    i = bisect.bisect_left(s['t'], x['decision_t'])
    P = H.Past(s, i)
    mc, liq, age = P('mcap'), P('liq'), P('age')
    young = not (age >= 14 * 1440)
    fake = bool(mc >= 20e6 and liq > 0 and liq / mc < 0.02 and young)
    tick = bool(young and H.other_pairs_same_ticker_before(P) >= 1)
    rem.append({'pair': x['pair'][:8], 'sym': x['sym'], 'part': 'train' if x['entry_t'] < CUT else 'holdout',
                'fake_mcap_branch': fake, 'ticker_reuse_branch': tick, 'n_same_ticker_before': H.other_pairs_same_ticker_before(P),
                'age_h': round(age / 60, 1), 'mcap': round(mc), 'liq': round(liq), 'usd50': round(x['usd50'], 2),
                'reason': x['reason'], 'flag': x['flag']})
agg = {}
for r in rem:
    k = '%s|fake=%s|ticker=%s|%s' % (r['part'], r['fake_mcap_branch'], r['ticker_reuse_branch'], r['sym'])
    a = agg.setdefault(k, [0, 0.0])
    a[0] += 1
    a[1] += r['usd50']
for k, v in sorted(agg.items()):
    print('INTERIM_REMOVED', k, 'n', v[0], 'sum usd50', round(v[1], 2), 'mean', round(v[1] / v[0], 2))
res['interim_removed'] = rem
res['no_interim'] = {'train': R.brief(R.split(no_int)[0]), 'holdout': R.brief(R.split(no_int)[1])}

# 2. train-only rug screen instead of the interim one (audit's sensitivity variant)
tro = run_with_screen(H.rug_risk_train_only)
res['train_only_screen'] = {'train': R.brief(R.split(tro)[0]), 'holdout': R.brief(R.split(tro)[1])}
print('train-only screen: train', res['train_only_screen']['train'].get('n'), res['train_only_screen']['train'].get('mean_usd'),
      'holdout', res['train_only_screen']['holdout'].get('n'), res['train_only_screen']['holdout'].get('pairs'),
      res['train_only_screen']['holdout'].get('mean_usd'), res['train_only_screen']['holdout'].get('ci95_mean_usd_pair'))

# 3. timing test: random entries in the SAME 5 holdout pools / same universe / holdout window, many salts;
#    distribution of the mean of 11 trades drawn as a strategy would (first 11 non-overlapping per salt is noisy,
#    so use a resampling of pooled random trades)
ho = [x for x in with_int if x['entry_t'] >= CUT]
pairs = {x['pair'] for x in ho}
pool = []
for k in range(40):
    salt = 'tm%02d' % k
    st = R.FlowState()

    def sig(P, st=st, salt=salt):
        liq = P('liq')
        if not (liq >= 20_000) or not (52.5 <= P.fee_bps() <= 125) or H.interim_rug_risk(P):
            return False
        ft = st.features(P.static('pair'), P.t)
        if ft is None or ft['age_s'] > 600:
            return False
        p0, p1 = P('price'), R.price_ago(P, 300)
        if not (R.fin(p0) and R.fin(p1) and p1 > 0 and -2 <= 100 * (p0 / p1 - 1) <= 5):
            return False
        if H.rug_guard_v1(P):
            return False
        return H.hashed_coin(P.static('pair'), P.t, 0.01, salt)
    tr = H.simulate(sig, pairs=pairs, t_from=CUT, **R.EX)
    pool.extend(tr)
mean_pool = sum(x['usd50'] for x in pool) / len(pool)
rng = random.Random(11)
# keep the pair composition of the real holdout trades (4 CRAWL, 3 swordcat, 2 MISTAKE, 1 JEANPHIL, 1 LMAO!)
comp = {}
for x in ho:
    comp[x['pair']] = comp.get(x['pair'], 0) + 1
bypair = {}
for x in pool:
    bypair.setdefault(x['pair'], []).append(x['usd50'])
real = sum(x['usd50'] for x in ho) / len(ho)
B, ge = 20000, 0
for _ in range(B):
    tot = 0.0
    for p, c in comp.items():
        lst = bypair[p]
        for _ in range(c):
            tot += lst[rng.randrange(len(lst))]
    if tot / len(ho) >= real:
        ge += 1
res['timing_test'] = {'random_trades_pooled': len(pool), 'random_mean_usd50': round(mean_pool, 3),
                      'random_per_pair_mean_usd50': {p[:8]: round(sum(v) / len(v), 3) for p, v in bypair.items()},
                      'random_per_pair_n': {p[:8]: len(v) for p, v in bypair.items()},
                      'real_mean_usd50': round(real, 3), 'p_value_composition_matched': round(ge / B, 4)}
print('TIMING', res['timing_test'])

# 4. same test for TRAIN: does the signal's timing beat random timing in its own train pools?
trn = [x for x in with_int if x['entry_t'] < CUT]
tpairs = {x['pair'] for x in trn}
tpool = []
for k in range(15):
    salt = 'tt%02d' % k
    st = R.FlowState()

    def sig(P, st=st, salt=salt):
        liq = P('liq')
        if not (liq >= 20_000) or not (52.5 <= P.fee_bps() <= 125) or H.interim_rug_risk(P):
            return False
        ft = st.features(P.static('pair'), P.t)
        if ft is None or ft['age_s'] > 600:
            return False
        p0, p1 = P('price'), R.price_ago(P, 300)
        if not (R.fin(p0) and R.fin(p1) and p1 > 0 and -2 <= 100 * (p0 / p1 - 1) <= 5):
            return False
        if H.rug_guard_v1(P):
            return False
        return H.hashed_coin(P.static('pair'), P.t, 0.01, salt)
    tpool.extend(H.simulate(sig, pairs=tpairs, t_to=CUT, **R.EX))
tcomp = {}
for x in trn:
    tcomp[x['pair']] = tcomp.get(x['pair'], 0) + 1
tby = {}
for x in tpool:
    tby.setdefault(x['pair'], []).append(x['usd50'])
treal = sum(x['usd50'] for x in trn) / len(trn)
ge = 0
B2 = 5000
for _ in range(B2):
    tot, n = 0.0, 0
    for p, c in tcomp.items():
        lst = tby.get(p)
        if not lst:
            continue
        for _ in range(c):
            tot += lst[rng.randrange(len(lst))]
            n += 1
    if tot / n >= treal:
        ge += 1
res['timing_test_train'] = {'random_trades_pooled': len(tpool),
                            'random_mean_usd50': round(sum(x['usd50'] for x in tpool) / len(tpool), 3),
                            'real_mean_usd50': round(treal, 3), 'p_value_composition_matched': round(ge / B2, 4)}
print('TIMING_TRAIN', res['timing_test_train'])
with open(OUT + '/screencheck_results.json', 'w', encoding='utf-8') as fh:
    json.dump(res, fh, indent=1, default=str)
print('done')
