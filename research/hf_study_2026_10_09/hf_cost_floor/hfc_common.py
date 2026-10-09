"""hf_cost_floor common loader (PAPER research only, read-only on all inputs).

Binds leaderboard/harness_final.py as sys.modules['harness'] exactly as rescore_lab_hypotheses.py / run_all.py do.
"""
import importlib.util
import os
import sys

sys.dont_write_bytecode = True
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
DEEP = os.path.normpath(os.path.join(HERE, '..', 'research', 'edge_study_2026_10_08'))


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def harness():
    if 'harness' in sys.modules and getattr(sys.modules['harness'], 'CALIB_VERSION', None):
        return sys.modules['harness']
    sys.path.insert(0, DEEP)
    H = _load('harness_final', os.path.join(DEEP, 'leaderboard', 'harness_final.py'))
    sys.modules['harness'] = H
    return H


def ab(x):
    return (x or '')[:8]
