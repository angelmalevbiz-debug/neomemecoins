"""F8 - on-chain order-flow / smart-wallet family: the 3 configs pre-selected on TRAIN, plus random baselines.

All three are TAPE-TRIGGERED and therefore use a custom 'run' (tsim.simulate) instead of H.simulate:
DexScreener prices lag the chain by ~5-30 s, so right after a large on-chain swap the series still shows
the pre-swap price. H.simulate would fill a follower at that stale price (measured: +5..11% phantom
discount after >=5 SOL buys). tsim.simulate is H.simulate line-for-line except that the entry (and here
also the exit) FILL price is the on-chain marginal price implied by the latest swap the engine had at that
moment (block time within 30 s), falling back to the series price when the tape has nothing fresh.
Costs, latency, stops, stress (net50), vanish haircut and cooldown are the harness primitives.

Selection was made on TRAIN only (entries before the 60% split); see REPORT.md for the full config count.
"""
import os, sys
sys.path.insert(0, (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r'')
import harness as H
_HERE = (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r'/f8_tape'
sys.path.insert(0, _HERE)
import onchain as _OC
import sig as _S
import tape_load as _TL
import tsim as _TS

COMMON = dict(min_liq=20_000, rug_screen=True, fresh_s=60)          # PumpSwap SOL pools only (simulate default dexes)
EXIT_TRAIL = dict(stop=-15, tp=None, trail_arm=10, trail=8, hold_min=60)
EXIT_WIDE = dict(stop=-20, tp=40, hold_min=60)


def _bind(Hmod):
    """Point the helper modules at the harness object the caller passes (integrator's audited harness)."""
    for m in (_OC, _S, _TS):
        m.H = Hmod


def _pairs():
    return set(_TL.load()['tape'].keys())


def _runner(signal_factory, exits, entry_mode='onchain', exit_mode='onchain', trigger_mode='series', tag=''):
    def run(Hmod=H):
        _bind(Hmod)
        return _TS.simulate(signal_factory(), pairs=_pairs(), entry_mode=entry_mode, exit_mode=exit_mode,
                            trigger_mode=trigger_mode, tag=tag, **exits)
    return run


CONFIGS = [
    {'name': 'F8_WHALE_FOLLOW_5SOL',
     'description': 'Buy after a single on-chain BUY >= 5 SOL becomes available (<=60 s after its block), PumpSwap SOL pool, '
                    'liq >= $20k, interim rug screen, any age. On-chain entry/exit fills. Stop -15, trail arm +10 / give-back 8, 60 min.',
     'signal': lambda P: _S.whale_signal(min_sol=5.0, side=1, **COMMON)(P),
     'kwargs': dict(EXIT_TRAIL),
     'run': _runner(lambda: _S.whale_signal(min_sol=5.0, side=1, **COMMON), EXIT_TRAIL, tag='F8_WHALE_FOLLOW_5SOL')},
    {'name': 'F8_BIGSELL_DIPBUY_5SOL',
     'description': 'Buy the dip after a single on-chain SELL >= 5 SOL (<=60 s old), same universe/screens. On-chain fills. '
                    'Stop -20, TP +40, 60 min.',
     'signal': lambda P: _S.whale_signal(min_sol=5.0, side=-1, **COMMON)(P),
     'kwargs': dict(EXIT_WIDE),
     'run': _runner(lambda: _S.whale_signal(min_sol=5.0, side=-1, **COMMON), EXIT_WIDE, tag='F8_BIGSELL_DIPBUY_5SOL')},
    {'name': 'F8_BIGSELL_DIPBUY_3SOL',
     'description': 'As F8_BIGSELL_DIPBUY_5SOL with a 3 SOL threshold (larger-sample sibling).',
     'signal': lambda P: _S.whale_signal(min_sol=3.0, side=-1, **COMMON)(P),
     'kwargs': dict(EXIT_WIDE),
     'run': _runner(lambda: _S.whale_signal(min_sol=3.0, side=-1, **COMMON), EXIT_WIDE, tag='F8_BIGSELL_DIPBUY_3SOL')},
]
# NOTE: 'signal' + 'kwargs' are given for reference only; running them through H.simulate prices the entry at the stale
# DexScreener quote and overstates results (see REPORT.md). Use 'run'.

_RCOMMON = dict(min_liq=20_000, rug_screen=True)
BASELINES = [
    {'name': 'RANDOM_COVERED_for_F8_WHALE_FOLLOW_5SOL',
     'description': 'Random entries at tape-covered moments (a tape event available within 300 s), same universe, screens, fills and exits.',
     'signal': lambda P: _S.random_covered_signal(0.001, salt='f8w5', **_RCOMMON)(P),
     'kwargs': dict(EXIT_TRAIL),
     'run': _runner(lambda: _S.random_covered_signal(0.001, salt='f8w5', **_RCOMMON), EXIT_TRAIL, tag='RND_W5')},
    {'name': 'RANDOM_COVERED_for_F8_BIGSELL_DIPBUY_5SOL',
     'description': 'Random entries at tape-covered moments, same universe, screens, fills and exits as F8_BIGSELL_DIPBUY_5SOL.',
     'signal': lambda P: _S.random_covered_signal(0.002, salt='f8s5', **_RCOMMON)(P),
     'kwargs': dict(EXIT_WIDE),
     'run': _runner(lambda: _S.random_covered_signal(0.002, salt='f8s5', **_RCOMMON), EXIT_WIDE, tag='RND_S5')},
    {'name': 'RANDOM_COVERED_for_F8_BIGSELL_DIPBUY_3SOL',
     'description': 'Random entries at tape-covered moments, same universe, screens, fills and exits as F8_BIGSELL_DIPBUY_3SOL.',
     'signal': lambda P: _S.random_covered_signal(0.005, salt='f8s3', **_RCOMMON)(P),
     'kwargs': dict(EXIT_WIDE),
     'run': _runner(lambda: _S.random_covered_signal(0.005, salt='f8s3', **_RCOMMON), EXIT_WIDE, tag='RND_S3')},
]

# Post-hoc fill-model sensitivity (NOT used for selection): exit triggers also valued at on-chain prices.
for _c, _f, _e in ((CONFIGS[0], lambda: _S.whale_signal(min_sol=5.0, side=1, **COMMON), EXIT_TRAIL),
                   (CONFIGS[1], lambda: _S.whale_signal(min_sol=5.0, side=-1, **COMMON), EXIT_WIDE),
                   (CONFIGS[2], lambda: _S.whale_signal(min_sol=3.0, side=-1, **COMMON), EXIT_WIDE)):
    _c['run_sensitivity_fully_onchain'] = _runner(_f, _e, trigger_mode='onchain', tag=_c['name'] + '_FULLY_ONCHAIN')
