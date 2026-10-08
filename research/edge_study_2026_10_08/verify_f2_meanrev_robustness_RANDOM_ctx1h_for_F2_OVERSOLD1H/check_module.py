"""Check that strategies.py loads under the final harness (as the integrator does) and reproduces the row."""
import sys
sys.dont_write_bytecode = True
import importlib.util, pickle
DEEP = (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r''


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    sys.modules[name] = m
    spec.loader.exec_module(m)
    return m


H = _load('harness_final', DEEP + '/leaderboard/harness_final.py')
sys.modules['harness'] = H
S = _load('vstrat', DEEP + '/verify_f2_meanrev_robustness_RANDOM_ctx1h_for_F2_OVERSOLD1H/strategies.py')
with open(DEEP + '/leaderboard/trades/f2_meanrev.pkl', 'rb') as fh:
    ref = {(r['name'], r['variant']): r['trades'] for r in pickle.load(fh)['rows']}[
        ('RANDOM_ctx1h_for_F2_OVERSOLD1H_STAB', 'rug_guard_v1')]
b = S.BASELINES[0]
tr = H.simulate(b['signal'], **b['kwargs'])
same = len(tr) == len(ref) and all(a['entry_t'] == r['entry_t'] and a['net50'] == r['net50'] for a, r in zip(tr, ref))
print('CONFIGS', len(S.CONFIGS), 'BASELINES', len(S.BASELINES), 'reproduces row:', same, len(tr))
