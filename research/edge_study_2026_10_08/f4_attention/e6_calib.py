"""Calibrate baseline entry probabilities: prints TRADE COUNTS ONLY (train / total), never outcomes."""
import sys, os
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r'')
import harness as H
import strategies as S

series, meta = H.load()
cut = H.split_t(meta)
DEF = dict(stop=-5, tp=10, hold_min=60)
for name, mk, probs in (('latest_list', S.base_latest_list, (0.001, 0.002, 0.004)),
                        ('boosted', S.base_boosted, (0.0001, 0.0002, 0.0004)),
                        ('nontoxic', S.base_nontoxic, (0.0002, 0.0005, 0.001))):
    for p in probs:
        tr = H.simulate(mk(p), **DEF)
        print(name, p, 'train n', sum(1 for x in tr if x['entry_t'] < cut), 'total n', len(tr))
