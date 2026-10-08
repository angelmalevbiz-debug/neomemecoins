"""Calibrate random-baseline probabilities on TRAIN trade counts only (no PnL used for the choice)."""
import sys
sys.path.insert(0, (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r'/f3_newpools')
import strategies as S
H = S.H
series, meta = H.load()
CUT = H.split_t(meta)
for age, ek, target in ((1440, S.E1, 23), (1440, S.E2, 19), (120, S.E1, 9)):
    for prob in (0.002, 0.004, 0.008, 0.015, 0.03):
        tr = H.simulate(S.random_in(age, prob, 'f3b'), t_to=CUT, exit_fn=S.drain_exit(0.6), size_fn=S.size_fn, **ek)
        print('age<%d hold%d prob %.3f train n=%d pairs=%d (target %d)' % (age, ek['hold_min'], prob, len(tr), len({x['pair'] for x in tr}), target))
