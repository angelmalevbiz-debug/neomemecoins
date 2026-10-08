"""Step 6 (TRAIN ONLY): verify strategies.py reproduces the train numbers and calibrate baseline trade counts."""
import os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import strategies as S
H = S.H
import f9lib as L

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
series, meta = H.load()
cut = H.split_t(meta)
g = L.build_grid(series, meta)
diff = 0
for col in ('med15', 'med15n', 'br15'):
    a, b = g[col], S.regime_grid()[col]
    for x, y in zip(a, b):
        if (x == x) != (y == y) or (x == x and abs(x - y) > 1e-9):
            diff += 1
print('grid mismatches vs f9lib:', diff, 'g0 equal', g['g0'] == S.regime_grid()['g0'])
for c in S.CONFIGS + S.BASELINES:
    tr = H.simulate(c['signal'], t_to=cut, **c['kwargs'])
    sm = H.summarize(tr)
    print('%-34s train n=%s pairs=%s mean50=%s' % (c['name'], sm.get('n'), sm.get('pairs'), sm.get('mean_pct')))
