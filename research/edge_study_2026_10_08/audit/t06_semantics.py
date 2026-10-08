"""Audit step 6: exit trigger / fill semantics measured on simulated trades (v1 harness)."""
import bisect, collections, math, sys, time, importlib
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.path.insert(0, (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r'')
H = importlib.import_module(sys.argv[1] if len(sys.argv) > 1 else 'harness')
from smoke_common import cost_first, rnd

series, meta = H.load()
q = lambda xs, p: xs[min(len(xs) - 1, int(p * len(xs)))] if xs else None
cfgs = {
    'random_all_ps': (lambda P: rnd(0.002)(P), {}),
    'random_cf_guard': (lambda P: cost_first(P) and not H.interim_rug_risk(P) and rnd(0.01)(P), {'size_fn': lambda P: min(200.0, P('liq') * 0.001)}),
    'random_mid_fee': (lambda P: 50 < P.fee_bps() <= 125 and P('liq') >= 50_000 and not H.interim_rug_risk(P) and rnd(0.004)(P), {}),
}
for name, (sig, extra) in cfgs.items():
    t0 = time.time()
    tr = H.simulate(sig, stop=-5, tp=10, hold_min=60, **extra)
    c = collections.Counter()
    gaps, stale_fill, hold_over, stop_net, tp_net = [], 0, [], [], []
    liqbad_trig = 0
    for x in tr:
        s = series[x['pair']]
        ts = s['t']
        e = bisect.bisect_left(ts, x['exit_t'])
        c[x['reason'] + ('/' + x['flag'] if x['flag'] else '')] += 1
        if not x['flag'] and x['reason'] != 'OPEN_AT_END':
            k = bisect.bisect_left(ts, x['trigger_t']) if 'trigger_t' in x else e - 1
            gaps.append((ts[e] - ts[k]) / 1000)
            if s['price'][e] == s['price'][k]:
                stale_fill += 1
            if not (s['liq'][k] > 0):
                liqbad_trig += 1
            if x['reason'] == 'HOLD':
                hold_over.append(x['hold_s'] / 60 - 60)
            if x['reason'] == 'STOP':
                stop_net.append(x['net0'])
            if x['reason'] == 'TP':
                tp_net.append(x['net0'])
    n_fill = len(gaps)
    gaps.sort(); hold_over.sort(); stop_net.sort(); tp_net.sort()
    print('==', name, 'trades', len(tr), 'secs', round(time.time() - t0, 1))
    print('  exits', dict(c))
    print('  trigger->fill gap s: p50 %.1f p90 %.1f p99 %.1f max %.1f ; >60s %d ; >600s %d' % (
        q(gaps, .5), q(gaps, .9), q(gaps, .99), gaps[-1], sum(1 for g in gaps if g > 60), sum(1 for g in gaps if g > 600)))
    print('  exit fill at the SAME DexScreener price as the trigger point: %d of %d (%.1f%%)' % (stale_fill, n_fill, 100 * stale_fill / max(1, n_fill)))
    print('  triggers at a point with missing liquidity (20%% modeled impact): %d' % liqbad_trig)
    if hold_over:
        print('  HOLD overshoot min: p50 %.2f p90 %.2f max %.1f' % (q(hold_over, .5), q(hold_over, .9), hold_over[-1]))
    if stop_net:
        print('  STOP net0: n %d median %.2f p10 %.2f min %.2f (stop=-5)' % (len(stop_net), q(stop_net, .5), q(stop_net, .1), stop_net[0]))
    if tp_net:
        print('  TP net0: n %d median %.2f p90 %.2f max %.2f (tp=+10)' % (len(tp_net), q(tp_net, .5), q(tp_net, .9), tp_net[-1]))
    ends = [x for x in tr if x['flag'] == 'END' or x['reason'] == 'OPEN_AT_END']
    if ends:
        print('  END/OPEN_AT_END trades %d mean net50 %.2f ; their entries within 60 min of dataset end: %d' % (
            len(ends), sum(x['net50'] for x in ends) / len(ends), sum(1 for x in ends if meta['t1'] - x['entry_t'] < 3600_000)))
    van = [x for x in tr if x['flag'] == 'VANISHED']
    if van:
        print('  VANISHED trades %d mean net50 %.2f (haircut %.0f%% applied)' % (len(van), sum(x['net50'] for x in van) / len(van), H.VANISH_HAIRCUT_PCT))
    late = [x for x in tr if meta['t1'] - x['entry_t'] < 3600_000]
    if late:
        ev_all = H.summarize([x for x in tr if x['entry_t'] >= H.split_t(meta)])
        ev_cut = H.summarize([x for x in tr if x['entry_t'] >= H.split_t(meta) and meta['t1'] - x['entry_t'] >= 3600_000 + 300_000])
        print('  holdout mean net50 all %.3f (n %d) vs excluding entries in last 65 min %.3f (n %d)' % (
            ev_all['mean_pct'], ev_all['n'], ev_cut.get('mean_pct', float('nan')), ev_cut['n']))
