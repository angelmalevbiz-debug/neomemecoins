"""Adversarial leakage / implementation verification of F5_C1_TAPE_BREADTH_NOCHASE (PAPER research only).

Independent re-implementation of the rule from its plain-language description:
  universe: PumpSwap SOL pair, fee tier 52.5-125 bps, liquidity >= $20k, not H.interim_rug_risk, tape-live
            (newest swap already ingested at t has block time within 600 s of t)
  flow (last 300 s by block time, only swaps ingested at or before t, read directly from tape_snapshot.sqlite3):
            >= 10 distinct buyer wallets, >= 5 first-time buyer wallets (wallet's earliest swap on this pool known
            at t lies inside the window), largest buyer < 30% of buy SOL, buy SOL - sell SOL > 0
  no chase: price change vs the latest point <= t-300 s within [-2%, +5%]
  exits: stop -10 net, tp +6 net, 30 min hold; size min($200, 0.2% liq); cooldown 300 s (harness default)
Variants: 'orig' and 'rug_guard_v1' (signal AND NOT H.rug_guard_v1(P)), as in leaderboard/run_all.py.
Runs through leaderboard/harness_final.py (the integrator's audited harness).
Writes only into this folder.
"""
import sys
sys.dont_write_bytecode = True
import bisect, importlib.util, json, os, pickle, sqlite3, time, math

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
DEEP = (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r''
LB = DEEP + '/leaderboard'
OUT = DEEP + '/verify_f5_flowaccel_leakage_F5_C1_TAPE_BREADTH_NOCHASE'
T_START = time.time()


def log(*a):
    print('[%6.1fs]' % (time.time() - T_START), *a, flush=True)


def _load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


H = _load_module('harness_final', LB + '/harness_final.py')
sys.modules['harness'] = H
if DEEP not in sys.path:
    sys.path.insert(0, DEEP)
series, meta = H.load()
CUT = H.split_t(meta)
log('loaded series', meta['pairs'], 'pairs; cut', CUT)

# ------------------------------------------------------------------ independent tape index
WSOL = 'So11111111111111111111111111111111111111112'


def build_tape():
    con = sqlite3.connect('file:%s/tape_snapshot.sqlite3?mode=ro' % DEEP, uri=True)
    per = {}
    stats = {'rows': 0, 'confirmed': 0, 'buy_sell': 0, 'non_wsol_quote': 0, 'bad_q': 0, 'et_gt_av': 0,
             'payload_av_mismatch': 0}
    for pair, et, av, payload in con.execute('SELECT pair, event_time, available, payload FROM events'):
        stats['rows'] += 1
        p = json.loads(payload)
        if p.get('confirmed_swap') is not True:
            continue
        stats['confirmed'] += 1
        d = p.get('direction')
        if d not in ('BUY', 'SELL'):
            continue
        stats['buy_sell'] += 1
        if p.get('quote_asset') != WSOL:
            stats['non_wsol_quote'] += 1
        try:
            q = float(p.get('quote_amount'))
            tok = float(p.get('token_amount') or 'nan')
        except (TypeError, ValueError):
            stats['bad_q'] += 1
            continue
        if not (q >= 0):
            stats['bad_q'] += 1
            continue
        if et > av:
            stats['et_gt_av'] += 1
        if int(p.get('available_at') or av) != av:
            stats['payload_av_mismatch'] += 1
        per.setdefault(pair, []).append((int(et), int(av), 1 if d == 'BUY' else -1, q, p.get('wallet') or '', tok))
    con.close()
    T = {}
    for pair, v in per.items():
        v.sort(key=lambda x: (x[0], x[1]))
        byav = sorted(range(len(v)), key=lambda k: (v[k][1], v[k][0]))
        T[pair] = {'et': [x[0] for x in v], 'av': [x[1] for x in v], 'sgn': [x[2] for x in v],
                   'q': [x[3] for x in v], 'w': [x[4] for x in v], 'tok': [x[5] for x in v],
                   'byav': byav, 'av_sorted': [v[k][1] for k in byav]}
    return T, stats


TAPE, TSTATS = build_tape()
log('tape built', len(TAPE), 'pools', TSTATS)


def fin(x):
    return x == x and x not in (float('inf'), float('-inf'))


class FlowState:
    """Per-run, per-pair incremental state: wallet -> earliest known block time among swaps ingested so far."""

    def __init__(self, lag_ms=0):
        self.lag = lag_ms
        self.ptr = {}
        self.wmin = {}
        self.last_t = {}
        self.nonmono = 0

    def _advance(self, pair, d, tt):
        if self.last_t.get(pair, -1) > tt:          # non-monotonic call: rebuild from scratch (never expected)
            self.nonmono += 1
            self.ptr[pair] = 0
            self.wmin[pair] = {}
        self.last_t[pair] = tt
        p = self.ptr.get(pair, 0)
        wm = self.wmin.setdefault(pair, {})
        byav, et, w = d['byav'], d['et'], d['w']
        avs = d['av_sorted']
        n = len(byav)
        while p < n and avs[p] <= tt:
            k = byav[p]
            m = wm.get(w[k])
            if m is None or et[k] < m:
                wm[w[k]] = et[k]
            p += 1
        self.ptr[pair] = p
        return wm

    def features(self, pair, t, window_s=300):
        d = TAPE.get(pair)
        if d is None:
            return None
        tt = t - self.lag                       # information cut-off (lag stress: pretend ingestion was later)
        wm = self._advance(pair, d, tt)
        et, av = d['et'], d['av']
        hi = bisect.bisect_right(et, tt)
        # tape-live: newest ingested swap (exact, no step limit)
        k = hi - 1
        while k >= 0 and av[k] > tt:
            k -= 1
        if k < 0:
            return None
        age_s = (t - et[k]) / 1000.0
        lo = bisect.bisect_right(et, tt - window_s * 1000, 0, hi)
        wstart = tt - window_s * 1000
        buyers, bsol, ssol = {}, 0.0, 0.0
        for k in range(lo, hi):
            if av[k] > tt:
                continue
            if d['sgn'][k] > 0:
                bsol += d['q'][k]
                buyers[d['w'][k]] = buyers.get(d['w'][k], 0.0) + d['q'][k]
            else:
                ssol += d['q'][k]
        newb = sum(1 for wl in buyers if wm.get(wl, -1) > wstart)
        top = (max(buyers.values()) / bsol) if (buyers and bsol > 0) else float('nan')
        return {'age_s': age_s, 'ub': len(buyers), 'newb': newb, 'top_share': top, 'net_sol': bsol - ssol,
                'bsol': bsol, 'ssol': ssol}


def price_ago(P, seconds):
    s, i = P.s, P.i
    ts = s['t']
    k = bisect.bisect_right(ts, ts[i] - seconds * 1000, 0, i + 1) - 1
    return s['price'][k] if k >= 0 else float('nan')


def make_signal(guard=False, lag_ms=0, nochase=True, record=None):
    st = FlowState(lag_ms)

    def sig(P):
        liq = P('liq')
        if not (liq >= 20_000):
            return False
        f = P.fee_bps()
        if not (52.5 <= f <= 125):
            return False
        if H.interim_rug_risk(P):
            return False
        pair = P.static('pair')
        ft = st.features(pair, P.t)
        if ft is None or ft['age_s'] > 600:
            return False
        if nochase:
            p0, p1 = P('price'), price_ago(P, 300)
            if not (fin(p0) and fin(p1) and p1 > 0):
                return False
            r = 100 * (p0 / p1 - 1)
            if not (-2 <= r <= 5):
                return False
        if not (ft['ub'] >= 10 and ft['newb'] >= 5 and fin(ft['top_share']) and ft['top_share'] < 0.3
                and ft['net_sol'] > 0):
            return False
        ok = (not H.rug_guard_v1(P)) if guard else True
        if ok and record is not None:
            record[(pair, P.t)] = ft
        return ok
    sig.state = st
    return sig


def size_fn(P):
    return min(200.0, 0.002 * P('liq'))


EX = dict(stop=-10.0, tp=6.0, hold_min=30.0, size_fn=size_fn)


def split(tr):
    return [x for x in tr if x['entry_t'] < CUT], [x for x in tr if x['entry_t'] >= CUT]


def brief(tr):
    s = H.summarize(tr) if tr else {'n': 0}
    if not tr:
        return s
    keep = ('n', 'pairs', 'clusters', 'win_rate', 'mean_usd', 'mean_pct', 'median_pct', 'sum_usd', 'pf',
            'ci95_mean_usd', 'ci95_mean_usd_pair', 'top_pair_share', 'trades_per_hour', 'exits')
    return {k: s.get(k) for k in keep}


def full_eval(tr):
    ev = H.evaluate(tr)
    trn, ho = split(tr)
    out = {}
    for part in ('train', 'holdout', 'train_model', 'holdout_model'):
        s = ev[part]
        out[part] = {k: s.get(k) for k in ('n', 'pairs', 'clusters', 'win_rate', 'mean_usd', 'mean_pct', 'median_pct',
                                           'sum_usd', 'pf', 'ci95_mean_usd', 'ci95_mean_usd_pair', 'top_pair_share',
                                           'trades_per_hour', 'exits')}
    out['holdout_portfolio3'] = H.portfolio(ho, slots=3) if ho else None
    out['train_portfolio3'] = H.portfolio(trn, slots=3) if trn else None
    return out


RESULTS = {'tape_stats': TSTATS, 'cut': CUT}

# ------------------------------------------------------------------ 1. stored leaderboard trades
lbd = pickle.load(open(LB + '/trades/f5_flowaccel.pkl', 'rb'))
stored = {}
for row in lbd['rows']:
    stored[(row['name'], row['variant'])] = row['trades']
st_g = stored[('F5_C1_TAPE_BREADTH_NOCHASE', 'rug_guard_v1')]
st_o = stored[('F5_C1_TAPE_BREADTH_NOCHASE', 'orig')]
RESULTS['stored_guard'] = full_eval(st_g)
log('stored guard holdout', RESULTS['stored_guard']['holdout']['n'], RESULTS['stored_guard']['holdout']['mean_usd'])

# ------------------------------------------------------------------ 2. family code re-run under harness_final
fam = _load_module('f5fam_verify', DEEP + '/f5_flowaccel/strategies.py')
c1 = fam.CONFIGS[0]
assert c1['name'] == 'F5_C1_TAPE_BREADTH_NOCHASE'
fam_g = H.simulate(lambda P: bool(c1['signal'](P)) and not H.rug_guard_v1(P), **c1['kwargs'])
same = len(fam_g) == len(st_g) and all(a['pair'] == b['pair'] and a['entry_t'] == b['entry_t'] and
                                        abs(a['net50'] - b['net50']) < 1e-9 for a, b in zip(fam_g, st_g))
RESULTS['family_rerun_identical_to_stored'] = same
log('family rerun guard n', len(fam_g), 'identical to stored:', same)

# ------------------------------------------------------------------ 3. independent re-implementation
rec_g = {}
sig_g = make_signal(guard=True, record=rec_g)
mine_g = H.simulate(sig_g, **EX)
RESULTS['reimpl_guard'] = full_eval(mine_g)
RESULTS['reimpl_guard_nonmono_calls'] = sig_g.state.nonmono
log('reimpl guard n', len(mine_g), 'holdout', RESULTS['reimpl_guard']['holdout']['n'],
    RESULTS['reimpl_guard']['holdout']['mean_usd'])

sig_o = make_signal(guard=False)
mine_o = H.simulate(sig_o, **EX)
RESULTS['reimpl_orig'] = full_eval(mine_o)
log('reimpl orig n', len(mine_o), 'holdout', RESULTS['reimpl_orig']['holdout']['n'],
    RESULTS['reimpl_orig']['holdout']['mean_usd'])


def overlap(a, b):
    ka = {(x['pair'], x['entry_t']) for x in a}
    kb = {(x['pair'], x['entry_t']) for x in b}
    return {'a': len(ka), 'b': len(kb), 'both': len(ka & kb), 'only_a': len(ka - kb), 'only_b': len(kb - ka)}


RESULTS['overlap_guard_reimpl_vs_stored'] = overlap(mine_g, st_g)
RESULTS['overlap_orig_reimpl_vs_stored'] = overlap(mine_o, st_o)
RESULTS['overlap_guard_reimpl_vs_stored_holdout'] = overlap(split(mine_g)[1], split(st_g)[1])
log('overlap guard', RESULTS['overlap_guard_reimpl_vs_stored'], 'holdout', RESULTS['overlap_guard_reimpl_vs_stored_holdout'])

# feature-level comparison at the reimpl's holdout decision points (family tapefeat vs mine)
TF = sys.modules.get('tapefeat')
cmp_rows = []
for x in split(mine_g)[1]:
    key = (x['pair'], x['decision_t'])
    mf = rec_g.get(key)
    ff = TF.flow(x['pair'], x['decision_t'], 300) if TF else None
    fa = TF.last_available_age_s(x['pair'], x['decision_t']) if TF else None
    cmp_rows.append({'pair': x['pair'][:8], 'sym': x['sym'], 'decision_t': x['decision_t'],
                     'mine': {k: (round(v, 3) if isinstance(v, float) else v) for k, v in (mf or {}).items()},
                     'family': ({k: (round(v, 3) if isinstance(v, float) else v) for k, v in ff.items()} if ff else None),
                     'family_age_s': fa})
RESULTS['holdout_feature_compare'] = cmp_rows

# ------------------------------------------------------------------ 4. leakage probes on the guarded rule
probes = {}
for lag in (30_000, 120_000):
    tr = H.simulate(make_signal(guard=True, lag_ms=lag), **EX)
    probes['ingest_lag_%ds' % (lag // 1000)] = {'train': brief(split(tr)[0]), 'holdout': brief(split(tr)[1])}
    log('lag', lag, 'holdout', probes['ingest_lag_%ds' % (lag // 1000)]['holdout'].get('n'),
        probes['ingest_lag_%ds' % (lag // 1000)]['holdout'].get('mean_usd'))
# v1 fill rule sensitivity (zero-latency fills) and plain v2 stress (no calibration) for context
H.FILL_RULE = 'next_point'
try:
    tr = H.simulate(make_signal(guard=True), **EX)
finally:
    H.FILL_RULE = 'next_refresh'
probes['fill_next_point_v1'] = {'train': brief(split(tr)[0]), 'holdout': brief(split(tr)[1])}
RESULTS['probes'] = probes
RESULTS['reimpl_guard_holdout_v2_stress_mean_usd'] = (
    round(sum(x['usd50_v2'] for x in split(mine_g)[1]) / max(1, len(split(mine_g)[1])), 3))

# ------------------------------------------------------------------ 5. random baselines (same universe, same exits)
def universe_nochase(P, guard=True, st=None):
    liq = P('liq')
    if not (liq >= 20_000):
        return False
    if not (52.5 <= P.fee_bps() <= 125):
        return False
    if H.interim_rug_risk(P):
        return False
    ft = st.features(P.static('pair'), P.t)
    if ft is None or ft['age_s'] > 600:
        return False
    p0, p1 = P('price'), price_ago(P, 300)
    if not (fin(p0) and fin(p1) and p1 > 0 and -2 <= 100 * (p0 / p1 - 1) <= 5):
        return False
    return not (guard and H.rug_guard_v1(P))


ho_pairs = sorted({x['pair'] for x in split(mine_g)[1]})
base = []
for salt in ('vA', 'vB', 'vC', 'vD', 'vE', 'vF'):
    st = FlowState()
    tr = H.simulate(lambda P, st=st, salt=salt: universe_nochase(P, True, st) and
                    H.hashed_coin(P.static('pair'), P.t, 0.007, salt), **EX)
    trn, ho = split(tr)
    base.append({'salt': salt, 'train': brief(trn), 'holdout': brief(ho)})
    log('random', salt, 'holdout n', len(ho), base[-1]['holdout'].get('mean_usd'))
RESULTS['random_universe_nochase_guard'] = base
# pair-matched: random timing restricted to the pools C1 traded in holdout, holdout window only
pm = []
for salt in ('pA', 'pB', 'pC', 'pD', 'pE', 'pF', 'pG', 'pH'):
    st = FlowState()
    tr = H.simulate(lambda P, st=st, salt=salt: universe_nochase(P, True, st) and
                    H.hashed_coin(P.static('pair'), P.t, 0.01, salt), pairs=set(ho_pairs), t_from=CUT, **EX)
    pm.append({'salt': salt, 'holdout': brief(tr)})
    log('pair-matched random', salt, 'n', len(tr), pm[-1]['holdout'].get('mean_usd'))
RESULTS['random_pair_matched_holdout'] = pm

# ------------------------------------------------------------------ 6. on-chain price check of the holdout fills
def tape_px(pair, t, half_s=45):
    d = TAPE.get(pair)
    if d is None:
        return None, 0
    et = d['et']
    lo = bisect.bisect_left(et, t - half_s * 1000)
    hi = bisect.bisect_right(et, t + half_s * 1000)
    px = [d['q'][k] / d['tok'][k] for k in range(lo, hi) if d['tok'][k] and d['tok'][k] > 0 and d['q'][k] > 0]
    if not px:
        return None, 0
    px.sort()
    return px[len(px) // 2], len(px)


chk = []
for x in split(mine_g)[1]:
    s = series[x['pair']]
    j = bisect.bisect_left(s['t'], x['entry_t'])
    ent_native = s['pnative'][j]
    e = bisect.bisect_left(s['t'], x['exit_t'])
    ex_native = s['pnative'][e] if e < len(s['t']) else float('nan')
    a, na = tape_px(x['pair'], x['entry_t'])
    b, nb = tape_px(x['pair'], x['exit_t'])
    ds_ret = 100 * (ex_native / ent_native - 1) if (ent_native and ex_native == ex_native) else None
    tp_ret = 100 * (b / a - 1) if (a and b) else None
    chk.append({'pair': x['pair'][:8], 'sym': x['sym'], 'reason': x['reason'], 'net50': round(x['net50'], 2),
                'ds_gross_ret_pct': round(ds_ret, 2) if ds_ret is not None else None,
                'tape_gross_ret_pct': round(tp_ret, 2) if tp_ret is not None else None,
                'tape_swaps_entry': na, 'tape_swaps_exit': nb})
RESULTS['holdout_onchain_check'] = chk

# ------------------------------------------------------------------ 7. concentration / leave-one-pair-out
def lopo(tr):
    pairs = sorted({x['pair'] for x in tr})
    out = []
    for p in pairs:
        rest = [x for x in tr if x['pair'] != p]
        out.append({'drop': p[:8], 'n': len(rest),
                    'mean_usd': round(sum(x['usd50'] for x in rest) / len(rest), 3) if rest else None})
    return out


RESULTS['holdout_leave_one_pair_out'] = lopo(split(mine_g)[1])
RESULTS['stored_holdout_leave_one_pair_out'] = lopo(split(st_g)[1])
RESULTS['holdout_trades_reimpl'] = [{'pair': x['pair'][:8], 'sym': x['sym'], 'decision_t': x['decision_t'],
                                     'entry_t': x['entry_t'], 'exit_t': x['exit_t'], 'reason': x['reason'],
                                     'flag': x['flag'], 'net0': round(x['net0'], 3), 'net50': round(x['net50'], 3),
                                     'usd50': round(x['usd50'], 3), 'size': round(x['size'], 1),
                                     'fee_bps': x['fee_bps'], 'liq': round(x['liq'])} for x in split(mine_g)[1]]
# per-hour holdout train split stability: train halves
trn = split(mine_g)[0]
mid = meta['t0'] + 0.3 * (meta['t1'] - meta['t0'])
RESULTS['train_halves'] = {'first': brief([x for x in trn if x['entry_t'] < mid]),
                           'second': brief([x for x in trn if x['entry_t'] >= mid])}

with open(OUT + '/reimpl_results.json', 'w', encoding='utf-8') as fh:
    json.dump(RESULTS, fh, indent=1, default=str)
with open(OUT + '/reimpl_trades.pkl', 'wb') as fh:
    pickle.dump({'guard': mine_g, 'orig': mine_o}, fh, protocol=pickle.HIGHEST_PROTOCOL)
log('done')
