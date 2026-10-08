"""Leaderboard integrator (PAPER research only): re-run every family's CONFIGS and BASELINES under the final audited
harness (leaderboard/harness_final.py), with and without the rug_guard_v1 screen, plus reference rows, and build one
leaderboard sorted by the holdout stressed (calibrated net50) mean $ per trade.

Usage (one python process at a time; each step caches its trades in leaderboard/trades/<key>.pkl):
    python -B run_all.py selfcheck            # verify the I1/I2 copies against their sources, v2 parity
    python -B run_all.py run <key> [<key>..]  # keys: reference f1_momentum ... f9_regime forensics  (or 'all')
    python -B run_all.py aggregate            # -> leaderboard.json, LEADERBOARD.md
    python -B run_all.py everything           # selfcheck + run all + aggregate in one process

How each row is produced
  * sys.modules['harness'] = harness_final BEFORE any family module is imported, so every `import harness as H`
    inside a family (and its helper modules) binds to the final harness.
  * signal configs: H.simulate(signal, **kwargs).  run(H) configs: cfg['run'](H), then H.calibrate_trade() on every
    trade (the custom simulators price net50 with the plain +50 bps stress; I2 is applied post hoc).
  * variant 'orig'          : as submitted by the family.
  * variant 'rug_guard_v1'  : signal configs -> signal'(P) = signal(P) and not H.rug_guard_v1(P)  (signal called
                              first so stateful signals see the same call pattern).  run(H) configs (f6, f8) have no
                              single signal: they get a copy of the harness module whose interim_rug_risk(P) returns
                              interim_rug_risk(P) or rug_guard_v1(P) - both families call H.interim_rug_risk inside their
                              universe/signal, so the guard is ANDed into their eligibility; the number of guard
                              consultations is recorded to prove it was wired in.
    Baselines are run in both variants too, so a guarded config is compared with a guarded baseline.
  * a fresh copy of the family module is loaded for each variant (no state shared between variants).
Mechanical fixes (logic unchanged): f6_rotation is loaded from leaderboard/fixed/f6_rotation/ where rotation.py reads
the pair's first timestamp through the past-only view instead of P.static('t') (refused by the audited harness, F4).
"""
import sys
sys.dont_write_bytecode = True
import importlib.util, json, math, os, pickle, time, traceback, types

