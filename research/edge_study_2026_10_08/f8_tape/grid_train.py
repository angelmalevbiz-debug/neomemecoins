"""TRAIN-ONLY grid for the whale-buy follow family (entries restricted to t < split via t_to)."""
import itertools, json, sys, time
sys.path.insert(0, (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r'')
sys.path.insert(0, (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r'/f8_tape')
import harness as H
import sig as S
import tape_load as TL

series, meta = H.load()
cut = H.split_t(meta)
PAIRS = set(TL.load()['tape'].keys())
EXITS = {
    'E1 -10/+25/30m': dict(stop=-10, tp=25, hold_min=30),
    'E2 -15/trail10>8/60m': dict(stop=-15, tp=None, trail_arm=10, trail=8, hold_min=60),
    'E3 -20/+40/60m': dict(stop=-20, tp=40, hold_min=60),
}
keep = ('n', 'pairs', 'clusters', 'win_rate', 'mean_pct', 'median_pct', 'sum_usd', 'pf', 'ci95_mean_usd', 'top_pair_share', 'exits')
n_cfg = 0
t0 = time.time()
mode = sys.argv[1] if len(sys.argv) > 1 else 'grid'
if mode == 'grid':
    for min_sol, min_age, (ename, ekw) in itertools.product((3.0, 5.0, 10.0), (0.0, 60.0), EXITS.items()):
        sig = S.whale_signal(min_sol=min_sol, min_age_min=min_age, min_liq=20_000)
        tr = H.simulate(sig, pairs=PAIRS, t_to=cut, **ekw)
        n_cfg += 1
        sm = H.summarize([x for x in tr if x['entry_t'] < cut])
        sm0 = H.summarize([x for x in tr if x['entry_t'] < cut], 'usd0')
        print('cfg%02d sol>=%g age>=%g %s' % (n_cfg, min_sol, min_age, ename))
        print('     net50', {k: sm.get(k) for k in keep})
        print('     net0  mean %s med %s pf %s' % (sm0.get('mean_pct'), sm0.get('median_pct'), sm0.get('pf')))
        sys.stdout.flush()
print('configs', n_cfg, 'secs', round(time.time() - t0, 1))
