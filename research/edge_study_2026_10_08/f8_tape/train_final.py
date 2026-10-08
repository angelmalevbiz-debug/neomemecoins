"""TRAIN ONLY: exit-fill mode check for the whale config + random-baseline probability calibration (by count)."""
import sys
sys.path.insert(0, (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r'')
sys.path.insert(0, (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r'/f8_tape')
import harness as H
import sig as S
import tsim
import tape_load as TL

series, meta = H.load()
cut = H.split_t(meta)
PAIRS = set(TL.load()['tape'].keys())
E2 = dict(stop=-15, tp=None, trail_arm=10, trail=8, hold_min=60)
E3 = dict(stop=-20, tp=40, hold_min=60)
keep = ('n', 'pairs', 'win_rate', 'mean_pct', 'median_pct', 'sum_usd', 'pf', 'ci95_mean_usd', 'top_pair_share')
for em in ('series', 'onchain'):
    tr = tsim.simulate(S.whale_signal(min_sol=5.0, min_liq=20_000), pairs=PAIRS, t_to=cut, entry_mode='onchain', exit_mode=em, **E2)
    sm = H.summarize([x for x in tr if x['entry_t'] < cut])
    print('WHALE5 E2 onchain-entry exit=%s' % em, {k: sm.get(k) for k in keep})
for prob in (0.002, 0.004, 0.008):
    for ex_name, ex, em in (('E2', E2, 'series'), ('E3', E3, 'onchain')):
        tr = tsim.simulate(S.random_covered_signal(prob, salt='f8b', min_liq=20_000), pairs=PAIRS, t_to=cut,
                           entry_mode='onchain', exit_mode=em, **ex)
        tr = [x for x in tr if x['entry_t'] < cut]
        print('random prob %g %s exit=%s train n %d' % (prob, ex_name, em, len(tr)))