DEEP = (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r''
LB = DEEP + '/leaderboard'
TRADES_DIR = LB + '/trades'
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass


def _load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


H = _load_module('harness_final', LB + '/harness_final.py')
sys.modules['harness'] = H          # every `import harness` from here on resolves to the final harness
if DEEP not in sys.path:
    sys.path.insert(0, DEEP)

FAMILY_PATHS = {
    'f1_momentum': DEEP + '/f1_momentum/strategies.py',
    'f2_meanrev': DEEP + '/f2_meanrev/strategies.py',
    'f3_newpools': DEEP + '/f3_newpools/strategies.py',
    'f4_attention': DEEP + '/f4_attention/strategies.py',
    'f5_flowaccel': DEEP + '/f5_flowaccel/strategies.py',
    'f6_rotation': LB + '/fixed/f6_rotation/strategies.py',     # mechanical fix, see module docstring
    'f7_exits': DEEP + '/f7_exits/strategies.py',
    'f8_tape': DEEP + '/f8_tape/strategies.py',
    'f9_regime': DEEP + '/f9_regime/strategies.py',
    'forensics': DEEP + '/forensics/strategies.py',              # reference family: the live rule sets replayed
}
FAMILY_KEYS = ['f1_momentum', 'f2_meanrev', 'f3_newpools', 'f4_attention', 'f5_flowaccel', 'f6_rotation',
               'f7_exits', 'f8_tape', 'f9_regime']
CONFIGS_TRIED = {'f1_momentum': 352, 'f2_meanrev': 509, 'f3_newpools': 38, 'f4_attention': 10, 'f5_flowaccel': 703,
                 'f6_rotation': 2208, 'f7_exits': 60714, 'f8_tape': 106, 'f9_regime': 453, 'forensics': 9}
FAMILY_VERDICT = {'f1_momentum': 'no_edge', 'f2_meanrev': 'no_edge', 'f3_newpools': 'no_edge',
                  'f4_attention': 'no_edge', 'f5_flowaccel': 'weak', 'f6_rotation': 'no_edge', 'f7_exits': 'no_edge',
                  'f8_tape': 'no_edge', 'f9_regime': 'no_edge', 'forensics': 'no_edge (live rules)'}
FILL_MODEL = {'f6_rotation': 'custom rotation sim: entry at i+1 / exit at trigger+1 (v1 next-point fills), no feed-gap '
                             'closes; I2 calibration applied post hoc',
              'f8_tape': 'custom tsim: next-point fills at the on-chain price when fresh (else series), no feed-gap '
                         'closes; I2 calibration applied post hoc'}
DEFAULT_FILL = 'harness_final.simulate (v2 next-refresh fills, feed-gap closes, calibrated stress inside)'
# explicit config -> baseline mapping where the family did not pair them one-to-one by position
BASELINE_MAP = {
    'f5_flowaccel': {'F5_C1_TAPE_BREADTH_NOCHASE': ['F5_RANDOM_TAPE_LIVE_NOCHASE'],
                     'F5_C2_TAPE_BREADTH': ['F5_RANDOM_TAPE_LIVE'],
                     'F5_C3_DEXSCREENER_TWIN_NOCHASE': ['F5_RANDOM_TAPE_LIVE_NOCHASE']},
}
VARIANTS = ('orig', 'rug_guard_v1')


# ------------------------------------------------------------------ guard wiring
def guarded_signal(sig):
    def f(P):
        return bool(sig(P)) and not H.rug_guard_v1(P)
    return f


def guarded_harness():
    """Copy of the final harness module whose interim_rug_risk also applies rug_guard_v1 (for run(H) configs)."""
    m = types.ModuleType('harness_final_guarded')
    m.__dict__.update({k: v for k, v in H.__dict__.items() if not (k.startswith('__') and k.endswith('__'))})
    cnt = {'calls': 0, 'guard_blocks': 0}

    def interim_or_guard(P):
        cnt['calls'] += 1
        if H.interim_rug_risk(P):
            return True
        if H.rug_guard_v1(P):
            cnt['guard_blocks'] += 1
            return True
        return False
    m.interim_rug_risk = interim_or_guard
    m.GUARD_COUNTERS = cnt
    return m


def run_one(cfg, variant, ghm=None):
    """Returns (trades, info). ghm: the ONE guarded harness copy used for every run(H) config of a family variant
    (one object per variant, so per-object caches inside family modules - e.g. f6 keys its universe preparation by
    id(H) - always refer to the guarded universe; counters are reported as deltas per config)."""
    info = {'variant': variant}
    if cfg.get('run') is not None:
        hm = H if variant == 'orig' else ghm
        before = dict(hm.GUARD_COUNTERS) if variant != 'orig' else None
        trades = cfg['run'](hm)
        for x in trades:
            H.calibrate_trade(x)
        if variant != 'orig':
            delta = {k: hm.GUARD_COUNTERS[k] - before[k] for k in before}
            info['guard_counters'] = delta
            info['guard_counters_family_cumulative'] = dict(hm.GUARD_COUNTERS)
            # wired = the guarded copy was consulted during this run, or the run reused this variant's guarded
            # (cached) universe preparation that consulted it earlier
            info['guard_wired'] = hm.GUARD_COUNTERS['calls'] > 0
            info['guard_note'] = ('consulted in this run' if delta['calls'] > 0 else
                                  'reused this variant\'s guarded cached universe (no new calls)')
        info['mode'] = 'run'
    else:
        sig = cfg['signal'] if variant == 'orig' else guarded_signal(cfg['signal'])
        trades = H.simulate(sig, **dict(cfg.get('kwargs') or {}))
        info['mode'] = 'simulate'
        if variant != 'orig':
            info['guard_wired'] = True
    return trades, info


def _uses_guard_already(cfg):
    """True when the config's signal source already calls rug_guard_v1 (then the guarded rerun is redundant)."""
    import inspect
    fn = cfg.get('signal') or cfg.get('run')
    try:
        src = inspect.getsource(fn)
    except Exception:
        return False
    return 'rug_guard_v1' in src


# ------------------------------------------------------------------ reference rows
def reference_specs():
    import smoke_common as SC   # imports `harness` -> harness_final

    def cf_size(P):
        return min(200.0, P('liq') * 0.001)
    cf_kw = dict(stop=-5, tp=10, hold_min=60, size_fn=cf_size)
    rnd_cf = SC.rnd(0.01)            # smoke3 baseline: hashed coin p=0.01/point, salt 'r'
    rnd_all = SC.rnd(0.002)          # audit random_all_pumpswap: p=0.002/point, salt 'r'
    configs = [
        {'name': 'REF_LIVE_COST_FIRST_RULE',
         'description': 'The live COST_FIRST_UNIVERSE_V1 rule (smoke_common.cost_first: PumpSwap, fee<=50 bps, '
                        'liq>=$250k, modeled RT<=1.2%) entering whenever eligible (cooldown 300 s), -5/+10/60, '
                        'size min($200, 0.1% liq). No flow gate.',
         'signal': SC.cost_first, 'kwargs': dict(cf_kw)},
        {'name': 'REF_LIVE_COST_FIRST_RULE+INTERIM_GUARD',
         'description': 'Same with the interim rug screen (H.interim_rug_risk) instead of rug_guard_v1.',
         'signal': (lambda P: SC.cost_first(P) and not H.interim_rug_risk(P)), 'kwargs': dict(cf_kw)},
    ]
    baselines = [
        {'name': 'REF_RANDOM_COST_FIRST',
         'description': 'Random entries (p=0.01/point, smoke3) in the live cost-first universe, -5/+10/60, same size.',
         'signal': (lambda P: SC.cost_first(P) and rnd_cf(P)), 'kwargs': dict(cf_kw)},
        {'name': 'REF_RANDOM_COST_FIRST+INTERIM_GUARD',
         'description': 'Random entries (p=0.01/point) in the cost-first universe with the interim screen.',
         'signal': (lambda P: SC.cost_first(P) and not H.interim_rug_risk(P) and rnd_cf(P)), 'kwargs': dict(cf_kw)},
        {'name': 'REF_RANDOM_ALL_PUMPSWAP',
         'description': 'Random entries (p=0.002/point) on all PumpSwap SOL pairs, -5/+10/60, $200.',
         'signal': (lambda P: rnd_all(P)), 'kwargs': dict(stop=-5, tp=10, hold_min=60)},
    ]
    bmap = {'REF_LIVE_COST_FIRST_RULE': ['REF_RANDOM_COST_FIRST'],
            'REF_LIVE_COST_FIRST_RULE+INTERIM_GUARD': ['REF_RANDOM_COST_FIRST+INTERIM_GUARD']}
    return configs, baselines, bmap


def baseline_map(key, mod_configs, mod_baselines):
    names_c = [c['name'] for c in mod_configs]
    names_b = [b['name'] for b in mod_baselines]
    if key in BASELINE_MAP:
        return BASELINE_MAP[key]
    if key == 'f6_rotation':
        return {c: [b for b in names_b if b.startswith(c + '__')] for c in names_c}
    if len(names_c) == len(names_b):
        return {c: [b] for c, b in zip(names_c, names_b)}
    return {c: list(names_b) for c in names_c}   # fallback: compare with every baseline


# ------------------------------------------------------------------ run a family
def run_family(key):
    t_start = time.time()
    out = {'key': key, 'rows': [], 'errors': [], 'started': t_start}
    bmap = None
    for variant in VARIANTS:
        try:
            if key == 'reference':
                configs, baselines, bmap = reference_specs()
            else:
                mod = _load_module('lbfam_%s_%s' % (key, variant), FAMILY_PATHS[key])
                configs = list(getattr(mod, 'CONFIGS', []) or [])
                baselines = list(getattr(mod, 'BASELINES', []) or [])
                bmap = baseline_map(key, configs, baselines)
        except Exception:
            tb = traceback.format_exc()
            print('!! load failed', key, variant, tb)
            out['errors'].append({'variant': variant, 'stage': 'load', 'traceback': tb})
            continue
        ghm = guarded_harness() if variant != 'orig' else None
        for kind, group in (('config', configs), ('baseline', baselines)):
            for cfg in group:
                if variant != 'orig' and _uses_guard_already(cfg):
                    out['rows'].append({'name': cfg['name'], 'kind': kind, 'variant': variant, 'skipped':
                                        'config already uses rug_guard_v1', 'trades': []})
                    continue
                t0 = time.time()
                try:
                    trades, info = run_one(cfg, variant, ghm)
                    err = None
                except Exception:
                    trades, info, err = [], {'variant': variant}, traceback.format_exc()
                    print('!! run failed', key, cfg['name'], variant, err)
                dt = round(time.time() - t0, 1)
                row = {'name': cfg['name'], 'kind': kind, 'variant': variant, 'description': cfg.get('description', ''),
                       'kwargs': {k: (v if isinstance(v, (int, float, str, type(None), tuple, list)) else repr(v))
                                  for k, v in (cfg.get('kwargs') or {}).items()},
                       'mode': info.get('mode'), 'info': info, 'trades': trades, 'seconds': dt, 'error': err,
                       'baselines': (bmap or {}).get(cfg['name'], []) if kind == 'config' else []}
                out['rows'].append(row)
                print('  %-12s %-9s %-13s %-55s n=%4d  %5.1fs%s' % (key, kind, variant, cfg['name'][:55], len(trades), dt,
                                                                     '  ERROR' if err else ''), flush=True)
    out['seconds'] = round(time.time() - t_start, 1)
    with open(os.path.join(TRADES_DIR, key + '.pkl'), 'wb') as fh:
        pickle.dump(out, fh, protocol=pickle.HIGHEST_PROTOCOL)
    print('== %s done in %.1fs' % (key, out['seconds']), flush=True)
    return out


# ------------------------------------------------------------------ self checks
def selfcheck():
    res = {}
    series, meta = H.load()
    # (1) rug guard copy vs rug/rug_guard.py on sampled points
    src = _load_module('rug_guard_src', H.RUG_GUARD_SOURCE)
    n = mism = blocked = 0
    for pair, s in series.items():
        for i in range(0, len(s['t']), 41):
            P = H.Past(s, i)
            a = (H.rug_guard_v1(P), H.rug_guard_structural(P), H.young_only(P), tuple(H.rug_reasons(P)))
            b = (src.rug_guard_v1(P), src.rug_guard_structural(P), src.young_only(P), tuple(src.rug_reasons(P)))
            n += 1
            blocked += a[0]
            mism += a != b
    res['rug_guard_copy'] = {'points_checked': n, 'mismatches': mism, 'share_blocked': round(blocked / max(n, 1), 4),
                             'source_version': getattr(src, 'VERSION', None), 'copy_version': H.RUG_GUARD_VERSION}
    # (2) calibration copy vs calib/calibration.py on a grid
    K = _load_module('calibration_src', H.CALIB_SOURCE)
    n = mism = 0
    for fee in (30, 40, 47.5, 50, 52.5, 60, 75, 90, 95, 100, 105, 120, 125):
        for liq in (5e3, 20e3, 30e3, 49_999, 50e3, 66_667, 100e3, 250e3, 450e3, 2e6):
            for N in (1, 10, 40, 50, 100, 150, 200, 500, 1000):
                for basis in ('engine', 'market'):
                    for cons in (True, False):
                        n += 1
                        mism += abs(H.calib_extra_bps_per_leg(fee, liq, N, basis, cons) -
                                    K.extra_bps_per_leg(fee, liq, N, basis, cons)) > 1e-12
    for r in ('STOP', 'STOP/VANISHED', 'TRAIL', 'TP', 'HOLD', 'FEED_GAP', 'DRAIN', 'BE', 'ROTATE', None, ''):
        for cons in (True, False):
            n += 1
            mism += H.calib_exit_reason_extra_bps(r, cons) != K.exit_reason_extra_bps(r, cons)
    res['calibration_copy'] = {'grid_points': n, 'mismatches': mism, 'source_version': K.VERSION,
                               'copy_version': H.CALIB_VERSION}
    # (3) parity with audit/harness_v2.py: with I2 switched off, simulate must reproduce v2 trade for trade
    V2 = _load_module('harness_v2_ref', DEEP + '/audit/harness_v2.py')
    V2._LOADED = H._LOADED
    sig_v2 = lambda P: V2.hashed_coin(P.static('pair'), P.t, 0.002, 'parity')
    sig_f = lambda P: H.hashed_coin(P.static('pair'), P.t, 0.002, 'parity')
    kw = dict(stop=-5, tp=10, trail_arm=6, trail=3, hold_min=60)
    tv2 = V2.simulate(sig_v2, **kw)
    H.CALIB_IN_STRESS = False
    try:
        toff = H.simulate(sig_f, **kw)
    finally:
        H.CALIB_IN_STRESS = True
    ton = H.simulate(sig_f, **kw)
    keys = ('pair', 'entry_t', 'exit_t', 'reason', 'flag', 'net0', 'net50', 'size')
    same_off = len(tv2) == len(toff) and all(all(a[k] == b[k] for k in keys) for a, b in zip(tv2, toff))
    same_entries = len(tv2) == len(ton) and all(all(a[k] == b[k] for k in ('pair', 'entry_t', 'exit_t', 'reason', 'net0'))
                                               for a, b in zip(tv2, ton))
    v2_eq = all(abs(a['net50'] - b['net50_v2']) < 1e-9 for a, b in zip(tv2, ton)) if same_entries else False
    d = [b['net50'] - a['net50'] for a, b in zip(tv2, ton)] if same_entries else []
    # (4) post-hoc calibrate_trade() vs exact in-simulate calibration (used for run(H) configs)
    errs = []
    for b in ton:
        c = dict(b)
        c['net50'], c['usd50'] = c['net50_v2'], c['usd50_v2']
        c.pop('calib', None)
        H.calibrate_trade(c)
        errs.append(abs(c['net50'] - b['net50']))
    res['v2_parity'] = {'trades': len(tv2), 'calib_off_identical_to_v2': same_off,
                        'calib_on_same_trades_as_v2': same_entries, 'net50_v2_field_equals_v2_net50': v2_eq,
                        'mean_net50_shift_pct': round(sum(d) / len(d), 4) if d else None,
                        'min_net50_shift_pct': round(min(d), 4) if d else None,
                        'max_net50_shift_pct': round(max(d), 4) if d else None,
                        'posthoc_vs_exact_mean_abs_err_pct': round(sum(errs) / len(errs), 5) if errs else None,
                        'posthoc_vs_exact_max_abs_err_pct': round(max(errs), 5) if errs else None}
    with open(os.path.join(TRADES_DIR, 'selfcheck.json'), 'w', encoding='utf-8') as fh:
        json.dump(res, fh, indent=1)
    print(json.dumps(res, indent=1))
    return res


# ------------------------------------------------------------------ aggregation
def _r(x, nd=3):
    if x is None:
        return None
    if isinstance(x, float) and (math.isnan(x) or math.isinf(x)):
        return None if math.isnan(x) else (1e9 if x > 0 else -1e9)
    return round(x, nd)


def _exitmix(d):
    return ', '.join('%s %d' % (k, v) for k, v in sorted((d or {}).items(), key=lambda kv: -kv[1]))


def metrics(trades):
    _, meta = H.load()
    cut = H.split_t(meta)
    ev = H.evaluate(trades)
    ho = [x for x in trades if x['entry_t'] >= cut]
    tr = [x for x in trades if x['entry_t'] < cut]
    sh_ho = (meta['t1'] - cut) / 3.6e6
    sh_tr = (cut - meta['t0']) / 3.6e6
    hv2 = H.summarize(ho, 'usd50_v2', span_h=sh_ho) if ho else {'n': 0}
    hcal = H.summarize(ho, 'usdcal', span_h=sh_ho) if ho else {'n': 0}
    tv2 = H.summarize(tr, 'usd50_v2', span_h=sh_tr) if tr else {'n': 0}
    pp = {}
    for x in ho:
        pp.setdefault((x.get('sym') or '?', x['pair'][:8]), []).append(x['net50'])
    top_pairs = sorted(([k[0], k[1], len(v), round(sum(v) / len(v), 2)] for k, v in pp.items()), key=lambda r: -r[2])[:5]
    # sensitivity: drop the STOP/TRAIL exit-leg extra (approx: proceeds x (1 + rx/1e4))
    noreason = [100 * ((1 + x['net50'] / 100) * (1 + x.get('calib_exit_bps', 0.0) / 1e4) - 1) for x in ho]
    pf_ho = H.portfolio(ho, slots=3) if ho else None
    pf_all = H.portfolio(trades, slots=3) if trades else None
    h, t, hm = ev['holdout'], ev['train'], ev['holdout_model']
    return {
        'holdout_n': h.get('n', 0), 'holdout_pairs': h.get('pairs'), 'holdout_clusters': h.get('clusters'),
        'holdout_mean_usd': h.get('mean_usd'), 'holdout_mean_pct': h.get('mean_pct'),
        'holdout_median_pct': h.get('median_pct'), 'holdout_sum_usd': h.get('sum_usd'),
        'holdout_ci95_usd': h.get('ci95_mean_usd'), 'holdout_ci95_usd_pair': h.get('ci95_mean_usd_pair'),
        'holdout_win_rate': h.get('win_rate'), 'holdout_pf': _r(h.get('pf')) if h.get('pf') is not None else None,
        'top_pair_share': h.get('top_pair_share'), 'trades_per_hour': h.get('trades_per_hour'),
        'holdout_avg_hold_min': h.get('avg_hold_min'), 'holdout_exits': h.get('exits'),
        'holdout_vanished': h.get('vanished'), 'holdout_gap_closed': h.get('gap_closed'),
        'holdout_model_mean_pct': hm.get('mean_pct'), 'holdout_model_mean_usd': hm.get('mean_usd'),
        'holdout_v2stress_mean_pct': hv2.get('mean_pct'), 'holdout_v2stress_mean_usd': hv2.get('mean_usd'),
        'holdout_calonly_mean_pct': hcal.get('mean_pct'), 'holdout_calonly_mean_usd': hcal.get('mean_usd'),
        'holdout_net50_noreason_mean_pct_approx': _r(sum(noreason) / len(noreason)) if noreason else None,
        'train_n': t.get('n', 0), 'train_pairs': t.get('pairs'), 'train_mean_usd': t.get('mean_usd'),
        'train_mean_pct': t.get('mean_pct'), 'train_win_rate': t.get('win_rate'),
        'train_top_pair_share': t.get('top_pair_share'),
        'train_model_mean_pct': ev['train_model'].get('mean_pct'), 'train_v2stress_mean_pct': tv2.get('mean_pct'),
        'holdout_top_pairs': top_pairs,
        'portfolio_holdout': pf_ho, 'portfolio_full': pf_all,
        'n_total': len(trades), 'n_train_check': len(tr),
    }


def gate(m, base_means):
    fails = []
    if not (m['holdout_n'] >= 20):
        fails.append('holdout n<20')
    if not ((m['holdout_pairs'] or 0) >= 8):
        fails.append('pairs<8')
    if not ((m['holdout_mean_pct'] or -1) > 0 and (m['holdout_mean_usd'] or -1) > 0):
        fails.append('holdout mean<=0')
    if not ((m['train_mean_pct'] or -1) > 0 and (m['train_mean_usd'] or -1) > 0):
        fails.append('train mean<=0')
    if not (m['top_pair_share'] is not None and m['top_pair_share'] <= 0.35):
        fails.append('top_pair_share>0.35')
    if not base_means:
        fails.append('no baseline')
    elif any(b is None or not ((m['holdout_mean_usd'] if m['holdout_mean_usd'] is not None else -1e9) > b)
             for b in base_means):
        fails.append('not > random baseline')
    return fails


def aggregate():
    rows = []
    loaded = {}
    for key in ['reference'] + FAMILY_KEYS + ['forensics']:
        p = os.path.join(TRADES_DIR, key + '.pkl')
        if not os.path.exists(p):
            print('missing', p)
            continue
        with open(p, 'rb') as fh:
            loaded[key] = pickle.load(fh)
    sc = None
    if os.path.exists(os.path.join(TRADES_DIR, 'selfcheck.json')):
        with open(os.path.join(TRADES_DIR, 'selfcheck.json'), encoding='utf-8') as fh:
            sc = json.load(fh)
    for key, d in loaded.items():
        by = {}
        for r in d['rows']:
            m = metrics(r['trades']) if r['trades'] else None
            by[(r['name'], r['variant'])] = (r, m)
        for (name, variant), (r, m) in by.items():
            kind = r['kind']
            if key == 'reference':
                row_kind = 'reference' if kind == 'config' else 'reference-baseline'
            elif key == 'forensics':
                row_kind = 'reference' if kind == 'config' else 'reference-baseline'
            else:
                row_kind = kind
            base_means, base_pcts, base_names = [], [], []
            for bn in r.get('baselines') or []:
                bb = by.get((bn, variant))
                base_names.append(bn)
                base_means.append(bb[1]['holdout_mean_usd'] if (bb and bb[1]) else None)
                base_pcts.append(bb[1]['holdout_mean_pct'] if (bb and bb[1]) else None)
            row = {'family': key, 'name': name, 'variant': variant, 'kind': row_kind,
                   'mode': r.get('mode'), 'fill_model': FILL_MODEL.get(key, DEFAULT_FILL),
                   'description': r.get('description', ''), 'error': (r.get('error') or '')[-600:] or None,
                   'skipped': r.get('skipped'), 'seconds': r.get('seconds'),
                   'guard_info': {k: v for k, v in (r.get('info') or {}).items() if k.startswith('guard')},
                   'baseline_names': base_names, 'baseline_holdout_mean_usd_each': base_means,
                   'baseline_holdout_mean_pct_each': base_pcts}
            if m is None:
                row.update({'holdout_n': 0, 'is_candidate': False, 'gate_fails': ['no trades']})
                rows.append(row)
                continue
            row.update(m)
            valid_b = [b for b in base_means if b is not None]
            row['baseline_holdout_mean_usd'] = max(valid_b) if valid_b else None
            vp = [b for b in base_pcts if b is not None]
            row['baseline_holdout_mean_pct'] = max(vp) if vp else None
            if kind == 'config':
                fails = gate(m, base_means)
                row['gate_fails'] = fails
                row['is_candidate'] = not fails
            else:
                fails = gate(m, [None])
                fails = [f for f in fails if f not in ('no baseline', 'not > random baseline')]
                row['gate_fails'] = ['random baseline (not eligible)'] + fails
                row['is_candidate'] = False
            rows.append(row)

    def sk(x):
        v = x.get('holdout_mean_usd')
        return -(v if v is not None else -1e9)
    rows.sort(key=sk)
    for k, x in enumerate(rows, 1):
        x['rank'] = k
    out = {'generated': time.strftime('%Y-%m-%d %H:%M:%S'), 'harness': 'leaderboard/harness_final.py',
           'selfcheck': sc, 'configs_tried_by_family': CONFIGS_TRIED,
           'configs_tried_total': sum(CONFIGS_TRIED.values()),
           'family_verdicts_reported': FAMILY_VERDICT,
           'run_seconds': {k: d.get('seconds') for k, d in loaded.items()},
           'run_errors': {k: d.get('errors') for k, d in loaded.items() if d.get('errors')},
           'gate': 'config row; holdout n>=20 across >=8 pairs; holdout mean net50 (calibrated stress) > 0 in % and $; '
                   'train mean net50 > 0; holdout top_pair_share <= 0.35; holdout mean $ > EVERY mapped random '
                   'baseline holdout mean $ in the same variant',
           'rows': rows}
    with open(os.path.join(LB, 'leaderboard.json'), 'w', encoding='utf-8') as fh:
        json.dump(out, fh, indent=1, default=str)
    write_md(out)
    print('rows', len(rows), 'candidates', sum(1 for x in rows if x.get('is_candidate')))
    return out


def _f(v, nd=2, pct=False):
    if v is None:
        return '-'
    if isinstance(v, float) and math.isinf(v):
        return 'inf'
    s = ('%.' + str(nd) + 'f') % v
    return s + ('%' if pct else '')


def _ci(c):
    if not c or c[0] is None:
        return '-'
    return '[%s, %s]' % (_f(c[0]), _f(c[1]))


def _pfs(p):
    if not p:
        return '-'
    return '%d tk, $%.0f, DD %.1f%%' % (p['taken'], p['final_balance'], p['max_drawdown_pct'])


def write_md(out):
    rows = out['rows']
    L = []
    L.append('# Strategy leaderboard (PAPER research, 22.76 h of recorded data)')
    L.append('')
    L.append('Generated %s by `run_all.py` under `harness_final.py`. All money is simulated. Nothing here promises '
             'profit.' % out['generated'])
    L.append('')
    cands = [x for x in rows if x.get('is_candidate')]
    L.append('**Candidates passing the gate: %d.**' % len(cands) + (
        ' ' + ', '.join('%s (%s)' % (x['name'], x['variant']) for x in cands) if cands else
        ' No configuration has positive holdout expectancy after calibrated stressed costs that also beats its random '
        'baseline with enough trades and pairs.'))
    L.append('')
    fam_tried = sum(v for k, v in out['configs_tried_by_family'].items() if k != 'forensics')
    L.append('Families f1-f9 submitted %d configurations, pre-selected on train out of %d train evaluations (%s); '
             'the forensics reference family adds the 3 replayed live rule sets (from 9). Each was re-run here '
             'unchanged and with the rug guard, next to its random baselines and the reference rows.' % (
                 sum(1 for x in rows if x['kind'] == 'config' and x['variant'] == 'orig'), fam_tried,
                 ', '.join('%s %d' % kv for kv in out['configs_tried_by_family'].items() if kv[0] != 'forensics')))
    L.append('')
    # per-family summary: best submitted config per family and variant
    L.append('## Best row per family (submitted configs and reference rules only)')
    L.append('')
    L.append('| family | best config | variant | H n | pairs | H mean $ | H mean % | CI95 $ | train mean % | '
             'best random baseline H $ | gate |')
    L.append('|' + '---|' * 11)
    fams = []
    for x in rows:
        if x['family'] not in fams:
            fams.append(x['family'])
    for fam in sorted(fams):
        cand = [x for x in rows if x['family'] == fam and x['kind'] in ('config', 'reference')
                and x.get('holdout_n')]
        if not cand:
            L.append('| %s | (no holdout trades) | | | | | | | | | |' % fam)
            continue
        b = max(cand, key=lambda x: x.get('holdout_mean_usd') if x.get('holdout_mean_usd') is not None else -1e9)
        L.append('| %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s |' % (
            fam, b['name'], b['variant'], b.get('holdout_n'), b.get('holdout_pairs'), _f(b.get('holdout_mean_usd')),
            _f(b.get('holdout_mean_pct')), _ci(b.get('holdout_ci95_usd')), _f(b.get('train_mean_pct')),
            _f(b.get('baseline_holdout_mean_usd')),
            'CANDIDATE' if b.get('is_candidate') else '; '.join(b.get('gate_fails') or [])))
    L.append('')
    # reference rows and the noise floor
    L.append('## Reference rows (live cost-first rule, random entries)')
    L.append('')
    L.append('| name | variant | H n | pairs | H mean % (net50) | v2-stress % | model % | train mean % | portfolio |')
    L.append('|' + '---|' * 9)
    for x in rows:
        if x['family'] == 'reference' and x.get('holdout_n'):
            L.append('| %s | %s | %s | %s | %s | %s | %s | %s | %s |' % (
                x['name'], x['variant'], x['holdout_n'], x.get('holdout_pairs'), _f(x.get('holdout_mean_pct')),
                _f(x.get('holdout_v2stress_mean_pct')), _f(x.get('holdout_model_mean_pct')),
                _f(x.get('train_mean_pct')), _pfs(x.get('portfolio_holdout'))))
    L.append('')
    rb = [x for x in rows if x['kind'] in ('baseline', 'reference-baseline') and x.get('holdout_n')]
    pos = [x for x in rb if (x.get('holdout_mean_usd') or -1) > 0]
    if rb:
        best = max(rb, key=lambda x: x.get('holdout_mean_usd') or -1e9)
        L.append('Noise floor: %d of %d random-baseline rows have a positive holdout mean; the best random row is %s '
                 '(%s) at %s %%/trade on %s trades, CI95 %s. Holdout means of a few percent on 20-40 trades are '
                 'within luck.' % (len(pos), len(rb), best['name'], best['variant'], _f(best.get('holdout_mean_pct')),
                                   best.get('holdout_n'), _ci(best.get('holdout_ci95_usd'))))
        L.append('')
    L.append('## How to read it')
    L.append('')
    L.append('- **Primary basis = stressed and calibrated net50**: engine cost model + calibrated extra bps per leg '
             '(CALIB_V1) + 50 bps per leg, with +200 bps on the exit leg of stop exits and +100 bps on trailing exits. '
             '`model %` is the engine cost model alone (net0), `v2-stress %` the audited harness stress without the '
             'calibration, `cal-only %` the calibration without the 50 bps stress (the most realistic central '
             'estimate).')
    L.append('- Holdout = the last 40% of the span by entry time (about 9.1 h); train = the first 60%.')
    L.append('- CI95 = bootstrap over (pair, hour) clusters of the mean $ per trade; `CI95 pair` resamples whole '
             'pairs (more conservative).')
    L.append('- `portfolio` = H.portfolio(holdout trades, slots=3, start $1000): trades taken, final balance, max '
             'drawdown.')
    L.append('- Variants: `orig` = as submitted; `rug_guard_v1` = the same with the frozen rug guard ANDed in '
             '(run(H) configs: guard ORed into their H.interim_rug_risk). Baselines are random entries in the same '
             'universe with the same exits, run in both variants.')
    L.append('- Gate: ' + out['gate'] + '.')
    L.append('')
    sc = out.get('selfcheck') or {}
    if sc:
        L.append('## Self-checks')
        L.append('')
        rg, ca, vp = sc.get('rug_guard_copy', {}), sc.get('calibration_copy', {}), sc.get('v2_parity', {})
        L.append('- rug_guard_v1 copy vs rug/rug_guard.py: %s sampled points, %s mismatches (guard blocks %s of '
                 'points).' % (rg.get('points_checked'), rg.get('mismatches'), rg.get('share_blocked')))
        L.append('- calibration copy vs calib/calibration.py: %s grid points, %s mismatches.' % (
            ca.get('grid_points'), ca.get('mismatches')))
        L.append('- harness_final with I2 off reproduces audit/harness_v2.py trade for trade: %s (%s trades); with I2 '
                 'on: same trades %s, net50 shift mean %s%% (min %s, max %s); post-hoc calibrate_trade vs exact: mean '
                 'abs error %s%%, max %s%%.' % (
                     vp.get('calib_off_identical_to_v2'), vp.get('trades'), vp.get('calib_on_same_trades_as_v2'),
                     vp.get('mean_net50_shift_pct'), vp.get('min_net50_shift_pct'), vp.get('max_net50_shift_pct'),
                     vp.get('posthoc_vs_exact_mean_abs_err_pct'), vp.get('posthoc_vs_exact_max_abs_err_pct')))
        L.append('')
    if out.get('run_errors'):
        L.append('## Run errors')
        L.append('')
        for k, v in out['run_errors'].items():
            for e in v:
                L.append('- %s %s: `%s`' % (k, e.get('variant'), (e.get('traceback') or '').strip().splitlines()[-1]))
        L.append('')
    L.append('## Leaderboard (sorted by holdout stressed mean $ per trade)')
    L.append('')
    L.append('| # | family | name | variant | kind | H n | pairs | H mean $ | H mean % | H median % | CI95 $ | '
             'CI95 pair | win % | PF | top share | tr/h | train mean $ (%) | baseline H $ | model % | v2-stress % | '
             'cal-only % | portfolio (holdout) | gate |')
    L.append('|' + '---|' * 23)
    for x in rows:
        g = 'CANDIDATE' if x.get('is_candidate') else '; '.join(x.get('gate_fails') or [])
        if x.get('error'):
            g = 'ERROR: ' + x['error'].strip().splitlines()[-1][:80]
        if x.get('skipped'):
            g = 'skipped: ' + x['skipped']
        L.append('| %d | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | '
                 '%s | %s | %s |' % (
                     x['rank'], x['family'], x['name'], x['variant'], x['kind'], x.get('holdout_n', 0),
                     x.get('holdout_pairs') or '-', _f(x.get('holdout_mean_usd')), _f(x.get('holdout_mean_pct')),
                     _f(x.get('holdout_median_pct')), _ci(x.get('holdout_ci95_usd')),
                     _ci(x.get('holdout_ci95_usd_pair')), _f(x.get('holdout_win_rate'), 1), _f(x.get('holdout_pf')),
                     _f(x.get('top_pair_share')), _f(x.get('trades_per_hour')),
                     '%s (%s)' % (_f(x.get('train_mean_usd')), _f(x.get('train_mean_pct'))) if x.get('train_n') else '-',
                     _f(x.get('baseline_holdout_mean_usd')), _f(x.get('holdout_model_mean_pct')),
                     _f(x.get('holdout_v2stress_mean_pct')), _f(x.get('holdout_calonly_mean_pct')),
                     _pfs(x.get('portfolio_holdout')), g))
    L.append('')
    L.append('## Exit mix and sensitivities (holdout)')
    L.append('')
    L.append('| family | name | variant | exits | avg hold min | vanished | gap closed | net50 without STOP/TRAIL '
             'extra % (approx) | train model % / v2-stress % / net50 % | top holdout pairs (sym, pair, n, mean net50 %) '
             '| fill model | guard wiring |')
    L.append('|' + '---|' * 12)
    for x in rows:
        if not x.get('holdout_n'):
            continue
        gi = x.get('guard_info') or {}
        L.append('| %s | %s | %s | %s | %s | %s | %s | %s | %s / %s / %s | %s | %s | %s |' % (
            x['family'], x['name'], x['variant'], _exitmix(x.get('holdout_exits')), _f(x.get('holdout_avg_hold_min'), 1),
            x.get('holdout_vanished'), x.get('holdout_gap_closed'),
            _f(x.get('holdout_net50_noreason_mean_pct_approx')), _f(x.get('train_model_mean_pct')),
            _f(x.get('train_v2stress_mean_pct')), _f(x.get('train_mean_pct')),
            '; '.join('%s %s %d %+.2f' % (str(p[0]).replace('|', '/'), p[1], p[2], p[3])
                      for p in (x.get('holdout_top_pairs') or [])),
            x.get('fill_model'),
            ('calls %s, blocks %s (%s)' % (gi['guard_counters'].get('calls'), gi['guard_counters'].get('guard_blocks'),
                                          gi.get('guard_note', '')) if 'guard_counters' in gi else
             ('signal AND not rug_guard_v1' if gi.get('guard_wired') else '-'))))
    L.append('')
    with open(os.path.join(LB, 'LEADERBOARD.md'), 'w', encoding='utf-8') as fh:
        fh.write('\n'.join(L) + '\n')


if __name__ == '__main__':
    os.makedirs(TRADES_DIR, exist_ok=True)
    args = sys.argv[1:]
    t0 = time.time()
    H.load()
    print('loaded series in %.1fs' % (time.time() - t0), flush=True)
    if not args or args[0] == 'everything':
        selfcheck()
        for k in ['reference'] + FAMILY_KEYS + ['forensics']:
            run_family(k)
        aggregate()
    elif args[0] == 'selfcheck':
        selfcheck()
    elif args[0] == 'run':
        keys = args[1:]
        if keys == ['all']:
            keys = ['reference'] + FAMILY_KEYS + ['forensics']
        for k in keys:
            run_family(k)
    elif args[0] == 'aggregate':
        aggregate()
    print('total %.1fs' % (time.time() - t0))
