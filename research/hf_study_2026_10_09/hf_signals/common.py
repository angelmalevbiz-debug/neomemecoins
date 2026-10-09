"""hf_signals common loader: binds the audited harness exactly as rescore_lab_hypotheses.py / run_all.py do.
PAPER research only. Read-only on the research data; writes only into this folder."""
import importlib.util
import math
import os
import sys

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
DEEP = os.path.normpath(os.path.join(HERE, '..', 'research', 'edge_study_2026_10_08'))


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


sys.path.insert(0, DEEP)
H = _load('harness_final', os.path.join(DEEP, 'leaderboard', 'harness_final.py'))
sys.modules['harness'] = H

NAN = float('nan')


def fin(x):
    return x == x and x not in (float('inf'), float('-inf'))


def ab(x):
    return (x or '')[:8]


def guard_ok_arrays(liq, mc, age):
    """Array form of NOT H.rug_guard_v1 (True = tradeable). Verified against H.rug_guard_v1 in census.py."""
    if not (liq == liq and mc == mc and age == age and liq > 0 and mc > 0 and age >= 0):
        return False
    lmc = liq / mc
    if lmc >= H.LP_PULLABLE_MIN_LIQ_TO_MCAP:
        return False
    if age < H.YOUNG_POOL_MAX_AGE_MIN:
        return False
    if mc >= H.FAKE_MCAP_MIN_USD and lmc < H.FAKE_MCAP_MAX_LIQ_TO_MCAP and age < H.FAKE_MCAP_MAX_AGE_MIN:
        return False
    return True
