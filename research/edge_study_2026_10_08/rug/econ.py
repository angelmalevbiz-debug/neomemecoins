"""Economic check: random entries per universe, with and without the frozen guards (harness simulate).

Also re-values every trade's exit with UNCAPPED constant-product impact (realistic drain loss):
the harness caps impact at 20% and treats liquidity 0 as 20% impact, so an LP pull (liquidity -> 0,
price unchanged) is booked at about -22..-32% instead of ~-100%.
"""
import bisect, json, os, sys, time
from rugcommon import HERE, H, CUT
import rug_guard as G

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.path.insert(0, os.path.dirname(HERE))
from smoke_common import cost_first  # noqa: E402

series, meta = H.load()


def exit_value_real(s, k, qty, extra_bps=0.0, price=None):
    p = s['price'][k] if price is None else price
    liq, su = s['liq'][k], H.sol_usd(s, k)
    if not (p > 0):
        return 0.0
    mv = qty * p
    x = (2 * mv / liq) if liq > 0 else float('inf')
    impact = 1.0 if x == float('inf') else max(min(x, 0.2), x / (1 + x))
    pen = impact + (H.BASE_BPS + extra_bps) / 1e4
    return max(0.0, mv * (1 - pen) * (1 - H.fee_bps(s, k) / 1e4) - H.NETWORK_SOL * su)


def revalue(trades):
    for x in trades:
        s = series[x['pair']]
        ts = s['t']
        j = bisect.bisect_left(ts, x['entry_t'])
        e = bisect.bisect_left(ts, x['exit_t'])
        f50 = H.entry_fill(s, j, x['size'], H.STRESS_BPS)
        f0 = H.entry_fill(s, j, x['size'])
        px = s['price'][e] * (1 - H.VANISH_HAIRCUT_PCT / 100) if x['flag'] == 'VANISHED' else s['price'][e]
        v50 = exit_value_real(s, e, f50[0], H.STRESS_BPS, px)
        v0 = exit_value_real(s, e, f0[0], 0.0, px)
        x['net50r'] = 100 * (v50 - x['size'] - f50[1]) / x['size']
        x['usd50r'] = x['size'] * x['net50r'] / 100
        x['net0r'] = 100 * (v0 - x['size'] - f0[1]) / x['size']
    return trades


def rnd(prob, salt='rug'):
    return lambda P: H.hashed_coin(P.static('pair'), P.t, prob, salt)


UNIS = {
    'CF': (lambda P: cost_first(P), lambda P: min(200.0, P('liq') * 0.001), 0.03),
    'LIQ20': (lambda P: P('liq') >= 20_000, None, 0.006),
    'HF50': (lambda P: P('liq') >= 50_000 and P.fee_bps() >= 100, None, 0.012),
    'HF250': (lambda P: P('liq') >= 250_000 and P.fee_bps() >= 100, None, 0.06),
}
GUARDS = {'none': lambda P: False, 'G1': G.rug_guard_v1, 'G2': G.rug_guard_structural, 'INTERIM': H.interim_rug_risk}
KEEP = ('n', 'pairs', 'clusters', 'win_rate', 'mean_pct', 'median_pct', 'pf', 'sum_usd', 'ci95_mean_usd', 'top_pair_share', 'trades_per_hour', 'exits')


def part_stats(trades):
    out = {}
    for name, sel in (('train', [x for x in trades if x['entry_t'] < CUT]), ('holdout', [x for x in trades if x['entry_t'] >= CUT])):
        sm = H.summarize(sel)
        d = {k: sm.get(k) for k in KEEP}
        if sel:
            d['mean_net0'] = round(sum(x['net0'] for x in sel) / len(sel), 3)
            d['mean_net50_realistic'] = round(sum(x['net50r'] for x in sel) / len(sel), 3)
            d['trades_le_-50pct_realistic'] = sum(1 for x in sel if x['net50r'] <= -50)
            d['trades_le_-50pct_harness'] = sum(1 for x in sel if x['net50'] <= -50)
            d['portfolio3'] = H.portfolio(sel, 3)
        out[name] = d
    return out
def main():
    
    
    results = {}
    only = sys.argv[1:]  # optional subset of universes
    for un, (uf, sz, p) in UNIS.items():
        if only and un not in only:
            continue
        for gn, gf in GUARDS.items():
            t0 = time.time()
            tr = H.simulate(lambda P, uf=uf, gf=gf, p=p: uf(P) and rnd(p)(P) and not gf(P), stop=-5, tp=10, hold_min=60,
                            size_fn=sz, tag='%s/%s' % (un, gn))
            revalue(tr)
            st = part_stats(tr)
            results['%s|%s' % (un, gn)] = st
            print('==', un, gn, 'n', len(tr), 'secs', round(time.time() - t0, 1))
            for part in ('train', 'holdout'):
                print('   ', part, json.dumps(st[part], default=str))
            sys.stdout.flush()
    json.dump(results, open(os.path.join(HERE, 'econ_%s.json' % ('_'.join(only) if only else 'all')), 'w'), indent=1, default=str)


if __name__ == "__main__":
    main()
