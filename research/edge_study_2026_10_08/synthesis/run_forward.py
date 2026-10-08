"""PRE-REGISTERED FORWARD (out-of-time) CHECK of the research conclusions. PAPER research only.

Frozen BEFORE any post-cutoff observation was read (sha256 of this file, strategies.py and build_forward.py recorded in
prereg.sha256 before build_forward.py ran). Data: the 22.76 h dataset (series.pkl) extended with every observation the
engine logged after the dataset cutoff (fwd_series.pkl). Only decisions with time >= CUT_T (the dataset end) are
evaluated; earlier points serve only as history. Harness: leaderboard/harness_final.py (audited v2 fills, feed-gap
closes, survivorship-safe forward_net, calibrated stress). Run once.

A. SAMPLE-LEVEL TESTS (1 sample per pair per wall-clock minute, PumpSwap SOL pairs, liq >= $20k, decision t >= CUT_T).
   Outcome fN = H.forward_net(s, i, N s, $200, extra_bps=50) (net %, +50 bps/leg stress, vanished pairs valued at
   last price -10 %, right-censored samples dropped). cat60 = 1 if f60 <= -50 %.
   G_RUG   universe S0 (all samples): rug_guard_v1 flagged vs passed.
           Prediction: flagged cat60 rate > passed (difference > 0) and flagged mean f30 < passed.
   Vetoes, universe S1 = fee >= 55 bps and rug_guard_structural passes (LP-pull / fake-mcap removed, young pools kept):
     V_MOM   (f1_momentum) 5-min return >= +3 % or 5-min buy share >= 0.70 or v5/(v1h/12) >= 1.3 or pc6h >= 200
             or pc24 >= 150.
     V_ATT   (f4_attention) fee >= 100 and the pair carried the paid-profile 'latest' flag in the last 60 min
             (tested inside fee >= 100 samples only).
     V_CRASH (f2_meanrev) price <= 75 % of its 15-min high, or 5-min return <= -20 %.
     V_HOT   (f8_tape) v5/liq >= V_HOT_Q80 (80th percentile in S1 on PRE-CUTOFF data, pre_constants.json).
     V_STACK any of the four.  Also V_STACK in S2 = samples passing rug_guard_v1 (the recommended production stack).
     Prediction for every veto: mean f30(vetoed) - mean f30(kept) < 0.
   Statistic: difference of means; 95 % CI by bootstrap over pairs (2,000 reps, seed 11).
   Decision: 'insufficient' if the flagged side has < 30 samples or < 5 pairs; 'confirmed' if the CI excludes 0 in
   the predicted direction; 'consistent' if the point estimate has the predicted sign; 'contradicted' if the CI
   excludes 0 against the prediction; else 'not_supported'.
B. TRADE-LEVEL TESTS (H.simulate with t_from = CUT_T, primary basis calibrated stressed net50):
     R1 live cost-first rule (smoke_common.cost_first, -5/+10/60, size min($200, 0.1 % liq)), as live on 18802.
     R2 R1 + rug_guard_v1.   R3 random all PumpSwap (p=0.002, salt 'r').   R4 R3 + rug_guard_v1.
     LAB_A / LAB_B from synthesis/strategies.py and their random baselines. LAB_C needs tape events after the cutoff,
     which the research tape snapshot does not have: not testable here (reported as such).
   Predictions: R1-R4 mean net50 < 0 ('confirmed' if the pair-bootstrap CI95 upper bound < 0).
   LAB_A / LAB_B: 'inconclusive' by rule when n < 20; otherwise reported against their baselines. No promotion
   decision can come from this window.
C. DESCRIPTIVE (no hypothesis): what happened after the cutoff to the open PAPER positions named by forensics
   (WOSE, GOIF, SARP in 18802, VSOF in the Lab, plus swordinu), and the rug-guard status of the cost-first universe.
"""
import bisect, json, math, os, pickle, random, sys, time
from datetime import datetime, timezone
sys.dont_write_bytecode = True
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
DEEP = (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r''
HERE = os.path.join(DEEP, 'synthesis')
sys.path.insert(0, os.path.join(DEEP, 'leaderboard'))
import harness_final as H
sys.modules['harness'] = H
sys.path.insert(0, DEEP)
sys.path.insert(0, HERE)

T_START = time.time()
NUM = ['t', 'price', 'pnative', 'liq', 'mcap', 'v5', 'v1h', 'v6h', 'v24', 'pc5', 'pc1h', 'pc6h', 'pc24',
       'b5', 's5', 'b1h', 's1h', 'b6h', 's6h', 'age', 'boost', 'score', 'risk',
       'vf_trades', 'vf_buy', 'vf_sell', 'vf_wallets', 'vf_age_ms', 'sf_buy', 'sf_sell', 'sf_wallets',
       'hp_uw5', 'hp_rb5', 'conv', 'src']
PRE = json.load(open(os.path.join(HERE, 'pre_constants.json')))
V_HOT_Q80 = PRE['V_HOT_Q80']


def ab(x):
    return (x or '')[:8]


# ------------------------------------------------------------------ data: dataset + forward extension
series, meta = H.load()
CUT_T = meta['t1']
fwd = pickle.load(open(os.path.join(HERE, 'fwd_series.pkl'), 'rb'))
new_pairs = ext_pairs = added = 0
for pair, fs in fwd['series'].items():
    s = series.get(pair)
    if s is None:
        series[pair] = fs
        new_pairs += 1
        added += len(fs['t'])
        continue
    k0 = bisect.bisect_right(fs['t'], s['t'][-1])
    if k0 >= len(fs['t']):
        continue
    for c in NUM:
        s[c].extend(fs[c][k0:])
    ext_pairs += 1
    added += len(fs['t']) - k0
T1 = max(meta['t1'], fwd['meta']['t_max'])
meta2 = dict(meta)
meta2['t1'] = T1
meta2['cut_t'] = CUT_T
H._LOADED = (series, meta2)
H._TICKERS = None
H._GAPS.clear()
FWD_H = (T1 - CUT_T) / 3.6e6
print('forward build meta', fwd['meta'])
print('merged: new pairs %d, extended pairs %d, forward points %d, forward window %.2f h (%s .. %s UTC)' % (
    new_pairs, ext_pairs, added, FWD_H, datetime.fromtimestamp(CUT_T / 1000, timezone.utc).strftime('%H:%M'),
    datetime.fromtimestamp(T1 / 1000, timezone.utc).strftime('%H:%M')))

import strategies as S          # synthesis Lab hypotheses (frozen)
import smoke_common as SC       # live cost-first universe rule

RESULTS = {'meta': {'cut_t': CUT_T, 't1': T1, 'forward_hours': FWD_H, 'fwd_build': fwd['meta'],
                    'new_pairs': new_pairs, 'ext_pairs': ext_pairs, 'forward_points': added}}


# ------------------------------------------------------------------ A. sample-level tests
def fin(x):
    return x == x and x not in (float('inf'), float('-inf'))


samples = []
for pair, s in series.items():
    if s['dex'] != 'pumpswap' or s['quote_sol'] != 1:
        continue
    ts = s['t']
    i0 = bisect.bisect_left(ts, CUT_T)
    last_m = None
    for i in range(i0, len(ts)):
        m = int(ts[i] // 60_000)
        if m == last_m:
            continue
        last_m = m
        P = H.Past(s, i)
        liq = P('liq')
        if not (liq >= 20_000):
            continue
        fee = P.fee_bps()
        p = P('price')
        p5 = P.ago('price', 300)
        ret5 = (p / p5 - 1) if (p > 0 and p5 > 0) else float('nan')
        b5, s5 = P('b5'), P('s5')
        bshare = b5 / (b5 + s5) if (fin(b5) and fin(s5) and b5 + s5 >= 1) else float('nan')
        v5, v1h = P('v5'), P('v1h')
        vacc = v5 / (v1h / 12) if (fin(v5) and fin(v1h) and v1h > 0) else float('nan')
        pc6h, pc24 = P('pc6h'), P('pc24')
        v_mom = (ret5 >= 0.03) or (bshare >= 0.70) or (vacc >= 1.3) or (pc6h >= 200) or (pc24 >= 150)
        latest60 = any(int(x) & 4 for x in P.window('src', 3600) if x == x)
        v_att = fee >= 100 and latest60
        win = [x for x in P.window('price', 900) if x > 0]
        hi15 = max(win) if win else float('nan')
        v_crash = (p > 0 and hi15 > 0 and p / hi15 - 1 <= -0.25) or (ret5 <= -0.20)
        v_hot = fin(v5) and liq > 0 and v5 / liq >= V_HOT_Q80
        f15 = H.forward_net(s, i, 900, 200.0, 50.0)
        f30 = H.forward_net(s, i, 1800, 200.0, 50.0)
        f60 = H.forward_net(s, i, 3600, 200.0, 50.0)
        samples.append({'pair': pair, 't': ts[i], 'fee': fee, 'liq': liq,
                        'g1': H.rug_guard_v1(P), 'gs': H.rug_guard_structural(P), 'why': H.rug_reasons(P),
                        'interim': H.interim_rug_risk(P),
                        'v_mom': bool(v_mom), 'v_att': bool(v_att), 'v_crash': bool(v_crash), 'v_hot': bool(v_hot),
                        'f15': f15, 'f30': f30, 'f60': f60,
                        'cat60': (None if f60 is None else (1.0 if f60 <= -50 else 0.0))})
for r in samples:
    r['v_stack'] = r['v_mom'] or r['v_att'] or r['v_crash'] or r['v_hot']
print('forward minute samples (liq >= $20k):', len(samples), 'pairs', len({r['pair'] for r in samples}),
      'secs %.0f' % (time.time() - T_START))


def boot_diff(recs, flag, val, reps=2000, seed=11):
    groups = {}
    for r in recs:
        v = r[val]
        if v is None:
            continue
        groups.setdefault(r['pair'], []).append((1 if r[flag] else 0, v))
    G = list(groups.values())

    def stat(gs):
        a = b = 0.0
        na = nb = 0
        pa = 0
        for g in gs:
            hit = False
            for fl, v in g:
                if fl:
                    a += v
                    na += 1
                    hit = True
                else:
                    b += v
                    nb += 1
            pa += hit
        if na == 0 or nb == 0:
            return None
        return a / na - b / nb, na, nb, a / na, b / nb, pa
    pt = stat(G)
    if pt is None:
        return {'n_flag': 0}
    rnd = random.Random(seed)
    ds = []
    for _ in range(reps):
        r = stat([G[rnd.randrange(len(G))] for _ in range(len(G))])
        if r is not None:
            ds.append(r[0])
    ds.sort()
    lo = ds[int(0.025 * len(ds))] if ds else None
    hi = ds[min(len(ds) - 1, int(0.975 * len(ds)))] if ds else None
    return {'diff': round(pt[0], 3), 'ci95': [round(lo, 3), round(hi, 3)], 'n_flag': pt[1], 'n_keep': pt[2],
            'mean_flag': round(pt[3], 3), 'mean_keep': round(pt[4], 3), 'pairs_flag': pt[5], 'pairs_all': len(G)}


def decide(res, predicted_sign):
    if not res or res.get('n_flag', 0) < 30 or res.get('pairs_flag', 0) < 5:
        return 'insufficient'
    lo, hi = res['ci95']
    d = res['diff']
    if predicted_sign < 0:
        if hi < 0:
            return 'confirmed'
        if lo > 0:
            return 'contradicted'
        return 'consistent' if d < 0 else 'not_supported'
    if lo > 0:
        return 'confirmed'
    if hi < 0:
        return 'contradicted'
    return 'consistent' if d > 0 else 'not_supported'


S0 = samples
S1 = [r for r in samples if r['fee'] >= 55 and not r['gs']]
S1h = [r for r in S1 if r['fee'] >= 100]
S2 = [r for r in samples if not r['g1']]
tests = {}
tests['G_RUG_cat60'] = (boot_diff(S0, 'g1', 'cat60'), +1)
tests['G_RUG_f30'] = (boot_diff(S0, 'g1', 'f30'), -1)
tests['V_MOM_S1_f30'] = (boot_diff(S1, 'v_mom', 'f30'), -1)
tests['V_ATT_S1fee100_f30'] = (boot_diff(S1h, 'v_att', 'f30'), -1)
tests['V_CRASH_S1_f30'] = (boot_diff(S1, 'v_crash', 'f30'), -1)
tests['V_HOT_S1_f30'] = (boot_diff(S1, 'v_hot', 'f30'), -1)
tests['V_STACK_S1_f30'] = (boot_diff(S1, 'v_stack', 'f30'), -1)
tests['V_STACK_S2_f30'] = (boot_diff(S2, 'v_stack', 'f30'), -1)
# secondary horizons (reported, not decided)
secondary = {'V_STACK_S1_f15': boot_diff(S1, 'v_stack', 'f15'), 'V_STACK_S1_f60': boot_diff(S1, 'v_stack', 'f60'),
             'V_ATT_S1fee100_f15': boot_diff(S1h, 'v_att', 'f15'), 'V_ATT_S1fee100_f60': boot_diff(S1h, 'v_att', 'f60'),
             'G_RUG_f60': boot_diff(S0, 'g1', 'f60')}
RESULTS['sample_tests'] = {}
print('\n== A. sample-level tests (diff = flagged minus kept, net % at +50 bps/leg; CI95 by pair bootstrap)')
for k, (res, sign) in tests.items():
    dec = decide(res, sign)
    RESULTS['sample_tests'][k] = dict(res, predicted_sign=sign, decision=dec)
    print('%-20s %-13s %s' % (k, dec, res))
RESULTS['sample_secondary'] = secondary
for k, res in secondary.items():
    print('  (secondary) %-20s %s' % (k, res))


def bucket_stats(recs, key):
    v = [r[key] for r in recs if r[key] is not None]
    if not v:
        return {'n': 0}
    v.sort()
    return {'n': len(v), 'pairs': len({r['pair'] for r in recs if r[key] is not None}), 'mean': round(sum(v) / len(v), 3),
            'median': round(v[len(v) // 2], 3), 'p10': round(v[int(0.1 * len(v))], 3), 'p90': round(v[int(0.9 * len(v))], 3)}


print('\n== base rates in the forward window (f30 / f60, net % at +50 bps/leg)')
base = {}
for name, recs in [('S0 all liq>=20k', S0), ('S0 guard-flagged', [r for r in S0 if r['g1']]),
                   ('S2 guard-pass', S2), ('S2 guard-pass, no veto', [r for r in S2 if not r['v_stack']]),
                   ('S2 fee<=50 liq>=250k', [r for r in S2 if r['fee'] <= 50 and r['liq'] >= 250_000]),
                   ('S2 fee55-95', [r for r in S2 if 55 <= r['fee'] <= 95]),
                   ('S2 fee>=100', [r for r in S2 if r['fee'] >= 100]),
                   ('S1 young (<12h) structural-pass', [r for r in S1 if 'YOUNG_POOL' in r['why']])]:
    base[name] = {'f30': bucket_stats(recs, 'f30'), 'f60': bucket_stats(recs, 'f60'),
                  'cat60_rate': (round(sum(r['cat60'] for r in recs if r['cat60'] is not None) /
                                       max(1, sum(1 for r in recs if r['cat60'] is not None)), 4))}
    print('%-34s %s' % (name, base[name]))
RESULTS['base_rates'] = base
reasons = {}
for r in S0:
    for w in (r['why'] or ['PASS']):
        reasons.setdefault(w, []).append(r)
RESULTS['guard_reason_rates'] = {w: {'cat60': bucket_stats(v, 'cat60'), 'f30': bucket_stats(v, 'f30')} for w, v in reasons.items()}
print('guard reasons:', {w: (len(v), RESULTS['guard_reason_rates'][w]['cat60'].get('mean')) for w, v in reasons.items()})

# ------------------------------------------------------------------ B. trade-level tests
def cf_size(P):
    return min(200.0, P('liq') * 0.001)


CF_KW = dict(stop=-5, tp=10, hold_min=60, size_fn=cf_size)
rnd_all = SC.rnd(0.002)
trade_specs = [
    ('R1_LIVE_COST_FIRST_RULE', SC.cost_first, dict(CF_KW), -1),
    ('R2_LIVE_COST_FIRST_RULE+RUG_GUARD_V1', lambda P: SC.cost_first(P) and not H.rug_guard_v1(P), dict(CF_KW), -1),
    ('R3_RANDOM_ALL_PUMPSWAP', rnd_all, dict(stop=-5, tp=10, hold_min=60), -1),
    ('R4_RANDOM_ALL_PUMPSWAP+RUG_GUARD_V1', lambda P: rnd_all(P) and not H.rug_guard_v1(P), dict(stop=-5, tp=10, hold_min=60), -1),
]
for c in S.CONFIGS[:2]:
    trade_specs.append((c['name'], c['signal'], dict(c['kwargs']), 0))
for b in S.BASELINES[:2]:
    trade_specs.append((b['name'], b['signal'], dict(b['kwargs']), 0))
RESULTS['trade_tests'] = {}
print('\n== B. trade-level tests, entries after the cutoff (calibrated stressed net50 primary)')
for name, sig, kw, sign in trade_specs:
    t0 = time.time()
    tr = H.simulate(sig, t_from=CUT_T, **kw)
    sm = H.summarize(tr, span_h=FWD_H)
    sm0 = H.summarize(tr, 'usd0', span_h=FWD_H)
    smc = H.summarize(tr, 'usdcal', span_h=FWD_H) if tr else {'n': 0}
    pf = H.portfolio(tr, slots=3)
    if sign < 0:
        if sm.get('n', 0) == 0:
            dec = 'no_trades'
        else:
            lo, hi = sm['ci95_mean_usd_pair']
            dec = 'confirmed' if (hi is not None and hi < 0) else ('consistent' if sm['mean_usd'] < 0 else
                                                                    ('contradicted' if (lo is not None and lo > 0) else 'not_supported'))
    else:
        dec = 'inconclusive (n<20)' if sm.get('n', 0) < 20 else 'reported'
    rec = {'net50': sm, 'net0_mean_pct': sm0.get('mean_pct'), 'netcal_mean_pct': smc.get('mean_pct'),
           'portfolio': pf, 'decision': dec,
           'trades': [{'pair': ab(x['pair']), 'sym': (x['sym'] or '')[:12], 'entry_utc':
                       datetime.fromtimestamp(x['entry_t'] / 1000, timezone.utc).strftime('%H:%M:%S'),
                       'reason': x['reason'], 'flag': x['flag'], 'net50': round(x['net50'], 2), 'net0': round(x['net0'], 2),
                       'fee': x['fee_bps'], 'liq': round(x['liq'])} for x in tr]}
    RESULTS['trade_tests'][name] = rec
    print('%-38s %-20s n=%s pairs=%s mean%%=%s median%%=%s win=%s sum$=%s CIpair=%s model%%=%s cal%%=%s exits=%s pf=%s (%.0fs)' % (
        name, dec, sm.get('n'), sm.get('pairs'), sm.get('mean_pct'), sm.get('median_pct'), sm.get('win_rate'),
        sm.get('sum_usd'), sm.get('ci95_mean_usd_pair'), sm0.get('mean_pct'), smc.get('mean_pct'), sm.get('exits'),
        pf, time.time() - t0))
RESULTS['trade_tests']['LAB_C_TAPE_BREADTH_EST'] = {'decision': 'not_testable: no tape events after the cutoff in the research snapshot'}

# ------------------------------------------------------------------ C. descriptive: live positions, cost-first universe
def utc_ms(s):
    return datetime.strptime(s, '%Y-%m-%d %H:%M:%S').replace(tzinfo=timezone.utc).timestamp() * 1000


ENTRIES_UTC = {'WOSE': '2026-10-08 07:12:18', 'GOIF': '2026-10-08 07:13:17', 'SARP': '2026-10-08 07:13:38',
               'VSOF': '2026-10-08 06:52:00'}
SYMS = {'GhBPuDpt': 'WOSE', 'D2pVedgH': 'GOIF', 'HiPe6mDS': 'SARP', 'AGZjpu5v': 'VSOF', '69fyvgoT': 'swordinu'}
live = {}
print('\n== C. open PAPER positions named by forensics: DexScreener price path after the cutoff (no ledger read)')
for pre, pair in PRE['live_pairs'].items():
    s = series[pair]
    ts, px, lq = s['t'], s['price'], s['liq']
    k_cut = bisect.bisect_right(ts, CUT_T) - 1
    sym = SYMS.get(pre, s['sym'])
    k_ent = None
    if sym in ENTRIES_UTC:
        k_ent = max(0, bisect.bisect_right(ts, utc_ms(ENTRIES_UTC[sym])) - 1)
    fw = [k for k in range(k_cut + 1, len(ts))]
    pmin = min((px[k] for k in fw if px[k] > 0), default=float('nan'))
    pmax = max((px[k] for k in fw if px[k] > 0), default=float('nan'))
    last = len(ts) - 1
    gone_min = (T1 - ts[last]) / 60000
    P_last = H.Past(s, last)
    rec = {'sym': sym, 'pair': pre, 'price_at_cut': px[k_cut], 'price_last': px[last],
           'chg_since_cut_pct': round(100 * (px[last] / px[k_cut] - 1), 2) if px[k_cut] > 0 else None,
           'chg_since_entry_pct': (round(100 * (px[last] / px[k_ent] - 1), 2) if (k_ent is not None and px[k_ent] > 0) else None),
           'fwd_min_vs_cut_pct': round(100 * (pmin / px[k_cut] - 1), 2) if px[k_cut] > 0 else None,
           'fwd_max_vs_cut_pct': round(100 * (pmax / px[k_cut] - 1), 2) if px[k_cut] > 0 else None,
           'liq_at_cut': round(lq[k_cut]), 'liq_last': round(lq[last]), 'minutes_since_last_point': round(gone_min, 1),
           'rug_reasons_last': H.rug_reasons(P_last), 'fwd_points': len(fw)}
    live[sym] = rec
    print(rec)
RESULTS['live_positions'] = live

cf_units = {}
for pair, s in series.items():
    if s['dex'] != 'pumpswap' or s['quote_sol'] != 1:
        continue
    ts = s['t']
    i0 = bisect.bisect_left(ts, CUT_T)
    last_m = None
    for i in range(i0, len(ts)):
        m = int(ts[i] // 60_000)
        if m == last_m:
            continue
        last_m = m
        P = H.Past(s, i)
        if not SC.cost_first(P):
            continue
        d = cf_units.setdefault(pair, {'sym': s['sym'], 'units': 0, 'guard': 0, 'interim': 0, 'why': set()})
        d['units'] += 1
        rr = H.rug_reasons(P)
        if rr:
            d['guard'] += 1
            d['why'].update(rr)
        if H.interim_rug_risk(P):
            d['interim'] += 1
tot = sum(d['units'] for d in cf_units.values())
g = sum(d['guard'] for d in cf_units.values())
it = sum(d['interim'] for d in cf_units.values())
print('\ncost-first universe after the cutoff: %d minute-units on %d pools; rug_guard_v1 flags %.1f %%, interim %.1f %%' % (
    tot, len(cf_units), 100 * g / max(1, tot), 100 * it / max(1, tot)))
cf_list = []
for pair, d in sorted(cf_units.items(), key=lambda kv: -kv[1]['units']):
    cf_list.append({'pair': ab(pair), 'sym': (d['sym'] or '')[:12], 'units': d['units'], 'guard_share': round(d['guard'] / d['units'], 2),
                    'interim_share': round(d['interim'] / d['units'], 2), 'why': sorted(d['why'])})
    print('  ', cf_list[-1])
RESULTS['cost_first_universe_forward'] = {'units': tot, 'pools': len(cf_units), 'guard_share': g / max(1, tot),
                                          'interim_share': it / max(1, tot), 'pools_detail': cf_list}

with open(os.path.join(HERE, 'forward_results.json'), 'w') as fh:
    json.dump(RESULTS, fh, indent=1, default=str)
print('\nwrote forward_results.json; total secs %.0f' % (time.time() - T_START))
