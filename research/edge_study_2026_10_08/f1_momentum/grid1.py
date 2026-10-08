"""F1 grid 1 (TRAIN ONLY): momentum signals x universes x exits through H.simulate.

Entries restricted to t < cut (t_to=cut). Every evaluated configuration is appended to grid1.jsonl.
"""
import json, os, sys, time
sys.path.insert(0, (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r'')
import harness as H
import features as F

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
HERE = os.path.dirname(os.path.abspath(__file__))
series, meta = H.load()
feats = F.load()
cut = H.split_t(meta)
OUT = os.path.join(HERE, sys.argv[2] if len(sys.argv) > 2 else 'grid1.jsonl')


def uni(name, fee, liq):
    if name == 'HI30':
        return fee >= 100 and liq >= 30_000
    if name == 'HI50':
        return fee >= 100 and liq >= 50_000
    if name == 'MID':
        return 55 <= fee <= 95 and liq >= 100_000
    if name == 'MIDHI50':
        return fee >= 55 and liq >= 50_000
    if name == 'LO':
        return fee <= 50 and liq >= 250_000
    raise ValueError(name)


def g(f, k, i):
    return f[k][i]


SIGNALS = {
    'spike3': lambda s, f, i: g(f, 'r300', i) >= 3 and g(f, 'bsh5', i) >= 0.55 and g(f, 'vacc', i) >= 1.0,
    'spike8': lambda s, f, i: g(f, 'r300', i) >= 8 and g(f, 'bsh5', i) >= 0.55 and g(f, 'vacc', i) >= 1.0,
    'trend10': lambda s, f, i: g(f, 'r1800', i) >= 10 and g(f, 'hi600', i) >= -2,
    'trend25': lambda s, f, i: g(f, 'r1800', i) >= 25 and g(f, 'hi600', i) >= -2,
    'dip_in_up20': lambda s, f, i: s['pc1h'][i] >= 20 and g(f, 'r300', i) <= -2,
    'dip_in_up50': lambda s, f, i: s['pc1h'][i] >= 50 and g(f, 'r300', i) <= -2,
    'breakout': lambda s, f, i: g(f, 'hi1800', i) >= 0.5 and g(f, 'r600', i) >= 3 and s['b5'][i] >= 20,
    'accel': lambda s, f, i: g(f, 'vacc', i) >= 2 and g(f, 'r60', i) >= 1 and g(f, 'bsh5', i) >= 0.6,
}
EXITS = {
    'E1_s5_tp10_h60': dict(stop=-5, tp=10, hold_min=60),
    'E2_s5_trail10/5_h120': dict(stop=-5, tp=None, trail_arm=10, trail=5, hold_min=120),
    'E3_s8_tp40_trail15/8_h120': dict(stop=-8, tp=40, trail_arm=15, trail=8, hold_min=120),
    'E4_s3_tp20_trail5/3_h30': dict(stop=-3, tp=20, trail_arm=5, trail=3, hold_min=30),
    'E5_s10_trail20/10_h120': dict(stop=-10, tp=None, trail_arm=20, trail=10, hold_min=120),
    'E6_s5_tp20_h15': dict(stop=-5, tp=20, hold_min=15),
}


def entry_sets(sig, uname):
    sets = {}
    for pair, f in feats.items():
        s = series[pair]
        ts = s['t']
        S = set()
        for i in range(len(ts)):
            if ts[i] >= cut:
                break
            if f['rug'][i] > 0 or not uni(uname, f['fee'][i], s['liq'][i]):
                continue
            if sig(s, f, i):
                S.add(i)
        if S:
            sets[pair] = S
    return sets


def keep(d):
    return {k: d.get(k) for k in ('n', 'pairs', 'clusters', 'win_rate', 'mean_pct', 'median_pct', 'sum_usd', 'pf',
                                   'ci95_mean_usd', 'trades_per_hour', 'avg_hold_min', 'top_pair_share', 'exits')}


def pair_robust(trades):
    """mean over pairs of the per-pair mean net50 (each pair weighs once)."""
    bp = {}
    for x in trades:
        bp.setdefault(x['pair'], []).append(x['net50'])
    return round(sum(sum(v) / len(v) for v in bp.values()) / len(bp), 3) if bp else None


if __name__ == '__main__':
    unis = sys.argv[1].split(',') if len(sys.argv) > 1 else ['HI30', 'HI50', 'MID', 'LO']
    n_cfg = 0
    t0 = time.time()
    with open(OUT, 'a', encoding='utf-8') as fh:
        for uname in unis:
            for sname, sig in SIGNALS.items():
                sets = entry_sets(sig, uname)
                signal = (lambda sets: (lambda P: P.i in sets.get(P.s['pair'], ())))(sets)
                for ename, kw in EXITS.items():
                    tr = H.simulate(signal, pairs=set(sets), t_to=cut, **kw)
                    tr = [x for x in tr if x['entry_t'] < cut]
                    n_cfg += 1
                    sm = H.summarize(tr)
                    rec = {'universe': uname, 'signal': sname, 'exit': ename, 'train': keep(sm),
                           'train_model_mean_pct': H.summarize(tr, 'usd0').get('mean_pct'), 'pair_mean_net50': pair_robust(tr)}
                    fh.write(json.dumps(rec) + '\n')
                    print(uname, sname, ename, 'n', sm.get('n'), 'pairs', sm.get('pairs'), 'mean50', sm.get('mean_pct'),
                          'med', sm.get('median_pct'), 'pm', rec['pair_mean_net50'], 'top', sm.get('top_pair_share'),
                          'model', rec['train_model_mean_pct'], flush=True)
    print('configs evaluated', n_cfg, 'secs', round(time.time() - t0, 1))
