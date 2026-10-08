"""forensics: the three LIVE entry rule sets replayed on the harness (pre-selected on TRAIN only).

None of these is a CANDIDATE (all have train AND holdout mean net50 < 0); they are kept so the
integrator can reproduce the forensic finding that neither tape-verified flow nor a DexScreener
txns proxy turns the live rule sets into positive expectancy.

configs_tried = 9 (3 families x {VF, PROXY, NONE}); selection rule fixed before holdout:
best train mean net50 per family with train n >= 10  ->  WE_PROXY, OFA_VF, CF_PROXY.
Baselines: hashed-coin random entries inside the same family universe, same exits, probability
picked from a grid by matching the TRAIN trade count only (outcome-blind).
"""
import os
import sys
sys.dont_write_bytecode = True
sys.path.insert(0, (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r'')
import harness as H  # noqa: E402
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import families as F  # noqa: E402  (imports backend rule modules read-only, no bytecode written)

EXITS = dict(F.EXITS)  # stop -5 / tp +10 / hold 60 min / cooldown 1200 s (engine same-token cooldown)

CONFIGS = [
    {'name': 'FORENSICS_WE_PROXY',
     'description': 'Main engine WINNER_ENSEMBLE_PAPER_V1 market rules (winner_ensemble.market_candidates) + '
                    'modeled RT <= 1.5% at the smallest size-ladder step, with the tape flow gate replaced by a '
                    'DexScreener proxy (b5 >= 3 and b5/max(s5,1) >= 1.2). Engine fixed exits -5/+10/60, 20 min cooldown.',
     'signal': F.we_signal('PROXY'),
     'kwargs': dict(EXITS, size_fn=F.main_requested)},
    {'name': 'FORENSICS_OFA_VF',
     'description': 'ORDER_FLOW_ADAPTIVE (Oct-4) market rules + modeled RT($200) <= 2.75%, requiring the '
                    'tape-verified 30 s window (fresh <= 12 s, promoted thresholds) plus the oct4 flow thresholds '
                    '(trades>=4, ratio>=1.3, buy>=$150, wallets>=4) and conviction >= 72. Simplified exits -5/+10/60.',
     'signal': F.ofa_signal('VF'),
     'kwargs': dict(EXITS)},
    {'name': 'FORENSICS_CF_PROXY',
     'description': 'COST_FIRST_ESTABLISHED universe (PumpSwap tier <= 50 bps, liq >= $250k, fee+impact RT <= 1.2%) '
                    'WITH the interim rug screen, tape flow gate replaced by the DexScreener proxy. '
                    'Size min($200, liq*0.001), exits -5/+10/60, 20 min cooldown.',
     'signal': F.cf_signal('PROXY'),
     'kwargs': dict(EXITS, size_fn=F.cf_size)},
]

BASELINES = [
    {'name': 'RANDOM_IN_WE_UNIVERSE',
     'description': 'Random hashed-coin entries (p=0.12 per point) inside the WE market+cost universe; same exits/size.',
     'signal': (lambda P: F.we_market(P) and H.hashed_coin(P.static('pair'), P.t, 0.12, 'forensics')),
     'kwargs': dict(EXITS, size_fn=F.main_requested)},
    {'name': 'RANDOM_IN_OFA_UNIVERSE',
     'description': 'Random hashed-coin entries (p=0.002 per point) inside the OFA market+cost universe; same exits.',
     'signal': (lambda P: F.ofa_market(P) and H.hashed_coin(P.static('pair'), P.t, 0.002, 'forensics')),
     'kwargs': dict(EXITS)},
    {'name': 'RANDOM_IN_CF_UNIVERSE_RUG_SCREENED',
     'description': 'Random hashed-coin entries (p=0.06 per point) inside the rug-screened cost-first universe; same exits/size.',
     'signal': (lambda P: F.cf_market(P) and H.hashed_coin(P.static('pair'), P.t, 0.06, 'forensics')),
     'kwargs': dict(EXITS, size_fn=F.cf_size)},
]


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    keep = ('n', 'pairs', 'clusters', 'win_rate', 'mean_pct', 'median_pct', 'pf', 'sum_usd', 'ci95_mean_usd',
            'top_pair_share', 'trades_per_hour', 'exits')
    for c in CONFIGS + BASELINES:
        tr = H.simulate(c['signal'], tag=c['name'], **c['kwargs'])
        ev = H.evaluate(tr)
        print('==', c['name'])
        for part in ('train', 'holdout', 'holdout_model'):
            print('  ', part, {k: ev[part].get(k) for k in keep})
        print('   portfolio(3)', H.portfolio(tr, slots=3))
