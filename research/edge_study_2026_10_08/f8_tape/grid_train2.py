"""TRAIN-ONLY whale-follow grid re-run with tape-aware entry prices (entries restricted to t < split)."""
import itertools, sys, time
sys.path.insert(0, (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r'')
sys.path.insert(0, (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r'/f8_tape')
import harness as H
import sig as S
import tsim
import tape_load as TL

series, meta = H.load()
cut = H.split_t(meta)
PAIRS = set(TL.load()['tape'].keys())
EXITS = {
    'E1 -10/+25/30m': dict(stop=-10, tp=25, hold_min=30),
    'E2 -15/trail10>8/60m': dict(stop=-15, tp=None, trail_arm=10, trail=8, hold_min=60),
    'E3 -20/+40/60m': dict(stop=-20, tp=40, hold_min=60),
}
keep = ('n', 'pairs', 'win_rate', 'mean_pct', 'median_pct', 'sum_usd', 'pf', 'ci95_mean_usd', 'top_pair_share', 'exits')
t0 = time.time()
# sanity: tsim(series) must equal H.simulate
sigx = S.whale_signal(min_sol=5.0, min_liq=20_000)
a = H.simulate(sigx, pairs=PAIRS, t_to=cut, **EXITS['E2 -15/trail10>8/60m'])
b = tsim.simulate(sigx, pairs=PAIRS, t_to=cut, entry_mode='series', **EXITS['E2 -15/trail10>8/60m'])
print('sanity identical:', len(a) == len(b) and all(abs(x['net50'] - y['net50']) < 1e-9 for x, y in zip(a, b)))
n_cfg = 0
for mode in ('onchain', 'max'):
    for min_sol, min_age, (ename, ekw) in itertools.product((3.0, 5.0, 10.0), (0.0, 60.0), EXITS.items()):
        sig = S.whale_signal(min_sol=min_sol, min_age_min=min_age, min_liq=20_000)
        tr = tsim.simulate(sig, pairs=PAIRS, t_to=cut, entry_mode=mode, **ekw)
        n_cfg += 1
        tr = [x for x in tr if x['entry_t'] < cut]
        sm = H.summarize(tr)
        sm0 = H.summarize(tr, 'usd0')
        prem = [x['entry_px_used'] / x['entry_px_series'] - 1 for x in tr if x['entry_px_series'] > 0]
        print('[%s] cfg%02d sol>=%g age>=%g %s  entry premium vs series: mean %.2f%% (onchain used %d/%d)' % (
            mode, n_cfg, min_sol, min_age, ename, 100 * sum(prem) / max(1, len(prem)), sum(x['entry_onchain'] for x in tr), len(tr)))
        print('     net50', {k: sm.get(k) for k in keep})
        print('     net0  mean %s med %s pf %s' % (sm0.get('mean_pct'), sm0.get('median_pct'), sm0.get('pf')))
        sys.stdout.flush()
print('configs', n_cfg, 'secs', round(time.time() - t0, 1))
