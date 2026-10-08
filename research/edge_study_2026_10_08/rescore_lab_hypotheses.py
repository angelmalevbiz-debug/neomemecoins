"""Re-score the frozen Lab hypotheses (synthesis/strategies.py) on the current dataset.

Run from anywhere after rebuilding the dataset (see README.md):
    python research/edge_study_2026_10_08/rescore_lab_hypotheses.py

Uses the audited, calibrated harness (leaderboard/harness_final.py) bound as `harness`,
exactly as leaderboard/run_all.py does, and prints train / holdout / full-span summaries at
the acceptance basis (net50 = engine model + CALIB_V1 + 50 bps per leg + stop/trail extras).
Read-only: it never touches the live runtime or the repo.
"""
import importlib.util
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.dont_write_bytecode = True


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def main():
    sys.path.insert(0, HERE)
    H = _load('harness_final', os.path.join(HERE, 'leaderboard', 'harness_final.py'))
    sys.modules['harness'] = H
    S = _load('lab_hypotheses', os.path.join(HERE, 'synthesis', 'strategies.py'))
    keep = ('n', 'pairs', 'win_rate', 'mean_pct', 'median_pct', 'sum_usd', 'pf', 'ci95_mean_usd', 'trades_per_hour')
    rows = []
    for kind, items in (('config', getattr(S, 'CONFIGS', [])), ('baseline', getattr(S, 'BASELINES', []))):
        for cfg in items:
            if 'run' in cfg:
                trades = cfg['run'](H)
            else:
                trades = H.simulate(cfg['signal'], **cfg.get('kwargs', {}))
            ev = H.evaluate(trades)
            row = {'kind': kind, 'name': cfg['name'], 'all': {k: H.summarize(trades).get(k) for k in keep}}
            for part in ('train', 'holdout'):
                row[part] = {k: ev[part].get(k) for k in keep}
            rows.append(row)
            print(json.dumps(row, ensure_ascii=True))
    return rows


if __name__ == '__main__':
    main()
