"""Sensitivity (NOT selection): frozen configs with exit triggers ALSO valued at on-chain prices (fully tape-priced)."""
import sys
sys.path.insert(0, (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r'')
sys.path.insert(0, (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r'/f8_tape')
import harness as H
import strategies as ST
import sig as S, tsim, tape_load as TL

PAIRS = set(TL.load()['tape'].keys())
keep = ('n', 'pairs', 'win_rate', 'mean_pct', 'median_pct', 'sum_usd', 'pf', 'ci95_mean_usd', 'top_pair_share', 'exits')
facs = [('F8_WHALE_FOLLOW_5SOL', lambda: S.whale_signal(min_sol=5.0, side=1, **ST.COMMON), ST.EXIT_TRAIL, 0.001, 'f8w5'),
        ('F8_BIGSELL_DIPBUY_5SOL', lambda: S.whale_signal(min_sol=5.0, side=-1, **ST.COMMON), ST.EXIT_WIDE, 0.002, 'f8s5'),
        ('F8_BIGSELL_DIPBUY_3SOL', lambda: S.whale_signal(min_sol=3.0, side=-1, **ST.COMMON), ST.EXIT_WIDE, 0.005, 'f8s3')]
for name, fac, ex, prob, salt in facs:
    tr = tsim.simulate(fac(), pairs=PAIRS, entry_mode='onchain', exit_mode='onchain', trigger_mode='onchain', **ex)
    bt = tsim.simulate(S.random_covered_signal(prob, salt=salt, min_liq=20_000), pairs=PAIRS, entry_mode='onchain',
                       exit_mode='onchain', trigger_mode='onchain', **ex)
    ev, eb = H.evaluate(tr), H.evaluate(bt)
    print(name, 'fully on-chain priced (entry, triggers, exits)')
    print('   train  ', {k: ev['train'].get(k) for k in keep})
    print('   holdout', {k: ev['holdout'].get(k) for k in keep})
    print('   baseline train  ', {k: eb['train'].get(k) for k in keep if k != 'exits'})
    print('   baseline holdout', {k: eb['holdout'].get(k) for k in keep if k != 'exits'})
    sys.stdout.flush()
