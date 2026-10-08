"""TRAIN-ONLY random-entry baselines inside tape-covered moments (same static filters), by token age."""
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
    'E2 -15/trail10>8/60m': dict(stop=-15, tp=None, trail_arm=10, trail=8, hold_min=60),
    'E3 -20/+40/60m': dict(stop=-20, tp=40, hold_min=60),
}
keep = ('n', 'pairs', 'win_rate', 'mean_pct', 'median_pct', 'sum_usd', 'pf', 'ci95_mean_usd', 'top_pair_share')


def young(P):
    return P('age') < 60


for (agename, agef), (ename, ekw) in itertools.product((('age<60', lambda P: P('age') < 60), ('age>=60', lambda P: P('age') >= 60)), EXITS.items()):
    for salt in ('a', 'b'):
        base = S.random_covered_signal(0.004, salt=salt, min_liq=20_000)
        sig = lambda P, base=base, agef=agef: base(P) and agef(P)
        tr = tsim.simulate(sig, pairs=PAIRS, t_to=cut, entry_mode='onchain', exit_mode='series', **ekw)
        tr = [x for x in tr if x['entry_t'] < cut]
        sm = H.summarize(tr)
        print('RANDOM covered %s %s salt %s onchain-entry net50' % (agename, ename, salt), {k: sm.get(k) for k in keep})
        sys.stdout.flush()
