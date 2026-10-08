"""Audit step 12: v1 vs v2 on smoke3-equivalent configs, random baselines, simple momentum/dip probes, base rates,
with a per-fix decomposition (F1 fills only, F2 gap closes only) and the rug-screen sensitivity."""
import json, sys, time
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
DEEP = (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r''
sys.path.insert(0, DEEP)
sys.path.insert(0, DEEP + '/audit')
import harness as H1
import harness_v2 as H2
from smoke_common import cost_first, rnd

H2._LOADED = H1.load()
series, meta = H1.load()
KEEP = ('n', 'pairs', 'win_rate', 'mean_pct', 'median_pct', 'sum_usd', 'pf', 'ci95_mean_usd', 'ci95_mean_usd_pair', 'trades_per_hour', 'top_pair_share', 'exits')
RESULTS = {}


def mode(fill, gap, gap_cut=0.0, gval='min'):
    H2.FILL_RULE, H2.FEED_GAP_MS, H2.TIME_ORDERED, H2.GAP_HAIRCUT_PCT, H2.GAP_VALUATION = fill, gap, True, gap_cut, gval


VARIANTS = [('v1', None), ('v2_F1_fills_only', ('next_refresh', None)), ('v2_F2_gaps_only', ('next_point', 600_000)),
            ('v2', ('next_refresh', 600_000)), ('v2_gap_pre', ('next_refresh', 600_000, 0.0, 'pre')), ('v2_gap_return', ('next_refresh', 600_000, 0.0, 'return'))]


def run(name, mk, show_full=False):
    print('==', name)
    RESULTS[name] = {}
    for vlabel, m in VARIANTS:
        if m is None:
            H = H1
        else:
            H = H2
            mode(*m)
        t0 = time.time()
        tr = H.simulate(**mk(H))
        ev = H.evaluate(tr)
        pf = H.portfolio([x for x in tr if x['entry_t'] >= H.split_t(meta)], slots=3)
        RESULTS[name][vlabel] = {'train': ev['train'], 'holdout': ev['holdout'], 'holdout_model': ev['holdout_model'], 'portfolio_holdout': pf}
        tr_s, ho_s = ev['train'], ev['holdout']
        print('  %-18s train n %4d mean %7.3f%% | holdout n %4d mean %7.3f%% median %7.3f win %5.1f sum $%8.2f | model holdout mean %7.3f | pf3 bal %s mdd %s | %.1fs' % (
            vlabel, tr_s['n'], tr_s.get('mean_pct', float('nan')), ho_s['n'], ho_s.get('mean_pct', float('nan')), ho_s.get('median_pct', float('nan')),
            ho_s.get('win_rate', float('nan')), ho_s.get('sum_usd', float('nan')), ev['holdout_model'].get('mean_pct', float('nan')),
            pf['final_balance'], pf['max_drawdown_pct'], time.time() - t0))
        if show_full and vlabel in ('v1', 'v2'):
            for part in ('train', 'holdout'):
                print('     ', vlabel, part, json.dumps({k: ev[part].get(k) for k in KEEP}, default=str))
    mode('next_refresh', 600_000)


sizef = lambda P: min(200.0, P('liq') * 0.001)
# smoke3 equivalents (the established facts #2)
run('smoke3: random cost-first, no guard', lambda H: dict(signal=lambda P: cost_first(P) and rnd(0.01)(P), stop=-5, tp=10, hold_min=60, size_fn=sizef), True)
run('smoke3: random cost-first + interim rug guard', lambda H: dict(signal=lambda P: cost_first(P) and not H.interim_rug_risk(P) and rnd(0.01)(P), stop=-5, tp=10, hold_min=60, size_fn=sizef), True)
run('random cost-first + TRAIN-ONLY rug guard (sensitivity)', lambda H: dict(signal=lambda P: cost_first(P) and not H2.rug_risk_train_only(P) and rnd(0.01)(P), stop=-5, tp=10, hold_min=60, size_fn=sizef))
run('smoke: random all PumpSwap', lambda H: dict(signal=lambda P: rnd(0.002)(P), stop=-5, tp=10, hold_min=60), True)
run('random mid/high fee, liq>=50k, guard', lambda H: dict(signal=lambda P: 50 < P.fee_bps() <= 125 and P('liq') >= 50_000 and not H.interim_rug_risk(P) and rnd(0.004)(P), stop=-5, tp=10, hold_min=60))


def mom(H, up):
    def sig(P):
        if not (P('liq') >= 50_000) or H.interim_rug_risk(P):
            return False
        a, b = P('price'), P.ago('price', 60)
        if not (a > 0 and b > 0):
            return False
        return a / b > 1.03 if up else a / b < 0.97
    return sig


run('probe: momentum (price +3% over 60 s), liq>=50k, guard', lambda H: dict(signal=mom(H, True), stop=-5, tp=10, hold_min=60))
run('probe: dip (price -3% over 60 s), liq>=50k, guard', lambda H: dict(signal=mom(H, False), stop=-5, tp=10, hold_min=60))
run('probe: momentum, tight exits -3/+3/15', lambda H: dict(signal=mom(H, True), stop=-3, tp=3, hold_min=15))

# base rates (fact #1): forward 60-min net % by bucket, sampled 1/min/pair
print('== base rates: forward net %, $200, model costs, sampled 1/min/pair (v1 forward_net vs v2)')
for hz in (900, 3600):
    for vlabel, H, md in (('v1', H1, None), ('v2_F3F6_only', H2, ('next_point', None)), ('v2', H2, ('next_refresh', 600_000))):
        if md: mode(*md)
        b = {}
        for pair, s in series.items():
            if s['dex'] != 'pumpswap' or s['quote_sol'] != 1:
                continue
            ts = s['t']
            last = -1e18
            for i in range(len(ts) - 1):
                if ts[i] - last < 60_000:
                    continue
                last = ts[i]
                fee = H1.fee_bps(s, i); liq = s['liq'][i]
                fb = 'fee<=50' if fee <= 50 else ('fee55-95' if fee <= 95 else 'fee100-125')
                lb = 'liq>=250k' if liq >= 250_000 else ('liq50-250k' if liq >= 50_000 else 'liq<50k')
                v = H.forward_net(s, i, hz)
                if v is not None:
                    b.setdefault((fb, lb), []).append(v)
        mode('next_refresh', 600_000)
        RESULTS['base_%d_%s' % (hz, vlabel)] = {}
        for k in sorted(b):
            xs = sorted(b[k]); n = len(xs)
            if n < 50:
                continue
            row = {'n': n, 'mean': round(sum(xs) / n, 2), 'median': round(xs[n // 2], 2), 'p10': round(xs[int(n * .1)], 2), 'p90': round(xs[int(n * .9)], 2), 'win': round(100 * sum(1 for x in xs if x > 0) / n, 1)}
            RESULTS['base_%d_%s' % (hz, vlabel)]['%s|%s' % k] = row
            print('  %5ds %s %-11s %-11s %s' % (hz, vlabel, k[0], k[1], row))
with open(DEEP + '/audit/t12_compare.json', 'w') as fh:
    json.dump(RESULTS, fh, indent=1, default=str)
