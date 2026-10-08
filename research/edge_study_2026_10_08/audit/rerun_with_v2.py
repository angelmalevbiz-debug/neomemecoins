"""Integrator tool: run any family's strategies.py (CONFIGS + BASELINES) under the audited harness_v2 and, for
comparison, under the original v1 harness - without editing the strategy module.

Usage:  python rerun_with_v2.py <path/to/strategies.py> [--v1-too] [--script path/to/script.py]
  The strategy module's own `import harness as H` resolves to harness_v2 because sys.modules['harness'] is aliased
  before the module is loaded. --script runs an arbitrary script (e.g. ../smoke3.py) under the same aliasing.
"""
import importlib, importlib.util, json, runpy, sys, time
DEEP = (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r''
sys.path.insert(0, DEEP)
sys.path.insert(0, DEEP + '/audit')
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

KEEP = ('n', 'pairs', 'clusters', 'win_rate', 'mean_pct', 'median_pct', 'sum_usd', 'pf', 'ci95_mean_usd',
        'ci95_mean_usd_pair', 'trades_per_hour', 'top_pair_share', 'vanished', 'gap_closed', 'censored_end', 'exits')


def use(version):
    for m in ('harness', 'smoke_common'):
        sys.modules.pop(m, None)
    if version == 'v2':
        import harness_v2
        sys.modules['harness'] = harness_v2
        return harness_v2
    H = importlib.import_module('harness')
    return H


def load_strategies(path, tag):
    spec = importlib.util.spec_from_file_location('strategies_%s' % tag, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def run_cfg(H, cfg):
    if 'run' in cfg:
        return cfg['run'](H)
    return H.simulate(cfg['signal'], **cfg.get('kwargs', {}))


def report(H, label, cfg, trades):
    ev = H.evaluate(trades)
    _, meta = H.load()
    ho = [x for x in trades if x['entry_t'] >= H.split_t(meta)]
    print('--', label, cfg['name'])
    for part in ('train', 'holdout', 'holdout_model'):
        print('   ', part, json.dumps({k: ev[part].get(k) for k in KEEP}, default=str))
    print('    portfolio(holdout, slots=3):', H.portfolio(ho, slots=3))


if __name__ == '__main__':
    args = sys.argv[1:]
    if '--script' in args:
        path = args[args.index('--script') + 1]
        for v in (['v1', 'v2'] if '--v1-too' in args else ['v2']):
            H = use(v)
            if v == 'v2':
                H._LOADED = None
            print('=== script %s under %s' % (path, v))
            t0 = time.time()
            runpy.run_path(path, run_name='__main__')
            print('=== %.1fs' % (time.time() - t0))
        sys.exit(0)
    path = args[0]
    shared = None
    for v in (['v1', 'v2'] if '--v1-too' in args else ['v2']):
        H = use(v)
        if shared is not None:
            H._LOADED = shared
        shared = H.load()
        mod = load_strategies(path, v)
        for group in ('CONFIGS', 'BASELINES'):
            for cfg in getattr(mod, group, []):
                t0 = time.time()
                tr = run_cfg(H, cfg)
                report(H, '%s %s (%.1fs)' % (v, group, time.time() - t0), cfg, tr)
