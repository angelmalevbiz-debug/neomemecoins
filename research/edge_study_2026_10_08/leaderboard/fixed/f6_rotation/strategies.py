"""F6 cross-sectional rotation: the (at most 3) configs pre-selected on TRAIN, plus their random baselines.

Every config is a custom simulator ('run': callable(H) -> trades) built only on harness primitives
(H.load, H.Past, H.entry_fill, H.exit_value, H.interim_rug_risk, H.fee_bps, H.roundtrip_cost_pct, constants).
The rotation engine lives in rotation.py next to this file (single source of truth).

Selection rule (fixed before any holdout trade was generated): among 2,208 train-only configurations,
keep those with train n >= 20, pairs >= 8, top_pair_share <= 0.35 and train mean net50 > 0; then take
(1) the best one positive in BOTH train halves, (2) the best pure size ('hold the K largest') config and
(3) the best config with train n >= 30 (a reversal factor) - three different hypotheses.
"""
import os, sys, importlib.util

sys.path.insert(0, (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '../../..'))) + r'')
import harness as H  # noqa: E402  (the integrator passes its own audited H to run())

_FALLBACK = (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '../../..'))) + r'/f6_rotation'
_here = os.path.dirname(os.path.abspath(__file__)) if '__file__' in globals() else _FALLBACK
_path = os.path.join(_here, 'rotation.py')
if not os.path.exists(_path):
    _path = os.path.join(_FALLBACK, 'rotation.py')
_spec = importlib.util.spec_from_file_location('f6_rotation_engine', _path)
R = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(R)

_UNIV = {'U50': dict(min_liq=50_000, max_rt=4.0), 'U100': dict(min_liq=100_000, max_rt=4.0)}
_PREP = {}
_TRADES = {}


def _prep(Hm, uname, every):
    key = (id(Hm), uname, every)
    if key not in _PREP:
        _PREP[key] = R.prepare(Hm, R.universe(**_UNIV[uname]), every_min=every)
    return _PREP[key]


def _strategy(name, uname, every, spec, K, buffer, max_hold_min, stop=None):
    def run(Hm):
        key = (id(Hm), name)
        if key not in _TRADES:
            _TRADES[key] = R.run(Hm, _prep(Hm, uname, every), spec, K=K, buffer=buffer, stop=stop,
                                 max_hold_min=max_hold_min, tag=name)
        return list(_TRADES[key])
    return run


def _matched_random(name, uname, every, strategy_run, salt, stop=None):
    def run(Hm):
        return R.matched_random(Hm, _prep(Hm, uname, every), strategy_run(Hm), salt=salt, stop=stop,
                                tag=name)
    return run


def _random_k(name, uname, every, K, buffer, max_hold_min, salt):
    def run(Hm):
        return R.run(Hm, _prep(Hm, uname, every), ('random', salt), K=K, buffer=buffer, stop=None,
                     max_hold_min=max_hold_min, tag=name)
    return run


_DEFS = [
    {'name': 'F6_STABLE_SIZE_U50_E5_K3',
     'description': 'Every 5 min rank rug-screened PumpSwap pools (liq >= $50k, modeled RT <= 4%) by the sum of '
                    'percentile ranks of liquidity and NEGATIVE 15-min realized volatility (big, calm pools); '
                    'hold the top 3, keep a holding while it stays in the top 5, no stop, no max hold.',
     'u': 'U50', 'every': 5, 'spec': ('rank', (('liq', 1), ('neg_vol15', 1))), 'K': 3, 'buffer': 2, 'mh': None},
    {'name': 'F6_LARGEST3_U50_E15_MH120',
     'description': 'Every 15 min hold the 3 largest rug-screened PumpSwap pools by liquidity (liq >= $50k, RT <= 4%); '
                    'keep while inside the top 5, sell after 120 min (may re-buy after one tick), no stop.',
     'u': 'U50', 'every': 15, 'spec': ('factor', 'liq'), 'K': 3, 'buffer': 2, 'mh': 120},
    {'name': 'F6_REVERSAL6H_U50_E5_MH120',
     'description': 'Every 5 min rank rug-screened PumpSwap pools (liq >= $50k, RT <= 4%) by NEGATIVE DexScreener 6h '
                    'price change (biggest 6h losers first); hold the top 3, keep while inside the top 5, '
                    'sell after 120 min, no stop.',
     'u': 'U50', 'every': 5, 'spec': ('factor', 'neg_pc6h'), 'K': 3, 'buffer': 2, 'mh': 120},
]

CONFIGS = []
BASELINES = []
for d in _DEFS:
    srun = _strategy(d['name'], d['u'], d['every'], d['spec'], d['K'], d['buffer'], d['mh'])
    CONFIGS.append({'name': d['name'], 'description': d['description'], 'run': srun, 'signal': None,
                    'kwargs': {'universe': d['u'], 'every_min': d['every'], 'spec': d['spec'], 'K': d['K'],
                               'buffer': d['buffer'], 'max_hold_min': d['mh'], 'stop': None, 'notional': 200.0}})
    BASELINES.append({'name': d['name'] + '__MATCHED_RANDOM',
                      'description': 'Same count, decision ticks and holding horizon as ' + d['name'] +
                                     ', but each entry is a hashed-uniform pool from the same eligible universe at that tick.',
                      'run': _matched_random(d['name'] + '__MATCHED_RANDOM', d['u'], d['every'], srun, 'm1'),
                      'signal': None, 'kwargs': {}})
    BASELINES.append({'name': d['name'] + '__RANDOM_K_ROTATION',
                      'description': 'Identical rotation mechanics as ' + d['name'] + ' with a hashed random score redrawn each tick.',
                      'run': _random_k(d['name'] + '__RANDOM_K_ROTATION', d['u'], d['every'], d['K'], d['buffer'], d['mh'], 'r1'),
                      'signal': None, 'kwargs': {}})
