"""Self-check: the local rug-guard copy in strategies.py equals harness_final.rug_guard_v1 on sampled points, and the
module imports under the plain v1 harness (no rug_guard_v1 attribute there -> local copy is used)."""
import importlib.util, os, random, sys
sys.dont_write_bytecode = True
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
DEEP = (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r''
HERE = os.path.join(DEEP, 'synthesis')
sys.path.insert(0, os.path.join(DEEP, 'leaderboard'))
import harness_final as HF
sys.modules['harness'] = HF
spec = importlib.util.spec_from_file_location('syn_strat', os.path.join(HERE, 'strategies.py'))
S = importlib.util.module_from_spec(spec)
spec.loader.exec_module(S)
print('under harness_final: rug_guard is harness rug_guard_v1:', S.rug_guard is HF.rug_guard_v1)
series, meta = HF.load()
rnd = random.Random(5)
pairs = [p for p, s in series.items() if s['dex'] == 'pumpswap' and s['quote_sol'] == 1]
n = mism = blocked = 0
for _ in range(60_000):
    s = series[pairs[rnd.randrange(len(pairs))]]
    i = rnd.randrange(len(s['t']))
    P = HF.Past(s, i)
    a, b = S._rug_guard_local(P), HF.rug_guard_v1(P)
    n += 1
    mism += a != b
    blocked += b
print('local copy vs harness_final.rug_guard_v1: points %d mismatches %d blocked share %.3f' % (n, mism, blocked / n))
# plain v1 harness import (module-level code only; no simulation)
del sys.modules['harness']
import importlib
H1 = importlib.import_module('harness')
print('plain harness module file:', os.path.basename(H1.__file__), 'has rug_guard_v1:', hasattr(H1, 'rug_guard_v1'))
spec2 = importlib.util.spec_from_file_location('syn_strat_v1', os.path.join(HERE, 'strategies.py'))
S1 = importlib.util.module_from_spec(spec2)
spec2.loader.exec_module(S1)
print('under v1: rug_guard is local copy:', S1.rug_guard is S1._rug_guard_local, '| CONFIGS', [c['name'] for c in S1.CONFIGS],
      '| BASELINES', [b['name'] for b in S1.BASELINES])
